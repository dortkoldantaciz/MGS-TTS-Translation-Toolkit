from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

SHORT_APP_NAME = "MGS TTS Translation Toolkit"
APP_NAME = "MGS The Twin Snakes Translation Toolkit"
LONG_APP_NAME = "Metal Gear Solid The Twin Snakes Translation Toolkit"
APP_VERSION = "1.0.0"
WORK_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = WORK_DIR / "output"
LIB_DIR = WORK_DIR / "lib"
if LIB_DIR.is_dir() and str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

TEXT_EXPORT_NAME = "mgs_twin_snakes_text.txt"
TEXT_MANIFEST_NAME = "mgs_twin_snakes_text.manifest.json"
TEXT_IMPORT_REPORT_NAME = "mgs_twin_snakes_text_import_report.csv"
ASSET_MANIFEST_NAME = "manifest.json"
TPL_MAGIC_BYTES = b"\x00\x20\xAF\x30"

SUBTITLE_LANGUAGE_ENGLISH = 1
SUBTITLE_LANGUAGE_JAPANESE = 7
TEXT_TARGET_NAMES = {
    "demo.dat",
    "movie.dat",
    "vox.dat",
    "codec.dat",
    "shared/movie.dat",
    "shared/vox.dat",
    "shared/codec.dat",
}
TEXTURE_NAME_RE = re.compile(r"(?:tex|texture|face|font|title|icon|menu|photo|image|pic)", re.IGNORECASE)
TEXTURE_EXTS = {".tpl", ".txp", ".bti", ".png", ".dds", ".tm2"}
MGS2_TEXTURE_FLATLIST = Path("Ara" + chr(0x00E7) + "lar") / "MGS2" / "GameData" / "GameData" / "BP_FlatlistTextureMapping.txt"
MGS2_STAGE_EXT_BY_TYPE = {
    0x01: "bin", 0x02: "gcx", 0x03: "tri", 0x04: "mdh", 0x05: "mds", 0x06: "lt2",
    0x07: "cv2", 0x08: "mtar", 0x09: "mtsq", 0x0A: "far", 0x0B: "mtcm", 0x0C: "geom",
    0x0D: "mdl", 0x0F: "nav", 0x10: "cvd", 0x11: "eft", 0x12: "zon", 0x13: "rpd",
}
CODEC_LANGUAGE_COUNT = 6
CODEC_LANGUAGE_BLOCKS = {SUBTITLE_LANGUAGE_ENGLISH: 0, SUBTITLE_LANGUAGE_JAPANESE: 5}
NON_ENGLISH_WORD_RE = re.compile(
    r"\b(?:vous|pour|avec|etre|" + chr(0x00EA) + r"tre|dans|qui|quoi|appelle|moi|peu|importe|"
    r"nicht|kannst|oder|dein|deine|puoi|devi|allora|quando|cosa|sono|"
    r"esta|est" + chr(0x00E1) + r"|puedes|debes|tienes|para|pero)\b",
    re.IGNORECASE,
)
NON_ENGLISH_MARKERS = "".join(chr(value) for value in [
    0x00E9, 0x00E8, 0x00EA, 0x00EB, 0x00E0, 0x00E2, 0x00EE, 0x00EF, 0x00F4, 0x00F6,
    0x00F9, 0x00FB, 0x00FC, 0x00E7, 0x00C9, 0x00C8, 0x00C0, 0x00C2, 0x00C7, 0x00C4,
    0x00D6, 0x00DC, 0x00DF, 0x00BF, 0x00A1, 0x00E1, 0x00ED, 0x00F3, 0x00FA, 0x00F1,
    0x00C1, 0x00CD, 0x00D3, 0x00DA, 0x00D1, 0x00C3, 0x00E3,
])
WESTERN_CODE_BY_CHAR = {
    chr(0x00C4): 0x1F01, chr(0x00C5): 0x1F02, chr(0x00C7): 0x1F03, chr(0x00C9): 0x1F04, chr(0x00D1): 0x1F05,
    chr(0x00D6): 0x1F06, chr(0x00DC): 0x1F07, chr(0x00E1): 0x1F08, chr(0x00E0): 0x1F09, chr(0x00E2): 0x1F0A,
    chr(0x00E4): 0x1F0B, chr(0x00E3): 0x1F0C, chr(0x00E5): 0x1F0D, chr(0x00E7): 0x1F0E, chr(0x00E9): 0x1F0F,
    chr(0x00E8): 0x1F10, chr(0x00EA): 0x1F11, chr(0x00EB): 0x1F12, chr(0x00ED): 0x1F13, chr(0x00EC): 0x1F14,
    chr(0x00EE): 0x1F15, chr(0x00EF): 0x1F16, chr(0x00F1): 0x1F17, chr(0x00F3): 0x1F18, chr(0x00F2): 0x1F19,
    chr(0x00F4): 0x1F1A, chr(0x00F6): 0x1F1B, chr(0x00F5): 0x1F1C, chr(0x00FA): 0x1F1D, chr(0x00F9): 0x1F1E,
    chr(0x00FB): 0x1F1F, chr(0x00FC): 0x1F20, chr(0x00DF): 0x1F28, chr(0x00C0): 0x1F4C, chr(0x00C3): 0x1F4D,
    chr(0x00D5): 0x1F4E, chr(0x0152): 0x1F4F, chr(0x0153): 0x1F50, chr(0x00FF): 0x1F59, chr(0x0178): 0x1F5A,
    chr(0x00C2): 0x1F66, chr(0x00CA): 0x1F67, chr(0x00C1): 0x1F68, chr(0x00CB): 0x1F69, chr(0x00C8): 0x1F6A,
    chr(0x00CD): 0x1F6B, chr(0x00CE): 0x1F6C, chr(0x00CF): 0x1F6D, chr(0x00CC): 0x1F6E, chr(0x00D3): 0x1F6F,
    chr(0x00D4): 0x1F70, chr(0x00D2): 0x1F72, chr(0x00DA): 0x1F73, chr(0x00DB): 0x1F74, chr(0x00D9): 0x1F75,
    chr(0x0131): 0x1F76,
    chr(0x00AE): 0x1F29, chr(0x221E): 0x8C01,
}
# The resident CNP page exposes 126 Mac Roman slots starting at 0x1F01.
# Keep the few engine-specific encodings above, then fill every ordinary slot
# so a translator can redraw any original glyph and type that same character
# in TXT without an external table file.
_CNP_CODE_BY_CHAR = {
    bytes((0x80 + index,)).decode("mac_roman"): 0x1F01 + index
    for index in range(126)
}
for _character, _code in _CNP_CODE_BY_CHAR.items():
    WESTERN_CODE_BY_CHAR.setdefault(_character, _code)
