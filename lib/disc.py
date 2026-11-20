from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

from common import *

class DiscSource:
    def __init__(self, source: Path):
        self.source = source
        self.is_directory = source.is_dir()
        self.files: dict[str, DiscFile] = {}
        self.fst_offset: int | None = None
        self.fst_size: int | None = None
        if self.is_directory:
            self._index_directory()
        else:
            self._index_image()

    def _index_directory(self) -> None:
        for path in self.source.rglob("*"):
            if path.is_file():
                rel = normalize_disc_path(path.relative_to(self.source).as_posix())
                self.files[rel.lower()] = DiscFile(rel, 0, path.stat().st_size, None)

    def _index_image(self) -> None:
        with self.source.open("rb") as f:
            f.seek(0x424)
            fst_offset = struct.unpack(">I", f.read(4))[0]
            fst_size = struct.unpack(">I", f.read(4))[0]
            self.fst_offset = fst_offset
            self.fst_size = fst_size
            if fst_offset <= 0 or fst_size <= 0:
                raise ToolkitError("The selected file does not look like a GameCube image with a readable FST.")
            f.seek(fst_offset)
            fst = f.read(fst_size)
        if len(fst) < 12:
            raise ToolkitError("The GameCube FST is truncated.")
        count = read_u32_be(fst, 8)
        names_offset = count * 12
        if count <= 0 or names_offset >= len(fst):
            raise ToolkitError("The GameCube FST has an invalid entry table.")

        def entry_name(index: int) -> str:
            value = read_u32_be(fst, index * 12)
            name_offset = value & 0xFFFFFF
            if name_offset == 0 and index == 0:
                return ""
            return read_c_string(fst, names_offset + name_offset)

        def walk(index: int, prefix: str) -> int:
            end = read_u32_be(fst, index * 12 + 8)
            cursor = index + 1
            while cursor < end:
                value = read_u32_be(fst, cursor * 12)
                is_dir = bool(value >> 24)
                name = entry_name(cursor)
                path = f"{prefix}/{name}" if prefix else name
                if is_dir:
                    cursor = walk(cursor, path)
                else:
                    offset = read_u32_be(fst, cursor * 12 + 4)
                    size = read_u32_be(fst, cursor * 12 + 8)
                    rel = normalize_disc_path(path)
                    self.files[rel.lower()] = DiscFile(rel, offset, size, cursor * 12)
                    cursor += 1
            return end

        walk(0, "")

    def list_files(self) -> list[DiscFile]:
        return sorted(self.files.values(), key=lambda item: item.path.lower())

    def get(self, rel_path: str) -> DiscFile | None:
        rel_path = normalize_disc_path(rel_path).lower()
        direct = self.files.get(rel_path)
        if direct:
            return direct
        leaf = rel_path.rsplit("/", 1)[-1]
        matches = [file for key, file in self.files.items() if key.endswith("/" + leaf) or key == leaf]
        return matches[0] if len(matches) == 1 else None

    def read_file(self, file: DiscFile | str) -> bytes:
        if isinstance(file, str):
            resolved = self.get(file)
            if not resolved:
                raise ToolkitError(f"File not found in source: {file}")
            file = resolved
        if self.is_directory:
            return (self.source / file.path).read_bytes()
        with self.source.open("rb") as f:
            f.seek(file.offset)
            return f.read(file.size)

    def write_file_to_directory(self, rel_path: str, data: bytes, output_root: Path) -> Path:
        out = output_root / normalize_disc_path(rel_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    def patch_image_copy(self, patches_by_file: dict[str, bytes], output_image: Path) -> None:
        if self.is_directory:
            raise ToolkitError("patch_image_copy is only valid for disc images.")
        if self.fst_offset is None or self.fst_size is None:
            raise ToolkitError("The source image does not have an indexed FST.")
        resolved: list[tuple[DiscFile, bytes]] = []
        for rel_path, data in patches_by_file.items():
            file = self.get(rel_path)
            if not file:
                raise ToolkitError(f"File not found in source: {rel_path}")
            if file.fst_entry_offset is None:
                raise ToolkitError(f"{rel_path} has no FST entry to update.")
            resolved.append((file, data))

        ordered = sorted(self.files.values(), key=lambda item: item.offset)
        slots: dict[str, int] = {}
        for index, file in enumerate(ordered):
            following = ordered[index + 1].offset if index + 1 < len(ordered) else self.source.stat().st_size
            slots[file.path.lower()] = max(0, following - file.offset)
        tail = align(max((file.offset + file.size for file in ordered), default=0), 0x800)

        layout: list[tuple[DiscFile, bytes, int]] = []
        moved = False
        for file, data in resolved:
            if len(data) <= slots.get(file.path.lower(), file.size):
                new_offset = file.offset
            else:
                new_offset = tail
                tail = align(tail + len(data), 0x800)
                moved = True
            if new_offset + len(data) > self.source.stat().st_size:
                raise ToolkitError(
                    f"The disc image has no free room for {file.path}: "
                    f"{new_offset + len(data) - self.source.stat().st_size:,} additional bytes are required."
                )
            layout.append((file, data, new_offset))

        output_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source, output_image)
        with self.source.open("rb") as src:
            src.seek(self.fst_offset)
            fst = bytearray(src.read(self.fst_size))
        with output_image.open("r+b") as out:
            for file, data, new_offset in layout:
                out.seek(new_offset)
                out.write(data)
                if len(data) < file.size and new_offset == file.offset:
                    out.write(b"\0" * (file.size - len(data)))
                struct.pack_into(">I", fst, file.fst_entry_offset + 4, new_offset)
                struct.pack_into(">I", fst, file.fst_entry_offset + 8, len(data))
            out.seek(self.fst_offset)
            out.write(fst)
            if moved and ordered:
                data_start = min(file.offset for file in ordered)
                out.seek(0x430)
                out.write(struct.pack(">II", data_start, self.source.stat().st_size - data_start))

