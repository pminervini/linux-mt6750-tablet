// SPDX-License-Identifier: GPL-2.0
/* MT6750 k50v1 board candidate. NO hardware is touched at registration.
 * STREAMON is the only power-on path. See README.md before any activation.
 * Board tables: existing still helper/data; CAMSV sequence adapted from
 * BayLibre ISP3.0 v8 (Copyright 2020 BayLibre / 2022 MediaTek Inc.).
 */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/i2c.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/of_address.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/wait.h>
#include <media/media-device.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-event.h>
#include <media/v4l2-fh.h>
#include <media/v4l2-ioctl.h>
#include <media/v4l2-subdev.h>
#include <media/videobuf2-v4l2.h>
#include <media/videobuf2-vmalloc.h>
#include "mtk-capture.h"
#include "board-tables.h"

extern int mt6755_pmic_read(u16 adr, u16 *val);
extern int mt6755_pmic_rmw(u16 adr, u16 mask, int shift, u16 val);

static bool legacy_excluded;
module_param(legacy_excluded, bool, 0444);
MODULE_PARM_DESC(legacy_excluded,
	"operator assertion: guarded legacy helper installed; load under its lock; no raw MMIO peers");
static bool allow_streaming;
module_param(allow_streaming, bool, 0444);
MODULE_PARM_DESC(allow_streaming, "allow sensor power/capture (default false; separate approval required)");

struct board;
struct camera_buffer {
	struct vb2_v4l2_buffer vb;
};
struct camera {
	struct board *board;
	unsigned int index, mode;
	struct i2c_client *client;
	struct mutex lock;
	spinlock_t queued_lock;
	struct mtk_fifo queued;
	wait_queue_head_t wait;
	struct task_struct *worker;
	struct vb2_queue queue;
	struct video_device video;
	struct media_pad video_pad;
	struct media_pipeline pipeline;
	struct v4l2_subdev sensor, receiver;
	struct media_pad sensor_pad, receiver_pads[2];
	struct v4l2_ctrl_handler controls;
	struct v4l2_ctrl *exposure, *gain;
	atomic_t exposure_lines, gain_units;
	unsigned int sequence;
	bool stopping, registered, sensor_started;
};
struct saved_pin {
	u32 mode, dir, value;
};
static const unsigned int pins[] = { 107, 108, 110, 109, 161 };
static const u16 pmic_regs[] = { 0xad6, 0xaa0, 0xaca, 0xa54, 0xa50, 0xa58 };
static const u16 pmic_masks[] = { 0x300, 0x300, 0x700, 2, 2, 2 };
struct board {
	struct platform_device *pdev;
	struct v4l2_device v4l2;
	struct media_device media;
	struct mutex owner_lock;
	struct camera *owner;
	struct camera cameras[2];
	void __iomem *sv, *sen, *ana[2], *spm, *gpio, *img, *larb;
	struct mtk_io io;
	void *dma_cpu;
	dma_addr_t dma_address;
	size_t dma_size;
	bool safe, poisoned, snapshot, isp_accessible, domain_started, ready, pm_transition;
	u16 pmic_saved[ARRAY_SIZE(pmic_regs)];
	struct saved_pin pin_saved[ARRAY_SIZE(pins)];
	u32 img_saved, larb_saved;
};
static struct board *board;

static u32 cam_read(void *ctx, u32 reg)
{
	return readl(((struct board *)ctx)->sv + reg);
}
static void cam_write(void *ctx, u32 reg, u32 value)
{
	writel(value, ((struct board *)ctx)->sv + reg);
}
static void cam_sleep(void *ctx, unsigned int us)
{
	usleep_range(us, us + max(10U, us / 10));
}
static u64 cam_now(void *ctx)
{
	return div_u64(ktime_get_ns(), 1000);
}
static bool cam_cancelled(void *ctx)
{
	struct board *b = ctx;

	return kthread_should_stop() || READ_ONCE(b->owner->stopping);
}
static void field(void __iomem *base, u32 reg, u32 mask, u32 value)
{
	writel((readl(base + reg) & ~mask) | value, base + reg);
}
/* ONLY atomic GPIO aliases, never full MODE words, including reset GPIO110
 * which shares the I2C2/CONSYS word. Gpiolib's current board pinctrl mux path
 * uses whole-word RMW, so it is deliberately not called here. See limitations.
 */
