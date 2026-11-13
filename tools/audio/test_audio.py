#!/usr/bin/env python3
"""Network/device-free candidate helper tests; baseline button tests run separately."""
import ast
import fcntl
import threading
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent


def definitions():
    tree = ast.parse((ROOT / 'tablet-audio').read_text())
    tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    scope: dict = {'__name__': 'offline'}
    exec(compile(tree, 'tablet-audio', 'exec'), scope)
    return scope


class HelperTests(unittest.TestCase):
    fm_metadata = {
        '/sys/module/snd_soc_mt6755_tablet/parameters/allow_playback': 'N\n',
        '/sys/module/snd_soc_mt6755_tablet/parameters/allow_capture': 'Y\n',
        '/sys/module/snd_soc_mt6755_tablet/parameters/legacy_quiesced': 'Y\n',
        '/proc/asound/MT6755/id': 'MT6755\n',
        '/proc/asound/MT6755/pcm1c/info': 'stream: CAPTURE\n',
    }

    def test_fm_backend_metadata_and_missing_unsafe_states(self):
        for changes in ({}, {'allow_playback': 'Y'}, {'allow_capture': 'N'}, {'legacy_quiesced': 'N'}, {'id': 'OTHER'}, {'info': 'stream: PLAYBACK'}):
            values = dict(self.fm_metadata)
            for name, value in changes.items():
                values.update({key: value for key in values if key.endswith('/'+name)})
            with self.subTest(changes=changes), patch.object(Path, 'read_text', lambda path: values[str(path)]), patch('shutil.which', return_value='/usr/bin/arecord'), patch('os.open', side_effect=AssertionError('raw hardware')):
                if changes:
                    with self.assertRaises(RuntimeError):
                        self.scope['fm_check']()
                else:
                    self.assertEqual(self.scope['fm_check'](), '/usr/bin/arecord')
        with patch.object(Path, 'read_text', lambda path: self.fm_metadata[str(path)]), patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'requires arecord'):
                self.scope['fm_check']()
        self.scope['kernel_owned'] = lambda: False
        with self.assertRaisesRegex(RuntimeError, 'not loaded'):
            self.scope['fm_check']()

    def test_fm_cli_exec_retains_lock_and_only_selects_capture(self):
        lock = MagicMock()
        lock.__enter__.return_value = lock
        lock.fileno.return_value = 99
        for command in (['fm-check'], ['fm-record', '5', '-']):
            with self.subTest(command=command), patch.object(Path, 'exists', return_value=True), patch.object(Path, 'read_text', lambda path: self.fm_metadata[str(path)]), patch.object(Path, 'open', return_value=lock), patch('shutil.which', return_value='/usr/bin/arecord'), patch('fcntl.flock') as flock, patch('os.set_inheritable') as inherit, patch('os.execv', side_effect=SystemExit(0)) as execute, patch('os.open', side_effect=AssertionError('raw hardware')), patch.object(sys, 'argv', ['tablet-audio', *command]), patch('sys.stdout', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(ROOT/'tablet-audio'), run_name='__main__')
                self.assertEqual(result.exception.code, 0)
                flock.assert_called_once_with(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if command[0] == 'fm-check':
                    execute.assert_not_called()
                    inherit.assert_not_called()
                else:
                    inherit.assert_called_once_with(99, True)
                    executable, args = execute.call_args.args
                    self.assertEqual(executable, '/usr/bin/arecord')
                    self.assertEqual(args, [executable, '-q', '--nonblock', '-D', 'hw:CARD=MT6755,DEV=1', '-t', 'raw', '-f', 'S16_LE', '-c', '2', '-r', '48000', '--buffer-size=8192', '--period-size=2048', '-d', '5', '--', '-'])
                    self.assertTrue(8192 <= 8192*2*2 <= 65536 and 1920 <= 2048*2*2 <= 16384)

    def test_fm_cli_invalid_requests_never_launch_or_open_hardware(self):
        for command in (['fm-check', 'extra'], ['fm-record'], ['fm-record', '0', '-'], ['fm-record', '301', '-'], ['fm-record', 'nan', '-'], ['fm-record', '1.5', '-']):
            with self.subTest(command=command), patch.object(sys, 'argv', ['tablet-audio', *command]), patch('os.open', side_effect=AssertionError('raw hardware')), patch('os.execv') as execute:
                with self.assertRaises((SystemExit, ValueError)):
                    runpy.run_path(str(ROOT/'tablet-audio'), run_name='__main__')
                execute.assert_not_called()

    def setUp(self):
        self.scope = definitions()
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.scope['VOLUME_PATH'] = Path(self.directory.name) / 'volume'
        self.scope['kernel_owned'] = lambda: True

    def test_volume_mixer_is_explicit_and_digital(self):
        with patch('subprocess.run') as run, patch('os.open', side_effect=AssertionError('hardware')):
            self.assertEqual(self.scope['output_volume']('up'), 35)
            run.assert_called_once_with(['amixer', '-q', '-c', 'MT6755', 'cset',
                                          'name=PCM Playback Volume', '35'], check=True, timeout=2)

    def test_mixer_failure_is_not_raw_fallback(self):
        with patch('subprocess.run', side_effect=OSError('mixer missing')), patch('os.open', side_effect=AssertionError('hardware')):
            with self.assertRaises(OSError):
                self.scope['output_volume'](0)
        self.assertEqual(self.scope['output_volume'](), 0)

    def test_loaded_module_blocks_every_raw_command_even_when_idle(self):
        for command in ('start', 'amp', 'stop', 'unknown'):
            with self.subTest(command=command), patch('pathlib.Path.open', return_value=MagicMock()), patch('fcntl.flock'), patch.object(sys, 'argv', ['tablet-audio', command]), patch('os.open', side_effect=AssertionError('hardware')):
                with self.assertRaisesRegex(SystemExit, 'ALSA owns'):
                    self.scope['hardware_admission']()

    def test_loaded_module_selects_alsa_without_raw_access(self):
        player = MagicMock()
        self.scope['play_alsa'] = player
        with patch('pathlib.Path.open', return_value=MagicMock()), patch('fcntl.flock'), patch.object(sys, 'argv', ['tablet-audio', 'play', 'file.wav']), patch('os.open', side_effect=AssertionError('hardware')):
            with self.assertRaises(SystemExit) as error:
                self.scope['hardware_admission']()
        self.assertEqual(error.exception.code, 0)
        player.assert_called_once_with('file.wav', None)

    def test_loaded_status_uses_owner_registers_without_raw_access(self):
        status = 'PWR_STATUS.audio=0 DAC_CON0=0x00000000 ADDA_DL=0x00000000 AUDDEC0=0x0000 UL_DL=0x0000\n'
        for failure in (None, OSError('status unavailable')):
            with self.subTest(failure=failure), patch.object(Path, 'open', return_value=MagicMock()), patch('fcntl.flock'), patch.object(sys, 'argv', ['tablet-audio', 'status']), patch('os.open', side_effect=AssertionError('raw hardware')), patch.object(Path, 'read_text', autospec=True, return_value=status, side_effect=failure) as read, patch('sys.stdout', new_callable=io.StringIO) as output:
                with self.assertRaises(OSError if failure else SystemExit):
                    self.scope['hardware_admission']()
                self.assertEqual(output.getvalue(), '' if failure else status)
                read.assert_called_once_with(Path('/sys/bus/platform/devices/mt6755-tablet/analogue_status'))

    def test_admission_lock_is_exclusive_and_nonblocking(self):
        self.scope['kernel_owned'] = lambda: False
        with patch('pathlib.Path.open', return_value=MagicMock()), patch('fcntl.flock') as lock:
            self.scope['hardware_admission']()
        import fcntl
        self.assertEqual(lock.call_args.args[1], fcntl.LOCK_EX | fcntl.LOCK_NB)

    def children(self):
        dec, sink = MagicMock(), MagicMock()
        dec.stdout = io.BytesIO()
        for child in (dec, sink):
            child.wait.return_value = 0
            child.poll.return_value = 0
        return dec, sink

    def test_pipeline_success_reaps_both(self):
        dec, sink = self.children()
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=[dec, sink]) as start:
            self.scope['play_alsa']('file.wav')
        self.assertIn('s16le', start.call_args_list[0].args[0])
        self.assertIn('hw:CARD=MT6755,DEV=0', start.call_args_list[1].args[0])
        self.assertTrue(dec.stdout.closed)
        self.assertGreaterEqual(dec.wait.call_count, 2)
        self.assertGreaterEqual(sink.wait.call_count, 2)

    def test_pipeline_setup_failures_cleanup(self):
        for where in ('decoder', 'sink'):
            dec, sink = self.children()
            dec.poll.return_value = None
            effects = [OSError('decoder')] if where == 'decoder' else [dec, OSError('sink')]
            with self.subTest(where=where), patch('subprocess.run'), patch('subprocess.Popen', side_effect=effects), patch('os.open', side_effect=AssertionError('hardware')):
                with self.assertRaises(OSError):
                    self.scope['play_alsa']('file.wav')
            if where == 'sink':
                dec.terminate.assert_called_once()
                dec.wait.assert_called_once_with(timeout=2)
                self.assertTrue(dec.stdout.closed)

    def test_pipeline_errors_do_not_fall_back(self):
        dec, sink = self.children()
        sink.wait.return_value = 1
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=[dec, sink]), patch('os.open', side_effect=AssertionError('hardware')):
            with self.assertRaisesRegex(RuntimeError, 'aplay=1'):
                self.scope['play_alsa']('file.wav')

    def test_startup_read_and_mixer_sync_share_side_button_lock(self):
        # Force mute between the startup read and mixer command. Real flock,
        # including a nonblocking contention probe, establishes the ordering.
        for existing in (False, True):
            with self.subTest(existing=existing):
                path = self.scope['VOLUME_PATH']
                if existing:
                    path.write_text('30\n')
                read = self.scope['output_volume']
                flock = fcntl.flock
                attempted = threading.Event()
                blocked, mixed, errors = [], [], []

                def mute():
                    try:
                        read(0)
                    except BaseException as error:
                        errors.append(error)

                muter = threading.Thread(target=mute)

                def lock(fd, flags):
                    if threading.current_thread() is muter:
                        try:
                            flock(fd, flags | fcntl.LOCK_NB)
                            blocked.append(False)
                        except BlockingIOError:
                            blocked.append(True)
                        finally:
                            attempted.set()
                    return flock(fd, flags)

                def startup_read(value=None):
                    level = read(value)
                    if threading.current_thread() is not muter:
                        muter.start()
                        self.assertTrue(attempted.wait(2))
                        if blocked == [False]:
                            muter.join(2)  # Baseline: mute completes before stale sync.
                    return level

                dec, sink = self.children()
                self.scope['output_volume'] = startup_read
                self.scope['sync_alsa_volume'] = mixed.append
                try:
                    with patch('fcntl.flock', side_effect=lock), patch('subprocess.Popen', side_effect=[dec, sink]):
                        self.scope['play_alsa']('offline')
                        muter.join(2)
                    self.assertFalse(muter.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(blocked, [True])
                    self.assertEqual(mixed, [30, 0])
                    self.assertEqual(path.read_text(), '0\n')
                finally:
                    self.scope['output_volume'] = read
                    muter.join(2)

    def test_failed_sink_reaps_decoder_blocked_on_empty_live_stdin(self):
        # Launch real harmless host children, never ffmpeg/aplay or a device.
        popen, children, errors = subprocess.Popen, [], []

        def start(argv, **kwargs):
            if argv[0] == 'ffmpeg':
                argv = [sys.executable, '-c', 'import sys; sys.stdin.buffer.read()']
                kwargs['stdin'] = subprocess.PIPE  # Held open, no input/EOF.
            else:
                self.assertEqual(argv[0], 'aplay')
                argv = [sys.executable, '-c', 'raise SystemExit(7)']
            child = popen(argv, **kwargs)
            children.append(child)
            return child

        def play():
            try:
                self.scope['play_alsa']('-')
            except BaseException as error:
                errors.append(error)

        player = threading.Thread(target=play)
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=start):
            player.start()
            player.join(4)
            try:
                self.assertFalse(player.is_alive(), 'failed sink waited on blocked decoder')
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], RuntimeError)
                self.assertIn('aplay=7', str(errors[0]))
                self.assertEqual(len(children), 2)
                self.assertIsNotNone(children[0].returncode)
                self.assertEqual(children[1].returncode, 7)
                self.assertTrue(children[0].stdout.closed)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=2)
                    if child.stdin is not None:
                        child.stdin.close()
                player.join(4)

    def test_successful_sink_still_checks_decoder_failure(self):
        dec, sink = self.children()
        dec.wait.return_value = 9
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=[dec, sink]):
            with self.assertRaisesRegex(RuntimeError, 'aplay=0, ffmpeg=9'):
                self.scope['play_alsa']('offline')

    def test_pipeline_interruption_and_timeout_kill_reap(self):
        dec, sink = self.children()
        dec.poll.return_value = sink.poll.return_value = None
        sink.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired('aplay', 2), 0]
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=[dec, sink]):
            with self.assertRaises(KeyboardInterrupt):
                self.scope['play_alsa']('file.wav')
        sink.kill.assert_called_once()
        dec.terminate.assert_called_once()
        dec.wait.assert_called_once_with(timeout=2)
        self.assertTrue(dec.stdout.closed)

    def test_pipeline_cleanup_error_still_cleans_decoder(self):
        dec, sink = self.children()
        sink.wait.side_effect = [0, OSError('cleanup')]
        dec.poll.return_value = None
        with patch('subprocess.run'), patch('subprocess.Popen', side_effect=[dec, sink]):
            with self.assertRaisesRegex(OSError, 'cleanup'):
                self.scope['play_alsa']('file.wav')
        dec.terminate.assert_called_once()
        dec.wait.assert_called()
        self.assertTrue(dec.stdout.closed)

    def test_invalid_initial_gain_never_launches(self):
        for value in (-1, 2, float('nan'), float('inf')):
            with patch('subprocess.Popen') as start:
                with self.assertRaises(ValueError):
                    self.scope['play_alsa']('file', value)
                start.assert_not_called()

    def test_raw_partial_initialization_always_attempts_all_cleanup(self):
        self.scope.update(SRAM_SIZE=16, SRAM=0x1000)
        self.scope['kernel_owned'] = lambda: False
        for stage in ('codec_on', 'afe_start', 'amp'):
            dec = MagicMock(stdout=io.BytesIO(bytes(16)))
            events = []
            def setup(name):
                def call(*args, **kwargs):
                    if name == stage and (name != 'amp' or args[0]):
                        raise OSError(name)
                    events.append(name if name != 'amp' else ('amp-on' if args[0] else 'amp-off'))
                return call
            for name in ('codec_on', 'afe_start', 'amp', 'afe_stop', 'codec_off'):
                self.scope[name] = setup(name)
            with patch('subprocess.Popen', return_value=dec), patch('select.select', return_value=([dec.stdout], [], [])):
                with self.assertRaises(OSError):
                    self.scope['play']('offline')
            self.assertEqual(events[-3:], ['amp-off', 'afe_stop', 'codec_off'])
            dec.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
