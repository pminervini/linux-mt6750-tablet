// SPDX-License-Identifier: GPL-2.0
/* Board-specific MT6750/MT6755 + MT6353 candidate. See README.md for acceptance.
 * The PMIC wrapper remains owned by mt6755-pmic; never map WACS2 here.
 * FM capture has no path to codec_power(), the audio pins, or amp().
 */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/gpio/machine.h>
#include <linux/hrtimer.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/uio.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>
#include "mt6755-audio-core.h"

extern int mt6755_pmic_read(u16 adr, u16 *val);
extern int mt6755_pmic_write(u16 adr, u16 val);
extern int mt6755_pmic_rmw(u16 adr, u16 mask, int shift, u16 val);

/* These settings are not approval. All default off and are immutable while
 * loaded. The administrator must use the documented shared admission lock.
 */
static bool legacy_quiesced;
module_param(legacy_quiesced, bool, 0444);
MODULE_PARM_DESC(legacy_quiesced, "legacy player stopped under admission lock (required)");
static bool allow_playback;
module_param(allow_playback, bool, 0444);
MODULE_PARM_DESC(allow_playback, "allow MT6353/AW8736 analogue playback (default false)");
static bool allow_capture;
module_param(allow_capture, bool, 0444);
MODULE_PARM_DESC(allow_capture, "allow digital FM PCM capture (default false)");

struct mt_audio;
struct mt_stream {
	struct mt_audio *audio;
	struct snd_pcm_substream *substream;
	struct hrtimer timer;
	struct mt_ring ring;
	void *area;
	dma_addr_t dma;
	size_t allocated;
	bool running, prepared;
};
struct mt_audio {
	struct device *dev;
	void __iomem *afe, *spm, *gpio;
	struct mutex mutex;
	spinlock_t reg_lock;
	spinlock_t state_lock; /* running + shared AFE_ON; nests outside reg_lock */
	struct mt_stream stream[2];
	unsigned int users, volume;
	u32 saved_top;
	bool powered_before, codec_on, fault;
	struct gpio_desc *pins[6];
	struct gpio_device *gpio_device;
	struct snd_soc_card card;
};
static const unsigned int mt_pins[] = {0, 54, 62, 149, 150, 151};

static void mt_update(struct mt_audio *a, u32 reg, u32 mask, u32 value)
{
	unsigned long flags;

	spin_lock_irqsave(&a->reg_lock, flags);
	writel((readl(a->afe + reg) & ~mask) | (value & mask), a->afe + reg);
	spin_unlock_irqrestore(&a->reg_lock, flags);
}
static void mt_steps(struct mt_audio *a, const struct mt_step *s, unsigned int n)
{
	while (n--) {
		mt_update(a, s->reg, s->mask, s->value);
		s++;
	}
}
#define AFE_STEPS(a, seq) mt_steps(a, seq, ARRAY_SIZE(seq))

/* Only the evidenced AUDIO MTCMOS domain, not CONSYS or shared GPIO mux words.
 * Current board DTS has no audio genpd/clock consumer. Do not transplant this
 * onto a DT with a real audio-domain owner; replace it with genpd there.
 */
static int mt_domain(struct mt_audio *a, bool on)
{
	u32 v, c;
	int ret;

	writel(0xb160001, a->spm); /* POWERON_CONFIG_EN */
	c = readl(a->spm + 0x314);
	if (on) {
		writel(c | 4, a->spm + 0x314);
		writel(c | 12, a->spm + 0x314);
		ret = readl_poll_timeout(a->spm + 0x180, v, v & BIT(24), 10, 100000);
		if (ret)
			return ret;
		ret = readl_poll_timeout(a->spm + 0x184, v, v & BIT(24), 10, 100000);
		if (ret)
			return ret;
		c = readl(a->spm + 0x314) & ~0x12;
		writel(c, a->spm + 0x314);
		writel((c | 1) & ~0xf00, a->spm + 0x314);
		return readl_poll_timeout(a->spm + 0x314, v, !(v & 0xf000), 10, 100000);
	}
	writel(c | 0xf00, a->spm + 0x314);
	ret = readl_poll_timeout(a->spm + 0x314, v, (v & 0xf000) == 0xf000, 10, 100000);
	if (ret)
		return ret;
	c = readl(a->spm + 0x314) | 0x12;
	writel(c, a->spm + 0x314);
	writel(c & ~1, a->spm + 0x314);
	writel(c & ~13, a->spm + 0x314);
	return readl_poll_timeout(a->spm + 0x180, v, !(v & BIT(24)), 10, 100000);
}

