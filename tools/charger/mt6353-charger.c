/*
 * mt6353-charger: keep the MT6353 PMIC pulse charger running on mainline (no
 * driver yet). Mirrors stock charging_hw_pmic.c (MT6353 branch): hw_init,
 * charging_enable, and a 4 s charger-watchdog kick every 2 s. Charge voltage is
 * left at the PMIC's reset default (= 4.2 V for this chip's trim table); the
 * current is set to 450 mA (USB). Hardware VBAT over-voltage cut (4.3 V) and
 * BATON (battery present/NTC) stay enabled, as in stock.
 *   mt6353-charger status   - dump charger registers once
 *   mt6353-charger run      - daemon loop (log to stdout)
 *   mt6353-charger peek ADR / poke ADR VAL - raw PMIC register access (hex)
 *   mt6353-charger battery  - VBAT and charge current from the PMIC AUXADC
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define PWRAP_BASE	0x1000D000
#define WACS2_CMD	0xA0
#define WACS2_RDATA	0xA4
#define WACS2_VLDCLR	0xA8
#define FSM(x)		(((x) >> 16) & 7)
#define FSM_IDLE	0
#define FSM_WFVLDCLR	6

#define CHR_CON0	0x0F8C	/* b0 VCDT_HV_EN, b1 RGS_CHR_LDO_DET, b3 CSDAC_EN, b4 CHR_EN, b5 RGS_CHRDET, b6 VCDT_LV_DET, b7 VCDT_HV_DET */
#define CHR_CON2	0x0F90	/* b1 VBAT_CV_EN, b3 CS_EN, b5 RGS_CS_DET, b6 RGS_VBAT_CV_DET, b7 RGS_VBAT_CC_DET */
#define CHR_CON3	0x0F92	/* [4:0] VBAT_CV_VTH */
#define CHR_CON4	0x0F94	/* [3:0] CS_VTH */
#define CHR_CON6	0x0F98	/* b0 VBAT_OV_EN, [4:2] VBAT_OV_VTH */
#define CHR_CON7	0x0F9A	/* b0 BATON_EN */
#define CHR_CON10	0x0FA0	/* b4 PCHR_FLAG_EN */
#define CHR_CON13	0x0FA6	/* [3:0] CHRWDT_TD, b4 CHRWDT_EN, b8 CHRWDT_WR */
#define CHR_CON15	0x0FAA	/* b0 CHRWDT_INT_EN, b1 CHRWDT_FLAG_WR */
#define CHR_CON16	0x0FAC	/* b2 USBDL_RST, b3 USBDL_SET */
#define CHR_CON20	0x0FB4	/* b0 BC11_BB_CTRL, b1 BC11_RST */
#define CHR_CON22	0x0FB8	/* [2:0] CSDAC_STP_INC, [6:4] CSDAC_STP_DEC */
#define CHR_CON23	0x0FBA	/* [2:0] CSDAC_DLY, [6:4] CSDAC_STP */
#define CHR_CON24	0x0FBC	/* [5:0] LOW_ICH_DB */
#define CHR_CON25	0x0FBE	/* b1 VCDT_MODE, b2 CSDAC_MODE, b6 HWCV_EN, b7 ULC_DET_EN */
#define CS_VTH_450MA	12	/* CS_VTH table: 2000,1600,1500,1350,1200,1100,1000,900,800,700,650,550,450,... */

static volatile uint32_t *pw;
static volatile int stop;

static int pw_wait(int want)
{
	for (int i = 0; i < 100000; i++)
		if (FSM(pw[WACS2_RDATA / 4]) == want)
			return 0;
	return -1;
}

static int pmic_read(uint16_t adr, uint16_t *val)
{
	if (pw_wait(FSM_IDLE))
		return -1;
	pw[WACS2_CMD / 4] = (uint32_t)(adr >> 1) << 16;
	if (pw_wait(FSM_WFVLDCLR))
		return -1;
	*val = pw[WACS2_RDATA / 4] & 0xffff;
	pw[WACS2_VLDCLR / 4] = 1;
	return 0;
}

static int pmic_write(uint16_t adr, uint16_t val)
{
	if (pw_wait(FSM_IDLE))
		return -1;
	pw[WACS2_CMD / 4] = (1u << 31) | ((uint32_t)(adr >> 1) << 16) | val;
	return pw_wait(FSM_IDLE);
}

