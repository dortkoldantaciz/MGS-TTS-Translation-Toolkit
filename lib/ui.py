from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Callable, Iterable

from all_ops import export_all, import_all, normalize_output_path_for_source
from assets import export_assets, import_assets
from common import *
from font import font_raw_from_png, png_from_font_raw
from png import payload_from_png_rgba, png_rgba_from_payload
from text import export_text, import_text, string_to_text_bytes, text_bytes_to_string

class ToolkitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("820x860")
        self.minsize(760, 860)
        self.configure(bg="#f6f7f9")
        self.option_add("*takeFocus", 0)
        self._build_style()
        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT_ROOT))
        self.all_input_var = tk.StringVar()
        self.disc2_source_var = tk.StringVar()
        self.disc2_import_source_var = tk.StringVar()
        self.include_disc2_var = tk.BooleanVar(value=False)
        self.all_mode_vars = {
            "text": tk.BooleanVar(value=True),
            "texture": tk.BooleanVar(value=True),
            "font": tk.BooleanVar(value=True),
        }
        self.text_manifest_var = tk.StringVar()
        self.text_file_var = tk.StringVar()
        self.asset_manifest_vars: dict[str, tk.StringVar] = {}
        self.asset_input_vars: dict[str, tk.StringVar] = {}
        self._build_ui()
        self.after(0, self._center_window)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 10), background="#f6f7f9", foreground="#161a22", focuscolor="#ffffff")
        style.configure("TFrame", background="#f6f7f9")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat", borderwidth=0)
        style.configure("TLabel", background="#f6f7f9")
        style.configure("Panel.TLabel", background="#ffffff")
        style.configure("TEntry", padding=(8, 7), fieldbackground="#ffffff", bordercolor="#d9dee7", lightcolor="#d9dee7", darkcolor="#d9dee7")
        style.map("TEntry", bordercolor=[("focus", "#d9dee7")], lightcolor=[("focus", "#d9dee7")], darkcolor=[("focus", "#d9dee7")])
        style.configure("TCombobox", padding=(8, 7), fieldbackground="#ffffff", bordercolor="#d9dee7", arrowcolor="#4b5563")
        style.map("TCombobox", bordercolor=[("focus", "#d9dee7")], fieldbackground=[("readonly", "#ffffff")])
        style.configure("TButton", padding=(14, 8), background="#ffffff", bordercolor="#d9dee7", lightcolor="#d9dee7", darkcolor="#d9dee7")
        style.map("TButton", background=[("active", "#f1f5f9"), ("focus", "#ffffff")], bordercolor=[("focus", "#d9dee7")])
        style.configure("Accent.TButton", background="#202632", foreground="#ffffff", bordercolor="#202632", lightcolor="#202632", darkcolor="#202632")
        style.map("Accent.TButton", background=[("active", "#111827"), ("focus", "#202632")], foreground=[("active", "#ffffff"), ("focus", "#ffffff")])
        style.configure("TNotebook", background="#f6f7f9", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), background="#eef1f5", borderwidth=1, bordercolor="#cfd6e1", lightcolor="#cfd6e1", darkcolor="#cfd6e1")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff"), ("active", "#f8fafc")], bordercolor=[("selected", "#b8c2d1"), ("active", "#c6ceda")])
        style.configure("Horizontal.TProgressbar", background="#202632", troughcolor="#e9edf5")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(header, text="Made by d" + chr(0x00F6) + "rtkoldantaciz - v1", foreground="#657083").pack(side="right", pady=(6, 0))

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self._build_all_tab()
        self._build_text_tab()
        self._build_asset_tab("Texture", "texture")
        self._build_asset_tab("Font", "font")

        log_panel = ttk.Frame(root, style="Panel.TFrame", padding=10)
        log_panel.pack(fill="both", pady=(12, 0))
        ttk.Label(log_panel, text="Log", style="Panel.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.progress = ttk.Progressbar(log_panel, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(6, 6))
        self.log = tk.Text(log_panel, height=13, borderwidth=0, background="#fbfcff", foreground="#1f2937", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _path_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, command: Callable[[], None], button_text: str = "Select") -> ttk.Label:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=4)
        label_widget = ttk.Label(row, text=label, width=24, style="Panel.TLabel")
        label_widget.pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text=button_text, command=command).pack(side="left")
        return label_widget

    def _operation_options(self, parent: ttk.Frame) -> ttk.Label:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        panel.pack(fill="x", pady=(0, 14))
        source_label = self._path_row(panel, "Game Disc 1 or 2", self.source_var, self._browse_source)
        self._path_row(panel, "Output Folder / ISO", self.output_var, self._browse_output)
        return source_label

    def _center_window(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_text_tab(self) -> None:
        outer = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(outer, text="Text")
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        export_tab = ttk.Frame(notebook, padding=14)
        notebook.add(export_tab, text="Export")
        self._operation_options(export_tab)
        ttk.Button(export_tab, text="Export Text", style="Accent.TButton", command=lambda: self._run_threaded(self._export_text_action)).pack(anchor="w")

        import_tab = ttk.Frame(notebook, padding=14)
        notebook.add(import_tab, text="Import")
        self._operation_options(import_tab)
        self._path_row(import_tab, "Text Manifest", self.text_manifest_var, lambda: self._browse_file(self.text_manifest_var, [("JSON manifest", "*.json")]))
        self._path_row(import_tab, "Text Export Folder", self.text_file_var, lambda: self._browse_dir(self.text_file_var))
        ttk.Button(import_tab, text="Import Text", style="Accent.TButton", command=lambda: self._run_threaded(self._import_text_action)).pack(anchor="w", pady=(12, 0))

    def _build_all_tab(self) -> None:
        outer = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(outer, text="All")
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        export_tab = ttk.Frame(notebook, padding=14)
        notebook.add(export_tab, text="Export")
        self.all_export_source_label = self._operation_options(export_tab)
        options = ttk.Frame(export_tab, style="Panel.TFrame", padding=14)
        options.pack(fill="x", pady=(0, 14))
        ttk.Checkbutton(options, text="Text", variable=self.all_mode_vars["text"]).pack(side="left", padx=(0, 18))
        self.texture_all_check = ttk.Checkbutton(options, text="Texture", variable=self.all_mode_vars["texture"])
        self.texture_all_check.pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="Font", variable=self.all_mode_vars["font"]).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="Include Disc 2", variable=self.include_disc2_var, command=self._toggle_disc2_export).pack(side="left")
        self.disc2_export_row = ttk.Frame(export_tab, style="Panel.TFrame", padding=(14, 0, 14, 14))
        self._path_row(self.disc2_export_row, "Game Disc 2", self.disc2_source_var, lambda: self._browse_file(self.disc2_source_var, [("GameCube images", "*.iso *.gcm *.nkit.iso")]))
        self.all_export_button = ttk.Button(export_tab, text="Export Selected Types", style="Accent.TButton", command=lambda: self._run_threaded(self._export_all_action))
        self.all_export_button.pack(anchor="w")

        import_tab = ttk.Frame(notebook, padding=14)
        notebook.add(import_tab, text="Import")
        self._operation_options(import_tab)
        self._path_row(import_tab, "Export Root Folder", self.all_input_var, lambda: self._browse_dir(self.all_input_var))
        self._path_row(import_tab, "Game Disc 2 If Exported\n(Optional)", self.disc2_import_source_var, lambda: self._browse_file(self.disc2_import_source_var, [("GameCube images", "*.iso *.gcm *.nkit.iso")]))
        ttk.Button(import_tab, text="Import Detected Exports", style="Accent.TButton", command=lambda: self._run_threaded(self._import_all_action)).pack(anchor="w", pady=(12, 0))
        self._toggle_disc2_export()

    def _build_asset_tab(self, label: str, mode: str) -> None:
        outer = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(outer, text=label)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.asset_manifest_vars[mode] = tk.StringVar()
        self.asset_input_vars[mode] = tk.StringVar()

        export_tab = ttk.Frame(notebook, padding=14)
        notebook.add(export_tab, text="Export")
        self._operation_options(export_tab)
        ttk.Button(export_tab, text=f"Export {label}", style="Accent.TButton", command=lambda m=mode: self._run_threaded(lambda: self._export_asset_action(m))).pack(anchor="w")

        import_tab = ttk.Frame(notebook, padding=14)
        notebook.add(import_tab, text="Import")
        self._operation_options(import_tab)
        self._path_row(import_tab, f"{label} Manifest", self.asset_manifest_vars[mode], lambda m=mode: self._browse_file(self.asset_manifest_vars[m], [("JSON manifest", "*.json")]))
        self._path_row(import_tab, f"{label} Export Folder", self.asset_input_vars[mode], lambda m=mode: self._browse_dir(self.asset_input_vars[m]))
        ttk.Button(import_tab, text=f"Import {label}", style="Accent.TButton", command=lambda m=mode: self._run_threaded(lambda: self._import_asset_action(m))).pack(anchor="w", pady=(12, 0))

    def _browse_source(self) -> None:
        path = filedialog.askopenfilename(title="Select Game Disc 1 or 2", filetypes=[("GameCube images", "*.iso *.gcm *.nkit.iso"), ("All files", "*.*")])
        if not path:
            folder = filedialog.askdirectory(title="Or Select an Extracted Disc Folder")
            path = folder
        if path:
            self.source_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_var.set(path)

    def _browse_file(self, var: tk.StringVar, filetypes: list[tuple[str, str]]) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes + [("All files", "*.*")])
        if path:
            var.set(path)

    def _browse_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _source_path(self) -> Path:
        value = self.source_var.get().strip()
        if not value:
            raise ToolkitError("Select a disc image or extracted disc root first.")
        path = Path(value)
        if not path.exists():
            raise ToolkitError(f"Source path does not exist: {path}")
        return path

    def _output_path(self) -> Path:
        value = self.output_var.get().strip() or str(DEFAULT_OUTPUT_ROOT)
        return Path(value)

    def _log(self, message: str) -> None:
        self.after(0, lambda: (self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n"), self.log.see("end")))

    def _run_threaded(self, action: Callable[[], None]) -> None:
        def runner() -> None:
            self.after(0, lambda: (self.progress.configure(mode="indeterminate"), self.progress.start(12)))
            try:
                action()
            except Exception as exc:
                trace = traceback.format_exc()
                self._log(trace)
                self.after(0, lambda: messagebox.showerror(APP_NAME, str(exc)))
            finally:
                self.after(0, lambda: (self.progress.stop(), self.progress.configure(mode="determinate", value=0)))
        threading.Thread(target=runner, daemon=True).start()


    def _toggle_disc2_export(self) -> None:
        if getattr(self, "disc2_export_row", None) is None:
            return
        if self.include_disc2_var.get():
            self.all_mode_vars["texture"].set(False)
            self.texture_all_check.state(["disabled"])
            self.all_export_source_label.configure(text="Game Disc 1")
            self.disc2_export_row.pack(fill="x", pady=(0, 8), before=self.all_export_button)
        else:
            self.texture_all_check.state(["!disabled"])
            self.all_export_source_label.configure(text="Game Disc 1 or 2")
            self.disc2_export_row.pack_forget()

    def _selected_all_modes(self) -> list[str]:
        modes = [mode for mode, var in self.all_mode_vars.items() if var.get()]
        if self.include_disc2_var.get() and "texture" in modes:
            modes.remove("texture")
        return modes

    def _export_all_action(self) -> None:
        out_root = self._output_path()
        disc2_path = Path(self.disc2_source_var.get().strip()) if self.include_disc2_var.get() else None
        if self.include_disc2_var.get() and (disc2_path is None or not disc2_path.exists()):
            raise ToolkitError("Select Game Disc 2 before exporting both discs.")
        counts = export_all(self._source_path(), out_root, self._selected_all_modes(), self._log, disc2_path=disc2_path)
        self.all_input_var.set(str(out_root))
        text_export = out_root / "text_export"
        if text_export.is_dir():
            self.text_file_var.set(str(text_export))
            self.text_manifest_var.set(str(text_export / TEXT_MANIFEST_NAME))
        for mode in ("texture", "font"):
            mode_root = out_root / f"{mode}_export"
            if mode_root.is_dir():
                self.asset_manifest_vars.setdefault(mode, tk.StringVar()).set(str(mode_root / ASSET_MANIFEST_NAME))
                self.asset_input_vars.setdefault(mode, tk.StringVar()).set(str(mode_root))
        summary = ", ".join(f"{mode}: {count:,}" for mode, count in counts.items())
        self._log(f"All export ready: {out_root}")
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Exported {summary}."))

    def _import_all_action(self) -> None:
        source = self._source_path()
        export_root = Path(self.all_input_var.get().strip() or self._output_path())
        output = normalize_output_path_for_source(source, self._output_path(), "all")
        disc2_path = Path(self.disc2_import_source_var.get().strip()) if self.disc2_import_source_var.get().strip() else None
        produced, changed, skipped = import_all(source, export_root, output, self._log, disc2_path=disc2_path)
        changed_text = ", ".join(f"{mode}: {count:,}" for mode, count in changed.items()) or "none"
        skipped_text = ", ".join(f"{mode}: {count:,}" for mode, count in skipped.items()) or "none"
        self._log(f"Patched output: {produced}")
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Imported changes: {changed_text}\nSkipped: {skipped_text}"))

    def _export_text_action(self) -> None:
        out_dir = self._output_path() / "text_export"
        text_dir, manifest_path, count = export_text(self._source_path(), out_dir, self._log)
        self.text_file_var.set(str(text_dir))
        self.text_manifest_var.set(str(manifest_path))
        self._log(f"Text export ready: {text_dir}")
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Exported {count:,} text lines."))

    def _import_text_action(self) -> None:
        output = self._output_path()
        if not output.suffix and not self._source_path().is_dir():
            output = output / f"{self._source_path().stem}.patched.iso"
        produced, report, changed, skipped = import_text(self._source_path(), Path(self.text_manifest_var.get()), Path(self.text_file_var.get()), output, self._log)
        self._log(f"Patched output: {produced}")
        self._log(f"Report: {report}")
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Patched {changed:,} lines; skipped {skipped:,}."))

    def _export_asset_action(self, mode: str) -> None:
        out_dir = self._output_path() / f"{mode}_export"
        manifest, count = export_assets(self._source_path(), out_dir, mode, self._log)
        self.asset_manifest_vars[mode].set(str(manifest))
        self.asset_input_vars[mode].set(str(out_dir))
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Exported {count:,} {mode} assets."))

    def _import_asset_action(self, mode: str) -> None:
        output = self._output_path()
        if not output.suffix and not self._source_path().is_dir():
            output = output / f"{self._source_path().stem}.{mode}.patched.iso"
        produced, changed, skipped = import_assets(self._source_path(), Path(self.asset_manifest_vars[mode].get()), Path(self.asset_input_vars[mode].get()), output, self._log)
        self._log(f"Patched output: {produced}")
        self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Imported {changed:,} {mode} assets; skipped {skipped:,}."))

def run_self_test(ui: bool = False) -> None:
    assert unescape_text_line(escape_text_line("a\nb\tc\\d")) == "a|b\tc\\d"
    assert text_bytes_to_string(b"A\r\nB\0") == "A\\nB"
    assert text_bytes_to_string(b"A\x80\x7CB\0") == "A/nB"
    assert string_to_text_bytes("A\\nB")[0] == b"A\r\nB"
    assert string_to_text_bytes("A/nB")[0] == b"A\x80\x7CB"
    encoded, warnings = string_to_text_bytes("HELLO|" + chr(0x00C7) + chr(0x0131))
    assert not warnings
    assert text_bytes_to_string(encoded + b"\0") == "HELLO|" + chr(0x00C7) + chr(0x0131)
    png = png_from_font_raw(b"\0" * (6 * 24))
    assert png.startswith(b"\x89PNG")
    payload_png = png_rgba_from_payload(b"abc123")
    tmp_payload = WORK_DIR / "_selftest_payload.png"
    tmp_font = WORK_DIR / "_selftest_font.png"
    try:
        tmp_payload.write_bytes(payload_png)
        assert payload_from_png_rgba(tmp_payload) == b"abc123"
        font_raw = bytes([0xFF] * (6 * 24))
        tmp_font.write_bytes(png_from_font_raw(font_raw))
        assert font_raw_from_png(tmp_font, 1) == font_raw
    finally:
        if tmp_payload.exists():
            tmp_payload.unlink()
        if tmp_font.exists():
            tmp_font.unlink()
    if ui:
        app = ToolkitApp()
        app.update_idletasks()
        assert app.tabs.index("end") == 4
        def widget_texts(widget: tk.Widget) -> Iterable[str]:
            try:
                text = widget.cget("text")
                if text:
                    yield str(text)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                yield from widget_texts(child)
        assert any(("Made by d" + chr(0x00F6) + "rtkoldantaciz") in text for text in widget_texts(app))
        app.destroy()
    print("self-test passed")

