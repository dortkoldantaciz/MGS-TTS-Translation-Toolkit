from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Iterable

from common import *
from assets import export_assets, import_assets
from text import export_text, import_text


def _load_disc_manifest(manifest_path: Path, disc_index: int) -> dict:
    manifest = read_json(manifest_path)
    for entry in manifest.get("entries", []):
        entry["disc_index"] = disc_index
    return manifest

def _entries_for_text_file(manifest: dict, text_file: str) -> list[dict]:
    return [entry for entry in manifest.get("entries", []) if entry.get("text_file") == text_file]

def _rename_manifest_text_file(manifest: dict, texts_dir: Path, old_name: str, new_name: str) -> None:
    old_text_file = f"texts/{old_name}"
    new_text_file = f"texts/{new_name}"
    if old_text_file == new_text_file:
        return
    touched = False
    for entry in manifest.get("entries", []):
        if entry.get("text_file") == old_text_file:
            entry["text_file"] = new_text_file
            touched = True
    if not touched:
        return
    old_path = texts_dir / old_name
    new_path = texts_dir / new_name
    if old_path.is_file() and not new_path.exists():
        old_path.replace(new_path)

def _refresh_text_files(manifest: dict) -> None:
    manifest["text_files"] = sorted({
        Path(entry.get("text_file", "")).name
        for entry in manifest.get("entries", [])
        if entry.get("text_file")
    })

def export_dual_disc_text(disc1_path: Path, disc2_path: Path, output_root: Path, log: Callable[[str], None] | None = None) -> int:
    text_root = output_root / "text_export"
    if text_root.exists():
        shutil.rmtree(text_root)
    _d1_dir, d1_manifest_path, d1_count = export_text(disc1_path, text_root, log)
    d1_manifest = _load_disc_manifest(d1_manifest_path, 1)
    texts_dir = text_root / "texts"

    with tempfile.TemporaryDirectory(prefix="mgsts_d2_text_") as tmp_name:
        tmp_root = Path(tmp_name) / "text_export"
        _d2_dir, d2_manifest_path, _d2_count = export_text(disc2_path, tmp_root, log)
        d2_manifest = _load_disc_manifest(d2_manifest_path, 2)
        d2_texts_dir = tmp_root / "texts"
        shared_files: list[str] = []
        split_files: list[str] = []
        d2_files = sorted({
            Path(entry.get("text_file", "")).name
            for entry in d2_manifest.get("entries", [])
            if entry.get("text_file")
        })
        for base_name in d2_files:
            d1_path = texts_dir / base_name
            d2_path = d2_texts_dir / base_name
            d2_entries = _entries_for_text_file(d2_manifest, f"texts/{base_name}")
            if not d2_entries or not d2_path.is_file():
                continue
            if d1_path.is_file() and d1_path.read_bytes() == d2_path.read_bytes():
                for entry in d2_entries:
                    entry["text_file"] = f"texts/{base_name}"
                d1_manifest["entries"].extend(d2_entries)
                shared_files.append(base_name)
                continue
            d1_entries = _entries_for_text_file(d1_manifest, f"texts/{base_name}")
            if d1_entries:
                d1_name = f"d1-{base_name}"
                _rename_manifest_text_file(d1_manifest, texts_dir, base_name, d1_name)
            d2_name = f"d2-{base_name}"
            shutil.copy2(d2_path, texts_dir / d2_name)
            for entry in d2_entries:
                entry["text_file"] = f"texts/{d2_name}"
            d1_manifest["entries"].extend(d2_entries)
            split_files.append(base_name)
        _refresh_text_files(d1_manifest)
        d1_manifest["dual_disc"] = True
        d1_manifest["disc_count"] = 2
        d1_manifest["disc2_text_scope"] = "full text manifest"
        d1_manifest["shared_disc_text_files"] = shared_files
        d1_manifest["split_disc_text_files"] = split_files
        write_json(d1_manifest_path, d1_manifest)
        return len(d1_manifest.get("entries", []))


def filtered_text_manifest(source_manifest: Path, output_dir: Path, disc_index: int) -> Path:
    manifest = read_json(source_manifest)
    entries = [entry for entry in manifest.get("entries", []) if int(entry.get("disc_index", 1)) == disc_index]
    manifest["entries"] = entries
    manifest["text_files"] = sorted({entry.get("text_file", "").replace("texts/", "") for entry in entries if entry.get("text_file")})
    out = output_dir / f"disc{disc_index}_{TEXT_MANIFEST_NAME}"
    write_json(out, manifest)
    return out


