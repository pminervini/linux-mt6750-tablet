#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""SYNTHETIC/OFFLINE tests only. Actual tablet-camera execution is --help only.

Photo definitions run with synthetic sysfs/frames and offline lock files; owned
children are local fixtures, never a device-facing v4l2-ctl process.
C tests compile shared production DMA/packing/FIFO code, and extracted actual
STREAMON/STREAMOFF and shutdown bodies against explicit kernel-boundary mocks.
They are not vb2-core integration tests and do not prove hardware behaviour.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
CAMERA = HERE.parent


def load_pure(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def c_function(source, name):
    import re
    match = re.search(r"^(?:static )?[^\n]+\b" + re.escape(name) + r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise ValueError(f"function not found: {name}")
    start = match.start()
    depth = 1
    index = match.end()
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index] + "\n"


def helper_function(name):
    tree = ast.parse((CAMERA / "tablet-camera").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return compile(ast.Module(body=[node], type_ignores=[]), "extracted-tablet-camera", "exec")


tables = load_pure("camera_tables", HERE / "generate_tables.py")
raw_develop = load_pure("raw_develop", CAMERA / "raw-develop.py")


class Tables(unittest.TestCase):
    def test_complete_preserved_tables_and_bounds(self):
        for filename, (sensor, last_reg) in tables.TABLES.items():
            rows = tables.parse_table((CAMERA / "data" / filename).read_text(), sensor, last_reg)
            self.assertGreater(len(rows), 0)
        self.assertEqual(tables.generate(CAMERA / "data"), tables.generate(CAMERA / "data"))

    def test_sensor_modes(self):
        for filename, width, height, hts, vts in (
            ("ov8856-preview.txt", 1632, 1224, 3744, 1246),
            ("ov8856-full.txt", 3264, 2448, 1932, 2482),
        ):
            regs = dict(tables.parse_table((CAMERA / "data" / filename).read_text(), True, 0xFFFF))
            for reg, expected in ((0x3808, width), (0x380A, height), (0x380C, hts), (0x380E, vts)):
                self.assertEqual(regs[reg] * 256 + regs[reg + 1], expected)
            self.assertEqual(regs[0x100], 1)
        front = tables.parse_table((CAMERA / "data/gc2365-init.txt").read_text(), True, 255)
        bank, regs = 0, {}
        for reg, value in front:
            if reg == 0xFE:
                bank = value
            else:
                regs[bank, reg] = value
        for reg, value in ((0x17, 0xC0), (0x92, 3), (0x94, 2)):
            self.assertEqual(regs[0, reg], value)  # BSP NO_MIRROR / STARTY / STARTX
        self.assertEqual(regs[0, 0x95] * 256 + regs[0, 0x96], 1200)
        self.assertEqual(regs[0, 0x97] * 256 + regs[0, 0x98], 1600)
        self.assertEqual(regs[0, 0x0D] * 256 + regs[0, 0x0E] + regs[0, 0x07] * 256 + regs[0, 0x08] + 16, 1240)
        self.assertEqual(tables.parse_table((CAMERA / "data/gc2365-stream.txt").read_text(), True, 255),
                         [(0xFE, 3), (0x10, 0x90), (0xFE, 0)])

    def test_reject_corrupt_register_tables(self):
        for payload, sensor, bound in (
            ("", True, 255), ("000 000 000", False, 0xFFC), ("001 00", False, 0xFFC),
            ("1000 1", False, 0xFFC), ("00 100", True, 255), ("100 0", True, 255),
            ("ffff 01", True, 0xFFFF), ("00 -1", True, 255), ("0x1 0", True, 255),
            ("delay 0", True, 255), ("delay 3001", True, 255), ("delay 1", False, 0xFC),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                tables.parse_table(payload, sensor, bound)
        self.assertEqual(tables.parse_table("delay 10", True, 255), [(0xFFFF, 10)])


class LegacyGuard(unittest.TestCase):
    def call_guard(self, loaded=False, busy=False, open_failure=False):
        events = []

        def open_file(*args):
            events.append("open")
            if open_failure:
                raise PermissionError("synthetic")
            return 91

        def lock(*args):
            events.append("lock")
            if busy:
                raise BlockingIOError("synthetic busy")

        def isdir(path):
            self.assertEqual(path, "/sys/module/mt6750_camera")
            events.append("check-module")
            return loaded

        os_mock = SimpleNamespace(open=open_file, close=lambda fd: events.append("close"),
                                  O_RDWR=2, O_CREAT=64, O_CLOEXEC=1, O_NOFOLLOW=2,
                                  path=SimpleNamespace(isdir=isdir))
        ns = {"os": os_mock, "fcntl": SimpleNamespace(flock=lock, LOCK_EX=2, LOCK_NB=4)}
        exec(helper_function("_legacy_guard"), ns)
        return ns["_legacy_guard"], events

    def test_unloaded_retains_lock(self):
        guard, events = self.call_guard()
        self.assertEqual(guard(), 91)
        self.assertEqual(events, ["open", "lock", "check-module"])

    def test_loaded_closes_and_refuses_before_devmem(self):
        guard, events = self.call_guard(loaded=True)
        with self.assertRaises(RuntimeError):
            guard()
        self.assertEqual(events, ["open", "lock", "check-module", "close"])

    def test_busy_and_open_failures(self):
        guard, events = self.call_guard(busy=True)
        with self.assertRaises(BlockingIOError):
            guard()
        self.assertEqual(events, ["open", "lock", "close"])
        guard, events = self.call_guard(open_failure=True)
        with self.assertRaises(PermissionError):
            guard()
        self.assertEqual(events, ["open"])

    def test_still_path_ast_unchanged_except_guard(self):
        original = LEGACY_HELPER.read_text()
        original_tree = ast.parse(original)
        current_tree = ast.parse((CAMERA / "tablet-camera").read_text())
        self.assertIn("V4L2", ast.get_docstring(current_tree))
        current_tree.body[0] = original_tree.body[0]  # Native-backend CLI documentation only.
        native_names = {"_legacy_guard", "_native_run", "_native_capture", "_pack_mtk10", "_native_main"}
        current_tree.body = [node for node in current_tree.body if not (
            isinstance(node, ast.FunctionDef) and node.name in native_names)]
        # Only dispatch and the exact original fd/MMIO initialization move into
        # main. Compare the entire remaining AST, including the legacy work body.
        moved = ast.parse('''
if os.path.isdir("/sys/module/mt6750_camera"):
    return _native_main(a)
global _legacy_lock, fd, spm, gpio
_legacy_lock = _legacy_guard()
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
spm, gpio = Blk(0x10006000), Blk(0x10005000)
''').body
        main = next(node for node in current_tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        index = next(i for i, node in enumerate(main.body) if ast.dump(node) == ast.dump(moved[0]))
        self.assertEqual([ast.dump(n) for n in main.body[index:index + len(moved)]],
                         [ast.dump(n) for n in moved])
        del main.body[index:index + len(moved)]
        for node in moved[-2:]:
            match = next(n for n in original_tree.body if ast.dump(n) == ast.dump(node))
            original_tree.body.remove(match)
        # The pure image developer is covered separately; every legacy hardware statement remains exact.
        for tree in (original_tree, current_tree):
            tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name == "develop")]
        self.assertEqual(ast.dump(original_tree), ast.dump(current_tree))
        source = (CAMERA / "tablet-camera").read_text()
        self.assertLess(source.index("return _native_main(a)"), source.index("_legacy_lock = _legacy_guard()"))
        self.assertLess(source.index("_legacy_lock = _legacy_guard()"), source.index('fd = os.open("/dev/mem"'))

    def test_real_flock_offline(self):
        import fcntl
        import os
        import tempfile

        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            ns = {"fcntl": fcntl, "os": SimpleNamespace(
                open=lambda path, flags, mode: os.open(Path(directory) / "lock", flags, mode),
                close=os.close, O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT,
                O_CLOEXEC=os.O_CLOEXEC, O_NOFOLLOW=os.O_NOFOLLOW,
                path=SimpleNamespace(isdir=lambda path: False))}
            exec(helper_function("_legacy_guard"), ns)
            held = ns["_legacy_guard"]()
            try:
                with self.assertRaises(BlockingIOError):
                    ns["_legacy_guard"]()
            finally:
                os.close(held)
            os.close(ns["_legacy_guard"]())


class NativePhoto(unittest.TestCase):
    """Extracted photo CLI with synthetic sysfs/frames and real offline flock."""
    def setUp(self):
        import ctypes
        import fcntl
        import glob
        import os
        import tempfile
        import time
        self.directory = tempfile.TemporaryDirectory(dir=TEST_OUTPUT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.lock = self.root / "camera.lock"
        tree = ast.parse((CAMERA / "tablet-camera").read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) or
                 isinstance(n, ast.Assign) and any(isinstance(t, ast.Tuple) for t in n.targets)]
        self.ns = dict(argparse=argparse, ctypes=ctypes, fcntl=fcntl, glob=glob, os=os,
                       subprocess=subprocess, sys=sys, time=time, __doc__="offline photo test")
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "extracted-photo-cli", "exec"), self.ns)
        self.names = [str(self.root / "video7/name"), str(self.root / "video2/name")]
        for path, name in zip(self.names, ("mt6750-rear-raw", "mt6750-front-raw")):
            Path(path).parent.mkdir()
            Path(path).write_text(name + "\n")
        self.os = SimpleNamespace(**{name: getattr(os, name) for name in (
            "close", "environ", "O_RDWR", "O_CREAT", "O_CLOEXEC", "O_NOFOLLOW")})
        def lock_open(path, flags, mode):
            self.assertEqual(path, "/run/lock/tablet-camera.lock")
            self.assertEqual(mode, 0o600)
            return os.open(self.lock, flags, mode)
        self.os.open = lock_open
        self.os.geteuid = lambda: 0
        self.os.path = SimpleNamespace(isdir=lambda path: True, basename=os.path.basename,
                                       dirname=os.path.dirname, join=os.path.join)
        self.ns["os"] = self.os
        self.ns["glob"] = SimpleNamespace(glob=lambda pattern: self.names)
        self.args = SimpleNamespace(front=False, full=False, exposure=600, gain=1.0,
                                    raw=None, output=str(self.root / "photo.png"))

    def assert_locked(self):
        import fcntl
        import os
        fd = os.open(self.lock, os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def assert_unlocked(self):
        import fcntl
        import os
        fd = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_native_dispatch_and_help_never_open_legacy(self):
        from unittest.mock import patch
        self.os.open = lambda *a: self.fail("unexpected OS open")
        self.ns["_native_main"] = lambda a: "native"
        for argv in (("tablet-camera",), ("tablet-camera", "--help")):
            with self.subTest(argv=argv), patch.object(sys, "argv", argv):
                if "--help" in argv:
                    with self.assertRaises(SystemExit) as caught:
                        self.ns["main"]()
                    self.assertEqual(caught.exception.code, 0)
                else:
                    self.assertEqual(self.ns["main"](), "native")
        # Actual script help also exercises all real top-level initialization.
        subprocess.run([sys.executable, "-B", str(CAMERA / "tablet-camera"), "--help"],
                       check=True, capture_output=True, timeout=5)

    def test_legacy_dispatch_race_refuses_kernel_owner(self):
        from unittest.mock import patch
        checks = iter((False, True))
        self.os.path.isdir = lambda path: next(checks)
        self.ns["_native_main"] = lambda a: self.fail("unexpected native dispatch")
        with patch.object(sys, "argv", ["tablet-camera"]), self.assertRaisesRegex(RuntimeError, "legacy capture excluded"):
            self.ns["main"]()
        self.assert_unlocked()

    def test_modes_gain_units_orientation_and_lock(self):
        import numpy as np
        for front, full, size, gain_units, flip in (
            (False, False, (1632, 1224), 128, True),
            (False, True, (3264, 2448), 128, True),
            (True, False, (1600, 1200), 64, False),
        ):
            with self.subTest(front=front, full=full):
                self.args.front, self.args.full = front, full
                calls = []
                def capture(device, cam, lines, gain):
                    self.assert_locked()
                    calls.append((device, cam.w, cam.h, lines, gain))
                    return np.zeros((4, 8), np.uint16)
                def develop(raw, black, path, flipped):
                    self.assert_locked()
                    self.assertEqual((black, flipped), (32 if front else 64, flip))
                self.ns.update(_native_capture=capture, develop=develop)
                self.ns["_native_main"](self.args)
                self.assertEqual(calls, [("/dev/video2" if front else "/dev/video7", *size, 600, gain_units)])
                self.assert_unlocked()

    def test_native_ae_bounded_dark_bright_and_converged(self):
        import numpy as np
        self.args.exposure = None
        for front, full in ((False, False), (False, True), (True, False)):
            self.args.front, self.args.full = front, full
            for level, count in ((0, 8), (1023, 8), (172 if front else 204, 1)):
                calls = []
                def capture(device, cam, lines, gain):
                    self.assert_locked()
                    self.assertTrue(6 <= lines <= cam.vts - 8)
                    self.assertTrue((64 if front else 128) <= gain <= (493 if front else 1984))
                    calls.append((lines, gain))
                    return np.full((4, 8), level, np.uint16)
                self.ns.update(_native_capture=capture, develop=lambda *a: self.assert_locked())
                self.ns["_native_main"](self.args)
                self.assertEqual(len(calls), count)
                if level == 0:
                    self.assertGreater(calls[-1][1], calls[0][1])
                self.assert_unlocked()

    def test_native_ae_meters_each_bayer_plane(self):
        import numpy as np
        self.args.exposure = None
        for front, full in ((False, False), (False, True), (True, False)):
            self.args.front, self.args.full = front, full
            black = 32 if front else 64
            for row, column in ((0, 0), (0, 1), (1, 0), (1, 1)):
                with self.subTest(front=front, full=full, phase=(row, column)):
                    calls = []
                    frame = np.full((4, 8), black, np.uint16)
                    frame[row::2, column::2] += 600
                    def capture(device, cam, lines, gain):
                        self.assert_locked()
                        calls.append((lines, gain))
                        return frame
                    self.ns.update(_native_capture=capture, develop=lambda *a: self.assert_locked())
                    self.ns["_native_main"](self.args)
                    self.assertEqual(len(calls), 1)  # Every cell has mean level 150.
                    self.assert_unlocked()

    def test_invalid_native_options_fail_before_capture(self):
        cases = (dict(front=True, full=True), dict(exposure=0), dict(exposure=5),
                 dict(exposure=1239), dict(full=True, exposure=2475),
                 dict(front=True, exposure=1233), dict(gain=0.9), dict(gain=15.6),
                 dict(front=True, gain=8), dict(gain=float("nan")), dict(gain=float("inf")),
                 dict(exposure=None, gain=2))
        self.ns["_native_capture"] = lambda *a: self.fail("invalid capture")
        self.os.open = lambda *a: self.fail("invalid options reached lock")
        for changes in cases:
            a = SimpleNamespace(**(vars(self.args) | changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ns["_native_main"](a)

    def test_discovery_busy_and_failures_never_fall_back(self):
        import fcntl
        import os
        self.ns["_legacy_guard"] = lambda: self.fail("legacy fallback")
        self.ns["_native_capture"] = lambda *a: self.fail("unexpected capture")
        for names in ([], [self.names[1]], [self.names[0], self.names[0]]):
            self.names = names
            with self.assertRaisesRegex(RuntimeError, "expected one"):
                self.ns["_native_main"](self.args)
            self.assert_unlocked()
        fd = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.ns["_native_main"](self.args)
        finally:
            os.close(fd)
        self.os.path.isdir = lambda path: False
        with self.assertRaisesRegex(RuntimeError, "owner disappeared"):
            self.ns["_native_main"](self.args)
        self.assert_unlocked()

    def test_capture_command_and_delivery_validation(self):
        import numpy as np
        cam = SimpleNamespace(w=8, h=4)
        pixels = np.arange(32, dtype="<u2").reshape(4, 8) * 31
        payload = pixels.tobytes()
        log = "cap dqbuf: 0 seq: 2 bytesused: 64 ts: 1.000000 (ts-monotonic, ts-src-eof)\n"
        calls = []
        def run(command):
            calls.append(command)
            Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--stream-to="))).write_bytes(payload)
            return log
        self.ns["_native_run"] = run
        np.testing.assert_array_equal(self.ns["_native_capture"]("/dev/video7", cam, 600, 128), pixels)
        command = calls[-1]
        for required in ("v4l2-ctl", "--verbose", "--stream-no-query", "--stream-mmap=3",
                         "--stream-count=1", "--stream-skip=2", "--set-fmt-video=width=8,height=4,pixelformat=BG10",
                         "--set-ctrl=test_pattern=0,exposure=600,analogue_gain=128"):
            self.assertIn(required, command)
        for payload, log in ((b"", log), (bytes(62), log), (bytes(66), log),
                             (b"\xff" * 64, log), (bytes(64), ""),
                             (bytes(64), log.replace("bytesused: 64", "bytesused: 0")),
                             (bytes(64), log.replace("ts-monotonic", "error, ts-monotonic"))):
            with self.subTest(size=len(payload), log=log), self.assertRaises((ValueError, RuntimeError)):
                self.ns["_native_capture"]("/dev/video7", cam, 600, 128)
        self.ns["_native_run"] = lambda command: (_ for _ in ()).throw(FileNotFoundError("no v4l2-ctl"))
        with self.assertRaises(FileNotFoundError):
            self.ns["_native_capture"]("/dev/video7", cam, 600, 128)

    def test_mtk_roundtrip_and_real_development(self):
        import numpy as np
        from PIL import Image
        raw = np.random.default_rng(0).integers(0, 1024, (8, 16), dtype=np.uint16)
        packed = self.ns["_pack_mtk10"](raw)
        self.assertEqual(len(packed), raw.size * 5 // 4)
        np.testing.assert_array_equal(self.ns["unpack"](packed, 16, 8), raw)
        np.testing.assert_array_equal(raw_develop.decode(packed, 16, 8, "mtk-packed10"), raw)
        self.ns["_native_capture"] = lambda *a: raw
        self.args.raw = str(self.root / "saved-mtk.raw")
        for front in (False, True):
            self.args.front = front
            for suffix in ("png", "jpg"):
                self.args.output = str(self.root / ("photo." + suffix))
                self.ns["_native_main"](self.args)
                self.assertEqual(Path(self.args.raw).read_bytes(), packed)
                with Image.open(self.args.output) as image:
                    self.assertEqual(image.size, (16, 8))
                    self.assertEqual(image.format, "PNG" if suffix == "png" else "JPEG")
                    if suffix == "png":
                        rgb = raw_develop.develop(raw, 32 if front else 64)
                        np.testing.assert_array_equal(np.asarray(image), rgb if front else rgb[::-1])
                self.assert_unlocked()
        self.ns["develop"] = lambda *a: (_ for _ in ()).throw(OSError("synthetic save failure"))
        with self.assertRaises(OSError):
            self.ns["_native_main"](self.args)
        self.assert_unlocked()
        self.ns["_native_capture"] = lambda *a: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.ns["_native_main"](self.args)
        self.assert_unlocked()

    def test_native_photo_with_owned_synthetic_v4l2_child(self):
        from unittest.mock import patch
        import numpy as np
        from PIL import Image
        fake = self.root / "synthetic-v4l2-ctl.py"
        fake.write_text('''import pathlib, sys
args = sys.argv[1:]
assert "--set-fmt-video=width=8,height=4,pixelformat=BG10" in args
assert "--set-ctrl=test_pattern=0,exposure=600,analogue_gain=128" in args
assert all(flag in args for flag in ("--stream-no-query", "--stream-mmap=3", "--stream-count=1", "--stream-skip=2"))
path = next(a.split("=", 1)[1] for a in args if a.startswith("--stream-to="))
pathlib.Path(path).write_bytes(b"".join((i * 31).to_bytes(2, "little") for i in range(32)))
print("cap dqbuf: 0 seq: 2 bytesused: 64 ts: 1.000000 (ts-monotonic, ts-src-eof)")
''')
        # Small synthetic mode only; actual sensor dimensions are checked above.
        self.ns["OV8856"] = lambda full: SimpleNamespace(w=8, h=4, vts=1246, black=64,
                                                          flip=True, name="SYNTHETIC rear")
        self.args.raw = str(self.root / "photo-mtk.raw")
        popen = subprocess.Popen
        def spawn(command, **kwargs):
            self.assert_locked()
            self.assertEqual(command[0], "v4l2-ctl")
            return popen([sys.executable, "-B", str(fake), *command[1:]], **kwargs)
        with patch.object(subprocess, "Popen", spawn):
            self.ns["_native_main"](self.args)
        with Image.open(self.args.output) as image:
            self.assertEqual((image.format, image.size), ("PNG", (8, 4)))
        pixels = np.arange(32, dtype=np.uint16).reshape(4, 8) * 31
        np.testing.assert_array_equal(self.ns["unpack"](Path(self.args.raw).read_bytes(), 8, 4), pixels)
        self.assert_unlocked()

    def test_owned_child_success_failure_timeout_and_signals(self):
        import os
        import signal
        import time
        import threading
        from unittest.mock import patch
        runner = self.ns["_native_run"]
        self.assertEqual(runner([sys.executable, "-c", "print('delivered')"]), "delivered\n")
        with self.assertRaises(subprocess.CalledProcessError):
            runner([sys.executable, "-c", "raise SystemExit(7)"])
        with self.assertRaises(FileNotFoundError):
            runner([str(self.root / "missing-executable")])
        for sig in (None, signal.SIGTERM, signal.SIGINT):
            children = []
            popen = subprocess.Popen
            def spawn(*args, **kwargs):
                child = popen(*args, **kwargs)
                children.append(child)
                return child
            handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
            timer = threading.Timer(0.4, lambda: os.kill(os.getpid(), sig)) if sig else None
            start = time.monotonic()
            try:
                if timer:
                    timer.start()
                with patch.object(subprocess, "Popen", spawn):
                    with self.assertRaises(SystemExit if sig else subprocess.TimeoutExpired) as caught:
                        runner([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"], timeout=0.4 if not sig else 5)
                if sig:
                    self.assertEqual(caught.exception.code, 128 + sig)
            finally:
                if timer:
                    timer.join()
            self.assertLess(time.monotonic() - start, 8)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0].pid, os.WNOHANG)
            self.assertEqual(handlers, {s: signal.getsignal(s) for s in handlers})


class Processing(unittest.TestCase):
    def test_measured_samples_and_precise_white_balance(self):
        import copy
        import numpy as np
        for filename in ("tablet-camera", "raw-develop.py"):
            tree = ast.parse((CAMERA / filename).read_text())
            function = copy.deepcopy(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "develop"))
            body = []
            for node in function.body:
                body.append(node)
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "mean" for t in node.targets):
                    break
            # Execute the actual demosaic/AWB-mean prefix, with no file output or device access.
            function.args = ast.parse("def stage(raw, black): pass").body[0].args
            function.body = body + [ast.parse("return rgb, mean").body[0]]
            function.name = "stage"
            namespace = {}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "processing-prefix", "exec"), namespace)
            with self.subTest(filename=filename, check="measured samples"):
                frame = np.full((8, 8), 164, np.uint16)
                frame[2, 3] = 564
                rgb, _ = namespace["stage"](frame, 64)
                self.assertEqual(rgb[2, 3, 1], 500)
                for row, column, channel in ((1, 1, 0), (0, 1, 1), (1, 0, 1), (0, 0, 2)):
                    np.testing.assert_array_equal(rgb[row::2, column::2, channel], frame[row::2, column::2] - 64)
            with self.subTest(filename=filename, check="white-balance precision"):
                frame = np.tile(np.array([[101, 201], [201, 301]], np.uint16), (512, 512))
                _, means = namespace["stage"](frame, 64)
                np.testing.assert_allclose(means, [237, 137, 37], rtol=0, atol=1e-9)

    def test_chroma_noise_luma_and_flat_fields(self):
        import copy
        import tempfile
        import numpy as np
        from PIL import Image
        function = copy.deepcopy(next(n for n in ast.parse((CAMERA / "raw-develop.py").read_text()).body
                                     if isinstance(n, ast.FunctionDef) and n.name == "develop"))
        cut = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "luma" for t in n.targets))
        function.body = function.body[:cut] + [ast.parse("return out").body[0]]
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "pre-chroma", "exec"), namespace)
        frame = np.clip(264 + np.random.default_rng(7).normal(0, 12, (64, 64)), 64, 1023).astype(np.uint16)
        frame[:, 56:] = 964
        before = namespace["develop"](frame, 64)
        after = raw_develop.develop(frame, 64).astype(np.float64) / 255
        area = np.s_[4:-4, 4:48]
        weights = np.array([0.299, 0.587, 0.114])
        self.assertLess(float((after[area][..., 0] - after[area][..., 1]).std()),
                        float((before[area][..., 0] - before[area][..., 1]).std()) * 0.8)
        np.testing.assert_allclose(after[area] @ weights, before[area] @ weights, rtol=0, atol=0.51 / 255)
        exec(helper_function("develop"), namespace)
        for black in (32, 64):
            for flip in (False, True):
                with self.subTest(black=black, flip=flip), tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
                    path = Path(directory) / "developed.png"
                    namespace["develop"](frame, black, str(path), flip)
                    expected = raw_develop.develop(frame, black)
                    np.testing.assert_array_equal(np.asarray(Image.open(path)), expected[::-1] if flip else expected)
            for level in (0, black, 164, 1023):
                flat = raw_develop.develop(np.full((8, 8), level, np.uint16), black)
                np.testing.assert_array_equal(flat, np.broadcast_to(flat[0, 0], flat.shape))

    def test_both_encodings_and_legacy_equivalence(self):
        import numpy as np
        pixels = np.arange(32, dtype=np.uint16) * 31
        packed = b"".join(sum(int(pixels[i + j]) << (10 * j) for j in range(4)).to_bytes(5, "little")
                          for i in range(0, 32, 4))
        raw = raw_develop.decode(packed, 8, 4, "mtk-packed10")
        ns = {}
        exec(helper_function("unpack"), ns)
        np.testing.assert_array_equal(raw, ns["unpack"](packed, 8, 4))
        np.testing.assert_array_equal(raw_develop.decode(pixels.astype("<u2").tobytes(), 8, 4, "sbggr10"), raw)
        self.assertEqual(raw_develop.develop(raw, 32).shape, (4, 8, 3))
        self.assertEqual(raw_develop.develop(np.zeros((4, 8), dtype=np.uint16), 64).max(), 0)

    def test_bad_frame_bounds_and_encoding(self):
        for args in ((b"", 8, 4, "sbggr10"), (bytes(64), 0, 4, "sbggr10"),
                     (bytes(64), 6, 4, "sbggr10"), (bytes(64), 8, 3, "sbggr10"),
                     (b"\xff" * 64, 8, 4, "sbggr10"), (bytes(40), 8, 4, "unknown")):
            with self.subTest(args=args[1:]), self.assertRaises(ValueError):
                raw_develop.decode(*args)


