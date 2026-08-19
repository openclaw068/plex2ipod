"""Generate Plex2iPod.ico and Plex2iPod_logo.png from scratch — pure stdlib.

The logo is a rounded-square gradient (pink → deep magenta) with a white
iPod-style click wheel in the centre and a pink play-triangle inside it.
Run once: ``python make_icon.py``
"""

import math
import struct
import zlib


# ---------------------------------------------------------------------------
# Pixel buffer helpers
# ---------------------------------------------------------------------------

def make_buffer(w, h):
    return [[(0, 0, 0, 0)] * w for _ in range(h)]


def lerp(a, b, t):
    return int(a + (b - a) * t)


def blend(dst, src):
    """Alpha-blend src (RGBA) over dst (RGBA)."""
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    a = sa / 255.0
    out_a = sa + da * (1 - a)
    if out_a == 0:
        return (0, 0, 0, 0)
    out_r = (sr * a + dr * (da / 255.0) * (1 - a)) / (out_a / 255.0)
    out_g = (sg * a + dg * (da / 255.0) * (1 - a)) / (out_a / 255.0)
    out_b = (sb * a + db * (da / 255.0) * (1 - a)) / (out_a / 255.0)
    return (int(out_r), int(out_g), int(out_b), int(out_a))


def put(px, x, y, color):
    h = len(px)
    w = len(px[0])
    if 0 <= x < w and 0 <= y < h:
        px[y][x] = blend(px[y][x], color)


# ---------------------------------------------------------------------------
# Drawing primitives (anti-aliased via supersample-on-edge)
# ---------------------------------------------------------------------------

def draw_rounded_rect_gradient(px, x0, y0, x1, y1, radius, top_color, bot_color):
    w = x1 - x0
    h = y1 - y0
    for y in range(y0, y1):
        t = (y - y0) / max(h - 1, 1)
        r = lerp(top_color[0], bot_color[0], t)
        g = lerp(top_color[1], bot_color[1], t)
        b = lerp(top_color[2], bot_color[2], t)
        for x in range(x0, x1):
            # rounded corner test
            cx = x - x0
            cy = y - y0
            inside = True
            if cx < radius and cy < radius:
                d = math.hypot(radius - 0.5 - cx, radius - 0.5 - cy)
                if d > radius:
                    inside = False
                elif d > radius - 1.2:
                    a = max(0, min(1, radius - d)) * 255
                    put(px, x, y, (r, g, b, int(a)))
                    continue
            elif cx >= w - radius and cy < radius:
                d = math.hypot(cx - (w - radius - 0.5), radius - 0.5 - cy)
                if d > radius:
                    inside = False
                elif d > radius - 1.2:
                    a = max(0, min(1, radius - d)) * 255
                    put(px, x, y, (r, g, b, int(a)))
                    continue
            elif cx < radius and cy >= h - radius:
                d = math.hypot(radius - 0.5 - cx, cy - (h - radius - 0.5))
                if d > radius:
                    inside = False
                elif d > radius - 1.2:
                    a = max(0, min(1, radius - d)) * 255
                    put(px, x, y, (r, g, b, int(a)))
                    continue
            elif cx >= w - radius and cy >= h - radius:
                d = math.hypot(cx - (w - radius - 0.5), cy - (h - radius - 0.5))
                if d > radius:
                    inside = False
                elif d > radius - 1.2:
                    a = max(0, min(1, radius - d)) * 255
                    put(px, x, y, (r, g, b, int(a)))
                    continue
            if inside:
                put(px, x, y, (r, g, b, 255))


def draw_ring(px, cx, cy, outer_r, inner_r, color):
    """Anti-aliased ring."""
    for y in range(int(cy - outer_r - 1), int(cy + outer_r + 2)):
        for x in range(int(cx - outer_r - 1), int(cx + outer_r + 2)):
            d = math.hypot(x - cx, y - cy)
            if d <= inner_r - 0.5:
                continue
            if d >= outer_r + 0.5:
                continue
            # outer edge AA
            outer_a = 1.0
            if d > outer_r - 0.5:
                outer_a = max(0, min(1, outer_r + 0.5 - d))
            inner_a = 1.0
            if d < inner_r + 0.5:
                inner_a = max(0, min(1, d - (inner_r - 0.5)))
            a = min(outer_a, inner_a)
            if a > 0:
                r, g, b, base_a = color
                put(px, x, y, (r, g, b, int(base_a * a)))