def has_disc2_text(export_root: Path) -> bool:
    manifest_path = export_root / "text_export" / TEXT_MANIFEST_NAME
    if not manifest_path.is_file():
        return False
    manifest = read_json(manifest_path)
    return any(int(entry.get("disc_index", 1)) == 2 for entry in manifest.get("entries", []))

def export_all(source_path: Path, output_root: Path, modes: Iterable[str], log: Callable[[str], None] | None = None, disc2_path: Path | None = None) -> dict[str, int]:
    mode_set = set(modes)
    selected = [mode for mode in ("text", "texture", "font") if mode in mode_set]
    if disc2_path is not None and "texture" in selected:
        selected.remove("texture")
        if log:
            log("Texture export is disabled when Disc 2 is included.")
    if not selected:
        raise ToolkitError("Select at least one export type.")
    output_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    if "text" in selected:
        if disc2_path is not None:
            counts["text"] = export_dual_disc_text(source_path, disc2_path, output_root, log)
        else:
            _text_dir, _manifest, count = export_text(source_path, output_root / "text_export", log)
            counts["text"] = count
    for mode in ("texture", "font"):
        if mode in selected:
            _manifest, count = export_assets(source_path, output_root / f"{mode}_export", mode, log)
            counts[mode] = count
    return counts

def detect_all_import_jobs(export_root: Path) -> list[tuple[str, Path, Path]]:
    jobs: list[tuple[str, Path, Path]] = []
    text_root = export_root / "text_export"
    text_manifest = text_root / TEXT_MANIFEST_NAME
    if text_manifest.is_file():
        jobs.append(("text", text_manifest, text_root))
    for mode in ("texture", "font"):
        asset_root = export_root / f"{mode}_export"
        manifest = asset_root / ASSET_MANIFEST_NAME
        if manifest.is_file():
            jobs.append((mode, manifest, asset_root))
    return jobs

def normalize_output_path_for_source(source_path: Path, output_path: Path, label: str) -> Path:
    if source_path.is_dir():
        return output_path if not output_path.suffix else output_path.with_suffix("")
    if output_path.suffix.lower() in {".iso", ".gcm"} or output_path.name.lower().endswith(".nkit.iso"):
        return output_path
    return output_path / f"{source_path.stem}.{label}.patched.iso"

def label_output_file(output_path: Path, label: str) -> Path:
    lower_name = output_path.name.lower()
    if lower_name.endswith(".nkit.iso"):
        stem = output_path.name[:-len(".nkit.iso")]
        suffix = ".nkit.iso"
    else:
        suffix = output_path.suffix or ".iso"
        stem = output_path.name[:-len(suffix)] if output_path.name.endswith(suffix) else output_path.stem
    return output_path.with_name(f"{stem}_{label}{suffix}")

def stepped_output_path(final_output: Path, step_index: int, source_is_directory: bool) -> Path:
    if source_is_directory:
        return final_output.parent / f"{final_output.name}.__all_step_{step_index:02d}"
    suffix = final_output.suffix or ".iso"
    stem = final_output.name[:-len(suffix)] if final_output.name.endswith(suffix) else final_output.stem
    return final_output.with_name(f"{stem}.__all_step_{step_index:02d}{suffix}")

