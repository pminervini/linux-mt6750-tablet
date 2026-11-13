#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""SYNTHETIC/OFFLINE tests only. Never import or execute tablet-camera.

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
        current_tree.body = [node for node in current_tree.body if not (
            isinstance(node, ast.FunctionDef) and node.name == "_legacy_guard" or
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_legacy_lock" for t in node.targets))]
        self.assertEqual(ast.dump(original_tree), ast.dump(current_tree))
        source = (CAMERA / "tablet-camera").read_text()
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


class Processing(unittest.TestCase):
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
