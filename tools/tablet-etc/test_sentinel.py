"""Offline control/renderer tests. Synthetic inputs are not reception evidence."""
import importlib.machinery
import importlib.util
import io
import json
import os
import runpy
import struct
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('sentinel', str(Path(__file__).resolve().parent / 'tablet-sentinel'))
spec = importlib.util.spec_from_loader(loader.name, loader)
s = importlib.util.module_from_spec(spec)
loader.exec_module(s)
s.FONT = os.environ.get('SENTINEL_TEST_FONT', s.FONT)

# Synthetic codec state for offline admission tests, not a device readback.
SAFE_STATUS = ('PWR_STATUS.audio=0 DAC_CON0=0x00000000 ADDA_DL=0x00000000 '
               'PMIC AUDDEC0=0x0000 UL_DL=0x0000\n')


class Process:
    def __init__(self, output=b'', returncode=None):
        r, self.w = os.pipe()
        self.stdout = os.fdopen(r, 'rb', buffering=0)
        os.set_blocking(r, False)
        self.stdin = io.BytesIO()
        self.returncode = returncode
        if output:
            os.write(self.w, output)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.returncode or 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def dispose(self):
        for stream in (self.stdin, self.stdout):
            if not stream.closed:
                stream.close()
        os.close(self.w)


