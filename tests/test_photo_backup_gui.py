import shutil
import subprocess
import tempfile
import tkinter as tk
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from photo_backup_gui import PhotoBackupApp, extract_video_preview, format_beijing_time
from photo_backup import read_media_properties


class PhotoBackupGuiTests(unittest.TestCase):
    def test_gui_builds_and_switches_to_folder_mode(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")

        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            self.assertIsNotNone(app.table)
            self.assertIsNotNone(app.preview_button)
            self.assertIsNotNone(app.load_button)
            self.assertIsNotNone(app.save_button)
            self.assertIsNotNone(app.apply_button)
            self.assertIsInstance(app.body_split, tk.PanedWindow)
            self.assertIsInstance(app.results_split, tk.PanedWindow)
            self.assertEqual(len(app.body_split.panes()), 2)
            self.assertEqual(len(app.results_split.panes()), 2)
            self.assertTrue(hasattr(app, "allow_visual_quarantine"))
            self.assertTrue(hasattr(app, "allow_video_quarantine"))

            with tempfile.TemporaryDirectory() as folder:
                app.source.set(folder)
                app.mode.set("folder")
                app._update_mode_controls()
                self.assertEqual(app.target.get(), folder)
                self.assertEqual(str(app.target_entry.cget("state")), "disabled")
                self.assertEqual(app.quarantine.get(), str(Path(folder).parent / "Quarantine"))
                self.assertEqual(Path(app.report.get()).parent, Path(folder).parent / "Quarantine")
                self.assertEqual(Path(app.audit_log.get()).parent, Path(folder).parent / "Quarantine")
                self.assertTrue(Path(app.report.get()).name.startswith("review-"))
                self.assertTrue(Path(app.audit_log.get()).name.startswith("actions-"))

            root.deiconify()
            root.update()
            self.assertEqual(str(root.state()), "zoomed")
            self.assertTrue(root.winfo_viewable())
            window_bottom = root.winfo_rooty() + root.winfo_height()
            for button in (
                app.preview_button,
                app.load_button,
                app.save_button,
                app.apply_button,
                app.project_link_button,
            ):
                self.assertLessEqual(button.winfo_rooty(), window_bottom)
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), window_bottom)
        finally:
            root.destroy()

    def test_gui_defaults_to_chinese_and_can_toggle_english_without_changing_rows(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            app.rows = [{
                "operation": "copy", "decision": "copy", "source": "a.jpg", "matched_path": "",
                "reason": "test",
            }]
            app.visible_rows = [0]
            app._render_page()
            self.assertEqual(app.language.get(), "zh")
            self.assertEqual(root.title(), "照片归档管理器")
            self.assertEqual(app.table.item("0", "values")[0], "复制")
            self.assertEqual(app.operation_filter.get(), "全部条目")
            self.assertIn("点亮 Star", app.community_message.get())
            self.assertEqual(app.project_link_button.cget("text"), "github.com/zny05/PhotoArchiveManager")
            with patch("photo_backup_gui.webbrowser.open", return_value=True) as open_project:
                app.project_link_button.invoke()
            open_project.assert_called_once_with("https://github.com/zny05/PhotoArchiveManager", new=2)

            app.table.selection_set("0")
            app._selection_changed()
            self.assertEqual(app.selected_decision.get(), "复制")
            app._toggle_language()

            self.assertEqual(root.title(), "Photo Archive Manager")
            self.assertEqual(app.table.item("0", "values")[0], "copy")
            self.assertEqual(app.selected_decision.get(), "copy")
            self.assertEqual(app.rows[0]["decision"], "copy")
            self.assertEqual(app.operation_filter.get(), "All entries")
            self.assertIn("please consider giving it a star", app.community_message.get())
            app._toggle_language()
            self.assertEqual(app.table.item("0", "values")[0], "复制")
            self.assertEqual(app.rows[0]["decision"], "copy")
            self.assertIn("点亮 Star", app.community_message.get())
        finally:
            root.destroy()

    def test_file_properties_are_rendered_in_preview_panel(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")

        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            app._render_media_properties(
                "source",
                Path("sample.jpg"),
                (2048, 1_760_000_000_000_000_000),
                {
                    "File:FileType": "JPEG",
                    "File:ImageWidth": 80,
                    "File:ImageHeight": 48,
                    "EXIF:Model": "Example Phone",
                    "EXIF:DateTimeOriginal": "2025:06:07 08:09:10",
                },
                None,
            )
            text = app.metadata_views["source"].get("1.0", "end")
            self.assertIn("2,048 bytes", text)
            self.assertIn("Example Phone", text)
            self.assertIn("80 x 48", text)
            self.assertIn("2025-06-07 08:09:10 UTC+08:00", text)
        finally:
            root.destroy()

    def test_file_time_is_displayed_in_beijing_timezone(self):
        utc_instant = datetime(2020, 1, 20, 17, 38, 26, tzinfo=timezone.utc).timestamp()
        expected = "2020-01-21 01:38:26 UTC+08:00"

        self.assertEqual(format_beijing_time(utc_instant), expected)
        self.assertEqual(format_beijing_time("2020:01:21 01:38:26"), expected)
        self.assertEqual(format_beijing_time("2020-01-20T17:38:26Z"), expected)

    def test_video_first_frame_and_properties_render_in_gui(self):
        ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("FFmpeg is not available in PATH")
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")

        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            with tempfile.TemporaryDirectory() as folder:
                video = Path(folder) / "sample.mp4"
                result = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=red:s=160x90:r=2:d=1",
                        "-c:v",
                        "mpeg4",
                        "-pix_fmt",
                        "yuv420p",
                        "-y",
                        str(video),
                    ],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if result.returncode:
                    self.skipTest("FFmpeg cannot encode the synthetic test video")

                image, error = extract_video_preview(video)
                self.assertIsNone(error)
                self.assertIsNotNone(image)

                metadata, warning = read_media_properties([video])
                self.assertIsNone(warning)
                properties = metadata[video.resolve()]
                width = properties.get("QuickTime:ImageWidth") or properties.get("File:ImageWidth")
                height = properties.get("QuickTime:ImageHeight") or properties.get("File:ImageHeight")
                self.assertEqual((width, height), (160, 90))

                app.preview_request_id = 1
                app._set_preview("source", str(video), app.preview_request_id)
                app._load_media_properties((("source", str(video)), ("match", "")))
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    root.update()
                    properties_text = app.metadata_views["source"].get("1.0", "end")
                    if (
                        app.preview_canvases["source"].find_withtag("preview-image")
                        and "Reading metadata…" not in properties_text
                    ):
                        break
                self.assertTrue(app.preview_canvases["source"].find_withtag("preview-image"))
                self.assertIn("时长:", app.metadata_views["source"].get("1.0", "end"))
                self.assertIn("分辨率: 160 x 90 px", app.metadata_views["source"].get("1.0", "end"))
        finally:
            root.destroy()

    def test_selecting_visual_row_renders_both_images(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")

        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "source.jpg"
                matched = Path(folder) / "matched.jpg"
                Image.new("RGB", (100, 70), "navy").save(source)
                Image.new("RGB", (100, 70), "orange").save(matched)
                app.rows = [{
                    "operation": "review_target_visual",
                    "decision": "pending",
                    "source": str(source),
                    "matched_path": str(matched),
                    "phash_distance": 2,
                    "reason": "test visual pair",
                }]
                app.visible_rows = [0]
                app._render_page()
                app.table.selection_set("0")
                app._selection_changed()
                root.update_idletasks()

                self.assertTrue(
                    app.preview_canvases["source"].find_withtag("preview-image"),
                    app.preview_captions["source"].get(),
                )
                self.assertTrue(
                    app.preview_canvases["match"].find_withtag("preview-image"),
                    app.preview_captions["match"].get(),
                )
        finally:
            root.destroy()


    def test_bulk_decision_applies_to_all_filtered_rows_and_undoes(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")

        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            app.rows = [
                {"operation": "review_target_visual", "decision": "pending", "source": f"a{i}.jpg"}
                for i in range(1000)
            ] + [
                {"operation": "review_source_visual", "decision": "pending", "source": "s.jpg"},
                {"operation": "move_exact", "decision": "quarantine", "source": "m.jpg"},
            ]
            app.operation_filter.set("review_target_visual")
            app._refresh_rows()
            self.assertEqual(len(app.visible_rows), 1000)

            app.bulk_decision.set("quarantine")
            with patch("photo_backup_gui.messagebox.askyesno", return_value=True):
                app._set_bulk_decision()
            self.assertEqual(sum(r["decision"] == "quarantine" for r in app.rows), 1001)
            self.assertEqual(app.rows[1000]["decision"], "pending")

            app._undo_bulk_decision()
            self.assertTrue(all(r["decision"] == "pending" for r in app.rows[:1001]))
            self.assertEqual(app.rows[1001]["decision"], "quarantine")
        finally:
            root.destroy()


    def _pump_until(self, root, condition, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            root.update()
            time.sleep(0.02)
        return condition()

    def test_preview_runs_from_gui_with_progress_and_default_output_folder(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            with tempfile.TemporaryDirectory() as parent:
                folder = Path(parent) / "Photos"
                folder.mkdir()
                (folder / "a.jpg").write_bytes(b"same")
                (folder / "b.jpg").write_bytes(b"same")
                app.mode.set("folder")
                app.source.set(str(folder))
                app._update_mode_controls()
                app.content_id_check.set(False)
                app.visual_check.set(False)

                app._start_preview()
                self.assertTrue(self._pump_until(root, lambda: not app.busy and bool(app.rows)))

                expected_dir = (Path(parent) / "Quarantine").resolve()
                self.assertEqual(Path(app.quarantine.get()).resolve(), expected_dir)
                self.assertTrue(Path(app.report.get()).is_file())
                self.assertEqual(Path(app.report.get()).resolve().parent, expected_dir)
                self.assertEqual(float(app.progress_bars["preview"]["value"]), 100.0)
                self.assertIn("Preview complete", app.preview_progress_text.get())

                # A second preview must not trip over the first report's timestamped name.
                first_report = app.report.get()
                app._refresh_default_paths()
                app.report.set(first_report)
                app._auto_values["report"] = first_report
                app._start_preview()
                self.assertTrue(self._pump_until(root, lambda: not app.busy and app.report.get() != first_report))
        finally:
            root.destroy()

    def test_apply_shows_progress_summary_and_moves_files_into_quarantine(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            with tempfile.TemporaryDirectory() as parent:
                folder = Path(parent) / "Photos"
                folder.mkdir()
                (folder / "a.jpg").write_bytes(b"same")
                (folder / "b.jpg").write_bytes(b"same")
                app.mode.set("folder")
                app.source.set(str(folder))
                app._update_mode_controls()
                app.content_id_check.set(False)
                app.visual_check.set(False)
                app._start_preview()
                self.assertTrue(self._pump_until(root, lambda: not app.busy and bool(app.rows)))

                app.allow_quarantine.set(True)
                app.confirm_changes.set(True)
                with patch("photo_backup_gui.messagebox.askyesno", return_value=True), patch(
                    "photo_backup_gui.messagebox.showinfo"
                ) as info:
                    app._confirm_apply()
                    self.assertTrue(self._pump_until(root, lambda: info.called))

                self.assertTrue((Path(parent) / "Quarantine" / "b.jpg").is_file())
                self.assertFalse((folder / "b.jpg").exists())
                self.assertIn("已移入隔离区 1", app.apply_progress_text.get())
                self.assertIn("移入隔离目录：1", info.call_args.args[1])
        finally:
            root.destroy()

    def test_auto_pick_applies_preference_in_background_and_can_be_undone(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            with tempfile.TemporaryDirectory() as parent:
                small, large = Path(parent) / "small.jpg", Path(parent) / "large.jpg"
                small.write_bytes(b"s" * 10)
                large.write_bytes(b"L" * 4000)
                app.rows = [{
                    "operation": "review_target_visual", "decision": "pending", "source": str(large),
                    "matched_path": str(small), "sha256": "L", "matched_sha256": "s",
                    "source_sig": None, "matched_sig": None,
                }]
                app.visible_rows = [0]
                app.keep_criteria[0].set("Larger file size")
                for extra in app.keep_criteria[1:]:
                    extra.set("(none)")

                app._run_auto_pick()
                self.assertTrue(self._pump_until(root, lambda: not app.busy))

                self.assertEqual(Path(app.rows[0]["source"]), small)
                self.assertEqual(app.rows[0]["decision"], "quarantine")
                self.assertIn("隔离 1", app.apply_progress_text.get())

                app._undo_bulk_decision()
                self.assertEqual(Path(app.rows[0]["source"]), large)
                self.assertEqual(app.rows[0]["decision"], "pending")
        finally:
            root.destroy()

    def test_selected_entry_explains_what_apply_will_do(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display is unavailable: {error}")
        root.withdraw()
        try:
            app = PhotoBackupApp(root)
            app.header = {"target_root": "C:\\Photos", "quarantine_root": "C:\\Quarantine"}
            text = app._entry_details({
                "operation": "move_exact", "decision": "quarantine", "source": "C:\\Photos\\2020\\b.jpg",
                "matched_path": "C:\\Photos\\2020\\a.jpg", "sha256": "abc", "matched_sha256": "abc",
                "reason": "Byte-identical common still image already exists in target; move to recoverable quarantine.",
            })
            self.assertIn("移入隔离区", text)
            self.assertIn("隔离目标：C:\\Quarantine\\2020\\b.jpg", text)
            self.assertIn("字节完全相同", text)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()