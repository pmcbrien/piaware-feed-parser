#!/usr/bin/env python3
"""
Draw Strip Bay's app icon and pack it into StripBay.icns.

Two flight strips in a holder: buff for 1090 MHz over pale blue for 978 UAT,
the same stock colours the interface uses. Run this to change the icon:

    python3 make_icon.py            # writes packaging/StripBay.icns

Needs Pillow. Only used at build time -- the app itself has no dependencies.
"""

import os
import struct
import sys

from PIL import Image, ImageDraw

SUPERSAMPLE = 4
CANVAS = 1024

BAY_TOP = (44, 48, 55)
BAY_BOTTOM = (20, 22, 26)
PAPER_1090 = (230, 220, 195)
PAPER_978 = (198, 214, 221)
INK = (30, 27, 22)
INK_SOFT = (110, 104, 96)
MARKER = (184, 50, 26)

# icns type -> pixel size. PNG payloads are valid for all of these.
VARIANTS = [
    ("icp4", 16), ("icp5", 32), ("ic11", 32), ("ic12", 64),
    ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512),
    ("ic09", 512), ("ic10", 1024),
]


def squircle(size, radius_ratio=0.225, samples=720):
    """
    macOS icons sit on a superellipse, not a rounded rectangle. Approximating
    it properly is the difference between looking native and looking pasted on.
    """
    import math
    n = 5.0
    half = size / 2.0
    inset = size * 0.085
    r = half - inset
    points = []
    for index in range(samples):
        theta = 2 * math.pi * index / samples
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        x = half + r * math.copysign(abs(cos_t) ** (2.0 / n), cos_t)
        y = half + r * math.copysign(abs(sin_t) ** (2.0 / n), sin_t)
        points.append((x, y))
    return points


def vertical_gradient(size, top, bottom):
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / float(size - 1)
        gradient.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return gradient.resize((size, size), Image.NEAREST)


def draw_strip(size, width, height, paper, rules, marker=False):
    """One paper strip, drawn flat then rotated by the caller."""
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(strip)
    pen.rectangle([0, 0, width - 1, height - 1], fill=paper + (255,))

    unit = size / 1024.0
    # Vertical field rules, as printed on a real progress strip.
    for fraction in rules:
        x = int(width * fraction)
        pen.line([(x, 0), (x, height)], fill=INK_SOFT + (150,),
                 width=max(1, int(3 * unit)))

    # Two pencilled entries in the first field.
    pad = int(width * 0.045)
    bar_h = max(2, int(26 * unit))
    pen.rectangle([pad, int(height * 0.26), pad + int(width * 0.20),
                   int(height * 0.26) + bar_h], fill=INK + (255,))
    pen.rectangle([pad, int(height * 0.56), pad + int(width * 0.13),
                   int(height * 0.56) + bar_h], fill=INK_SOFT + (255,))

    if marker:
        x = int(width * rules[-1]) + int(width * 0.035)
        pen.rectangle([x, int(height * 0.30), x + int(width * 0.11),
                       int(height * 0.30) + int(bar_h * 1.5)],
                      fill=MARKER + (255,))
    return strip


def render(size):
    scale = size * SUPERSAMPLE
    image = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))

    # Body of the icon, clipped to the squircle.
    mask = Image.new("L", (scale, scale), 0)
    ImageDraw.Draw(mask).polygon(squircle(scale), fill=255)
    body = vertical_gradient(scale, BAY_TOP, BAY_BOTTOM).convert("RGBA")
    image.paste(body, (0, 0), mask)

    strip_w = int(scale * 0.74)
    strip_h = int(scale * 0.225)

    back = draw_strip(scale, strip_w, strip_h, PAPER_978, [0.30, 0.52, 0.72])
    back = back.rotate(5.5, resample=Image.BICUBIC, expand=True)
    image.alpha_composite(back, (int(scale * 0.125), int(scale * 0.235)))

    front = draw_strip(scale, strip_w, strip_h, PAPER_1090,
                       [0.30, 0.52, 0.72], marker=True)
    front = front.rotate(-3.5, resample=Image.BICUBIC, expand=True)
    image.alpha_composite(front, (int(scale * 0.135), int(scale * 0.495)))

    # Re-clip so nothing overhangs the squircle edge.
    clipped = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    clipped.paste(image, (0, 0), mask)

    return clipped.resize((size, size), Image.LANCZOS)


def pack_icns(images, destination):
    chunks = []
    for kind, size in VARIANTS:
        import io
        buffer = io.BytesIO()
        images[size].save(buffer, format="PNG", optimize=True)
        payload = buffer.getvalue()
        chunks.append(kind.encode("ascii")
                      + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    with open(destination, "wb") as handle:
        handle.write(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return len(body) + 8


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "packaging")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    sizes = sorted(set(size for _, size in VARIANTS))
    images = {}
    for size in sizes:
        images[size] = render(size)
        sys.stderr.write("rendered {0}x{0}\n".format(size))

    icns = os.path.join(out_dir, "StripBay.icns")
    total = pack_icns(images, icns)
    images[1024].save(os.path.join(out_dir, "icon-preview.png"))
    sys.stderr.write("wrote {} ({:.0f} KB)\n".format(icns, total / 1024.0))


if __name__ == "__main__":
    main()
