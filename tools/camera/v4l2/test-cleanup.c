// SPDX-License-Identifier: GPL-2.0
/* Compiles the production power_down() body extracted by run_tests.py.
 * SYNTHETIC failure injection, not a hardware shutdown test. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define dev_err(...) ((void)0)
typedef uint16_t u16;
struct device { int unused; };
struct platform { struct device dev; };
struct board {
	struct platform *pdev;
	bool isp_accessible, safe, snapshot, domain_started, poisoned;
	unsigned int *sen, *larb, *img;
	unsigned int larb_saved, img_saved;
	u16 pmic_saved[6];
	int io;
};
struct camera { struct board *board; bool sensor_started; };
static const u16 pmic_regs[] = { 0xad6, 0xaa0, 0xaca, 0xa54, 0xa50, 0xa58 };
static const u16 pmic_masks[] = { 0x300, 0x300, 0x700, 2, 2, 2 };
static int stop_fail, halt_fail, domain_fail, pmic_fail;
static unsigned int stops, halts, domains, pmics, pins, restores, writes;
static int sensor_stop(struct camera *c) { assert(c->sensor_started); stops++; return stop_fail; }
static int mtk_dma_halt(int *io) { (void)io; halts++; return halt_fail; }
static void writel(unsigned int val, unsigned int *reg) { *reg = val; writes++; }
static void pin_out(struct board *b, unsigned int pin, bool high)
{
	(void)b;
	assert((pin == 110 && !high) || (pin == 107 && !high) || (pin == 108 && high));
	pins++;
}
static int isp_off(struct board *b)
{
	domains++;
	if (!domain_fail) { b->safe = true; b->isp_accessible = false; b->domain_started = false; }
	return domain_fail;
}
static int mt6755_pmic_rmw(u16 reg, u16 mask, int shift, u16 val)
{
	unsigned int i = 5 - pmics++;
	assert(reg == pmic_regs[i] && mask == pmic_masks[i] && shift == 0 && val == i);
	return pmic_fail == (int)(i + 1) ? -55 : 0;
}
static void restore_pins(struct board *b) { (void)b; restores++; }
#include "extracted-cleanup.h"
int main(void)
{
	unsigned int scenario;

	for (scenario = 0; scenario < 11; scenario++) {
		unsigned int mmio[8192] = { 0 };
		struct platform p = { 0 };
		struct board b = { .pdev = &p, .isp_accessible = true, .safe = false,
			.snapshot = true, .domain_started = true, .sen = mmio, .larb = mmio, .img = mmio };
		struct camera c = { &b, true };
		int ret;

		for (unsigned int i = 0; i < 6; i++) b.pmic_saved[i] = i;
		stop_fail = scenario == 1 ? -11 : 0;
		halt_fail = scenario == 2 || scenario == 10 ? -22 : 0;
		domain_fail = scenario == 3 || scenario == 10 ? -33 : 0;
		pmic_fail = scenario >= 4 && scenario < 10 ? scenario - 3 : 0;
		stops = halts = domains = pmics = pins = restores = writes = 0;
		mmio[0x200] = mmio[0x600] = 0xa0000001;
		ret = power_down(&c);
		assert(mmio[0x200] == 0 && mmio[0x600] == 0);
		assert(stops == 1 && halts == 1 && domains == 1 && pmics == 6 && pins == 3 && restores == 1);
		assert(!c.sensor_started && !b.snapshot);
		assert(b.safe == (scenario != 10));
		assert(b.poisoned == (scenario != 0));
		assert((ret != 0) == (scenario != 0));
		assert(ret == (scenario == 1 ? -11 : scenario == 2 || scenario == 10 ? -22 :
			scenario == 3 ? -33 : scenario >= 4 && scenario < 10 ? -55 : 0));
		/* Do not restore larb / gates while DMA might still be active. */
		assert(writes == (halt_fail ? 2U : 4U));
	}
	puts("PASS extracted production cleanup: success, sensor-stop, DMA-reset, domain-off, each PMIC field, combined unsafe failure; all cleanup steps attempted; quarantine flag");
	return 0;
}
