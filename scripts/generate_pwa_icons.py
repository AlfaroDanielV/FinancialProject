#!/usr/bin/env python3
"""Phase 6e B13 — PNG generator for the Centro Financiero PWA icons.

Stdlib-only. Writes three PNGs:
    web/public/icons/icon-192.png        (192x192, standard)
    web/public/icons/icon-512.png        (512x512, standard)
    web/public/icons/icon-512-maskable.png (512x512, maskable safe zone)

Placeholder design — solid navy background + centered white disc + inner
navy circle (a coin-ish shape). Maskable variant keeps content inside the
inner 80% so the OS mask doesn't clip the disc.

Replace with real brand assets in a later polish pass.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG_RGB = (30, 58, 138)        # Tailwind blue-900 (#1e3a8a)
FG_RGB = (255, 255, 255)      # white
INNER_RGB = (37, 99, 235)     # Tailwind blue-600 (#2563eb)


def chunk(chunk_type: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return length + chunk_type + data + struct.pack(">I", crc)


def write_png(path: Path, width: int, pixels: bytes) -> None:
    """`pixels` is `width*width*3` bytes of RGB data."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0)
    )
    raw = bytearray()
    row_bytes = width * 3
    for y in range(width):
        raw.append(0)  # filter: None
        raw.extend(pixels[y * row_bytes : (y + 1) * row_bytes])
    idat = chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
    iend = chunk(b"IEND", b"")
    path.write_bytes(signature + ihdr + idat + iend)


def render_icon(size: int, *, maskable: bool) -> bytes:
    """Compose RGB pixels for the icon.

    Standard: white disc at 78% of canvas, inner navy circle at 30%.
    Maskable: 80% safe zone, so the disc is at 60% of canvas (everything
    inside the inner 80% diameter).
    """
    pixels = bytearray(size * size * 3)
    center = size / 2
    outer_radius = size * (0.30 if maskable else 0.39)
    inner_radius = size * (0.12 if maskable else 0.15)
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            distance = (dx * dx + dy * dy) ** 0.5
            if distance <= inner_radius:
                rgb = INNER_RGB
            elif distance <= outer_radius:
                rgb = FG_RGB
            else:
                rgb = BG_RGB
            offset = (y * size + x) * 3
            pixels[offset : offset + 3] = bytes(rgb)
    return bytes(pixels)


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "web" / "public" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_png(out_dir / "icon-192.png", 192, render_icon(192, maskable=False))
    write_png(out_dir / "icon-512.png", 512, render_icon(512, maskable=False))
    write_png(
        out_dir / "icon-512-maskable.png",
        512,
        render_icon(512, maskable=True),
    )
    print(f"wrote 3 icons to {out_dir}")


if __name__ == "__main__":
    main()
