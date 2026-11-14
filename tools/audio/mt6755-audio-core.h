/* SPDX-License-Identifier: GPL-2.0 */
#ifndef MT6755_AUDIO_CORE_H
#define MT6755_AUDIO_CORE_H
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#include <stdbool.h>
typedef uint32_t u32;
typedef uint16_t u16;
typedef int16_t s16;
typedef uint64_t u64;
#endif

#define MT_RATE 48000U
#define MT_FRAME_BYTES 4U
#define MT_SRAM_BASE 0x11221000U
#define MT_SRAM_BYTES 0xc000U
#define MT_CAPTURE_BYTES 0x10000U

struct mt_step { u16 reg; u32 mask, value; };
/* MT6750 BSP mt_soc_afe_control.c: Set2ndI2SIn, SetI2SASRCConfig.
 * AWB destinations only: masks generated from the BSP connection tables for
 * O05/O06, including shifted connections. No ADDA, PMIC, GPIO or DAC writes.
 */
static const struct mt_step mt_fm_config[] = {
	{0x0cc, 1U << 3, 0}, /* AWB DMA address below 4GB */
	{0x028, 0x03fd0000, 0}, {0x420, 0x3000, 0},
	{0x438, 0x3000, 0}, {0x030, 0x1803e0, 0},
	{0x460, 0x03c00000, 0},
	{0x018, 0xfffffffe, 0x8000000c}, /* internal CONNSYS, slave I2S16 */
	{0x4c4, 0x100, 0},
	{0x030, 1U << 30, 0}, /* ASRC, not bypass */
	{0x014, 0x00000f00, 0x00000a00}, /* I2S target 48k */
	{0x550, 1U << 16, 0}, /* stereo */
	{0x554, 0xffffffff, 0x600000},
	{0x558, 0xffffffff, 0x400000},
	{0x560, 0xffffffff, 0xcb2},
	{0x55c, 0xffffffff, 0x75987},
	{0x56c, 0xffffffff, 0x1b00},
	{0x014, 0x0100f000, 0x0000a000}, /* AWB stereo 48k */
	{0x3d8, 0x00300000, 0}, /* AWB S16 */
	{0x06c, 0x60, 0}, /* O05/O06 S16 */
	{0x028, (1U << 16) | (1U << 22), (1U << 16) | (1U << 22)},
};
static const struct mt_step mt_fm_start[] = {
	{0x500, 0x41, 0x41}, {0x018, 1, 1}, {0x010, 0x41, 0x41},
};
static const struct mt_step mt_fm_stop[] = {
	{0x010, 1U << 6, 0}, {0x018, 1, 0}, {0x500, 0x41, 0},
	{0x030, 1U << 30, 1U << 30},
	{0x028, (1U << 16) | (1U << 22), 0},
};
static const struct mt_step mt_dl_config[] = {
	/* Remove alternate/shifted O03/O04 inputs too, so a stale direct FM
	 * connection cannot bypass the software-gained DL2 PCM route.
	 */
	{0x024, 0xffff0000, 0}, {0x028, 0x0000fffb, 0},
	{0x420, 0x0f00, 0}, {0x438, 0x0f00, 0},
	{0x02c, 0xf8000000, 0}, {0x030, 0x1001f, 0},
	{0x05c, 3, 0}, {0x460, 0x003f4000, 0}, {0x0bc, 0x8000, 0},
	{0x014, 0x004000f0, 0xa0}, /* DL2 stereo 48k */
	{0x3d8, 0x000c0000, 0}, /* DL2 S16 */
	{0x0cc, 1U << 1, 0}, /* DMA address below 4GB */
	{0x034, 0xffffffff, 0x00000a08}, /* I2S DAC S16/48k, off */
	{0x4c4, 0x10, 0},
	{0x260, 0xffffffff, 0}, {0x264, 0xffffffff, 0},
	{0x024, 1U << 23, 1U << 23}, /* I07 -> O03 */
	{0x028, 1U << 8, 1U << 8}, /* I08 -> O04 */
	{0x06c, 0x18, 0}, /* O03/O04 S16 */
	{0x138, 0xffffffff, 0x03f87200},
	{0x13c, 0xffffffff, 0x03117180},
	{0x10c, 0xffffffff, 0xf74f0000},
	{0x108, 0xffffffff, 0x83001802}, /* configure, leave off until trigger */
};

/* Same code is compiled in the module and the host-only regression executable. */
static inline s16 mt_scale(s16 sample, unsigned int percent)
{
	return (int)sample * (int)percent / 100;
}
static inline unsigned int mt_span(unsigned int pos, unsigned int bytes,
				   unsigned int buffer)
{
	return bytes < buffer - pos ? bytes : buffer - pos;
}
struct mt_ring {
	u32 bytes, period, last, residue;
	u64 sampled_ns, changed_ns;
	bool xrun;
};
/* Return elapsed periods, or -1 on invalid/stale/ambiguous DMA position.
 * Sampling cannot recover whole wraps. Reject a >= ring-duration delay rather
 * than synthesising progress; a stopped FM clock becomes XRUN, not fake PCM.
 */
static inline int mt_advance(struct mt_ring *r, u32 pos, u64 now)
{
	u32 delta, periods;
	u64 duration = (u64)r->bytes * 1000000000ULL / (MT_RATE * MT_FRAME_BYTES);

	if (!r->bytes || !r->period || pos >= r->bytes || pos % MT_FRAME_BYTES ||
	    now < r->sampled_ns || now - r->sampled_ns >= duration ||
	    now - r->changed_ns >= 100000000ULL) {
		r->xrun = true;
		return -1;
	}
	delta = (pos + r->bytes - r->last) % r->bytes;
	if (delta)
		r->changed_ns = now;
	r->sampled_ns = now;
	r->last = pos;
	r->residue += delta;
	periods = r->residue / r->period;
	r->residue %= r->period;
	return periods;
}
#endif