def draw_triangle(px, cx, cy, size, color):
    """Right-pointing isoceles triangle (play button) centred at cx,cy."""
    # vertices
    half = size
    v1 = (cx - half * 0.55, cy - half)
    v2 = (cx - half * 0.55, cy + half)
    v3 = (cx + half * 0.85, cy)
    min_x = int(min(v1[0], v2[0], v3[0]) - 1)
    max_x = int(max(v1[0], v2[0], v3[0]) + 2)
    min_y = int(min(v1[1], v2[1], v3[1]) - 1)
    max_y = int(max(v1[1], v2[1], v3[1]) + 2)

    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    for y in range(min_y, max_y):
        for x in range(min_x, max_x):
            # 4x supersample for smoother edges
            hits = 0
            for sx in (0.25, 0.75):
                for sy in (0.25, 0.75):
                    p = (x + sx, y + sy)
                    d1 = sign(p, v1, v2)
                    d2 = sign(p, v2, v3)
                    d3 = sign(p, v3, v1)
                    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                    if not (has_neg and has_pos):
                        hits += 1
            if hits:
                r, g, b, a = color
                put(px, x, y, (r, g, b, int(a * hits / 4)))


def draw_drop_shadow(px, x0, y0, x1, y1, radius, blur, opacity):
    """Soft shadow under the rounded rect."""
    for y in range(y0 - blur, y1 + blur):
        for x in range(x0 - blur, x1 + blur):
            # distance to nearest point of rounded rect
            cx = max(x0 + radius, min(x, x1 - radius - 1))
            cy = max(y0 + radius, min(y, y1 - radius - 1))
            # for points within the inset rect, distance is 0; outside corners use radius
            inset_x = max(x0, min(x, x1 - 1))
            inset_y = max(y0, min(y, y1 - 1))
            d = math.hypot(x - inset_x, y - inset_y)
            if d <= 0:
                continue
            if d > blur:
                continue
            a = (1 - d / blur) ** 2 * opacity
            put(px, x, y + 4, (0, 0, 0, int(a * 255)))


# ---------------------------------------------------------------------------
# PNG writer (stdlib only)
# ---------------------------------------------------------------------------

def write_png(pixels, w, h):
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter: none
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# ICO writer — wraps PNGs at multiple sizes
# ---------------------------------------------------------------------------

def write_ico(images):
    """images: list of (width, height, png_bytes)."""
    count = len(images)
    out = bytearray()
    out += struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    for w, h, data in images:
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        out += struct.pack(
            "<BBBBHHII", bw, bh, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    for _, _, data in images:
        out += data
    return bytes(out)


# ---------------------------------------------------------------------------
# Compose the logo
# ---------------------------------------------------------------------------

def render_logo(size):
    px = make_buffer(size, size)

    # padding for shadow
    pad = max(int(size * 0.06), 2)
    radius = int(size * 0.22)

    # gradient colors — same accent as the app
    top = (255, 92, 128)     # bright pink
    bot = (74, 14, 78)       # deep purple

    # subtle shadow
    if size >= 64:
        draw_drop_shadow(
            px, pad, pad, size - pad, size - pad,
            radius, blur=pad, opacity=0.35,
        )

    # rounded gradient body
    draw_rounded_rect_gradient(
        px, pad, pad, size - pad, size - pad, radius, top, bot
    )

    # iPod click wheel — white ring
    cx = size / 2
    cy = size / 2
    outer = size * 0.32
    inner = size * 0.13
    draw_ring(px, cx, cy, outer, inner, (255, 255, 255, 255))

    # subtle inner shadow on the ring (a darker ring just inside the outer edge)
    if size >= 64:
        draw_ring(px, cx, cy, outer, outer - max(size * 0.012, 1),
                  (0, 0, 0, 35))

    # play triangle in centre — bright accent pink
    tri_size = size * 0.10
    draw_triangle(px, cx + size * 0.005, cy, tri_size, (233, 69, 96, 255))

    return px


def main():
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))

    # Big logo PNG
    print("Rendering 256x256 logo...")
    big = render_logo(256)
    big_png = write_png(big, 256, 256)
    with open(os.path.join(out_dir, "Plex2iPod_logo.png"), "wb") as f:
        f.write(big_png)
    print("  -> Plex2iPod_logo.png")

    # ICO with multiple sizes for crisp rendering
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        print(f"Rendering {s}x{s}...")
        buf = render_logo(s)
        png = write_png(buf, s, s)
        images.append((s, s, png))

    ico = write_ico(images)
    with open(os.path.join(out_dir, "Plex2iPod.ico"), "wb") as f:
        f.write(ico)
    print("  -> Plex2iPod.ico")
    print("Done.")


if __name__ == "__main__":
    main()