CHAR_BY_WESTERN_CODE = {value: key for key, value in _CNP_CODE_BY_CHAR.items()}
CHAR_BY_WESTERN_CODE.update({value: key for key, value in WESTERN_CODE_BY_CHAR.items()})
EMPTY_TEXT_TOKEN = "<EMPTY>"
LINE_FEED_TEXT_TOKEN = "|"
CODEC_NEWLINE_TEXT_TOKEN = "/n"
CRLF_TEXT_TOKEN = "\\n"
NEWLINE_TEXT_TOKEN = LINE_FEED_TEXT_TOKEN


class ToolkitError(RuntimeError):
    pass


@dataclass
class DiscFile:
    path: str
    offset: int
    size: int
    fst_entry_offset: int | None = None


@dataclass
class TextPatch:
    source_file: str
    offset: int
    capacity: int
    original_text: str
    kind: str
    metadata: dict


@dataclass
class TextEntry:
    text: str
    manifest: dict


@dataclass
class AssetPatch:
    source_file: str
    offset: int | None
    capacity: int | None
    output_name: str
    kind: str
    metadata: dict


def normalize_disc_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def safe_file_part(text: str) -> str:
    text = normalize_disc_path(text)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "asset"


def find_project_root() -> Path:
    tools_dir = "Ara" + chr(0x00E7) + "lar"
    for parent in [WORK_DIR, *WORK_DIR.parents]:
        if (parent / "Work").is_dir() and (parent / tools_dir).is_dir():
            return parent
    return WORK_DIR