/* Set/clear aliases, never read-modify-write a shared MODE word. */
static void mt_pin_mode(struct mt_audio *a, unsigned int pin, unsigned int mode)
{
	unsigned int index = pin % 10;
	unsigned int shift = index < 5 ? index * 3 : 16 + (index - 5) * 3;
	unsigned int reg = 0x300 + pin / 10 * 0x10;

	writel(7U << shift, a->gpio + reg + 8);
	writel(mode << shift, a->gpio + reg + 4);
}
static void mt_gpio_out(struct mt_audio *a, unsigned int pin, bool high)
{
	unsigned int bank = pin / 32 * 0x10;
	u32 bit = BIT(pin % 32);

	writel(bit, a->gpio + 0x100 + bank + (high ? 4 : 8));
	writel(bit, a->gpio + bank + 4);
}
static void mt_amp_off(struct mt_audio *a)
{
	mt_gpio_out(a, 0, false);
	mt_gpio_out(a, 54, false);
	mt_gpio_out(a, 62, false);
}
static void mt_amp_on(struct mt_audio *a)
{
	int i;

	mt_gpio_out(a, 62, true);
	mt_gpio_out(a, 0, false);
	mt_gpio_out(a, 54, false);
	usleep_range(2000, 2500);
	for (i = 0; i < 3; i++) {
		mt_gpio_out(a, 0, false);
		mt_gpio_out(a, 54, false);
		udelay(2);
		mt_gpio_out(a, 0, true);
		mt_gpio_out(a, 54, true);
		udelay(2);
	}
	msleep(35);
}

struct mt_codec_step { u16 reg, value, mask, delay_us; };
/* Exact deployed, stock-derived speaker route: HPL/HPR feed AW8736. This is
 * NOT a claimed headphone-jack route. No ADC, micbias, ACCDET, or guessed pins.
 */
