#!/usr/bin/env python
"""Launcher for Metal Gear Solid The Twin Snakes Translation Toolkit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORK_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
LIB_DIR = WORK_DIR / "lib"
if LIB_DIR.is_dir() and str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from all_ops import export_all, import_all
from assets import export_assets, import_assets
from common import APP_NAME, ToolkitError
from text import export_text, import_text
from ui import ToolkitApp, run_self_test


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--export-text", nargs=2, metavar=("DISC", "OUTPUT_DIR"))
    parser.add_argument("--import-text", nargs=4, metavar=("DISC", "MANIFEST", "TEXT_FOLDER", "OUTPUT"))
    parser.add_argument("--export-assets", nargs=3, metavar=("MODE", "DISC", "OUTPUT_DIR"), choices=None)
    parser.add_argument("--import-assets", nargs=4, metavar=("DISC", "MANIFEST", "INPUT_DIR", "OUTPUT"))
    parser.add_argument("--export-all", nargs="+", metavar="ARG")
    parser.add_argument("--import-all", nargs=3, metavar=("DISC", "EXPORT_ROOT", "OUTPUT"))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ui-smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        run_self_test(ui=args.ui_smoke)
        return 0
    if args.export_text:
        text_dir, manifest, count = export_text(Path(args.export_text[0]), Path(args.export_text[1]), print)
        print(f"Exported {count} lines\n{text_dir}\n{manifest}")
        return 0
    if args.import_text:
        produced, report, changed, skipped = import_text(Path(args.import_text[0]), Path(args.import_text[1]), Path(args.import_text[2]), Path(args.import_text[3]), print)
        print(f"Patched {changed}, skipped {skipped}\n{produced}\n{report}")
        return 0
    if args.export_assets:
        mode, source, output = args.export_assets
        manifest, count = export_assets(Path(source), Path(output), mode, print)
        print(f"Exported {count} {mode} assets\n{manifest}")
        return 0
    if args.import_assets:
        source, manifest, input_dir, output = args.import_assets
        produced, changed, skipped = import_assets(Path(source), Path(manifest), Path(input_dir), Path(output), print)
        print(f"Imported {changed} assets, skipped {skipped}\n{produced}")
        return 0
    if args.export_all:
        if len(args.export_all) < 2:
            raise ToolkitError("--export-all requires DISC OUTPUT_DIR [text texture font]")
        source, output, *modes = args.export_all
        counts = export_all(Path(source), Path(output), modes or ["text", "texture", "font"], print)
        print("Exported " + ", ".join(f"{mode}: {count}" for mode, count in counts.items()))
        return 0
    if args.import_all:
        source, export_root, output = args.import_all
        produced, changed, skipped = import_all(Path(source), Path(export_root), Path(output), print)
        print(f"Imported all\n{produced}\nchanged={changed}\nskipped={skipped}")
        return 0
    ToolkitApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