static int field(uint16_t adr, uint16_t mask, int shift, uint16_t v)
{
	uint16_t old;

	if (pmic_read(adr, &old))
		return -1;
	return pmic_write(adr, (old & ~(mask << shift)) | ((v & mask) << shift));
}

static void hw_init(void)
{
	field(CHR_CON13, 0xF, 0, 0);	/* CHRWDT_TD = 4 s */
	field(CHR_CON15, 1, 0, 1);	/* CHRWDT_INT_EN */
	field(CHR_CON13, 1, 4, 1);	/* CHRWDT_EN */
	field(CHR_CON13, 1, 8, 1);	/* CHRWDT_WR */
	field(CHR_CON25, 1, 1, 0);	/* VCDT_MODE */
	field(CHR_CON0, 1, 0, 1);	/* VCDT_HV_EN */
	field(CHR_CON16, 1, 3, 0);	/* USBDL_SET = 0 (leave USB download mode) */
	field(CHR_CON16, 1, 2, 1);	/* USBDL_RST */
	field(CHR_CON20, 1, 0, 1);	/* BC11_BB_CTRL */
	field(CHR_CON20, 1, 1, 1);	/* BC11_RST */
	field(CHR_CON25, 1, 2, 1);	/* CSDAC_MODE */
	field(CHR_CON6, 1, 0, 1);	/* VBAT_OV_EN */
	field(CHR_CON6, 7, 2, 2);	/* VBAT_OV_VTH = 4.3 V */
	field(CHR_CON7, 1, 0, 1);	/* BATON_EN */
	field(CHR_CON25, 1, 7, 1);	/* ULC_DET_EN */
	field(CHR_CON24, 0x3F, 0, 1);	/* LOW_ICH_DB */
}

static void enable(void)
{
	field(CHR_CON4, 0xF, 0, CS_VTH_450MA);
	field(CHR_CON23, 7, 0, 4);	/* CSDAC_DLY */
	field(CHR_CON23, 7, 4, 1);	/* CSDAC_STP */
	field(CHR_CON22, 7, 0, 1);	/* CSDAC_STP_INC */
	field(CHR_CON22, 7, 4, 2);	/* CSDAC_STP_DEC */
	field(CHR_CON2, 1, 3, 1);	/* CS_EN */
	field(CHR_CON25, 1, 6, 1);	/* HWCV_EN */
	field(CHR_CON2, 1, 1, 1);	/* VBAT_CV_EN */
	field(CHR_CON0, 1, 3, 1);	/* CSDAC_EN */
	field(CHR_CON10, 1, 4, 1);	/* PCHR_FLAG_EN */
	field(CHR_CON0, 1, 4, 1);	/* CHR_EN */
	field(CHR_CON25, 1, 2, 1);	/* CSDAC_MODE */
	field(CHR_CON0, 1, 3, 1);	/* CSDAC_EN */
}

static void kick(void)
{
	field(CHR_CON13, 0xF, 0, 0);	/* CHRWDT_TD = 4 s */
	field(CHR_CON13, 1, 8, 1);	/* CHRWDT_WR */
	field(CHR_CON15, 1, 0, 1);	/* CHRWDT_INT_EN */
	field(CHR_CON13, 1, 4, 1);	/* CHRWDT_EN */
	field(CHR_CON15, 1, 1, 1);	/* CHRWDT_FLAG_WR */
}

static void dump(const char *tag)
{
	static const uint16_t regs[] = { CHR_CON0, CHR_CON2, CHR_CON3, CHR_CON4, CHR_CON6,
					 CHR_CON7, CHR_CON13, CHR_CON15, CHR_CON25 };
	uint16_t v, c0 = 0, c2 = 0;

	pmic_read(CHR_CON0, &c0);
	pmic_read(CHR_CON2, &c2);
	printf("%s chrdet=%d chr_en=%d cc=%d cv=%d |", tag, !!(c0 & 0x20), !!(c0 & 0x10),
	       !!(c2 & 0x80), !!(c2 & 0x40));
	for (unsigned i = 0; i < sizeof(regs) / sizeof(regs[0]); i++)
		if (!pmic_read(regs[i], &v))
			printf(" %04x=%04x", regs[i], v);
	printf("\n");
	fflush(stdout);
}

