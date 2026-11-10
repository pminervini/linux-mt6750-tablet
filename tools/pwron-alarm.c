/* pwron-alarm <seconds-from-now> | clear | get : MTK Android power-on alarm via /dev/alarm.
 * Powers the device on from a powered-off state at that time (minute granularity). */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <linux/rtc.h>
#define ALARM_IOW(c, type, size) _IOW('a', (c) | ((type) << 4), size)
#define ANDROID_ALARM_SET(type) ALARM_IOW(2, type, struct timespec)
#define ANDROID_ALARM_CLEAR(type) _IO('a', 0 | ((type) << 4))
#define ANDROID_ALARM_GET_POWER_ON _IOR('a', 7, struct rtc_wkalrm)
#define POWER_ON 6
int main(int argc, char **argv)
{
	int fd = open("/dev/alarm", O_RDWR);
	if (fd < 0) { perror("/dev/alarm"); return 1; }
	if (argc > 1 && !strcmp(argv[1], "clear"))
		return ioctl(fd, ANDROID_ALARM_CLEAR(POWER_ON)) ? perror("clear"), 1 : 0;
	if (argc > 1 && !strcmp(argv[1], "get")) {
		struct rtc_wkalrm w = {0};
		if (ioctl(fd, ANDROID_ALARM_GET_POWER_ON, &w)) { perror("get"); return 1; }
		printf("enabled=%d %04d-%02d-%02d %02d:%02d:%02d\n", w.enabled, w.time.tm_year + 1900,
		       w.time.tm_mon + 1, w.time.tm_mday, w.time.tm_hour, w.time.tm_min, w.time.tm_sec);
		return 0;
	}
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	ts.tv_sec += argc > 1 ? atol(argv[1]) : 600;
	ts.tv_nsec = 0;
	if (ioctl(fd, ANDROID_ALARM_SET(POWER_ON), &ts)) { perror("set"); return 1; }
	printf("power-on alarm at %ld (now+%s s)\n", (long)ts.tv_sec, argc > 1 ? argv[1] : "600");
	return 0;
}