class SentinelTests(unittest.TestCase):
    def setUp(self):
        self.fm = s.FM(False)
        self.processes = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.fm.close()
        for process in self.processes:
            process.dispose()

    def process(self, output=b'', returncode=None):
        process = Process(output, returncode)
        self.processes.append(process)
        return process

    def receiver(self, output=b''):
        self.fm.tuner = self.process(output)
        self.fm.target, self.fm.phase = 8750, 'tuning'
        self.fm.deadline = time.monotonic()+8

    def test_disabled_does_not_open_hardware(self):
        with patch.object(s.subprocess, 'Popen', side_effect=AssertionError):
            self.assertIsNone(s.FM(False).tuner)

    def test_missing_receiver_is_optional(self):
        with patch.object(s.Path, 'exists', return_value=False):
            self.assertEqual(s.FM().status, 'FM unavailable')

    def test_busy_receiver_not_stolen(self):
        with patch.object(s.Path, 'exists', return_value=True), patch.object(s.subprocess, 'run', return_value=type('R', (), {'returncode': 0})()):
            self.assertIn('busy', s.FM().status)

    def test_failed_silent_preflight_does_not_start_tuner(self):
        with patch.object(s.Path, 'exists', return_value=True), patch.object(s.subprocess, 'run', side_effect=[type('R', (), {'returncode': 1})(), type('R', (), {'returncode': 1})()]), patch.object(s.subprocess, 'Popen', side_effect=AssertionError):
            self.assertIn('preflight failed', s.FM().status)

    def test_actual_controller_line_format(self):
        self.receiver(b'tuned 87.50 MHz\nrssi -93 dBm, mono\nrds PI ABCD\nrds PS "TEST FM "\n')
        self.fm.phase = 'station-tuning'
        self.fm.poll()
        self.assertEqual(self.fm.samples[8750][0], -93)
        self.assertEqual(self.fm.names[8750][0], 'TEST FM')
        self.assertEqual(self.fm.phase, 'dwell')

    def test_partial_lines_wait(self):
        self.receiver(b'tuned 87.')
        self.fm.poll()
        self.assertIsNone(self.fm.frequency)
        os.write(self.fm.tuner.w, b'50 MHz\nrssi -97 dBm, mono\n')
        self.fm.poll()
        self.assertEqual(self.fm.samples[8750][0], -97)

    def test_mismatched_frequency_stops(self):
        self.receiver(b'tuned 101.90 MHz\n')
        self.fm.poll()
        self.assertIsNone(self.fm.tuner)
        self.assertIn('unexpected', self.fm.status)

    def test_tuner_error_stops(self):
        self.receiver(b'tune: Input/output error\n')
        self.fm.poll()
        self.assertIsNone(self.fm.tuner)
        self.assertIn('failed', self.fm.status)

    def test_tuner_timeout_stops(self):
        self.receiver()
        self.fm.deadline = 0
        self.fm.poll()
        self.assertIsNone(self.fm.tuner)
        self.assertIn('timeout', self.fm.status)

    def test_sweep_advances_exactly_one_channel(self):
        self.receiver()
        self.fm.phase, self.fm.deadline = 'sweep', 0
        self.fm.poll()
        self.assertEqual(self.fm.target, 8760)
        self.assertEqual(self.fm.tuner.stdin.getvalue(), b't 87.60\nr\n')

    def test_full_band_selects_separated_peaks(self):
        self.receiver()
        self.fm.target, self.fm.phase, self.fm.deadline = 10800, 'sweep', 0
        now = time.monotonic()
        self.fm.samples = {8750: (-100, now), 10190: (-80, now), 10200: (-81, now), 8870: (-90, now)}
        self.fm.poll()
        self.assertEqual(self.fm.targets, [10190, 8870, 8750])
        self.assertEqual(self.fm.target, 10190)
        self.assertEqual(self.fm.phase, 'station-tuning')

    def test_audio_is_measured_fft_not_animation(self):
        import numpy as np
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        mono = (10000*np.sin(np.arange(1024)*2*np.pi*1000/48000)).astype('<i2')
        raw = np.column_stack((mono, mono)).tobytes()
        self.fm.capture = self.process(raw)
        self.fm.poll()
        self.assertEqual(len(self.fm.bins), 40)
        self.assertGreater(max(self.fm.bins), -20)
        self.assertLess(max(self.fm.bins), 0)
        self.assertEqual(self.fm.audio, 'FM audio / dBFS')

    def test_zero_audio_is_labelled_silence(self):
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        self.fm.capture = self.process(bytes(4096))
        self.fm.poll()
        self.assertIn('digital silence', self.fm.audio)
        self.assertTrue(all(value == -120 for value in self.fm.bins))

    def test_stale_audio_clears_bars(self):
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        self.fm.bins, self.fm.audio_at = [-10]*40, time.monotonic()-1
        self.fm.poll()
        self.assertEqual(self.fm.bins, [])
        self.assertIn('not live', self.fm.audio)

    def test_capture_failure_never_invents_bars(self):
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        self.fm.capture = self.process(returncode=1)
        self.fm.poll()
        self.assertEqual(self.fm.bins, [])
        self.assertIn('unavailable', self.fm.audio)

    def test_tune_clears_old_audio_and_frequency(self):
        self.receiver()
        self.fm.bins = [-10]*40
        self.fm.frequency = 8750
        self.fm.tune(8870, 'station-tuning')
        self.assertEqual(self.fm.bins, [])
        self.assertIsNone(self.fm.frequency)

    def test_audio_diagnostics_use_both_channels(self):
        import numpy as np
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        self.fm.capture = self.process(np.tile(np.array([16384, -16384], dtype='<i2'), 1024).tobytes())
        self.fm.poll()
        self.assertAlmostEqual(self.fm.level_dbfs, -6.0206, places=3)
        self.assertAlmostEqual(self.fm.peak_dbfs, -6.0206, places=3)
        self.assertFalse(self.fm.clipped)
        self.assertEqual(self.fm.audio, 'FM audio / dBFS')

    def test_clipping_and_partial_pcm_frame(self):
        import numpy as np
        self.receiver()
        self.fm.frequency, self.fm.phase = 10190, 'dwell'
        raw = np.tile(np.array([32767, -32768], dtype='<i2'), 1025).tobytes()
        self.fm.capture = self.process(raw[:-3])
        self.fm.poll()
        self.assertTrue(self.fm.clipped)
        os.write(self.fm.capture.w, raw[-3:])
        self.fm.poll()
        self.assertTrue(self.fm.clipped)
        self.assertEqual(len(self.fm.bins), 40)

    def test_retune_clears_audio_diagnostics(self):
        self.receiver()
        self.fm.level_dbfs, self.fm.peak_dbfs, self.fm.clipped = -6, 0, True
        self.fm.audio_at = time.monotonic()
        self.fm.tune(8870, 'station-tuning')
        self.assertIsNone(self.fm.level_dbfs)
        self.assertIsNone(self.fm.clipped)
        self.assertEqual(self.fm.audio_at, 0)

    def test_health_uses_real_source_contracts_and_warns(self):
        battery = '/sys/class/power_supply/battery/'
        values = {battery+'capacity': '15', battery+'health': 'Good', battery+'temp': '300', battery+'current_now': '100000', battery+'voltage_now': '4000000', '/proc/meminfo': 'MemTotal: 2048000 kB\nMemAvailable: 1024000 kB', '/test/thermal/type': 'cpu-thermal', '/test/thermal/temp': '85000', '/test/thermal/trip_point_0_type': 'hot', '/test/thermal/trip_point_0_temp': '80000'}
        paths = {'/sys/class/thermal/thermal_zone*': ['/test/thermal'], '/test/thermal/trip_point_*_type': ['/test/thermal/trip_point_0_type']}
        gps = dict(mode=None, used=None, tpv_at=0, sky_at=0, fix_time=None)
        with patch.object(s, 'read', side_effect=lambda p, default='?': values.get(p, default)), patch.object(s.glob, 'glob', side_effect=lambda p: paths.get(p, [])), patch.object(s, 'counters', return_value=(time.monotonic(), {}, {'wlan0': (0,0), 'tun0': (0,0)})), patch.object(s.subprocess, 'run', return_value=type('R', (), {'returncode': 0, 'stdout': 'example.service loaded failed failed\n'})()):
            _, sections = s.telemetry((0, {}, {'wlan0': (0,0), 'tun0': (0,0)}), gps)
        self.assertEqual(len(sections), 4)
        health = ' '.join(sections[3][1])
        self.assertIn('failed services: 1', health)
        self.assertIn('battery <=15%', health)
        self.assertIn('thermal hot', health)
        self.assertIn('stale/unknown', health)

    def test_four_panels_wrap_without_overlap_or_lost_rows(self):
        sections = [('CPU / MEMORY', ['load 100.0% / 85.0C', 'RAM 2000/2048 MiB', '0-3 100.0% 100.0% 100.0% 100.0%', '4-7 100.0% 100.0% 100.0% 100.0%', 'DVFS high', 'load 10.00 10.00 10.00']), ('NETWORK / SYSTEM', ['wlan RX/TX 12345.6/12345.6 KiB/s', 'VPN RX/TX 12345.6/12345.6 KiB/s', 'Wi-Fi -55dBm', 'USB device / charger 1', 'disk free 10.0 GiB', 'up 100d 23:59:59']), ('GPS / POWER', ['GPS awaiting reports / ? used', 'position hidden', 'battery 15% / 30.0C', '-1000mA 4.00V 4.00W', 'discharging', 'charge band 70-80%']), ('HEALTH / AUDIO', ['failed services: unknown', 'warnings: battery <=15%, battery overheat, thermal hot, thermal critical, sensor missing', 'GPS data: stale/unknown'])]
        self.fm.audio = 'FM digital capture unavailable'
        original = s.ImageDraw.ImageDraw.text
        for rotation in range(4):
            calls = []
            def record(draw, xy, text, *args, **kwargs):
                calls.append((xy, text, kwargs.get('font')))
                return original(draw, xy, text, *args, **kwargs)
            with patch.object(s.ImageDraw.ImageDraw, 'text', autospec=True, side_effect=record):
                image = s.background(rotation, sections, time.monotonic()+180, self.fm)
            titles = [(xy, text) for xy, text, _ in calls if text in [p[0] for p in sections]]
            self.assertEqual(len(titles), 4)
            self.assertEqual(len({xy[1] for xy, _ in titles}), 1 if rotation%2 else 2)
            for (x,y), text, font in calls:
                if y >= (548 if rotation%2 else 590) and y < image.height-57:
                    right = (330 if x < 354 else 628 if x < 652 else 926 if x < 950 else 1224) if rotation%2 else (380 if x < 420 else 764)
                    self.assertLessEqual(x+font.getlength(text), right)
                    self.assertLess(y+font.getbbox(text)[3], image.height-57)
            drawn = ' '.join(text for _, text, _ in calls)
            self.assertIn('HEALTH / AUDIO', drawn)
            self.assertIn('RMS / peak: unavailable', drawn)
            self.assertIn('charge band 70-80%', drawn)
        self.assertEqual(len(sections[3][1]), 3, 'render must not mutate caller rows')

    def test_stale_audio_never_shows_live_metrics_in_panel(self):
        self.fm.bins, self.fm.level_dbfs, self.fm.peak_dbfs = [-10]*40, -6, -1
        self.fm.audio_at = time.monotonic()-1
        texts = []
        with patch.object(s.ImageDraw.ImageDraw, 'text', autospec=True, side_effect=lambda draw, xy, text, **kwargs: texts.append(text)):
            s.background(1, [('a',[]), ('b',[]), ('c',[])], time.monotonic()+180, self.fm)
        self.assertIn('capture: stale / not live', texts)
        self.assertIn('RMS / peak: unavailable', texts)

    def test_level_meter_only_draws_for_fresh_samples(self):
        self.fm.bins, self.fm.level_dbfs, self.fm.peak_dbfs, self.fm.clipped = [-10]*40, -6, -1, False
        self.fm.audio_at = time.monotonic()
        rectangles = []
        original = s.ImageDraw.ImageDraw.rectangle
        def record(draw, xy, *args, **kwargs):
            rectangles.append(xy)
            return original(draw, xy, *args, **kwargs)
        sections = [('a',[]), ('b',[]), ('c',[])]
        with patch.object(s.ImageDraw.ImageDraw, 'rectangle', autospec=True, side_effect=record):
            s.background(1, sections, time.monotonic()+180, self.fm)
        meters = [r for r in rectangles if r[0] == 950 and r[1] >= 548]
        self.assertEqual(len(meters), 2)
        self.assertTrue(all(r[3] < 743 for r in meters))
        self.fm.audio_at = time.monotonic()-1
        rectangles.clear()
        with patch.object(s.ImageDraw.ImageDraw, 'rectangle', autospec=True, side_effect=record):
            s.background(1, sections, time.monotonic()+180, self.fm)
        self.assertFalse([r for r in rectangles if r[0] == 950 and r[1] >= 548])

    def test_renderer_all_rotations_preserves_telemetry(self):
        self.fm.names = {10190: ('TEST FM', time.monotonic())}
        self.fm.samples = {f: (-95, time.monotonic()) for f in range(8750,10801,10)}
        sections = [('CPU / MEMORY', ['cpu']*6), ('NETWORK / SYSTEM', ['network']*6), ('GPS / POWER', ['gps']*6)]
        for rotation in range(4):
            image = s.frame(s.background(rotation, sections, time.monotonic()+180, self.fm), (0,0), compact=True)
            self.assertEqual(s.physical(image, rotation).size, (800,1280))
            s.ImageDraw.Draw(image).text((36, 135), 'SYNTHETIC OFFLINE / NOT LIVE RF OR PCM', font=s.ImageFont.truetype(s.FONT, 14), fill=255)
            image.save(Path(__file__).parent/f'synthetic-offline-rotation-{rotation}.png')


    # Additional register/RF/PCM regressions use synthetic fixtures only.
    def test_missing_pcm_module_still_starts_muted_receiver(self):
        tuner = self.process()
        checks = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=0, stdout=SAFE_STATUS)]
        with patch.object(s.Path, 'exists', side_effect=lambda p: str(p) == '/dev/fm', autospec=True), patch.object(s.subprocess, 'run', side_effect=checks) as run, patch.object(s.os, 'open', return_value=91) as opening, patch.object(s.os, 'close') as closing, patch.object(s.mmap, 'mmap', return_value=nullcontext(bytes(4096))) as mapping, patch.object(s.subprocess, 'Popen', return_value=tuner) as launch:
            self.fm = s.FM()
        self.assertIs(self.fm.tuner, tuner)
        self.assertFalse(self.fm.capture_ready)
        self.assertIn('kernel FM capture absent', self.fm.audio)
        self.assertEqual([c.args[0] for c in run.call_args_list], [['fuser', '/dev/fm'], ['/usr/local/sbin/tablet-audio', 'status']])
        launch.assert_called_once_with(['/usr/local/bin/fmctl', '-m', '87.5'], stdin=s.subprocess.PIPE, stdout=s.subprocess.PIPE, stderr=s.subprocess.STDOUT)
        opening.assert_called_once_with('/dev/mem', os.O_RDONLY | os.O_SYNC)
        mapping.assert_called_once_with(91, 4096, flags=s.mmap.MAP_SHARED, prot=s.mmap.PROT_READ, offset=0x10005000)
        closing.assert_called_once_with(91)
        os.write(tuner.w, b'tuned 87.50 MHz\nrssi -95 dBm, mono\n')
        self.fm.poll()
        self.assertEqual(self.fm.samples[8750][0], -95)
        self.assertEqual(self.fm.phase, 'sweep')

    def test_status_gate_rejects_missing_nonzero_duplicate_and_malformed_fields(self):
        for token in SAFE_STATUS.split():
            if '=' not in token:
                continue
            key, value = token.split('=')
            for invalid in (SAFE_STATUS.replace(token, ''), SAFE_STATUS.replace(token, key+'=1'), SAFE_STATUS+token, SAFE_STATUS.replace(token, token+'bad')):
                with self.subTest(field=key, invalid=invalid), patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=invalid)), patch.object(s.os, 'open', side_effect=AssertionError('memory opened despite unsafe codec')):
                    with self.assertRaisesRegex(RuntimeError, 'silent preflight failed'):
                        self.fm.silent_preflight()

    def test_amplifier_gate_rejects_each_unsafe_mode_or_output_pin(self):
        for pin in (0, 54, 62):
            index = pin % 10
            shift = index*3 if index < 5 else 16+(index-5)*3
            for offset, bit in ((0x300+(pin//10)*0x10, shift), (0x100+(pin//32)*0x10, pin % 32)):
                page = bytearray(4096)
                struct.pack_into('<I', page, offset, 1 << bit)
                before = bytes(page)
                with self.subTest(pin=pin, offset=offset), patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=SAFE_STATUS)), patch.object(s.os, 'open', return_value=91), patch.object(s.os, 'close') as closing, patch.object(s.mmap, 'mmap', return_value=nullcontext(page)):
                    with self.assertRaisesRegex(RuntimeError, f'GPIO{pin}'):
                        self.fm.silent_preflight()
                    closing.assert_called_once_with(91)
                self.assertEqual(bytes(page), before, 'preflight must never write GPIO registers')

    def test_gpio_mapping_failure_closes_fd(self):
        with patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=SAFE_STATUS)), patch.object(s.os, 'open', return_value=91), patch.object(s.os, 'close') as closing, patch.object(s.mmap, 'mmap', side_effect=OSError('mapping denied')):
            with self.assertRaisesRegex(OSError, 'mapping denied'):
                self.fm.silent_preflight()
            closing.assert_called_once_with(91)

    def test_legacy_unknown_fm_check_success_cannot_enable_pcm(self):
        tuner = self.process()
        with patch.object(s.Path, 'exists', return_value=True), patch.object(s.FM, 'silent_preflight'), patch.object(s.subprocess, 'run', side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0, stdout=SAFE_STATUS)]), patch.object(s.subprocess, 'Popen', return_value=tuner):
            self.fm = s.FM()
        self.assertIs(self.fm.tuner, tuner)
        self.assertFalse(self.fm.capture_ready)
        self.fm.phase = 'station-tuning'
        os.write(tuner.w, b'tuned 87.50 MHz\nrssi -95 dBm, mono\n')
        with patch.object(s.subprocess, 'Popen', side_effect=AssertionError('legacy helper capture attempted')):
            self.fm.poll()
        self.assertEqual(self.fm.phase, 'dwell')
        self.assertIn('unavailable', self.fm.audio)

    def test_optional_capture_launch_failure_does_not_stop_rf(self):
        self.receiver(b'tuned 87.50 MHz\nrssi -95 dBm, mono\n')
        self.fm.phase, self.fm.capture_ready = 'station-tuning', True
        with patch.object(s.subprocess, 'Popen', side_effect=OSError('capture absent')):
            self.fm.poll()
        self.assertIsNotNone(self.fm.tuner)
        self.assertIsNone(self.fm.capture)
        self.assertEqual(self.fm.phase, 'dwell')
        self.assertIn('capture unavailable', self.fm.audio)

    def test_retune_rejects_old_rf_and_names_until_ack_and_fresh_pi(self):
        self.receiver()
        self.fm.frequency, self.fm.rds_pi = 8750, 0xAAAA
        self.fm.names[8870] = ('OLD NAME', time.monotonic()-20)
        self.fm.buffer = b'rds PS "OLD PART'
        self.fm.tune(8870, 'station-tuning')
        self.assertEqual(self.fm.buffer, b'')
        self.assertNotIn(8870, self.fm.names)
        os.write(self.fm.tuner.w, b'rssi -20 dBm, mono\nrds PI AAAA\nrds PS "OLD NAME"\n')
        self.fm.poll()
        self.assertNotIn(8870, self.fm.samples)
        self.assertNotIn(8870, self.fm.names)
        self.assertIn('awaiting tune', self.fm.status)
        os.write(self.fm.tuner.w, b'tuned 88.70 MHz\nrssi -97 dBm, mono\nrds PS "OLD NAME"\n')
        self.fm.poll()
        self.assertNotIn(8870, self.fm.names)
        os.write(self.fm.tuner.w, b'rds PI BBBB\nrds PS "NEW NAME"\n')
        self.fm.poll()
        self.assertEqual(self.fm.names[8870][0], 'NEW NAME')
        os.write(self.fm.tuner.w, b'rds PI CCCC\n')
        self.fm.poll()
        self.assertNotIn(8870, self.fm.names)

    def test_rds_between_tune_ack_and_rssi_is_bound_to_new_station(self):
        self.receiver(b'tuned 87.50 MHz\nrds PI ABCD\nrds PS "NEW NAME"\nrssi -95 dBm, mono\n')
        self.fm.phase = 'station-tuning'
        self.fm.poll()
        self.assertEqual(self.fm.names[8750][0], 'NEW NAME')
        self.assertEqual(self.fm.samples[8750][0], -95)
        self.assertEqual(self.fm.phase, 'dwell')

    def test_retune_drains_unread_old_station_reports(self):
        self.receiver(b'rssi -10 dBm, stereo\nrds PI AAAA\nrds PS "OLD NAME"\n')
        self.fm.frequency = 8750
        self.fm.tune(8870, 'station-tuning')
        self.fm.poll()
        self.assertEqual(self.fm.samples, {})
        self.assertEqual(self.fm.names, {})
        self.assertIn('awaiting tune', self.fm.status)
        self.assertIsNone(self.fm.frequency)

    def test_partial_pcm_frame_cannot_refresh_old_metrics(self):
        self.receiver()
        self.fm.capture = self.process(bytes(4096))
        self.fm.poll()
        first_at = self.fm.audio_at
        os.write(self.fm.capture.w, b'\x01')
        with patch.object(s.time, 'monotonic', return_value=first_at+0.6):
            self.fm.poll()
        self.assertEqual(self.fm.audio_at, first_at)
        self.assertEqual(self.fm.bins, [])
        self.assertIn('not live', self.fm.audio)

    def test_tuner_cleanup_runs_even_if_capture_cleanup_raises(self):
        self.receiver()
        process = self.fm.tuner
        with patch.object(self.fm, 'stop_capture', side_effect=RuntimeError('capture cleanup failed')):
            with self.assertRaisesRegex(RuntimeError, 'capture cleanup failed'):
                self.fm.close()
        self.assertIsNone(self.fm.tuner)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertEqual(self.fm.phase, 'off')

    def test_warnings_have_nonoverlapping_glyphs_and_complete_content(self):
        warnings = 'battery <=15%, battery overheat, thermal hot, thermal critical, sensor missing, battery health unknown'
        sections = [('CPU / MEMORY', ['0-3 100.0% 100.0% 100.0% 100.0%']*6), ('NETWORK / SYSTEM', ['wlan RX/TX 12345.6/12345.6 KiB/s']*6), ('GPS / POWER', ['GPS awaiting reports / ? used']*6), ('HEALTH / AUDIO', ['failed services: unknown', 'warnings: '+warnings, 'GPS data: stale/unknown'])]
        self.fm.audio = 'PCM unavailable: kernel FM capture absent'
        for rotation in range(4):
            calls = []
            with patch.object(s.ImageDraw.ImageDraw, 'text', autospec=True, side_effect=lambda draw, xy, text, **kw: calls.append((xy, text, kw['font']))):
                s.background(rotation, sections, time.monotonic()+180, self.fm)
            x, y = (950, 548) if rotation % 2 else (420, 896)
            panel = [(xy, text, font) for xy, text, font in calls if xy[0] == x and y < xy[1] < (743 if rotation % 2 else 1223)]
            drawn = ' '.join(text for _, text, _ in panel)
            for warning in warnings.split(', '):
                self.assertIn(warning, drawn)
            for (xy, text, font), (next_xy, _, next_font) in zip(panel, panel[1:]):
                self.assertGreaterEqual(next_xy[1]-xy[1], font.size+3, 'warning baselines collapse')
                self.assertLessEqual(xy[1]+font.getbbox(text)[3], next_xy[1]+next_font.getbbox('M')[1])

    def test_stale_pcm_clears_all_diagnostics_and_plot(self):
        self.receiver()
        self.fm.bins, self.fm.level_dbfs, self.fm.peak_dbfs, self.fm.clipped = [-10]*40, -6, -1, True
        self.fm.audio_at = time.monotonic()-1
        rectangles = []
        with patch.object(s.ImageDraw.ImageDraw, 'rectangle', autospec=True, side_effect=lambda draw, xy, **kwargs: rectangles.append(xy)):
            s.background(1, [('a', []), ('b', []), ('c', [])], time.monotonic()+180, self.fm)
        self.assertEqual(rectangles, [], 'neither stale spectrum nor level meter may be drawn')
        self.fm.poll()
        self.assertIsNone(self.fm.level_dbfs)
        self.assertIsNone(self.fm.peak_dbfs)
        self.assertIsNone(self.fm.clipped)