static u32 mode_reg(unsigned int pin)
{
	return 0x300 + pin / 10 * 0x10;
}
static u32 mode_shift(unsigned int pin)
{
	return (pin % 5) * 3 + (pin % 10 >= 5 ? 16 : 0);
}
static void pin_mode(struct board *b, unsigned int pin, unsigned int mode)
{
	u32 reg = mode_reg(pin), shift = mode_shift(pin);

	writel(7 << shift, b->gpio + reg + 8);
	if (mode)
		writel(mode << shift, b->gpio + reg + 4);
}
static void pin_out(struct board *b, unsigned int pin, bool high)
{
	u32 bank = pin / 32 * 0x10, bit = BIT(pin % 32);

	/* Establish the output latch before driving the line. */
	writel(bit, b->gpio + 0x100 + bank + (high ? 4 : 8));
	writel(bit, b->gpio + bank + 4);
}
static int snapshot(struct board *b)
{
	unsigned int i, pin, bank;
	int ret;

	for (i = 0; i < ARRAY_SIZE(pmic_regs); i++) {
		ret = mt6755_pmic_read(pmic_regs[i], &b->pmic_saved[i]);
		if (ret)
			return ret;
	}
	/* Refuse to borrow enabled shared camera rails. There is no regulator
	 * ownership framework for this board yet; this guards unknown users.
	 */
	if ((b->pmic_saved[3] | b->pmic_saved[4] | b->pmic_saved[5]) & 2)
		return -EBUSY;
	for (i = 0; i < ARRAY_SIZE(pins); i++) {
		pin = pins[i]; bank = pin / 32 * 0x10;
		b->pin_saved[i].mode = (readl(b->gpio + mode_reg(pin)) >> mode_shift(pin)) & 7;
		b->pin_saved[i].dir = !!(readl(b->gpio + bank) & BIT(pin % 32));
		b->pin_saved[i].value = !!(readl(b->gpio + 0x100 + bank) & BIT(pin % 32));
	}
	b->snapshot = true;
	return 0;
}
static void restore_pins(struct board *b)
{
	unsigned int i, pin, bank, bit;

	for (i = 0; i < ARRAY_SIZE(pins); i++) {
		pin = pins[i]; bank = pin / 32 * 0x10; bit = BIT(pin % 32);
		writel(bit, b->gpio + 0x100 + bank + (b->pin_saved[i].value ? 4 : 8));
		writel(bit, b->gpio + bank + (b->pin_saved[i].dir ? 4 : 8));
		pin_mode(b, pin, b->pin_saved[i].mode);
	}
}
static int isp_on(struct board *b)
{
	u32 value;
	int ret;

	/* Domain ownership is board-wide, and borrowed live domains are refused. */
	if ((readl(b->spm + 0x180) | readl(b->spm + 0x184)) & BIT(5))
		return -EBUSY;
	b->domain_started = true; /* also unwind partially acknowledged power-on */
	writel(0xb160001, b->spm);
	field(b->spm, 0x308, 0, BIT(2));
	field(b->spm, 0x308, 0, BIT(3));
	ret = readl_poll_timeout(b->spm + 0x180, value,
		(value & BIT(5)) && (readl(b->spm + 0x184) & BIT(5)), 1000, 1000000);
	if (ret)
		return ret;
	field(b->spm, 0x308, BIT(4), 0);
	field(b->spm, 0x308, BIT(1), 0);
	field(b->spm, 0x308, 0, BIT(0));
	field(b->spm, 0x308, BIT(8), 0);
	field(b->spm, 0x308, BIT(9), 0);
	ret = readl_poll_timeout(b->spm + 0x308, value, !(value & (3 << 12)), 1000, 1000000);
	if (!ret) {
		b->isp_accessible = true;
		b->img_saved = readl(b->img);
		b->larb_saved = readl(b->larb + 0xfc0);
	}
	return ret;
}
static int isp_off(struct board *b)
{
	u32 value;
	int ret;

	writel(0xb160001, b->spm);
	field(b->spm, 0x308, 0, 3 << 8);
	ret = readl_poll_timeout(b->spm + 0x308, value,
		(value & (3 << 12)) == (3 << 12), 1000, 1000000);
	if (ret)
		return ret;
	field(b->spm, 0x308, 0, BIT(1));
	field(b->spm, 0x308, 0, BIT(4));
	field(b->spm, 0x308, BIT(0), 0);
	field(b->spm, 0x308, BIT(2), 0);
	field(b->spm, 0x308, BIT(3), 0);
	ret = readl_poll_timeout(b->spm + 0x180, value,
		!(value & BIT(5)) && !(readl(b->spm + 0x184) & BIT(5)), 1000, 1000000);
	if (!ret) {
		b->safe = true; /* independent final DMA fence, even after reset failure */
		b->isp_accessible = false;
		b->domain_started = false;
	}
	return ret;
}
static int sensor_write(struct camera *c, u16 reg, u8 value)
{
	u8 data[] = { reg >> 8, reg & 255, value };
	int ret = i2c_master_send(c->client, data + (c->index ? 1 : 0), c->index ? 2 : 3);

	return ret == (c->index ? 2 : 3) ? 0 : ret < 0 ? ret : -EIO;
}
static int sensor_read(struct camera *c, u16 reg)
{
	u8 address[] = { reg >> 8, reg & 255 }, value;
	struct i2c_msg msgs[] = {
		{ .addr = c->client->addr, .len = c->index ? 1 : 2,
		  .buf = address + (c->index ? 1 : 0) },
		{ .addr = c->client->addr, .flags = I2C_M_RD, .len = 1, .buf = &value },
	};
	int ret = i2c_transfer(c->client->adapter, msgs, 2);

	return ret == 2 ? value : ret < 0 ? ret : -EIO;
}
static int sensor_table(struct camera *c, const struct board_reg *table, unsigned int count)
{
	unsigned int i;
	int ret;

	for (i = 0; i < count; i++) {
		if (table[i].reg == 0xffff) {
			msleep(table[i].value);
			continue;
		}
		ret = sensor_write(c, table[i].reg, table[i].value);
		if (ret)
			return ret;
		if (!c->index && table[i].reg == 0x0103)
			usleep_range(10000, 11000);
	}
	return 0;
}
static void mmio_table(void __iomem *base, const struct board_reg *table, unsigned int count)
{
	unsigned int i;

	for (i = 0; i < count; i++)
		writel(table[i].value, base + table[i].reg);
}
#define SENSOR_TABLE(c, t) sensor_table(c, t, ARRAY_SIZE(t))
#define MMIO_TABLE(b, t) mmio_table(b, t, ARRAY_SIZE(t))
static const struct board_mode *current_mode(struct camera *c)
{
	return &board_modes[c->index ? 2 : c->mode];
}
static int sensor_controls(struct camera *c)
{
	unsigned int lines = atomic_read(&c->exposure_lines), gain = atomic_read(&c->gain_units);
	int ret;
	unsigned int i;
	struct board_reg regs[10];

	if (!c->index) {
		struct board_reg r[] = {
			{ 0x3500, (lines >> 12) & 15 }, { 0x3501, (lines >> 4) & 255 },
			{ 0x3502, (lines << 4) & 255 }, { 0x3508, gain >> 8 }, { 0x3509, gain & 255 },
		};

		return sensor_table(c, r, ARRAY_SIZE(r));
	} else {
		static const unsigned int thresholds[] = { 64, 89, 123, 170, 248, 344, 494 };
		static const u8 r33[] = { 0x20, 0x24, 0x28, 0x30, 0x50, 0x50, 0x50 };
		unsigned int stage = 0, fine;

		for (i = 1; i < ARRAY_SIZE(thresholds); i++)
			if (gain >= thresholds[i])
				stage = i;
		fine = 64 * gain / thresholds[stage];
		regs[0] = (struct board_reg){ 0xfe, 0 };
		regs[1] = (struct board_reg){ 0x03, (lines >> 8) & 63 };
		regs[2] = (struct board_reg){ 0x04, lines & 255 };
		regs[3] = (struct board_reg){ 0x33, r33[stage] };
		regs[4] = (struct board_reg){ 0xb6, stage };
		regs[5] = (struct board_reg){ 0xb1, fine >> 6 };
		regs[6] = (struct board_reg){ 0xb2, (fine << 2) & 252 };
		ret = sensor_table(c, regs, 7);
		return ret;
	}
}
static int sensor_stop(struct camera *c)
{
	int ret, first;

	if (!c->index)
		return sensor_write(c, 0x100, 0);
	first = sensor_write(c, 0xfe, 3);
	ret = sensor_write(c, 0x10, 0);
	if (!first)
		first = ret;
	ret = sensor_write(c, 0xfe, 0);
	return first ? first : ret;
}
/* Cleanup never short-circuits: force shared reset/PDNs safe, fence DMA,
 * restore only owned regulator fields, preserve cleanup failures explicitly.
 */
