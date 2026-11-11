#!/usr/bin/python3
"""raw2png.py IN.raw W H OUT.png [PATTERN]: MediaTek packed RAW10 (LSB-first bitstream, 4 px in
5 bytes) -> half-resolution RGB PNG with gray-world white balance and sRGB-ish gamma.
PATTERN = rggb | grbg | gbrg | bggr (default bggr)."""
import sys
import numpy as np
from PIL import Image

f, W, H, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
pat = sys.argv[5] if len(sys.argv) > 5 else "bggr"
bpl = W * 10 // 8
b = np.frombuffer(open(f, "rb").read()[:bpl * H], np.uint8).reshape(H, bpl // 5, 5).astype(np.uint16)
px = np.empty((H, bpl // 5, 4), np.uint16)
px[..., 0] = b[..., 0] | ((b[..., 1] & 0x03) << 8)
px[..., 1] = (b[..., 1] >> 2) | ((b[..., 2] & 0x0f) << 6)
px[..., 2] = (b[..., 2] >> 4) | ((b[..., 3] & 0x3f) << 4)
px[..., 3] = (b[..., 3] >> 6) | (b[..., 4] << 2)
raw = px.reshape(H, W).astype(np.float32)
pos = {c: [] for c in "rgb"}
for i, c in enumerate(pat):
    pos[c].append(raw[i // 2::2, i % 2::2])
r, g, bl = pos["r"][0], (pos["g"][0] + pos["g"][1]) / 2, pos["b"][0]
blk = 64.0                                              # OV8856 black level (10-bit)
rgb = np.stack([r, g, bl], -1) - blk
rgb = np.clip(rgb, 0, None)
rgb *= rgb[..., 1].mean() / np.maximum(rgb.reshape(-1, 3).mean(0), 1e-3)   # gray world
rgb /= max(np.percentile(rgb, 99.5), 1)
img = (np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
Image.fromarray(img).save(out)
print(out, img.shape, "raw min/mean/max", int(raw.min()), int(raw.mean()), int(raw.max()))
