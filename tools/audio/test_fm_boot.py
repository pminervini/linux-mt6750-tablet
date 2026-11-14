#!/usr/bin/env python3
"""Offline loader fixtures only: no device access, ELF loading, RF, or PCM."""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).parent
LOADER = (ROOT / 'fm-boot-load.in').read_text()
MODULES = [[name, '/fixture/' + name + '.ko', options] for name, options in [
    ('soundcore', ''), ('snd', ''), ('snd_timer', ''), ('snd_pcm', ''),
    ('snd_soc_core', ''),
    ('snd_soc_mt6755_tablet', 'legacy_quiesced=1 allow_capture=1 allow_playback=0'),
    ('mtk_fm_drv', ''),
]]
STUB = r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
r=Path(os.environ['FIXTURE']);name=Path(sys.argv[0]).name;args=sys.argv[1:]
with (r/'calls').open('a') as f:f.write(json.dumps([name,*args])+'\n')
if name=='uname':
 print(os.environ.get('BAD_KERNEL','fixture-release') if args==['-r'] else os.environ.get('BAD_BANNER','fixture-banner'))
elif name=='systemctl':sys.exit(1 if os.environ.get('BAD_WIFI') else 0)
elif name=='python3':
 assert args[0:2]==['-B','-c'] and 'enabled=False' in args[2] and 'silent_preflight("idle")' in args[2]
 sys.exit(1 if os.environ.get('BAD_INTERLOCK') else 0)
elif name=='insmod':
 module=Path(args[0]).stem
 if os.environ.get('FAIL_INSERT')==module:sys.exit(1)
 p=r/'sys/module'/module;(p/'notes').mkdir(parents=True)
 data=(r/'release/notes'/module).read_bytes()
 (p/'notes/.note.gnu.build-id').write_bytes(b'FOREIGN' if os.environ.get('WRONG_INSERT')==module else data)
 if module=='snd_soc_mt6755_tablet':
  assert args[1:]==['legacy_quiesced=1','allow_capture=1','allow_playback=0']
  (p/'parameters').mkdir()
  for key,value in [('legacy_quiesced','Y'),('allow_capture','Y'),('allow_playback','Y' if os.environ.get('WRONG_POLICY') else 'N')]:
   (p/'parameters'/key).write_text(value+'\n')
