"""Exercise the real renderer main with in-memory device stand-ins only."""
import io
import itertools
from pathlib import Path
import runpy
import struct
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent


class RendererLifecycleTests(unittest.TestCase):
    def exercise(self, failure=None):
        scope = runpy.run_path(str(ROOT/'tablet-sentinel'), run_name='offline')['main'].__globals__
        scope['STOP'] = failure is not None
        scope['counters'] = lambda: None
        scope['read'] = lambda path, default='?': {
            '/sys/class/tty/tty0/active': 'tty1',
            '/sys/class/graphics/fb0/virtual_size': '800,1280',
            '/sys/class/graphics/fb0/stride': '3200',
            '/sys/class/graphics/fb0/bits_per_pixel': '32',
            '/sys/class/graphics/fbcon/rotate': '1',
        }.get(path, default)
        scope['volume_status'] = lambda: (50, 0)
        memory = MagicMock()
        memory.__getitem__.return_value = bytes(3200*1280)
        if failure == 'restore':
            memory.__setitem__.side_effect = OSError('restore failed')
        elif failure == 'close':
            memory.close.side_effect = OSError('close failed')
        turns = []
        def sleeping(_):
            turns.append(None)
            if len(turns) == 3:
                scope['STOP'] = True
        def ioctl(fd, command, argument):
            return struct.pack('i', 0) if command == 0x4B3B else 0
        with patch.object(sys, 'argv', ['tablet-sentinel','--volume-osd','--seconds','2']), patch('signal.signal'), patch('os.open', side_effect=[11,12]), patch('os.close') as close, patch('fcntl.flock'), patch('fcntl.ioctl', side_effect=ioctl) as control, patch('mmap.mmap', return_value=memory), patch('time.monotonic', side_effect=itertools.count(100)), patch('time.sleep', side_effect=sleeping), patch('sys.stdout', new_callable=io.StringIO):
            if failure:
                with self.assertRaises(OSError):
                    scope['main']()
            else:
                scope['main']()
            self.assertIn(((11,0x4B3A,0),{}),control.call_args_list)
            self.assertEqual(sorted(c.args[0] for c in close.call_args_list),[11,12])
            memory.close.assert_called_once()
        return turns

    def test_cleanup_restores_text_despite_framebuffer_failure(self):
        for failure in ('restore','close'):
            with self.subTest(failure=failure):
                self.exercise(failure)

    def test_osd_waits_for_controller_even_after_local_deadline(self):
        self.assertEqual(len(self.exercise()),3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
