from __future__ import annotations

import re
import struct
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from common import *
from disc import DiscSource

def iter_target_text_files(source: DiscSource) -> Iterable[DiscFile]:
    for file in source.list_files():
        rel = file.path.lower()
        leaf = rel.rsplit("/", 1)[-1]
        if rel in TEXT_TARGET_NAMES or leaf in TEXT_TARGET_NAMES:
            yield file

def parse_subtitle_patches(data: bytes, rel_path: str, language_id: int) -> list[TextPatch]:
    patches: list[TextPatch] = []
    stream_index = 0
    pos = 0
    while pos + 8 <= len(data):
        section_start = pos
        section_language = read_u16_be(data, pos)
        stream_id = read_u16_be(data, pos + 2)
        length = read_u32_be(data, pos + 4)
        if length < 8 or section_start + length > len(data):
            pos += 0x800 if data[pos:pos + 0x10].count(0) == 0x10 else 4
            continue
        section_type = stream_id & 0xFF
        if section_type == 0x04 and section_language == language_id and length >= 24:
            try:
                base_time = read_u32_be(data, pos + 8)
                content_start = pos + 12
                text_length = read_u32_le(data, content_start + 4)
                text_start = content_start + 8
                text_end = text_start + text_length
                cursor = text_start
                text_index = 0
                while cursor + 0x10 <= text_end:
                    start = read_u32_le(data, cursor)
                    end = read_u32_le(data, cursor + 4)
                    unknown1 = read_u32_le(data, cursor + 8)
                    entry_length = read_u16_le(data, cursor + 12)
                    unknown2 = read_u16_le(data, cursor + 14)
                    if entry_length < 0x10 or cursor + entry_length > text_end:
                        break
                    raw_offset = cursor + 0x10
                    capacity = entry_length - 0x10
                    raw_text = data[raw_offset:raw_offset + capacity]
                    patches.append(TextPatch(
                        source_file=rel_path,
                        offset=raw_offset,
                        capacity=capacity,
                        original_text=text_bytes_to_string(raw_text),
                        kind="movie_subtitle",
                        metadata={
                            "stream_index": stream_index,
                            "section_offset": section_start,
                            "language_id": section_language,
                            "stream_id": stream_id,
                            "base_time": base_time,
                            "text_index": text_index,
                            "start": start,
                            "end": end,
                            "unknown1": unknown1,
                            "unknown2": unknown2,
                            "use_double_ascii": section_language == SUBTITLE_LANGUAGE_JAPANESE,
                        },
                    ))
                    cursor += entry_length
                    text_index += 1
            except (struct.error, IndexError):
                pass
        if section_type == 0xF0:
            pos = align(section_start + length, 0x800)
            stream_index += 1
        else:
            pos = section_start + length
    return patches

def read_gcx_text_layout(segment: bytes, segment_start: int = 0, segment_end: int | None = None) -> tuple[int, int, int] | None:
    if segment_end is None:
        segment_end = len(segment)
    try:
        cursor = segment_start + 4
        while cursor + 8 <= segment_end:
            value1 = read_u32_be(segment, cursor)
            value2 = read_u32_be(segment, cursor + 4)
            cursor += 8
            if value1 == 0 and value2 == 0:
                break
        start = cursor
        if start + 16 > segment_end:
            return None
        text_start = start + read_u32_le(segment, start + 8)
        font_start = start + read_u32_le(segment, start + 12)
        if not (start + 16 <= text_start <= font_start <= segment_end):
            return None
        entity_count = (text_start - (start + 16)) // 4
        if entity_count <= 0 or entity_count > 0x10000:
            return None
        return start, text_start, font_start
    except (struct.error, IndexError):
        return None

def iter_gcx_segments(data: bytes) -> Iterable[tuple[int, int]]:
    markers = [b"\x44\xAA\x21\x40"]
    if len(data) >= 16 and data[:4] not in markers:
        first_marker = data[:4]
        if first_marker.count(0) < 4 and read_gcx_text_layout(data, 0, len(data)) is not None:
            markers.append(first_marker)
        elif first_marker.count(0) < 4:
            count = data.count(first_marker)
            standalone_gcx = False
            if data[4:12] == b"\0" * 8 and len(data) >= 32:
                start = 12
                try:
                    text_start = start + read_u32_le(data, start + 8)
                    font_start = start + read_u32_le(data, start + 12)
                    standalone_gcx = start + 16 <= text_start <= font_start <= len(data)
                except (struct.error, IndexError):
                    standalone_gcx = False
            if count > 1 or standalone_gcx:
                markers.append(first_marker)
    positions: list[int] = []
    for marker in markers:
        positions = [match.start() for match in re.finditer(re.escape(marker), data)]
        if positions:
            break
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(data)
        if end - start > 32 and read_gcx_text_layout(data, start, end) is not None:
            yield start, end

def parse_gcx_text_patches(data: bytes, rel_path: str) -> list[TextPatch]:
    patches: list[TextPatch] = []
    for segment_index, (segment_start, segment_end) in enumerate(iter_gcx_segments(data)):
        try:
            layout = read_gcx_text_layout(data, segment_start, segment_end)
            if layout is None:
                continue
            start, text_start, font_start = layout
            entity_count = (text_start - (start + 16)) // 4
            for entity_index in range(entity_count):
                entry_offset = start + 16 + entity_index * 4
                offset_value = read_u32_le(data, entry_offset)
                text_offset = text_start + (offset_value & 0xFFFFFF)
                if text_offset < text_start or text_offset >= segment_end:
                    continue
                end = data.find(b"\0", text_offset, segment_end)
                if end < 0:
                    continue
                capacity = end - text_offset + 1
                patches.append(TextPatch(
                    source_file=rel_path,
                    offset=text_offset,
                    capacity=capacity,
                    original_text=text_bytes_to_string(data[text_offset:end + 1]),
                    kind="codec_gcx",
                    metadata={
                        "segment_index": segment_index,
                        "segment_offset": segment_start,
                        "entity_index": entity_index,
                        "entity_table_offset": entry_offset,
                        "offset_value": offset_value,
                        "use_double_ascii": False,
                    },
                ))
        except (struct.error, IndexError):
            continue
    return patches

CODEC_ENGLISH_ENTITY_RANGES_BY_SEGMENT: dict[int, tuple[tuple[int, int], ...]] = {
    61: ((0, 21), (132, 304)),
    171: ((0, 3), (24, 139)),
    173: ((0, 62),),
}

_ENGLISH_WORDS = frozenset("""
the you to and of is it that your we they this for with be have not
can are will she but what when there here from about my
him her his i'm it's don't get got just now know out up yes
""".split())
_FOREIGN_WORDS = frozenset("""
le la les de du des un une et est pas vous tu je il elle nous ce qui que dans
pour sur avec tout
der das den und ist nicht sie ich wir es zu ein eine mit auf sich aber
noch nur
lo gli una di che non ti mi ci per sono questo sei
el los las se te por para su usted pero como esta

temps imparti otages effacement charger sauvegarde fichier appuyez bouton
touche manette activee desactivee ecran
geiseln atomwaffen objekte bildschirm zeitlimit einheit waffe taste knopf
werden einstellen anordnen aufgenommenen
oggetti ostaggi armi nucleari seleziona attivata disattivata pulsante tasto
schermo organizzati descrizione operativo dettagliate disposizione
objetos rehenes armas nucleares seleccionar activada desactivada boton
pantalla ayudantes organizados descripcion
""".split())
_ESCAPED_GLYPH_RE = re.compile(r"\\x[0-9A-Fa-f]{2,4}")
_MARKUP_RE = re.compile(r"\[/?[A-Za-z]+\]|\\x[0-9A-Fa-f]{2,4}|/n|\|")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_CJK_MIN = 0x2E00

