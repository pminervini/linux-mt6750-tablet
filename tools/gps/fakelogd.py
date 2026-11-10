#!/usr/bin/python3
# minimal logd for the Android chroot: receive liblog datagrams on /dev/socket/logdw, print text logs
import socket, os, struct, sys, time
path = sys.argv[1] if len(sys.argv) > 1 else "/srv/android/dev/socket/logdw"
try: os.unlink(path)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); s.bind(path); os.chmod(path, 0o666)
P = "VDIWEF"
out = open(sys.argv[2] if len(sys.argv) > 2 else "/tmp/mnld-log.txt", "a", buffering=1)
while True:
    d = s.recv(8192)
    if len(d) < 12: continue
    lid = d[0]; body = d[11:]
    if lid == 2 or lid == 3:   # events/security: binary
        continue
    prio = body[0]; rest = body[1:]
    tag, _, msg = rest.partition(b"\0")
    msg = msg.rstrip(b"\0")
    out.write("%.3f %s/%s: %s\n" % (time.time(), P[prio-2] if 2 <= prio <= 7 else prio, tag.decode(errors="replace"), msg.decode(errors="replace")))
