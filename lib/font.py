from __future__ import annotations

from pathlib import Path

from png import png_rgba_from_pixels, read_png_pixels


class FontCodecError(ValueError):
    pass


def png_from_font_raw(raw: bytes) -> bytes:
    cell_w = 24
    cell_h = 24
    columns = 20
    count = len(raw) // (6 * 24)
    rows = max(1, ((count + columns - 1) // columns))
    width = columns * cell_w
    height = rows * cell_h
    pixels: list[tuple[int, int, int, int]] = [(0, 0, 0, 255)] * (width * height)
    offset = 0
    for glyph in range(count):
        col = glyph % columns
        row = glyph // columns
        for y in range(cell_h):
            for group in range(6):
                value = raw[offset]
                offset += 1
                for bit in range(4):
                    level = (value >> (6 - bit * 2)) & 0x03
                    x = col * cell_w + group * 4 + bit
                    shade = level * 85
                    pixels[(row * cell_h + y) * width + x] = (shade, shade, shade, 255)
    return png_rgba_from_pixels(width, height, pixels)


def font_raw_from_png(path: Path, glyph_count: int) -> bytes:
    width, height, pixels = read_png_pixels(path)
    cell_w = 24
    cell_h = 24
    if width % cell_w or height % cell_h:
        raise FontCodecError(f"Font PNG dimensions must be multiples of 24 pixels: {path}")
    columns = width // cell_w
    rows = height // cell_h
    max_glyphs = columns * rows
    if glyph_count > max_glyphs:
        raise FontCodecError(f"Font PNG has {max_glyphs} cells but manifest expects {glyph_count}: {path}")
    count = glyph_count
    if max_glyphs > glyph_count:
        for glyph in range(glyph_count, max_glyphs):
            col = glyph % columns
            row = glyph // columns
            found = False
            for y in range(cell_h):
                for x in range(cell_w):
                    r, g, b, a = pixels[(row * cell_h + y) * width + col * cell_w + x]
                    if a and (r + g + b) // 3 >= 128:
                        found = True
                        break
                if found:
                    break
            if found:
                count = max_glyphs
                break
    raw = bytearray()
    for glyph in range(count):
        col = glyph % columns
        row = glyph // columns
        for y in range(cell_h):
            for group in range(6):
                value = 0
                for bit in range(4):
                    x = group * 4 + bit
                    r, g, b, a = pixels[(row * cell_h + y) * width + col * cell_w + x]
                    intensity = 0 if a == 0 else (r + g + b) // 3
                    level = max(0, min(3, round(intensity / 85)))
                    value |= level << (6 - bit * 2)
                raw.append(value)
    return bytes(raw)


def parse_keepfont_header(data: bytes) -> dict:
    """Parse Twin Snakes' resident proportional CNP header.

    The first eight bytes resemble the MGS2 keepfont header, but the glyph
    table is not the same bitfield.  CNP stores a 16-bit pixel offset followed
    by a split five-bit width and flags byte.
    """
    if len(data) < 16:
        raise FontCodecError("The CNP font resource is too small.")
    pixel_offset = int.from_bytes(data[0:2], "little")
    section_size = int.from_bytes(data[4:6], "little")
    if not (8 < pixel_offset <= section_size <= len(data)):
        raise FontCodecError("The CNP font resource has invalid section offsets.")

    table_count = 0
    previous = -1
    for table_offset in range(8, pixel_offset, 4):
        if table_offset + 4 > pixel_offset:
            break
        position = int.from_bytes(data[table_offset:table_offset + 2], "little")
        if position < previous:
            break
        previous = position
        table_count += 1
    if table_count <= 0:
        raise FontCodecError("The CNP font resource has no glyph table.")
    return {
        "pixel_offset": pixel_offset,
        "section_size": section_size,
        "table_count": table_count,
    }


def iter_han_glyphs(data: bytes) -> list[dict]:
    header = parse_keepfont_header(data)
    table_count = int(header["table_count"])
    pixel_offset = int(header["pixel_offset"])
    section_size = int(header["section_size"])
    glyphs: list[dict] = []
    for table_index in range(table_count):
        table_offset = 8 + table_index * 4
        glyph_offset = int.from_bytes(data[table_offset:table_offset + 2], "little")
        width_byte = data[table_offset + 2]
        flags = data[table_offset + 3]
        width = (((flags & 1) << 4) | (width_byte >> 4)) + 1
        byte_length = width * 6
        absolute = pixel_offset + glyph_offset
        if not 1 <= width <= 32 or absolute + byte_length > section_size:
            raise FontCodecError(f"CNP glyph {table_index} has invalid pixel data.")
        if table_index < 96:
            code = 0x20 + table_index
        else:
            code = 0x1F01 + table_index - 96
        glyphs.append({
            "code": code,
            "table_index": table_index,
            "offset": absolute,
            "relative_offset": glyph_offset,
            "width": width,
            "height": 24,
            "flags": flags,
            "byte_length": byte_length,
        })
    return glyphs


def png_from_han_font(data: bytes) -> tuple[bytes, list[dict]]:
    glyphs = iter_han_glyphs(data)
    cell_w = 32
    cell_h = 24
    columns = 16
    rows = max(1, (len(glyphs) + columns - 1) // columns)
    width = columns * cell_w
    height = rows * cell_h
    pixels: list[tuple[int, int, int, int]] = [(0, 0, 0, 255)] * (width * height)
    for glyph_index, glyph in enumerate(glyphs):
        col = glyph_index % columns
        row = glyph_index // columns
        cell_x = col * cell_w
        cell_y = row * cell_h
        raw = data[int(glyph["offset"]):int(glyph["offset"]) + int(glyph["byte_length"])]
        bit_index = 0
        for y in range(24):
            for x in range(int(glyph["width"])):
                byte = raw[bit_index // 4]
                level = (byte >> (6 - (bit_index % 4) * 2)) & 0x03
                shade = level * 85
                py = cell_y + y
                px = cell_x + x
                if 0 <= px < width and 0 <= py < height:
                    pixels[py * width + px] = (shade, shade, shade, 255)
                bit_index += 1
    png = png_rgba_from_pixels(width, height, pixels)
    return png, glyphs


def han_font_from_png(path: Path, original_data: bytes) -> bytes:
    header = parse_keepfont_header(original_data)
    glyphs = iter_han_glyphs(original_data)
    width, height, pixels = read_png_pixels(path)
    columns = 16
    if width % columns:
        raise FontCodecError(f"Latin font PNG has invalid dimensions: {path}")
    cell_w = width // columns
    cell_h = 24
    rows = (len(glyphs) + columns - 1) // columns
    legacy_layout = cell_w == 24 and height >= rows * 26
    row_stride = height // rows if legacy_layout else cell_h
    if not 24 <= cell_w <= 64 or height < rows * row_stride:
        raise FontCodecError(f"Latin font PNG has invalid dimensions: {path}")

    def levels_from_raw(raw: bytes, glyph_width: int) -> list[int]:
        levels: list[int] = []
        for bit_index in range(glyph_width * cell_h):
            byte = raw[bit_index // 4]
            levels.append((byte >> (6 - (bit_index % 4) * 2)) & 0x03)
        return levels

    def png_level(px: int, py: int) -> int:
        r, g, b, a = pixels[py * width + px]
        intensity = 0 if a == 0 else (r + g + b) // 3
        return max(0, min(3, round(intensity / 85)))

    rebuilt: list[tuple[int, int, bytes]] = []
    changed_any = False
    for glyph_index, glyph in enumerate(glyphs):
        col = glyph_index % columns
        row = glyph_index // columns
        cell_x = col * cell_w
        cell_y = row * row_stride
        y_shift = (int(glyph["flags"]) >> 2) if legacy_layout else 0
        if cell_y + y_shift + cell_h > height:
            raise FontCodecError(f"Latin font PNG is missing glyph cells: {path}")
        old_width = int(glyph["width"])
        old_raw = original_data[int(glyph["offset"]):int(glyph["offset"]) + int(glyph["byte_length"])]
        old_levels = levels_from_raw(old_raw, old_width)
        # The previous toolkit exported only the first 222 slots.  Keep the
        # two trailing combining-mark slots intact when importing that layout.
        if legacy_layout and glyph_index >= 222:
            rebuilt.append((old_width, int(glyph["flags"]), old_raw))
            continue
        visible_width = min(old_width, cell_w)
        changed = any(
            png_level(cell_x + x, cell_y + y_shift + y) != old_levels[y * old_width + x]
            for y in range(cell_h)
            for x in range(visible_width)
        )
        rightmost_ink = max(
            (x for y in range(cell_h) for x in range(cell_w)
             if png_level(cell_x + x, cell_y + y_shift + y)),
            default=-1,
        )
        if rightmost_ink >= old_width:
            changed = True
        if not changed:
            rebuilt.append((old_width, int(glyph["flags"]), old_raw))
            continue

        changed_any = True
        new_width = max(old_width, rightmost_ink + 1)
        if new_width > 32:
            raise FontCodecError(
                f"CNP glyph {glyph_index} needs {new_width} pixels; the game supports at most 32."
            )
        raw = bytearray(new_width * 6)
        for y in range(cell_h):
            for x in range(new_width):
                if x < cell_w:
                    level = png_level(cell_x + x, cell_y + y_shift + y)
                elif x < old_width:
                    level = old_levels[y * old_width + x]
                else:
                    level = 0
                bit_index = y * new_width + x
                raw[bit_index // 4] |= level << (6 - (bit_index % 4) * 2)
        # The tested CNP builder clears unknown positioning flags on edited
        # glyphs; bit 0 is restored below as part of the encoded width.
        rebuilt.append((new_width, 0, bytes(raw)))

    if not changed_any:
        return original_data

    table = bytearray()
    pixel_data = bytearray()
    for glyph_width, flags, raw in rebuilt:
        if len(pixel_data) > 0xFFFF:
            raise FontCodecError("The rebuilt CNP pixel table exceeds its 16-bit offset field.")
        table.extend(len(pixel_data).to_bytes(2, "little"))
        encoded_width = glyph_width - 1
        table.append((encoded_width & 0x0F) << 4)
        table.append((flags & 0xFE) | ((encoded_width >> 4) & 1))
        pixel_data.extend(raw)

    pixel_offset = 8 + len(table)
    section_size = pixel_offset + len(pixel_data)
    if section_size > 0xFFFF:
        raise FontCodecError("The rebuilt CNP font section exceeds 65,535 bytes.")
    out_header = bytearray(original_data[:8])
    out_header[0:2] = pixel_offset.to_bytes(2, "little")
    out_header[4:6] = section_size.to_bytes(2, "little")
    tail = original_data[int(header["section_size"]):]
    return bytes(out_header) + bytes(table) + bytes(pixel_data) + tail


def split_keepfont_resource(data: bytes) -> tuple[bytes, bytes, int]:
    def read_u32_le(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 4], "little")

    if len(data) >= 8:
        handata_offset = read_u32_le(0) & 0x03FFFFFF
        zen_offset = read_u32_le(4)
        if 8 <= handata_offset < zen_offset < len(data):
            bitmap_offset = zen_offset
            remainder = len(data) - bitmap_offset
            if remainder % (6 * 24):
                bitmap_offset += remainder % (6 * 24)
            raw_len = len(data) - bitmap_offset
            if raw_len > 0 and raw_len % (6 * 24) == 0:
                return data[:bitmap_offset], data[bitmap_offset:], raw_len // (6 * 24)
    for offset in range(0, min(0x200, len(data)), 4):
        raw_len = len(data) - offset
        if raw_len > 0 and raw_len % (6 * 24) == 0:
            return data[:offset], data[offset:], raw_len // (6 * 24)
    raise FontCodecError("keepfont.r does not contain a recognized 24x24 glyph bitmap block.")