else:raise RuntimeError(name)
'''


def case(name, *, preloaded=0, env_changes=None, corrupt=None, locked=False, expected_success=False, expected_inserts=None):
    with tempfile.TemporaryDirectory(prefix='offline-', dir=ROOT) as directory:
        r = Path(directory)
        release = r / 'release'
        (release / 'notes').mkdir(parents=True)
        (r / 'sys/module').mkdir(parents=True)
        (r / 'bin').mkdir()
        for module in ['mtk_stp_wmt_soc', *[row[0] for row in MODULES]]:
            (release / 'notes' / module).write_bytes(('SYNTHETIC note ' + module).encode())
        mappings = []
        for row in MODULES:
            module, original = row[:2]
            path = r / 'modules' / (module + '.ko')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(('SYNTHETIC module ' + module).encode())
            mappings.append((original, str(path)))
        module_list = ''.join(' '.join(row).rstrip() + '\n' for row in MODULES)
        for old, new in mappings:
            module_list = module_list.replace(old, new)
        (release / 'modules.list').write_text(module_list)
        config = b'SYNTHETIC running configuration\n'
        with gzip.open(r / 'config.gz', 'wb') as f:
            f.write(config)
        script = LOADER.replace('@QUALIFIED_DIR@', str(release)).replace('@KERNEL_RELEASE@', 'fixture-release').replace('@KERNEL_BANNER@', 'fixture-banner').replace('/sys/module', str(r / 'sys/module')).replace('/proc/config.gz', str(r / 'config.gz')).replace('@KERNEL_CONFIG_SHA256@', hashlib.sha256(config).hexdigest()).replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'PATH=' + str(r / 'bin') + ':/usr/sbin:/usr/bin:/sbin:/bin')
        assert '@' not in script
        (release / 'load').write_text(script)
        pinned = [release / 'load', release / 'modules.list', *list((release / 'notes').iterdir()), *list((r / 'modules').iterdir())]
        (release / 'files.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p) + '\n' for p in pinned))
        stub = r / 'stub'
        stub.write_text(STUB.replace('#!/usr/bin/env python3', '#!' + sys.executable, 1))
        stub.chmod(0o700)
        for command in ['uname', 'systemctl', 'insmod', 'python3']:
            (r / 'bin' / command).symlink_to(stub)
        for module in ['mtk_stp_wmt_soc', *[row[0] for row in MODULES[:preloaded]]]:
            p = r / 'sys/module' / module
            (p / 'notes').mkdir(parents=True)
            shutil.copyfile(release / 'notes' / module, p / 'notes/.note.gnu.build-id')
            if module == 'snd_soc_mt6755_tablet':
                (p / 'parameters').mkdir()
                for key, value in [('legacy_quiesced', 'Y'), ('allow_capture', 'Y'), ('allow_playback', 'N')]:
                    (p / 'parameters' / key).write_text(value + '\n')
        if corrupt == 'hash':
            (release / 'notes/snd').write_bytes(b'CHANGED')
        elif corrupt == 'config':
            with gzip.open(r / 'config.gz', 'wb') as f:
                f.write(b'CHANGED')
        elif corrupt == 'wmt':
            (r / 'sys/module/mtk_stp_wmt_soc/notes/.note.gnu.build-id').write_bytes(b'FOREIGN')
        elif corrupt == 'resident':
            p = r / 'sys/module/mtk_fm_drv/notes'
            p.mkdir(parents=True)
            (p / '.note.gnu.build-id').write_bytes(b'FOREIGN')
        elif corrupt == 'policy':
            (r / 'sys/module/snd_soc_mt6755_tablet/parameters/allow_playback').write_text('Y\n')
        env = os.environ.copy()
        env.update(FIXTURE=str(r), PYTHONDONTWRITEBYTECODE='1', **(env_changes or {}))
        with (r / 'lock').open('w') as lock:
            if locked:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            command = ['flock', '-n', str(r / 'lock'), '/bin/sh', str(release / 'load')]
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=8)
        calls = [json.loads(line) for line in (r / 'calls').read_text().splitlines()] if (r / 'calls').exists() else []
        inserts = [Path(call[1]).stem for call in calls if call[0] == 'insmod']
        assert (result.returncode == 0) == expected_success, (name, result.returncode, result.stderr)
        if expected_inserts is not None:
            assert inserts == expected_inserts, (name, inserts)
        assert all(call[0] in ['uname', 'systemctl', 'insmod', 'python3'] for call in calls)
        return {'name': name, 'passed': True, 'returncode': result.returncode, 'synthetic_inserts': inserts}


all_modules = [row[0] for row in MODULES]
results = [
    case('cold synthetic load', expected_success=True, expected_inserts=all_modules),
    case('already-loaded exact stack', preloaded=7, expected_success=True, expected_inserts=[]),
    case('partial exact stack', preloaded=3, expected_success=True, expected_inserts=all_modules[3:]),
    case('wrong release', env_changes={'BAD_KERNEL': 'foreign'}, expected_inserts=[]),
    case('wrong banner', env_changes={'BAD_BANNER': 'foreign'}, expected_inserts=[]),
    case('wrong running config', corrupt='config', expected_inserts=[]),
    case('modified pinned input', corrupt='hash', expected_inserts=[]),
    case('WiFi prerequisite inactive', env_changes={'BAD_WIFI': '1'}, expected_inserts=[]),
    case('foreign WMT', corrupt='wmt', expected_inserts=[]),
    case('foreign resident dependency before any insert', corrupt='resident', expected_inserts=[]),
    case('unsafe existing playback policy', preloaded=7, corrupt='policy', expected_inserts=[]),
    case('failed framework insert', env_changes={'FAIL_INSERT': 'snd_pcm'}, expected_inserts=all_modules[:4]),
    case('foreign inserted board note', env_changes={'WRONG_INSERT': 'snd_soc_mt6755_tablet'}, expected_inserts=all_modules[:6]),
    case('wrong inserted policy', env_changes={'WRONG_POLICY': '1'}, expected_inserts=all_modules),
    case('unsafe analogue/amp interlock', env_changes={'BAD_INTERLOCK': '1'}, expected_inserts=all_modules),
    case('busy common admission lock', locked=True, expected_inserts=[]),
]
assert 'ExecStop=' not in (ROOT / 'tablet-fm.service.in').read_text()
assert not any(token in LOADER for token in ['rmmod', 'modprobe -r', 'fmctl', 'arecord', 'aplay'])
print(json.dumps({'scope': 'OFFLINE synthetic module/host fixtures only; no device/RF/PCM', 'passed': True, 'cases': results}, indent=2))