class MainIntegrationTests(unittest.TestCase):
    def scope(self):
        scope = runpy.run_path(str(Path(__file__).parent/'tablet-sentinel'), run_name='offline')['main'].__globals__
        scope['counters'] = lambda: None
        scope['FONT'] = s.FONT
        scope['read'] = lambda path, default='?': {
            '/sys/class/tty/tty0/active': 'tty1',
            '/sys/class/graphics/fb0/virtual_size': '800,1280',
            '/sys/class/graphics/fb0/stride': '3200',
            '/sys/class/graphics/fb0/bits_per_pixel': '32',
            '/sys/class/graphics/fbcon/rotate': '1',
        }.get(path, default)
        return scope

    def test_render_never_starts_radio_or_opens_display(self):
        scope = self.scope()
        scope['telemetry'] = lambda *_: (None, [('a', []), ('b', []), ('c', [])])
        scope['gps_poll'] = lambda *_: None
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, patch.object(sys, 'argv', ['tablet-sentinel', '--render', str(Path(directory)/'synthetic.png')]), patch('signal.signal'), patch('os.open', side_effect=AssertionError('device opened')), patch('subprocess.Popen', side_effect=AssertionError('radio started')), patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            scope['main']()
            self.assertEqual(s.Image.open(Path(directory)/'synthetic.png').size, (1280, 800))

    def test_standalone_osd_does_not_even_construct_radio(self):
        scope = self.scope()
        scope['STOP'] = True
        scope['FM'] = lambda **_: self.fail('OSD constructed FM')
        from unittest.mock import MagicMock
        memory = MagicMock()
        memory.__getitem__.return_value = bytes(3200*1280)
        with patch.object(sys, 'argv', ['tablet-sentinel', '--volume-osd']), patch('signal.signal'), patch('os.open', side_effect=[11, 12]), patch('os.close'), patch('fcntl.flock'), patch('fcntl.ioctl', return_value=struct.pack('i', 0)), patch('mmap.mmap', return_value=memory), patch('sys.stderr', new_callable=io.StringIO):
            scope['main']()
        memory.close.assert_called_once()

    def test_fm_and_console_cleanup_survive_independent_failures(self):
        from unittest.mock import MagicMock
        for failure in ('framebuffer', 'fm', 'both'):
            with self.subTest(failure=failure):
                scope = self.scope()
                scope['STOP'] = True
                fm, memory = MagicMock(), MagicMock()
                scope['FM'] = lambda **_: fm
                if failure in ('framebuffer', 'both'):
                    memory.close.side_effect = OSError('framebuffer close failed')
                if failure in ('fm', 'both'):
                    fm.close.side_effect = OSError('FM close failed')
                with patch.object(sys, 'argv', ['tablet-sentinel']), patch('signal.signal'), patch('os.open', side_effect=[11, 12]), patch('os.close') as closing, patch('fcntl.flock'), patch('fcntl.ioctl', return_value=struct.pack('i', 0)) as control, patch('mmap.mmap', return_value=memory), patch('sys.stderr', new_callable=io.StringIO):
                    with self.assertRaises(OSError):
                        scope['main']()
                    fm.close.assert_called_once()
                    memory.close.assert_called_once()
                    self.assertIn(((11, 0x4B3A, 0), {}), control.call_args_list)
                    self.assertEqual(sorted(c.args[0] for c in closing.call_args_list), [11, 12])

    def test_pcm_freshness_transition_invalidates_cached_background(self):
        from unittest.mock import MagicMock
        scope = self.scope()
        scope['telemetry'] = lambda *_: (None, [('a', []), ('b', []), ('c', [])])
        scope['gps_poll'] = lambda *_: None
        scope['volume_status'] = lambda: None
        fm = s.FM(False)
        fm.frequency, fm.bins, fm.audio_at = 10190, [-10]*40, 10.0
        fm.poll, fm.close = MagicMock(), MagicMock()
        scope['FM'] = lambda **_: fm
        clock, backgrounds = [10.0], []
        def sleeping(seconds):
            clock[0] = round(clock[0]+seconds, 6)
            if clock[0] >= 10.8:
                scope['STOP'] = True
        def draw_background(*_):
            backgrounds.append(clock[0])
            return s.Image.new('L', (1280, 800))
        scope['background'] = draw_background
        with patch.object(sys, 'argv', ['tablet-sentinel']), patch('signal.signal'), patch('time.monotonic', side_effect=lambda: clock[0]), patch('time.sleep', side_effect=sleeping), patch('os.open', side_effect=[11, 12]), patch('os.close'), patch('fcntl.flock'), patch('fcntl.ioctl', return_value=struct.pack('i', 0)), patch('mmap.mmap', return_value=MagicMock()), patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            scope['main']()
        self.assertIn(10.6, backgrounds, 'freshness transition must invalidate the 1s stats cache')
        fm.close.assert_called_once()

    def test_probe_is_bounded_json_and_never_reads_gps_or_display(self):
        from unittest.mock import MagicMock
        scope = self.scope()
        fm = s.FM(False)
        fm.samples = {8750: (-97, 10.0)}  # Explicit synthetic observation.
        fm.poll, fm.close = MagicMock(), MagicMock()
        scope['FM'] = lambda: fm
        scope['gps_poll'] = lambda *_: self.fail('probe read GPS')
        scope['telemetry'] = lambda *_: self.fail('probe read telemetry')
        clock = [0.0]
        def sleeping(seconds):
            clock[0] += seconds
        with patch.object(sys, 'argv', ['tablet-sentinel', '--fm-probe', '0.3']), patch('signal.signal'), patch('time.monotonic', side_effect=lambda: clock[0]), patch('time.sleep', side_effect=sleeping), patch('os.open', side_effect=AssertionError('display opened')), patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO):
            scope['main']()
        measured = json.loads(output.getvalue())
        self.assertEqual(measured['samples']['8750'], [-97, 10.0])
        self.assertNotIn('lat', measured)
        self.assertNotIn('lon', measured)
        self.assertAlmostEqual(clock[0], 0.3)
        fm.close.assert_called_once()

    def test_probe_rejects_unbounded_or_conflicting_modes_before_hardware(self):
        for arguments in (['--fm-probe', '0'], ['--fm-probe', '301'], ['--fm-probe', 'nan'], ['--fm-probe', 'inf'], ['--fm-probe', '1', '--volume-osd'], ['--fm-probe', '1', '--render', 'ignored.png']):
            scope = self.scope()
            scope['FM'] = lambda **_: self.fail('invalid probe constructed FM')
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['tablet-sentinel', *arguments]), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as result:
                    scope['main']()
                self.assertEqual(result.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
