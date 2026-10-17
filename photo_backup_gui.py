"""Desktop interface for the photo backup and duplicate-review tool."""

from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from photo_backup import (
    BackupError,
    DEFAULT_TYPE_ORDER,
    HIDE_CONSOLE_WINDOW,
    IMAGE_EXTENSIONS,
    KEEP_CRITERIA,
    VIDEO_EXTENSIONS,
    KeepPreference,
    TaskCancelled,
    TaskControl,
    apply_report,
    app_resource_path,
    find_ffmpeg_executable,
    find_exiftool_executable,
    plan_keep_decisions,
    preview,
    read_media_properties,
)
from translations import GUI_ZH


PAGE_SIZE = 400
DECISION_OPTIONS = {
    "copy": ("copy", "skip"),
    "review_source_visual": ("copy", "skip"),
    "move_exact": ("quarantine", "keep"),
    "review_target_exact": ("quarantine", "keep"),
    "review_target_visual": ("quarantine", "keep"),
    "collision": ("keep",),
    "skip": ("skip",),
    "live_pair_info": ("keep",),
}
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def extract_video_preview(path: Path) -> tuple[Any | None, str | None]:
    executable = find_ffmpeg_executable()
    if executable is None:
        return None, "FFmpeg was not found in PATH."

    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=720:-2",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=30, check=False, **HIDE_CONSOLE_WINDOW
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"FFmpeg could not extract a frame: {error}"
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        return None, detail[-1] if detail else "No video frame could be decoded."

    try:
        from PIL import Image

        with Image.open(BytesIO(result.stdout)) as image:
            return image.convert("RGB").copy(), None
    except Exception as error:
        return None, f"Could not decode the extracted video frame: {error}"


def format_beijing_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).astimezone(BEIJING_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M:%S UTC+08:00"
        )
    text = str(value).strip()
    if text.startswith(("0000:00:00", "0000-00-00")):
        return ""
    normalized = text.replace("Z", "+00:00")
    if len(normalized) >= 19 and normalized[4:5] == ":" and normalized[7:8] == ":":
        normalized = f"{normalized[:4]}-{normalized[5:7]}-{normalized[8:]}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        parsed = parsed.astimezone(BEIJING_TIMEZONE)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC+08:00")


class PhotoBackupApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Photo Archive Manager")
        icon_path = app_resource_path("icon.ico")
        if icon_path.is_file():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        initial_width = min(1420, max(900, screen_width - 32))
        initial_height = min(900, max(640, screen_height - 80))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(min(900, initial_width), min(620, initial_height))
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.task_control = TaskControl()
        self.header: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self.visible_rows: list[int] = []
        self.current_page = 0
        self.current_report: Path | None = None
        self.preview_images: dict[str, Any] = {}
        self.preview_photos: dict[str, Any] = {}
        self.metadata_cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}
        self.metadata_request_id = 0
        self.preview_request_id = 0

        self.mode = tk.StringVar(value="backup")
        self.language = tk.StringVar(value="zh")
        self.source = tk.StringVar()
        self.target = tk.StringVar()
        self.quarantine = tk.StringVar()
        self.report = tk.StringVar()
        self.audit_log = tk.StringVar()
        self._auto_values = {"quarantine": "", "report": "", "log": ""}
        self.visual_check = tk.BooleanVar(value=True)
        self.content_id_check = tk.BooleanVar(value=find_exiftool_executable() is not None)
        self.phash_distance = tk.IntVar(value=4)
        self.allow_quarantine = tk.BooleanVar(value=False)
        self.allow_visual_quarantine = tk.BooleanVar(value=False)
        self.allow_video_quarantine = tk.BooleanVar(value=False)
        self.confirm_changes = tk.BooleanVar(value=False)
        self.search = tk.StringVar()
        self.operation_filter = tk.StringVar(value="All entries")
        self.selected_decision = tk.StringVar()
        self.bulk_decision = tk.StringVar(value="quarantine")
        self.bulk_pending_only = tk.BooleanVar(value=True)
        self.last_bulk_changes: list[tuple[int, dict[str, Any]]] = []
        default_keep = KeepPreference()
        self.keep_criteria = [tk.StringVar(value=KEEP_CRITERIA[name]) for name in default_keep.criteria]
        self.keep_type_order = tk.StringVar(value=", ".join(default_keep.type_order))
        self.preview_progress_text = tk.StringVar(value="预览：未开始")
        self.apply_progress_text = tk.StringVar(value="应用 / 自动选择：未开始")

        self._configure_style()
        self._build_ui()
        self.root.title(self._tr("Photo Archive Manager"))
        self._refresh_default_paths()
        self._update_mode_controls()
        self._capture_localizable_widgets()
        self._apply_language()
        self.source.trace_add("write", lambda *_: self._refresh_default_paths())
        self.target.trace_add("write", lambda *_: self._refresh_default_paths())
        self._poll_after_id: str | None = None
        self._maximize_after_id: str | None = None
        self.root.bind("<Destroy>", self._cancel_pending_callbacks, add="+")
        self._poll_after_id = self.root.after(100, self._poll_events)
        self._maximize_after_id = self.root.after_idle(self._maximize_window)

    def _maximize_window(self) -> None:
        self._maximize_after_id = None
        try:
            self.root.deiconify()
            self.root.state("zoomed")
        except tk.TclError:
            return

    def _cancel_pending_callbacks(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        for callback_id in (self._poll_after_id, self._maximize_after_id):
            if callback_id is not None:
                try:
                    self.root.after_cancel(callback_id)
                except tk.TclError:
                    pass

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background="#eef2ef")
        style.configure("Header.TFrame", background="#173c35")
        style.configure("Header.TLabel", background="#173c35", foreground="#f4f7f3")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtle.TLabel", foreground="#5b6863")
        style.configure("Link.TButton", foreground="#176b5b", font=("Segoe UI", 9, "underline"))
        style.map("Link.TButton", foreground=[("active", "#0b4f43")])
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def _tr(self, text: str) -> str:
        return GUI_ZH.get(text, text) if self.language.get() == "zh" else text

    def _tr_progress(self, text: str) -> str:
        if self.language.get() != "zh":
            return text
        for prefix in (
            "Phase 1/5: scanning folders...",
            "Phase 2/5: Live Photo identifiers",
            "Phase 3/5: hashing target files",
            "Phase 4/5: hashing source files",
            "Phase 5/5: comparing images",
            "Preparing plan",
            "Validating paths and permissions",
            "Applying changes",
            "Auto-pick",
            "Preview running",
            "Apply running",
            "Auto-pick running",
            "Preview complete: ",
            "Apply complete: copied ",
        ):
            if text == prefix or text.startswith(prefix + " "):
                return self._tr(prefix) + text[len(prefix):]
        return self._tr(text)

    def _to_english(self, value: str, choices: tuple[str, ...] | list[str] | None = None) -> str:
        candidates = choices if choices is not None else GUI_ZH.keys()
        for candidate in candidates:
            if self._tr(candidate) == value:
                return candidate
        return value

    def _capture_localizable_widgets(self) -> None:
        self._localizable_widgets: list[tuple[tk.Widget, str]] = []
        self._tree_headings: list[tuple[str, str]] = []

        def visit(widget: tk.Widget) -> None:
            try:
                if "text" in widget.keys():
                    text = widget.cget("text")
                    if text:
                        self._localizable_widgets.append((widget, str(text)))
            except tk.TclError:
                pass
            if isinstance(widget, ttk.Treeview):
                for column in widget["columns"]:
                    heading = widget.heading(column, "text")
                    self._tree_headings.append((str(column), str(heading)))
            if isinstance(widget, ttk.Combobox):
                widget._gui_original_values = tuple(widget.cget("values"))
            for child in widget.winfo_children():
                visit(child)

        visit(self.root)

    def _toggle_language(self) -> None:
        old_filter = self._to_english(self.operation_filter.get(), tuple(self.operation_filter_box._gui_original_values))
        old_bulk = self._to_english(self.bulk_decision.get(), ("copy", "skip", "quarantine", "keep"))
        old_selected = self._to_english(self.selected_decision.get())
        self.language.set("en" if self.language.get() == "zh" else "zh")
        self.operation_filter.set(old_filter)
        self.bulk_decision.set(old_bulk)
        self.selected_decision.set(old_selected)
        self._apply_language()
        self._refresh_rows()

    def _apply_language(self) -> None:
        for widget, original in self._localizable_widgets:
            try:
                widget.configure(text=self._tr(original))
            except tk.TclError:
                continue
        for widget, _original in self._localizable_widgets:
            if not isinstance(widget, ttk.Combobox):
                continue
            original_values = tuple(getattr(widget, "_gui_original_values", ()))
            widget.configure(values=tuple(self._tr(str(value)) for value in original_values))
        for column, original in self._tree_headings:
            self.table.heading(column, text=self._tr(original))
        self.operation_filter.set(
            self._tr(self._to_english(self.operation_filter.get(), tuple(self.operation_filter_box._gui_original_values)))
        )
        self.bulk_decision.set(
            self._tr(self._to_english(self.bulk_decision.get(), ("copy", "skip", "quarantine", "keep")))
        )
        keep_labels = tuple(KEEP_CRITERIA.values())
        for variable in self.keep_criteria:
            variable.set(self._tr(self._to_english(variable.get(), keep_labels)))
        self.language_button.configure(text=self._tr("Switch to English" if self.language.get() == "zh" else "Switch to Chinese"))
        self.root.title(self._tr("Photo Archive Manager"))
        self.status.set(self._tr("Language switched; report values are unchanged."))
        self.preview_progress_text.set(self._tr("Preview: idle"))
        self.apply_progress_text.set(self._tr("Apply / auto-pick: idle"))
        self._refresh_task_buttons()
        self.community_message.set(self._tr("If Photo Archive Manager is useful to you, please consider giving it a star on GitHub. Help maintaining and improving the project is always welcome."))
        self._render_page()
        selected = self.table.selection()
        if selected:
            self._selection_changed()


    def _build_ui(self) -> None:
        self.root.configure(background="#eef2ef")
        shell = ttk.Frame(self.root, style="App.TFrame", padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="Header.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Photo Archive Manager", style="Header.TLabel", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Preview first  |  SHA-256 identity  |  reviewed moves only",
            style="Header.TLabel",
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky="e")
        self.language_button = ttk.Button(header, text="Language: 中文", command=self._toggle_language)
        self.language_button.grid(row=0, column=2, sticky="e", padx=(12, 0))

        body = tk.PanedWindow(
            shell,
            orient=tk.HORIZONTAL,
            sashwidth=9,
            sashrelief="raised",
            showhandle=True,
            handlesize=12,
            handlepad=4,
            background="#b9c6bf",
            borderwidth=0,
            relief="flat",
        )
        body.grid(row=1, column=0, sticky="nsew")
        settings = ttk.Frame(body, padding=(0, 0, 10, 0), width=390)
        results = ttk.Frame(body)
        body.add(settings, minsize=350, width=390, stretch="never")
        body.add(results, minsize=620, stretch="always")
        self.body_split = body
        self._build_settings(settings)
        self._build_results(results)

        self.status = tk.StringVar(value=self._tr("Choose a scan mode and folder, then create a preview."))
        footer = ttk.Label(shell, textvariable=self.status, style="Subtle.TLabel", anchor="w", padding=(3, 8, 3, 0))
        footer.grid(row=2, column=0, sticky="ew")

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(5, weight=1)
        mode_box = ttk.LabelFrame(parent, text="Scan mode", padding=10)
        mode_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Radiobutton(
            mode_box,
            text="Compare source folder to target folder",
            value="backup",
            variable=self.mode,
            command=self._update_mode_controls,
        ).pack(anchor="w", pady=2)
        ttk.Radiobutton(
            mode_box,
            text="Find duplicates inside one folder",
            value="folder",
            variable=self.mode,
            command=self._update_mode_controls,
        ).pack(anchor="w", pady=2)

        paths = ttk.LabelFrame(parent, text="Folders and reports", padding=10)
        paths.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        paths.columnconfigure(0, weight=1)
        self._path_row(paths, 0, "Source / folder to scan", self.source, self._browse_source)
        self._path_row(paths, 2, "Target folder", self.target, self._browse_target)
        self._path_row(paths, 4, "Quarantine folder", self.quarantine, self._browse_quarantine)
        self._path_row(paths, 6, "Review report (JSONL)", self.report, self._browse_report)
        self._path_row(paths, 8, "Action log (JSONL)", self.audit_log, self._browse_log)
        self.target_entry = self._row_entries["target"]

        options = ttk.LabelFrame(parent, text="Preview options", padding=10)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        options.columnconfigure(0, weight=1)
        ttk.Checkbutton(options, text="Find visual-similarity candidates (pHash)", variable=self.visual_check).grid(
            row=0, column=0, sticky="w", pady=2
        )
        ttk.Checkbutton(
            options,
            text="Read Live Photo ContentIdentifier (ExifTool)",
            variable=self.content_id_check,
        ).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Label(options, text="pHash review distance (0-64)").grid(row=2, column=0, sticky="w", pady=(8, 2))
        ttk.Spinbox(options, from_=0, to=64, textvariable=self.phash_distance, width=8).grid(
            row=3, column=0, sticky="w"
        )

        actions = ttk.LabelFrame(parent, text="Apply safeguards", padding=10)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(actions, text="Allow quarantine moves (master permission)", variable=self.allow_quarantine).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(actions, text="Allow reviewed visual-match quarantine", variable=self.allow_visual_quarantine).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(actions, text="Allow reviewed video quarantine", variable=self.allow_video_quarantine).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(actions, text="Confirm filesystem changes", variable=self.confirm_changes).pack(
            anchor="w", pady=2
        )

        buttons = ttk.Frame(parent)
        buttons.grid(row=4, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        self.preview_button = ttk.Button(buttons, text="Create preview", style="Accent.TButton", command=self._start_preview)
        self.preview_button.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.load_button = ttk.Button(buttons, text="Load report", command=self._load_report_dialog)
        self.load_button.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=3)
        self.save_button = ttk.Button(buttons, text="Save decisions", command=self._save_report, state="disabled")
        self.save_button.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.apply_button = ttk.Button(buttons, text="Apply reviewed plan", command=self._confirm_apply, state="disabled")
        self.apply_button.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=3)
        self.open_quarantine_button = ttk.Button(buttons, text="Open quarantine folder", command=self._open_quarantine_folder)
        self.open_quarantine_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)
        task_buttons = ttk.Frame(buttons)
        task_buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)
        task_buttons.columnconfigure((0, 1), weight=1)
        self.pause_button = ttk.Button(task_buttons, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.stop_button = ttk.Button(task_buttons, text="Stop", command=self._stop_task, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        doc_buttons = ttk.Frame(buttons)
        doc_buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=3)
        doc_buttons.columnconfigure((0, 1), weight=1)
        self.open_report_button = ttk.Button(doc_buttons, text="Open review report", command=self._open_review_report)
        self.open_report_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.open_log_button = ttk.Button(doc_buttons, text="Open review log", command=self._open_review_log)
        self.open_log_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        community = ttk.Frame(parent)
        community.grid(row=5, column=0, sticky="sew", pady=(12, 2))
        community.columnconfigure(0, weight=1)
        self.community_message = tk.StringVar()
        ttk.Label(
            community,
            textvariable=self.community_message,
            style="Subtle.TLabel",
            wraplength=350,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.project_link_button = ttk.Button(
            community,
            text="github.com/zny05/PhotoArchiveManager",
            style="Link.TButton",
            command=self._open_project_page,
        )
        self.project_link_button.grid(row=1, column=0, sticky="w")

    def _open_project_page(self) -> None:
        webbrowser.open("https://github.com/zny05/PhotoArchiveManager", new=2)

    def _open_document(self, variable: tk.StringVar, title: str) -> None:
        text = variable.get().strip()
        path = Path(text) if text else None
        if path is None or not path.is_file():
            messagebox.showinfo(self._tr(title), self._tr("Document is not available yet.") + (f"\n{path}" if path else ""), parent=self.root)
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except (AttributeError, OSError) as error:
            messagebox.showerror(self._tr("Could not open document"), str(error), parent=self.root)

    def _open_review_report(self) -> None:
        self._open_document(self.report, "Open review report")

    def _open_review_log(self) -> None:
        self._open_document(self.audit_log, "Open review log")

    def _build_results(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        workspace = tk.PanedWindow(
            parent,
            orient=tk.VERTICAL,
            sashwidth=9,
            sashrelief="raised",
            showhandle=True,
            handlesize=12,
            handlepad=4,
            background="#b9c6bf",
            borderwidth=0,
            relief="flat",
        )
        workspace.grid(row=0, column=0, sticky="nsew")
        table_area = ttk.Frame(workspace)
        detail_area = ttk.Frame(workspace)
        detail_area.columnconfigure(0, weight=1)
        detail_area.rowconfigure(1, weight=1)
        workspace.add(table_area, minsize=170, stretch="always")
        workspace.add(detail_area, minsize=320, stretch="always")
        self.results_split = workspace

        table_area.columnconfigure(0, weight=1)
        table_area.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(table_area)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Filter").grid(row=0, column=0, padx=(0, 6))
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.operation_filter,
            values=("All entries", "Needs review", "copy", "skip", "move_exact", "review_target_exact", "review_source_visual", "review_target_visual", "collision", "live_pair_info"),
            state="readonly",
            width=24,
        )
        self.operation_filter_box = filter_box
        filter_box.grid(row=0, column=1, sticky="w")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_rows())
        ttk.Label(toolbar, text="Search paths / notes").grid(row=0, column=2, padx=(14, 6))
        search_entry = ttk.Entry(toolbar, textvariable=self.search, width=30)
        search_entry.grid(row=0, column=3, sticky="e")
        self.search.trace_add("write", lambda *_: self.root.after_idle(self._refresh_rows))

        table_frame = ttk.Frame(table_area)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("decision", "operation", "source", "match", "distance")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.table.heading("decision", text="Decision")
        self.table.heading("operation", text="Action")
        self.table.heading("source", text="File")
        self.table.heading("match", text="Matched item")
        self.table.heading("distance", text="pHash")
        self.table.column("decision", width=105, minwidth=90, stretch=False)
        self.table.column("operation", width=155, minwidth=125, stretch=False)
        self.table.column("source", width=360, minwidth=160)
        self.table.column("match", width=360, minwidth=160)
        self.table.column("distance", width=70, minwidth=55, stretch=False, anchor="center")
        self.table.tag_configure("pending", background="#fff1ce")
        self.table.tag_configure("quarantine", foreground="#8b3424")
        self.table.tag_configure("copy", foreground="#1d5e46")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.table.bind("<<TreeviewSelect>>", self._selection_changed)

        pager = ttk.Frame(table_area, padding=(0, 6))
        pager.grid(row=2, column=0, sticky="ew")
        self.prev_button = ttk.Button(pager, text="Previous", command=lambda: self._change_page(-1), state="disabled")
        self.prev_button.pack(side="left")
        self.page_label = ttk.Label(pager, text="No report loaded", padding=(8, 4))
        self.page_label.pack(side="left")
        self.next_button = ttk.Button(pager, text="Next", command=lambda: self._change_page(1), state="disabled")
        self.next_button.pack(side="left")
        ttk.Separator(pager, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(pager, text="Selected decision").pack(side="left", padx=(0, 5))
        self.decision_box = ttk.Combobox(pager, textvariable=self.selected_decision, state="disabled", width=14)
        self.decision_box.pack(side="left")
        self.set_decision_button = ttk.Button(pager, text="Set", command=self._set_decision, state="disabled")
        self.set_decision_button.pack(side="left", padx=5)

        bulk = ttk.Frame(table_area, padding=(0, 0, 0, 2))
        bulk.grid(row=3, column=0, sticky="ew")
        bulk_keep = ttk.Frame(table_area, padding=(0, 0, 0, 2))
        bulk_keep.grid(row=4, column=0, sticky="ew")
        ttk.Label(bulk, text="Bulk decision for ALL filtered entries").pack(side="left", padx=(0, 5))
        self.bulk_decision_box = ttk.Combobox(
            bulk, textvariable=self.bulk_decision, values=("copy", "skip", "quarantine", "keep"), state="readonly", width=12
        )
        self.bulk_decision_box.pack(side="left")
        ttk.Checkbutton(bulk, text="Only pending", variable=self.bulk_pending_only).pack(side="left", padx=8)
        self.bulk_button = ttk.Button(bulk, text="Set all filtered", command=self._set_bulk_decision, state="disabled")
        self.bulk_button.pack(side="left")
        self.undo_bulk_button = ttk.Button(bulk, text="Undo last bulk", command=self._undo_bulk_decision, state="disabled")
        self.undo_bulk_button.pack(side="left", padx=5)
        self.autopick_button = ttk.Button(bulk_keep, text="Keep preference / auto-pick...", command=self._open_keep_dialog, state="disabled")
        self.autopick_button.pack(side="left")
        ttk.Label(
            bulk_keep,
            text="  Auto-keep the best file by type, size, creation time or resolution for ALL filtered entries",
            style="Subtle.TLabel",
        ).pack(side="left")

        progress_frame = ttk.Frame(table_area)
        progress_frame.grid(row=5, column=0, sticky="ew", pady=(2, 4))
        progress_frame.columnconfigure(1, weight=1)
        self.progress_bars: dict[str, ttk.Progressbar] = {}
        for row_number, (key, text_var) in enumerate((
            ("preview", self.preview_progress_text),
            ("apply", self.apply_progress_text),
        )):
            ttk.Label(progress_frame, textvariable=text_var, width=46, anchor="w").grid(row=row_number, column=0, sticky="w")
            bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
            bar.grid(row=row_number, column=1, sticky="ew", padx=(6, 0), pady=1)
            self.progress_bars[key] = bar

        detail_frame = ttk.LabelFrame(detail_area, text="Selected entry", padding=8)
        detail_frame.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        detail_frame.columnconfigure(0, weight=1)
        self.details = tk.Text(detail_frame, height=9, wrap="word", relief="flat", background="#f6f8f6", font=("Consolas", 9))
        self.details.grid(row=0, column=0, sticky="ew")
        self.details.configure(state="disabled")

        preview_frame = ttk.LabelFrame(detail_area, text="Image comparison and file properties", padding=8)
        preview_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 2))
        preview_frame.columnconfigure((0, 1), weight=1)
        ttk.Label(preview_frame, text="Selected file").grid(row=0, column=0, sticky="w")
        ttk.Label(preview_frame, text="Matched file").grid(row=0, column=1, sticky="w")
        self.preview_captions: dict[str, tk.StringVar] = {}
        self.preview_canvases: dict[str, tk.Canvas] = {}
        self.metadata_views: dict[str, tk.Text] = {}
        for column, key in enumerate(("source", "match")):
            column_frame = ttk.Frame(preview_frame)
            column_frame.grid(row=1, column=column, sticky="nsew", padx=(0, 6), pady=(0, 4))
            column_frame.columnconfigure(0, weight=1)
            column_frame.rowconfigure(2, weight=1)
            caption = tk.StringVar(value="Select a report row to view its image.")
            canvas = tk.Canvas(
                column_frame,
                width=340,
                height=160,
                background="#dce4df",
                highlightthickness=0,
            )
            ttk.Label(column_frame, textvariable=caption, style="Subtle.TLabel").grid(
                row=0, column=0, sticky="ew"
            )
            canvas.grid(row=1, column=0, sticky="nsew", pady=(3, 5))
            canvas.bind("<Configure>", lambda event, preview_key=key: self._paint_preview(preview_key))
            property_frame = ttk.Frame(column_frame)
            property_frame.grid(row=2, column=0, sticky="nsew")
            property_frame.columnconfigure(0, weight=1)
            property_frame.rowconfigure(0, weight=1)
            properties = tk.Text(
                property_frame,
                height=7,
                wrap="word",
                relief="solid",
                borderwidth=1,
                background="#f6f8f6",
                font=("Consolas", 9),
            )
            property_scroll = ttk.Scrollbar(property_frame, orient="vertical", command=properties.yview)
            properties.configure(yscrollcommand=property_scroll.set, state="disabled")
            properties.grid(row=0, column=0, sticky="nsew")
            property_scroll.grid(row=0, column=1, sticky="ns")
            self.preview_captions[key] = caption
            self.preview_canvases[key] = canvas
            self.metadata_views[key] = properties

    def _path_row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, command: Any) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(3, 1))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(parent, text="Browse", command=command, width=8).grid(row=row + 1, column=1, padx=(5, 0), pady=(0, 4))
        key = label.split()[0].lower()
        if "source" in label.lower():
            key = "source"
        elif "target" in label.lower():
            key = "target"
        elif "quarantine" in label.lower():
            key = "quarantine"
        elif "report" in label.lower():
            key = "report"
        else:
            key = "log"
        if not hasattr(self, "_row_entries"):
            self._row_entries: dict[str, ttk.Entry] = {}
        self._row_entries[key] = entry

    def _update_mode_controls(self) -> None:
        if self.mode.get() == "folder":
            self.target.set(self.source.get())
            self.target_entry.configure(state="disabled")
        else:
            self.target_entry.configure(state="normal")
        self._refresh_default_paths()

    def _default_output_dir(self) -> Path | None:
        # Outputs go beside the organized folder: <parent>\Quarantine.
        organized = (self.source if self.mode.get() == "folder" else self.target).get().strip() or self.source.get().strip()
        if not organized:
            return None
        base = Path(organized)
        parent = base.parent
        return (parent if parent != base else base) / "Quarantine"

    def _default_value(self, field: str, output_dir: Path) -> str:
        if field == "quarantine":
            return str(output_dir)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return str(output_dir / (f"review-{stamp}.jsonl" if field == "report" else f"actions-{stamp}.jsonl"))

    def _refresh_default_paths(self, force_new_names: bool = False) -> None:
        output_dir = self._default_output_dir()
        if output_dir is None:
            return
        for field, variable in (("quarantine", self.quarantine), ("report", self.report), ("log", self.audit_log)):
            current = variable.get().strip()
            if current not in ("", self._auto_values[field]):
                continue  # The user chose this path explicitly.
            if field != "quarantine" and current and not force_new_names and Path(current).parent == output_dir:
                continue  # Keep the stable timestamped name while the folder is unchanged.
            new_value = self._default_value(field, output_dir)
            variable.set(new_value)
            self._auto_values[field] = new_value

    def _browse_source(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("Choose source folder" if self.mode.get() == "backup" else "Choose folder to deduplicate"))
        if selected:
            self.source.set(selected)
            if self.mode.get() == "folder":
                self.target.set(selected)

    def _browse_target(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("Choose target / backup folder"))
        if selected:
            self.target.set(selected)

    def _browse_quarantine(self) -> None:
        selected = filedialog.askdirectory(title=self._tr("Choose quarantine folder"), mustexist=False)
        if selected:
            self.quarantine.set(selected)

    def _browse_report(self) -> None:
        selected = filedialog.asksaveasfilename(
            title=self._tr("Choose JSONL report path"),
            defaultextension=".jsonl",
            filetypes=(("JSONL report", "*.jsonl"), ("All files", "*.*")),
        )
        if selected:
            self.report.set(selected)

    def _browse_log(self) -> None:
        selected = filedialog.asksaveasfilename(
            title=self._tr("Choose action log path"),
            defaultextension=".jsonl",
            filetypes=(("JSONL log", "*.jsonl"), ("All files", "*.*")),
        )
        if selected:
            self.audit_log.set(selected)

    def _start_preview(self) -> None:
        if self.report.get().strip() and Path(self.report.get().strip()).exists() \
                and self.report.get().strip() == self._auto_values["report"]:
            self._refresh_default_paths(force_new_names=True)
        source = Path(self.source.get().strip())
        target_text = self.source.get().strip() if self.mode.get() == "folder" else self.target.get().strip()
        report = Path(self.report.get().strip())
        quarantine_text = self.quarantine.get().strip()
        try:
            distance = self.phash_distance.get()
        except tk.TclError:
            messagebox.showerror(self._tr("Invalid pHash distance"), self._tr("Enter a whole number from 0 to 64."), parent=self.root)
            return
        if not 0 <= distance <= 64:
            messagebox.showerror(self._tr("Invalid pHash distance"), self._tr("Enter a value from 0 to 64."), parent=self.root)
            return
        if not source.is_dir():
            messagebox.showerror(self._tr("Missing source"), self._tr("Choose an existing source folder."), parent=self.root)
            return
        if self.mode.get() == "backup" and not Path(target_text).is_dir():
            messagebox.showerror(self._tr("Missing target"), self._tr("Choose an existing target folder."), parent=self.root)
            return
        if not report.name:
            messagebox.showerror(self._tr("Missing report"), self._tr("Choose a JSONL report path."), parent=self.root)
            return
        if report.exists():
            messagebox.showerror(self._tr("Report exists"), self._tr("Choose a new report filename; preview never overwrites a report."), parent=self.root)
            return
        if not quarantine_text:
            messagebox.showerror(self._tr("Missing quarantine folder"), self._tr("Choose a quarantine folder."), parent=self.root)
            return

        self._set_progress("preview", "Preview: starting...", None, None)
        args = argparse.Namespace(
            mode=self.mode.get(),
            source=str(source),
            target=target_text,
            report=str(report),
            quarantine=quarantine_text,
            phash_distance=distance,
            visual_check=self.visual_check.get(),
            content_id_check=self.content_id_check.get(),
            progress_callback=lambda text, current=None, total=None: self.events.put(
                ("preview_progress", (text, current, total))
            ),
            task_control=self.task_control,
        )
        self._run_worker("preview", lambda: preview(args), report)

    def _set_progress(self, key: str, text: str, current: int | None, total: int | None) -> None:
        bar = self.progress_bars[key]
        label = self.preview_progress_text if key == "preview" else self.apply_progress_text
        if current is None or not total:
            bar.configure(mode="indeterminate")
            bar.start(40)
            label.set(self._tr_progress(text))
            return
        bar.stop()
        bar.configure(mode="determinate", value=min(100.0, current * 100.0 / total))
        label.set(f"{self._tr_progress(text)}  ({current * 100.0 / total:.0f}%)")

    def _finish_progress(self, key: str, text: str, complete: bool) -> None:
        bar = self.progress_bars[key]
        bar.stop()
        bar.configure(mode="determinate", value=100 if complete else 0)
        (self.preview_progress_text if key == "preview" else self.apply_progress_text).set(self._tr_progress(text))

    def _open_quarantine_folder(self) -> None:
        folder = Path(self.quarantine.get().strip()) if self.quarantine.get().strip() else None
        if folder is None:
            messagebox.showinfo(self._tr("Quarantine folder"), self._tr("No quarantine folder is set yet."), parent=self.root)
        elif not folder.is_dir():
            messagebox.showinfo(self._tr("Quarantine folder"), f"{self._tr('The folder does not exist yet (nothing has been moved):')}\n{folder}", parent=self.root)
        else:
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
            except (AttributeError, OSError) as error:
                messagebox.showinfo(self._tr("Quarantine folder"), f"{folder}\n\n{self._tr('Could not open it automatically: ')}{error}", parent=self.root)

    def _run_worker(self, label: str, operation: Any, report_path: Path | None = None) -> None:
        if self.busy:
            return
        self.busy = True
        self.task_control.reset()
        self._set_busy(True)
        self.status.set(self._tr(f"{label.title()} running…"))

        def work() -> None:
            try:
                operation()
                self.events.put((f"{label}_done", report_path))
            except TaskCancelled:
                self.events.put(("cancelled", label))
            except Exception as error:
                self.events.put(("error", (label, error)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "preview_progress":
                    text, current, total = payload
                    self._set_progress("preview", text, current, total)
                    self.status.set(self._tr_progress(text))
                elif event == "apply_progress":
                    text, current, total = payload
                    self._set_progress("apply", text, current, total)
                    self.status.set(self._tr_progress(text))
                elif event == "apply_result":
                    self.apply_summary = payload
                elif event == "autopick_done":
                    plan, old_rows = payload
                    self.busy = False
                    self._set_busy(False)
                    for index, new_row in plan.changes.items():
                        self.rows[index] = new_row
                    self.last_bulk_changes = old_rows
                    self._refresh_rows()
                    self.save_button.configure(state="normal")
                    stats = plan.stats
                    if self.language.get() == "zh":
                        summary = (
                            f"自动选择：隔离 {stats['quarantine']:,}，保留 {stats['keep']:,}，复制 {stats['copy']:,}，"
                            f"跳过 {stats['skip']:,}；留待人工审核 {stats['left_manual']:,}，无法读取 {stats['unreadable']:,}。"
                        )
                    else:
                        summary = (
                            f"Auto-pick: {stats['quarantine']:,} to quarantine, {stats['keep']:,} kept, "
                            f"{stats['copy']:,} copy, {stats['skip']:,} skip; "
                            f"{stats['left_manual']:,} left for manual review, {stats['unreadable']:,} unreadable."
                        )
                    self._finish_progress("apply", summary, True)
                    self.status.set(summary + (" 请审核、保存后再应用。" if self.language.get() == "zh" else " Review, save, then apply."))
                elif event == "preview_done":
                    self.busy = False
                    self._set_busy(False)
                    self.current_report = Path(payload) if payload else Path(self.report.get())
                    self.report.set(str(self.current_report))
                    self._read_report(self.current_report)
                    self._finish_progress("preview", f"Preview complete: {len(self.rows):,} entries", True)
                    self.status.set(f"{self._tr('Preview complete: ')}{len(self.rows)} {self._tr('report entries')}")
                elif event == "apply_done":
                    self.busy = False
                    self._set_busy(False)
                    summary = getattr(self, "apply_summary", None) or {}
                    copied, moved = summary.get("copied", 0), summary.get("quarantined", 0)
                    text = f"{self._tr('Apply complete: copied ')}{copied:,}{self._tr(', moved to quarantine ')}{moved:,}"
                    self._finish_progress("apply", text, True)
                    self.status.set(f"{text}. {self._tr('Action log:')}{self.audit_log.get()}")
                    note = ""
                    if copied == 0 and moved == 0:
                        note = "\n\n" + self._tr("No files were changed: no entry had decision 'copy' or 'quarantine'.")
                    messagebox.showinfo(
                        self._tr("Apply complete"),
                        f"{self._tr('Copied to target: ')}{copied:,}\n{self._tr('Moved to quarantine: ')}{moved:,}\n"
                        f"{self._tr('Quarantine folder:')}\n{summary.get('quarantine_root', self.quarantine.get())}\n\n"
                        f"{self._tr('No files were permanently deleted.')}\n{self._tr('Action log:')}\n{self.audit_log.get()}{note}",
                        parent=self.root,
                    )
                elif event == "media_properties":
                    request_id, requested, results, warning = payload
                    if request_id != self.metadata_request_id:
                        continue
                    for key, path, signature in requested:
                        metadata = results.get(path.resolve(), {})
                        self.metadata_cache[path.resolve()] = (signature[0], signature[1], metadata)
                        self._render_media_properties(key, path, signature, metadata, warning)
                elif event == "video_preview":
                    request_id, key, path, image, warning = payload
                    if request_id != self.preview_request_id:
                        continue
                    if image is None:
                        self._set_preview_message(key, f"{self._tr('Video preview unavailable: ')}{warning}")
                    else:
                        self.preview_images[key] = image
                        self.preview_captions[key].set(f"{path}  |  first frame")
                        self._paint_preview(key)
                elif event == "cancelled":
                    self.busy = False
                    self._set_busy(False)
                    key = "preview" if payload == "preview" else "apply"
                    notes = {
                        "preview": "Preview stopped. No report was written.",
                        "apply": "Apply stopped. Completed operations are in the action log; create a new preview before applying again.",
                        "autopick": "Auto-pick stopped. No decisions were changed.",
                    }
                    self._finish_progress(key, notes[payload], False)
                    self.status.set(self._tr(notes[payload]))
                elif event == "error":
                    self.busy = False
                    self._set_busy(False)
                    label, error = payload
                    key = "preview" if label == "preview" else "apply"
                    self._finish_progress(key, f"{label.title()} failed", False)
                    self.status.set(f"{self._tr(label.title() + ' failed')}: {error}")
                    messagebox.showerror(self._tr(label.title() + " failed"), str(error), parent=self.root)
        except queue.Empty:
            pass
        self._poll_after_id = self.root.after(100, self._poll_events)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.preview_button, self.load_button):
            button.configure(state=state)
        has_report = bool(self.rows)
        self.save_button.configure(state="normal" if has_report and not busy else "disabled")
        self._update_apply_state()
        self._refresh_task_buttons()

    def _refresh_task_buttons(self) -> None:
        paused = self.task_control.paused
        self.pause_button.configure(
            text=self._tr("Resume" if paused else "Pause"), state="normal" if self.busy else "disabled"
        )
        self.stop_button.configure(state="normal" if self.busy else "disabled")

    def _toggle_pause(self) -> None:
        if not self.busy:
            return
        if self.task_control.paused:
            self.task_control.resume()
            self.status.set(self._tr("Resumed."))
        else:
            self.task_control.pause()
            self.status.set(self._tr("Paused. Click Resume to continue."))
        self._refresh_task_buttons()

    def _stop_task(self) -> None:
        if not self.busy:
            return
        self.task_control.stop()
        self.stop_button.configure(state="disabled")
        self.pause_button.configure(state="disabled")
        self.status.set(self._tr("Stopping after the current file…"))

    def _load_report_dialog(self) -> None:
        selected = filedialog.askopenfilename(
            title=self._tr("Open review report"),
            filetypes=(("JSONL report", "*.jsonl"), ("All files", "*.*")),
        )
        if selected:
            self._read_report(Path(selected))

    def _read_report(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as report_file:
            records = [json.loads(line) for line in report_file if line.strip()]
        if not records or records[0].get("type") != "header" or records[0].get("version") != 1:
            raise BackupError(self._tr("Unsupported or malformed review report."))
        self.header = records[0]
        self.rows = records[1:]
        self.last_bulk_changes = []
        self.current_report = path
        self.source.set(self.header["source_root"])
        self.target.set(self.header["target_root"])
        self.quarantine.set(self.header["quarantine_root"])
        self.mode.set(self.header.get("mode", "backup"))
        self.report.set(str(path))
        self.phash_distance.set(self.header.get("phash_distance", 4))
        self.allow_quarantine.set(False)
        self.allow_visual_quarantine.set(False)
        self.allow_video_quarantine.set(False)
        self.confirm_changes.set(False)
        self._update_mode_controls()
        self._refresh_rows()
        self.save_button.configure(state="normal")
        self._update_apply_state()
        self.status.set(f"{self._tr('Loaded ')}{len(self.rows)} {self._tr('report entries')}{self._tr(' from ')}{path}")

    def _save_report(self) -> bool:
        if self.header is None:
            return False
        path = Path(self.report.get().strip())
        if not path.name:
            messagebox.showerror(self._tr("Missing report"), self._tr("Choose a report filename."), parent=self.root)
            return False
        if path.exists() and path != self.current_report:
            messagebox.showerror(self._tr("Report exists"), self._tr("Choose a new filename; another report will not be overwritten."), parent=self.root)
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as report_file:
            report_file.write(json.dumps(self.header, ensure_ascii=False) + "\n")
            for row in self.rows:
                report_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.current_report = path
        self.status.set(f"{self._tr('Saved decisions: ')}{path}")
        self._update_apply_state()
        return True

    def _confirm_apply(self) -> None:
        if self.header is None or self.current_report is None:
            return
        pending = sum(row.get("decision") == "pending" for row in self.rows)
        if pending:
            messagebox.showwarning(
                self._tr("Review required"),
                self._tr("Resolve all {n} pending decisions before applying.").format(n=pending), parent=self.root
            )
            return
        if not self.confirm_changes.get():
            messagebox.showwarning(
                self._tr("Confirmation required"),
                self._tr("Enable 'Confirm filesystem changes' before applying."), parent=self.root
            )
            return
        if not messagebox.askyesno(
            self._tr("Apply reviewed plan"),
            self._tr("This will copy selected files and move authorized duplicates to quarantine.") + "\n\n"
            + self._tr("Source files are never deleted by copy actions, and quarantine items are recoverable.") + "\n"
            + self._tr("Continue?"),
            parent=self.root,
        ):
            return
        if not self._save_report():
            return
        if self.audit_log.get().strip() and Path(self.audit_log.get().strip()).exists() \
                and self.audit_log.get().strip() == self._auto_values["log"]:
            self._refresh_default_paths(force_new_names=True)
        self.apply_summary = None
        self._set_progress("apply", "Apply: preparing plan...", None, None)
        args = argparse.Namespace(
            report=str(self.current_report),
            source=self.source.get(),
            target=self.target.get(),
            log=self.audit_log.get(),
            quarantine=self.quarantine.get(),
            confirm=self.confirm_changes.get(),
            allow_quarantine=self.allow_quarantine.get(),
            allow_visual_quarantine=self.allow_visual_quarantine.get(),
            allow_video_quarantine=self.allow_video_quarantine.get(),
            progress_callback=lambda text, current=None, total=None: self.events.put(
                ("apply_progress", (text, current, total))
            ),
            result_callback=lambda summary: self.events.put(("apply_result", summary)),
            task_control=self.task_control,
        )
        self._run_worker("apply", lambda: apply_report(args))

    def _refresh_rows(self) -> None:
        query = self.search.get().casefold().strip()
        selected_filter = self._to_english(
            self.operation_filter.get(), tuple(self.operation_filter_box._gui_original_values)
        )
        self.visible_rows = []
        for index, row in enumerate(self.rows):
            operation = row.get("operation", "")
            if selected_filter == "Needs review" and row.get("decision") != "pending":
                continue
            if selected_filter not in {"All entries", "Needs review"} and operation != selected_filter:
                continue
            searchable = " ".join(
                str(row.get(key, ""))
                for key in (
                    "source",
                    "matched_path",
                    "reason",
                    "operation",
                    "decision",
                    "content_identifier",
                    "still_paths",
                    "motion_paths",
                )
            )
            if query and query not in searchable.casefold():
                continue
            self.visible_rows.append(index)
        self.current_page = min(self.current_page, max(0, (len(self.visible_rows) - 1) // PAGE_SIZE))
        self._render_page()
        self._update_apply_state()

    def _render_page(self) -> None:
        self.table.delete(*self.table.get_children())
        start = self.current_page * PAGE_SIZE
        page_indices = self.visible_rows[start : start + PAGE_SIZE]
        for index in page_indices:
            row = self.rows[index]
            operation = row.get("operation", "")
            tag = "pending" if row.get("decision") == "pending" else "quarantine" if row.get("decision") == "quarantine" else "copy" if row.get("decision") == "copy" else ""
            distance = row.get("phash_distance")
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    self._tr(row.get("decision", "")),
                    self._tr(operation),
                    self._short_path(row.get("source", "")),
                    self._short_path(row.get("matched_path", "")),
                    "" if distance is None else distance,
                ),
                tags=(tag,) if tag else (),
            )
        page_count = max(1, (len(self.visible_rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_label.configure(
            text=(
                f"{len(self.visible_rows):,} {self._tr('entries')}  |  {self._tr('Page')} {self.current_page + 1} {self._tr('of')} {page_count}"
                if self.language.get() == "en"
                else f"{len(self.visible_rows):,} 条 | 第 {self.current_page + 1}/{page_count} 页"
            )
        )
        self.prev_button.configure(state="normal" if self.current_page else "disabled")
        self.next_button.configure(state="normal" if self.current_page + 1 < page_count else "disabled")

    @staticmethod
    def _short_path(value: str) -> str:
        if not value:
            return ""
        path = Path(value)
        return f"…{path.parent.name}/{path.name}" if path.parent.name else str(path)

    def _change_page(self, delta: int) -> None:
        self.current_page = max(0, self.current_page + delta)
        self._render_page()

    def _selection_changed(self, _event: Any = None) -> None:
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        row = self.rows[index]
        allowed = DECISION_OPTIONS.get(row.get("operation", ""), ("keep",))
        self.decision_box.configure(values=tuple(self._tr(value) for value in allowed), state="readonly")
        self.selected_decision.set(self._tr(row.get("decision", allowed[0])))
        self.set_decision_button.configure(state="normal")
        details = self._entry_details(row)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", details)
        self.details.configure(state="disabled")
        source_path = row.get("source", "")
        matched_path = row.get("matched_path", "")
        if row.get("operation") == "live_pair_info":
            stills = row.get("still_paths", [])
            source_path = stills[0] if stills else ""
            matched_path = ""
        self.preview_request_id += 1
        preview_request_id = self.preview_request_id
        self._set_preview("source", source_path, preview_request_id)
        self._set_preview("match", matched_path, preview_request_id)
        self._load_media_properties((("source", source_path), ("match", matched_path)))

    def _entry_details(self, row: dict[str, Any]) -> str:
        operation = row.get("operation", "")
        decision = row.get("decision", "")
        source = row.get("source", "")
        matched = row.get("matched_path", "")
        distance = row.get("phash_distance")
        first = f"{self._tr('Action')}: {self._tr(operation)}    {self._tr('Decision')}: {self._tr(decision)}"
        if distance is not None:
            first += f"    {self._tr('pHash distance: ')}{distance}"
        if operation == "live_pair_info":
            return "\n".join((
                first,
                self._tr("Information only: no file is changed for this entry."),
                f"ContentIdentifier: {row.get('content_identifier', '')}",
                self._tr("Still image(s):  ") + "; ".join(row.get("still_paths", [])),
                self._tr("Motion file(s):  ") + "; ".join(row.get("motion_paths", [])),
                f"{self._tr('Reason: ')}{self._tr(row.get('reason', ''))}",
            ))

        quarantine_root = (self.header or {}).get("quarantine_root") or self.quarantine.get()
        target_root = (self.header or {}).get("target_root", "")
        effects = {
            "pending": self._tr("UNDECIDED - Apply stays blocked until every pending entry has a decision."),
            "quarantine": self._tr("Apply will MOVE the selected file into quarantine and keep the matched file in place."),
            "keep": self._tr("Apply leaves both files untouched."),
            "copy": self._tr("Apply will COPY the selected file to the target folder; the original stays where it is."),
            "skip": self._tr("Apply copies and moves nothing for this entry."),
        }
        lines = [first, effects.get(decision, ""), "", f"{self._tr('Selected file: ')}{source}"]
        if decision == "quarantine" and quarantine_root and source:
            try:
                relative = Path(source).relative_to(target_root)
                lines.append(f"{self._tr('Quarantine destination: ')}{Path(quarantine_root) / relative}")
            except ValueError:
                pass
        lines.append(f"{self._tr('Matched file:  ')}{matched or self._tr('(none)')}")
        sha, matched_sha = row.get("sha256", ""), row.get("matched_sha256", "")
        if sha:
            lines.append(f"{self._tr('SHA-256 selected: ')}{sha}")
        if matched_sha:
            same = "identical bytes" if matched_sha == sha else "different bytes (visual match only)"
            lines.append(f"{self._tr('SHA-256 matched:  ')}{matched_sha}  [{self._tr(same)}]")
        lines.append(f"{self._tr('Reason: ')}{self._tr(row.get('reason', ''))}")
        return "\n".join(lines)

    def _load_media_properties(self, selections: tuple[tuple[str, str], ...]) -> None:
        self.metadata_request_id += 1
        request_id = self.metadata_request_id
        requested: list[tuple[str, Path, tuple[int, int]]] = []
        uncached: list[Path] = []
        for key, path_text in selections:
            if not path_text:
                self._set_properties_text(key, "No matched file.")
                continue
            path = Path(path_text)
            try:
                stat = path.stat()
            except OSError as error:
                self._set_properties_text(key, f"File unavailable: {error}")
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            resolved_path = path.resolve()
            cached = self.metadata_cache.get(resolved_path)
            if cached and cached[:2] == signature:
                self._render_media_properties(key, path, signature, cached[2], None)
            else:
                self._render_media_properties(key, path, signature, {}, "Reading metadata…")
                requested.append((key, path, signature))
                uncached.append(path)

        if not uncached:
            return

        def read_properties() -> None:
            results, warning = read_media_properties(uncached)
            self.events.put(("media_properties", (request_id, requested, results, warning)))

        threading.Thread(target=read_properties, daemon=True).start()

    def _render_media_properties(
        self,
        key: str,
        path: Path,
        signature: tuple[int, int],
        metadata: dict[str, Any],
        warning: str | None,
    ) -> None:
        size, modified_ns = signature
        modified = datetime.fromtimestamp(
            modified_ns / 1_000_000_000,
            timezone.utc,
        ).astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S UTC+08:00")
        entries = [
            ("File", path.name),
            ("Type", metadata.get("File:FileType") or path.suffix.lstrip(".").upper() or "Unknown"),
            ("File size", f"{size:,} bytes ({size / (1024 * 1024):.2f} MiB)"),
            ("Modified", modified),
        ]

        date_taken = (
            metadata.get("EXIF:DateTimeOriginal")
            or metadata.get("EXIF:CreateDate")
            or metadata.get("QuickTime:CreationDate")
            or metadata.get("QuickTime:CreateDate")
        )
        camera = " ".join(
            str(metadata.get(tag, "")).strip()
            for tag in ("EXIF:Make", "EXIF:Model")
            if metadata.get(tag)
        )
        width = (
            metadata.get("Computed:Width")
            or metadata.get("EXIF:ImageWidth")
            or metadata.get("File:ImageWidth")
            or metadata.get("QuickTime:ImageWidth")
            or metadata.get("Image:ImageWidth")
        )
        height = (
            metadata.get("Computed:Height")
            or metadata.get("EXIF:ImageHeight")
            or metadata.get("File:ImageHeight")
            or metadata.get("QuickTime:ImageHeight")
            or metadata.get("Image:ImageHeight")
        )
        dimensions = (
            f"{width} x {height} px ({int(width) * int(height) / 1_000_000:.1f} MP)"
            if width and height
            else str(metadata.get("Composite:ImageSize", "")).replace(" ", " x ", 1) or "Unavailable"
        )
        values = (
            ("MIME", metadata.get("File:MIMEType")),
            ("Resolution", dimensions),
            ("Captured", date_taken),
            ("Camera", camera),
            ("Duration", metadata.get("QuickTime:Duration")),
            ("Compressor", metadata.get("QuickTime:CompressorName")),
            ("Orientation", metadata.get("EXIF:Orientation")),
            (
                "ContentIdentifier",
                metadata.get("MakerNotes:ContentIdentifier")
                or metadata.get("Keys:ContentIdentifier")
                or metadata.get("QuickTime:ContentIdentifier")
                or metadata.get("XMP:ContentIdentifier"),
            ),
        )
        for label, value in values:
            if value in (None, ""):
                continue
            if label == "Captured":
                value = format_beijing_time(value)
            if value not in (None, ""):
                entries.append((label, value))
        if warning:
            entries.append(("Metadata", warning))
        elif not any(not tag.startswith(("File:", "Computed:")) for tag in metadata):
            entries.append(("Metadata", "No camera/QuickTime metadata found."))
        self._set_properties_text(key, "\n".join(f"{self._tr(label)}: {value}" for label, value in entries))

    def _set_properties_text(self, key: str, text: str) -> None:
        widget = self.metadata_views[key]
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_preview(self, key: str, path_text: str, request_id: int) -> None:
        canvas = self.preview_canvases[key]
        canvas.delete("all")
        self.preview_images.pop(key, None)
        self.preview_photos.pop(key, None)
        path = Path(path_text) if path_text else None
        self.preview_captions[key].set(str(path) if path else "No matched image")
        if path is None:
            canvas.create_text(
                220,
                90,
                text="No image preview for this item",
                fill="#596760",
                tags=("preview-placeholder",),
            )
            return
        if not path.is_file():
            canvas.create_text(220, 90, text="File is unavailable", fill="#596760", tags=("preview-placeholder",))
            return
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            self.preview_captions[key].set(f"{path}  |  extracting first frame…")

            def extract() -> None:
                image, warning = extract_video_preview(path)
                self.events.put(("video_preview", (request_id, key, str(path), image, warning)))

            threading.Thread(target=extract, daemon=True).start()
            return
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            self._set_preview_message(key, "No image preview for this item")
            return
        try:
            try:
                import pillow_heif

                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            from PIL import Image, ImageOps

            with Image.open(path) as image_file:
                image = ImageOps.exif_transpose(image_file).convert("RGB")
                self.preview_images[key] = image.copy()
            self._paint_preview(key)
        except Exception as error:
            canvas.create_text(
                220,
                90,
                text=f"Preview unavailable: {error}",
                width=400,
                fill="#7a3f32",
                tags=("preview-placeholder",),
            )

    def _set_preview_message(self, key: str, message: str) -> None:
        canvas = self.preview_canvases[key]
        canvas.delete("all")
        width = max(canvas.winfo_width(), 440)
        height = max(canvas.winfo_height(), 180)
        canvas.create_text(
            width // 2,
            height // 2,
            text=message,
            width=width - 24,
            fill="#7a3f32",
            tags=("preview-placeholder",),
        )
        self.preview_captions[key].set(message)

    def _paint_preview(self, key: str) -> None:
        image = self.preview_images.get(key)
        canvas = self.preview_canvases.get(key)
        if image is None or canvas is None:
            return
        try:
            from PIL import Image, ImageTk

            width = max(canvas.winfo_width(), 440)
            height = max(canvas.winfo_height(), 180)
            thumbnail = image.copy()
            thumbnail.thumbnail((width - 12, height - 12), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumbnail, master=self.root)
            canvas.delete("all")
            canvas.create_image(width // 2, height // 2, image=photo, tags=("preview-image",))
            self.preview_photos[key] = photo
        except Exception as error:
            canvas.delete("all")
            canvas.create_text(
                220,
                90,
                text=f"Preview rendering failed: {error}",
                width=400,
                fill="#7a3f32",
                tags=("preview-placeholder",),
            )
            self.preview_captions[key].set(f"Preview unavailable: {error}")

    def _set_decision(self) -> None:
        selected = self.table.selection()
        if not selected or self.busy:
            return
        index = int(selected[0])
        row = self.rows[index]
        allowed = DECISION_OPTIONS.get(row.get("operation", ""), ("keep",))
        decision = self._to_english(self.selected_decision.get(), allowed)
        if decision not in allowed:
            messagebox.showerror(
                self._tr("Invalid decision"),
                self._tr("Choose one of: ") + ", ".join(self._tr(option) for option in allowed),
                parent=self.root,
            )
            return
        row["decision"] = decision
        self._refresh_rows()
        if str(index) in self.table.get_children():
            self.table.selection_set(str(index))
            self.table.see(str(index))
        self.save_button.configure(state="normal")
        self.status.set(self._tr("Decision updated in memory. Save the report before applying."))

    def _update_apply_state(self) -> None:
        ready = bool(self.rows) and not self.busy and not any(row.get("decision") == "pending" for row in self.rows)
        self.apply_button.configure(state="normal" if ready else "disabled")
        self.bulk_button.configure(state="normal" if self.visible_rows and not self.busy else "disabled")
        self.autopick_button.configure(state="normal" if self.visible_rows and not self.busy else "disabled")
        self.undo_bulk_button.configure(state="normal" if self.last_bulk_changes and not self.busy else "disabled")

    def _set_bulk_decision(self) -> None:
        if self.busy:
            return
        decision = self._to_english(self.bulk_decision.get(), ("copy", "skip", "quarantine", "keep"))
        pending_only = self.bulk_pending_only.get()
        targets: list[int] = []
        incompatible = 0
        for index in self.visible_rows:
            row = self.rows[index]
            if pending_only and row.get("decision") != "pending":
                continue
            if decision in DECISION_OPTIONS.get(row.get("operation", ""), ("keep",)):
                targets.append(index)
            else:
                incompatible += 1
        if not targets:
            messagebox.showinfo(
                self._tr("Nothing to change"),
                self._tr("No filtered entries accept '{decision}'").format(decision=self._tr(decision))
                + (f" ({self._tr('Only pending entries')})." if pending_only else "."),
                parent=self.root,
            )
            return
        search_text = self.search.get().strip()
        scope = f"{self._tr('Filter')}: {self.operation_filter.get()}" + (f"; {self._tr('Search paths / notes')}: '{search_text}'" if search_text else "")
        if not messagebox.askyesno(
            self._tr("Apply bulk decision"),
            self._tr("Set {n} entries to '{decision}' across all pages?").format(
                n=f"{len(targets):,}", decision=self._tr(decision)
            ) + f"\n{scope}\n{self._tr('Skipped as incompatible: ')}{incompatible:,}\n\n"
            + self._tr("This only edits the in-memory report; no files move until Apply."),
            parent=self.root,
        ):
            return
        self.last_bulk_changes = [(index, copy.deepcopy(self.rows[index])) for index in targets]
        for index in targets:
            self.rows[index]["decision"] = decision
        self._refresh_rows()
        self.save_button.configure(state="normal")
        self.status.set(
            self._tr("Bulk set {n} entries to '{decision}'. Save the report before applying.").format(
                n=f"{len(targets):,}", decision=self._tr(decision)
            )
        )

    def _undo_bulk_decision(self) -> None:
        for index, previous in self.last_bulk_changes:
            self.rows[index] = previous
        count = len(self.last_bulk_changes)
        self.last_bulk_changes = []
        self._refresh_rows()
        self.status.set(f"{self._tr('Reverted ')}{count:,}{self._tr(' bulk changes.')}")

    def _current_keep_preference(self) -> KeepPreference:
        by_label = {label: name for name, label in KEEP_CRITERIA.items()}
        criteria = tuple(
            by_label[self._to_english(var.get(), tuple(by_label))]
            for var in self.keep_criteria
            if self._to_english(var.get(), tuple(by_label)) in by_label
        )
        order = tuple(
            item.strip().lower() if item.strip().startswith(".") else "." + item.strip().lower()
            for item in self.keep_type_order.get().replace(";", ",").split(",")
            if item.strip()
        )
        return KeepPreference(criteria=criteria, type_order=order or DEFAULT_TYPE_ORDER)

    def _open_keep_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(self._tr("Keep preference / auto-pick"))
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=self._tr("For every duplicate pair/group in the current Filter + Search, keep the best file by these\n"
            "criteria (earlier rows win; later rows only break ties) and mark the others for quarantine."),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        labels = [self._tr(label) for label in KEEP_CRITERIA.values()] + [self._tr("(none)")]
        for position, var in enumerate(self.keep_criteria):
            ttk.Label(frame, text=self._tr(f"Criterion {position + 1}")).grid(row=position + 1, column=0, sticky="w", pady=2)
            ttk.Combobox(frame, textvariable=var, values=labels, state="readonly", width=34).grid(
                row=position + 1, column=1, sticky="w", pady=2
            )
        ttk.Label(frame, text=self._tr("File type order\n(best first)")).grid(row=5, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frame, textvariable=self.keep_type_order, width=50).grid(row=5, column=1, sticky="w", pady=(8, 2))
        ttk.Checkbutton(frame, text=self._tr("Only pending entries"), variable=self.bulk_pending_only).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            frame,
            text=self._tr("Exact duplicates keep one file per group. Visual pairs where the kept file is itself removed by another pair are left\n"
            "for manual review. Source-visual rows become copy (source wins) or skip (match wins). Undo last bulk reverts it."),
            style="Subtle.TLabel",
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 10))
        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text=self._tr("Cancel"), command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(
            buttons, text=self._tr("Auto-pick for filtered entries"),
            command=lambda: (dialog.destroy(), self._run_auto_pick()), style="Accent.TButton",
        ).pack(side="right")
        dialog.grab_set()

    def _run_auto_pick(self) -> None:
        preference = self._current_keep_preference()
        if not preference.criteria:
            messagebox.showerror(self._tr("Keep preference"), self._tr("Choose at least one criterion."), parent=self.root)
            return
        indices = list(self.visible_rows)
        if not indices or self.busy:
            return
        pending_only = self.bulk_pending_only.get()
        snapshot = [(index, copy.deepcopy(self.rows[index])) for index in indices]
        rows_view = self.rows
        self._set_progress("apply", "Auto-pick: reading file facts...", None, None)

        def work() -> None:
            def progress(done: int, total: int) -> None:
                self.events.put(("apply_progress", (f"Auto-pick {done:,}/{total:,}", done, total)))

            try:
                plan = plan_keep_decisions(
                    rows_view, indices, preference, pending_only, progress, self.task_control
                )
                self.events.put(("autopick_done", (plan, [item for item in snapshot if item[0] in plan.changes])))
            except TaskCancelled:
                self.events.put(("cancelled", "autopick"))
            except Exception as error:
                self.events.put(("error", ("autopick", error)))

        self.busy = True
        self.task_control.reset()
        self._set_busy(True)
        threading.Thread(target=work, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    PhotoBackupApp(root)
    root.deiconify()
    root.update_idletasks()
    try:
        root.state("zoomed")
    except tk.TclError:
        pass
    root.mainloop()


if __name__ == "__main__":
    main()