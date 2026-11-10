/* stphook: log every STP frame the stock MT6755 wmt_drv.ko sends to CONSYS (stock 3.18 kernel).
 * insmod stphook.ko lo=<low 32 bits> hi=<high 32 bits> of &stp_btif_if_tx (from /proc/kallsyms);
 * rmmod restores the pointer. Patch-download frames (WMT opcode 1, > 200 bytes) are summarised. */
#include <linux/module.h>
#include <linux/kernel.h>

typedef int (*if_tx_t)(const unsigned char *data, const unsigned int size, unsigned int *written);

static unsigned int lo, hi, exp;
module_param(exp, uint, 0);
module_param(lo, uint, 0);
module_param(hi, uint, 0);

static if_tx_t *slot;
static if_tx_t orig;
static unsigned int patch_frames;

static int hook_tx(const unsigned char *data, const unsigned int size, unsigned int *written)
{
	if (size > 200 && size >= 6 && data[4] == 0x01 && data[5] == 0x01 && data[0] != 0xaa) {
		patch_frames++;
	} else {
		if (patch_frames) {
			printk(KERN_INFO "STPTX ... %u patch frames\n", patch_frames);
			patch_frames = 0;
		}
		printk(KERN_INFO "STPTX %u: %*ph ... %*ph\n", size, size > 64 ? 64 : (int)size, data, size > 8 ? 8 : (int)size, data + (size > 8 ? size - 8 : 0));
	}
	return orig(data, size, written);
}

static int __init stphook_init(void)
{
	slot = (if_tx_t *)(((unsigned long)hi << 32) | lo);
	if (!lo || !hi)
		return -EINVAL;
	orig = *slot;
	if (!orig || (unsigned int)(unsigned long)orig != exp) {
		printk(KERN_INFO "stphook: slot holds %p, expected low32 %x\n", orig, exp);
		return -ENODEV;
	}
	*slot = hook_tx;
	printk(KERN_INFO "stphook: hooked %p (orig %pS)\n", slot, orig);
	return 0;
}

static void __exit stphook_exit(void)
{
	*slot = orig;
	printk(KERN_INFO "stphook: restored\n");
}
module_init(stphook_init);
module_exit(stphook_exit);
MODULE_LICENSE("GPL");