def language_verdict(text: str) -> int:
    body = _MARKUP_RE.sub(" ", text or "")
    if any(ord(char) >= _CJK_MIN for char in body):
        return -1
    escapes = len(_ESCAPED_GLYPH_RE.findall(text or ""))
    if escapes >= 2 and len(_ASCII_LETTER_RE.findall(_ESCAPED_GLYPH_RE.sub("", text or ""))) < escapes:
        return -1
    words = [word.lower() for word in _WORD_RE.findall(body)]
    if not words:
        return 0
    accented = any(ord(char) > 127 and unicodedata.category(char).startswith("L") for char in body)
    english = sum(word in _ENGLISH_WORDS for word in words)
    foreign = sum(word in _FOREIGN_WORDS for word in words)
    if accented or (foreign and foreign >= english):
        return -1
    return 1 if english else 0

def looks_foreign_text(text: str) -> bool:
    body = _MARKUP_RE.sub(" ", _ESCAPED_GLYPH_RE.sub(" ", text or ""))
    if any(ord(char) >= _CJK_MIN for char in body):
        return True
    words = [word.lower() for word in _WORD_RE.findall(body)]
    if not words:
        return False
    if any(ord(char) > 127 and unicodedata.category(char).startswith("L") for char in body):
        return True
    foreign = sum(word in _FOREIGN_WORDS for word in words)
    return bool(foreign) and foreign >= sum(word in _ENGLISH_WORDS for word in words)

def looks_table_or_escape_heavy(text: str) -> bool:
    escapes = _ESCAPED_GLYPH_RE.findall(text or "")
    if len(escapes) < 2:
        return False
    body = _ESCAPED_GLYPH_RE.sub("", text or "")
    ascii_letters = _ASCII_LETTER_RE.findall(body)
    if (text or "").lstrip().startswith("\\x"):
        return True
    return len(ascii_letters) <= len(escapes) * 4

def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def english_spans_from_texts(texts: list[str], *, include_latin_leftovers: bool = False) -> list[tuple[int, int]]:
    marks = [language_verdict(text) for text in texts]
    adjusted = list(marks)
    for index, mark in enumerate(adjusted):
        if mark <= 0:
            continue
        before = next((item for item in reversed(adjusted[:index]) if item), 0)
        after = next((item for item in adjusted[index + 1:] if item), 0)
        if before < 0 and after < 0:
            adjusted[index] = -1

    tiled: list[tuple[int, int]] = []
    at = 0
    while at < len(adjusted):
        end = at
        while end < len(adjusted) and adjusted[end] >= 0:
            end += 1
        while end > at and adjusted[end - 1] == 0:
            end -= 1
        if end == at:
            tiled = []
            break
        tiled.append((at, end))
        at += CODEC_LANGUAGE_COUNT * (end - at)
    spans = tiled if at == len(adjusted) else []

    if not spans:
        start: int | None = None
        spans = []
        for index, mark in enumerate(adjusted + [-1]):
            if mark < 0:
                if start is not None and any(item > 0 for item in adjusted[start:index]):
                    spans.append((start, index))
                start = None
            elif start is None:
                start = index

    claimed = {index for start, end in spans for index in range(start, end)}
    menu_spans: list[tuple[int, int]] = []
    run: list[int] = []
    for index in range(len(marks) + 1):
        if index < len(marks) and index not in claimed and marks[index] >= 0:
            run.append(index)
            continue
        if run:
            last = max((item for item in run if marks[item] > 0), default=None)
            if last is not None:
                menu_spans.append((run[0], last + 1))
            run = []

    if include_latin_leftovers:
        for index in range(len(texts) + 1):
            if index < len(texts) and index not in claimed and marks[index] >= 0:
                run.append(index)
                continue
            if run:
                last = max((item for item in run if _ASCII_LETTER_RE.search(_ESCAPED_GLYPH_RE.sub("", texts[item]))), default=None)
                if last is not None:
                    menu_spans.append((run[0], last + 1))
                run = []

    return merge_spans(spans + menu_spans)

def select_codec_language_block(items: list[TextPatch], language_id: int, segment_index: int) -> list[TextPatch]:
    if language_id == SUBTITLE_LANGUAGE_ENGLISH:
        texts = [item.original_text for item in items]
        spans = english_spans_from_texts(texts)
        if spans:
            selected: list[TextPatch] = []
            for item in items:
                if looks_table_or_escape_heavy(item.original_text):
                    continue
                entity_index = int(item.metadata.get("entity_index", -1))
                for start, end in spans:
                    if start <= entity_index < end:
                        item.metadata["language_id"] = SUBTITLE_LANGUAGE_ENGLISH
                        item.metadata["language"] = "English"
                        item.metadata["language_selector"] = f"codec_english_span_{start}_{end}"
                        item.metadata["codec_english_spans"] = [list(span) for span in spans]
                        selected.append(item)
                        break
            if selected:
                return selected
    if language_id == SUBTITLE_LANGUAGE_ENGLISH and segment_index in CODEC_ENGLISH_ENTITY_RANGES_BY_SEGMENT:
        selected: list[TextPatch] = []
        for item in items:
            entity_index = int(item.metadata.get("entity_index", -1))
            for start, end in CODEC_ENGLISH_ENTITY_RANGES_BY_SEGMENT[segment_index]:
                if start <= entity_index <= end:
                    item.metadata["language_id"] = SUBTITLE_LANGUAGE_ENGLISH
                    item.metadata["language"] = "English"
                    item.metadata["language_selector"] = f"codec_english_entities_{start}_{end}"
                    selected.append(item)
                    break
        return selected
    block_index = CODEC_LANGUAGE_BLOCKS.get(language_id, 0)
    if block_index == 0:
        block_size = (len(items) + CODEC_LANGUAGE_COUNT - 1) // CODEC_LANGUAGE_COUNT
        selected = items[:block_size]
        for item in selected:
            item.metadata["language_id"] = language_id
            item.metadata["language"] = "English" if language_id == SUBTITLE_LANGUAGE_ENGLISH else "Japanese"
            item.metadata["language_selector"] = "codec_leading_language_block"
        return selected
    block_size = len(items) // CODEC_LANGUAGE_COUNT
    start = block_index * block_size
    if start >= len(items):
        return []
    selected = items[start:start + block_size]
    for item in selected:
        item.metadata["language_id"] = language_id
        item.metadata["language"] = "English" if language_id == SUBTITLE_LANGUAGE_ENGLISH else "Japanese"
        item.metadata["language_selector"] = "codec_language_block"
    return selected

def select_codec_patches_by_language(patches: list[TextPatch], language_id: int) -> list[TextPatch]:
    by_segment: dict[int, list[TextPatch]] = {}
    passthrough: list[TextPatch] = []
    for item in patches:
        if item.kind != "codec_gcx":
            passthrough.append(item)
            continue
        segment = int(item.metadata.get("segment_index", -1))
        by_segment.setdefault(segment, []).append(item)
    for segment in sorted(by_segment):
        passthrough.extend(select_codec_language_block(by_segment[segment], language_id, segment))
    return passthrough

STAGE_LANGUAGE_ENGLISH = 1

