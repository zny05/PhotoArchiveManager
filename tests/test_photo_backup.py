import argparse
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from photo_backup import (
    BackupError,
    apply_report,
    find_exiftool_executable,
    load_phash,
    phash_supported,
    preview,
    read_media_properties,
)


class PhotoBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "D"
        self.target = self.root / "F"
        self.report = self.root / "review.jsonl"
        self.log = self.root / "actions.jsonl"
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_preview(self):
        args = argparse.Namespace(
            source=str(self.source),
            target=str(self.target),
            report=str(self.report),
            quarantine=str(self.target / "PhotoQuarantine"),
            phash_distance=4,
        )
        preview(args)

    def read_report(self):
        with self.report.open("r", encoding="utf-8") as report_file:
            return [json.loads(line) for line in report_file if line.strip()]

    def test_preview_and_apply_copy_new_file(self):
        source_file = self.source / "2024" / "IMG_0001.MOV"
        source_file.parent.mkdir()
        source_file.write_bytes(b"motion data")

        self.make_preview()
        rows = self.read_report()
        self.assertEqual([row["operation"] for row in rows[1:]], ["copy"])

        with self.assertRaises(BackupError):
            apply_report(argparse.Namespace(
                report=str(self.report),
                source=str(self.source),
                target=str(self.target),
                log=str(self.log),
                quarantine=None,
                confirm=False,
                allow_quarantine=False,
                allow_visual_quarantine=False,
                allow_video_quarantine=False,
            ))
        self.assertFalse((self.target / "2024" / "IMG_0001.MOV").exists())

        self.apply(confirm=True)
        self.assertEqual((self.target / "2024" / "IMG_0001.MOV").read_bytes(), b"motion data")
        self.assertTrue(source_file.exists())
        self.assertIn('"action": "copied"', self.log.read_text(encoding="utf-8"))

    def test_destination_conflict_prevents_all_copies(self):
        (self.source / "a.jpg").write_bytes(b"new a")
        (self.source / "b.jpg").write_bytes(b"new b")
        self.make_preview()
        (self.target / "b.jpg").write_bytes(b"unrelated existing file")

        with self.assertRaises(BackupError):
            self.apply(confirm=True)

        self.assertFalse((self.target / "a.jpg").exists())
        self.assertEqual((self.target / "b.jpg").read_bytes(), b"unrelated existing file")

    def test_exact_source_match_is_skipped(self):
        (self.source / "IMG_0001.MOV").write_bytes(b"same media")
        (self.target / "IMG_0001.MOV").write_bytes(b"same media")

        self.make_preview()

        rows = self.read_report()[1:]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "skip")
        self.assertEqual(rows[0]["decision"], "skip")

    def test_apply_requires_roots_from_report(self):
        source_file = self.source / "IMG_0001.MOV"
        source_file.write_bytes(b"media")
        self.make_preview()
        other_target = self.root / "other-F"
        other_target.mkdir()

        with self.assertRaises(BackupError):
            apply_report(argparse.Namespace(
                report=str(self.report),
                source=str(self.source),
                target=str(other_target),
                log=str(self.log),
                quarantine=None,
                confirm=True,
                allow_quarantine=False,
                allow_visual_quarantine=False,
                allow_video_quarantine=False,
            ))

        self.assertFalse((self.target / "IMG_0001.MOV").exists())

    def test_folder_mode_quarantines_duplicate_in_place(self):
        root = self.target
        first = root / "a.jpg"
        duplicate = root / "b.jpg"
        first.write_bytes(b"same image")
        duplicate.write_bytes(b"same image")
        quarantine = self.root / "PhotoQuarantine"

        preview(argparse.Namespace(
            mode="folder",
            source=str(root),
            target=str(root),
            report=str(self.report),
            quarantine=str(quarantine),
            phash_distance=4,
        ))

        rows = self.read_report()
        self.assertEqual(rows[0]["mode"], "folder")
        self.assertTrue(any(row["operation"] == "move_exact" for row in rows[1:]))
        apply_report(argparse.Namespace(
            report=str(self.report),
            source=str(root),
            target=str(root),
            log=str(self.log),
            quarantine=None,
            confirm=True,
            allow_quarantine=True,
            allow_visual_quarantine=False,
            allow_video_quarantine=False,
        ))

        self.assertTrue(first.exists())
        self.assertFalse(duplicate.exists())
        self.assertEqual((quarantine / "b.jpg").read_bytes(), b"same image")

    def test_bundled_exiftool_reads_media_properties(self):
        from PIL import Image

        image_path = self.source / "metadata-test.jpg"
        Image.new("RGB", (80, 48), "teal").save(image_path)

        executable = find_exiftool_executable()
        self.assertIsNotNone(executable)
        properties, warning = read_media_properties([image_path])

        self.assertIsNone(warning)
        self.assertIn(image_path.resolve(), properties)
        self.assertEqual(properties[image_path.resolve()]["File:FileType"], "JPEG")
        self.assertEqual(properties[image_path.resolve()]["File:ImageWidth"], 80)

    def test_relative_path_conflict_is_reported_not_overwritten(self):
        (self.source / "IMG_0001.MOV").write_bytes(b"new source content")
        (self.target / "IMG_0001.MOV").write_bytes(b"different target content")

        self.make_preview()

        rows = self.read_report()[1:]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "collision")
        self.assertEqual((self.target / "IMG_0001.MOV").read_bytes(), b"different target content")

    def test_conflicting_duplicate_path_does_not_hide_available_copy(self):
        (self.source / "a.jpg").write_bytes(b"same source content")
        (self.source / "b.jpg").write_bytes(b"same source content")
        (self.target / "a.jpg").write_bytes(b"different target content")

        self.make_preview()

        rows = self.read_report()[1:]
        self.assertCountEqual([row["operation"] for row in rows], ["collision", "copy"])
        self.assertEqual((self.target / "a.jpg").read_bytes(), b"different target content")

    def test_source_visual_candidates_are_pending_for_review(self):
        first = self.source / "a.jpg"
        second = self.source / "b.jpg"
        first.write_bytes(b"first image bytes")
        second.write_bytes(b"second image bytes")
        fake_hashes = {first.resolve(): 0, second.resolve(): 1}

        with patch("photo_backup.phash_supported", return_value=True), patch(
            "photo_backup.load_phash", side_effect=lambda path: fake_hashes[Path(path)]
        ):
            self.make_preview()

        rows = self.read_report()[1:]
        review_rows = [row for row in rows if row["operation"] == "review_source_visual"]
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0]["decision"], "pending")
        self.assertEqual(Path(review_rows[0]["matched_path"]), first.resolve())
        self.assertEqual(review_rows[0]["phash_distance"], 1)

        rows = self.read_report()
        rows[2]["decision"] = "copy"
        self.write_report(rows)
        self.apply(confirm=True)
        self.assertEqual((self.target / "b.jpg").read_bytes(), b"second image bytes")

    @unittest.skipUnless(phash_supported(), "Optional pHash dependencies are not installed")
    def test_real_cross_format_visual_candidate(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (96, 72), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 10, 45, 60), fill="navy")
        draw.ellipse((52, 18, 84, 50), fill="orange")
        image.save(self.source / "a.jpg", quality=95)
        image.save(self.source / "b.png")

        self.make_preview()

        rows = self.read_report()[1:]
        self.assertTrue(any(row["operation"] == "review_source_visual" for row in rows))
        self.assertIsNotNone(load_phash(self.source / "a.jpg"))

    def write_report(self, rows):
        with self.report.open("w", encoding="utf-8", newline="\n") as report_file:
            for row in rows:
                report_file.write(json.dumps(row) + "\n")

    def test_exact_duplicates_require_authorization_and_are_quarantined(self):
        (self.target / "a.jpg").write_bytes(b"identical photo bytes")
        (self.target / "b.jpg").write_bytes(b"identical photo bytes")
        self.make_preview()
        self.assertTrue(any(row["operation"] == "move_exact" for row in self.read_report()[1:]))

        with self.assertRaises(BackupError):
            self.apply(confirm=True, allow_quarantine=False)
        self.assertTrue((self.target / "b.jpg").exists())

        self.apply(confirm=True, allow_quarantine=True)
        self.assertTrue((self.target / "a.jpg").exists())
        self.assertFalse((self.target / "b.jpg").exists())
        self.assertEqual(
            (self.target / "PhotoQuarantine" / "b.jpg").read_bytes(),
            b"identical photo bytes",
        )
        self.assertIn('"action": "moved_to_quarantine"', self.log.read_text(encoding="utf-8"))

    def apply(self, *, confirm, allow_quarantine=False):
        return apply_report(argparse.Namespace(
            report=str(self.report),
            source=str(self.source),
            target=str(self.target),
            log=str(self.log),
            quarantine=None,
            confirm=confirm,
            allow_quarantine=allow_quarantine,
            allow_visual_quarantine=False,
            allow_video_quarantine=False,
        ))


if __name__ == "__main__":
    unittest.main()