static const struct mt_codec_step codec_up[] = {
	{0x0d2a, 0x8000, 0x8000}, {0x0244, 1, 1}, {0x0246, 1, 0xffff},
	{0x0272, 0x70, 0xffff, 1000}, {0x0d02, 0, 2},
	{0x203c, 0x8000, 0x8000}, {0x2014, 0x3a, 0xffff},
	{0x2020, 6, 0xffff}, {0x201c, 0xc3a1, 0xffff},
	{0x2020, 3, 0xffff}, {0x2020, 0xb, 0xffff},
	{0x2008, 0x1e, 0xffff}, {0x2000, 1, 0xffff},
	{0x2038, 0x8330, 0xffff}, {0x2002, 0x8300, 0xffff},
	{0x2004, 1, 0xffff}, {0x2012, 0, 0xffff},
	{0x0d1c, 1, 0xffff}, {0x0d1e, 0x2b, 0xffff},
	{0x0d1a, 1, 0xffff}, {0x0d22, 3, 0xffff}, {0x0d20, 0, 0xffff},
	{0x0cfe, 0x0a41, 0xfeeb}, {0x0cfe, 0x2ac1, 0xfeeb},
	{0x0d00, 0x8000, 0x8000}, {0x0800, 0, 0xffff},
	{0x0cf2, 0xe080, 0xffff}, {0x0cfe, 0x2ac0, 0xfeeb},
	{0x0cfa, 0x0700, 0xffff}, {0x0cfc, 0x5490, 0xffff, 50},
	{0x0804, 0x0f9f, 0xffff}, {0x0806, 0x001f, 0xffff},
	{0x0cf4, 0x0480, 0xffff}, {0x0cf4, 0x1480, 0xffff},
	{0x0cf2, 0xe090, 0xffff}, {0x0cf4, 0x14a0, 0xffff, 50},
	{0x0cfe, 0x2ac2, 0xfeeb}, {0x0cf2, 0x000f, 0x000f},
	{0x0cf2, 0x1480, 0x1f80}, {0x0cf2, 0x0060, 0x0060, 50},
	{0x0cf4, 0x1480, 0xffff}, {0x0cf4, 0x0480, 0xffff},
	{0x0cf2, 0, 0x0190}, {0x0cfa, 0x0300, 0xffff},
};
static const struct mt_codec_step codec_down[] = {
	{0x0804, 0x0f9f, 0xffff}, {0x0cf2, 0, 0xffff},
	{0x0cf8, 0, 0xffff}, {0x0cfa, 0, 0xffff},
	{0x0d00, 0, 0x8000}, {0x0cfe, 1, 0xfeeb},
	{0x2004, 0, 0xffff}, {0x2000, 0, 0xffff}, {0x0d02, 2, 2},
};
static int mt_codec_write(const struct mt_codec_step *s)
{
	int ret;

	ret = s->mask == 0xffff ? mt6755_pmic_write(s->reg, s->value) :
		mt6755_pmic_rmw(s->reg, s->mask, 0, s->value);
	if (!ret && s->delay_us)
		usleep_range(s->delay_us, s->delay_us + 50);
	return ret;
}
static int mt_codec_off(struct mt_audio *a)
{
	unsigned int i;
	int ret, first = 0;

	if (!a->codec_on)
		return 0;
	mt_amp_off(a);
	for (i = 0; i < ARRAY_SIZE(codec_down); i++) {
		ret = mt_codec_write(&codec_down[i]);
		if (ret && !first)
			first = ret;
	}
	a->codec_on = false;
	if (first) {
		a->fault = true;
		dev_err(a->dev, "codec cleanup failed: %d; reopen blocked\n", first);
	}
	return first;
}
static int mt_codec_on(struct mt_audio *a)
{
	unsigned int i;
	int ret, gain;
	struct mt_codec_step s = {0x0804, 0, 0xffff, 500};

	a->codec_on = true; /* cleanup includes partial initialization */
	for (i = 0; i < ARRAY_SIZE(codec_up); i++) {
		ret = mt_codec_write(&codec_up[i]);
		if (ret)
			goto fail;
	}
	for (gain = 31; gain >= 0; gain--) {
		s.value = gain | gain << 7;
		ret = mt_codec_write(&s);
		if (ret)
			goto fail;
	}
	for (i = 0; i < 3; i++) {
		static const struct mt_codec_step tail[] = {
			{0x0802, 0x0408, 0xffff}, {0x0808, 0x0505, 0xffff},
			{0x0cf8, 0x0072, 0xffff},
		};
		ret = mt_codec_write(&tail[i]);
		if (ret)
			goto fail;
	}
	return 0;
fail:
	mt_codec_off(a);
	return ret;
}

static enum hrtimer_restart mt_poll(struct hrtimer *timer)
{
	struct mt_stream *s = container_of(timer, struct mt_stream, timer);
	struct mt_audio *a = s->audio;
	u32 cur, offset;
	int periods;

	if (!READ_ONCE(s->running))
		return HRTIMER_NORESTART;
	cur = readl(a->afe + (s == &a->stream[0] ? 0x054 : 0x07c));
	/* Hardware may report zero until its first sample. Never count it as PCM. */
	offset = cur ? cur - (u32)s->dma : s->ring.last;
	periods = mt_advance(&s->ring, offset, ktime_get_ns());
	if (periods < 0) {
		snd_pcm_stop_xrun(s->substream);
		return HRTIMER_NORESTART;
	}
	/* ALSA accounts the actual .pointer delta, including multiple periods. */
	if (periods)
		snd_pcm_period_elapsed(s->substream);
	if (!READ_ONCE(s->running))
		return HRTIMER_NORESTART;
	hrtimer_forward_now(timer, ns_to_ktime(1000000));
	return HRTIMER_RESTART;
}
static const struct snd_pcm_hardware mt_hw = {
	.info = SNDRV_PCM_INFO_INTERLEAVED | SNDRV_PCM_INFO_BLOCK_TRANSFER,
	.formats = SNDRV_PCM_FMTBIT_S16_LE,
	.rates = SNDRV_PCM_RATE_48000, .rate_min = MT_RATE, .rate_max = MT_RATE,
	.channels_min = 2, .channels_max = 2,
	.buffer_bytes_max = MT_CAPTURE_BYTES,
	.period_bytes_min = 1920, .period_bytes_max = 16384,
	.periods_min = 2, .periods_max = 32,
};
static struct mt_stream *mt_get_stream(struct snd_soc_component *c,
				      struct snd_pcm_substream *substream)
{
	struct mt_audio *a = snd_soc_card_get_drvdata(c->card);