# Twin Snakes carries several title/resident PAL string resources in one GCX
# table. MGS2 source selects those resources with GM_Language / GM_Language-1;
# these ranges are the English resource blocks in the original entity table,
# not a visible-text language filter.
STAGE_ENGLISH_ENTITY_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "n_title": (
        (0, 41),       # option/radar/menu explanations
        (252, 331),    # previous story, part 1
        (732, 779),    # previous story, part 2
        (1020, 1021),  # MSX story introductions
        (1032, 1079),  # title/load/save/card messages
        (1320, 1323),  # disc swap messages
        (1344, 1344),  # controller warning
    ),
    "r_cmmn": (
        (80, 141),     # shared English system/location strings
        (208, 251),    # shared English system/location strings
        (760, 764),    # difficulty labels
        (812, 940),    # English Twin Snakes item/help/card strings
    ),
    "area06b": ((1, 1),),
    "area13a": ((1, 1),),
    "area15a": ((1, 1),),
    "disk1": ((0, 3),),
    "disk2": ((0, 3),),
    "ending": ((0, 2),),
}
STAGE_EXCLUDED_ENTITIES_BY_SHA: dict[tuple[str, int], set[str]] = {
    ("r_cmmn", 812): {"712ec40a537d098b1a51cb029af269bd13c8cbc55ff0199206523cb4024688da"},
}

def is_stage_english_entity(directory: str, entity_index: int, resource_sha: str = "") -> tuple[bool, str]:
    directory_key = directory.lower()
    if resource_sha in STAGE_EXCLUDED_ENTITIES_BY_SHA.get((directory_key, entity_index), set()):
        return False, "non_english_disc_variant"
    ranges = STAGE_ENGLISH_ENTITY_RANGES.get(directory_key)
    if ranges is None:
        return False, "unclassified_stage_resource"
    for start, end in ranges:
        if start <= entity_index <= end:
            return True, f"english_resource_entities_{start}_{end}"
    return False, "non_english_resource_entity"

def parse_stage_resident_text_patches(stage_data: bytes, rel_path: str) -> list[TextPatch]:
    from assets import iter_stage_resources

    patches: list[TextPatch] = []
    seen_signatures: set[tuple[int, int, str]] = set()
    for resource_ordinal, resource in enumerate(iter_stage_resources(stage_data) or []):
        resource_type = int(resource["type"])
        resource_hash = int(resource["hash"])
        if resource_type != 0x06:
            continue
        resource_data = resource["data"]
        resource_sha = sha256_bytes(resource_data)
        signature = (resource_type, resource_hash, resource_sha)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        gcx_segments = list(iter_gcx_segments(resource_data))
        for segment_index, (segment_start, segment_end) in enumerate(gcx_segments):
            try:
                layout = read_gcx_text_layout(resource_data, segment_start, segment_end)
                if layout is None:
                    continue
                start, text_start, font_start = layout
                entity_count = (text_start - (start + 16)) // 4
                entity_records: list[tuple[int, int, int, str, int]] = []
                for entity_index in range(entity_count):
                    entry_offset = start + 16 + entity_index * 4
                    offset_value = read_u32_le(resource_data, entry_offset)
                    text_offset = text_start + (offset_value & 0xFFFFFF)
                    if text_offset < text_start or text_offset >= font_start:
                        continue
                    end = resource_data.find(b"\0", text_offset, font_start)
                    if end < 0:
                        continue
                    original_text = text_bytes_to_string(resource_data[text_offset:end + 1])
                    if original_text == "":
                        continue
                    entity_records.append((entity_index, entry_offset, offset_value, original_text, end - text_offset + 1))
                texts_by_entity = [""] * entity_count
                for entity_index, _entry_offset, _offset_value, original_text, _capacity in entity_records:
                    texts_by_entity[entity_index] = original_text
                dynamic_spans = english_spans_from_texts(texts_by_entity, include_latin_leftovers=True)
                dynamic_entities = {
                    index
                    for span_start, span_end in dynamic_spans
                    for index in range(span_start, span_end)
                }
                # Text-only stage resources do not always have six language
                # blocks.  The tested tool exposes the whole GCX only when no
                # language structure can be found; otherwise it uses the
                # detected English/menu spans and never falls back to fixed
                # directory-specific ranges.
                selected_entities = dynamic_entities or set(range(entity_count))
                for entity_index, entry_offset, offset_value, original_text, capacity in entity_records:
                    if looks_table_or_escape_heavy(original_text):
                        continue
                    if entity_index not in selected_entities:
                        continue
                    language_selector = (
                        "dynamic_stage_english_span"
                        if dynamic_entities
                        else "unstructured_stage_gcx"
                    )
                    text_offset = text_start + (offset_value & 0xFFFFFF)
                    patches.append(TextPatch(
                        source_file=rel_path,
                        offset=text_offset,
                        capacity=capacity,
                        original_text=original_text,
                        kind="stage_gcx",
                        metadata={
                            "stage_ordinal": resource_ordinal,
                            "stage_directory": resource["directory"],
                            "resource_type": resource_type,
                            "resource_hash": resource_hash,
                            "resource_sha256": resource_sha,
                            "segment_index": segment_index,
                            "segment_offset": segment_start,
                            "entity_index": entity_index,
                            "entity_count": entity_count,
                            "entity_table_offset": entry_offset,
                            "offset_value": offset_value,
                            "language_id": STAGE_LANGUAGE_ENGLISH,
                            "language": "English",
                            "language_selector": language_selector,
                            "stage_english_spans": [list(span) for span in dynamic_spans],
                            "use_double_ascii": False,
                            "apply_to_matching_signature": True,
                        },
                    ))
            except (struct.error, IndexError):
                continue
    return patches

def collect_text_patches(source: DiscSource, language_id: int, log: Callable[[str], None] | None = None) -> list[TextPatch]:
    patches: list[TextPatch] = []
    for file in iter_target_text_files(source):
        data = source.read_file(file)
        leaf = file.path.lower().rsplit("/", 1)[-1]
        if log:
            log(f"Scanning {file.path} ({file.size:,} bytes)")
        if leaf in {"demo.dat", "movie.dat", "vox.dat"}:
            patches.extend(parse_subtitle_patches(data, file.path, language_id))
        elif leaf == "codec.dat":
            patches.extend(parse_gcx_text_patches(data, file.path))
    patches = select_codec_patches_by_language(patches, language_id)
    stage_file = source.get("stage.dat")
    if stage_file:
        if log:
            log(f"Scanning {stage_file.path} ({stage_file.size:,} bytes)")
        patches.extend(parse_stage_resident_text_patches(source.read_file(stage_file), stage_file.path))
    return patches

def export_text(source_path: Path, output_dir: Path, log: Callable[[str], None] | None = None) -> tuple[Path, Path, int]:
    source = DiscSource(source_path)
    patches = collect_text_patches(source, SUBTITLE_LANGUAGE_ENGLISH, log)
    output_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = output_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / TEXT_MANIFEST_NAME
    lines_by_file: dict[str, list[str]] = {}
    export_entries: list[dict] = []
    for item in patches:
        leaf = Path(item.source_file).name.lower()
        text_name = f"{Path(leaf).stem}.txt"
        lines = lines_by_file.setdefault(text_name, [])
        lines.append(escape_text_line(item.original_text))
        export_entries.append({
            "source_file": item.source_file,
            "text_file": f"texts/{text_name}",
            "line_number": len(lines),
            "offset": item.offset,
            "capacity": item.capacity,
            "kind": item.kind,
            "original_text": item.original_text,
            "metadata": item.metadata,
        })
    for text_name, lines in sorted(lines_by_file.items()):
        (texts_dir / text_name).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "type": "text",
        "source_name": source_path.name,
        "source_kind": "directory" if source.is_directory else "gamecube_image",
        "language_id": SUBTITLE_LANGUAGE_ENGLISH,
        "language": "English",
        "text_files": sorted(lines_by_file),
        "entries": export_entries,
    }
    write_json(manifest_path, manifest)
    if log:
        log(f"Exported {len(patches):,} text lines.")
    return output_dir, manifest_path, len(patches)