static int power_down(struct camera *c)
{
	struct board *b = c->board;
	int first = 0, ret, i;

	if (b->isp_accessible) {
		ret = c->sensor_started ? sensor_stop(c) : 0;
		c->sensor_started = false;
		if (ret)
			first = ret;
		ret = mtk_dma_halt(&b->io);
		if (ret && !first)
			first = ret;
		if (!ret)
			b->safe = true;
		writel(0, b->sen + 0x200);
	}
	if (b->snapshot) {
		pin_out(b, 110, false);
		pin_out(b, 107, false);
		pin_out(b, 108, true);
	}
	if (b->isp_accessible && b->safe) {
		writel(b->larb_saved, b->larb + 0xfc0);
		writel(b->img_saved & (1 | (0x1f << 5)), b->img + 4);
	}
	if (b->domain_started) {
		ret = isp_off(b);
		if (ret && !first)
			first = ret;
	}
	if (b->snapshot) {
		/* Reverse enable order; voltages restored only after the rails. */
		for (i = ARRAY_SIZE(pmic_regs) - 1; i >= 0; i--) {
			ret = mt6755_pmic_rmw(pmic_regs[i], pmic_masks[i], 0, b->pmic_saved[i]);
			if (ret && !first)
				first = ret;
		}
		restore_pins(b);
		b->snapshot = false;
	}
	if (first || !b->safe) {
		b->poisoned = true; /* fail closed after ANY cleanup error */
		dev_err(&b->pdev->dev, "cleanup error %d, DMA safe=%d; further STREAMON disabled\n", first, b->safe);
	}
	return first;
}
static int power_up(struct camera *c)
{
	struct board *b = c->board;
	unsigned int i, off;
	int ret;

	ret = snapshot(b);
	if (ret)
		return ret;
	/* Refuse already-on ISP before changing even sensor power. */
	if ((readl(b->spm + 0x180) | readl(b->spm + 0x184)) & BIT(5)) {
		b->snapshot = false;
		return -EBUSY;
	}
	for (i = 0; i < ARRAY_SIZE(pins); i++)
		pin_mode(b, pins[i], 0);
	pin_out(b, 107, false); pin_out(b, 108, true); pin_out(b, 110, false);
	/* Only selected MCLK pin muxed; inactive camera stays in PDN. */
	pin_mode(b, c->index ? 109 : 161, 1);
	for (i = 0; i < 3; i++) {
		ret = mt6755_pmic_rmw(pmic_regs[i], pmic_masks[i], 0, 0x300);
		if (ret)
			return ret;
	}
	for (i = 3; i < ARRAY_SIZE(pmic_regs); i++) {
		ret = mt6755_pmic_rmw(pmic_regs[i], 2, 0, 2);
		if (ret)
			return ret;
		usleep_range(2000, 2500);
	}
	ret = isp_on(b);
	if (ret)
		return ret;
	writel(1 | (0x1f << 5), b->img + 8);
	writel(0, b->larb + 0xfc0);
	if (c->index) {
		MMIO_TABLE(b->sen, front_seninf);
		MMIO_TABLE(b->ana[1], front_csi1);
	} else {
		MMIO_TABLE(b->sen, rear_seninf);
		MMIO_TABLE(b->ana[0], rear_csi0);
	}
	for (off = 0; off < 0x20; off += 4)
		writel(readl(b->sen + 0x120 + off), b->sen + 0x920 + off);
	field(b->sen, 8, 0xf00, c->index << 8);
	usleep_range(5000, 6000);
	pin_out(b, c->index ? 108 : 107, !c->index);
	usleep_range(2000, 2500);
	pin_out(b, 110, true);
	msleep(20);
	if (c->index) {
		ret = sensor_read(c, 0xf0);
		if (ret < 0)
			return ret;
		if (ret != 0x23 || sensor_read(c, 0xf1) != 0x65)
			return -ENODEV;
		c->sensor_started = true;
		ret = SENSOR_TABLE(c, gc2365_init);
		if (!ret)
			ret = SENSOR_TABLE(c, gc2365_stream);
	} else {
		ret = sensor_read(c, 0x300b);
		if (ret < 0)
			return ret;
		if (ret != 0x88 || sensor_read(c, 0x300c) != 0x5a)
			return -ENODEV;
		c->sensor_started = true;
		ret = SENSOR_TABLE(c, ov8856_init);
		if (!ret)
			ret = c->mode ? SENSOR_TABLE(c, ov8856_full) : SENSOR_TABLE(c, ov8856_preview);
	}
	if (!ret)
		ret = sensor_controls(c);
	return ret;
}