/* AUXADC ch0 = BATSNS, ch1 = ISENSE (15-bit, x3 divider, 1.8 V full range);
 * charge current through the 56 mOhm sense resistor (stock cust_r_sense). */
#define CLK_CKPDN_CON0_CLR	0x0272
#define AUXADC_CON0		0x0EA4
#define AUXADC_RQST0_SET	0x0E9A
#define AUXADC_ADC23		0x0E2E	/* ch0 by AP: b15 rdy, [14:0] */
#define AUXADC_ADC25		0x0E32	/* ch1 by AP */
#define R_SENSE_MOHM		56

static int auxadc_read(uint16_t reg, int *mv)
{
	uint16_t v = 0;
	for (int i = 0; i < 1000; i++) {
		if (pmic_read(reg, &v))
			return -1;
		if (v & 0x8000) {
			*mv = (int)(v & 0x7fff) * 3 * 1800 / 32768;
			return 0;
		}
		usleep(100);
	}
	return -1;
}

static int battery(int *vbat, int *ibat)
{
	int isense;

	pmic_write(CLK_CKPDN_CON0_CLR, 1 << 11);	/* AUXADC clock on */
	field(AUXADC_CON0, 1, 15, 1);			/* AUXADC_CK_AON */
	pmic_write(AUXADC_RQST0_SET, 0x3);		/* request ch0 + ch1 */
	usleep(1000);
	if (auxadc_read(AUXADC_ADC23, vbat) || auxadc_read(AUXADC_ADC25, &isense))
		return -1;
	*ibat = (isense - *vbat) * 1000 / R_SENSE_MOHM;
	return 0;
}

static void on_sig(int s) { (void)s; stop = 1; }

int main(int argc, char **argv)
{
	int fd = open("/dev/mem", O_RDWR | O_SYNC);
	int run = argc > 1 && !strcmp(argv[1], "run"), was_present = 0, n = 0;
	uint16_t c0;

	if (fd < 0) { perror("/dev/mem"); return 1; }
	pw = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, PWRAP_BASE);
	if (pw == MAP_FAILED) { perror("mmap pwrap"); return 1; }
	if (argc > 1 && !strcmp(argv[1], "battery")) {
		int vb, ib;
		if (battery(&vb, &ib)) { printf("auxadc timeout\n"); return 1; }
		printf("vbat=%d mV ichg=%d mA\n", vb, ib);
		return 0;
	}
	if (argc > 2 && !strcmp(argv[1], "peek")) {
		uint16_t v;
		if (pmic_read(strtoul(argv[2], NULL, 16), &v)) return 1;
		printf("0x%04lx = 0x%04x\n", strtoul(argv[2], NULL, 16), v);
		return 0;
	}
	if (argc > 3 && !strcmp(argv[1], "poke")) {
		uint16_t v, a = strtoul(argv[2], NULL, 16);
		if (pmic_write(a, strtoul(argv[3], NULL, 16)) || pmic_read(a, &v)) return 1;
		printf("0x%04x <- 0x%04lx, reads 0x%04x\n", a, strtoul(argv[3], NULL, 16), v);
		return 0;
	}
	dump("start");
	if (!run)
		return 0;
	signal(SIGTERM, on_sig);
	signal(SIGINT, on_sig);
	hw_init();
	while (!stop) {
		if (pmic_read(CHR_CON0, &c0)) {
			printf("pwrap read failed\n");
			sleep(2);
			continue;
		}
		if (c0 & 0x20) {		/* charger present */
			if (!was_present) {
				kick();		/* an expired charger WDT blocks CHR_EN; clear it first */
				enable();
				dump("charger plugged, enabled");
			}
			kick();
			if (!(c0 & 0x10)) {	/* CHR_EN dropped: re-enable */
				enable();
				dump("re-enabled");
			}
		} else if (was_present) {
			dump("charger removed");
		}
		was_present = !!(c0 & 0x20);
		if (++n % 150 == 0) {		/* every ~5 min */
			int vb, ib;
			if (!battery(&vb, &ib))
				printf("battery vbat=%d mV ichg=%d mA\n", vb, ib);
			dump("status");
		}
		sleep(2);
	}
	dump("exit");
	return 0;
}