def escape_text_line(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text == "":
        return EMPTY_TEXT_TOKEN
    crlf_placeholder = "{__MGSTS_CRLF__}"
    text = text.replace(CRLF_TEXT_TOKEN, crlf_placeholder)
    text = text.replace("\\", "\\\\").replace("\n", NEWLINE_TEXT_TOKEN).replace("\t", "\\t")
    return text.replace(crlf_placeholder, CRLF_TEXT_TOKEN)


def unescape_text_line(text: str) -> str:
    if text == EMPTY_TEXT_TOKEN:
        return ""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\" or index + 1 >= len(text):
            out.append(char)
            index += 1
            continue
        marker = text[index + 1]
        if marker == "n":
            out.append("\\n")
        elif marker == "t":
            out.append("\t")
        elif marker == "r":
            out.append("\r")
        elif marker == "\\":
            out.append("\\")
        else:
            out.append("\\")
            out.append(marker)
        index += 2
    return "".join(out)


def align(value: int, boundary: int) -> int:
    return ((value + boundary - 1) // boundary) * boundary


def read_c_string(data: bytes, offset: int, encoding: str = "shift_jis") -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode(encoding, errors="replace")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_u16_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def read_u32_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def read_u16_le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def read_u32_le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def text_bytes_to_string(raw: bytes) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        value = raw[i]
        if value == 0:
            break
        if value == 0x0D and i + 1 < len(raw) and raw[i + 1] == 0x0A:
            out.append(CRLF_TEXT_TOKEN)
            i += 2
            continue
        if value == 0x0A:
            out.append(LINE_FEED_TEXT_TOKEN)
            i += 1
            continue
        if 0x20 <= value <= 0x7E:
            out.append(chr(value))
            i += 1
            continue
        if i + 1 >= len(raw):
            out.append(f"\\x{value:02X}")
            i += 1
            continue
        pair = (value << 8) | raw[i + 1]
        if pair == 0x807C:
            out.append(CODEC_NEWLINE_TEXT_TOKEN)
        elif pair == 0xA07B:
            out.append("[B]")
        elif pair == 0xC07D:
            out.append("[/B]")
        elif pair in CHAR_BY_WESTERN_CODE:
            out.append(CHAR_BY_WESTERN_CODE[pair])
        elif value == 0x80:
            out.append(chr(raw[i + 1]))
        else:
            out.append(f"\\x{pair:04X}")
        i += 2
    return "".join(out)


def string_to_text_bytes(text: str, *, use_double_ascii: bool = False) -> tuple[bytes, list[str]]:
    out = bytearray()
    warnings: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("[RN]", i):
            out.extend(b"\r\n")
            i += 4
            continue
        if text.startswith("[NL]", i):
            out.extend(b"\x80\x7C")
            i += 4
            continue
        if text.startswith("[N]", i):
            out.append(0x0A)
            i += 3
            continue
        if text.startswith(CRLF_TEXT_TOKEN, i):
            out.extend(b"\r\n")
            i += len(CRLF_TEXT_TOKEN)
            continue
        if text.startswith(CODEC_NEWLINE_TEXT_TOKEN, i):
            out.extend(b"\x80\x7C")
            i += len(CODEC_NEWLINE_TEXT_TOKEN)
            continue
        if text.startswith(LINE_FEED_TEXT_TOKEN, i):
            out.append(0x0A)
            i += len(LINE_FEED_TEXT_TOKEN)
            continue
        if text.startswith("[B]", i):
            out.extend(b"\xA0\x7B")
            i += 3
            continue
        if text.startswith("[/B]", i):
            out.extend(b"\xC0\x7D")
            i += 4
            continue
        if text.startswith("\\x", i):
            match = re.match(r"\\x([0-9A-Fa-f]{2}|[0-9A-Fa-f]{4})", text[i:])
            if match:
                hex_value = match.group(1)
                number = int(hex_value, 16)
                if len(hex_value) == 2:
                    out.append(number)
                else:
                    out.extend(struct.pack(">H", number))
                i += len(match.group(0))
                continue
        char = text[i]
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            if use_double_ascii:
                out.extend((0x80, code))
            else:
                out.append(code)
        elif char in WESTERN_CODE_BY_CHAR:
            out.extend(struct.pack(">H", WESTERN_CODE_BY_CHAR[char]))
        else:
            out.append(ord("?"))
            warnings.append(f"Unsupported character {char!r} was replaced with '?'. Use a custom \\xNNNN code and matching font data if needed.")
        i += 1
    return bytes(out), warnings
