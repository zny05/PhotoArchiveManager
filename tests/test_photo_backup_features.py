import argparse
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import photo_backup
from photo_backup import (
    BackupError,
    KeepPreference,
    apply_report,
    plan_keep_decisions,
    preview,
    read_media_properties,
    read_resolution,
    sha256_file,
)


class FeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.folder = self.root / "Photos"
        self.quarantine = self.root / "Quarantine"
        self.report = self.quarantine / "review.jsonl"
        self.log = self.quarantine / "actions.jsonl"
        self.folder.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def folder_preview(self, **extra):
        preview(argparse.Namespace(
            mode="folder", source=str(self.folder), target=str(self.folder), report=str(self.report),
            quarantine=str(self.quarantine), phash_distance=4, content_id_check=False, **extra,
        ))

    def read_report(self):
        with self.report.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def write_report(self, rows):
        with self.report.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def apply(self, **extra):
        values = dict(
            report=str(self.report), source=str(self.folder), target=str(self.folder), log=str(self.log),
            quarantine=None, confirm=True, allow_quarantine=True, allow_visual_quarantine=True,
            allow_video_quarantine=True,
        )
        values.update(extra)
        return apply_report(argparse.Namespace(**values))


class ApplyFixTests(FeatureTestCase):
    def test_report_and_log_may_live_in_the_quarantine_folder(self):
        (self.folder / "a.jpg").write_bytes(b"same image")
        (self.folder / "b.jpg").write_bytes(b"same image")
        self.folder_preview()
        phases, results = [], []

        self.apply(
            progress_callback=lambda text, current=None, total=None: phases.append(text),
            result_callback=results.append,
        )

        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertFalse((self.folder / "b.jpg").exists())
        self.assertEqual((self.quarantine / "b.jpg").read_bytes(), b"same image")
        self.assertEqual(results[0]["quarantined"], 1)
        self.assertTrue(any(text.startswith("Preparing") for text in phases))
        self.assertTrue(any(text.startswith("Applying") for text in phases))

    def test_path_problems_fail_before_any_file_is_read(self):
        (self.folder / "a.jpg").write_bytes(b"same image")
        (self.folder / "b.jpg").write_bytes(b"same image")
        self.folder_preview()

        with patch("photo_backup.sha256_file", side_effect=AssertionError("file was hashed")):
            with self.assertRaises(BackupError):
                self.apply(allow_quarantine=False)
            with self.assertRaises(BackupError):
                self.apply(log=str(self.folder / "inside-scanned-folder.jsonl"))

    def test_unchanged_files_are_not_rehashed_during_apply(self):
        source, target = self.root / "D", self.root / "F"
        source.mkdir()
        target.mkdir()
        (source / "same.mov").write_bytes(b"identical" * 1000)
        (target / "same.mov").write_bytes(b"identical" * 1000)
        (source / "new.mov").write_bytes(b"brand new")
        preview(argparse.Namespace(
            source=str(source), target=str(target), report=str(self.report),
            quarantine=str(self.quarantine), phash_distance=4, content_id_check=False,
        ))
        report_resolved = self.report.resolve()
        hashed = []
        real_hash = sha256_file

        def counting_hash(path):
            hashed.append(Path(path).resolve())
            return real_hash(path)

        with patch("photo_backup.sha256_file", side_effect=counting_hash):
            apply_report(argparse.Namespace(
                report=str(self.report), source=str(source), target=str(target), log=str(self.log),
                quarantine=None, confirm=True, allow_quarantine=False, allow_visual_quarantine=False,
                allow_video_quarantine=False,
            ))

        self.assertEqual([path for path in hashed if path != report_resolved], [])
        self.assertEqual((target / "new.mov").read_bytes(), b"brand new")

    def test_apply_executes_the_recorded_decisions_without_reading_any_media(self):
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            (self.folder / name).write_bytes(b"same image")
        self.folder_preview()
        (self.folder / "c.jpg").write_bytes(b"edited after the preview")
        hashed = []
        real_hash = sha256_file

        def counting_hash(path):
            hashed.append(Path(path).resolve())
            return real_hash(path)

        with patch("photo_backup.sha256_file", side_effect=counting_hash):
            self.apply()

        self.assertEqual([path for path in hashed if path != self.report.resolve()], [])
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertEqual((self.quarantine / "b.jpg").read_bytes(), b"same image")
        self.assertEqual((self.quarantine / "c.jpg").read_bytes(), b"edited after the preview")

    def test_plan_that_removes_every_copy_is_refused(self):
        (self.folder / "a.jpg").write_bytes(b"same image")
        (self.folder / "b.jpg").write_bytes(b"same image")
        self.folder_preview()
        rows = self.read_report()
        forward = rows[1]
        backward = dict(forward, source=forward["matched_path"], matched_path=forward["source"],
                        source_sig=forward["matched_sig"], matched_sig=forward["source_sig"])
        self.write_report([rows[0], forward, backward])

        with self.assertRaises(BackupError):
            self.apply()

        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertTrue((self.folder / "b.jpg").exists())