def main():
    global TEST_OUTPUT, LEGACY_HELPER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="owned evidence directory")
    parser.add_argument("--kernel-source", type=Path, required=True, help="matching Linux source for the ioctl/control-lock regression")
    parser.add_argument("--legacy-helper", type=Path, required=True, help="preserved unguarded tablet-camera snapshot for the AST regression")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    TEST_OUTPUT = args.output
    LEGACY_HELPER = args.legacy_helper
    source = (HERE / "mt6750-camera.c").read_text()
    ioctl = (args.kernel_source / "drivers/media/v4l2-core/v4l2-ioctl.c").read_text()
    controls = (args.kernel_source / "drivers/media/v4l2-core/v4l2-ctrls-api.c").read_text()
    registration = c_function(source, "register_path")
    control_init = registration[registration.index("\tv4l2_ctrl_handler_init("):registration.index("\tc->exposure =")]
    # Actual wrapper/query bodies retain their locks. Lookup, queue and control
    # effects are explicit host fixtures, not a running V4L2/vb2 integration.
    locking = "static void init_control_lock(struct camera *c) {\n" + control_init + "}\n"
    locking += "\n".join(c_function(source, name) for name in ("current_mode", "fill_format", "try_fmt", "set_fmt", "control_set", "pad_fmt"))
    locking += c_function(ioctl, "v4l2_ioctl_get_lock") + c_function(controls, "v4l2_query_ext_ctrl") + c_function(ioctl, "__video_do_ioctl")
    for name in ("control_set", "capture_worker", "start_streaming", "stop_streaming", "camera_pm_event"):
        body = c_function(source, name)
        assert "mutex_lock(&c->lock)" not in body and "mutex_lock(c->controls.lock)" not in body, name
    (args.output / "extracted-locking.h").write_text(locking)
    (args.output / "extracted-cleanup.h").write_text(c_function(source, "power_down"))
    (args.output / "extracted-sensor-controls.h").write_text(c_function(source, "sensor_controls"))
    (args.output / "extracted-lifecycle.h").write_text(c_function(source, "queue_setup") + c_function(source, "start_streaming") + c_function(source, "stop_streaming") + c_function(source, "camera_pm_event"))
    (args.output / "extracted-pins.h").write_text("\n".join(c_function(source, name)
        for name in ("mode_reg", "mode_shift", "pin_mode", "pin_out")))
    c_results = []
    for filename in ("test-capture.c", "test-cleanup.c", "test-lifecycle.c", "test-pins.c", "test-locking.c"):
        executable = args.output / Path(filename).stem
        command = ["cc", "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(HERE),
                   "-I", str(args.output), str(HERE / filename), "-o", str(executable)]
        if filename == "test-locking.c":
            command.extend(["-Wno-unused-parameter", "-pthread"])
        subprocess.run(command, check=True)
        result = subprocess.run([str(executable)], text=True, capture_output=True, check=True, timeout=30)
        print(result.stdout, end="")
        c_results.append({"source": filename, "command": command, "stdout": result.stdout})
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # Actual executable CLI, on a labelled synthetic file only.
    synthetic = args.output / "synthetic-sbggr10.raw"
    synthetic.write_bytes(b"".join((i * 31).to_bytes(2, "little") for i in range(32)))
    png = args.output / "synthetic-developed.png"
    subprocess.run([sys.executable, "-B", str(CAMERA / "raw-develop.py"), str(synthetic), str(png),
                    "--width", "8", "--height", "4", "--front"], check=True)
    if png.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("no valid software PNG output")
    receipt = {"scope": "offline synthetic mocks only; no hardware or vb2-core runtime",
               "c_tests": c_results, "python_tests_run": result.testsRun,
               "errors": len(result.errors), "failures": len(result.failures),
               "driver_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "data": {name: hashlib.sha256((CAMERA / "data" / name).read_bytes()).hexdigest()
                        for name in tables.TABLES}}
    (args.output / "tests.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
