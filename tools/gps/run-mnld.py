#!/usr/bin/python3
# exec stock mnld in /srv/android with its init control socket (ANDROID_SOCKET_mnld)
import os, socket
os.chroot("/srv/android"); os.chdir("/")
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try: os.unlink("/dev/socket/mnld")
except FileNotFoundError: pass
s.bind("/dev/socket/mnld"); s.listen(4); os.set_inheritable(s.fileno(), True)
os.execve("/vendor/bin/mnld", ["mnld"], {"ANDROID_SOCKET_mnld": str(s.fileno()),
          "PATH": "/system/bin:/vendor/bin", "ANDROID_ROOT": "/system", "ANDROID_DATA": "/data",
          "LD_LIBRARY_PATH": "/vendor/lib:/system/lib"})