static bool have_buffer(struct camera *c)
{
	unsigned long flags;
	bool ready;

	spin_lock_irqsave(&c->queued_lock, flags);
	ready = c->queued.count != 0;
	spin_unlock_irqrestore(&c->queued_lock, flags);
	return ready;
}
static struct camera_buffer *take_buffer(struct camera *c)
{
	struct camera_buffer *buf;
	unsigned long flags;

	spin_lock_irqsave(&c->queued_lock, flags);
	buf = mtk_fifo_pop(&c->queued);
	spin_unlock_irqrestore(&c->queued_lock, flags);
	return buf;
}
static void return_buffers(struct camera *c, enum vb2_buffer_state state)
{
	struct camera_buffer *buf;

	while ((buf = take_buffer(c))) {
		vb2_set_plane_payload(&buf->vb.vb2_buf, 0, 0);
		vb2_buffer_done(&buf->vb.vb2_buf, state);
	}
}
static int capture_worker(void *arg)
{
	struct camera *c = arg;
	struct board *b = c->board;
	const struct board_mode *mode = current_mode(c);
	struct camera_buffer *buf;
	int ret;

	while (!kthread_should_stop() && !READ_ONCE(c->stopping)) {
		ret = wait_event_interruptible(c->wait, kthread_should_stop() ||
			READ_ONCE(c->stopping) || have_buffer(c));
		if (ret || kthread_should_stop() || READ_ONCE(c->stopping))
			break;
		buf = take_buffer(c);
		if (!buf)
			continue;
		ret = sensor_controls(c);
		if (!ret)
			ret = mtk_dma_frame(&b->io, mode->width, mode->height,
				lower_32_bits(b->dma_address), 3000000, &b->safe);
		if (!ret) {
			dma_rmb();
			mtk_unpack10(vb2_plane_vaddr(&buf->vb.vb2_buf, 0), b->dma_cpu,
				mode->width * mode->height);
			buf->vb.vb2_buf.timestamp = b->io.completed_us * 1000;
			buf->vb.sequence = c->sequence++;
			buf->vb.field = V4L2_FIELD_NONE;
			vb2_set_plane_payload(&buf->vb.vb2_buf, 0, mode->width * mode->height * 2);
		} else {
			vb2_set_plane_payload(&buf->vb.vb2_buf, 0, 0);
		}
		vb2_buffer_done(&buf->vb.vb2_buf, ret ? VB2_BUF_STATE_ERROR : VB2_BUF_STATE_DONE);
		if (ret) {
			if (ret != -ECANCELED)
				dev_err(&b->pdev->dev, "capture failed: %d\n", ret);
			vb2_queue_error(&c->queue);
			break;
		}
	}
	/* Even if userspace never STREAMOFFs after EIO, hardware goes dark. */
	power_down(c);
	return_buffers(c, VB2_BUF_STATE_ERROR);
	return 0;
}
static int queue_setup(struct vb2_queue *q, unsigned int *count, unsigned int *planes,
		       unsigned int sizes[], struct device *alloc_devs[])
{
	struct camera *c = vb2_get_drv_priv(q);
	const struct board_mode *m = current_mode(c);
	u32 size = m->width * m->height * 2;
	unsigned int allocated = vb2_get_num_buffers(q);

	(void)alloc_devs;
	/* Core index storage requires >= VB2_MAX_FRAME; retain our total cap here,
	 * including repeated CREATE_BUFS calls, not only the initial REQBUFS. */
	if (allocated >= MTK_QUEUE_DEPTH)
		return -ENOBUFS;
	*count = min_t(unsigned int, *count, MTK_QUEUE_DEPTH - allocated);
	if (*planes)
		return *planes == 1 && sizes[0] >= size ? 0 : -EINVAL;
	*planes = 1; sizes[0] = size;
	return 0;
}
static int buffer_prepare(struct vb2_buffer *vb)
{
	struct camera *c = vb2_get_drv_priv(vb->vb2_queue);
	const struct board_mode *m = current_mode(c);

	if (vb2_plane_size(vb, 0) < m->width * m->height * 2 || !vb2_plane_vaddr(vb, 0))
		return -EINVAL;
	vb2_set_plane_payload(vb, 0, 0);
	return 0;
}
static void buffer_queue(struct vb2_buffer *vb)
{
	struct camera *c = vb2_get_drv_priv(vb->vb2_queue);
	struct camera_buffer *buf = container_of(to_vb2_v4l2_buffer(vb), struct camera_buffer, vb);
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&c->queued_lock, flags);
	ret = mtk_fifo_push(&c->queued, buf);
	spin_unlock_irqrestore(&c->queued_lock, flags);
	if (WARN_ON_ONCE(ret)) {
		vb2_buffer_done(vb, VB2_BUF_STATE_ERROR);
		vb2_queue_error(&c->queue);
	}
	wake_up(&c->wait);
}
static int start_streaming(struct vb2_queue *q, unsigned int count)
{
	struct camera *c = vb2_get_drv_priv(q);
	struct board *b = c->board;
	const struct board_mode *mode = current_mode(c);
	int ret;

	mutex_lock(&b->owner_lock);
	if (!allow_streaming) {
		ret = -EPERM;
		goto fail;
	}
	if (b->owner || b->pm_transition) {
		ret = -EBUSY;
		goto fail;
	}
	if (b->poisoned) {
		ret = -EIO;
		goto fail;
	}
	if (count < 2) {
		ret = -ENOBUFS;
		goto fail;
	}
	b->owner = c;
	b->safe = true;
	WRITE_ONCE(c->stopping, false);
	c->sequence = 0;
	ret = video_device_pipeline_start(&c->video, &c->pipeline);
	if (ret)
		goto clear_owner;
	b->dma_size = mode->width * mode->height * 5 / 4;
	b->dma_cpu = dma_alloc_coherent(&b->pdev->dev, b->dma_size, &b->dma_address, GFP_KERNEL);
	if (!b->dma_cpu) {
		ret = -ENOMEM;
		goto stop_pipeline;
	}
	ret = power_up(c);
	if (ret)
		goto power_fail;
	c->worker = kthread_run(capture_worker, c, "mt6750-cam%u", c->index);
	if (IS_ERR(c->worker)) {
		ret = PTR_ERR(c->worker);
		c->worker = NULL;
		goto power_fail;
	}
	mutex_unlock(&b->owner_lock);
	return 0;

power_fail:
	power_down(c);
	if (b->safe) {
		dma_free_coherent(&b->pdev->dev, b->dma_size, b->dma_cpu, b->dma_address);
		b->dma_cpu = NULL;
	} else {
		dev_err(&b->pdev->dev, "DMA not fenced: staging allocation quarantined, never freed\n");
		b->poisoned = true;
	}
stop_pipeline:
	video_device_pipeline_stop(&c->video);
clear_owner:
	b->owner = NULL;
fail:
	return_buffers(c, VB2_BUF_STATE_QUEUED);
	mutex_unlock(&b->owner_lock);
	return ret;
}
static void stop_streaming(struct vb2_queue *q)
{
	struct camera *c = vb2_get_drv_priv(q);
	struct board *b = c->board;

	mutex_lock(&b->owner_lock);
	WRITE_ONCE(c->stopping, true);
	wake_up(&c->wait);
	if (c->worker) {
		kthread_stop(c->worker); /* includes bounded DMA fence and power-down */
		c->worker = NULL;
	}
	return_buffers(c, VB2_BUF_STATE_ERROR);
	if (b->dma_cpu && b->safe) {
		dma_free_coherent(&b->pdev->dev, b->dma_size, b->dma_cpu, b->dma_address);
		b->dma_cpu = NULL;
	} else if (b->dma_cpu) {
		b->poisoned = true;
		dev_err(&b->pdev->dev, "DMA not fenced: leaking private staging allocation for safety\n");
	}
	video_device_pipeline_stop(&c->video);
	b->owner = NULL;
	mutex_unlock(&b->owner_lock);
}
static const struct vb2_ops queue_ops = {
	.queue_setup = queue_setup,
	.buf_prepare = buffer_prepare,
	.buf_queue = buffer_queue,
	.start_streaming = start_streaming,
	.stop_streaming = stop_streaming,
};
static void fill_format(struct camera *c, struct v4l2_pix_format *pix)
{
	const struct board_mode *m = current_mode(c);

	memset(pix, 0, sizeof(*pix));
	pix->width = m->width; pix->height = m->height;
	pix->pixelformat = V4L2_PIX_FMT_SBGGR10;
	pix->field = V4L2_FIELD_NONE;
	pix->bytesperline = m->width * 2;
	pix->sizeimage = pix->bytesperline * m->height;
	pix->colorspace = V4L2_COLORSPACE_RAW;
	pix->xfer_func = V4L2_XFER_FUNC_NONE;
	pix->quantization = V4L2_QUANTIZATION_FULL_RANGE;
}
static int querycap(struct file *file, void *priv, struct v4l2_capability *cap)
{
	strscpy(cap->driver, "mt6750-camera", sizeof(cap->driver));
	strscpy(cap->card, "k50v1 RAW camera candidate", sizeof(cap->card));
	strscpy(cap->bus_info, "platform:mt6750-camera", sizeof(cap->bus_info));
	return 0;
}
static int enum_fmt(struct file *file, void *priv, struct v4l2_fmtdesc *f)
{
	if (f->index || (f->mbus_code && f->mbus_code != MEDIA_BUS_FMT_SBGGR10_1X10))
		return -EINVAL;
	f->pixelformat = V4L2_PIX_FMT_SBGGR10;
	return 0;
}
static int get_fmt(struct file *file, void *priv, struct v4l2_format *fmt)
{
	fill_format(video_drvdata(file), &fmt->fmt.pix);
	return 0;
}
static int try_fmt(struct file *file, void *priv, struct v4l2_format *fmt)
{
	struct camera *c = video_drvdata(file);
	unsigned int saved = c->mode;

	if (!c->index)
		c->mode = fmt->fmt.pix.width > 2448 || fmt->fmt.pix.height > 1836;
	fill_format(c, &fmt->fmt.pix);
	c->mode = saved;
	return 0;
}
static int set_fmt(struct file *file, void *priv, struct v4l2_format *fmt)
{
	struct camera *c = video_drvdata(file);
	const struct board_mode *m;
	int ret;

	if (vb2_is_busy(&c->queue))
		return -EBUSY;
	try_fmt(file, priv, fmt);
	c->mode = !c->index && fmt->fmt.pix.width == 3264;
	m = current_mode(c);
	/* Lock order: video/queue mutex, then the independent control mutex. */
	mutex_lock(c->controls.lock);
	ret = __v4l2_ctrl_modify_range(c->exposure, 6, m->vts - 8, 1, m->vts / 2);
	if (!ret)
		ret = __v4l2_ctrl_s_ctrl(c->exposure, min_t(unsigned int, c->exposure->val, m->vts - 8));
	mutex_unlock(c->controls.lock);
	return ret;
}
static int enum_size(struct file *file, void *priv, struct v4l2_frmsizeenum *f)
{
	struct camera *c = video_drvdata(file);
	const struct board_mode *m;

	if (f->pixel_format != V4L2_PIX_FMT_SBGGR10 || f->index >= (c->index ? 1 : 2))
		return -EINVAL;
	m = &board_modes[c->index ? 2 : f->index];
	f->type = V4L2_FRMSIZE_TYPE_DISCRETE;
	f->discrete.width = m->width; f->discrete.height = m->height;
	return 0;
}
static int enum_input(struct file *file, void *priv, struct v4l2_input *input)
{
	struct camera *c = video_drvdata(file);

	if (input->index)
		return -EINVAL;
	input->type = V4L2_INPUT_TYPE_CAMERA;
	strscpy(input->name, c->index ? "GC2365 front" : "OV8856 rear", sizeof(input->name));
	return 0;
}
static int get_input(struct file *file, void *priv, unsigned int *index)
{
	*index = 0;
	return 0;
}
static int set_input(struct file *file, void *priv, unsigned int index)
{
	return index ? -EINVAL : 0;
}
static const struct v4l2_ioctl_ops ioctl_ops = {
	.vidioc_querycap = querycap,
	.vidioc_enum_fmt_vid_cap = enum_fmt,
	.vidioc_g_fmt_vid_cap = get_fmt,
	.vidioc_try_fmt_vid_cap = try_fmt,
	.vidioc_s_fmt_vid_cap = set_fmt,
	.vidioc_enum_framesizes = enum_size,
	.vidioc_enum_input = enum_input,
	.vidioc_g_input = get_input,
	.vidioc_s_input = set_input,
	.vidioc_reqbufs = vb2_ioctl_reqbufs,
	.vidioc_create_bufs = vb2_ioctl_create_bufs,
	.vidioc_prepare_buf = vb2_ioctl_prepare_buf,
	.vidioc_querybuf = vb2_ioctl_querybuf,
	.vidioc_qbuf = vb2_ioctl_qbuf,
	.vidioc_dqbuf = vb2_ioctl_dqbuf,
	.vidioc_streamon = vb2_ioctl_streamon,
	.vidioc_streamoff = vb2_ioctl_streamoff,
	.vidioc_subscribe_event = v4l2_ctrl_subscribe_event,
	.vidioc_unsubscribe_event = v4l2_event_unsubscribe,
};
static int camera_open(struct file *file)
{
	struct camera *c = video_drvdata(file);

	/* Pair with init's release: do not open a partially published media graph. */
	if (!smp_load_acquire(&c->board->ready))
		return -ENODEV;
	return v4l2_fh_open(file);
}
static const struct v4l2_file_operations file_ops = {
	.owner = THIS_MODULE,
	.open = camera_open,
	.release = vb2_fop_release,
	.poll = vb2_fop_poll,
	.mmap = vb2_fop_mmap,
	.unlocked_ioctl = video_ioctl2,
};
static int control_set(struct v4l2_ctrl *ctrl)
{
	struct camera *c = container_of(ctrl->handler, struct camera, controls);

	if (ctrl->id == V4L2_CID_EXPOSURE)
		atomic_set(&c->exposure_lines, ctrl->val);
	else if (ctrl->id == V4L2_CID_ANALOGUE_GAIN)
		atomic_set(&c->gain_units, ctrl->val);
	return 0; /* worker applies controls over I2C; no idle sensor activation */
}
static const struct v4l2_ctrl_ops ctrl_ops = { .s_ctrl = control_set };
static int pad_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
		   struct v4l2_subdev_format *fmt)
{
	struct camera *c = v4l2_get_subdevdata(sd);
	const struct board_mode *m;