def load_translated_lines(text_path: Path) -> list[str]:
    raw = text_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if raw.endswith("\n"):
        return [unescape_text_line(line) for line in lines]
    return [unescape_text_line(line) for line in lines]

def load_manifest_translations(manifest: dict, text_root: Path) -> list[str]:
    entries = manifest.get("entries", [])
    if text_root.is_file():
        lines = load_translated_lines(text_root)
        if len(entries) != len(lines):
            raise ToolkitError(f"Line count mismatch: manifest has {len(entries)} entries but TXT has {len(lines)} lines.")
        return lines
    cache: dict[str, list[str]] = {}
    translations: list[str] = []
    manifest_dir = text_root
    for entry in entries:
        text_name = entry.get("text_file")
        if not text_name:
            raise ToolkitError("This manifest does not contain split text file names.")
        if text_name not in cache:
            path = manifest_dir / text_name
            if not path.is_file():
                raise ToolkitError(f"Missing translated text file: {path}")
            cache[text_name] = load_translated_lines(path)
        line_index = int(entry.get("line_number", 0)) - 1
        lines = cache[text_name]
        if line_index < 0 or line_index >= len(lines):
            raise ToolkitError(f"{text_name} does not contain line {line_index + 1}.")
        translations.append(lines[line_index])
    return translations

def encode_game_text(text: str, use_double_ascii: bool) -> tuple[bytes, list[str]]:
    encoded, warnings = string_to_text_bytes(text, use_double_ascii=use_double_ascii)
    return encoded + b"\0", warnings

def _parse_subtitle_section(data: bytes, section_start: int, section_end: int) -> dict | None:
    try:
        if section_end - section_start < 24:
            return None
        language = read_u16_be(data, section_start)
        stream_id = read_u16_be(data, section_start + 2)
        length = read_u32_be(data, section_start + 4)
        base_time = read_u32_be(data, section_start + 8)
        header_extra = data[section_start + 12:section_start + 16]
        text_length = read_u32_le(data, section_start + 16)
        text_start = section_start + 20
        text_end = text_start + text_length
        if text_end + 4 > section_end:
            return None
        records: list[dict] = []
        cursor = text_start
        while cursor < text_end:
            if cursor + 0x10 > text_end:
                return None
            entry_length = read_u16_le(data, cursor + 12)
            if entry_length < 0x10 or cursor + entry_length > text_end:
                return None
            records.append({
                "offset": cursor,
                "start": read_u32_le(data, cursor),
                "end": read_u32_le(data, cursor + 4),
                "unknown1": read_u32_le(data, cursor + 8),
                "length": entry_length,
                "unknown2": read_u16_le(data, cursor + 14),
                "raw": data[cursor + 0x10:cursor + entry_length],
                "full": data[cursor:cursor + entry_length],
            })
            cursor += entry_length
        if cursor != text_end:
            return None
        font_length = read_u32_le(data, text_end)
        font_start = text_end + 4
        font_end = font_start + font_length
        if font_end > section_end:
            return None
        tail = data[font_end:section_end]
        if not tail.strip(b"\0"):
            tail = b""
        return {
            "language": language,
            "stream_id": stream_id,
            "length": length,
            "base_time": base_time,
            "header_extra": header_extra,
            "records": records,
            "font": data[font_start:font_end],
            "tail": tail,
        }
    except (struct.error, IndexError):
        return None

def _build_subtitle_section(info: dict, records: list[bytes], *, compact: bool = False, font: bytes | None = None, tail: bytes | None = None) -> bytes:
    body = b"".join(records)
    font_data = info["font"] if font is None else font
    tail_data = info["tail"] if tail is None else tail
    needed = align(24 + len(body) + len(font_data) + len(tail_data), 0x10)
    total = needed if compact else max(needed, int(info["length"]))
    out = bytearray()
    out.extend(struct.pack(">HHII", int(info["language"]), int(info["stream_id"]), total, int(info["base_time"])))
    out.extend(info["header_extra"])
    out.extend(struct.pack("<I", len(body)))
    out.extend(body)
    out.extend(struct.pack("<I", len(font_data)))
    out.extend(font_data)
    out.extend(tail_data)
    out.extend(b"\0" * (total - len(out)))
    return bytes(out)

def _subtitle_record(start: int, end: int, unknown1: int, raw: bytes, unknown2: int, *, old_length: int | None = None) -> bytes:
    padded_length = align(len(raw), 4)
    entry_length = 0x10 + padded_length
    if old_length is not None and entry_length <= old_length:
        entry_length = old_length
        padded_length = old_length - 0x10
    out = bytearray()
    out.extend(struct.pack("<IIIHH", start, end, unknown1, entry_length, unknown2))
    out.extend(raw)
    out.extend(b"\0" * (padded_length - len(raw)))
    return bytes(out)

def _blank_subtitle_section(data: bytes, section_start: int, section_end: int) -> bytes | None:
    info = _parse_subtitle_section(data, section_start, section_end)
    if info is None:
        return None
    records = []
    for record in info["records"]:
        records.append(_subtitle_record(
            int(record["start"]),
            int(record["end"]),
            int(record["unknown1"]),
            b"\0",
            int(record["unknown2"]),
        ))
    return _build_subtitle_section(info, records, compact=True, font=b"", tail=b"")

def _pad_subtitle_chunks_to(chunks: list[bytes], wanted: int) -> list[bytes]:
    if wanted <= 0:
        return chunks
    for index in range(len(chunks) - 1, -1, -1):
        chunk = chunks[index]
        if len(chunk) < 8:
            continue
        stream_id = int.from_bytes(chunk[2:4], "big")
        if stream_id & 0xFF != 0x04:
            continue
        length = int.from_bytes(chunk[4:8], "big")
        if length != len(chunk):
            continue
        grown = bytearray(chunk)
        grown[4:8] = (length + wanted).to_bytes(4, "big")
        chunks[index] = bytes(grown) + b"\0" * wanted
        break
    return chunks

def _rebuild_translated_subtitle_section(data: bytes, section_start: int, section_end: int, replacements: dict[tuple[int, int, int], tuple[str, dict, int]]) -> tuple[bytes, int, list[str], set[tuple[int, int, int]]] | None:
    info = _parse_subtitle_section(data, section_start, section_end)
    if info is None:
        return None
    language = int(info["language"])
    changed = 0
    report: list[str] = []
    used: set[tuple[int, int, int]] = set()
    records: list[bytes] = []
    use_double = language == SUBTITLE_LANGUAGE_JAPANESE
    for text_index, record in enumerate(info["records"]):
        key = (section_start, text_index, language)
        if key not in replacements:
            records.append(bytes(record["full"]))
            continue
        text, _entry, line_number = replacements[key]
        raw_text, warnings = encode_game_text(text, use_double)
        message = "; ".join(dict.fromkeys(warnings))
        records.append(_subtitle_record(
            int(record["start"]),
            int(record["end"]),
            int(record["unknown1"]),
            raw_text,
            int(record["unknown2"]),
        ))
        old_raw = bytes(record["raw"]).rstrip(b"\0") + b"\0"
        if raw_text != old_raw:
            changed += 1
        report.append(f'{line_number},"subtitle",patched,{int(record["length"])},{len(records[-1])},"{message}"')
        used.add(key)
    return _build_subtitle_section(info, records), changed, report, used

