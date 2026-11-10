#!/usr/bin/env python3
"""One MTKClient multi session; commands come from session-<name>.txt next to this file."""
import os, runpy, signal, sys
from pathlib import Path
os.umask(0o077)
BASE = Path(__file__).resolve().parent
CLIENT = Path(os.environ.get('MTKCLIENT', 'mtkclient'))  # path to an mtkclient checkout
os.chdir(BASE)
os.environ['HOME'] = str(BASE / 'tool-home')
sys.path.insert(0, str(CLIENT))
from mtkclient.config.usb_ids import default_ids
default_ids.clear()
default_ids[0x0E8D] = {0x0003: -1, 0x2000: -1, 0x2001: -1}
commands = [l.strip() for l in (BASE / f'session-{sys.argv[1]}.txt').read_text().splitlines() if l.strip()]
sys.argv = [str(CLIENT / 'mtk.py'), 'multi', ';'.join(commands)]
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('deadline')))
signal.alarm(900)
try:
    runpy.run_path(str(CLIENT / 'mtk.py'), run_name='__main__')
finally:
    signal.alarm(0)
    if os.geteuid() == 0:
        for p in BASE.rglob('*'):
            if not p.is_symlink(): os.chown(p, 1000, 1000)