	mutex_lock(&c->lock);
	m = current_mode(c);
	memset(&fmt->format, 0, sizeof(fmt->format));
	fmt->format.width = m->width; fmt->format.height = m->height;
	fmt->format.code = MEDIA_BUS_FMT_SBGGR10_1X10;
	fmt->format.field = V4L2_FIELD_NONE;
	fmt->format.colorspace = V4L2_COLORSPACE_RAW;
	fmt->format.xfer_func = V4L2_XFER_FUNC_NONE;
	mutex_unlock(&c->lock);
	return 0;
}
static const struct v4l2_subdev_pad_ops pad_ops = { .get_fmt = pad_fmt };
static const struct v4l2_subdev_ops subdev_ops = { .pad = &pad_ops };
/* Validation occurs in the board's S_FMT/STREAMON path. All links immutable,
 * both pads always carry this camera's current SBGGR10 mode.
 */
static const struct media_entity_operations entity_ops = { };
static int register_path(struct board *b, struct camera *c, unsigned int index,
			 struct i2c_adapter *adapter)
{
	const struct board_mode *m;
	int ret;

	c->board = b; c->index = index;
	mutex_init(&c->lock);
	spin_lock_init(&c->queued_lock);

	init_waitqueue_head(&c->wait);
	c->client = i2c_new_dummy_device(adapter, index ? 0x37 : 0x36);
	if (IS_ERR(c->client))
		return PTR_ERR(c->client);
	m = current_mode(c);
	v4l2_ctrl_handler_init(&c->controls, 2);
	/* Keep the handler's own mutex: video_ioctl2 already holds c->lock. */
	c->exposure = v4l2_ctrl_new_std(&c->controls, &ctrl_ops, V4L2_CID_EXPOSURE,
		6, m->vts - 8, 1, m->vts / 2);
	c->gain = v4l2_ctrl_new_std(&c->controls, &ctrl_ops, V4L2_CID_ANALOGUE_GAIN,
		index ? 64 : 128, index ? 493 : 1984, 1, index ? 64 : 128);
	ret = c->controls.error;
	if (ret)
		goto free_controls;
	v4l2_ctrl_handler_setup(&c->controls);
	v4l2_subdev_init(&c->sensor, &subdev_ops);
	c->sensor.owner = THIS_MODULE;
	v4l2_set_subdevdata(&c->sensor, c);
	strscpy(c->sensor.name, index ? "GC2365 2-0037" : "OV8856 2-0036", sizeof(c->sensor.name));
	c->sensor.entity.function = MEDIA_ENT_F_CAM_SENSOR;
	c->sensor.entity.ops = &entity_ops;
	c->sensor_pad.flags = MEDIA_PAD_FL_SOURCE;
	ret = media_entity_pads_init(&c->sensor.entity, 1, &c->sensor_pad);
	if (ret)
		goto free_controls;
	ret = v4l2_device_register_subdev(&b->v4l2, &c->sensor);
	if (ret)
		goto clean_sensor;
	v4l2_subdev_init(&c->receiver, &subdev_ops);
	c->receiver.owner = THIS_MODULE;
	v4l2_set_subdevdata(&c->receiver, c);
	snprintf(c->receiver.name, sizeof(c->receiver.name), "SENINF%u-CAMSV0 exclusive", index + 1);
	c->receiver.entity.function = MEDIA_ENT_F_VID_IF_BRIDGE;
	c->receiver.entity.ops = &entity_ops;
	c->receiver_pads[0].flags = MEDIA_PAD_FL_SINK;
	c->receiver_pads[1].flags = MEDIA_PAD_FL_SOURCE;
	ret = media_entity_pads_init(&c->receiver.entity, 2, c->receiver_pads);
	if (ret)
		goto unreg_sensor;
	ret = v4l2_device_register_subdev(&b->v4l2, &c->receiver);
	if (ret)
		goto clean_receiver;
	c->queue = (struct vb2_queue){
		.type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
		.io_modes = VB2_MMAP,
		.drv_priv = c,
		.buf_struct_size = sizeof(struct camera_buffer),
		.ops = &queue_ops,
		.mem_ops = &vb2_vmalloc_memops,
		.timestamp_flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC | V4L2_BUF_FLAG_TSTAMP_SRC_EOF,
		.lock = &c->lock,
		.dev = &b->pdev->dev,
		.min_queued_buffers = 2,
		.max_num_buffers = VB2_MAX_FRAME,
	};
	ret = vb2_queue_init(&c->queue);
	if (ret)
		goto unreg_receiver;
	c->video = (struct video_device){
		.fops = &file_ops, .ioctl_ops = &ioctl_ops,
		.release = video_device_release_empty,
		.v4l2_dev = &b->v4l2, .queue = &c->queue, .lock = &c->lock,
		.ctrl_handler = &c->controls,
		.device_caps = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING,
		.vfl_dir = VFL_DIR_RX,
	};
	strscpy(c->video.name, index ? "mt6750-front-raw" : "mt6750-rear-raw", sizeof(c->video.name));
	video_set_drvdata(&c->video, c);
	c->video_pad.flags = MEDIA_PAD_FL_SINK;
	ret = media_entity_pads_init(&c->video.entity, 1, &c->video_pad);
	if (ret)
		goto release_queue;
	ret = video_register_device(&c->video, VFL_TYPE_VIDEO, -1);
	if (ret)
		goto clean_video;
	ret = media_create_pad_link(&c->sensor.entity, 0, &c->receiver.entity, 0,
		MEDIA_LNK_FL_ENABLED | MEDIA_LNK_FL_IMMUTABLE);
	if (!ret)
		ret = media_create_pad_link(&c->receiver.entity, 1, &c->video.entity, 0,
			MEDIA_LNK_FL_ENABLED | MEDIA_LNK_FL_IMMUTABLE);
	if (!ret) {
		c->registered = true;
		return 0;
	}
	video_unregister_device(&c->video);
clean_video:
	media_entity_cleanup(&c->video.entity);
release_queue:
	vb2_queue_release(&c->queue);
unreg_receiver:
	v4l2_device_unregister_subdev(&c->receiver);
clean_receiver:
	media_entity_cleanup(&c->receiver.entity);
unreg_sensor:
	v4l2_device_unregister_subdev(&c->sensor);
clean_sensor:
	media_entity_cleanup(&c->sensor.entity);
free_controls:
	v4l2_ctrl_handler_free(&c->controls);
	i2c_unregister_device(c->client);
	return ret;
}
static void unregister_path(struct camera *c)
{
	if (!c->registered)
		return;
	video_unregister_device(&c->video);
	vb2_queue_release(&c->queue);
	media_entity_cleanup(&c->video.entity);
	v4l2_device_unregister_subdev(&c->receiver);
	media_entity_cleanup(&c->receiver.entity);
	v4l2_device_unregister_subdev(&c->sensor);
	media_entity_cleanup(&c->sensor.entity);
	v4l2_ctrl_handler_free(&c->controls);
	i2c_unregister_device(c->client);
}
static void __iomem *map_region(struct board *b, resource_size_t start, size_t size, bool shared)
{
	struct device *dev = &b->pdev->dev;
	void __iomem *base;

	if (!shared && !devm_request_mem_region(dev, start, size, "mt6750-camera"))
		return ERR_PTR(-EBUSY);
	base = devm_ioremap(dev, start, size);
	return base ? base : ERR_PTR(-ENOMEM);
}
#define MAP(member, address, size, shared) do { \
	b->member = map_region(b, address, size, shared); \
	if (IS_ERR(b->member)) { \
		ret = PTR_ERR(b->member); \
		goto free_board; \
	} \
} while (0)
/* Block explicit suspend/hibernate as well as autosleep while this prototype
 * owns hardware. An idle PM transition also excludes a racing STREAMON.
 */