def rebuild_subtitle_dat(data: bytes, entries: list[dict], translations: list[str]) -> tuple[bytes, int, int, list[str]]:
    replacements: dict[tuple[int, int, int], tuple[str, dict, int]] = {}
    for fallback_line_number, (entry, text) in enumerate(zip(entries, translations, strict=True), start=1):
        meta = entry.get("metadata", {})
        line_number = int(entry.get("line_number", fallback_line_number))
        replacements[(int(meta["section_offset"]), int(meta["text_index"]), int(meta["language_id"]))] = (text, entry, line_number)

    output = bytearray()
    pos = 0
    changed = 0
    skipped = 0
    report: list[str] = []
    stream_start = 0
    stream_index = 0
    stream_sections: list[tuple[int, int, int, int]] = []

    def stream_has_translation(sections: list[tuple[int, int, int, int]]) -> bool:
        for section_start, _section_end, section_type, section_language in sections:
            if section_type != 0x04:
                continue
            if any(key[0] == section_start and key[2] == section_language for key in replacements):
                return True
        return False

    def replacement_count_for_stream(sections: list[tuple[int, int, int, int]]) -> int:
        starts = {section_start for section_start, _section_end, section_type, _section_language in sections if section_type == 0x04}
        return sum(1 for key in replacements if key[0] in starts)

    def rebuild_stream(sections: list[tuple[int, int, int, int]], eos_start: int, original_end: int) -> tuple[bytes, int, int, list[str]]:
        has_translation = stream_has_translation(sections)
        chunks: list[bytes] = []
        local_changed = 0
        local_skipped = 0
        local_report: list[str] = []
        for section_start, section_end, section_type, section_language in sections:
            original = data[section_start:section_end]
            if section_type != 0x04:
                chunks.append(original)
                continue
            if any(key[0] == section_start and key[2] == section_language for key in replacements):
                rebuilt = _rebuild_translated_subtitle_section(data, section_start, section_end, replacements)
                if rebuilt is None:
                    chunks.append(original)
                    local_skipped += sum(1 for key in replacements if key[0] == section_start and key[2] == section_language)
                    local_report.append(f'0,"subtitle",skipped,{len(original)},{len(original)},"Failed to rebuild translated subtitle section."')
                    continue
                chunk, section_changed, section_report, _used = rebuilt
                chunks.append(chunk)
                local_changed += section_changed
                local_report.extend(section_report)
                continue
            if has_translation and section_language != SUBTITLE_LANGUAGE_ENGLISH:
                blank = _blank_subtitle_section(data, section_start, section_end)
                chunks.append(blank if blank is not None else original)
                continue
            chunks.append(original)

        wanted = eos_start - (len(output) + sum(len(chunk) for chunk in chunks))
        if wanted > 0:
            chunks = _pad_subtitle_chunks_to(chunks, wanted)
        chunks_blob = b"".join(chunks)
        if len(output) + len(chunks_blob) > eos_start:
            failed = replacement_count_for_stream(sections)
            overrun = len(output) + len(chunks_blob) - eos_start
            fallback = data[stream_start:original_end]
            return fallback, 0, failed, [
                f'0,"subtitle",skipped,{original_end - stream_start},{original_end - stream_start + overrun},'
                f'"Stream {stream_index:05d} exceeds its fixed boundary by {overrun} byte(s) even after blanking other languages."'
            ]

        eos_header = data[eos_start:min(eos_start + 16, len(data))]
        rebuilt_stream = bytearray(chunks_blob)
        rebuilt_stream.extend(eos_header)
        absolute_after_eos_header = len(output) + len(rebuilt_stream)
        if absolute_after_eos_header < original_end:
            rebuilt_stream.extend(b"\0" * (original_end - absolute_after_eos_header))
        elif absolute_after_eos_header > original_end:
            failed = replacement_count_for_stream(sections)
            overrun = absolute_after_eos_header - original_end
            fallback = data[stream_start:original_end]
            return fallback, 0, failed, [
                f'0,"subtitle",skipped,{original_end - stream_start},{original_end - stream_start + overrun},'
                f'"Stream {stream_index:05d} end-of-stream packet exceeds its fixed boundary by {overrun} byte(s)."'
            ]
        return bytes(rebuilt_stream), local_changed, local_skipped, local_report

    while pos + 8 <= len(data):
        section_start = pos
        section_language = read_u16_be(data, pos)
        stream_id = read_u16_be(data, pos + 2)
        length = read_u32_be(data, pos + 4)
        if length < 8 or section_start + length > len(data):
            if stream_sections:
                output.extend(data[stream_start:pos])
            output.extend(data[pos:])
            break
        section_type = stream_id & 0xFF
        section_end = section_start + length
        if section_type == 0xF0:
            original_end = min(len(data), align(section_end, 0x800))
            stream_blob, stream_changed, stream_skipped, stream_report = rebuild_stream(stream_sections, section_start, original_end)
            output.extend(stream_blob)
            changed += stream_changed
            skipped += stream_skipped
            report.extend(stream_report)
            pos = original_end
            stream_start = pos
            stream_index += 1
            stream_sections = []
        else:
            stream_sections.append((section_start, section_end, section_type, section_language))
            pos = section_end
    if stream_sections:
        output.extend(data[stream_start:])
    return bytes(output), changed, skipped, report

