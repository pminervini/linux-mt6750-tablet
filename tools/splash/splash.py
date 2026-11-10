#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot splash tooling for the MT6750 tablet (runs on the build machine; needs Pillow).

Orientation: everything takes images in the *view* orientation, landscape 1280x800, the way the
tablet is held with the rotated console (fbcon=rotate:3: console top at the panel's left edge).
The panel itself is portrait 800x1280; the conversions are done here.

  splash.py cyber OUTDIR                  generate the default splash: OUTDIR/logo.png (static,
                                          for lk) and OUTDIR/frames/*.png (loop for tablet-splash)
  splash.py logo-unpack LOGO.BIN OUTDIR   every image of an MTK logo partition as PNG
  splash.py logo-pack LOGO.BIN IMAGE OUT  replace the boot logo (images 0, 38, 39 by default)
  splash.py anim-pack SRC OUT.splash      animation for tablet-splash; SRC = GIF/APNG, a single
                                          image, or a directory of PNG frames (sorted by name)
  splash.py lk-patch LK.BIN OUT           remove lk's "Orange State" screen and its 5 s delay
  splash.py fb2png RAW OUT.png            panel dump (800x1280 BGRA, e.g. install.sh capture)
                                          -> PNG in view orientation

Images that are not 1280x800 are scaled to fit (letterboxed on black).
"""
import argparse, glob, os, re, struct, subprocess, sys, zlib

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageSequence

VIEW = (1280, 800)              # landscape, as held
PANEL = (800, 1280)             # portrait framebuffer
# lk's logo blit (vendor build: rotation forced to 90) puts the stored image's top row on the
# panel's right edge; the view has its top on the panel's left edge -> store rotated by 180.
LK_ROTATE = 180
LOGO_MAGIC = 0x58881688
BOOT_LOGO_IDX = (0, 38, 39)     # the MediaTek boot logo and its two copies in this logo.bin


# ---------------------------------------------------------------- orientation helpers
def fit(im, size=VIEW, bg=(0, 0, 0)):
    im = im.convert("RGB")
    if im.size == size:
        return im
    s = min(size[0] / im.width, size[1] / im.height)
    r = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    out = Image.new("RGB", size, bg)
    out.paste(r, ((size[0] - r.width) // 2, (size[1] - r.height) // 2))
    return out


def view_to_panel(im):
    """view (1280x800) -> panel framebuffer (800x1280): view top on the panel's left edge."""
    return im.transpose(Image.ROTATE_90)


def panel_to_view(im):
    return im.transpose(Image.ROTATE_270)


def bgra(im):
    return im.convert("RGBA").tobytes("raw", "BGRA")


# ---------------------------------------------------------------- MTK logo partition
def logo_read(path):
    d = open(path, "rb").read()
    magic, size = struct.unpack("<II", d[:8])
    if magic != LOGO_MAGIC or d[8:12] != b"logo":
        sys.exit("%s: not an MTK logo partition (magic %08x)" % (path, magic))
    b = d[512:512 + size]
    n, total = struct.unpack("<II", b[:8])
    offs = struct.unpack("<%dI" % n, b[8:8 + 4 * n])
    if total != size:
        sys.exit("%s: table size %d != header size %d" % (path, total, size))
    blobs = [b[offs[i]:offs[i + 1] if i + 1 < n else total] for i in range(n)]
    return d[:512], blobs


def logo_write(hdr, blobs, out, part_size=None):
    n = len(blobs)
    first = 8 + 4 * n
    offs, o = [], first
    for z in blobs:
        offs.append(o)
        o += len(z)
    payload = struct.pack("<II", n, o) + struct.pack("<%dI" % n, *offs) + b"".join(blobs)
    hdr = bytearray(hdr)
    hdr[4:8] = struct.pack("<I", len(payload))
    data = bytes(hdr) + payload
    if part_size and len(data) > part_size:
        sys.exit("logo image %d bytes > partition %d" % (len(data), part_size))
    open(out, "wb").write(data)
    # self-check: parse back and inflate everything
    _, chk = logo_read(out)
    assert [zlib.decompress(z) for z in chk] == [zlib.decompress(z) for z in blobs]
    return len(data)


def logo_image(raw):
    """decoded logo entry -> PIL image (stored orientation), or None for the small sprites."""
    if len(raw) == VIEW[0] * VIEW[1] * 4:
        return Image.frombytes("RGBA", VIEW, raw, "raw", "BGRA").convert("RGB")
    return None


def cmd_logo_unpack(a):
    _, blobs = logo_read(a.logo)
    os.makedirs(a.outdir, exist_ok=True)
    for i, z in enumerate(blobs):
        raw = zlib.decompress(z)
        im = logo_image(raw)
        if im is not None:      # full-screen entries: shown as lk draws them, in view orientation
            im.rotate(LK_ROTATE).save(os.path.join(a.outdir, "%02d.png" % i))
        else:
            open(os.path.join(a.outdir, "%02d.raw" % i), "wb").write(raw)
        print("%2d %7d bytes %s" % (i, len(raw), "1280x800" if im else "sprite (raw)"))


def cmd_logo_pack(a):
    hdr, blobs = logo_read(a.logo)
    im = fit(Image.open(a.image)).rotate(LK_ROTATE)
    z = zlib.compress(bgra(im), 9)
    for i in (int(x) for x in a.index.split(",")):
        if logo_image(zlib.decompress(blobs[i])) is None:
            sys.exit("logo entry %d is not a full-screen image" % i)
        blobs[i] = z
    n = logo_write(hdr, blobs, a.out, a.part_size)
    print("%s: %d bytes, boot logo entries %s replaced" % (a.out, n, a.index))


# ---------------------------------------------------------------- tablet-splash animation format
# "TSPLASH1" | u16 panel_w, panel_h | u16 nframes | u16 loop_from | frames...
# frame: u16 delay_ms | u16 nrects | rects: u16 x, y, w, h | u32 zlen | zlib(BGRA rows, w*4 each)
# Rects are in panel (framebuffer) coordinates; frame 0 is full screen, later frames carry only
# the regions that changed.
def changed_rects(prev, cur, step=40):
    """regions of cur (panel orientation) that differ from prev, as horizontal bands of the
    bounding box of each changed band of rows (keeps rects tight for separate small changes)."""
    diff = ImageChops.difference(prev, cur).convert("L").point(lambda v: 255 if v else 0)
    w, h = cur.size
    rects = []
    for y0 in range(0, h, step):
        bb = diff.crop((0, y0, w, min(h, y0 + step))).getbbox()
        if bb:
            r = [bb[0], y0 + bb[1], bb[2], y0 + bb[3]]
            if rects and rects[-1][3] == r[1] and abs(rects[-1][0] - r[0]) < 64 and abs(rects[-1][2] - r[2]) < 64:
                rects[-1] = [min(rects[-1][0], r[0]), rects[-1][1], max(rects[-1][2], r[2]), r[3]]
            else:
                rects.append(r)
    return rects


def anim_pack(frames_view, delays, out, loop_from=0):
    panels = [view_to_panel(fit(f)) for f in frames_view]
    blob = bytearray(b"TSPLASH1" + struct.pack("<HHHH", PANEL[0], PANEL[1], len(panels), loop_from))
    for i, p in enumerate(panels):
        if i == 0:
            rects = [[0, 0, PANEL[0], PANEL[1]]]
        else:
            rects = changed_rects(panels[i - 1], p)
        blob += struct.pack("<HH", delays[i], len(rects))
        for x0, y0, x1, y1 in rects:
            z = zlib.compress(bgra(p.crop((x0, y0, x1, y1))), 6)
            blob += struct.pack("<HHHHI", x0, y0, x1 - x0, y1 - y0, len(z)) + z
    # wrap-around: changes from the last frame back to frame loop_from, as one extra frame
    if len(panels) > 1:
        rects = changed_rects(panels[-1], panels[loop_from])
        blob += struct.pack("<HH", 0, len(rects))
        for x0, y0, x1, y1 in rects:
            z = zlib.compress(bgra(panels[loop_from].crop((x0, y0, x1, y1))), 6)
            blob += struct.pack("<HHHHI", x0, y0, x1 - x0, y1 - y0, len(z)) + z
    open(out, "wb").write(blob)
    return len(blob)


def cmd_anim_pack(a):
    src = a.src
    if os.path.isdir(src):
        files = sorted(glob.glob(os.path.join(src, "*.png")))
        frames = [Image.open(f).convert("RGB") for f in files]
        delays = [round(1000 / a.fps)] * len(frames)
    else:
        im = Image.open(src)
        frames, delays = [], []
        for f in ImageSequence.Iterator(im):
            frames.append(f.convert("RGB"))
            delays.append(int(f.info.get("duration") or round(1000 / a.fps)))
    if not frames:
        sys.exit("no frames in %s" % src)
    n = anim_pack(frames, delays, a.out)
    print("%s: %d frames, %d bytes" % (a.out, len(frames), n))


# ---------------------------------------------------------------- lk patch
def thumb_pic_refs(code, base, target):
    """Thumb PIC string references: `ldr rX, [pc, #imm]` ... `add rX, pc` -> target."""
    refs = []
    for i in range(0, len(code) - 4, 2):
        hw = code[i] | code[i + 1] << 8
        if hw & 0xf800 != 0x4800:           # ldr rX, [pc, #imm8*4]
            continue
        rx, imm = (hw >> 8) & 7, (hw & 0xff) * 4
        la = ((base + i + 4) & ~3) + imm
        if not base <= la < base + len(code) - 4:
            continue
        lit = struct.unpack_from("<I", code, la - base)[0]
        for j in range(i + 2, min(i + 12, len(code) - 2), 2):
            if (code[j] | code[j + 1] << 8) == 0x4478 | rx:     # add rX, pc
                if (lit + base + j + 4) & 0xffffffff == target:
                    refs.append(base + j)
                break
    return refs


def cmd_lk_patch(a):
    d = bytearray(open(a.lk, "rb").read())
    magic, size = struct.unpack("<II", d[:8])
    if magic != LOGO_MAGIC or d[8:10] != b"lk":
        sys.exit("%s: not an MTK lk partition" % a.lk)
    code = bytes(d[512:512 + size])
    # load address: the reset vector's literal pool holds it (word 0x46000000 at offset 0x74 here)
    base = a.base
    s = code.find(b"Orange State\n")
    if s < 0:
        sys.exit("no 'Orange State' string: not this lk family")
    refs = thumb_pic_refs(code, base, base + s)
    if len(refs) != 1:
        sys.exit("expected one reference to the Orange State text, found %d" % len(refs))
    # walk back to the function entry: push {r3, lr} (0xb508)
    fn = None
    for o in range(refs[0] - base, max(0, refs[0] - base - 0x40), -2):
        if code[o:o + 2] == b"\x08\xb5":
            fn = o
            break
    if fn is None:
        sys.exit("function entry not found before the reference")
    body = code[fn:fn + 0x50]
    if b"\x41\xf2\x88\x30" not in body:          # movw r0, #5000 (the 5 s delay)
        sys.exit("function at %#x does not look like the orange-state warning" % (base + fn))
    if code[fn:fn + 4] == b"\x00\x20\x70\x47":
        print("already patched at %#x" % (base + fn))
    else:
        d[512 + fn:512 + fn + 4] = b"\x00\x20\x70\x47"      # movs r0, #0; bx lr
        print("patched orange-state warning at %#x (file offset %#x): return 0 at entry"
              % (base + fn, 512 + fn))
    open(a.out, "wb").write(d)


# ---------------------------------------------------------------- default cyberpunk splash
def font(names, size):
    for n in names:
        try:
            p = subprocess.run(["fc-match", "-f", "%{file}", n], capture_output=True, text=True).stdout
            if p and os.path.exists(p):
                return ImageFont.truetype(p, size)
        except FileNotFoundError:
            break
    return ImageFont.load_default(size)


BG, CYAN, MAGENTA, VIOLET = (5, 6, 12), (5, 217, 232), (255, 42, 109), (60, 20, 110)


def cyber_frame(phase=0.0, cursor=True, glitch=0):
    W, H = VIEW
    hz = 520                                   # horizon
    im = Image.new("RGB", VIEW, BG)
    d = ImageDraw.Draw(im)
    for y in range(hz, H):                     # floor: dark violet glow rising from the horizon
        t = 1 - (y - hz) / (H - hz)
        d.line([(0, y), (W, y)], fill=tuple(int(BG[k] + (VIOLET[k] - BG[k]) * 0.35 * t ** 3) for k in range(3)))

    grid = Image.new("RGB", VIEW, (0, 0, 0))
    g = ImageDraw.Draw(grid)
    vx = W / 2
    for k in range(-16, 17):                   # rays to the vanishing point
        x_bot = vx + k * 190
        g.line([(vx + k * 12, hz), (x_bot, H + 40)], fill=MAGENTA, width=2)
    z0, dz = 1.0, 0.55
    for n in range(0, 40):                     # cross lines, scrolling toward the viewer
        z = z0 + (n + 1 - phase) * dz
        y = hz + (H - hz) * z0 / z
        if y <= hz + 1 or y > H:
            continue
        g.line([(0, y), (W, y)], fill=MAGENTA, width=2)
    # fade toward the horizon
    fade = Image.new("L", VIEW, 0)
    fd = ImageDraw.Draw(fade)
    for y in range(hz, H):
        fd.line([(0, y), (W, y)], fill=int(255 * min(1, ((y - hz) / (H - hz)) ** 1.7 * 1.6)))
    grid = Image.composite(grid, Image.new("RGB", VIEW, (0, 0, 0)), fade)
    glow = grid.filter(ImageFilter.GaussianBlur(6))
    im = ImageChops.add(im, ImageChops.add(grid.point(lambda v: v * 0.7), glow))

    d = ImageDraw.Draw(im)
    d.line([(0, hz), (W, hz)], fill=CYAN, width=2)  # horizon line + glow
    hl = Image.new("RGB", VIEW, (0, 0, 0))
    ImageDraw.Draw(hl).line([(0, hz), (W, hz)], fill=CYAN, width=6)
    im = ImageChops.add(im, hl.filter(ImageFilter.GaussianBlur(10)))

    # wordmark with chromatic aberration
    big = font(["Noto Sans Mono:style=Black", "DejaVu Sans Mono:style=Bold", "monospace:bold"], 168)
    txt = "MT6750"
    tw = big.getbbox(txt)[2]
    tx, ty = (W - tw) // 2, 200
    word = Image.new("RGB", VIEW, (0, 0, 0))
    wd = ImageDraw.Draw(word)
    wd.text((tx - 5, ty), txt, font=big, fill=CYAN)
    wd.text((tx + 5, ty), txt, font=big, fill=MAGENTA)
    wd.text((tx, ty), txt, font=big, fill=(235, 240, 255))
    # glitch: shift a few horizontal slices of the wordmark
    slices = [(334, 338, 9)] if glitch == 0 else \
             [(236, 252, 26), (286, 296, -34), (334, 342, 14)]
    for y0, y1, dx in slices:
        band = word.crop((0, y0, W, y1))
        word.paste((0, 0, 0), (0, y0, W, y1))
        word.paste(band, (dx, y0))
    im = ImageChops.add(im, ImageChops.add(word, word.filter(ImageFilter.GaussianBlur(10)).point(lambda v: v // 3)))

    d = ImageDraw.Draw(im)
    mono = font(["Noto Sans Mono:style=Regular", "DejaVu Sans Mono", "monospace"], 34)
    prompt = "> mainline linux"
    pw = mono.getbbox(prompt)[2]
    px, py = (W - pw - 24) // 2, 410
    d.text((px, py), prompt, font=mono, fill=CYAN)
    if cursor:
        d.rectangle([px + pw + 8, py + 6, px + pw + 26, py + 42], fill=CYAN)

    # HUD corner brackets + one small status line
    small = font(["Noto Sans Mono:style=Regular", "DejaVu Sans Mono", "monospace"], 18)
    c, L, m = (40, 110, 120), 34, 28
    for (x, y, sx, sy) in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        d.line([(x, y), (x + sx * L, y)], fill=c, width=2)
        d.line([(x, y), (x, y + sy * L)], fill=c, width=2)
    d.text((m + 6, m + 10), "SYS://BOOT", font=small, fill=c)
    d.text((W - m - 6 - small.getbbox("ARM64 // 8C")[2], m + 10), "ARM64 // 8C", font=small, fill=c)

    # scanlines
    sl = Image.new("L", VIEW, 255)
    sd = ImageDraw.Draw(sl)
    for y in range(0, H, 3):
        sd.line([(0, y), (W, y)], fill=190)
    return Image.composite(im, Image.new("RGB", VIEW, (0, 0, 0)), sl)


def cmd_cyber(a):
    os.makedirs(os.path.join(a.outdir, "frames"), exist_ok=True)
    cyber_frame().save(os.path.join(a.outdir, "logo.png"))
    n = a.frames
    for i in range(n):
        f = cyber_frame(phase=i / n, cursor=(i // (n // 2)) == 0, glitch=1 if i in (n // 3,) else 0)
        f.save(os.path.join(a.outdir, "frames", "%03d.png" % i))
    print("%s/logo.png + %d frames" % (a.outdir, n))


def cmd_fb2png(a):
    raw = open(a.raw, "rb").read()[:PANEL[0] * PANEL[1] * 4]
    panel_to_view(Image.frombytes("RGBA", PANEL, raw, "raw", "BGRA").convert("RGB")).save(a.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("cyber"); p.add_argument("outdir"); p.add_argument("--frames", type=int, default=24)
    p.set_defaults(fn=cmd_cyber)
    p = sp.add_parser("logo-unpack"); p.add_argument("logo"); p.add_argument("outdir")
    p.set_defaults(fn=cmd_logo_unpack)
    p = sp.add_parser("logo-pack"); p.add_argument("logo"); p.add_argument("image"); p.add_argument("out")
    p.add_argument("--index", default=",".join(map(str, BOOT_LOGO_IDX)))
    p.add_argument("--part-size", type=int, default=8 << 20)
    p.set_defaults(fn=cmd_logo_pack)
    p = sp.add_parser("anim-pack"); p.add_argument("src"); p.add_argument("out")
    p.add_argument("--fps", type=float, default=20)
    p.set_defaults(fn=cmd_anim_pack)
    p = sp.add_parser("lk-patch"); p.add_argument("lk"); p.add_argument("out")
    p.add_argument("--base", type=lambda v: int(v, 0), default=0x46000000)
    p.set_defaults(fn=cmd_lk_patch)
    p = sp.add_parser("fb2png"); p.add_argument("raw"); p.add_argument("out")
    p.set_defaults(fn=cmd_fb2png)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
