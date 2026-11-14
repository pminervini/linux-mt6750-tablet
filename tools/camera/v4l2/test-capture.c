// SPDX-License-Identifier: GPL-2.0
/* SYNTHETIC MOCKS ONLY. No device files, I2C, DMA or module activation. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mtk-capture.h"

/* Actual sensor control body, mocked I2C only; no sensor or MMIO access. */
struct camera { unsigned index; int exposure_lines, gain_units, test_pattern; };
struct board_reg { u32 reg, value; };
#define atomic_read(p) (*(p))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static u8 sensor_regs[65536];
static unsigned writes, fail_write;
static bool fail_read;
int sensor_read(struct camera *c, u32 reg) {
 assert(!c->index && reg == 0x5000);
 return fail_read ? -EIO : sensor_regs[reg];
}
static int sensor_table(struct camera *c, const struct board_reg *table, unsigned count) {
 (void)c;
 for(unsigned i=0;i<count;i++) {
  if(++writes==fail_write) return -EREMOTEIO;
  assert(table[i].reg<sizeof(sensor_regs) && table[i].value<=255);
  sensor_regs[table[i].reg]=table[i].value;
 }
 return 0;
}
#include "extracted-sensor-controls.h"
static void test_sensor_controls(void) {
 for(unsigned index=0;index<2;index++) for(unsigned pattern=0;pattern<2;pattern++) {
  struct camera c={.index=index,.exposure_lines=623,.gain_units=index?64:128,.test_pattern=pattern};
  memset(sensor_regs,0xee,sizeof(sensor_regs)); sensor_regs[0x5000]=0xaf;
  writes=fail_write=0; fail_read=false;
  assert(!sensor_controls(&c)); assert(writes==(index?8U:7U));
  if(index) { assert(!sensor_regs[0xfe]); assert(sensor_regs[0x8c]==pattern); }
  else { assert(sensor_regs[0x5e00]==(pattern?0x80:0)); assert(sensor_regs[0x5000]==((0xaf&0xbf)|(pattern?0:0x40))); }
  for(fail_write=1;fail_write<=(index?8U:7U);fail_write++) {
   writes=0; assert(sensor_controls(&c)==-EREMOTEIO);
  }
  fail_write=0;
  if(!index) { fail_read=true; writes=0; assert(sensor_controls(&c)==-EIO); assert(!writes); fail_read=false; }
 }
}