	return &a->stream[substream->stream];
}
static int mt_open(struct snd_soc_component *c, struct snd_pcm_substream *substream)
{
	struct mt_audio *a = snd_soc_card_get_drvdata(c->card);
	struct mt_stream *s = mt_get_stream(c, substream);
	int ret;

	if (substream->stream ? !allow_capture : !allow_playback)
		return -EPERM;
	substream->runtime->hw = mt_hw;
	ret = snd_pcm_hw_constraint_minmax(substream->runtime, SNDRV_PCM_HW_PARAM_BUFFER_BYTES,
					   8192, MT_CAPTURE_BYTES);
	if (ret < 0)
		return ret;
	ret = snd_pcm_hw_constraint_integer(substream->runtime, SNDRV_PCM_HW_PARAM_PERIODS);
	if (ret < 0)
		return ret;
	ret = snd_pcm_hw_constraint_step(substream->runtime, 0, SNDRV_PCM_HW_PARAM_BUFFER_BYTES, 32);
	if (ret)
		return ret;
	ret = snd_pcm_hw_constraint_step(substream->runtime, 0, SNDRV_PCM_HW_PARAM_PERIOD_BYTES, 32);
	if (ret)
		return ret;
	mutex_lock(&a->mutex);
	ret = -EBUSY;
	if (s->substream)
		goto out;
	ret = -EIO;
	if (a->fault)
		goto out;
	if (!a->users) {
		a->powered_before = !!(readl(a->spm + 0x180) & BIT(24));
		ret = mt_domain(a, true);
		if (ret)
			goto domain_fail;
		/* A second AFE owner (legacy persistent `start`, for example) is unsafe. */
		if (readl(a->afe + 0x010) & 0x3fe) {
			ret = -EBUSY;
			goto domain_fail;
		}
		a->saved_top = readl(a->afe);
		mt_update(a, 0, 0x4044, 0x4000); /* AFE + I2S only, not DAC/ADC */
	}
	a->users++;
	s->substream = substream;
	ret = 0;
	goto out;
domain_fail:
	if (!a->powered_before && mt_domain(a, false))
		a->fault = true;
out:
	mutex_unlock(&a->mutex);
	return ret;
}
static void mt_stop(struct mt_stream *s)
{
	struct mt_audio *a = s->audio;
	unsigned long flags;

	spin_lock_irqsave(&a->state_lock, flags);
	WRITE_ONCE(s->running, false);
	if (!s->prepared)
		goto out;
	if (s == &a->stream[0]) {
		mt_amp_off(a);
		mt_update(a, 0x010, BIT(2), 0);
		mt_update(a, 0x108, 1, 0);
		mt_update(a, 0x034, 1, 0);
		mt_update(a, 0x4c4, 0x10, 0x10);
		mt_update(a, 0x124, 1, 0);
	} else {
		AFE_STEPS(a, mt_fm_stop);
	}
	if (!READ_ONCE(a->stream[0].running) && !READ_ONCE(a->stream[1].running))
		mt_update(a, 0x010, 1, 0);
out:
	spin_unlock_irqrestore(&a->state_lock, flags);
}
static int mt_sync_stop(struct snd_soc_component *c, struct snd_pcm_substream *substream)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	struct mt_audio *a = s->audio;
	int ret = 0;

	hrtimer_cancel(&s->timer);
	mutex_lock(&a->mutex);
	if (!substream->stream) {
		ret = mt_codec_off(a);
		mt_update(a, 0, BIT(25) | BIT(26), a->saved_top);
	}
	s->prepared = false;
	mutex_unlock(&a->mutex);
	return ret;
}
static int mt_hw_free(struct snd_soc_component *c, struct snd_pcm_substream *substream)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	int ret;

	mt_stop(s);
	ret = mt_sync_stop(c, substream);
	mutex_lock(&s->audio->mutex);
	if (s->area) {
		dma_free_coherent(s->audio->dev, s->allocated, s->area, s->dma);
		s->area = NULL;
	}
	substream->runtime->dma_area = NULL;
	substream->runtime->dma_bytes = 0;
	mutex_unlock(&s->audio->mutex);
	return ret;
}
static int mt_close(struct snd_soc_component *c, struct snd_pcm_substream *substream)
{
	struct mt_audio *a = snd_soc_card_get_drvdata(c->card);
	struct mt_stream *s = mt_get_stream(c, substream);
	int ret;

	ret = mt_hw_free(c, substream);
	mutex_lock(&a->mutex);
	s->substream = NULL;
	if (!--a->users) {
		mt_update(a, 0, 0x06004044, a->saved_top);
		if (!a->powered_before && mt_domain(a, false)) {
			a->fault = true;
			ret = -EIO;
		}
	}
	mutex_unlock(&a->mutex);
	return ret;
}
static int mt_hw_params(struct snd_soc_component *c, struct snd_pcm_substream *substream,
			struct snd_pcm_hw_params *params)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	size_t bytes = params_buffer_bytes(params);

	if (params_rate(params) != MT_RATE || params_channels(params) != 2 ||
	    params_format(params) != SNDRV_PCM_FORMAT_S16_LE ||
	    bytes < 8192 || bytes > MT_CAPTURE_BYTES || bytes % 32 ||
	    params_period_bytes(params) % 32)
		return -EINVAL;
	if (s->area)
		return -EBUSY;
	mutex_lock(&s->audio->mutex);
	s->area = dma_alloc_coherent(s->audio->dev, bytes, &s->dma, GFP_KERNEL);
	if (!s->area) {
		mutex_unlock(&s->audio->mutex);
		return -ENOMEM;
	}
	s->allocated = bytes;
	substream->runtime->dma_area = s->area;
	substream->runtime->dma_addr = s->dma;
	substream->runtime->dma_bytes = bytes;
	mutex_unlock(&s->audio->mutex);
	return 0;
}
static int mt_prepare(struct snd_soc_component *c, struct snd_pcm_substream *substream)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	struct mt_audio *a = s->audio;
	u32 base = substream->stream ? 0x070 : 0x050;
	unsigned int i;
	int ret = 0;

	mt_stop(s);
	mt_sync_stop(c, substream);
	mutex_lock(&a->mutex);
	if (a->fault || !s->area) {
		ret = -EIO;
		goto out;
	}
	memset(s->area, 0, s->allocated);
	dma_wmb();
	writel(lower_32_bits(s->dma), a->afe + base);
	writel(lower_32_bits(s->dma + s->allocated - 1), a->afe + base + 8);
	mt_update(a, 0x0cc, substream->stream ? BIT(3) : BIT(1), 0);
	if (substream->stream) {
		AFE_STEPS(a, mt_fm_config);
	} else {
		for (i = 0; i < ARRAY_SIZE(mt_pins); i++) {
			mt_pin_mode(a, mt_pins[i], i < 3 ? 0 : 1);
			if (i >= 3)
				writel(BIT(mt_pins[i] % 32), a->gpio + mt_pins[i] / 32 * 0x10 + 8);
		}
		mt_amp_off(a);
		mt_update(a, 0, BIT(25) | BIT(26), 0);
		AFE_STEPS(a, mt_dl_config);
		ret = mt_codec_on(a);
		if (ret) {
			mt_update(a, 0, BIT(25) | BIT(26), a->saved_top);
			goto out;
		}
		mt_amp_on(a); /* ring is zero and DL2 disabled */
	}
	s->ring = (struct mt_ring) { .bytes = s->allocated,
		.period = frames_to_bytes(substream->runtime, substream->runtime->period_size) };
	s->prepared = true;
