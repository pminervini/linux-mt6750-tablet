#!/usr/bin/python3
# Stand-in for MediaTek's GPS HAL: HAL2MNL v1 commands to @mtk_hal2mnl, listen on @mtk_mnl2hal.
# usage: fakehal.py <seconds>   (101 init, 102 start, ..., 103 stop)
import socket, struct, time, sys, collections
A = lambda n: ("\0" + n).ljust(108, "\0")   # abstract name incl. zero padding (bound with full sockaddr length)
rx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); rx.bind(A("mtk_mnl2hal")); rx.settimeout(1)
tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
def send(cmd, extra=b""):
    for _ in range(30):
        try: return tx.sendto(struct.pack("<ii", 1, cmd) + extra, A("mtk_hal2mnl"))
        except ConnectionRefusedError: time.sleep(1)
    print("mnld not listening", flush=True)
send(101); time.sleep(1); send(102)
NMEALOG = open(sys.argv[2], 'w') if len(sys.argv) > 2 else None  # optional: every NMEA line with unix time
t0 = time.time(); types = collections.Counter(); nmea = []; maxsv = {}; best = {}; fix = []
while time.time() - t0 < float(sys.argv[1]):
    try: m = rx.recv(16384)
    except socket.timeout: continue
    ver, typ = struct.unpack_from("<ii", m); types[typ] += 1
    if b"$G" in m:
        s = m[m.index(b"$G"):].split(b"\0")[0].decode(errors="replace").strip()
        nmea.append(s)
        if NMEALOG:
            NMEALOG.write(''.join('%.3f %s\n' % (time.time(), l) for l in s.split()))
            NMEALOG.flush()
        for line in s.split():
            f = line.split("*")[0].split(",")
            dt = time.time() - t0
            if f[0][3:] == "GSV" and len(f) > 3 and f[3].isdigit() and int(f[3]) > maxsv.get(f[0], 0):
                maxsv[f[0]] = int(f[3])
                print(f"{dt:6.1f}s {f[0][1:]} in view {f[3]}: {line[:100]}", flush=True)
            if f[0][3:] == "GSV" and len(f) > 7:
                snr = [float(x) for x in f[7::4] if x.replace(".", "", 1).isdigit()]
                if snr and max(snr) > best.get(f[0], 0):
                    best[f[0]] = max(snr)
                    print(f"{dt:6.1f}s {f[0][1:]} tracked, best SNR {max(snr)} dB-Hz: {line[:100]}", flush=True)
            if f[0] == "$GNGGA" and len(f) > 7 and f[6] not in ("", "0") and not fix:
                fix.append(dt)
                print(f"{dt:6.1f}s FIX {line}", flush=True)
    elif types[typ] <= 2: print("type", typ, "len", len(m), m[8:72].hex(), flush=True)
send(103)
print("message types:", dict(types))
print("max in view:", maxsv or 0, "best SNR:", best or "none (no satellite received)", "fix after:", f"{fix[0]:.1f}s" if fix else "none")
for s in nmea[-12:]: print(s)
