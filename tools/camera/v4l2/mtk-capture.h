/* SPDX-License-Identifier: GPL-2.0 */
/* Shared by the kernel candidate and the synthetic, no-device C tests. */
#ifndef MTK_CAPTURE_H
#define MTK_CAPTURE_H
#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/errno.h>
#else
#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

/* Bounded FIFO, external locking in kernel. Same ownership operations are
 * exercised by the offline mock harness. vb2 owns buffers before/after this FIFO.
 */
#define MTK_QUEUE_DEPTH 8
struct mtk_fifo {
	void *items[MTK_QUEUE_DEPTH];
	unsigned int head, count;
};
static inline int mtk_fifo_push(struct mtk_fifo *q, void *buffer)
{
	unsigned int i;

	if (!buffer || q->count == MTK_QUEUE_DEPTH)
		return -EINVAL;
	for (i = 0; i < q->count; i++)
		if (q->items[(q->head + i) % MTK_QUEUE_DEPTH] == buffer)
			return -EINVAL;
	q->items[(q->head + q->count++) % MTK_QUEUE_DEPTH] = buffer;
	return 0;
}
static inline void *mtk_fifo_pop(struct mtk_fifo *q)
{
	void *buffer;

	if (!q->count)
		return 0;
	buffer = q->items[q->head];
	q->items[q->head] = 0;
	q->head = (q->head + 1) % MTK_QUEUE_DEPTH;
	q->count--;
	return buffer;
}

struct mtk_io {
	void *ctx;
	u32 (*read)(void *ctx, u32 reg);
	void (*write)(void *ctx, u32 reg, u32 value);
	void (*sleep_us)(void *ctx, unsigned int duration_us);
	u64 (*now_us)(void *ctx);
	bool (*cancelled)(void *ctx);
	u64 completed_us; /* polling observation of PASS1_DONE, before reset/unpack */
};

static inline void mtk_wr(struct mtk_io *io, u32 reg, u32 val)
{
	io->write(io->ctx, reg, val);
}

/* IMGO reset acknowledges that no earlier DMA can still target the buffer.
 * Never clear its trigger / recycle storage if the acknowledgement is absent.
 */
static inline int mtk_dma_reset(struct mtk_io *io)
{
	u64 end = io->now_us(io->ctx) + 100000;

	mtk_wr(io, 0x010, 4);
	mtk_wr(io, 0x010, 0);
	mtk_wr(io, 0x010, 1);
	do {
		if (io->read(io->ctx, 0x010) == 3) {
			mtk_wr(io, 0x010, 0);
			return 0;
		}
		io->sleep_us(io->ctx, 100);
	} while (io->now_us(io->ctx) < end);
	return -ETIMEDOUT;
}

static inline int mtk_dma_halt(struct mtk_io *io)
{
	int ret;

	mtk_wr(io, 0x414, 0); /* VFDATA_EN off before CMOS / FBC. */
	mtk_wr(io, 0x410, 0);
	mtk_wr(io, 0x01c, 0);
	mtk_wr(io, 0x008, 0); /* Polling: do not assert an unowned IRQ. */
	ret = mtk_dma_reset(io);
	mtk_wr(io, 0x000, 0);
	if (!ret)
		mtk_wr(io, 0x020, 0);
	return ret;
}

/* Repeated hardware frames, one FBC slot in a private coherent staging buffer.
 * Queued vb2 buffers are never DMA targets. Each successful frame is fenced
 * before unpack, and each new frame has a freshly armed address and FBC count.
 * This favours auditable ownership over an unverified continuous IRQ ring.
 */
static inline int mtk_dma_frame(struct mtk_io *io, u32 width, u32 height,
			       u32 address, u64 timeout_us, bool *safe)
{
	u32 bpl, status;
	u64 end;
	int ret, halt_ret;

	*safe = true;
	io->completed_us = 0;
	if (!width || width % 4 || width > 3264 || !height || height > 2448 ||
	    (address & 15) || !timeout_us)
		return -EINVAL;
	if (io->cancelled(io->ctx))
		return -ECANCELED;
	bpl = width * 5 / 4;
	*safe = false;
	ret = mtk_dma_halt(io);
	if (ret)
		return ret;
	mtk_wr(io, 0x418, width << 16);
	mtk_wr(io, 0x41c, height << 16);
	mtk_wr(io, 0x420, 0x1000);
	mtk_wr(io, 0x000, (1U << 30) | 1 | 4);
	mtk_wr(io, 0x004, (1U << 24) | 1);
	mtk_wr(io, 0x03c, 2);
	mtk_wr(io, 0x210, bpl - 1);
	mtk_wr(io, 0x214, height - 1);
	mtk_wr(io, 0x218, (1U << 19) | (1U << 16) | bpl);
	mtk_wr(io, 0x21c, (8U << 28) | 0x80);
	mtk_wr(io, 0x220, (2U << 8) | (2U << 16));
	mtk_wr(io, 0x208, address);
	mtk_wr(io, 0x410, 1);
	mtk_wr(io, 0x000, (1U << 30) | 1 | 4 | 16);
	mtk_wr(io, 0x01c, (1U << 14) | (1U << 16));
	mtk_wr(io, 0x01c, (1U << 14) | (1U << 16) | (1U << 11));
	mtk_wr(io, 0x01c, (1U << 14) | (1U << 16));
	mtk_wr(io, 0x020, 1 | 4 | (1U << 15));
	io->read(io->ctx, 0x00c); /* Read-clear stale status before the new frame. */
	mtk_wr(io, 0x414, 1);
	end = io->now_us(io->ctx) + timeout_us;
	ret = -ETIMEDOUT;
	do {
		if (io->cancelled(io->ctx)) {
			ret = -ECANCELED;
			break;
		}
		status = io->read(io->ctx, 0x00c);
		if (status & ((1U << 4) | (1U << 5) | (1U << 16))) {
			ret = -EIO;
			break;
		}
		if (status & (1U << 10)) {
			io->completed_us = io->now_us(io->ctx);
			ret = 0;
			break;
		}
		io->sleep_us(io->ctx, 500);
	} while (io->now_us(io->ctx) < end);
	halt_ret = mtk_dma_halt(io);
	*safe = !halt_ret;
	return halt_ret ? halt_ret : ret;
}

/* CAMSV LE bitstream RAW10 -> V4L2 SBGGR10 (LE16, bits 15:10 zero).
 * Not MIPI's four MSB bytes followed by a shared LSB byte.
 */
static inline void mtk_unpack10(u8 *dst, const u8 *src, u32 pixels)
{
	u32 i, j;

	for (i = 0; i < pixels; i += 4, src += 5, dst += 8) {
		u16 p[4] = {
			src[0] | ((src[1] & 3) << 8),
			(src[1] >> 2) | ((src[2] & 15) << 6),
			(src[2] >> 4) | ((src[3] & 63) << 4),
			(src[3] >> 6) | (src[4] << 2),
		};

		for (j = 0; j < 4; j++) {
			dst[2 * j] = p[j] & 255;
			dst[2 * j + 1] = p[j] >> 8;
		}
	}
}
#endif