class KeepPreferenceTests(FeatureTestCase):
    def test_exact_group_keeps_the_preferred_file_type(self):
        for name in ("a.jpg", "b.jpeg", "c.jpg"):
            (self.folder / name).write_bytes(b"same image")
        self.folder_preview()
        rows = self.read_report()[1:]

        plan = plan_keep_decisions(
            rows, range(len(rows)), KeepPreference(criteria=("type",), type_order=(".jpeg", ".jpg")),
            only_pending=False,
        )
        for index, row in plan.changes.items():
            rows[index] = row
        self.write_report([self.read_report()[0], *rows])
        self.apply()

        self.assertTrue((self.folder / "b.jpeg").exists())
        self.assertFalse((self.folder / "a.jpg").exists())
        self.assertFalse((self.folder / "c.jpg").exists())
        self.assertEqual(plan.stats["quarantine"], 2)

    def test_visual_pair_keeps_the_larger_file_whichever_side_it_is_on(self):
        (self.folder / "small.jpg").write_bytes(b"s" * 10)
        (self.folder / "large.jpg").write_bytes(b"L" * 5000)
        hashes = {(self.folder / "small.jpg").resolve(): 0, (self.folder / "large.jpg").resolve(): 1}
        with patch("photo_backup.phash_supported", return_value=True), patch(
            "photo_backup.load_phash", side_effect=lambda path: hashes[Path(path)]
        ):
            self.folder_preview()
        rows = self.read_report()[1:]
        self.assertEqual([row["operation"] for row in rows], ["review_target_visual"])

        plan = plan_keep_decisions(rows, [0], KeepPreference(criteria=("size_large",)))

        changed = plan.changes[0]
        self.assertEqual(Path(changed["source"]).name, "small.jpg")
        self.assertEqual(Path(changed["matched_path"]).name, "large.jpg")
        self.assertEqual(changed["decision"], "quarantine")
        self.assertEqual(changed["sha256"], sha256_file(self.folder / "small.jpg"))
        self.assertEqual(changed["matched_sha256"], sha256_file(self.folder / "large.jpg"))

    def test_chain_where_the_kept_file_is_removed_elsewhere_is_left_for_review(self):
        for name, size in (("a.jpg", 1), ("b.jpg", 2), ("c.jpg", 3)):
            (self.folder / name).write_bytes(b"x" * size)

        def row(source, matched):
            return {
                "operation": "review_target_visual", "decision": "pending",
                "source": str(self.folder / source), "matched_path": str(self.folder / matched),
                "sha256": "s-" + source, "matched_sha256": "s-" + matched,
                "source_sig": None, "matched_sig": None,
            }

        rows = [row("a.jpg", "b.jpg"), row("b.jpg", "c.jpg")]
        plan = plan_keep_decisions(rows, [0, 1], KeepPreference(criteria=("size_large",)))

        self.assertNotIn(0, plan.changes)
        self.assertEqual(Path(plan.changes[1]["source"]).name, "b.jpg")
        self.assertEqual(plan.stats["left_manual"], 1)
        self.assertEqual(plan.stats["quarantine"], 1)

    def test_source_visual_rows_become_copy_or_skip(self):
        (self.folder / "source.jpg").write_bytes(b"s" * 5000)
        (self.folder / "match.jpg").write_bytes(b"m" * 10)
        rows = [{
            "operation": "review_source_visual", "decision": "pending",
            "source": str(self.folder / "source.jpg"), "matched_path": str(self.folder / "match.jpg"),
            "sha256": "s", "matched_sha256": "m", "source_sig": None, "matched_sig": None,
        }]

        plan = plan_keep_decisions(rows, [0], KeepPreference(criteria=("size_large",)))

        self.assertEqual(plan.changes[0]["decision"], "copy")

    def test_unknown_criterion_is_rejected(self):
        with self.assertRaises(BackupError):
            plan_keep_decisions([], [], KeepPreference(criteria=("nonsense",)))


class ResolutionTests(FeatureTestCase):
    def test_unicode_and_emoji_paths_still_report_resolution_and_metadata(self):
        from PIL import Image

        album = self.folder / "相册 2020"
        album.mkdir()
        paths = [album / name for name in ("IMG_20200121_013826.jpeg", "中文照片.jpg", "emoji_😀.jpg")]
        for path in paths:
            Image.new("RGB", (80, 48), "teal").save(path)

        properties, warning = read_media_properties(paths)

        self.assertIsNone(warning)
        for path in paths:
            self.assertEqual(properties[path.resolve()]["Computed:Width"], 80, path.name)
            self.assertEqual(properties[path.resolve()]["Computed:Height"], 48, path.name)
            self.assertEqual(properties[path.resolve()]["File:FileType"], "JPEG", path.name)

    def test_resolution_is_available_even_when_exiftool_is_missing(self):
        from PIL import Image

        image = self.folder / "plain.png"
        Image.new("RGB", (33, 21), "navy").save(image)

        with patch("photo_backup.find_exiftool_executable", return_value=None):
            properties, warning = read_media_properties([image])

        self.assertIsNotNone(warning)
        self.assertEqual(properties[image.resolve()]["Computed:Width"], 33)
        self.assertEqual(read_resolution(image), (33, 21))

    def test_video_resolution_comes_from_ffprobe(self):
        ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("FFmpeg is not available in PATH")
        video = self.folder / "视频.mp4"
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=160x90:r=2:d=1",
             "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-y", str(video)],
            capture_output=True, timeout=20, check=False,
        )
        if result.returncode:
            self.skipTest("FFmpeg cannot encode the synthetic test video")

        self.assertEqual(read_resolution(video), (160, 90))
        with patch("photo_backup.find_ffprobe_executable", return_value=None):
            self.assertEqual(read_resolution(video), (160, 90))


if __name__ == "__main__":
    unittest.main()
