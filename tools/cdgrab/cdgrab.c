/* cdgrab: collect the CONSYS paged core dump on the stock 3.18 kernel (its own driver stops after
 * page 1, "coredump is disabled"). Load right after triggering a firmware assert; pages are printed
 * as "CDG <offset> <hex>" lines (64 bytes each), then the load fails on purpose. */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/io.h>
#include <linux/delay.h>

static unsigned int secs = 30;
module_param(secs, uint, 0);

#define C 0x80000
#define HS 0x4
#define HN 0x8
#define CS 0xc
#define CA 0x14
#define CL 0x18
#define PEND 0x2c

static int __init cdgrab_init(void)
{
	void __iomem *m = ioremap(0x9fe00000, 0x100000);
	unsigned int loops = 0, pages = 0, total = 0, a, n, o, i, t;
	unsigned char line[64];

	if (!m)
		return -ENOMEM;
	while (loops++ < secs * 100) {
		if (readl(m + C + CS) != 2) {
			if (pages && readl(m + C + PEND))
				break;
			msleep(10);
			continue;
		}
		writel(1, m + C + HS);
		a = readl(m + C + CA);
		n = readl(m + C + CL);
		o = a - 0xf0000000;
		if (o >= 0x100000 || !n || n > 0x10000 || o + n > 0x100000) {
			printk(KERN_INFO "CDG bad page %x %u\n", a, n);
			break;
		}
		for (i = 0; i < n; i += 64) {
			memcpy_fromio(line, m + o + i, 64);
			printk(KERN_INFO "CDG %06x %*ph\n", total + i, (int)(n - i > 64 ? 64 : n - i), line);
		}
		total += n;
		pages++;
		writel(readl(m + C + HN) + 1, m + C + HN);
		writel(2, m + C + HS);
		for (t = 0; t < 500 && readl(m + C + CS) != 3; t++)
			msleep(2);
		writel(3, m + C + HS);
		for (t = 0; t < 500 && readl(m + C + CS) == 3; t++)
			msleep(2);
		writel(0, m + C + HS);
	}
	printk(KERN_INFO "CDG done pages %u bytes %u end %u\n", pages, total, readl(m + C + PEND));
	iounmap(m);
	return -EAGAIN;
}
module_init(cdgrab_init);
MODULE_LICENSE("GPL");
