// SPDX-License-Identifier: GPL-2.0
/* Production GPIO alias functions, extracted and run against mock MMIO. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
struct board { void *gpio; };
static u32 registers[0x500 / 4];
static unsigned int writes, offsets[4];
static void writel(u32 value, void *address)
{
	unsigned int offset = (char *)address - (char *)registers;
	unsigned int alias = offset & 15;

	assert(offset < sizeof(registers) && (alias == 4 || alias == 8));
	assert(writes < 4); offsets[writes++] = offset;
	if (alias == 4) registers[(offset - 4) / 4] |= value;
	else registers[(offset - 8) / 4] &= ~value;
}
#include "extracted-pins.h"
int main(void)
{
	const unsigned int pins[] = {107,108,110,109,161,112,113};
	struct board b = { registers };

	for (unsigned int i = 0; i < sizeof(pins)/sizeof(pins[0]); i++) {
		for (unsigned int mode = 0; mode < 8; mode++) {
			unsigned int pin = pins[i], reg = mode_reg(pin), shift = mode_shift(pin);
			memset(registers, 0x5a, sizeof(registers)); writes = 0;
			pin_mode(&b, pin, mode);
			assert(registers[reg/4] == ((0x5a5a5a5a & ~(7U << shift)) | mode << shift));
			assert(offsets[0] == reg + 8 && writes == (mode ? 2U : 1U));
			if (mode) assert(offsets[1] == reg + 4);
			/* GPIO117-119 occupy 22..24/25..27/28..30 of 0x3b0. */
			if (reg == 0x3b0) assert((registers[reg/4] & 0x7fc00000) == (0x5a5a5a5a & 0x7fc00000));
		}
		for (unsigned int high = 0; high < 2; high++) {
			unsigned int pin = pins[i], bank = pin/32 * 16;
			writes = 0;
			pin_out(&b, pin, high);
			assert(writes == 2 && offsets[0] == 0x100 + bank + (high ? 4 : 8));
			assert(offsets[1] == bank + 4); /* latch-before-direction */
		}
	}
	puts("PASS extracted production GPIO: all modes use SET/CLR only, neighbour bits preserved incl CONSYS117-119; output latch before direction");
	return 0;
}
