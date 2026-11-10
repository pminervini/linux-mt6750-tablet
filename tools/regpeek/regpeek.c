/*
 * regpeek: dump physical MMIO words to the kernel log, for the stock 3.18
 * Android kernel (no /dev/mem). insmod regpeek.ko base=0x10002000 len=0xc00
 * Prints "regpeek <addr> <val>" per word, then fails the load on purpose so
 * nothing stays resident.
 */
#include <linux/module.h>
#include <linux/io.h>

static unsigned int base, len = 0x100, samp;
module_param(base, uint, 0);
module_param(len, uint, 0);
module_param(samp, uint, 0);	/* sample the word at base samp times, print a histogram */

static unsigned int hv[128], hc[128];

static int __init regpeek_init(void)
{
	void __iomem *p;
	unsigned int o;

	if (!base || len > 0x10000)
		return -EINVAL;
	p = ioremap(base, len);
	if (!p)
		return -ENOMEM;
	if (samp) {
		unsigned int i, j, n = 0, v, other = 0;

		for (i = 0; i < samp; i++) {
			v = readl(p);
			for (j = 0; j < n && hv[j] != v; j++)
				;
			if (j == n) {
				if (n == 128) {
					other++;
					continue;
				}
				hv[n] = v;
				hc[n++] = 0;
			}
			hc[j]++;
		}
		for (j = 0; j < n; j++)
			if (hc[j] * 200 >= samp)
				printk(KERN_INFO "regsamp %08x %u\n", hv[j], hc[j]);
		printk(KERN_INFO "regsamp other %u distinct %u\n", other, n);
		iounmap(p);
		return -EAGAIN;
	}
	for (o = 0; o < len; o += 4)
		printk(KERN_INFO "regpeek %08x %08x\n", base + o, readl(p + o));
	iounmap(p);
	return -EAGAIN;
}
module_init(regpeek_init);
MODULE_LICENSE("GPL");
