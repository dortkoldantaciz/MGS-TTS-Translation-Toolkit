from __future__ import annotations

import struct
from dataclasses import dataclass


TPL_MAGIC = 0x0020AF30

FMT_I4 = 0
FMT_I8 = 1
FMT_IA4 = 2
FMT_IA8 = 3
FMT_RGB565 = 4
FMT_RGB5A3 = 5
FMT_RGBA32 = 6
FMT_C4 = 8
FMT_C8 = 9
FMT_C14X2 = 10
FMT_CMPR = 14

PALETTE_IA8 = 0
PALETTE_RGB565 = 1
PALETTE_RGB5A3 = 2


FORMAT_NAMES = {
    FMT_I4: "I4",
    FMT_I8: "I8",
    FMT_IA4: "IA4",
    FMT_IA8: "IA8",
    FMT_RGB565: "RGB565",
    FMT_RGB5A3: "RGB5A3",
    FMT_RGBA32: "RGBA32",
    FMT_C4: "C4",
    FMT_C8: "C8",
    FMT_C14X2: "C14X2",
    FMT_CMPR: "CMPR",
}


@dataclass
class TplImage:
    index: int
    width: int
    height: int
    fmt: int
    data_offset: int
    data_size: int
    header_offset: int
    palette_offset: int
    palette_format: int | None = None
    palette_entries: int = 0
    palette_data_offset: int = 0

    @property
    def format_name(self) -> str:
        return FORMAT_NAMES.get(self.fmt, f"FMT_{self.fmt}")


class TplError(ValueError):
    pass


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _rgb565(value: int) -> tuple[int, int, int, int]:
    r = ((value >> 11) & 0x1F) * 255 // 31
    g = ((value >> 5) & 0x3F) * 255 // 63
    b = (value & 0x1F) * 255 // 31
    return r, g, b, 255


def _rgb5a3(value: int) -> tuple[int, int, int, int]:
    if value & 0x8000:
        r = ((value >> 10) & 0x1F) * 255 // 31
        g = ((value >> 5) & 0x1F) * 255 // 31
        b = (value & 0x1F) * 255 // 31
        return r, g, b, 255
    a = ((value >> 12) & 0x07) * 255 // 7
    r = ((value >> 8) & 0x0F) * 255 // 15
    g = ((value >> 4) & 0x0F) * 255 // 15
    b = (value & 0x0F) * 255 // 15
    return r, g, b, a


def _ia8(value: int) -> tuple[int, int, int, int]:
    intensity = value & 0xFF
    alpha = (value >> 8) & 0xFF
    return intensity, intensity, intensity, alpha


