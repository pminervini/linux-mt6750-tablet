// SPDX-License-Identifier: GPL-2.0
/*
 * physbuf: hold a physically contiguous, uncached DMA buffer from CMA for register-level
 * capture experiments. insmod physbuf.ko size_mb=8 ; the physical address is in
 * /sys/module/physbuf/parameters/phys (read it, then use /dev/mem). rmmod frees it.
 */
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/dma-mapping.h>

static unsigned int size_mb = 8;
module_param(size_mb, uint, 0444);
static unsigned long phys;
module_param(phys, ulong, 0444);

static struct platform_device *pdev;
static void *cpu;
static dma_addr_t dma;

static int __init physbuf_init(void)
{
	int ret;

	pdev = platform_device_register_simple("physbuf", -1, NULL, 0);
	if (IS_ERR(pdev))
		return PTR_ERR(pdev);
	ret = dma_coerce_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (!ret) {
		cpu = dma_alloc_coherent(&pdev->dev, (size_t)size_mb << 20, &dma, GFP_KERNEL);
		ret = cpu ? 0 : -ENOMEM;
	}
	if (ret) {
		platform_device_unregister(pdev);
		return ret;
	}
	phys = dma;
	pr_info("physbuf: %u MB at %pad\n", size_mb, &dma);
	return 0;
}

static void __exit physbuf_exit(void)
{
	dma_free_coherent(&pdev->dev, (size_t)size_mb << 20, cpu, dma);
	platform_device_unregister(pdev);
}

module_init(physbuf_init);
module_exit(physbuf_exit);
MODULE_LICENSE("GPL");