out:
	mutex_unlock(&a->mutex);
	return ret;
}
static int mt_trigger(struct snd_soc_component *c, struct snd_pcm_substream *substream, int cmd)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	struct mt_audio *a = s->audio;
	unsigned long flags;

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
		spin_lock_irqsave(&a->state_lock, flags);
		if (!s->prepared || s->running) {
			spin_unlock_irqrestore(&a->state_lock, flags);
			return -EINVAL;
		}
		s->ring.sampled_ns = s->ring.changed_ns = ktime_get_ns();
		WRITE_ONCE(s->running, true);
		dma_wmb();
		if (substream->stream) {
			AFE_STEPS(a, mt_fm_start);
		} else {
			mt_update(a, 0x108, 1, 1);
			mt_update(a, 0x034, 1, 1);
			mt_update(a, 0x124, 1, 1);
			mt_update(a, 0x010, 5, 5);
		}
		hrtimer_start(&s->timer, ns_to_ktime(1000000), HRTIMER_MODE_REL);
		spin_unlock_irqrestore(&a->state_lock, flags);
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
		mt_stop(s);
		return 0;
	default:
		return -EINVAL;
	}
}
static snd_pcm_uframes_t mt_pointer(struct snd_soc_component *c,
				   struct snd_pcm_substream *substream)
{
	struct mt_stream *s = mt_get_stream(c, substream);