def import_all(source_path: Path, export_root: Path, output_path: Path, log: Callable[[str], None] | None = None, disc2_path: Path | None = None) -> tuple[Path, dict[str, int], dict[str, int]]:
    jobs = detect_all_import_jobs(export_root)
    if not jobs:
        raise ToolkitError(f"No text/texture/font export manifests were found in: {export_root}")
    if has_disc2_text(export_root) and disc2_path is None:
        raise ToolkitError("This export includes Disc 2 text. Select Game Disc 2 before importing.")
    if has_disc2_text(export_root) and disc2_path is not None:
        with tempfile.TemporaryDirectory(prefix="mgsts_dual_import_") as tmp_name:
            tmp_dir = Path(tmp_name)
            text_manifest = export_root / "text_export" / TEXT_MANIFEST_NAME
            d1_manifest = filtered_text_manifest(text_manifest, tmp_dir, 1)
            d2_manifest = filtered_text_manifest(text_manifest, tmp_dir, 2)
            # Reuse the normal importer twice so each disc keeps its own FST and file layout.
            d1_export = export_root / "text_export"
            if output_path.suffix:
                final_output = label_output_file(output_path, "disc1")
                d2_output = label_output_file(output_path, "disc2")
            else:
                final_output = normalize_output_path_for_source(source_path, output_path, "all_disc1")
                d2_output = output_path / f"{disc2_path.stem}.all.patched.iso"
            asset_jobs = [(mode, manifest, root) for mode, manifest, root in jobs if mode != "text"]
            d1_text_output = stepped_output_path(final_output, 0, source_path.is_dir()) if asset_jobs else final_output
            d2_text_output = stepped_output_path(d2_output, 0, disc2_path.is_dir()) if asset_jobs else d2_output
            d1_produced, d1_report, d1_changed, d1_skipped = import_text(source_path, d1_manifest, d1_export, d1_text_output, log)
            d1_report_copy = d1_report.with_name(d1_report.stem + "_disc1" + d1_report.suffix)
            shutil.copy2(d1_report, d1_report_copy)
            d2_produced, d2_report, d2_changed, d2_skipped = import_text(disc2_path, d2_manifest, d1_export, d2_text_output, log)
            d2_report_copy = d2_report.with_name(d2_report.stem + "_disc2" + d2_report.suffix)
            shutil.copy2(d2_report, d2_report_copy)
            if log:
                log(f"Disc 1 text report: {d1_report_copy}")
                log(f"Disc 2 text report: {d2_report_copy}")
                log(f"Disc 2 text output: {d2_produced}")
            changed_by_mode = {"text": d1_changed, "disc2_text": d2_changed}
            skipped_by_mode = {"text": d1_skipped, "disc2_text": d2_skipped}
            # Font and any other detected asset manifests belong to the build,
            # not to Disc 1. Apply the same matching-hash jobs to both discs;
            # each importer resolves targets against that disc's own stage.dat.
            temp_outputs: list[Path] = []

            def apply_disc_assets(current_source: Path, final: Path, source_is_directory: bool, key_prefix: str) -> Path:
                if asset_jobs:
                    temp_outputs.append(current_source)
                for index, (mode, manifest, input_root) in enumerate(asset_jobs):
                    step_output = final if index == len(asset_jobs) - 1 else stepped_output_path(final, index + 1, source_is_directory)
                    produced, asset_changed, asset_skipped = import_assets(current_source, manifest, input_root, step_output, log)
                    key = f"{key_prefix}{mode}"
                    changed_by_mode[key] = changed_by_mode.get(key, 0) + asset_changed
                    skipped_by_mode[key] = skipped_by_mode.get(key, 0) + asset_skipped
                    if index < len(asset_jobs) - 1:
                        temp_outputs.append(produced)
                    current_source = produced
                return current_source

            d1_final = apply_disc_assets(d1_produced, final_output, source_path.is_dir(), "")
            d2_final = apply_disc_assets(d2_produced, d2_output, disc2_path.is_dir(), "disc2_")
            if log and asset_jobs:
                log(f"Disc 1 assets patched output: {d1_final}")
                log(f"Disc 2 assets patched output: {d2_final}")
            for temp in temp_outputs:
                try:
                    if temp.is_dir():
                        shutil.rmtree(temp)
                    elif temp.is_file():
                        temp.unlink()
                except OSError:
                    pass
            return final_output, changed_by_mode, skipped_by_mode
    final_output = normalize_output_path_for_source(source_path, output_path, "all")
    source_is_directory = source_path.is_dir()
    current_source = source_path
    temp_outputs: list[Path] = []
    changed_by_mode: dict[str, int] = {}
    skipped_by_mode: dict[str, int] = {}
    for index, (mode, manifest, input_root) in enumerate(jobs):
        step_output = final_output if index == len(jobs) - 1 else stepped_output_path(final_output, index + 1, source_is_directory)
        if log:
            log(f"Importing {mode} from {input_root}")
        if mode == "text":
            produced, report, changed, skipped = import_text(current_source, manifest, input_root, step_output, log)
            if log:
                log(f"Text report: {report}")
        else:
            produced, changed, skipped = import_assets(current_source, manifest, input_root, step_output, log)
        changed_by_mode[mode] = changed_by_mode.get(mode, 0) + changed
        skipped_by_mode[mode] = skipped_by_mode.get(mode, 0) + skipped
        if index < len(jobs) - 1:
            temp_outputs.append(produced)
        current_source = produced
    for temp in temp_outputs:
        try:
            if temp.is_dir():
                shutil.rmtree(temp)
            elif temp.is_file():
                temp.unlink()
        except OSError:
            pass
    return final_output, changed_by_mode, skipped_by_mode

