# MGS The Twin Snakes Translation Toolkit

A toolkit for translating the text and graphics of **Metal Gear Solid: The Twin Snakes** (GameCube). It exports the game's text to editable TXT files and its textures and fonts to PNG files, then rebuilds a patched game disc or folder.

## Workflow

1. Open the toolkit and select a game disc ISO (`.iso`, `.gcm`, `.nkit.iso`) or an extracted disc folder as the source. Disc 1 and Disc 2 are both supported.
2. Open the **All** tab and choose **Export**. Select which types to export: **Text**, **Texture**, and **Font**, and check **Include Disc 2** if needed.
3. Translate the exported files:
   - Text is written to `mgs_twin_snakes_text.txt`. Keep its `.manifest.json` file next to it and keep each entry on one physical line. Use `|` for a line break, `/n` for a codec line break, `\n` for a literal newline, and `<EMPTY>` for an empty entry.
   - Textures and fonts are exported as PNG files with a `manifest.json`. Edit the PNGs while keeping their names.
4. Open the **All** tab and choose **Import**. Point it at the export root folder and pick an output folder or ISO. The toolkit patches only changed files and writes an import report.
5. Text, textures, and fonts can also be exported and imported individually using their own tabs.

## Files

Keep these together in the same folder:

- `mgs_tts_translation_toolkit.py` or the compiled executable
- The `lib/` folder with its Python modules

## Requirements

- Python 3.10 or newer when running the source script. No third-party packages are required.

## Credits

- **al6uiz** - took reference from TwinSnakesTools.
- **abuali129** - took reference from Metal-Gear-Solid-The-Twin-Snakes-GC-translation-tools.
- **gdkchan** - took reference from MGSDATool.