static int camera_pm_event(struct notifier_block *nb, unsigned long event, void *unused)
{
	struct board *b = board;
	int ret = NOTIFY_OK;

	(void)nb;
	(void)unused;
	mutex_lock(&b->owner_lock);
	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		if (b->owner)
			ret = notifier_from_errno(-EBUSY);
		else
			b->pm_transition = true;
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		b->pm_transition = false;
		break;
	}
	mutex_unlock(&b->owner_lock);
	return ret;
}
static struct notifier_block camera_pm_notifier = { .notifier_call = camera_pm_event };

static int camera_probe(struct platform_device *pdev)
{
	struct board *b;
	struct i2c_adapter *adapter;
	struct resource resource;
	int ret;

	b = kzalloc(sizeof(*b), GFP_KERNEL);
	if (!b)
		return -ENOMEM;
	board = b;
	mutex_init(&b->owner_lock);
	b->pdev = pdev;
	platform_set_drvdata(pdev, b);
	ret = dma_coerce_mask_and_coherent(&b->pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		goto free_board;
	MAP(sv, 0x15009000, 0x1000, false);
	MAP(sen, 0x15008000, 0x1000, false);
	MAP(ana[0], 0x10217000, 0x100, false);
	MAP(ana[1], 0x10218000, 0x100, false);
	MAP(img, 0x15000000, 0x100, false);
	MAP(larb, 0x15001000, 0x1000, false);
	/* Shared syscon blocks: never claim them against pinctrl / PM domains. */
	MAP(spm, 0x10006000, 0x1000, true);
	MAP(gpio, 0x10005000, 0x1000, true);
	b->io = (struct mtk_io){
		.ctx = b, .read = cam_read, .write = cam_write,
		.sleep_us = cam_sleep, .now_us = cam_now, .cancelled = cam_cancelled,
	};
	adapter = i2c_get_adapter(2);
	if (!adapter) {
		ret = -ENODEV;
		goto free_board;
	}
	/* Fail closed if Linux's bus number was reassigned to another controller. */
	ret = adapter->dev.parent && adapter->dev.parent->of_node ?
		of_address_to_resource(adapter->dev.parent->of_node, 0, &resource) : -ENODEV;
	if (ret || resource.start != 0x11009000 || !i2c_check_functionality(adapter, I2C_FUNC_I2C)) {
		ret = -ENODEV;
		goto put_adapter;
	}
	b->media.dev = &b->pdev->dev;
	strscpy(b->media.model, "MT6750 k50v1 exclusive RAW paths", sizeof(b->media.model));
	strscpy(b->media.bus_info, "platform:mt6750-camera", sizeof(b->media.bus_info));
	media_device_init(&b->media);
	b->v4l2.mdev = &b->media;
	ret = v4l2_device_register(&b->pdev->dev, &b->v4l2);
	if (ret)
		goto cleanup_media;
	ret = register_path(b, &b->cameras[0], 0, adapter);
	if (!ret)
		ret = register_path(b, &b->cameras[1], 1, adapter);
	if (ret)
		goto unregister_paths;
	ret = media_device_register(&b->media);
	if (ret)
		goto unregister_paths;
	ret = register_pm_notifier(&camera_pm_notifier);
	if (ret)
		goto unregister_media;
	i2c_put_adapter(adapter);
	/* Publish both complete paths before camera_open() can admit users. */
	smp_store_release(&b->ready, true);
	dev_info(&b->pdev->dev, "candidate registered, sensors unpowered; hardware validation pending\n");
	return 0;
unregister_media:
	media_device_unregister(&b->media);
unregister_paths:
	unregister_path(&b->cameras[1]);
	unregister_path(&b->cameras[0]);
	v4l2_device_unregister(&b->v4l2);
cleanup_media:
	media_device_cleanup(&b->media);
put_adapter:
	i2c_put_adapter(adapter);
free_board:
	platform_set_drvdata(pdev, NULL);
	board = NULL;
	kfree(b);
	return dev_err_probe(&pdev->dev, ret, "camera registration failed\n");
}
static void camera_remove(struct platform_device *pdev)
{
	struct board *b = platform_get_drvdata(pdev);

	/* fops owner pins the module for open files / mappings. */
	smp_store_release(&b->ready, false);
	unregister_pm_notifier(&camera_pm_notifier);
	media_device_unregister(&b->media);
	unregister_path(&b->cameras[1]);
	unregister_path(&b->cameras[0]);
	v4l2_device_unregister(&b->v4l2);
	media_device_cleanup(&b->media);
	platform_set_drvdata(pdev, NULL);
	board = NULL;
	kfree(b);
}
static struct platform_driver camera_driver = {
	.probe = camera_probe,
	.remove = camera_remove,
	.driver = {
		.name = "mt6750-camera",
		.probe_type = PROBE_FORCE_SYNCHRONOUS,
		.suppress_bind_attrs = true,
	},
};
static struct platform_device *camera_device;
static int __init camera_init(void)
{
	int ret;

	if (!legacy_excluded || !of_machine_is_compatible("mediatek,k50v1-tablet"))
		return -EPERM;
	ret = platform_driver_register(&camera_driver);
	if (ret)
		return ret;
	camera_device = platform_device_register_simple("mt6750-camera", -1, NULL, 0);
	if (IS_ERR(camera_device)) {
		ret = PTR_ERR(camera_device);
		goto unregister_driver;
	}
	if (!platform_get_drvdata(camera_device)) {
		ret = -ENODEV;
		platform_device_unregister(camera_device);
		goto unregister_driver;
	}
	return 0;
unregister_driver:
	platform_driver_unregister(&camera_driver);
	return ret;
}
static void __exit camera_exit(void)
{
	platform_device_unregister(camera_device);
	platform_driver_unregister(&camera_driver);
}
module_init(camera_init);
module_exit(camera_exit);
MODULE_DESCRIPTION("MT6750 k50v1 OV8856/GC2365 V4L2 RAW streaming candidate (unvalidated)");
MODULE_LICENSE("GPL");
