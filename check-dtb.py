"""check-dtb.py <dtb>: refuse images that lk would reject (=> BROM loop + power-off).
1) the stock ODM overlay (dtbo/ov0.dtbo) must apply (lk ASSERTs otherwise)
2) static /reserved-memory regions must not overlap lk's own mblock reservations"""
import subprocess, sys, tempfile
dtb = sys.argv[1]
with tempfile.NamedTemporaryFile(suffix='.dtb') as t:
    r = subprocess.run(['fdtoverlay', '-i', dtb, '-o', t.name, 'dtbo/ov0.dtbo'], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'FAIL overlay: {r.stderr.strip()}')
    syms = subprocess.run(['fdtget', '-l', dtb, '/'], capture_output=True, text=True).stdout.split()
    if '__symbols__' not in syms:
        sys.exit('FAIL no __symbols__')
LK = [(0x7ff80000, 0x80000, 'log_store'), (0x44600000, 0x40000, 'atf'), (0x7ff40000, 0x40000, 'atf-log'),
      (0x44000000, 0x80000, 'dtb'), (0x40080000, 0x3200000, 'kernel'), (0x45000000, 0x1000000, 'ramdisk'),
      (0x7f190000, 0xdb0000, 'framebuffer'), (0x98000000, 0x20000, 'ccci'), (0x8e000000, 0xa000000, 'ccci-md'),
      (0xbffff000, 0x1000, 'dram-dummy')]
nodes = subprocess.run(['fdtget', '-l', dtb, '/reserved-memory'], capture_output=True, text=True).stdout.split()
bad = 0
for n in nodes:
    r = subprocess.run(['fdtget', '-t', 'x', dtb, f'/reserved-memory/{n}', 'reg'], capture_output=True, text=True)
    if r.returncode:
        continue
    v = [int(x, 16) for x in r.stdout.split()]
    for i in range(0, len(v), 4):
        a, s = (v[i] << 32) | v[i + 1], (v[i + 2] << 32) | v[i + 3]
        for la, ls, ln in LK:
            if a < la + ls and la < a + s:
                print(f'FAIL {n} {a:#x}+{s:#x} overlaps lk {ln}'); bad = 1
if bad:
    sys.exit(1)
print('dtb ok for lk')