	if (READ_ONCE(s->ring.xrun))
		return SNDRV_PCM_POS_XRUN;
	return bytes_to_frames(substream->runtime, READ_ONCE(s->ring.last));
}
static int mt_copy(struct snd_soc_component *c, struct snd_pcm_substream *substream,
		   int channel, unsigned long pos, struct iov_iter *iter, unsigned long bytes)
{
	struct mt_stream *s = mt_get_stream(c, substream);
	struct mt_audio *a = s->audio;
	u32 at, n, i;
	s16 *samples = NULL;
	unsigned int copied = 0;
	int ret = 0;

	if (!s->area || pos >= s->allocated || bytes > s->allocated || pos % 4 || bytes % 4)
		return -EINVAL;
	/* Never publish unscaled user samples into an active DMA ring, even for
	 * the short interval between a copy_from_iter() and its gain pass.
	 */
	if (!substream->stream) {
		samples = kmalloc(bytes, GFP_KERNEL);
		if (!samples)
			return -ENOMEM;
		if (copy_from_iter(samples, bytes, iter) != bytes) {
			kfree(samples);
			return -EFAULT;
		}
	}
	mutex_lock(&a->mutex);
	if (samples)
		for (i = 0; i < bytes / 2; i++)
			samples[i] = mt_scale(samples[i], a->volume);
	while (bytes) {
		at = pos;
		n = mt_span(at, bytes, s->allocated);
		if (substream->stream) {
			dma_rmb();
			if (copy_to_iter(s->area + at, n, iter) != n) {
				ret = -EFAULT;
				break;
			}
		} else {
			memcpy(s->area + at, (char *)samples + copied, n);
			dma_wmb();
		}
		copied += n;
		bytes -= n;
		pos = (pos + n) % s->allocated;
	}
	mutex_unlock(&a->mutex);
	kfree(samples);
	return ret;
}
static int mt_mmap(struct snd_soc_component *c, struct snd_pcm_substream *s,
		   struct vm_area_struct *vma)
{
	return -ENXIO; /* no bypass of software PCM gain */
}
static int mt_volume_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct snd_soc_component *c = snd_kcontrol_chip(k);
	struct mt_audio *a = snd_soc_card_get_drvdata(c->card);

	v->value.integer.value[0] = READ_ONCE(a->volume);
	return 0;
}
static int mt_volume_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct snd_soc_component *c = snd_kcontrol_chip(k);
	struct mt_audio *a = snd_soc_card_get_drvdata(c->card);
	long volume = v->value.integer.value[0];
	int changed;

	if (volume < 0 || volume > 100)
		return -EINVAL;
	mutex_lock(&a->mutex);
	changed = a->volume != volume;
	a->volume = volume;
	if (!volume && a->stream[0].area) {
		memset(a->stream[0].area, 0, a->stream[0].allocated);
		dma_wmb();
	}
	mutex_unlock(&a->mutex);
	return changed;
}
static const struct snd_kcontrol_new mt_controls[] = {
	SOC_SINGLE_EXT("PCM Playback Volume", SND_SOC_NOPM, 0, 100, 0,
		       mt_volume_get, mt_volume_put),
};
static const struct snd_soc_component_driver mt_component = {
	.module_get_upon_open = 1,
	.name = "mt6755-tablet", .open = mt_open, .close = mt_close,
	.hw_params = mt_hw_params, .hw_free = mt_hw_free, .prepare = mt_prepare,
	.trigger = mt_trigger, .sync_stop = mt_sync_stop, .pointer = mt_pointer,
	.copy = mt_copy, .mmap = mt_mmap,
	.controls = mt_controls, .num_controls = ARRAY_SIZE(mt_controls),
};
#define MT_DAI_STREAM(n) { .stream_name = n, .channels_min = 2, .channels_max = 2, \
	.rates = SNDRV_PCM_RATE_48000, .formats = SNDRV_PCM_FMTBIT_S16_LE }
