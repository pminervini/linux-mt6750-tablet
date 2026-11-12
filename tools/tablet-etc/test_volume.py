"""Offline tests: hardware and decoder are replaced, except real local pipes."""
import ast
import io
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent


def definitions(name):
    tree = ast.parse((ROOT.parent / 'audio' / name if name == 'tablet-audio' else ROOT / name).read_text())
    tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef))]
    scope = {'__name__': 'offline'}
    exec(compile(tree, name, 'exec'), scope)
    return scope


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'volume'
        self.audio = definitions('tablet-audio')
        self.audio['VOLUME_PATH'] = self.path

    def test_default_and_saved_value(self):
        self.assertEqual(self.audio['output_volume'](), 30)
        self.assertEqual(self.audio['output_volume']('up'), 35)
        self.assertEqual(self.path.read_text(), '35\n')
        self.assertEqual(self.audio['output_volume']('down'), 30)

    def test_limits_and_mute(self):
        for initial, direction, expected in ((0, 'down', 0), (100, 'up', 100), (95, 'up', 100), (5, 'down', 0)):
            self.audio['output_volume'](initial)
            self.assertEqual(self.audio['output_volume'](direction), expected)

    def test_corrupt_state_is_not_loud(self):
        for raw in ('bad', '', '-1', '101'):
            self.path.write_text(raw)
            self.assertEqual(self.audio['output_volume'](), 0)

    def test_invalid_values_do_not_replace_state(self):
        self.audio['output_volume'](25)
        for value in (-1, 101, 'wrong'):
            with self.assertRaises(ValueError):
                self.audio['output_volume'](value)
            self.assertEqual(self.path.read_text(), '25\n')

    def test_cli_never_opens_hardware(self):
        for arguments, expected in ((['volume'], '30%'), (['volume', 'up'], '35%'), (['volume', 'down'], '30%'), (['volume', '0'], '0%')):
            with patch.dict(os.environ, {'TABLET_VOLUME_PATH': str(self.path)}), patch.object(sys, 'argv', ['tablet-audio', *arguments]), patch('os.open', side_effect=AssertionError('hardware opened')), patch('sys.stdout', new_callable=io.StringIO) as output:
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(ROOT.parent / 'audio' / 'tablet-audio'), run_name='__main__')
                self.assertEqual(result.exception.code, 0)
                self.assertIn(expected, output.getvalue())

    def test_cli_invalid_values_fail_without_hardware(self):
        for arguments in (['volume', '101'], ['volume', 'nan'], ['volume', '1', 'extra']):
            result = subprocess.run([sys.executable, str(ROOT.parent / 'audio' / 'tablet-audio'), *arguments], env={**os.environ, 'TABLET_VOLUME_PATH': str(self.path)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('/dev/mem', result.stderr)

    def test_concurrent_updates_are_serialised(self):
        self.audio['output_volume'](0)
        jobs = [subprocess.Popen([sys.executable, str(ROOT.parent / 'audio' / 'tablet-audio'), 'volume', 'up'], env={**os.environ, 'TABLET_VOLUME_PATH': str(self.path)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(12)]
        for job in jobs:
            job.communicate(timeout=15)
            self.assertEqual(job.returncode, 0)
        self.assertEqual(self.audio['output_volume'](), 60)

    def test_running_player_reads_new_volume_and_zero_mutes_pcm(self):
        scope = self.audio
        scope.update(SRAM_SIZE=16, SRAM=0x1000, afe=None)
        self.path.write_text('100\n')
        writes, first = [], []
        levels = iter((50, 0))
        raw = struct.pack('<ii', 0x40000000, -0x40000000)
        class Decoder:
            stdout = io.BytesIO(raw * 3)
            def kill(self):
                pass
        def clock(_):
            try:
                scope['output_volume'](next(levels))
            except StopIteration:
                pass
        pointer = iter((0x1008, 0x1010, 0x1018, 0x1020))
        decoder = Decoder()
        scope.update(codec_on=lambda: None, codec_off=lambda: None, afe_start=lambda **kw: first.append(kw['buf']), afe_stop=lambda: None, amp=lambda _: None, rd=lambda *_: next(pointer), sram_write=lambda _, data: writes.append(data))
        with patch('subprocess.Popen', return_value=decoder), patch('time.sleep', side_effect=clock), patch('select.select', return_value=([decoder.stdout], [], [])):
            scope['play']('offline-input')
        self.assertEqual(struct.unpack('<II', first[0][:8]), (0x400000, 0xc00000))
        self.assertEqual(struct.unpack('<II', writes[0]), (0x200000, 0xe00000))
        self.assertTrue(writes[1:])
        self.assertTrue(all(not any(data) for data in writes[1:]))

    def test_stalled_decoder_refills_silence_without_waiting(self):
        scope = self.audio
        scope.update(SRAM_SIZE=16, SRAM=0x1000, afe=None)
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        source = os.fdopen(read_fd, 'rb', buffering=0)
        self.addCleanup(source.close)
        os.write(write_fd, struct.pack('<iiii', *([0x40000000] * 4)))
        decoder = MagicMock(stdout=source)
        writes, turns = [], []
        class EndProbe(Exception):
            pass
        def clock(_):
            turns.append(None)
            if len(turns) == 4:
                raise EndProbe()
            scope['output_volume'](0)
        pointer = iter((0x1008, 0x1010, 0x1018))
        scope.update(codec_on=lambda: None, codec_off=lambda: None, afe_start=lambda **_: None, afe_stop=lambda: None, amp=lambda _: None, rd=lambda *_: next(pointer), sram_write=lambda _, data: writes.append(data))
        with patch('subprocess.Popen', return_value=decoder), patch('time.sleep', side_effect=clock):
            with self.assertRaises(EndProbe):
                scope['play']('offline-pipe')
        self.assertEqual(writes, [bytes(8)] * 3)
        decoder.kill.assert_called_once()


class ButtonTests(unittest.TestCase):
    def setUp(self):
        cls = definitions('tablet-pwrkey')['SideButtons']
        self.buttons = cls.__new__(cls)
        self.buttons.keypad = bytearray(4096)
        self.buttons.mask, self.buttons.raw_mask, self.buttons.changed_at = 3, 3, 0
        self.buttons.next_at, self.buttons.down_at = None, 0
        self.buttons.armed = False
        struct.pack_into('<I', self.buttons.keypad, 0x24, 1)

    def poll(self, mask, now):
        struct.pack_into('<I', self.buttons.keypad, 4, 0xffff ^ mask)
        return self.buttons.poll(now)

    def arm(self):
        self.poll(0, 0)
        self.poll(0, .06)

    def test_press_release_and_debounce(self):
        self.arm()
        self.assertIsNone(self.poll(1, 1))
        self.assertIsNone(self.poll(1, 1.049))
        self.assertEqual(self.poll(1, 1.051), 'up')
        self.assertIsNone(self.poll(0, 1.1))
        self.poll(0, 1.16)
        self.assertIsNone(self.poll(2, 2))
        self.assertEqual(self.poll(2, 2.051), 'down')

    def test_bounce_does_not_adjust(self):
        self.arm()
        for mask, now in ((1, .1), (0, .12), (1, .14), (0, .16)):
            self.assertIsNone(self.poll(mask, now))

    def test_hold_repeat(self):
        self.arm()
        self.poll(1, 1)
        self.assertEqual(self.poll(1, 1.051), 'up')
        self.assertIsNone(self.poll(1, 1.49))
        self.assertEqual(self.poll(1, 1.51), 'up')
        self.assertIsNone(self.poll(1, 1.65))
        self.assertEqual(self.poll(1, 1.67), 'up')

    def test_held_at_startup_requires_release(self):
        self.assertIsNone(self.poll(1, 0))
        self.assertIsNone(self.poll(1, 2))
        self.poll(0, 3)
        self.poll(0, 3.06)
        self.poll(1, 4)
        self.assertEqual(self.poll(1, 4.06), 'up')

    def test_both_pressed_require_release(self):
        self.arm()
        for mask, now in ((3, 1), (3, 2), (1, 3), (1, 4)):
            self.assertIsNone(self.poll(mask, now))
        self.poll(0, 5)
        self.poll(0, 5.06)
        self.poll(2, 6)
        self.assertEqual(self.poll(2, 6.06), 'down')

    def test_disabled_keypad_does_not_adjust(self):
        self.arm()
        struct.pack_into('<I', self.buttons.keypad, 0x24, 0)
        self.assertIsNone(self.poll(1, 1))
        self.assertIsNone(self.poll(1, 2))

    def test_release_bounce_cannot_rearm_startup_hold(self):
        for mask, now in ((1, 0), (1, .1), (0, .2), (1, .22), (1, .3), (1, 1)):
            self.assertIsNone(self.poll(mask, now))

    def test_release_bounce_does_not_add_a_step(self):
        self.arm()
        self.poll(1, 1)
        self.assertEqual(self.poll(1, 1.06), 'up')
        for mask, now in ((0, 1.1), (1, 1.12), (1, 1.2)):
            self.assertIsNone(self.poll(mask, now))

    def test_hardware_map_is_read_only(self):
        cls = definitions('tablet-pwrkey')['SideButtons']
        with patch('os.open', return_value=99) as opening, patch('os.close') as closing, patch('mmap.mmap', return_value=bytearray(4096)) as mapping:
            cls()
        opening.assert_called_once_with('/dev/mem', os.O_RDONLY | os.O_SYNC)
        self.assertEqual(mapping.call_args.kwargs['prot'], __import__('mmap').PROT_READ)
        self.assertEqual(mapping.call_args.kwargs['offset'], 0x10010000)
        closing.assert_called_once_with(99)


class ControllerTests(unittest.TestCase):
    def test_button_commands_preserve_screen_and_idle_history(self):
        scope = runpy.run_path(str(ROOT / 'tablet-pwrkey'), run_name='offline')['main'].__globals__
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            scope['STATE'] = Path(directory) / 'ambient.json'
            levels = []
            scope['bl'] = lambda value=None: levels.append(value) if value is not None else 0
            class Buttons:
                keypad = io.BytesIO()
                def poll(self, _):
                    if not commands:
                        scope['STOP'] = True
                        return None
                    return commands.pop(0)
            scope['SideButtons'] = Buttons
            commands = ['up', 'down']
            job = MagicMock()
            job.poll.return_value = 0
            job.communicate.return_value = ('volume updated', '')
            with patch('glob.glob', return_value=[]), patch('signal.signal'), patch('select.select', return_value=([], [], [])), patch('subprocess.Popen', return_value=job) as run, patch('sys.stdout', new_callable=io.StringIO):
                scope['main']()
            self.assertEqual([call.args[0] for call in run.call_args_list], [['/usr/local/sbin/tablet-audio', 'volume', 'up'], ['/usr/local/sbin/tablet-audio', 'volume', 'down']])
            self.assertEqual(levels, [])
            self.assertTrue(Buttons.keypad.closed)
            state = __import__('json').loads(scope['STATE'].read_text())
            self.assertIsNone(state['active_pid'])
            self.assertIsNone(state['previous_level'])

    def test_missing_or_hung_helper_does_not_stop_controller(self):
        for mode in ('missing', 'hung'):
            scope = runpy.run_path(str(ROOT / 'tablet-pwrkey'), run_name='offline')['main'].__globals__
            with tempfile.TemporaryDirectory(dir=ROOT) as directory:
                scope['STATE'] = Path(directory) / 'ambient.json'
                scope['bl'] = lambda _=None: 0
                turns = []
                class Buttons:
                    keypad = io.BytesIO()
                    def poll(self, _):
                        turns.append(None)
                        if len(turns) >= 5:
                            scope['STOP'] = True
                        return 'up' if len(turns) == 1 else None
                scope['SideButtons'] = Buttons
                job = MagicMock()
                job.poll.return_value = None
                job.communicate.return_value = ('', '')
                with patch('glob.glob', return_value=[]), patch('signal.signal'), patch('select.select', return_value=([], [], [])), patch('time.monotonic', side_effect=iter(range(100))), patch('subprocess.Popen', side_effect=OSError('missing') if mode == 'missing' else None, return_value=job), patch('sys.stdout', new_callable=io.StringIO):
                    scope['main']()
                self.assertEqual(len(turns), 5)
                self.assertTrue(Buttons.keypad.closed)
                if mode == 'hung':
                    self.assertTrue(job.kill.called)


class OverlayTests(unittest.TestCase):
    def test_volume_survives_ambient_deadline_without_extending_eyes(self):
        scope = runpy.run_path(str(ROOT/'tablet-pwrkey'), run_name='offline')['main'].__globals__
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            scope.update(STATE=Path(directory)/'ambient.json', CHECK_MIN=.1, CHECK_MAX=.1, SHOW_MIN=1, SHOW_MAX=1)
            clock, brightness, renderers, updates = [0.0], [0], [], []
            def level(value=None):
                if value is not None:
                    brightness[0] = value
                return brightness[0]
            scope['bl'] = level
            scope['eligible'] = lambda *_: not renderers
            class Renderer:
                def __init__(self, args):
                    self.pid = 100+len(renderers)
                    self.osd = '--volume-osd' in args
                    self.start = clock[0]
                    self.end = None
                    renderers.append(self)
                def poll(self):
                    if not self.osd and clock[0] >= self.start+1:
                        self.end = clock[0]
                    return 0 if self.end is not None else None
                def terminate(self):
                    self.end = clock[0]
                def wait(self, **_):
                    return 0
            class Buttons:
                keypad = io.BytesIO()
                def poll(self, now):
                    if len(renderers) == 1 and not updates and now >= renderers[0].start+.8:
                        updates.append(now)
                        return 'up'
                    if clock[0] > 4:
                        scope['STOP'] = True
                    return None
            scope['SideButtons'] = Buttons
            gain = MagicMock(returncode=0)
            gain.poll.return_value = 0
            gain.communicate.return_value = ('Playback volume: 35%', '')
            real_read = Path.read_text
            def reading(path, *args, **kwargs):
                return 'tty1' if str(path) == '/sys/class/tty/tty0/active' else real_read(path, *args, **kwargs)
            def selecting(*_):
                clock[0] += .025
                return [], [], []
            def launch(args, **_):
                return gain if 'volume' in args else Renderer(args)
            with patch.object(Path, 'read_text', reading), patch('glob.glob', return_value=[]), patch('signal.signal'), patch('time.monotonic', side_effect=lambda: clock[0]), patch('select.select', side_effect=selecting), patch('subprocess.Popen', side_effect=launch), patch('sys.stdout', new_callable=io.StringIO):
                scope['main']()
            self.assertEqual([renderer.osd for renderer in renderers], [False, True])
            self.assertAlmostEqual(renderers[0].end-renderers[0].start, 1, delta=.05)
            self.assertGreaterEqual(renderers[1].end-updates[0], 2)
            self.assertEqual(brightness[0], 0)

    def setUp(self):
        self.sentinel = runpy.run_path(str(ROOT / 'tablet-sentinel'), run_name='offline')

    def test_bars_and_rotation_bounds(self):
        Image = self.sentinel['Image']
        for rotation in range(4):
            w, h = (1280,800) if rotation % 2 else (800,1280)
            for level in (0,5,50,100):
                image = Image.new('L', (w,h))
                self.sentinel['volume_overlay'](image,level)
                self.assertEqual(self.sentinel['physical'](image,rotation).size,(800,1280))
                width = min(w-72,640)
                for n in range(20):
                    x = int((w-width)//2+24+n*(width-48)/20)+2
                    self.assertEqual(image.getpixel((x,h-85)),255 if n*5 < level else 0)
                self.assertIsNone(image.crop((0,0,w,h-170)).getbbox())

    def test_volume_state_missing_invalid_and_fresh(self):
        scope = self.sentinel['volume_status'].__globals__
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            scope['VOLUME'] = Path(directory)/'level'
            self.assertIsNone(scope['volume_status']())
            for raw in ('bad','-1','101'):
                scope['VOLUME'].write_text(raw)
                self.assertIsNone(scope['volume_status']())
            scope['VOLUME'].write_text('0')
            self.assertEqual(scope['volume_status']()[0],0)

    def test_successful_buttons_show_and_restore_without_keyboard_activity(self):
        scope = runpy.run_path(str(ROOT/'tablet-pwrkey'),run_name='offline')['main'].__globals__
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            scope['STATE'] = Path(directory)/'ambient.json'
            real_read = Path.read_text
            def reading(path,*args,**kwargs):
                return 'tty1' if str(path) == '/sys/class/tty/tty0/active' else real_read(path,*args,**kwargs)
            levels = []
            scope['bl'] = lambda value=None: levels.append(value) if value is not None else 0
            turns=[]
            class Buttons:
                keypad=io.BytesIO()
                def poll(self,_):
                    turns.append(None)
                    if len(turns)>4:
                        scope['STOP']=True
                    return 'up' if len(turns) in (1,3) else None
            scope['SideButtons']=Buttons
            gain = MagicMock(returncode=0)
            gain.poll.return_value=0
            gain.communicate.return_value=('Playback volume: 5%','')
            renderer = MagicMock(pid=12345)
            renderer.poll.return_value=None
            renderer.wait.return_value=0
            def launch(args,**kwargs):
                return renderer if '--volume-osd' in args else gain
            with patch.object(Path,'read_text',reading), patch('glob.glob',return_value=[]), patch('signal.signal'), patch('time.monotonic',side_effect=(100+i/10 for i in range(100))), patch('select.select',return_value=([],[],[])), patch('subprocess.Popen',side_effect=launch) as run, patch('sys.stdout',new_callable=io.StringIO):
                scope['main']()
            self.assertEqual(levels,[300,0])
            self.assertEqual(sum('--volume-osd' in c.args[0] for c in run.call_args_list),1)
            renderer.terminate.assert_called_once()
            state=__import__('json').loads(scope['STATE'].read_text())
            self.assertIsNone(state['active_pid'])
            self.assertIsNone(state['previous_level'])
            self.assertEqual(state['last_keyboard'],100)


if __name__ == '__main__':
    unittest.main(verbosity=2)
