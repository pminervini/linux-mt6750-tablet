// SPDX-License-Identifier: GPL-2.0
/* Dump the MT6755 EMI MPU (secure-only registers) through the ATF SiP call
 * MTK_SIP_KERNEL_EMIMPU_READ (0x82000208), as the stock kernel does.
 * Optional: set=1 start=.. end=.. perm=.. -> MTK_SIP_KERNEL_EMIMPU_SET (0x82000209). */
#include <linux/arm-smccc.h>
#include <linux/module.h>

static unsigned long start, end, perm;
static int set;
module_param(start, ulong, 0); module_param(end, ulong, 0);
module_param(perm, ulong, 0); module_param(set, int, 0);

static int __init emimpu_init(void)
{
	struct arm_smccc_res res;
	unsigned int o;

	if (set) {
		arm_smccc_smc(0x82000209, start, end, perm, 0, 0, 0, 0, &res);
		pr_info("EMIMPU set %lx-%lx perm %lx -> %lx\n", start, end, perm, res.a0);
	}
	for (o = 0x160; o <= 0x3bc; o += 4) {
		arm_smccc_smc(0x82000208, o, 0, 0, 0, 0, 0, 0, &res);
		if ((unsigned int)res.a0)
			pr_info("EMIMPU %03x %08x\n", o, (unsigned int)res.a0);
	}
	pr_info("EMIMPU done\n");
	return -EAGAIN;		/* never stays loaded */
}
module_init(emimpu_init);
MODULE_LICENSE("GPL");