/* Real DMA CPU DAIs and board endpoints; no snd_dummy or fake PCM producer. */
static struct snd_soc_dai_driver mt_dais[] = {
	{ .name = "mt6755-dl2", .playback = MT_DAI_STREAM("DL2 Playback") },
	{ .name = "mt6353-speaker", .playback = MT_DAI_STREAM("Speaker Endpoint") },
	{ .name = "mt6755-awb", .capture = MT_DAI_STREAM("FM AWB Capture") },
	{ .name = "connsys-fm", .capture = MT_DAI_STREAM("FM Digital Endpoint") },
};
SND_SOC_DAILINK_DEFS(dl2, DAILINK_COMP_ARRAY(COMP_CPU("mt6755-dl2")),
	DAILINK_COMP_ARRAY(COMP_CODEC("mt6755-tablet", "mt6353-speaker")),
	DAILINK_COMP_ARRAY(COMP_PLATFORM("mt6755-tablet")));
SND_SOC_DAILINK_DEFS(awb, DAILINK_COMP_ARRAY(COMP_CPU("mt6755-awb")),
	DAILINK_COMP_ARRAY(COMP_CODEC("mt6755-tablet", "connsys-fm")),
	DAILINK_COMP_ARRAY(COMP_PLATFORM("mt6755-tablet")));
static struct snd_soc_dai_link mt_links[] = {
	{ .name = "DL2-MT6353-AW8736", .stream_name = "Speaker", .id = 0,
	  SND_SOC_DAILINK_REG(dl2), .playback_only = 1 },
	{ .name = "CONNSYS-ASRC-AWB", .stream_name = "FM Digital", .id = 1,
	  SND_SOC_DAILINK_REG(awb), .capture_only = 1 },
};
static int mt_match_gpio(struct gpio_chip *gc, const void *unused)
{
	return gc->parent && of_device_is_compatible(gc->parent->of_node,
						    "mediatek,mt6755-pinctrl");
}
static void mt_free_pins(void *data)
{
	struct mt_audio *a = data;
	int i;

	for (i = 0; i < ARRAY_SIZE(mt_pins); i++)
		if (a->pins[i])
			gpiochip_free_own_desc(a->pins[i]);
	if (a->gpio_device)
		gpio_device_put(a->gpio_device);
}
/* The owner reports actual idle registers; never invent a silence attestation.
 * Use the serialized PMIC API, not the global userspace debug-address selector.
 */
static ssize_t analogue_status_show(struct device *dev,
				    struct device_attribute *attr, char *buf)
{
	struct snd_soc_card *card = dev_get_drvdata(dev);
	struct mt_audio *a = snd_soc_card_get_drvdata(card);
	u16 auddec, ul_dl;
	int ret;

	mutex_lock(&a->mutex);
	ret = -EBUSY;
	if (a->users)
		goto out;
	ret = mt6755_pmic_read(0x0cf2, &auddec);
	if (ret)
		goto out;
	ret = mt6755_pmic_read(0x2000, &ul_dl);
	if (ret)
		goto out;
	ret = sysfs_emit(buf,
		"PWR_STATUS.audio=%u DAC_CON0=0x%08x ADDA_DL=0x%08x AUDDEC0=0x%04x UL_DL=0x%04x\n",
		!!(readl(a->spm + 0x180) & BIT(24)), readl(a->afe + 0x010),
		readl(a->afe + 0x108), auddec, ul_dl);
out:
	mutex_unlock(&a->mutex);
	return ret;
}
static DEVICE_ATTR_RO(analogue_status);
static struct attribute *mt_status_attrs[] = { &dev_attr_analogue_status.attr, NULL };
static const struct attribute_group mt_status_group = { .attrs = mt_status_attrs };

