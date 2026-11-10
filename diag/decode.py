"""decode.py <tag>: print bring-up trace, HEAD marker, RAM console and lk log tail from diag/out."""
import struct, sys, subprocess
from pathlib import Path

O = Path(__file__).parent / 'out'
tag = sys.argv[1]
STAGES = {1: 'entry+vemc', 2: 'idmap', 3: 'init_kernel_el', 4: 'cpu_setup', 5: 'mmu on',
          6: 'early_map_kernel', 7: 'primary_switched', 8: 'start_kernel', 10: 'banner',
          11: 'before setup_arch', 20: 'setup_arch', 21: 'early_ioremap', 22: 'ramcon up'}


def rd(name):
    p = O / f'{tag}-{name}'
    if not p.exists():
        return None
    return p.read_bytes()


t = rd('trace.bin')
if t:
    v = struct.unpack('<Q', t[:8])[0]
    print(f'trace: last stage {v} ({STAGES.get(v, "?")})')
m = rd('marker-ramcon.bin')
if m:
    h = struct.unpack('<8Q', m[:64])
    print('HEAD' if m[:4] == b'HEAD' else 'no HEAD', [hex(x) for x in h[1:]] if m[:4] == b'HEAD' else '')
    r = m[0x10000 + 64:]
    mg, pos, size, wr = struct.unpack('<4I', r[:16])
    if mg == 0x4E4F4352 and size < 0x10000:
        data = r[16:16 + size]
        txt = (data[pos:] + data[:pos]) if wr else data[:pos]
        (O / f'{tag}-ramcon.txt').write_bytes(txt)
        print(f'--- ramcon ({len(txt)} bytes, last 3000) ---')
        print(txt[-3000:].decode('utf-8', 'replace'))
    else:
        print('no ramcon')
lk = rd('logstore.bin')
if lk:
    s = subprocess.run(['strings', '-n', '4'], input=lk, capture_output=True).stdout.decode(errors='replace')
    print('--- lk/pl log tail ---')
    print('\n'.join(s.splitlines()[-8:]))

ro = rd('ramoops.bin')
if ro:
    best = None
    for off in range(0, len(ro), 0x1000):
        if ro[off:off + 4] == b'DBGC':
            start, size = struct.unpack('<II', ro[off + 4:off + 12])
            if size and size < 0x100000 and (not best or size > best[2]):
                best = (off, start, size)
    if best:
        off, start, size = best
        zone = 0x80000 - 12
        buf = ro[off + 12:off + 12 + zone]
        txt = (buf[start:size] + buf[:start]) if size >= zone else buf[:size]
        (O / f'{tag}-console.txt').write_bytes(txt)
        lines = txt.decode('utf-8', 'replace').splitlines()
        print(f'--- ramoops console @{0xb0000000 + off:#x}: {len(lines)} lines, tail ---')
        print('\n'.join(lines[-40:]))
    else:
        print('no ramoops console')
