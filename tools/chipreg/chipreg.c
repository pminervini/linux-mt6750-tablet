/* chipreg: read CONSYS chip-internal registers through the stock wmt_drv's wmt_lib_reg_rw()
 * (stock 3.18 kernel). insmod chipreg.ko lo=.. hi=.. base=0x80070000 count=64 [step=4]
 * Prints "CHIPREG <addr> <val>", then fails the load on purpose. */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>

typedef int (*reg_rw_t)(unsigned int isWrite, unsigned int offset, unsigned int *pvalue, unsigned int mask);
static unsigned int lo, hi, base, count = 16, step = 4, mbox, mbcount;
/* A-die (RF chip) dump: stock wmt_core_tx/wmt_core_rx called directly (disable PSM first) */
static unsigned int tlo, thi, rlo, rhi, adie, adcount, dlo, dhi, elo, ehi, adrep, adie2;
module_param(adrep, uint, 0);	/* poll adie (and adie2) adcount times, print changes */
module_param(adie2, uint, 0);
module_param(dlo, uint, 0);
module_param(dhi, uint, 0);
module_param(elo, uint, 0);
module_param(ehi, uint, 0);
module_param(tlo, uint, 0);
module_param(thi, uint, 0);
module_param(rlo, uint, 0);
module_param(rhi, uint, 0);
module_param(adie, uint, 0);
module_param(adcount, uint, 0);
typedef int (*core_tx_t)(const unsigned char *d, unsigned int size, unsigned int *written, int raw);
typedef int (*core_rx_t)(unsigned char *buf, unsigned int len, unsigned int *read);
module_param(lo, uint, 0);
module_param(hi, uint, 0);
module_param(base, uint, 0);
module_param(count, uint, 0);
module_param(step, uint, 0);
module_param(mbox, uint, 0);	/* read GPS core memory through the 0x80070000 mailbox */
module_param(mbcount, uint, 0);

static int __init chipreg_init(void)
{
	reg_rw_t f = (reg_rw_t)(((unsigned long)hi << 32) | lo);
	unsigned int i, v;
	int r;

	if (adcount && thi == 0xffffffbf && rhi == 0xffffffbf && tlo && rlo) {
		core_tx_t tx = (core_tx_t)(((unsigned long)thi << 32) | tlo);
		core_rx_t rx = (core_rx_t)(((unsigned long)rhi << 32) | rlo);

		int (*psm_off)(void) = (void *)(((unsigned long)dhi << 32) | dlo);
		void (*psm_on)(void) = (void *)(((unsigned long)ehi << 32) | elo);

		if (dhi != 0xffffffbf || ehi != 0xffffffbf || !dlo || !elo)
			return -EINVAL;
		r = psm_off();	/* stock DISABLE_PSM_MONITOR: psm lock + chip awake, PSM off */
		printk(KERN_INFO "ADIE psm_off %d\n", r);
		if (r)
			return -EIO;
		for (i = 0; i < adcount && i < (adrep ? 100000 : 1024); i++) {
			unsigned int a = adrep ? ((i & 1) && adie2 ? adie2 : adie) : adie + i * 4, n = 0, t;
			static unsigned int prev[2] = { 0xdeadbeef, 0xdeadbeef };
			unsigned char cmd[8] = { 1, 0x13, 4, 0, 2, 4, a & 0xff, (a >> 8) & 0xff };
			unsigned char evt[16] = { 0 };

			r = tx(cmd, 8, &n, 0);
			if (r || n != 8) {
				printk(KERN_INFO "ADIE tx fail %d %u\n", r, n);
				break;
			}
			for (t = 0; t < 4; t++) {
				r = rx(evt, 13, &n);
				if (!r && n == 13 && evt[1] == 0x13)
					break;
				printk(KERN_INFO "ADIE skip %d %u %*ph\n", r, n, 13, evt);
			}
			if (r || n != 13) {
				printk(KERN_INFO "ADIE rx fail %d %u %*ph\n", r, n, 13, evt);
				break;
			}
			if (adrep) {
				unsigned int val = evt[9] | evt[10] << 8 | evt[11] << 16 | evt[12] << 24;

				if (val != prev[i & 1] || i < 2)
					printk(KERN_INFO "ADIEREG %04x 0x%08x st %02x\n", a, val, evt[4]);
				prev[i & 1] = val;
				continue;
			}
			printk(KERN_INFO "ADIEREG %04x 0x%02x%02x%02x%02x st %02x\n", a,
			       evt[12], evt[11], evt[10], evt[9], evt[4]);
		}
		psm_on();
		return -EAGAIN;
	}
	if (!lo || !hi || count > 4096)
		return -EINVAL;
	for (i = 0; i < mbcount; i++) {
		unsigned int a = mbox + i, st = 0, d = 0, t;

		v = a;
		f(1, 0x80070030, &v, 0xffffffff);
		v = 4;
		f(1, 0x80070020, &v, 0xffffffff);
		for (t = 0; t < 20; t++) {
			f(0, 0x80070020, &st, 0xffffffff);
			if (!(st & 4))
				break;
		}
		f(0, 0x80070050, &d, 0xffffffff);
		printk(KERN_INFO "GPSMEM 0x%04x = 0x%04x st 0x%x\n", a, d & 0xffff, st);
	}
	if (!base)
		return -EAGAIN;
	for (i = 0; i < count; i++) {
		v = 0;
		r = f(0, base + i * step, &v, 0xffffffff);
		printk(KERN_INFO "CHIPREG 0x%08x = 0x%08x%s\n", base + i * step, v, r ? " FAIL" : "");
	}
	return -EAGAIN;
}
module_init(chipreg_init);
MODULE_LICENSE("GPL");
