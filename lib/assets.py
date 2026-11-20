from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Callable, Iterable

import tpl as tpl_codec
from common import *
from disc import DiscSource
from font import font_raw_from_png, han_font_from_png, png_from_font_raw, png_from_han_font, split_keepfont_resource
from png import payload_from_png_rgba, png_rgba_from_payload, png_rgba_from_pixels, read_png_pixels
from text import iter_gcx_segments

def load_mgs2_texture_flatlist() -> dict[int, str]:
    flatlist_path = find_project_root() / MGS2_TEXTURE_FLATLIST
    if not flatlist_path.is_file():
        return {}
    mapping: dict[int, str] = {}
    with flatlist_path.open("r", encoding="utf-8", errors="replace") as handle:
        version = handle.readline().strip()
        if version != "flatmapv2":
            return {}
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            lhs, rhs = parts
            for token in (Path(lhs).stem, rhs):
                if re.fullmatch(r"[0-9A-Fa-f]{6,8}", token):
                    value = int(token, 16)
                    mapping.setdefault(value, rhs)
                    mapping.setdefault(value & 0xFFFFFF, rhs)
    return mapping

def mgs2_stage_resource_extension(resource_type: int) -> str:
    return MGS2_STAGE_EXT_BY_TYPE.get(resource_type, f"type{resource_type:02X}.bin")

