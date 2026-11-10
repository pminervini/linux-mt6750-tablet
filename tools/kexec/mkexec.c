/* mkexec <Image> <initrd> : kexec_file_load + reboot(KEXEC). Static, nolibc. */
#define KEXEC_FILE_ON_CRASH 2
int main(int argc, char **argv)
{
	static const char cmdline[] = "console=tty0";
	int kfd, ifd;
	long r;

	if (argc < 3) { write(2, "usage: mkexec Image initrd [go]\n", 32); return 2; }
	kfd = open(argv[1], O_RDONLY, 0);
	ifd = open(argv[2], O_RDONLY, 0);
	if (kfd < 0 || ifd < 0) { write(2, "open failed\n", 12); return 1; }
	r = __nolibc_syscall5(__NR_kexec_file_load, kfd, ifd, sizeof(cmdline), cmdline, 0);
	if (r < 0) { printf("kexec_file_load failed: %ld\n", r); return 1; }
	printf("kexec_file_load ok\n");
	if (argc > 3) {
		__nolibc_syscall0(__NR_sync);
		r = __nolibc_syscall4(__NR_reboot, 0xfee1dead, 672274793, 0x45584543 /* KEXEC */, 0);
		printf("reboot(KEXEC) returned %ld\n", r);
	}
	return 0;
}
