#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Offline only: validate the preserved still tables, emit a C header."""
import argparse
import hashlib
import re
from pathlib import Path

TABLES = {
    "ov8856-init.txt": (True, 0xFFFF),
    "ov8856-preview.txt": (True, 0xFFFF),
    "ov8856-full.txt": (True, 0xFFFF),
    "gc2365-init.txt": (True, 0xFF),
    "gc2365-stream.txt": (True, 0xFF),
    "rear-seninf.txt": (False, 0xFFC),
    "front-seninf.txt": (False, 0xFFC),
    "rear-csi0.txt": (False, 0xFC),
    "front-csi1.txt": (False, 0xFC),
}


def parse_table(text, sensor, last_reg):
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"line {number}: expected two fields")
        reg, value = parts
        if reg == "delay":
            if not sensor or not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 3000:
                raise ValueError(f"line {number}: invalid delay")
            result.append((0xFFFF, int(value)))
            continue
        if not all(re.fullmatch(r"[0-9a-fA-F]+", item) for item in parts):
            raise ValueError(f"line {number}: invalid hex register/value")
        reg, value = int(reg, 16), int(value, 16)
        if not 0 <= reg <= last_reg or (not sensor and reg % 4) or (sensor and reg == 0xFFFF):
            raise ValueError(f"line {number}: register outside table bounds")
        if not 0 <= value <= (255 if sensor else 0xFFFFFFFF):
            raise ValueError(f"line {number}: value outside table bounds")
        result.append((reg, value))
    if not result:
        raise ValueError("empty table")
    return result


def generate(data):
    out = ["/* SPDX-License-Identifier: GPL-2.0 */",
           "/* Generated offline by generate_tables.py from preserved data; do not edit. */",
           "#ifndef MT6750_BOARD_TABLES_H", "#define MT6750_BOARD_TABLES_H",
           "struct board_reg { u16 reg; u32 value; };",
           "struct board_mode { u32 width, height, vts; };",
           "static const struct board_mode board_modes[] = {",
           "\t{ 1632, 1224, 1246 }, { 3264, 2448, 2482 }, { 1600, 1200, 1240 },", "};"]
    for filename, (sensor, last_reg) in TABLES.items():
        payload = (data / filename).read_bytes()
        rows = parse_table(payload.decode("ascii"), sensor, last_reg)
        name = Path(filename).stem.replace("-", "_")
        out.extend([f"/* {filename}: sha256 {hashlib.sha256(payload).hexdigest()} */",
                    f"static const struct board_reg {name}[] = {{"])
        out.extend(f"\t{{ 0x{reg:04x}, 0x{value:08x} }}," for reg, value in rows)
        out.append("};")
    out.extend(["#endif", ""])
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(generate(args.data))


if __name__ == "__main__":
    main()