def looks_like_stage_dat(data: bytes) -> bool:
    if len(data) < 32:
        return False
    if data[:3] == b"@!d":
        return True
    try:
        entry_count = read_u16_be(data, 8)
        if entry_count <= 0 or entry_count > 1024:
            return False
        end = data.find(b"\0", 16)
        if end <= 16 or end > 96:
            return False
        name = data[16:end]
        if not all(0x20 <= value < 0x7F for value in name):
            return False
        pos = align(end + 1, 4)
        sector = read_u32_be(data, pos)
        return 0 < sector < max(2, len(data) // 0x800)
    except (struct.error, IndexError):
        return False

def stage_resource_output_name(resource: dict, texture_names: dict[int, str]) -> tuple[str, str | None]:
    resource_type = int(resource["type"])
    resource_hash = int(resource["hash"])
    ext = mgs2_stage_resource_extension(resource_type)
    texture_name = texture_names.get(resource_hash) or texture_names.get(resource_hash & 0xFFFFFF)
    if texture_name:
        stem = safe_file_part(Path(texture_name).stem)
        return f"stage_{safe_file_part(resource['directory'])}_{resource_hash:06X}_{stem}.{ext}", texture_name
    return f"stage_{safe_file_part(resource['directory'])}_type{resource_type:02X}_{resource_hash:06X}.{ext}", None

def parse_font_assets(data: bytes, rel_path: str, language_id: int) -> list[AssetPatch]:
    assets: list[AssetPatch] = []
    stream_index = 0
    pos = 0
    while pos + 8 <= len(data):
        section_start = pos
        section_language = read_u16_be(data, pos)
        stream_id = read_u16_be(data, pos + 2)
        length = read_u32_be(data, pos + 4)
        if length < 8 or section_start + length > len(data):
            pos += 4
            continue
        section_type = stream_id & 0xFF
        if section_type == 0x04 and section_language == language_id and length >= 24:
            try:
                text_length = read_u32_le(data, pos + 16)
                font_length_offset = pos + 20 + text_length
                font_length = read_u32_le(data, font_length_offset)
                font_offset = font_length_offset + 4
                if font_length > 0 and font_offset + font_length <= section_start + length:
                    name = f"{safe_file_part(rel_path)}_stream_{stream_index:05d}_font.bin"
                    assets.append(AssetPatch(rel_path, font_offset, font_length, name, "subtitle_font", {
                        "stream_index": stream_index,
                        "section_offset": section_start,
                        "language_id": section_language,
                        "font_length_offset": font_length_offset,
                    }))
            except (struct.error, IndexError):
                pass
        if section_type == 0xF0:
            pos = align(section_start + length, 0x800)
            stream_index += 1
        else:
            pos = section_start + length
    for segment_index, (segment_start, segment_end) in enumerate(iter_gcx_segments(data)):
        try:
            cursor = segment_start + 4
            while cursor + 8 <= segment_end:
                value1 = read_u32_be(data, cursor)
                value2 = read_u32_be(data, cursor + 4)
                cursor += 8
                if value1 == 0 and value2 == 0:
                    break
            start = cursor
            font_start = start + read_u32_le(data, start + 12)
            if font_start + 4 <= segment_end:
                font_length = read_u32_le(data, font_start)
                font_offset = font_start + 4
                if font_length > 0 and font_offset + font_length <= segment_end:
                    name = f"{safe_file_part(rel_path)}_gcx_{segment_index:05d}_font.bin"
                    assets.append(AssetPatch(rel_path, font_offset, font_length, name, "gcx_font", {
                        "segment_index": segment_index,
                        "segment_offset": segment_start,
                        "font_length_offset": font_start,
                    }))
        except (struct.error, IndexError):
            continue
    return assets

def iter_stage_resources(data: bytes) -> Iterable[dict]:
    if not looks_like_stage_dat(data):
        return
    pos = 16
    entry_count = read_u16_be(data, 8)
    directories: list[tuple[str, int]] = []
    for _ in range(entry_count):
        end = data.find(b"\0", pos)
        if end < 0:
            return
        name = data[pos:end].decode("ascii", errors="replace")
        pos = align(end + 1, 4)
        if pos + 4 > len(data):
            return
        directories.append((name, read_u32_be(data, pos)))
        pos += 4
    sector_starts = sorted({sector * 0x800 for _directory, sector in directories})
    for dir_index, (directory, sector) in enumerate(directories):
        start = sector * 0x800
        next_start = next((value for value in sector_starts if value > start), len(data))
        block = data[start:next_start]
        if len(block) < 4:
            continue
        count = read_u32_be(block, 0)
        if count > 10000 or 4 + count * 8 > len(block):
            continue
        items = [(read_u32_be(block, 4 + i * 8), read_u32_be(block, 8 + i * 8)) for i in range(count)]
        data_offset = align(4 + count * 8, 0x800)
        last_data: bytearray | None = None
        for index, (key, offset) in enumerate(items):
            resource_type = key >> 24
            resource_hash = key & 0xFFFFFF
            if resource_type == 0x7F:
                last_data = bytearray(offset) if resource_hash > 0 else last_data
                continue
            if resource_type == 0x7E and last_data is not None:
                comp_start = offset + data_offset
                comp_end = comp_start + resource_hash
                try:
                    last_data[:] = zlib.decompress(block[comp_start:comp_end])
                except zlib.error:
                    pass
                continue
            if resource_type == 0 or last_data is None:
                continue
            next_offset = items[index + 1][1] if index + 1 < len(items) else len(last_data)
            if next_offset < offset or offset > len(last_data):
                continue
            chunk = bytes(last_data[offset:min(next_offset, len(last_data))])
            yield {
                "directory": directory,
                "type": resource_type,
                "hash": resource_hash,
                "offset": offset,
                "data": chunk,
                "index": index,
            }

def collect_stage_font_texture_hashes(stage_data: bytes) -> set[int]:
    font_hashes: set[int] = set()
    recent_font_stub = False
    for resource in iter_stage_resources(stage_data) or []:
        data = resource["data"]
        if resource["type"] == 0x05 and b"keepfontsub.fnt" in data:
            recent_font_stub = True
            continue
        if recent_font_stub and resource["type"] == 0x13:
            font_hashes.add(int(resource["hash"]))
            continue
        if recent_font_stub and resource["type"] != 0x13:
            recent_font_stub = False
    return font_hashes

def parse_stage_header(data: bytes) -> tuple[bytearray, list[tuple[str, int, int]]]:
    if not looks_like_stage_dat(data):
        raise ToolkitError("stage.dat header was not recognized.")
    header = bytearray(data[:16])
    pos = 16
    directories: list[tuple[str, int, int]] = []
    for _ in range(read_u16_be(data, 8)):
        name_start = pos
        end = data.find(b"\0", pos)
        if end < 0:
            raise ToolkitError("stage.dat directory name table is truncated.")
        name = data[pos:end].decode("ascii", errors="replace")
        pos = align(end + 1, 4)
        if pos + 4 > len(data):
            raise ToolkitError("stage.dat directory offset table is truncated.")
        sector = read_u32_be(data, pos)
        directories.append((name, sector, pos))
        pos += 4
    header.extend(data[16:pos])
    return header, directories

def rebuild_stage_directory_block(directory: str, block: bytes, replacements: dict[int, bytes], start_ordinal: int) -> tuple[bytes, int, int]:
    if len(block) < 4:
        return block, 0, start_ordinal
    count = read_u32_be(block, 0)
    if count > 10000 or 4 + count * 8 > len(block):
        return block, 0, start_ordinal
    original_items = [(read_u32_be(block, 4 + i * 8), read_u32_be(block, 8 + i * 8)) for i in range(count)]
    data_offset = align(4 + count * 8, 0x800)
    groups: list[dict] = []
    changed = 0
    group_start_index: int | None = None
    group_compress_index: int | None = None
    group_original_compressed: bytes | None = None
    last_data: bytes | None = None
    resources: list[tuple[int, int | None, int, int, bytes]] = []
    next_ordinal = start_ordinal

    def flush_group() -> None:
        nonlocal changed, group_start_index, group_compress_index
        nonlocal group_original_compressed, last_data, resources
        if group_start_index is None or group_compress_index is None or last_data is None:
            group_start_index = None
            group_compress_index = None
            group_original_compressed = None
            last_data = None
            resources = []
            return
        changed_group = any(
            resource_ordinal is not None and resource_ordinal in replacements
            for _item_index, resource_ordinal, _resource_type, _resource_hash, _chunk in resources
        )
        compressed = group_original_compressed or zlib.compress(last_data, 9)
        if changed_group:
            # Patch the original decompressed folder in place.  Every row and
            # unindexed byte range must survive even when it is not exported;
            # dropping either can turn a later stage load into invalid code.
            rebuilt = bytearray(last_data)
            for item_index, resource_ordinal, resource_type, resource_hash, chunk in resources:
                if resource_ordinal is None:
                    continue
                replacement = replacements.get(resource_ordinal)
                if replacement is None:
                    continue
                if len(replacement) != len(chunk):
                    raise ToolkitError(
                        f"stage.dat {directory} resource {resource_type:02X}:{resource_hash:06X} "
                        f"would change its fixed LT3 slot from {len(chunk):,} to {len(replacement):,} bytes."
                    )
                resource_offset = original_items[item_index][1]
                resource_end = resource_offset + len(chunk)
                if resource_end > len(rebuilt):
                    raise ToolkitError(
                        f"stage.dat {directory} resource {resource_type:02X}:{resource_hash:06X} "
                        "runs outside its decompressed folder."
                    )
                rebuilt[resource_offset:resource_end] = replacement
                changed += 1
            compressed = zlib.compress(bytes(rebuilt), 9)
            if zlib.decompress(compressed) != bytes(rebuilt):
                raise ToolkitError(f"stage.dat {directory} LT3 compression did not round-trip.")
        groups.append({
            "meta_index": group_compress_index,
            "original_offset": original_items[group_compress_index][1],
            "original_size": len(group_original_compressed or b""),
            "compressed": compressed,
            "changed": changed_group,
        })
        group_start_index = None
        group_compress_index = None
        group_original_compressed = None
        last_data = None
        resources = []

    for index, (key, offset) in enumerate(original_items):
        resource_type = key >> 24
        resource_hash = key & 0xFFFFFF
        if resource_type == 0x7F:
            flush_group()
            if resource_hash > 0:
                group_start_index = index
                last_data = bytes(offset)
            continue
        if resource_type == 0x7E and last_data is not None:
            comp_start = offset + data_offset
            comp_end = comp_start + resource_hash
            try:
                group_original_compressed = block[comp_start:comp_end]
                last_data = zlib.decompress(group_original_compressed)
                group_compress_index = index
            except zlib.error:
                last_data = None
            continue
        if last_data is None:
            continue
        next_offset = original_items[index + 1][1] if index + 1 < len(original_items) else len(last_data)
        if next_offset < offset:
            next_offset = len(last_data)
        resource_ordinal = None
        if resource_type != 0:
            resource_ordinal = next_ordinal
            next_ordinal += 1
        resources.append((index, resource_ordinal, resource_type, resource_hash, last_data[offset:min(next_offset, len(last_data))]))
    flush_group()

    # Recompressing an untouched directory changes zlib byte streams and can
    # make stage.dat grow even though no resource in that directory changed.
    # Keep the retail block byte-for-byte, as the tested repacker does.
    if changed == 0:
        return block, 0, next_ordinal

    # The retail builder and MGS2's bldstage.c write every compressed folder
    # sequentially and sector-align the start of the next folder.  The loader
    # walks that same stream order, so leaving later folders at their retail
    # offsets after an earlier stream changes size can stall stage loading.
    # Preserve every decompressed row above, but always rebuild this outer
    # stream in the canonical sequential layout.
    output = bytearray(block[:data_offset])
    payload = bytearray()
    for group in groups:
        if len(payload) % 0x800:
            payload.extend(b"\0" * (align(len(payload), 0x800) - len(payload)))
        compressed_offset = len(payload)
        compressed = group["compressed"]
        old_key, _old_offset = original_items[group["meta_index"]]
        struct.pack_into(
            ">II", output, 4 + group["meta_index"] * 8,
            (old_key & 0xFF000000) | len(compressed), compressed_offset,
        )
        payload.extend(compressed)
    output.extend(payload)
    if len(output) % 0x800:
        output.extend(b"\0" * (align(len(output), 0x800) - len(output)))
    return bytes(output), changed, next_ordinal

def rebuild_stage_dat(data: bytes, replacements: dict[int, bytes]) -> tuple[bytes, int]:
    header, directories = parse_stage_header(data)
    rebuilt_blocks: list[tuple[int, int, int, bytes, int]] = []
    changed = 0
    stage_ordinal = 0
    sector_starts = sorted({sector * 0x800 for _name, sector, _offset in directories})
    for index, (name, sector, _offset_pos) in enumerate(directories):
        start = sector * 0x800
        end = next((value for value in sector_starts if value > start), len(data))
        block, block_changed, stage_ordinal = rebuild_stage_directory_block(name, data[start:end], replacements, stage_ordinal)
        rebuilt_blocks.append((index, start, end, block, block_changed))
        changed += block_changed

    if changed == 0:
        return data, 0

    if changed != len(replacements):
        raise ToolkitError(
            f"stage.dat rebuild applied {changed:,} of {len(replacements):,} requested fixed-slot replacements."
        )

    if all(len(block) <= end - start for _index, start, end, block, _count in rebuilt_blocks):
        output = bytearray(data)
        for _index, start, end, block, block_changed in rebuilt_blocks:
            if not block_changed:
                continue
            output[start:start + len(block)] = block
            output[start + len(block):end] = b"\0" * (end - start - len(block))
    else:
        prefix_end = min(
            (start for _index, start, _end, _block, _count in rebuilt_blocks),
            default=align(len(header), 0x800),
        )
        output = bytearray(data[:prefix_end])
        for index, _start, _end, block, _block_changed in sorted(rebuilt_blocks, key=lambda item: item[1]):
            if len(output) % 0x800:
                output.extend(b"\0" * (align(len(output), 0x800) - len(output)))
            sector = len(output) // 0x800
            _name, _old_sector, offset_pos = directories[index]
            struct.pack_into(">I", output, offset_pos, sector)
            output.extend(block)

    rebuilt = bytes(output)
    original_resources = list(iter_stage_resources(data) or [])
    rebuilt_resources = list(iter_stage_resources(rebuilt) or [])
    if len(original_resources) != len(rebuilt_resources):
        raise ToolkitError("stage.dat validation failed: the resource count changed after rebuild.")
    for ordinal, (original, current) in enumerate(zip(original_resources, rebuilt_resources, strict=True)):
        identity = (original["directory"], original["type"], original["hash"], original["offset"])
        current_identity = (current["directory"], current["type"], current["hash"], current["offset"])
        if identity != current_identity:
            raise ToolkitError(f"stage.dat validation failed: resource {ordinal:,} moved or changed identity.")
        expected = replacements.get(ordinal, original["data"])
        if current["data"] != expected:
            raise ToolkitError(f"stage.dat validation failed: resource {ordinal:,} did not round-trip exactly.")
    return rebuilt, changed

def export_assets(source_path: Path, output_dir: Path, mode: str, log: Callable[[str], None] | None = None) -> tuple[Path, int]:
    source = DiscSource(source_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    exported_texture_by_hash: dict[str, str] = {}

    def export_tpl_images(raw_data: bytes, output_dir_for_images: Path, output_folder: str, source_file: str, output_prefix: str, entry_base: dict) -> None:
        try:
            tpl_images = tpl_codec.parse_tpl(raw_data)
        except tpl_codec.TplError:
            return
        for image in tpl_images:
            try:
                pixels = tpl_codec.decode_image(raw_data, image)
            except tpl_codec.TplError:
                continue
            png_name = f"{output_prefix}_image_{image.index:02d}_{image.format_name}_{image.width}x{image.height}.png"
            png_bytes = png_rgba_from_pixels(image.width, image.height, pixels)
            png_hash = sha256_bytes(png_bytes)
            output_name = f"{output_folder}/{png_name}"
            duplicate_of = None
            if mode == "texture" and png_hash in exported_texture_by_hash:
                output_name = exported_texture_by_hash[png_hash]
                duplicate_of = output_name
            else:
                (output_dir_for_images / png_name).write_bytes(png_bytes)
                if mode == "texture":
                    exported_texture_by_hash[png_hash] = output_name
            can_import = image.fmt in {
                tpl_codec.FMT_I4,
                tpl_codec.FMT_I8,
                tpl_codec.FMT_IA4,
                tpl_codec.FMT_IA8,
                tpl_codec.FMT_RGB565,
                tpl_codec.FMT_RGB5A3,
                tpl_codec.FMT_RGBA32,
                tpl_codec.FMT_C4,
                tpl_codec.FMT_C8,
                tpl_codec.FMT_C14X2,
                tpl_codec.FMT_CMPR,
            }
            entry = {
                "source_file": source_file,
                "output_name": output_name,
                "kind": "tpl_image",
                "asset_format": "tpl_png",
                "image_index": image.index,
                "width": image.width,
                "height": image.height,
                "tpl_format": image.format_name,
                "import_supported": can_import,
                "output_sha256": png_hash,
                "size": image.data_size,
            }
            if duplicate_of:
                entry["duplicate_of"] = duplicate_of
            entry.update(entry_base)
            entries.append(entry)

    if mode == "texture":
        texture_dir = output_dir / "textures"
        texture_dir.mkdir(parents=True, exist_ok=True)
        for file in source.list_files():
            leaf = file.path.rsplit("/", 1)[-1]
            ext = Path(leaf).suffix.lower()
            if ext != ".tpl":
                continue
            raw_data = source.read_file(file)
            export_tpl_images(raw_data, texture_dir, "textures", file.path, safe_file_part(file.path), {})
        stage_file = source.get("stage.dat")
        if stage_file:
            stage_data = source.read_file(stage_file)
            for resource_ordinal, resource in enumerate(iter_stage_resources(stage_data) or []):
                if int(resource["type"]) != 0x13:
                    continue
                tpl_offset = resource["data"].find(TPL_MAGIC_BYTES)
                if tpl_offset < 0:
                    continue
                prefix = f"stage_{safe_file_part(resource['directory'])}_ord{resource_ordinal:05d}_res{int(resource['index']):04d}_{int(resource['hash']):06X}_rpd_tpl{tpl_offset:06X}"
                export_tpl_images(resource["data"][tpl_offset:], texture_dir, "textures", "stage.dat", prefix, {
                    "kind": "stage_tpl_image",
                    "stage_ordinal": resource_ordinal,
                    "stage_directory": resource["directory"],
                    "resource_type": resource["type"],
                    "resource_hash": resource["hash"],
                    "tpl_offset": tpl_offset,
                    "resource_size": len(resource["data"]),
                })
    elif mode == "font":
        asset_dir = output_dir / "fonts"
        asset_dir.mkdir(parents=True, exist_ok=True)
        stage_file = source.get("stage.dat")
        seen_fonts: set[tuple[int, int]] = set()
        if stage_file:
            stage_data = source.read_file(stage_file)
            for resource_ordinal, resource in enumerate(iter_stage_resources(stage_data) or []):
                if resource["type"] != 0x11 or int(resource["hash"]) != 0x383C45:
                    continue
                key = (int(resource["type"]), int(resource["hash"]))
                if key in seen_fonts:
                    continue
                seen_fonts.add(key)
                font_header, font_raw, glyph_count = split_keepfont_resource(resource["data"])
                out_name = f"keepfont_{resource['hash']:06X}.png"
                png_bytes = png_from_font_raw(font_raw)
                (asset_dir / out_name).write_bytes(png_bytes)
                entries.append({
                    "source_file": "stage.dat",
                    "output_name": f"fonts/{out_name}",
                    "kind": "font_bitmap",
                    "asset_format": "font_bitmap_png",
                    "stage_ordinal": resource_ordinal,
                    "stage_directory": resource["directory"],
                    "resource_type": resource["type"],
                    "resource_hash": resource["hash"],
                    "size": len(resource["data"]),
                    "header_size": len(font_header),
                    "glyph_count": glyph_count,
                    "output_sha256": sha256_bytes(png_bytes),
                    "apply_to_matching_hashes": True,
                })
                latin_name = f"latin_keepfont_{resource['hash']:06X}.png"
                latin_png, latin_glyphs = png_from_han_font(resource["data"])
                (asset_dir / latin_name).write_bytes(latin_png)
                entries.append({
                    "source_file": "stage.dat",
                    "output_name": f"fonts/{latin_name}",
                    "kind": "font_latin",
                    "asset_format": "font_han_png",
                    "stage_ordinal": resource_ordinal,
                    "stage_directory": resource["directory"],
                    "resource_type": resource["type"],
                    "resource_hash": resource["hash"],
                    "size": len(resource["data"]),
                    "glyph_count": len(latin_glyphs),
                    "glyphs": latin_glyphs,
                    "output_sha256": sha256_bytes(latin_png),
                    "apply_to_matching_hashes": True,
                })
    else:
        raise ToolkitError(f"Unknown asset mode: {mode}")
    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "type": mode,
        "source_name": source_path.name,
        "source_kind": "directory" if source.is_directory else "gamecube_image",
        "entries": entries,
    }
    manifest_path = output_dir / ASSET_MANIFEST_NAME
    write_json(manifest_path, manifest)
    if log:
        log(f"Exported {len(entries):,} {mode} assets.")
    return manifest_path, len(entries)

def import_assets(source_path: Path, manifest_path: Path, input_dir: Path, output_path: Path, log: Callable[[str], None] | None = None) -> tuple[Path, int, int]:
    source = DiscSource(source_path)
    manifest = read_json(manifest_path)
    entries = manifest.get("entries", [])
    files: dict[str, bytearray] = {}
    stage_replacements: dict[int, bytes] = {}
    stage_tpl_replacements: dict[int, dict[int, dict[int, list[tuple[int, int, int, int]]]]] = {}
    tpl_replacements: dict[str, dict[int, list[tuple[int, int, int, int]]]] = {}
    stage_originals: dict[int, dict] | None = None
    changed = 0
    skipped = 0

    def ensure_stage_originals() -> dict[int, dict]:
        nonlocal stage_originals
        if stage_originals is None:
            stage_originals = {}
            for ordinal, resource in enumerate(iter_stage_resources(source.read_file("stage.dat")) or []):
                stage_originals[ordinal] = {
                    "data": resource["data"],
                    "directory": str(resource["directory"]),
                    "type": int(resource["type"]),
                    "hash": int(resource["hash"]),
                    "index": int(resource["index"]),
                }
        return stage_originals

    def stage_target_keys(entry: dict) -> list[int]:
        base_ordinal = int(entry["stage_ordinal"])
        if not entry.get("apply_to_matching_hashes"):
            return [base_ordinal]
        originals = ensure_stage_originals()
        base = originals.get(base_ordinal, {"type": int(entry["resource_type"]), "hash": int(entry["resource_hash"])})
        return [ordinal for ordinal, resource in originals.items() if resource["type"] == base["type"] and resource["hash"] == base["hash"]]

    for entry in entries:
        rel = normalize_disc_path(entry["source_file"])
        asset_path = input_dir / entry["output_name"]
        if not asset_path.is_file():
            skipped += 1
            continue
        if entry.get("output_sha256") and sha256_file(asset_path) == entry["output_sha256"]:
            continue
        if entry.get("asset_format") == "payload_png":
            asset_data = payload_from_png_rgba(asset_path)
        elif entry.get("asset_format") == "tpl_png":
            if not entry.get("import_supported", False):
                skipped += 1
                continue
            width, height, pixels = read_png_pixels(asset_path)
            if width != int(entry["width"]) or height != int(entry["height"]):
                skipped += 1
                continue
            if entry.get("kind") == "stage_tpl_image":
                for stage_key in stage_target_keys(entry):
                    stage_tpl_replacements.setdefault(stage_key, {}).setdefault(int(entry["tpl_offset"]), {})[int(entry["image_index"])] = pixels
                continue
            tpl_replacements.setdefault(rel, {})[int(entry["image_index"])] = pixels
            continue
        elif entry.get("asset_format") == "font_bitmap_png":
            if entry.get("kind") == "font_bitmap_region":
                asset_data = font_raw_from_png(asset_path, int(entry["glyph_count"]))
            elif rel == "stage.dat":
                originals = ensure_stage_originals()
                applied = False
                for stage_key in stage_target_keys(entry):
                    original_resource = stage_replacements.get(
                        stage_key, originals.get(stage_key, {}).get("data")
                    )
                    if original_resource is None:
                        skipped += 1
                        continue
                    header, _raw, glyph_count = split_keepfont_resource(original_resource)
                    merged = header + font_raw_from_png(
                        asset_path, int(entry.get("glyph_count", glyph_count))
                    )
                    if merged != original_resource:
                        stage_replacements[stage_key] = merged
                        applied = True
                if not applied:
                    continue
                # The stage resource was handled per target so a following CNP
                # atlas edit can merge onto it instead of replacing it.
                continue
            else:
                asset_data = font_raw_from_png(asset_path, int(entry["glyph_count"]))
        elif entry.get("asset_format") == "font_han_png":
            if rel != "stage.dat":
                skipped += 1
                continue
            originals = ensure_stage_originals()
            applied = False
            for stage_key in stage_target_keys(entry):
                original_resource = stage_replacements.get(
                    stage_key, originals.get(stage_key, {}).get("data")
                )
                if original_resource is None:
                    skipped += 1
                    continue
                merged = han_font_from_png(asset_path, original_resource)
                if merged != original_resource:
                    stage_replacements[stage_key] = merged
                    applied = True
            if not applied:
                continue
            continue
        else:
            asset_data = asset_path.read_bytes()
        if entry.get("kind") in {"stage_resource", "stage_texture", "font_texture", "font_bitmap", "font_latin"}:
            originals = ensure_stage_originals()
            for stage_key in stage_target_keys(entry):
                if originals.get(stage_key, {}).get("data") == asset_data:
                    continue
                stage_replacements[stage_key] = asset_data
            continue
        if entry.get("kind") == "raw_asset":
            if asset_data == source.read_file(rel):
                continue
            files[rel] = bytearray(asset_data)
            changed += 1
            continue
        offset = entry.get("offset")
        capacity = entry.get("capacity")
        if offset is None or capacity is None:
            skipped += 1
            continue
        data = asset_data
        if len(data) != int(capacity):
            skipped += 1
            continue
        if rel not in files:
            files[rel] = bytearray(source.read_file(rel))
        files[rel][int(offset):int(offset) + int(capacity)] = data
        changed += 1

    for rel, replacements in tpl_replacements.items():
        base_data = bytes(files[rel]) if rel in files else source.read_file(rel)
        try:
            rebuilt = tpl_codec.replace_images(base_data, replacements)
        except tpl_codec.TplError:
            skipped += len(replacements)
            continue
        if rebuilt == base_data:
            continue
        files[rel] = bytearray(rebuilt)
        changed += len(replacements)

    if stage_tpl_replacements:
        originals = ensure_stage_originals()
        for stage_key, tpl_groups in stage_tpl_replacements.items():
            base_resource = stage_replacements.get(stage_key, originals.get(stage_key, {}).get("data"))
            if base_resource is None:
                skipped += sum(len(images) for images in tpl_groups.values())
                continue
            rebuilt_resource = bytearray(base_resource)
            resource_changed = False
            for tpl_offset, replacements in tpl_groups.items():
                try:
                    tpl_blob = bytes(rebuilt_resource[tpl_offset:])
                    tpl_length = tpl_codec.tpl_size(tpl_blob)
                    old_tpl = tpl_blob[:tpl_length]
                    new_tpl = tpl_codec.replace_images(old_tpl, replacements)
                except (tpl_codec.TplError, IndexError):
                    skipped += len(replacements)
                    continue
                if new_tpl == old_tpl:
                    continue
                rebuilt_resource[tpl_offset:tpl_offset + tpl_length] = new_tpl
                resource_changed = True
            if resource_changed:
                stage_replacements[stage_key] = bytes(rebuilt_resource)

    if stage_replacements:
        stage_data = source.read_file("stage.dat")
        rebuilt_stage, stage_changed = rebuild_stage_dat(stage_data, stage_replacements)
        if stage_changed:
            files["stage.dat"] = bytearray(rebuilt_stage)
            changed += len(stage_replacements)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if source.is_directory:
        output_root = output_path if not output_path.suffix else output_path.with_suffix("")
        for rel, data in files.items():
            source.write_file_to_directory(rel, bytes(data), output_root)
        produced = output_root
    else:
        produced = output_path if output_path.suffix else output_path.with_suffix(".iso")
        source.patch_image_copy({rel: bytes(data) for rel, data in files.items()}, produced)
    if log:
        log(f"Imported {changed:,} assets; skipped {skipped:,}.")
    return produced, changed, skipped

