import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from photo_archive_manager.core import (
    ACTION_VALUES,
    apply_report,
    read_report,
    restore_from_log,
    scan_backup,
    scan_duplicates,
    write_report,
)


class ArchiveCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _image(self, path: Path, variant: int = 0):
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (64, 64), "white")
        draw = ImageDraw.Draw(image)
        if variant == 2:
            draw.rectangle((0, 0, 63, 63), fill="black")
        offset = 1 if variant == 1 else 0
        draw.rectangle((8 + offset, 8, 44 + offset, 44), fill="black")
        image.save(path, format="PNG")
        return path

    def test_backup_preview_records_exact_and_new_files_then_copies_with_relative_path(self):
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        target.mkdir()
        exact = self._image(source / "nested" / "same.png")
        (target / "already.png").write_bytes(exact.read_bytes())
        self._image(source / "nested" / "new.png", variant=2)

        rows = scan_backup(source, target)
        actions = {Path(row["SourcePath"]).name: row["Action"] for row in rows}
        self.assertEqual(actions, {"same.png": "skip", "new.png": "copy"})

        report = self.root / "report.csv"
        write_report(report, rows)
        loaded = read_report(report)
        results = apply_report(loaded, report)
        self.assertEqual(len(results), 1)
        self.assertTrue((target / "nested" / "new.png").exists())
        self.assertFalse((target / "nested" / "same.png").exists())
        operation_log = target / "Quarantine" / "action_log.jsonl"
        self.assertEqual(json.loads(operation_log.read_text().splitlines()[0])["operation"], "copy")
        self.assertEqual(ACTION_VALUES, ("copy", "skip", "quarantine", "keep"))

    def test_duplicate_preview_can_quarantine_and_restore_with_log(self):
        root = self.root / "archive"
        root.mkdir()
        canonical = self._image(root / "a.png")
        duplicate = root / "nested" / "copy.png"
        duplicate.parent.mkdir()
        duplicate.write_bytes(canonical.read_bytes())
        rows = scan_duplicates(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Type"], "exact")
        rows[0]["Action"] = "quarantine"
        report = self.root / "duplicates.csv"

        apply_report(rows, report)
        log = root / "Quarantine" / "action_log.jsonl"
        quarantined = root / "Quarantine" / "nested" / "copy.png"
        self.assertTrue(quarantined.is_file())
        self.assertFalse(duplicate.exists())
        self.assertTrue(log.is_file())

        results = restore_from_log(log)
        self.assertEqual(len(results), 1)
        self.assertTrue(duplicate.is_file())
        self.assertFalse(quarantined.exists())

    def test_similar_images_are_review_candidates_not_automatic_actions(self):
        root = self.root / "archive"
        root.mkdir()
        self._image(root / "first.png")
        self._image(root / "second.png", variant=1)

        rows = scan_duplicates(root)
        self.assertTrue(any(row["Type"] == "phash" for row in rows))
        candidate = next(row for row in rows if row["Type"] == "phash")
        self.assertEqual(candidate["Action"], "keep")
        self.assertLessEqual(int(candidate["pHashDistance"]), 8)

    def test_content_identifier_is_in_report(self):
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        target.mkdir()
        identifier = "12345678-1234-5678-1234-567812345678"
        image = self._image(source / "photo.png")
        image.write_bytes(image.read_bytes() + f'<xmp:ContentIdentifier="{identifier}">'.encode())
        rows = scan_backup(source, target)
        self.assertEqual(rows[0]["SourceContentIdentifier"], identifier)

    def test_live_photo_identifier_links_still_and_motion_files(self):
        root = self.root / "archive"
        root.mkdir()
        identifier = "12345678-1234-5678-1234-567812345678"
        photo = self._image(root / "still.jpg")
        photo.write_bytes(photo.read_bytes() + f'<xmp:ContentIdentifier="{identifier}">'.encode())
        video = root / "motion.mov"
        video.write_bytes(f'com.apple.quicktime.content.identifier\x00{identifier}'.encode())

        rows = scan_duplicates(root)
        pair = next(row for row in rows if row["Type"] == "live_photo")
        self.assertEqual(Path(pair["SourcePath"]), photo)
        self.assertEqual(Path(pair["MatchPath"]), video)
        self.assertEqual(pair["SourceContentIdentifier"], identifier)
        self.assertEqual(pair["MatchContentIdentifier"], identifier)
        self.assertEqual(pair["Action"], "keep")

    def test_report_operation_values_are_validated(self):
        report = self.root / "bad.csv"
        with report.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("Action",))
            writer.writeheader()
            writer.writerow({"Action": "delete"})
        with self.assertRaises(ValueError):
            read_report(report)

    def test_copy_action_requires_destination_before_any_action_is_applied(self):
        root = self.root / "archive"
        root.mkdir()
        first = self._image(root / "a.png")
        duplicate = root / "b.png"
        duplicate.write_bytes(first.read_bytes())
        rows = scan_duplicates(root)
        rows[0]["Action"] = "copy"

        with self.assertRaisesRegex(ValueError, "selected destination"):
            apply_report(rows, self.root / "report.csv")
        self.assertTrue(duplicate.is_file())

    def test_copy_collision_uses_a_non_overwriting_stable_destination(self):
        source = self.root / "source"
        target = self.root / "target"
        source_file = self._image(source / "nested" / "photo.png", variant=2)
        conflicting = target / "nested" / "photo.png"
        self._image(conflicting)
        rows = scan_backup(source, target)
        report = self.root / "collision.csv"

        apply_report(rows, report)
        copied = target / "nested" / "photo (1).png"
        self.assertTrue(copied.is_file())
        self.assertNotEqual(conflicting.read_bytes(), source_file.read_bytes())
        results = apply_report(read_report(report), report)
        self.assertEqual(results, [f"Already present {copied}"])
        self.assertFalse((target / "nested" / "photo (2).png").exists())

    def test_restore_rejects_log_entries_outside_the_archive_root(self):
        root = self.root / "archive"
        quarantine = root / "Quarantine"
        quarantine.mkdir(parents=True)
        log = quarantine / "action_log.jsonl"
        log.write_text(json.dumps({
            "operation": "quarantine",
            "root_path": str(self.root),
            "original_path": str(self.root / "outside.txt"),
            "quarantine_path": str(quarantine / "outside.txt"),
            "sha256": "0" * 64,
        }), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "different archive"):
            restore_from_log(log)


if __name__ == "__main__":
    unittest.main()
