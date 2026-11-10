/* memsum: per-4KiB summary of a physical range (non-zero words, xor, sum) for the stock 3.18
 * kernel. insmod memsum.ko base=0x9fe00000 len=0x100000 ; prints "memsum <addr> <nz> <xor> <sum>"
 * for each page with any non-zero word, then fails the load on purpose. */
#include <linux/module.h>
#include <linux/io.h>

static unsigned int base;
static unsigned int len = 0x100000;
module_param(base, uint, 0);
module_param(len, uint, 0);

static int __init memsum_init(void)
{
	void __iomem *p;
	unsigned int o, w, nz, x, s, v;

	if (!base || len > 0x400000)
		return -EINVAL;
	p = ioremap(base, len);
	if (!p)
		return -ENOMEM;
	for (o = 0; o < len; o += 0x1000) {
		nz = x = s = 0;
		for (w = 0; w < 0x1000; w += 4) {
			v = readl(p + o + w);
			if (v) { nz++; x ^= v; s += v; }
		}
		if (nz)
			printk(KERN_INFO "memsum %08x %u %08x %08x\n", base + o, nz, x, s);
	}
	iounmap(p);
	return -EAGAIN;
}
module_init(memsum_init);
MODULE_LICENSE("GPL");