struct mock {
	u32 regs[0x500 / 4];
	u64 time, cancel_at;
	unsigned int resets, frames, reset_failure, ticks;
	bool running, done, error, never_done, stale;
};
static u32 mock_read(void *ctx, u32 reg)
{
	struct mock *m = ctx;

	assert(reg < sizeof(m->regs) && reg % 4 == 0);
	if (reg == 0x10 && m->regs[reg / 4] == 1)
		return m->resets == m->reset_failure ? 1 : 3;
	if (reg == 0xc) {
		if (!m->running) {
			u32 v = m->stale ? 1U << 10 : 0;

			m->stale = false;
			return v;
		}
		if (m->error)
			return 1U << 16;
		if (!m->never_done && ++m->ticks > 2) {
			m->done = true;
			return 1U << 10;
		}
		return 0;
	}
	return m->regs[reg / 4];
}
static void mock_write(void *ctx, u32 reg, u32 value)
{
	struct mock *m = ctx;

	assert(reg < sizeof(m->regs) && reg % 4 == 0);
	if (reg == 0x208)
		assert(!m->running); /* no address replacement while DMA can run */
	if (reg == 0x10 && value == 1)
		m->resets++;
	if (reg == 0x414) {
		if (value) {
			assert(!m->running);
			assert(!m->stale);
			assert(m->regs[0x208 / 4] == 0x50000000);
			assert(m->regs[0x1c / 4] == ((1U << 14) | (1U << 16)));
			assert(m->regs[0x3c / 4] == 2); /* no two-pixel mode */
			assert(m->regs[4 / 4] == ((1U << 24) | 1));
			assert(m->regs[8 / 4] == ((1U << 4) | (1U << 5) | (1U << 10) | (1U << 16)));
			m->frames++; m->ticks = 0;
		}
		m->running = value != 0;
	}
	m->regs[reg / 4] = value;
}
static void mock_sleep(void *ctx, unsigned int us)
{
	((struct mock *)ctx)->time += us;
}
static u64 mock_time(void *ctx)
{
	return ((struct mock *)ctx)->time;
}
static bool mock_cancel(void *ctx)
{
	struct mock *m = ctx;

	return m->cancel_at && m->time >= m->cancel_at;
}
static struct mtk_io io_for(struct mock *m)
{
	return (struct mtk_io){
		.ctx = m, .read = mock_read, .write = mock_write,
		.sleep_us = mock_sleep, .now_us = mock_time, .cancelled = mock_cancel,
	};
}
static void test_frames(void)
{
	const u32 sizes[][2] = { {1632,1224}, {3264,2448}, {1600,1200} };
	unsigned int i, frame;

	for (i = 0; i < 3; i++) {
		struct mock m = { .stale = true };
		struct mtk_io io = io_for(&m);
		bool safe;

		for (frame = 0; frame < 128; frame++) {
			assert(!mtk_dma_frame(&io, sizes[i][0], sizes[i][1], 0x50000000, 3000000, &safe));
			assert(safe && m.done && !m.running);
			assert(m.frames == frame + 1 && m.resets == 2 * (frame + 1));
			assert(io.completed_us <= m.time && io.completed_us);
			assert(m.regs[0x210 / 4] == sizes[i][0] * 5 / 4 - 1);
			assert(m.regs[0x214 / 4] == sizes[i][1] - 1);
			assert((m.regs[0x218 / 4] & 0xffff) == sizes[i][0] * 5 / 4);
			assert(!m.regs[0x20 / 4] && !m.regs[0x1c / 4]);
		}
	}
}
static void test_failures(void)
{
	unsigned int type;

	for (type = 0; type < 6; type++) {
		struct mock m = { 0 };
		struct mtk_io io = io_for(&m);
		bool safe = false;
		int ret;

		switch (type) {
		case 0: m.never_done = true; break;
		case 1: m.error = true; break;
		case 2: m.cancel_at = 500; break;
		case 3: m.reset_failure = 1; break;
		case 4: m.reset_failure = 2; break;
		case 5: m.stale = true; m.never_done = true; break;
		}
		ret = mtk_dma_frame(&io, 1632, 1224, 0x50000000, 3000, &safe);
		assert(ret == (type == 1 ? -EIO : type == 2 ? -ECANCELED : -ETIMEDOUT));
		assert(safe == (type != 3 && type != 4));
		assert(!m.running);
		if (!safe)
			assert(m.regs[0x10 / 4] == 1); /* trigger stays asserted, no false fence */
		if (type == 3)
			assert(!m.frames); /* do not arm after reset timeout */
	}
	{
		struct mock m = { 0 };
		struct mtk_io io = io_for(&m);
		bool safe;

		assert(mtk_dma_frame(&io, 3, 1200, 0x50000000, 1, &safe) == -EINVAL);
		assert(mtk_dma_frame(&io, 4000, 1200, 0x50000000, 1, &safe) == -EINVAL);
		assert(mtk_dma_frame(&io, 1600, 0, 0x50000000, 1, &safe) == -EINVAL);
		assert(mtk_dma_frame(&io, 1600, 1200, 0x50000001, 1, &safe) == -EINVAL);
		assert(!m.resets && !m.frames);
	}
}
static void test_unpack(void)
{
	u8 src[5], dst[10];
	u32 i, j, seed = 17;

	for (i = 0; i < 100000; i++) {
		u64 packed = 0;
		u16 expected[4];

		for (j = 0; j < 4; j++) {
			seed = seed * 1664525U + 1013904223U;
			expected[j] = i < 1024 ? i : (seed >> 16) & 1023;
			packed |= (u64)expected[j] << (10 * j);
		}
		for (j = 0; j < 5; j++)
			src[j] = packed >> (8 * j);
		memset(dst, 0xaa, sizeof(dst));
		mtk_unpack10(dst + 1, src, 4);
		assert(dst[0] == 0xaa && dst[9] == 0xaa);
		for (j = 0; j < 4; j++)
			assert((dst[1 + 2*j] | (dst[2 + 2*j] << 8)) == expected[j]);
	}
}
static void test_queue(void)
{
	struct mtk_fifo q = { 0 };
	unsigned int buffers[9], i, round;
	void *p;

	assert(!mtk_fifo_pop(&q)); /* starvation: no buffer means no capture */
	assert(mtk_fifo_push(&q, NULL) == -EINVAL);
	assert(!mtk_fifo_push(&q, &buffers[0]));
	assert(mtk_fifo_push(&q, &buffers[0]) == -EINVAL);
	for (i = 1; i < 8; i++)
		assert(!mtk_fifo_push(&q, &buffers[i]));
	assert(mtk_fifo_push(&q, &buffers[8]) == -EINVAL);
	for (round = 0; round < 1000; round++) {
		for (i = 0; i < 8; i++) {
			p = mtk_fifo_pop(&q);
			assert(p == &buffers[i]);
			assert(!mtk_fifo_push(&q, p)); /* only after simulated DQBUF/QBUF */
		}
	}
	for (i = 0; i < 8; i++)
		assert(mtk_fifo_pop(&q) == &buffers[i]);
	assert(!q.count && !mtk_fifo_pop(&q)); /* stop/failure drain returns each once */
}
int main(void)
{
	test_sensor_controls();
	test_frames(); test_failures(); test_unpack(); test_queue();
	puts("PASS mock capture: 384 rearmed frames / all 3 sizes; reset/error/cancel/timeout fences; 400000 pixels; FIFO wrap/drain/duplicate/bounds");
	return 0;
}
