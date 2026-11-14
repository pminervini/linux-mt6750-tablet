#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Offline software development of a saved BGGR frame; never opens a camera.

Accepts the V4L2 candidate's SBGGR10 (16-bit LE words) or the preserved legacy
MediaTek LE bitstream. Bilinear demosaic, grey-world AWB, gamma, and mild
chroma smoothing run in software. Hardware ISP, exposure/colour calibration,
and lens shading remain unavailable.
"""
import argparse
from pathlib import Path


def decode(payload, width, height, packing):
    import numpy as np

    if width <= 0 or height <= 0 or width % 4 or height % 2:
        raise ValueError("expected positive width divisible by 4 and even height")
    expected = width * height * 2 if packing == "sbggr10" else width * height * 5 // 4
    if len(payload) != expected:
        raise ValueError(f"frame size mismatch: expected {int(expected)}, got {len(payload)}")
    if packing == "sbggr10":
        raw = np.frombuffer(payload, dtype="<u2").reshape(height, width)
        if np.any(raw > 1023):
            raise ValueError("SBGGR10 has nonzero bits 15:10")
        return raw
    if packing != "mtk-packed10":
        raise ValueError("unknown packing")
    b = np.frombuffer(payload, dtype=np.uint8).reshape(height, width // 4, 5).astype(np.uint16)
    raw = np.empty((height, width // 4, 4), dtype=np.uint16)
    raw[..., 0] = b[..., 0] | ((b[..., 1] & 3) << 8)
    raw[..., 1] = (b[..., 1] >> 2) | ((b[..., 2] & 15) << 6)
    raw[..., 2] = (b[..., 2] >> 4) | ((b[..., 3] & 63) << 4)
    raw[..., 3] = (b[..., 3] >> 6) | (b[..., 4] << 2)
    return raw.reshape(height, width)


def develop(raw, black):
    import numpy as np

    x = np.maximum(raw.astype(np.float32) - black, 0)
    height, width = x.shape
    rgb = np.zeros((height, width, 3), dtype=np.float32)
    weights = np.zeros_like(rgb)
    for row, column, channel in ((1, 1, 0), (0, 1, 1), (1, 0, 1), (0, 0, 2)):
        rgb[row::2, column::2, channel] = x[row::2, column::2]
        weights[row::2, column::2, channel] = 1

    def box(image):
        padded = np.pad(image, ((1, 1), (1, 1), (0, 0)), mode="edge")
        return sum(padded[y:y + height, z:z + width] for y in range(3) for z in range(3))

    rgb = np.where(weights, rgb, box(rgb) / np.maximum(box(weights), 1e-6))
    mean = rgb.reshape(-1, 3).mean(axis=0, dtype=np.float64)
    rgb *= mean[1] / np.maximum(mean, 1e-3)
    rgb /= max(float(np.percentile(rgb, 99.7)), 1.0)
    out = np.clip(rgb, 0, 1) ** (1 / 2.2)
    del rgb, weights, x  # Release full-resolution intermediates before chroma smoothing.
    luma = np.sum(out * np.array([0.299, 0.587, 0.114], np.float32), axis=2, keepdims=True)
    out -= luma
    out = luma + box(out) / 9  # Mild chroma-only smoothing; retain display luma.
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--format", choices=("sbggr10", "mtk-packed10"), default="sbggr10")
    parser.add_argument("--front", action="store_true", help="black level 32 rather than rear 64")
    parser.add_argument("--flip-vertical", action="store_true", help="legacy rear display orientation, after demosaic")
    args = parser.parse_args()
    from PIL import Image

    rgb = develop(decode(args.input.read_bytes(), args.width, args.height, args.format), 32 if args.front else 64)
    Image.fromarray(rgb[::-1] if args.flip_vertical else rgb).save(args.output)


if __name__ == "__main__":
    main()