def rebuild_codec_segment(segment: bytes, replacements: dict[int, tuple[str, dict, int]]) -> tuple[bytes, int, int, list[str]]:
    report: list[str] = []
    if not replacements:
        return segment, 0, 0, report
    layout = read_gcx_text_layout(segment)
    if layout is None:
        return segment, 0, len(replacements), ['0,"codec",skipped,0,0,"Invalid GCX offsets"']
    start, text_start, font_start = layout
    entity_offsets = list(range(start + 16, text_start, 4))
    old_font_len = read_u32_le(segment, font_start) if font_start + 4 <= len(segment) else 0
    font_end = min(len(segment), font_start + 4 + old_font_len)
    original_font_blob = segment[font_start:font_end]
    tail = segment[font_end:]
    original_text_blob = segment[text_start:font_start]
    text_capacity = len(original_text_blob)
    raw_by_entity: list[bytes] = []
    entity_info: list[tuple[int, int, int]] = []
    for entry_offset in entity_offsets:
        offset_value = read_u32_le(segment, entry_offset)
        old_rel = offset_value & 0xFFFFFF
        old_text_offset = text_start + old_rel
        old_end = segment.find(b"\0", old_text_offset, font_start)
        if 0 <= old_rel < text_capacity and old_end >= 0:
            raw = segment[old_text_offset:old_end + 1]
        else:
            raw = b"\0"
        raw_by_entity.append(raw)
        entity_info.append((entry_offset, offset_value, old_rel))

    decoded_texts = [text_bytes_to_string(raw) for raw in raw_by_entity]
    protected_entities: set[int] = set(replacements)
    for _entity_index, (_text, entry, _line_number) in replacements.items():
        for span in entry.get("metadata", {}).get("codec_english_spans", []):
            if isinstance(span, (list, tuple)) and len(span) == 2:
                protected_entities.update(range(max(0, int(span[0])), min(len(entity_offsets), int(span[1]))))
    if protected_entities == set(replacements):
        for span_start, span_end in english_spans_from_texts(decoded_texts):
            protected_entities.update(range(max(0, span_start), min(len(entity_offsets), span_end)))
    if protected_entities == set(replacements):
        english_count = (len(entity_offsets) + CODEC_LANGUAGE_COUNT - 1) // CODEC_LANGUAGE_COUNT
        protected_entities.update(range(english_count))

    replacement_raws: dict[int, tuple[bytes, str, int, int]] = {}
    for entity_index, (text, _entry, line_number) in replacements.items():
        if not 0 <= entity_index < len(raw_by_entity):
            report.append(f'{line_number},"codec",skipped,0,0,"Entity index is outside this codec blob."')
            continue
        raw, warnings = encode_game_text(text, False)
        if warnings:
            report.append(
                f'{line_number},"codec",skipped,{len(raw_by_entity[entity_index])},{len(raw)},'
                f'"{"; ".join(dict.fromkeys(warnings))}"'
            )
            continue
        replacement_raws[entity_index] = (raw, "", line_number, len(raw_by_entity[entity_index]))

    if not replacement_raws:
        return segment, 0, len(replacements), report

    def uses_embedded_atlas(raw: bytes) -> bool:
        index = 0
        while index + 1 < len(raw):
            first = raw[index]
            if first == 0:
                break
            if 0x20 <= first <= 0x7E or first == 0x0A:
                index += 1
                continue
            if first == 0x0D and raw[index + 1] == 0x0A:
                index += 2
                continue
            if (first & 0x8C) == 0x8C or (first & 0x9C) == 0x90:
                return True
            index += 2
        return False

    # The tested builder drops the per-call kanji atlas only when no English
    # or translated line addresses it.  Its space then becomes available to
    # the text pool while the blob itself remains at its original offset.
    keep_atlas = any(
        uses_embedded_atlas(replacement_raws.get(index, (raw_by_entity[index], "", 0, 0))[0])
        for index in protected_entities
        if 0 <= index < len(raw_by_entity)
    )
    font_blob = original_font_blob if keep_atlas else b"\0\0\0\0"

    # Codec pools have the same externally addressed gaps as resident stage
    # GCX data.  Most are the leading bytes of the font boundary (F/FO/FON),
    # but they still are not owned by an entity and must remain at their exact
    # pool-relative offsets.  Freeze those spans and every unedited entity;
    # only edited or positively identified foreign strings release space.
    covered: list[tuple[int, int]] = []
    for raw, (_entry_offset, _offset_value, old_rel) in zip(raw_by_entity, entity_info, strict=True):
        if 0 <= old_rel < text_capacity:
            covered.append((old_rel, min(text_capacity, old_rel + len(raw))))
    covered.sort()
    reserved: list[tuple[int, int]] = []
    cursor = 0
    for span_start, span_end in covered:
        if span_start > cursor and any(original_text_blob[cursor:span_start]):
            reserved.append((cursor, span_start))
        cursor = max(cursor, span_end)
    if cursor < text_capacity and any(original_text_blob[cursor:]):
        reserved.append((cursor, text_capacity))

    target_raws: dict[int, bytes] = {
        entity_index: value[0] for entity_index, value in replacement_raws.items()
    }
    for entity_index, decoded in enumerate(decoded_texts):
        if entity_index not in protected_entities and looks_foreign_text(decoded):
            target_raws[entity_index] = b"\0"

    held = bytearray(text_capacity)
    for span_start, span_end in reserved:
        held[span_start:span_end] = b"\1" * (span_end - span_start)
    for entity_index, raw in enumerate(raw_by_entity):
        if entity_index in target_raws:
            continue
        old_rel = entity_info[entity_index][2]
        if 0 <= old_rel < text_capacity:
            span_end = min(text_capacity, old_rel + len(raw))
            held[old_rel:span_end] = b"\1" * (span_end - old_rel)

    free: list[list[int]] = []
    for index, flag in enumerate(held):
        if flag:
            continue
        if free and free[-1][1] == index:
            free[-1][1] = index + 1
        else:
            free.append([index, index + 1])

    text_blob = bytearray(original_text_blob)
    new_relative_offsets = [info[2] for info in entity_info]

    def settled(position: int, size: int) -> bool:
        return (
            position >= 0
            and position + size <= len(held)
            and all(held[position:position + size])
        )

    for entity_index in sorted(target_raws, key=lambda index: (-len(target_raws[index]), index)):
        raw = target_raws[entity_index]
        position = text_blob.find(raw)
        while position >= 0 and not settled(position, len(raw)):
            position = text_blob.find(raw, position + 1)
        if position < 0:
            span = next((item for item in free if item[1] - item[0] >= len(raw)), None)
            if span is None:
                position = len(text_blob)
                text_blob.extend(raw)
                held.extend(b"\1" * len(raw))
            else:
                position = span[0]
                text_blob[position:position + len(raw)] = raw
                held[position:position + len(raw)] = b"\1" * len(raw)
                span[0] += len(raw)
        new_relative_offsets[entity_index] = position

    new_offset_values = [
        (offset_value & 0xFF000000) | (new_relative_offsets[entity_index] & 0xFFFFFF)
        for entity_index, (_entry_offset, offset_value, _old_rel) in enumerate(entity_info)
    ]

    new_text_start_rel = 16 + len(entity_offsets) * 4
    new_font_start_rel = align(new_text_start_rel + len(text_blob), 4)
    old_font_start_rel = read_u32_le(segment, start + 12)
    old_block_length = read_u32_le(segment, start)
    block_delta = old_block_length - (old_font_start_rel + len(original_font_blob))
    new_block_length = new_font_start_rel + len(font_blob) + block_delta
    rebuilt = bytearray()
    rebuilt.extend(segment[:start])
    rebuilt.extend(struct.pack("<II", new_block_length, read_u32_le(segment, start + 4)))
    rebuilt.extend(struct.pack("<II", new_text_start_rel, new_font_start_rel))
    for value in new_offset_values:
        rebuilt.extend(struct.pack("<I", value))
    rebuilt.extend(text_blob)
    rebuilt.extend(b"\0" * (start + new_font_start_rel - len(rebuilt)))
    rebuilt.extend(font_blob)
    rebuilt.extend(tail)
    if len(rebuilt) > len(segment):
        overflow = len(rebuilt) - len(segment)
        return segment, 0, len(replacements), [
            f'0,"codec",skipped,{len(segment)},{len(rebuilt)},'
            f'"Codec blob outgrew its fixed slot by {overflow} bytes after blanking other languages and the unused atlas."'
        ]
    rebuilt.extend(b"\0" * (len(segment) - len(rebuilt)))

    new_pool_start = start + new_text_start_rel
    for span_start, span_end in reserved:
        if rebuilt[new_pool_start + span_start:new_pool_start + span_end] != original_text_blob[span_start:span_end]:
            return segment, 0, len(replacements), [
                '0,"codec",skipped,0,0,"Codec GCX reserved data changed during rebuild."'
            ]

    changed = 0
    skipped = len(replacements) - len(replacement_raws)
    for entity_index, (raw, message, line_number, old_size) in replacement_raws.items():
        if raw == raw_by_entity[entity_index]:
            continue
        changed += 1
        report.append(f'{line_number},"codec",patched,{old_size},{len(raw)},"{message}"')
    return bytes(rebuilt), changed, skipped, report

def rebuild_codec_dat(data: bytes, entries: list[dict], translations: list[str]) -> tuple[bytes, int, int, list[str]]:
    by_segment: dict[int, dict[int, tuple[str, dict, int]]] = {}
    for fallback_line_number, (entry, text) in enumerate(zip(entries, translations, strict=True), start=1):
        meta = entry.get("metadata", {})
        line_number = int(entry.get("line_number", fallback_line_number))
        segment = int(meta["segment_index"])
        entity = int(meta["entity_index"])
        by_segment.setdefault(segment, {})[entity] = (text, entry, line_number)
    starts = [start for start, _end in iter_gcx_segments(data)]
    if not starts:
        return data, 0, len(entries), ['0,"codec",skipped,0,0,"No GCX segments found"']
    output = bytearray()
    changed = 0
    skipped = 0
    report: list[str] = []
    last = 0
    for segment_index, start in enumerate(starts):
        end = starts[segment_index + 1] if segment_index + 1 < len(starts) else len(data)
        output.extend(data[last:start])
        segment, seg_changed, seg_skipped, seg_report = rebuild_codec_segment(data[start:end], by_segment.get(segment_index, {}))
        output.extend(segment)
        changed += seg_changed
        skipped += seg_skipped
        report.extend(seg_report)
        last = end
    output.extend(data[last:])
    return bytes(output), changed, skipped, report

