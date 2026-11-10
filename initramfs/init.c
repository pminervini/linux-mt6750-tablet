/* Stage-0 init for the MT6750 mainline bring-up.
 * Logs system state to /dev/kmsg (-> ramoops console -> visible from the
 * stock kernel's /sys/fs/pstore after a warm reset), shows a heartbeat on
 * the framebuffer console, then reboots so the stock kernel can read it.
 */
#include <dirent.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define RUN_SECONDS 60

static int kmsg = -1;

static void say(const char *fmt, ...)
{
	char buf[1024];
	int n = snprintf(buf, sizeof buf, "<5>STAGE0: ");
	va_list ap;
	va_start(ap, fmt);
	n += vsnprintf(buf + n, sizeof buf - n, fmt, ap);
	va_end(ap);
	if (n >= (int)sizeof buf)
		n = sizeof buf - 1;
	if (kmsg >= 0)
		write(kmsg, buf, n);
}

static void dump(const char *path, int maxlines)
{
	char line[512];
	FILE *f = fopen(path, "r");
	if (!f) {
		say("%s: (unreadable)\n", path);
		return;
	}
	say("---- %s\n", path);
	for (int i = 0; fgets(line, sizeof line, f) && i < maxlines; i++) {
		/* kmsg wants one record per write; strip trailing newline then add */
		line[strcspn(line, "\n")] = 0;
		say("  %s\n", line);
	}
	fclose(f);
}

static void dump_str(const char *path)
{
	char buf[512] = { 0 };
	int fd = open(path, O_RDONLY);
	if (fd < 0)
		return;
	int n = read(fd, buf, sizeof buf - 1);
	close(fd);
	for (int i = 0; i < n; i++)
		if (buf[i] == 0)
			buf[i] = ' ';
	say("%s = %s\n", path, buf);
}

static void list(const char *dir)
{
	DIR *d = opendir(dir);
	struct dirent *e;
	char acc[900];
	int len = 0;
	if (!d)
		return;
	say("---- ls %s\n", dir);
	acc[0] = 0;
	while ((e = readdir(d))) {
		if (e->d_name[0] == '.')
			continue;
		int l = strlen(e->d_name);
		if (len + l + 2 > 800) {
			say("  %s\n", acc);
			len = 0;
			acc[0] = 0;
		}
		len += snprintf(acc + len, sizeof acc - len, "%s ", e->d_name);
	}
	if (len)
		say("  %s\n", acc);
	closedir(d);
}

int main(void)
{
	mkdir("/proc", 0755);
	mkdir("/sys", 0755);
	mkdir("/dev", 0755);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
	mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL);
	mkdir("/pstore", 0755);
	mount("pstore", "/pstore", "pstore", 0, NULL);

	kmsg = open("/dev/kmsg", O_WRONLY);
	int tty = open("/dev/tty0", O_WRONLY);

	say("hello from userspace on mainline\n");
	dump("/proc/version", 5);
	dump("/proc/cmdline", 5);
	dump_str("/sys/firmware/devicetree/base/model");
	dump_str("/sys/firmware/devicetree/base/chosen/bootargs");
	dump("/proc/cpuinfo", 12);
	dump("/proc/meminfo", 6);
	dump("/proc/iomem", 80);
	dump("/proc/interrupts", 40);
	dump("/proc/consoles", 10);
	dump("/sys/kernel/debug/devices_deferred", 40);
	list("/sys/bus/platform/devices");
	list("/sys/bus/platform/drivers");
	list("/sys/class/graphics");
	list("/sys/class/watchdog");
	list("/pstore");
	dump("/sys/class/watchdog/watchdog0/state", 2);
	dump("/sys/class/watchdog/watchdog0/timeout", 2);

	for (int t = 0; t < RUN_SECONDS; t += 5) {
		struct timespec ts;
		clock_gettime(CLOCK_MONOTONIC, &ts);
		say("heartbeat t=%d uptime=%ld.%03ld\n", t, (long)ts.tv_sec, ts.tv_nsec / 1000000);
		if (tty >= 0) {
			char b[128];
			int n = snprintf(b, sizeof b, "\r\nMAINLINE ALIVE  t=%ds  rebooting at %ds\r\n", t, RUN_SECONDS);
			write(tty, b, n);
		}
		sleep(5);
	}

	say("rebooting (RB_AUTOBOOT) for pstore readback\n");
	sync();
	sleep(1);
	reboot(RB_AUTOBOOT);
	say("reboot() returned?!\n");
	for (;;)
		pause();
}
