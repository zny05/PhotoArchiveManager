"""Tk desktop interface for Photo Archive Manager."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from .core import (
    ACTION_VALUES,
    apply_report,
    read_report,
    restore_from_log,
    scan_backup,
    scan_duplicates,
    write_report,
)


TEXT = {
    "zh": {
        "title": "照片归档管理器",
        "mode": "任务",
        "backup": "备份并检查目标",
        "duplicates": "扫描单个目录重复项",
        "source": "来源目录",
        "target": "目标目录",
        "browse": "浏览…",
        "preview": "生成预览报告",
        "load": "打开报告…",
        "apply": "确认并执行 Apply",
        "restore": "按操作日志还原…",
        "language": "语言",
        "english": "English",
        "chinese": "中文",
        "status_ready": "就绪。预览不会更改或移动文件。",
        "working": "正在扫描文件，请稍候…",
        "applying": "正在执行已授权操作…",
        "restoring": "正在还原隔离文件…",
        "done": "完成",
        "error": "错误",
        "choose_source": "选择来源目录",
        "choose_target": "选择目标目录",
        "save_report": "保存预览报告",
        "open_report": "打开预览报告",
        "open_log": "选择 Quarantine/action_log.jsonl",
        "confirm_apply": "Apply 将执行报告中标记为 copy 的复制，以及 quarantine 的移动操作。是否继续？",
        "confirm_restore": "根据此日志还原可用的隔离文件？",
        "select_row": "请先在报告中选择一行。",
        "columns": ("来源文件", "匹配文件", "类型", "操作"),
        "preview_source": "来源预览",
        "preview_match": "匹配预览",
        "no_image": "无可用图片预览",
        "count": "报告行数",
    },
    "en": {
        "title": "Photo Archive Manager",
        "mode": "Task",
        "backup": "Back up and check destination",
        "duplicates": "Scan one folder for duplicates",
        "source": "Source folder",
        "target": "Target folder",
        "browse": "Browse…",
        "preview": "Generate preview report",
        "load": "Open report…",
        "apply": "Authorize and Apply",
        "restore": "Restore from action log…",
        "language": "Language",
        "english": "English",
        "chinese": "中文",
        "status_ready": "Ready. Preview does not change or move files.",
        "working": "Scanning files…",
        "applying": "Applying authorized actions…",
        "restoring": "Restoring quarantined files…",
        "done": "Done",
        "error": "Error",
        "choose_source": "Select source folder",
        "choose_target": "Select target folder",
        "save_report": "Save preview report",
        "open_report": "Open preview report",
        "open_log": "Select Quarantine/action_log.jsonl",
        "confirm_apply": "Apply will copy rows marked copy and move rows marked quarantine. Continue?",
        "confirm_restore": "Restore available quarantined files using this log?",
        "select_row": "Select a report row first.",
        "columns": ("Source file", "Match file", "Type", "Action"),
        "preview_source": "Source preview",
        "preview_match": "Match preview",
        "no_image": "No image preview available",
        "count": "Report rows",
    },
}


class ArchiveApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.language = "zh"
        self.mode = tk.StringVar(value="backup")
        self.source = tk.StringVar()
        self.target = tk.StringVar()
        self.status = tk.StringVar()
        self.report_path: Path | None = None
        self.rows: list[dict[str, str]] = []
        self.images: list[ImageTk.PhotoImage | None] = [None, None]
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._build()
        self._translate()
        self.root.after(100, self._poll_messages)

    def _build(self) -> None:
        self.root.geometry("1180x760")
        self.root.minsize(850, 580)

        self.toolbar = ttk.Frame(self.root, padding=(10, 8))
        self.toolbar.pack(fill="x")
        self.mode_label = ttk.Label(self.toolbar)
        self.mode_label.pack(side="left")
        self.backup_radio = ttk.Radiobutton(
            self.toolbar, variable=self.mode, value="backup", command=self._mode_changed,
        )
        self.backup_radio.pack(side="left", padx=(8, 2))
        self.duplicates_radio = ttk.Radiobutton(
            self.toolbar, variable=self.mode, value="duplicates", command=self._mode_changed,
        )
        self.duplicates_radio.pack(side="left", padx=(2, 16))
        self.language_label = ttk.Label(self.toolbar)
        self.language_label.pack(side="left")
        self.language_box = ttk.Combobox(self.toolbar, state="readonly", width=9)
        self.language_box.pack(side="left", padx=6)
        self.language_box.bind("<<ComboboxSelected>>", self._language_changed)

        self.path_frame = ttk.Frame(self.root, padding=(10, 2))
        self.path_frame.pack(fill="x")
        self.source_label = ttk.Label(self.path_frame, width=15)
        self.source_label.grid(row=0, column=0, sticky="w", pady=3)
        self.source_entry = ttk.Entry(self.path_frame, textvariable=self.source)
        self.source_entry.grid(row=0, column=1, sticky="ew", padx=5)
        self.source_button = ttk.Button(self.path_frame, command=self._browse_source)
        self.source_button.grid(row=0, column=2, padx=4)
        self.target_label = ttk.Label(self.path_frame, width=15)
        self.target_label.grid(row=1, column=0, sticky="w", pady=3)
        self.target_entry = ttk.Entry(self.path_frame, textvariable=self.target)
        self.target_entry.grid(row=1, column=1, sticky="ew", padx=5)
        self.target_button = ttk.Button(self.path_frame, command=self._browse_target)
        self.target_button.grid(row=1, column=2, padx=4)
        self.path_frame.columnconfigure(1, weight=1)

        self.action_frame = ttk.Frame(self.root, padding=(10, 7))
        self.action_frame.pack(fill="x")
        self.preview_button = ttk.Button(self.action_frame, command=self._generate_preview)
        self.preview_button.pack(side="left", padx=(0, 6))
        self.load_button = ttk.Button(self.action_frame, command=self._load_report)
        self.load_button.pack(side="left", padx=6)
        self.apply_button = ttk.Button(self.action_frame, command=self._apply)
        self.apply_button.pack(side="left", padx=6)
        self.restore_button = ttk.Button(self.action_frame, command=self._restore)
        self.restore_button.pack(side="left", padx=6)
        self.count_label = ttk.Label(self.action_frame)
        self.count_label.pack(side="right", padx=5)

        self.table_frame = ttk.Frame(self.root, padding=(10, 0))
        self.table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(self.table_frame, columns=("source", "match", "kind", "action"), show="headings")
        self.table_scroll = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=self.table_scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        self.table_scroll.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self._selection_changed)
        self.table.bind("<Double-1>", self._cycle_action)

        self.lower_frame = ttk.Frame(self.root, padding=(10, 8))
        self.lower_frame.pack(fill="both", expand=True)
        self.action_label = ttk.Label(self.lower_frame, text="Action:")
        self.action_label.pack(side="left", padx=(0, 5))
        self.action_box = ttk.Combobox(self.lower_frame, state="readonly", width=16, values=ACTION_VALUES)
        self.action_box.pack(side="left")
        self.action_box.set("skip")
        self.action_box.bind("<<ComboboxSelected>>", self._set_selected_action)
        self.bulk_label = ttk.Label(self.lower_frame, text="Apply action to selected rows:")
        self.bulk_label.pack(side="left", padx=(16, 5))
        self.bulk_box = ttk.Combobox(self.lower_frame, state="readonly", width=16, values=ACTION_VALUES)
        self.bulk_box.pack(side="left")
        self.bulk_box.set("skip")
        self.bulk_button = ttk.Button(self.lower_frame, command=self._set_bulk_action)
        self.bulk_button.pack(side="left", padx=5)

        self.preview_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        self.preview_frame.pack(fill="both", expand=True)
        self.preview_labels = []
        for index in range(2):
            pane = ttk.LabelFrame(self.preview_frame, padding=5)
            pane.pack(side="left", fill="both", expand=True, padx=4)
            label = ttk.Label(pane, anchor="center")
            label.pack(fill="both", expand=True)
            self.preview_labels.append(label)

        self.status_bar = ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=5)
        self.status_bar.pack(fill="x", side="bottom")

    def _text(self, key: str) -> str:
        return TEXT[self.language][key]

    def _translate(self) -> None:
        strings = TEXT[self.language]
        self.root.title(strings["title"])
        self.mode_label.configure(text=f'{strings["mode"]}:')
        self.backup_radio.configure(text=strings["backup"])
        self.duplicates_radio.configure(text=strings["duplicates"])
        self.language_label.configure(text=f'{strings["language"]}:')
        self.language_box.configure(values=(strings["chinese"], strings["english"]))
        self.language_box.set(strings["chinese"] if self.language == "zh" else strings["english"])
        self.source_label.configure(text=f'{strings["source"]}:')
        self.source_button.configure(text=strings["browse"])
        self.target_button.configure(text=strings["browse"])
        self.preview_button.configure(text=strings["preview"])
        self.load_button.configure(text=strings["load"])
        self.apply_button.configure(text=strings["apply"])
        self.restore_button.configure(text=strings["restore"])
        for column, heading in zip(("source", "match", "kind", "action"), strings["columns"]):
            self.table.heading(column, text=heading)
        self.table.column("source", width=390, stretch=True)
        self.table.column("match", width=390, stretch=True)
        self.table.column("kind", width=90, stretch=False)
        self.table.column("action", width=105, stretch=False)
        self.action_label.configure(text="操作:" if self.language == "zh" else "Action:")
        self.bulk_label.configure(text="批量设置所选行:" if self.language == "zh" else "Set selected rows:")
        self.bulk_button.configure(text="应用" if self.language == "zh" else "Apply")
        self.status.set(strings["status_ready"])
        self._mode_changed()
        self._selection_changed()

    def _language_changed(self, _event: tk.Event | None = None) -> None:
        self.language = "zh" if self.language_box.current() == 0 else "en"
        self._translate()

    def _mode_changed(self) -> None:
        backup = self.mode.get() == "backup"
        self.target_entry.configure(state="normal")
        self.target_button.configure(state="normal")
        self._translate_mode_labels(backup)

    def _translate_mode_labels(self, backup: bool) -> None:
        target_text = TEXT[self.language]["target"]
        if not backup:
            target_text += "（可选；用于 copy）" if self.language == "zh" else " (optional; for copy)"
        self.target_label.configure(text=f"{target_text}:")

    def _browse_source(self) -> None:
        path = filedialog.askdirectory(title=self._text("choose_source"))
        if path:
            self.source.set(path)

    def _browse_target(self) -> None:
        path = filedialog.askdirectory(title=self._text("choose_target"))
        if path:
            self.target.set(path)

    def _generate_preview(self) -> None:
        source = self.source.get().strip()
        target = self.target.get().strip()
        if not source or (self.mode.get() == "backup" and not target):
            missing = self._text("choose_source" if not source else "choose_target")
            messagebox.showerror(self._text("error"), missing)
            return
        report_path = filedialog.asksaveasfilename(
            title=self._text("save_report"),
            defaultextension=".csv",
            filetypes=(("CSV report", "*.csv"),),
            initialfile="photo_archive_report.csv",
        )
        if not report_path:
            return
        report_destination = Path(report_path)
        self.report_path = report_destination
        self._busy(True, self._text("working"))
        mode = self.mode.get()
        worker = scan_backup if mode == "backup" else scan_duplicates
        arguments = (source, target) if mode == "backup" else (source, target or None)

        def work() -> None:
            try:
                rows = worker(*arguments)
                write_report(report_destination, rows)
                self.messages.put(("scan", rows))
            except Exception as error:
                self.messages.put(("error", error))

        threading.Thread(target=work, daemon=True).start()

    def _load_report(self) -> None:
        path = filedialog.askopenfilename(
            title=self._text("open_report"),
            filetypes=(("CSV report", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            self.rows = read_report(path)
            self.report_path = Path(path)
            self._populate_table()
        except Exception as error:
            messagebox.showerror(self._text("error"), str(error))

    def _populate_table(self) -> None:
        self.table.delete(*self.table.get_children())
        for index, row in enumerate(self.rows):
            self.table.insert(
                "", "end", iid=str(index),
                values=(
                    row.get("SourcePath", ""),
                    row.get("MatchPath", ""),
                    row.get("Type", ""),
                    row.get("Action", ""),
                ),
            )
        self.count_label.configure(text=f'{self._text("count")}: {len(self.rows)}')
        self.status.set(f'{self._text("done")}: {len(self.rows)}')
        self._clear_previews()

    def _selection_changed(self, _event: tk.Event | None = None) -> None:
        selected = self.table.selection()
        if not selected:
            self._clear_previews()
            return
        index = int(selected[0])
        row = self.rows[index]
        self.action_box.set(row.get("Action", "skip"))
        self._show_preview(0, row.get("SourcePath", ""), self._text("preview_source"))
        self._show_preview(1, row.get("MatchPath", ""), self._text("preview_match"))

    def _clear_previews(self) -> None:
        for label in getattr(self, "preview_labels", []):
            label.configure(image="", text=self._text("no_image"))
        self.images = [None, None]

    def _show_preview(self, slot: int, filename: str, title: str) -> None:
        label = self.preview_labels[slot]
        self.images[slot] = None
        if not filename:
            label.configure(image="", text=f"{title}\n{self._text('no_image')}")
            return
        try:
            with Image.open(filename) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((480, 220))
                photo = ImageTk.PhotoImage(image.copy())
            self.images[slot] = photo
            label.configure(image=photo, text=f"{title}\n{filename}", compound="top", wraplength=500)
        except (OSError, ValueError):
            label.configure(image="", text=f"{title}\n{filename}\n{self._text('no_image')}", wraplength=500)

    def _cycle_action(self, event: tk.Event) -> None:
        item = self.table.identify_row(event.y)
        if not item:
            return
        row = self.rows[int(item)]
        row["Action"] = ACTION_VALUES[(ACTION_VALUES.index(row["Action"]) + 1) % len(ACTION_VALUES)]
        self._update_row(int(item))

    def _set_selected_action(self, _event: tk.Event | None = None) -> None:
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        self.rows[index]["Action"] = self.action_box.get()
        self._update_row(index)

    def _set_bulk_action(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo(self._text("title"), self._text("select_row"))
            return
        action = self.bulk_box.get()
        for item in selected:
            index = int(item)
            self.rows[index]["Action"] = action
            self.table.set(str(index), "action", action)
        self.action_box.set(action)
        self._save_current_report()

    def _update_row(self, index: int) -> None:
        row = self.rows[index]
        self.table.set(str(index), "action", row["Action"])
        self.action_box.set(row["Action"])
        self._save_current_report()

    def _save_current_report(self) -> None:
        if self.report_path is not None:
            try:
                write_report(self.report_path, self.rows)
            except OSError as error:
                messagebox.showerror(self._text("error"), str(error))

    def _apply(self) -> None:
        if self.report_path is None or not self.rows:
            messagebox.showinfo(self._text("title"), self._text("select_row"))
            return
        if not any(row["Action"] in ("copy", "quarantine") for row in self.rows):
            messagebox.showinfo(self._text("title"), self._text("status_ready"))
            return
        if not messagebox.askyesno(self._text("apply"), self._text("confirm_apply")):
            return
        self._busy(True, self._text("applying"))
        rows = [row.copy() for row in self.rows]
        path = self.report_path

        def work() -> None:
            try:
                results = apply_report(rows, path)
                self.messages.put(("result", results))
            except Exception as error:
                self.messages.put(("error", error))

        threading.Thread(target=work, daemon=True).start()

    def _restore(self) -> None:
        path = filedialog.askopenfilename(
            title=self._text("open_log"),
            filetypes=(("JSON Lines", "*.jsonl"), ("All files", "*.*")),
        )
        if not path or not messagebox.askyesno(self._text("restore"), self._text("confirm_restore")):
            return
        self._busy(True, self._text("restoring"))

        def work() -> None:
            try:
                results = restore_from_log(path)
                self.messages.put(("result", results))
            except Exception as error:
                self.messages.put(("error", error))

        threading.Thread(target=work, daemon=True).start()

    def _busy(self, busy: bool, message: str) -> None:
        self.status.set(message)
        state = "disabled" if busy else "normal"
        self.preview_button.configure(state=state)
        self.load_button.configure(state=state)
        self.apply_button.configure(state=state)
        self.restore_button.configure(state=state)
        for control in (
            self.source_entry,
            self.source_button,
            self.target_entry,
            self.target_button,
            self.backup_radio,
            self.duplicates_radio,
            self.language_box,
            self.action_box,
            self.bulk_box,
            self.bulk_button,
        ):
            control.state(["disabled"] if busy else ["!disabled"])
        self.table.state(["disabled"] if busy else ["!disabled"])

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                self._busy(False, self._text("status_ready"))
                if kind == "scan":
                    self.rows = payload
                    self._populate_table()
                elif kind == "result":
                    results = payload
                    self.status.set(f'{self._text("done")}: {len(results)}')
                    messagebox.showinfo(self._text("done"), "\n".join(results) if results else self._text("done"))
                    if self.report_path is not None:
                        self._populate_table()
                elif kind == "error":
                    messagebox.showerror(self._text("error"), str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)


def run() -> None:
    root = tk.Tk()
    ArchiveApp(root)
    root.mainloop()