static int mt_probe(struct platform_device *pdev)
{
	struct mt_audio *a;
	struct gpio_chip *gc;
	int i, ret;

	a = devm_kzalloc(&pdev->dev, sizeof(*a), GFP_KERNEL);
	if (!a)
		return -ENOMEM;
	a->dev = &pdev->dev;
	a->volume = 30;
	mutex_init(&a->mutex);
	spin_lock_init(&a->reg_lock);
	spin_lock_init(&a->state_lock);
	platform_set_drvdata(pdev, a);
	/* Exclusive AFE register aperture, independent of whether a PCM is open. */
	a->afe = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(a->afe))
		return PTR_ERR(a->afe);
	a->spm = devm_ioremap(a->dev, 0x10006000, 0x1000);
	if (!a->spm)
		return -ENOMEM;
	if (allow_playback) {
		a->gpio = devm_ioremap(a->dev, 0x10005000, 0x1000);
		if (!a->gpio)
			return -ENOMEM;
		a->gpio_device = gpio_device_find(NULL, mt_match_gpio);
		if (!a->gpio_device)
			return -EPROBE_DEFER;
		ret = devm_add_action_or_reset(a->dev, mt_free_pins, a);
		if (ret)
			return ret;
		gc = gpio_device_get_chip(a->gpio_device);
		if (!gc)
			return -ENODEV;
		for (i = 0; i < ARRAY_SIZE(mt_pins); i++) {
			a->pins[i] = gpiochip_request_own_desc(gc, mt_pins[i],
				"mt6755-audio", GPIO_ACTIVE_HIGH, GPIOD_ASIS);
			if (IS_ERR(a->pins[i])) {
				ret = PTR_ERR(a->pins[i]);
				a->pins[i] = NULL;
				return ret;
			}
		}
	}
	a->dev->dma_mask = &a->dev->coherent_dma_mask;
	ret = dma_set_mask_and_coherent(a->dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	for (i = 0; i < 2; i++) {
		a->stream[i].audio = a;
		hrtimer_setup(&a->stream[i].timer, mt_poll, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	}
	ret = devm_snd_soc_register_component(a->dev, &mt_component, mt_dais, ARRAY_SIZE(mt_dais));
	if (ret)
		return ret;
	a->card = (struct snd_soc_card) { .name = "MT6755", .owner = THIS_MODULE,
		.dev = a->dev, .dai_link = mt_links, .num_links = ARRAY_SIZE(mt_links) };
	/* Card registration replaces dev->driver_data; keep callback state here. */
	snd_soc_card_set_drvdata(&a->card, a);
	ret = devm_snd_soc_register_card(a->dev, &a->card);
	if (ret)
		return ret;
	return devm_device_add_group(a->dev, &mt_status_group);
}
static struct platform_driver mt_driver = {
	.probe = mt_probe, .driver = { .name = "mt6755-tablet" },
};
static struct platform_device *mt_device;
static int __init mt_audio_init(void)
{
	static const struct resource resources[] = {
		DEFINE_RES_MEM(0x11220000, 0x1000),
	};
	int ret;

	if (!legacy_quiesced || !of_machine_is_compatible("mediatek,k50v1-tablet"))
		return -EPERM;
	ret = platform_driver_register(&mt_driver);
	if (ret)
		return ret;
	mt_device = platform_device_register_simple("mt6755-tablet", -1,
						     resources, ARRAY_SIZE(resources));
	if (IS_ERR(mt_device)) {
		ret = PTR_ERR(mt_device);
		platform_driver_unregister(&mt_driver);
		return ret;
	}
	return 0;
}
static void __exit mt_exit(void)
{
	platform_device_unregister(mt_device);
	platform_driver_unregister(&mt_driver);
}
module_init(mt_audio_init);
module_exit(mt_exit);
MODULE_DESCRIPTION("MT6755 tablet DL2/MT6353/AW8736 and digital CONNSYS FM AWB candidate");
MODULE_LICENSE("GPL");
