/* regpoke: write up to 4 physical MMIO words (stock 3.18 kernel), print before/after.
 * insmod regpoke.ko a0=.. v0=.. [a1=.. v1=.. ...] ; fails the load on purpose. */
#include <linux/module.h>
#include <linux/io.h>

static unsigned int a0, v0, a1, v1, a2, v2, a3, v3;
module_param(a0, uint, 0); module_param(v0, uint, 0);
module_param(a1, uint, 0); module_param(v1, uint, 0);
module_param(a2, uint, 0); module_param(v2, uint, 0);
module_param(a3, uint, 0); module_param(v3, uint, 0);

static void poke(unsigned int a, unsigned int v)
{
	void __iomem *p;

	if (!a)
		return;
	p = ioremap(a & ~0xfffU, 0x1000);
	if (!p)
		return;
	printk(KERN_INFO "regpoke %08x: %08x -> %08x", a, readl(p + (a & 0xfff)), v);
	writel(v, p + (a & 0xfff));
	printk(KERN_CONT " now %08x\n", readl(p + (a & 0xfff)));
	iounmap(p);
}

static int __init regpoke_init(void)
{
	poke(a0, v0); poke(a1, v1); poke(a2, v2); poke(a3, v3);
	return -EAGAIN;
}
module_init(regpoke_init);
MODULE_LICENSE("GPL");
