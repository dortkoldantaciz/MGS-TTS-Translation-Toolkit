from __future__ import annotations

import struct
import zlib
from pathlib import Path


class PngError(ValueError):
    pass


def _read_u32_be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def make_png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _scale_sample(value: int, bit_depth: int) -> int:
    if bit_depth == 8:
        return value
    if bit_depth == 16:
        return value >> 8
    return (value * 255) // ((1 << bit_depth) - 1)


def _packed_sample(row: bytes | bytearray, index: int, bit_depth: int) -> int:
    if bit_depth == 8:
        return row[index]
    if bit_depth == 16:
        base = index * 2
        return (row[base] << 8) | row[base + 1]
    per_byte = 8 // bit_depth
    byte = row[index // per_byte]
    shift = 8 - bit_depth - (index % per_byte) * bit_depth
    return (byte >> shift) & ((1 << bit_depth) - 1)


def png_rgba_from_pixels(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    if len(pixels) != width * height:
        raise PngError("PNG pixel count does not match dimensions.")
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for r, g, b, a in pixels[y * width:(y + 1) * width]:
            scanlines.extend((r, g, b, a))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + make_png_chunk(b"IHDR", ihdr) + make_png_chunk(b"IDAT", zlib.compress(bytes(scanlines))) + make_png_chunk(b"IEND", b"")


def png_rgba_from_payload(payload: bytes, width: int = 256) -> bytes:
    packed = struct.pack(">I", len(payload)) + payload
    pixel_count = (len(packed) + 3) // 4
    height = max(1, (pixel_count + width - 1) // width)
    rgba = bytearray(width * height * 4)
    rgba[:len(packed)] = packed
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        start = y * width * 4
        scanlines.extend(rgba[start:start + width * 4])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + make_png_chunk(b"IHDR", ihdr) + make_png_chunk(b"IDAT", zlib.compress(bytes(scanlines))) + make_png_chunk(b"IEND", b"")


def read_png_pixels(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PngError(f"Not a PNG file: {path}")
    pos = 8
    width = height = color_type = bit_depth = None
    compressed = bytearray()
    palette: list[tuple[int, int, int, int]] = []
    while pos + 8 <= len(data):
        length = _read_u32_be(data, pos)
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", payload)
            if compression != 0 or filter_method != 0 or interlace != 0:
                raise PngError(f"Unsupported PNG compression/filter/interlace settings: {path}")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"PLTE":
            palette = [(payload[i], payload[i + 1], payload[i + 2], 255) for i in range(0, len(payload) - 2, 3)]
        elif kind == b"tRNS" and palette:
            palette = [(r, g, b, payload[i] if i < len(payload) else a) for i, (r, g, b, a) in enumerate(palette)]
        elif kind == b"IEND":
            break
    valid_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if width is None or height is None or color_type not in valid_depths or bit_depth not in valid_depths[color_type]:
        raise PngError(f"Unsupported PNG format for import: {path}")
    if color_type == 3 and not palette:
        raise PngError(f"Indexed PNG has no palette: {path}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = channels * bit_depth
    stride = (width * bits_per_pixel + 7) // 8
    raw = zlib.decompress(bytes(compressed))
    rows: list[bytearray] = []
    cursor = 0
    for _ in range(height):
        if cursor >= len(raw):
            raise PngError(f"PNG pixel data is truncated: {path}")
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if len(row) != stride:
            raise PngError(f"PNG scanline is truncated: {path}")
        prev = rows[-1] if rows else bytearray(stride)
        bpp = max(1, (bits_per_pixel + 7) // 8)
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            if filter_type == 1:
                row[i] = (row[i] + left) & 0xFF
            elif filter_type == 2:
                row[i] = (row[i] + up) & 0xFF
            elif filter_type == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                row[i] = (row[i] + predictor) & 0xFF
            elif filter_type != 0:
                raise PngError(f"Unsupported PNG filter {filter_type}: {path}")
        rows.append(row)
    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for x in range(width):
            if color_type == 0:
                gray = _scale_sample(_packed_sample(row, x, bit_depth), bit_depth)
                pixels.append((gray, gray, gray, 255))
            elif color_type == 2:
                if bit_depth == 16:
                    base = x * 6
                    pixels.append((row[base], row[base + 2], row[base + 4], 255))
                else:
                    base = x * 3
                    pixels.append((row[base], row[base + 1], row[base + 2], 255))
            elif color_type == 3:
                index = _packed_sample(row, x, bit_depth)
                pixels.append(palette[index] if index < len(palette) else (0, 0, 0, 0))
            elif color_type == 4:
                if bit_depth == 16:
                    base = x * 4
                    gray = row[base]
                    alpha = row[base + 2]
                else:
                    base = x * 2
                    gray = row[base]
                    alpha = row[base + 1]
                pixels.append((gray, gray, gray, alpha))
            else:
                if bit_depth == 16:
                    base = x * 8
                    pixels.append((row[base], row[base + 2], row[base + 4], row[base + 6]))
                else:
                    base = x * 4
                    pixels.append((row[base], row[base + 1], row[base + 2], row[base + 3]))
    return width, height, pixels


def payload_from_png_rgba(path: Path) -> bytes:
    _width, _height, pixels = read_png_pixels(path)
    if not pixels:
        raise PngError(f"Unsupported PNG format for import: {path}")
    rgba = bytearray()
    for r, g, b, a in pixels:
        rgba.extend((r, g, b, a))
    if len(rgba) < 4:
        raise PngError(f"PNG payload is truncated: {path}")
    payload_length = _read_u32_be(rgba, 0)
    if payload_length > len(rgba) - 4:
        raise PngError(f"PNG payload length is invalid: {path}")
    return bytes(rgba[4:4 + payload_length])