def _encode_rgb565(pixel: tuple[int, int, int, int]) -> int:
    r, g, b, _a = pixel
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def _encode_rgb5a3(pixel: tuple[int, int, int, int]) -> int:
    r, g, b, a = pixel
    if a >= 224:
        return 0x8000 | ((r * 31 // 255) << 10) | ((g * 31 // 255) << 5) | (b * 31 // 255)
    return ((a * 7 // 255) << 12) | ((r * 15 // 255) << 8) | ((g * 15 // 255) << 4) | (b * 15 // 255)


def _color_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    return sum((a[i] - b[i]) * (a[i] - b[i]) for i in range(3))


def _cmpr_block(block: list[tuple[int, int, int, int]]) -> bytes:
    opaque = [pixel for pixel in block if pixel[3] >= 128]
    use_alpha = len(opaque) != len(block)
    if not opaque:
        return b"\0\0\0\0" + b"\xFF\xFF\xFF\xFF"

    c_min = c_max = opaque[0]
    best_distance = -1
    for left in opaque:
        for right in opaque:
            distance = _color_distance(left, right)
            if distance > best_distance:
                best_distance = distance
                c_min, c_max = left, right

    c0 = _encode_rgb565(c_max)
    c1 = _encode_rgb565(c_min)
    if use_alpha:
        if c0 > c1:
            c0, c1 = c1, c0
    elif c0 <= c1:
        c0, c1 = c1, c0
        if c0 <= c1:
            c0 = min(0xFFFF, c1 + 1)

    p0 = _rgb565(c0)
    p1 = _rgb565(c1)
    if c0 > c1:
        table = [
            p0,
            p1,
            tuple((2 * p0[i] + p1[i]) // 3 for i in range(3)) + (255,),
            tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3)) + (255,),
        ]
        candidate_count = 4
    else:
        table = [
            p0,
            p1,
            tuple((p0[i] + p1[i]) // 2 for i in range(3)) + (255,),
            (0, 0, 0, 0),
        ]
        candidate_count = 3

    bits = 0
    for index, pixel in enumerate(block):
        if use_alpha and pixel[3] < 128:
            best = 3
        else:
            best = min(range(candidate_count), key=lambda candidate: _color_distance(pixel, table[candidate]))
        bits |= best << (30 - 2 * index)
    return struct.pack(">HHI", c0, c1, bits)


def _blocks(width: int, height: int, block_w: int, block_h: int):
    for by in range(0, height, block_h):
        for bx in range(0, width, block_w):
            yield bx, by


def _block_count(width: int, height: int, block_w: int, block_h: int) -> int:
    return ((width + block_w - 1) // block_w) * ((height + block_h - 1) // block_h)


def encoded_size(width: int, height: int, fmt: int) -> int:
    if fmt in {FMT_I4, FMT_C4}:
        return _block_count(width, height, 8, 8) * 32
    if fmt in {FMT_I8, FMT_IA4, FMT_C8}:
        return _block_count(width, height, 8, 4) * 32
    if fmt in {FMT_IA8, FMT_RGB565, FMT_RGB5A3, FMT_C14X2}:
        return _block_count(width, height, 4, 4) * 32
    if fmt == FMT_RGBA32:
        return _block_count(width, height, 4, 4) * 64
    if fmt == FMT_CMPR:
        return _block_count(width, height, 8, 8) * 32
    raise TplError(f"Unsupported TPL format {fmt}.")


def parse_tpl(data: bytes) -> list[TplImage]:
    if len(data) < 12 or _read_u32(data, 0) != TPL_MAGIC:
        raise TplError("Not a GameCube TPL file.")
    count = _read_u32(data, 4)
    table_offset = _read_u32(data, 8)
    if count <= 0 or table_offset + count * 8 > len(data):
        raise TplError("TPL image table is truncated.")
    rows: list[tuple[int, int]] = []
    data_offsets: list[int] = []
    for index in range(count):
        image_header, palette_header = struct.unpack_from(">II", data, table_offset + index * 8)
        if image_header <= 0 or image_header + 0x24 > len(data):
            raise TplError(f"TPL image header {index} is invalid.")
        rows.append((image_header, palette_header))
        data_offsets.append(_read_u32(data, image_header + 8))
        if palette_header:
            data_offsets.append(_read_u32(data, palette_header + 8))
    sorted_offsets = sorted(offset for offset in data_offsets if 0 < offset < len(data))
    images: list[TplImage] = []
    for index, (image_header, palette_header) in enumerate(rows):
        height = _read_u16(data, image_header)
        width = _read_u16(data, image_header + 2)
        fmt = _read_u32(data, image_header + 4)
        data_offset = _read_u32(data, image_header + 8)
        later_offsets = [value for value in sorted_offsets if value > data_offset]
        max_data_size = (later_offsets[0] if later_offsets else len(data)) - data_offset
        try:
            data_size = min(encoded_size(width, height, fmt), max_data_size)
        except TplError:
            data_size = max_data_size
        palette_format = None
        palette_entries = 0
        palette_data_offset = 0
        if palette_header:
            palette_entries = _read_u16(data, palette_header)
            palette_format = _read_u32(data, palette_header + 4)
            palette_data_offset = _read_u32(data, palette_header + 8)
        images.append(TplImage(index, width, height, fmt, data_offset, data_size, image_header, palette_header, palette_format, palette_entries, palette_data_offset))
    return images


def tpl_size(data: bytes) -> int:
    images = parse_tpl(data)
    end = 12
    if images:
        end = max(end, max(image.header_offset + 0x24 for image in images))
    for image in images:
        end = max(end, image.data_offset + image.data_size)
        if image.palette_offset:
            end = max(end, image.palette_offset + 0x0C, image.palette_data_offset + image.palette_entries * 2)
    return end


def _palette(data: bytes, image: TplImage) -> list[tuple[int, int, int, int]]:
    if not image.palette_offset or image.palette_format is None:
        return []
    out = []
    for index in range(image.palette_entries):
        value = _read_u16(data, image.palette_data_offset + index * 2)
        if image.palette_format == PALETTE_IA8:
            out.append(_ia8(value))
        elif image.palette_format == PALETTE_RGB565:
            out.append(_rgb565(value))
        elif image.palette_format == PALETTE_RGB5A3:
            out.append(_rgb5a3(value))
        else:
            out.append((0, 0, 0, 0))
    return out


def _nearest_palette_index(pixel: tuple[int, int, int, int], palette: list[tuple[int, int, int, int]], limit: int) -> int:
    if not palette:
        return 0
    capped = palette[:min(limit, len(palette))]
    return min(
        range(len(capped)),
        key=lambda index: _color_distance(pixel, capped[index]) + ((pixel[3] - capped[index][3]) * (pixel[3] - capped[index][3])),
    )


def decode_image(data: bytes, image: TplImage) -> list[tuple[int, int, int, int]]:
    src = data[image.data_offset:image.data_offset + image.data_size]
    pixels = [(0, 0, 0, 0)] * (image.width * image.height)

    def put(x: int, y: int, pixel: tuple[int, int, int, int]) -> None:
        if x < image.width and y < image.height:
            pixels[y * image.width + x] = pixel

    if image.fmt == FMT_I4:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 8, 8):
            for y in range(8):
                for xpair in range(4):
                    value = src[cursor] if cursor < len(src) else 0
                    cursor += 1
                    for x, nibble in enumerate((value >> 4, value & 0x0F)):
                        gray = nibble * 17
                        put(bx + xpair * 2 + x, by + y, (gray, gray, gray, 255))
    elif image.fmt == FMT_I8:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 8, 4):
            for y in range(4):
                for x in range(8):
                    gray = src[cursor] if cursor < len(src) else 0
                    cursor += 1
                    put(bx + x, by + y, (gray, gray, gray, 255))
    elif image.fmt == FMT_IA4:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 8, 4):
            for y in range(4):
                for x in range(8):
                    value = src[cursor] if cursor < len(src) else 0
                    cursor += 1
                    alpha = (value >> 4) * 17
                    gray = (value & 0x0F) * 17
                    put(bx + x, by + y, (gray, gray, gray, alpha))
    elif image.fmt == FMT_IA8:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 4, 4):
            for y in range(4):
                for x in range(4):
                    value = _read_u16(src, cursor) if cursor + 2 <= len(src) else 0
                    cursor += 2
                    put(bx + x, by + y, _ia8(value))
    elif image.fmt == FMT_RGB565:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 4, 4):
            for y in range(4):
                for x in range(4):
                    value = _read_u16(src, cursor) if cursor + 2 <= len(src) else 0
                    cursor += 2
                    put(bx + x, by + y, _rgb565(value))
    elif image.fmt == FMT_RGB5A3:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 4, 4):
            for y in range(4):
                for x in range(4):
                    value = _read_u16(src, cursor) if cursor + 2 <= len(src) else 0
                    cursor += 2
                    put(bx + x, by + y, _rgb5a3(value))
    elif image.fmt == FMT_RGBA32:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 4, 4):
            ar = src[cursor:cursor + 32]
            gb = src[cursor + 32:cursor + 64]
            cursor += 64
            for y in range(4):
                for x in range(4):
                    i = y * 4 + x
                    a = ar[i * 2] if i * 2 < len(ar) else 0
                    r = ar[i * 2 + 1] if i * 2 + 1 < len(ar) else 0
                    g = gb[i * 2] if i * 2 < len(gb) else 0
                    b = gb[i * 2 + 1] if i * 2 + 1 < len(gb) else 0
                    put(bx + x, by + y, (r, g, b, a))
    elif image.fmt in {FMT_C4, FMT_C8, FMT_C14X2}:
        pal = _palette(data, image)
        cursor = 0
        if image.fmt == FMT_C4:
            for bx, by in _blocks(image.width, image.height, 8, 8):
                for y in range(8):
                    for xpair in range(4):
                        value = src[cursor] if cursor < len(src) else 0
                        cursor += 1
                        for x, idx in ((0, value >> 4), (1, value & 0x0F)):
                            put(bx + xpair * 2 + x, by + y, pal[idx] if idx < len(pal) else (0, 0, 0, 0))
        elif image.fmt == FMT_C8:
            for bx, by in _blocks(image.width, image.height, 8, 4):
                for y in range(4):
                    for x in range(8):
                        idx = src[cursor] if cursor < len(src) else 0
                        cursor += 1
                        put(bx + x, by + y, pal[idx] if idx < len(pal) else (0, 0, 0, 0))
        else:
            for bx, by in _blocks(image.width, image.height, 4, 4):
                for y in range(4):
                    for x in range(4):
                        idx = (_read_u16(src, cursor) & 0x3FFF) if cursor + 2 <= len(src) else 0
                        cursor += 2
                        put(bx + x, by + y, pal[idx] if idx < len(pal) else (0, 0, 0, 0))
    elif image.fmt == FMT_CMPR:
        cursor = 0
        for bx, by in _blocks(image.width, image.height, 8, 8):
            for sub in range(4):
                sx = bx + (sub & 1) * 4
                sy = by + (sub >> 1) * 4
                c0 = _read_u16(src, cursor) if cursor + 2 <= len(src) else 0
                c1 = _read_u16(src, cursor + 2) if cursor + 4 <= len(src) else 0
                bits = _read_u32(src, cursor + 4) if cursor + 8 <= len(src) else 0
                cursor += 8
                p0 = _rgb565(c0)
                p1 = _rgb565(c1)
                if c0 > c1:
                    table = [
                        p0,
                        p1,
                        tuple((2 * p0[i] + p1[i]) // 3 for i in range(3)) + (255,),
                        tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3)) + (255,),
                    ]
                else:
                    table = [
                        p0,
                        p1,
                        tuple((p0[i] + p1[i]) // 2 for i in range(3)) + (255,),
                        (0, 0, 0, 0),
                    ]
                for y in range(4):
                    for x in range(4):
                        shift = 30 - 2 * (y * 4 + x)
                        put(sx + x, sy + y, table[(bits >> shift) & 3])
    else:
        raise TplError(f"Unsupported TPL format {image.fmt}.")
    return pixels


def encode_image(width: int, height: int, fmt: int, pixels: list[tuple[int, int, int, int]], palette: list[tuple[int, int, int, int]] | None = None) -> bytes:
    def get(x: int, y: int) -> tuple[int, int, int, int]:
        if x < width and y < height:
            return pixels[y * width + x]
        return (0, 0, 0, 0)

    out = bytearray()
    if fmt == FMT_RGBA32:
        for bx, by in _blocks(width, height, 4, 4):
            ar = bytearray()
            gb = bytearray()
            for y in range(4):
                for x in range(4):
                    r, g, b, a = get(bx + x, by + y)
                    ar.extend((a, r))
                    gb.extend((g, b))
            out.extend(ar)
            out.extend(gb)
    elif fmt == FMT_RGB565:
        for bx, by in _blocks(width, height, 4, 4):
            for y in range(4):
                for x in range(4):
                    out.extend(struct.pack(">H", _encode_rgb565(get(bx + x, by + y))))
    elif fmt == FMT_RGB5A3:
        for bx, by in _blocks(width, height, 4, 4):
            for y in range(4):
                for x in range(4):
                    out.extend(struct.pack(">H", _encode_rgb5a3(get(bx + x, by + y))))
    elif fmt == FMT_I8:
        for bx, by in _blocks(width, height, 8, 4):
            for y in range(4):
                for x in range(8):
                    r, g, b, _a = get(bx + x, by + y)
                    out.append((r + g + b) // 3)
    elif fmt == FMT_IA8:
        for bx, by in _blocks(width, height, 4, 4):
            for y in range(4):
                for x in range(4):
                    r, g, b, a = get(bx + x, by + y)
                    out.extend(struct.pack(">H", (a << 8) | ((r + g + b) // 3)))
    elif fmt == FMT_IA4:
        for bx, by in _blocks(width, height, 8, 4):
            for y in range(4):
                for x in range(8):
                    r, g, b, a = get(bx + x, by + y)
                    out.append(((a * 15 // 255) << 4) | (((r + g + b) // 3) * 15 // 255))
    elif fmt == FMT_I4:
        for bx, by in _blocks(width, height, 8, 8):
            for y in range(8):
                for xpair in range(4):
                    vals = []
                    for x in range(2):
                        r, g, b, _a = get(bx + xpair * 2 + x, by + y)
                        vals.append(((r + g + b) // 3) * 15 // 255)
                    out.append((vals[0] << 4) | vals[1])
    elif fmt == FMT_C4:
        pal = palette or []
        for bx, by in _blocks(width, height, 8, 8):
            for y in range(8):
                for xpair in range(4):
                    left = _nearest_palette_index(get(bx + xpair * 2, by + y), pal, 16)
                    right = _nearest_palette_index(get(bx + xpair * 2 + 1, by + y), pal, 16)
                    out.append((left << 4) | right)
    elif fmt == FMT_C8:
        pal = palette or []
        for bx, by in _blocks(width, height, 8, 4):
            for y in range(4):
                for x in range(8):
                    out.append(_nearest_palette_index(get(bx + x, by + y), pal, 256))
    elif fmt == FMT_C14X2:
        pal = palette or []
        for bx, by in _blocks(width, height, 4, 4):
            for y in range(4):
                for x in range(4):
                    out.extend(struct.pack(">H", _nearest_palette_index(get(bx + x, by + y), pal, 0x4000)))
    elif fmt == FMT_CMPR:
        for bx, by in _blocks(width, height, 8, 8):
            for sub in range(4):
                sx = bx + (sub & 1) * 4
                sy = by + (sub >> 1) * 4
                block = [get(sx + x, sy + y) for y in range(4) for x in range(4)]
                out.extend(_cmpr_block(block))
    else:
        raise TplError(f"TPL encode is not supported for {FORMAT_NAMES.get(fmt, fmt)}.")
    return bytes(out)


def replace_images(data: bytes, replacements: dict[int, list[tuple[int, int, int, int]]]) -> bytes:
    images = parse_tpl(data)
    out = bytearray(data)
    for image in images:
        pixels = replacements.get(image.index)
        if pixels is None:
            continue
        if len(pixels) != image.width * image.height:
            raise TplError(f"Replacement image {image.index} has wrong dimensions.")
        encoded = encode_image(image.width, image.height, image.fmt, pixels, _palette(data, image))
        if len(encoded) != image.data_size:
            raise TplError(f"Replacement image {image.index} changed encoded size.")
        out[image.data_offset:image.data_offset + image.data_size] = encoded
    return bytes(out)