def rebuild_gcx_text_pool(segment: bytes, replacements: dict[int, tuple[str, dict, int]], *, allow_growth: bool) -> tuple[bytes, int, int, list[str]]:
    report: list[str] = []
    if not replacements:
        return segment, 0, 0, report
    layout = read_gcx_text_layout(segment)
    if layout is None:
        return segment, 0, len(replacements), ['0,"stage",skipped,0,0,"Invalid GCX resident offsets"']
    start, text_start, font_start = layout
    entity_offsets = list(range(start + 16, text_start, 4))
    original_text_blob = segment[text_start:font_start]
    text_capacity = len(original_text_blob)
    font_blob = b""
    tail = b""
    if font_start + 4 <= len(segment):
        old_font_len = read_u32_le(segment, font_start)
        font_end = min(len(segment), font_start + 4 + old_font_len)
        font_blob = segment[font_start:font_end]
        tail = segment[font_end:]

    raw_by_entity: list[bytes] = []
    entity_info: list[tuple[int, int, int]] = []
    for entry_offset in entity_offsets:
        offset_value = read_u32_le(segment, entry_offset)
        old_rel = offset_value & 0xFFFFFF
        old_text_offset = text_start + old_rel
        old_end = segment.find(b"\0", old_text_offset, font_start)
        if 0 <= old_rel < text_capacity and old_end >= 0:
            raw = segment[old_text_offset:old_end + 1]
        else:
            raw = b"\0"
        raw_by_entity.append(raw)
        entity_info.append((entry_offset, offset_value, old_rel))

    decoded_texts = [text_bytes_to_string(raw) for raw in raw_by_entity]
    skipped = 0
    replacement_raws: dict[int, tuple[bytes, str, int]] = {}
    for entity_index, (text, _entry, line_number) in replacements.items():
        if not 0 <= entity_index < len(raw_by_entity):
            skipped += 1
            report.append(f'{line_number},"stage",skipped,0,0,"Entity index is outside this stage GCX."')
            continue
        raw, warnings = encode_game_text(text, False)
        message = "; ".join(dict.fromkeys(warnings))
        if warnings:
            skipped += 1
            report.append(f'{line_number},"stage",skipped,{len(raw_by_entity[entity_index])},{len(raw)},"{message}"')
            continue
        replacement_raws[entity_index] = (raw, message, line_number)

    if not replacement_raws:
        return segment, 0, skipped, report

    # GCX pools can contain non-text tables reached by offsets outside the
    # entity table.  Repacking the pool destroys those tables even if the GCX
    # keeps the same total size.  Keep all unedited and unreferenced data in
    # place, then allocate only edited strings into the holes they free.
    covered: list[tuple[int, int]] = []
    for raw, (_entry_offset, _offset_value, old_rel) in zip(raw_by_entity, entity_info, strict=True):
        if 0 <= old_rel < text_capacity:
            covered.append((old_rel, min(text_capacity, old_rel + len(raw))))
    covered.sort()
    reserved: list[tuple[int, int]] = []
    cursor = 0
    for span_start, span_end in covered:
        if span_start > cursor and any(original_text_blob[cursor:span_start]):
            reserved.append((cursor, span_start))
        cursor = max(cursor, span_end)
    if cursor < text_capacity and any(original_text_blob[cursor:]):
        reserved.append((cursor, text_capacity))

    protected_entities = set(replacement_raws)
    for _entity_index, (_text, entry, _line_number) in replacements.items():
        for span in entry.get("metadata", {}).get("stage_english_spans", []):
            if isinstance(span, (list, tuple)) and len(span) == 2:
                span_start = max(0, int(span[0]))
                span_end = min(len(entity_offsets), int(span[1]))
                protected_entities.update(range(span_start, span_end))

    target_raws: dict[int, bytes] = {
        entity_index: value[0] for entity_index, value in replacement_raws.items()
    }
    for entity_index, decoded in enumerate(decoded_texts):
        if entity_index not in protected_entities and looks_foreign_text(decoded):
            target_raws[entity_index] = b"\0"

    held = bytearray(text_capacity)
    for span_start, span_end in reserved:
        held[span_start:span_end] = b"\1" * (span_end - span_start)
    for entity_index, raw in enumerate(raw_by_entity):
        if entity_index in target_raws:
            continue
        old_rel = entity_info[entity_index][2]
        if 0 <= old_rel < text_capacity:
            span_end = min(text_capacity, old_rel + len(raw))
            held[old_rel:span_end] = b"\1" * (span_end - old_rel)

    free: list[list[int]] = []
    for index, flag in enumerate(held):
        if flag:
            continue
        if free and free[-1][1] == index:
            free[-1][1] = index + 1
        else:
            free.append([index, index + 1])

    text_blob = bytearray(original_text_blob)
    new_relative_offsets = [info[2] for info in entity_info]

    def settled(position: int, size: int) -> bool:
        return (
            position >= 0
            and position + size <= text_capacity
            and all(held[position:position + size])
        )

    for entity_index in sorted(target_raws, key=lambda index: (-len(target_raws[index]), index)):
        raw = target_raws[entity_index]
        position = text_blob.find(raw)
        while position >= 0 and not settled(position, len(raw)):
            position = text_blob.find(raw, position + 1)
        if position < 0:
            span = next((item for item in free if item[1] - item[0] >= len(raw)), None)
            if span is None:
                failed = len(replacement_raws)
                report.append(
                    f'0,"stage",skipped,{text_capacity},{sum(len(raw) for raw in target_raws.values())},'
                    '"Stage GCX fixed pool has no safe room; reserved script data was left untouched."'
                )
                return segment, 0, skipped + failed, report
            position = span[0]
            text_blob[position:position + len(raw)] = raw
            held[position:position + len(raw)] = b"\1" * len(raw)
            span[0] += len(raw)
        new_relative_offsets[entity_index] = position

    rebuilt = bytearray(segment)
    for entity_index, (entry_offset, offset_value, _old_rel) in enumerate(entity_info):
        value = (offset_value & 0xFF000000) | (new_relative_offsets[entity_index] & 0xFFFFFF)
        struct.pack_into("<I", rebuilt, entry_offset, value)
    rebuilt[text_start:font_start] = text_blob

    for span_start, span_end in reserved:
        if rebuilt[text_start + span_start:text_start + span_end] != original_text_blob[span_start:span_end]:
            return segment, 0, skipped + len(replacement_raws), [
                '0,"stage",skipped,0,0,"Stage GCX reserved script data changed during rebuild."'
            ]

    changed = 0
    for entity_index, (raw, message, line_number) in replacement_raws.items():
        if raw == raw_by_entity[entity_index]:
            continue
        changed += 1
        report.append(f'{line_number},"stage",patched,{len(raw_by_entity[entity_index])},{len(raw)},"{message}"')
    return bytes(rebuilt), changed, skipped, report

def rebuild_stage_resident_text_dat(data: bytes, entries: list[dict], translations: list[str]) -> tuple[bytes, int, int, list[str]]:
    from assets import iter_stage_resources, rebuild_stage_dat

    by_gcx_signature: dict[tuple[int, int, str], dict[int, dict[int, tuple[str, dict, int]]]] = {}
    by_inline_signature: dict[tuple[int, int, str], dict[int, tuple[str, dict, int]]] = {}
    by_legacy_gcx_ordinal: dict[tuple[int, int, int], dict[int, dict[int, tuple[str, dict, int]]]] = {}
    by_legacy_inline_ordinal: dict[tuple[int, int, int], dict[int, tuple[str, dict, int]]] = {}
    for fallback_line_number, (entry, text) in enumerate(zip(entries, translations, strict=True), start=1):
        meta = entry.get("metadata", {})
        resource_type = int(meta["resource_type"])
        resource_hash = int(meta["resource_hash"])
        stage_ordinal = int(meta["stage_ordinal"])
        line_number = int(entry.get("line_number", fallback_line_number))
        resource_sha = str(meta.get("resource_sha256", ""))
        signature = (resource_type, resource_hash, resource_sha)
        legacy_key = (stage_ordinal, resource_type, resource_hash)
        if entry.get("kind") == "stage_inline_ascii":
            inline_offset = int(meta.get("inline_offset", entry.get("offset", 0)))
            if resource_sha:
                by_inline_signature.setdefault(signature, {})[inline_offset] = (text, entry, line_number)
            else:
                by_legacy_inline_ordinal.setdefault(legacy_key, {})[inline_offset] = (text, entry, line_number)
            continue
        segment_index = int(meta["segment_index"])
        entity_index = int(meta["entity_index"])
        if resource_sha:
            by_gcx_signature.setdefault(signature, {}).setdefault(segment_index, {})[entity_index] = (text, entry, line_number)
        else:
            by_legacy_gcx_ordinal.setdefault(legacy_key, {}).setdefault(segment_index, {})[entity_index] = (text, entry, line_number)

    resources = list(iter_stage_resources(data) or [])
    replacements: dict[int, bytes] = {}
    changed = 0
    skipped = 0
    report: list[str] = []
    template_rebuilt: dict[tuple[str, tuple[int, int, str]], bytes] = {}

    for ordinal, resource in enumerate(resources):
        resource_type = int(resource["type"])
        resource_hash = int(resource["hash"])
        resource_data = resource["data"]
        resource_sha = sha256_bytes(resource_data)
        signature = (resource_type, resource_hash, resource_sha)
        legacy_key = (ordinal, resource_type, resource_hash)
        segment_replacements = by_gcx_signature.get(signature) or by_legacy_gcx_ordinal.get(legacy_key)
        inline_replacements = by_inline_signature.get(signature) or by_legacy_inline_ordinal.get(legacy_key)
        if not segment_replacements and not inline_replacements:
            continue

        template_key = ("mixed", signature)
        template = template_rebuilt.get(template_key)
        if template is not None:
            replacements[ordinal] = template
            continue

        output = bytearray(resource_data)
        resource_changed = 0
        resource_skipped = 0

        if segment_replacements:
            gcx_output = bytearray()
            last = 0
            for segment_index, (start, end) in enumerate(iter_gcx_segments(resource_data)):
                gcx_output.extend(resource_data[last:start])
                rebuilt, seg_changed, seg_skipped, seg_report = rebuild_gcx_text_pool(resource_data[start:end], segment_replacements.get(segment_index, {}), allow_growth=False)
                gcx_output.extend(rebuilt)
                resource_changed += seg_changed
                resource_skipped += seg_skipped
                report.extend(seg_report)
                last = end
            gcx_output.extend(resource_data[last:])
            output = gcx_output

        if inline_replacements:
            for inline_offset, (text, entry, line_number) in sorted(inline_replacements.items()):
                capacity = int(entry.get("capacity", 0))
                if inline_offset < 0 or capacity <= 0 or inline_offset + capacity > len(output):
                    resource_skipped += 1
                    report.append(f'{line_number},"stage_inline",skipped,0,0,"Inline stage string offset is outside the resource."')
                    continue
                raw, warnings = encode_game_text(text, False)
                message = "; ".join(dict.fromkeys(warnings))
                old_raw = bytes(output[inline_offset:inline_offset + capacity])
                if len(raw) > capacity:
                    resource_skipped += 1
                    report.append(f'{line_number},"stage_inline",skipped,{capacity},{len(raw)},"Inline script string is longer than its original slot; this string needs bytecode resizing before it can grow."')
                    continue
                padded = raw + b"\0" * (capacity - len(raw))
                if padded != old_raw:
                    output[inline_offset:inline_offset + capacity] = padded
                    resource_changed += 1
                    report.append(f'{line_number},"stage_inline",patched,{capacity},{len(raw)},"{message}"')

        if resource_changed:
            rebuilt_resource = bytes(output)
            replacements[ordinal] = rebuilt_resource
            template_rebuilt[template_key] = rebuilt_resource
            changed += resource_changed
        skipped += resource_skipped

    if replacements:
        rebuilt_stage, _stage_changed = rebuild_stage_dat(data, replacements)
        return rebuilt_stage, changed, skipped, report
    return data, changed, skipped, report

def import_text(source_path: Path, manifest_path: Path, text_path: Path, output_path: Path, log: Callable[[str], None] | None = None) -> tuple[Path, Path, int, int]:
    source = DiscSource(source_path)
    manifest = read_json(manifest_path)
    entries = manifest.get("entries", [])
    translated = load_manifest_translations(manifest, text_path)
    entries_by_file: dict[str, list[dict]] = {}
    translations_by_file: dict[str, list[str]] = {}
    for entry, text in zip(entries, translated, strict=True):
        if text == entry.get("original_text"):
            continue
        rel = normalize_disc_path(entry["source_file"])
        entries_by_file.setdefault(rel, []).append(entry)
        translations_by_file.setdefault(rel, []).append(text)

    files: dict[str, bytes] = {}
    report_rows = ["line,source_file,kind,status,original_bytes,new_bytes,message"]
    changed = 0
    skipped = 0
    for rel, file_entries in entries_by_file.items():
        data = source.read_file(rel)
        file_translations = translations_by_file[rel]
        leaf = rel.lower().rsplit("/", 1)[-1]
        if leaf in {"demo.dat", "movie.dat", "vox.dat"}:
            rebuilt, file_changed, file_skipped, file_report = rebuild_subtitle_dat(data, file_entries, file_translations)
        elif leaf == "codec.dat":
            rebuilt, file_changed, file_skipped, file_report = rebuild_codec_dat(data, file_entries, file_translations)
        elif leaf == "stage.dat":
            rebuilt, file_changed, file_skipped, file_report = rebuild_stage_resident_text_dat(data, file_entries, file_translations)
        else:
            rebuilt = data
            file_changed = 0
            file_skipped = len(file_entries)
            file_report = [f'0,"{leaf}",skipped,{len(data)},{len(data)},"Unsupported text container"']
        files[rel] = rebuilt
        changed += file_changed
        skipped += file_skipped
        for row in file_report:
            line_number, rest = row.split(",", 1)
            report_rows.append(f'{line_number},"{rel}",{rest}')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if source.is_directory:
        if output_path.suffix:
            output_root = output_path.with_suffix("")
        else:
            output_root = output_path
        if output_root.exists() and not output_root.is_dir():
            raise ToolkitError(f"Output path exists and is not a folder: {output_root}")
        for rel, data in files.items():
            source.write_file_to_directory(rel, data, output_root)
        produced = output_root
    else:
        produced = output_path
        if produced.suffix.lower() not in {".iso", ".gcm"} and not produced.name.lower().endswith(".nkit.iso"):
            produced = produced.with_suffix(".iso")
        source.patch_image_copy(files, produced)

    report_path = produced.parent / TEXT_IMPORT_REPORT_NAME
    report_path.write_text("\n".join(report_rows) + "\n", encoding="utf-8")
    if log:
        log(f"Patched {changed:,} lines; skipped {skipped:,}.")
    return produced, report_path, changed, skipped

