"""Preview and apply a conservative photo-backup plan."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


VERSION = 1
MEDIA_EXTENSIONS = {
    ".aae",
    ".arw",
    ".avi",
    ".bmp",
    ".cr2",
    ".cr3",
    ".dng",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".m4v",
    ".mts",
    ".mov",
    ".mp4",
    ".nef",
    ".orf",
    ".pef",
    ".png",
    ".raf",
    ".rw2",
    ".srw",
    ".tif",
    ".tiff",
    ".webm",
    ".webp",
}
IMAGE_EXTENSIONS = {
    ".arw", ".cr2", ".cr3", ".dng", ".heic", ".heif", ".jpeg", ".jpg",
    ".nef", ".orf", ".pef", ".png", ".raf", ".rw2", ".srw", ".tif", ".tiff",
    ".webp",
}
AUTO_QUARANTINE_IMAGE_EXTENSIONS = {".heic", ".heif", ".jpeg", ".jpg", ".png"}
VIDEO_EXTENSIONS = {".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mts", ".webm"}
VALID_DECISIONS = {
    "copy": ("copy", "skip"),
    "review_source_visual": ("copy", "skip"),
    "move_exact": ("quarantine", "keep"),
    "review_target_exact": ("quarantine", "keep"),
    "review_target_visual": ("quarantine", "keep"),
    "collision": ("keep",),
    "skip": ("skip",),
}
CHUNK_SIZE = 1024 * 1024
MEDIA_PROPERTY_TAGS = [
    "File:FileType",
    "File:MIMEType",
    "File:FileSize",
    "File:FileModifyDate",
    "File:ImageWidth",
    "File:ImageHeight",
    "EXIF:Make",
    "EXIF:Model",
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "EXIF:ImageWidth",
    "EXIF:ImageHeight",
    "Image:ImageWidth",
    "Image:ImageHeight",
    "EXIF:Orientation",
    "Composite:ImageSize",
    "Composite:Megapixels",
    "QuickTime:CreationDate",
    "QuickTime:CreateDate",
    "QuickTime:Duration",
    "QuickTime:ImageWidth",
    "QuickTime:ImageHeight",
    "QuickTime:CompressorName",
    "MakerNotes:ContentIdentifier",
    "Keys:ContentIdentifier",
    "QuickTime:ContentIdentifier",
    "XMP:ContentIdentifier",
]


class BackupError(Exception):
    pass


def resolved(path: Path, *, strict: bool = False) -> Path:
    return path.expanduser().resolve(strict=strict)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def require_disjoint(source: Path, target: Path) -> None:
    if is_within(source, target) or is_within(target, source):
        raise BackupError("Source and target directories must not overlap.")


def app_resource_path(*parts: str) -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    roots = [Path(frozen_root)] if frozen_root else []
    roots.extend((Path(sys.executable).resolve().parent, Path(__file__).resolve().parent))
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return roots[0].joinpath(*parts) if roots else Path(__file__).resolve().parent.joinpath(*parts)


def iter_media(
    root: Path,
    excluded: Iterable[Path] = (),
    progress: Callable[[int], None] | None = None,
) -> list[Path]:
    excluded_roots = [resolved(path) for path in excluded]
    found: list[Path] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names[:] = [
            name
            for name in directory_names
            if not (current_path / name).is_symlink()
            and not any(is_within(resolved(current_path / name), item) for item in excluded_roots)
        ]
        for name in file_names:
            path = current_path / name
            if path.is_symlink() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            found.append(path)
            if progress is not None and len(found) % 250 == 0:
                progress(len(found))
    return sorted(found, key=lambda item: item.as_posix().casefold())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as media:
        for block in iter(lambda: media.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def phash_supported() -> bool:
    if importlib.util.find_spec("imagehash") is None or importlib.util.find_spec("PIL") is None:
        return False
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return True


def load_phash(path: Path) -> int | None:
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return None

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    try:
        with Image.open(path) as image:
            image.seek(0)
            return int(str(imagehash.phash(image.convert("RGB"))), 16)
    except Exception:
        return None


def bit_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def source_suffix_is_video(path_text: str) -> bool:
    return Path(path_text).suffix.lower() in VIDEO_EXTENSIONS


def find_exiftool_executable() -> Path | None:
    configured = os.environ.get("PHOTO_BACKUP_EXIFTOOL")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path.resolve()

    for bundled_path in (
        app_resource_path("exiftool-13.59_64", "exiftool.exe"),
        app_resource_path("exiftool.exe"),
    ):
        if bundled_path.is_file():
            return bundled_path

    system_path = shutil.which("exiftool.exe") or shutil.which("exiftool")
    return Path(system_path).resolve() if system_path else None


def find_ffprobe_executable() -> Path | None:
    bundled = app_resource_path("ffprobe.exe")
    if bundled.is_file():
        return bundled
    configured = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
    if configured:
        return Path(configured)
    ffmpeg = find_ffmpeg_executable()
    if ffmpeg:
        sibling = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return sibling
    return None


def find_ffmpeg_executable() -> Path | None:
    bundled = app_resource_path("ffmpeg.exe")
    if bundled.is_file():
        return bundled
    configured = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    return Path(configured).resolve() if configured else None


def _swap_for_rotation(width: int, height: int, rotation: Any) -> tuple[int, int]:
    try:
        if int(float(rotation)) % 180 == 90:
            return height, width
    except (TypeError, ValueError):
        pass
    return width, height


def _video_resolution(path: Path) -> tuple[int, int] | None:
    ffprobe = find_ffprobe_executable()
    if ffprobe is not None:
        command = [
            str(ffprobe), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
            "-of", "json", str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=30, check=False)
            stream = (json.loads(result.stdout.decode("utf-8", errors="replace")).get("streams") or [{}])[0]
            width, height = int(stream["width"]), int(stream["height"])
            rotation = (stream.get("tags") or {}).get("rotate")
            for side_data in stream.get("side_data_list") or []:
                rotation = side_data.get("rotation", rotation)
            return _swap_for_rotation(width, height, rotation)
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError):
            pass

    ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if ffmpeg is not None:
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, timeout=30, check=False
            )
            match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})[\s,\[]", result.stderr.decode("utf-8", errors="replace"))
            if match:
                return int(match.group(1)), int(match.group(2))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _image_resolution(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(path) as image:
            width, height = image.size
            orientation = image.getexif().get(0x0112)
        return _swap_for_rotation(width, height, 90 if orientation in (5, 6, 7, 8) else 0)
    except Exception:
        return None


def read_resolution(path: Path) -> tuple[int, int] | None:
    """Read pixel dimensions from the file header (Pillow) or the first video stream (ffprobe)."""
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return _video_resolution(path)
    return _image_resolution(path)


def file_signature(path: Path) -> list[int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return [stat.st_size, stat.st_mtime_ns]


_EXIFTOOL_ARGS = ["-G", "-n", "-charset", "filename=utf8"]


def read_media_properties(paths: Iterable[Path]) -> tuple[dict[Path, dict[str, Any]], str | None]:
    requested = [path for path in paths if path.is_file()]
    properties: dict[Path, dict[str, Any]] = {}
    for path in requested:
        record: dict[str, Any] = {}
        dimensions = read_resolution(path)
        if dimensions:
            record["Computed:Width"], record["Computed:Height"] = dimensions
        properties[path.resolve()] = record
    if not requested:
        return properties, None

    try:
        import exiftool
    except ImportError:
        return properties, "PyExifTool is not installed."
    executable = find_exiftool_executable()
    if executable is None:
        return properties, "Bundled or system exiftool.exe was not found."

    failures = 0
    try:
        with exiftool.ExifToolHelper(executable=str(executable), encoding="utf-8", common_args=_EXIFTOOL_ARGS) as tool:
            for path in requested:
                try:
                    records = tool.get_tags(str(path), MEDIA_PROPERTY_TAGS)
                except Exception:
                    failures += 1
                    continue
                if records:
                    properties[path.resolve()].update(
                        {tag: records[0][tag] for tag in MEDIA_PROPERTY_TAGS if records[0].get(tag) not in (None, "")}
                    )
    except Exception as error:
        return properties, f"ExifTool metadata lookup failed: {error}"
    return properties, (f"ExifTool could not read {failures} file(s)." if failures else None)


def read_content_identifiers(
    paths: Iterable[Path],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[Path, str], str | None]:
    try:
        import exiftool
    except ImportError:
        return {}, "PyExifTool is not installed."

    identifiers: dict[Path, str] = {}
    tags = [
        "MakerNotes:ContentIdentifier",
        "QuickTime:ContentIdentifier",
        "Keys:ContentIdentifier",
        "XMP:ContentIdentifier",
    ]
    path_list = list(paths)
    failures = 0
    last_report = 0.0
    try:
        executable = find_exiftool_executable()
        if executable is None:
            return {}, "Bundled or system exiftool.exe was not found."
        with exiftool.ExifToolHelper(executable=str(executable), encoding="utf-8", common_args=_EXIFTOOL_ARGS) as tool:
            for index, path in enumerate(path_list, 1):
                try:
                    metadata_rows = tool.get_tags(str(path), tags)
                except Exception:
                    failures += 1
                    continue
                metadata = metadata_rows[0] if metadata_rows else {}
                value = next((metadata[tag] for tag in tags if metadata.get(tag)), None)
                if value:
                    identifiers[path] = str(value).strip().casefold()
                now = time.monotonic()
                if progress is not None and (now - last_report > 0.2 or index == len(path_list)):
                    last_report = now
                    progress(index, len(path_list))
    except Exception as error:
        return {}, f"ExifTool metadata scan failed: {error}"
    return identifiers, (f"ExifTool could not read {failures} file(s)." if failures else None)


def live_pair_rows(identifiers: dict[Path, str]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, list[str]]] = {}
    for path, identifier in identifiers.items():
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            kind = "still_paths"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "motion_paths"
        else:
            continue
        groups.setdefault(identifier, {"still_paths": [], "motion_paths": []})[kind].append(str(path))

    rows = []
    for identifier, members in sorted(groups.items()):
        if members["still_paths"] and members["motion_paths"]:
            rows.append(
                {
                    "type": "live_pair",
                    "operation": "live_pair_info",
                    "decision": "keep",
                    "content_identifier": identifier,
                    **members,
                    "reason": "ExifTool ContentIdentifier matches a still image and motion file; retain the pair.",
                }
            )
    return rows


class HashNode:
    def __init__(self, value: int, path: Path) -> None:
        self.value = value
        self.paths = [path]
        self.children: dict[int, HashNode] = {}


class HashTree:
    def __init__(self) -> None:
        self.root: HashNode | None = None

    def add(self, value: int, path: Path) -> None:
        if self.root is None:
            self.root = HashNode(value, path)
            return
        node = self.root
        while True:
            distance = bit_distance(value, node.value)
            if distance == 0:
                node.paths.append(path)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = HashNode(value, path)
                return
            node = child

    def find(self, value: int, threshold: int) -> list[tuple[int, Path]]:
        if self.root is None:
            return []
        matches: list[tuple[int, Path]] = []
        pending = [self.root]
        while pending:
            node = pending.pop()
            distance = bit_distance(value, node.value)
            if distance <= threshold:
                matches.extend((distance, path) for path in node.paths)
            low = distance - threshold
            high = distance + threshold
            pending.extend(
                child for edge, child in node.children.items() if low <= edge <= high
            )
        return sorted(matches, key=lambda item: (item[0], item[1].as_posix().casefold()))


def relative_text(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def new_row(
    operation: str,
    decision: str,
    source: Path,
    *,
    digest: str,
    matched_path: Path | None = None,
    matched_sha256: str | None = None,
    distance: int | None = None,
    reason: str,
) -> dict[str, Any]:
    return {
        "type": "item",
        "operation": operation,
        "decision": decision,
        "source": str(source),
        "sha256": digest,
        "matched_path": str(matched_path) if matched_path else "",
        "matched_sha256": matched_sha256 or "",
        "source_sig": file_signature(source),
        "matched_sig": file_signature(matched_path) if matched_path else None,
        "phash_distance": distance,
        "reason": reason,
    }


def validate_roots(source: Path, target: Path) -> tuple[Path, Path]:
    source_root = resolved(source, strict=True)
    target_root = resolved(target, strict=True)
    if not source_root.is_dir() or not target_root.is_dir():
        raise BackupError("Source and target must both be existing directories.")
    require_disjoint(source_root, target_root)
    return source_root, target_root


def write_jsonl(path: Path, header: dict[str, Any], rows: Iterable[dict[str, Any]]) -> None:
    path = resolved(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as report:
            report.write(json.dumps(header, ensure_ascii=False) + "\n")
            for row in rows:
                report.write(json.dumps(row, ensure_ascii=False) + "\n")
    except FileExistsError as error:
        raise BackupError(f"Report already exists; choose a new path: {path}") from error


def report_progress(
    args: argparse.Namespace,
    message: str,
    current: int | None = None,
    total: int | None = None,
) -> None:
    callback = getattr(args, "progress_callback", None)
    if callback is not None:
        callback(message, current, total)
    elif current is None:
        print(message, file=sys.stderr)


class ProgressThrottle:
    def __init__(self, args: argparse.Namespace, label: str, total: int) -> None:
        self.args, self.label, self.total, self.last = args, label, total, 0.0

    def update(self, current: int) -> None:
        now = time.monotonic()
        if current >= self.total or now - self.last > 0.15:
            self.last = now
            report_progress(self.args, f"{self.label} {current:,}/{self.total:,}", current, self.total)


def preview(args: argparse.Namespace) -> int:
    mode = getattr(args, "mode", "backup")
    if mode == "folder":
        source_root = resolved(Path(args.source), strict=True)
        if not source_root.is_dir():
            raise BackupError("Folder scan path must be an existing directory.")
        target_root = source_root
    else:
        source_root, target_root = validate_roots(Path(args.source), Path(args.target))
    quarantine_root = resolved(Path(args.quarantine)) if args.quarantine else target_root.parent / "Quarantine"
    if mode == "folder":
        if quarantine_root == source_root or is_within(source_root, quarantine_root):
            raise BackupError("Quarantine cannot be the folder-scan root or contain it.")
    elif (
        is_within(quarantine_root, source_root)
        or is_within(source_root, quarantine_root)
        or quarantine_root == target_root
        or is_within(target_root, quarantine_root)
    ):
        raise BackupError("Quarantine cannot overlap the source or contain the target directory.")

    report_path = resolved(Path(args.report))
    if is_within(report_path, source_root) or is_within(report_path, target_root):
        raise BackupError("Keep the report outside the source and target directories.")

    report_progress(args, "Phase 1/5: scanning folders...")

    def scan_progress(found: int) -> None:
        report_progress(args, f"Phase 1/5: scanning folders... {found:,} media files found")

    source_files = iter_media(source_root, excluded=(quarantine_root,), progress=scan_progress)
    target_files = source_files if mode == "folder" else iter_media(
        target_root, excluded=(quarantine_root,), progress=scan_progress
    )
    content_id_check = getattr(args, "content_id_check", True)
    if content_id_check:
        unique_files = list(dict.fromkeys(source_files + target_files))
        content_ids, content_id_warning = read_content_identifiers(
            unique_files,
            progress=lambda done, total: report_progress(
                args, f"Phase 2/5: Live Photo identifiers {done:,}/{total:,}", done, total
            ),
        )
    else:
        content_ids, content_id_warning = {}, None
    target_hashes: dict[str, list[Path]] = {}
    source_hashes: dict[str, list[Path]] = {}
    target_digest_by_path: dict[Path, str] = {}
    source_digest_by_path: dict[Path, str] = {}

    progress = ProgressThrottle(args, "Phase 3/5: hashing target files", len(target_files))
    for index, path in enumerate(target_files, 1):
        digest = sha256_file(path)
        target_digest_by_path[path] = digest
        target_hashes.setdefault(digest, []).append(path)
        progress.update(index)
    if mode != "folder":
        progress = ProgressThrottle(args, "Phase 4/5: hashing source files", len(source_files))
        for index, path in enumerate(source_files, 1):
            digest = sha256_file(path)
            source_digest_by_path[path] = digest
            source_hashes.setdefault(digest, []).append(path)
            progress.update(index)

    rows: list[dict[str, Any]] = []
    for digest, paths in target_hashes.items():
        ordered = sorted(paths, key=lambda item: relative_text(item, target_root).casefold())
        keeper = ordered[0]
        for duplicate in ordered[1:]:
            auto_quarantine = duplicate.suffix.lower() in AUTO_QUARANTINE_IMAGE_EXTENSIONS
            operation = "move_exact" if auto_quarantine else "review_target_exact"
            decision = "quarantine" if auto_quarantine else "pending"
            rows.append(
                new_row(
                    operation,
                    decision,
                    duplicate,
                    digest=digest,
                    matched_path=keeper,
                    matched_sha256=digest,
                    reason=(
                        "Byte-identical common still image already exists in target; move to recoverable quarantine."
                        if auto_quarantine
                        else "Byte-identical video, RAW, TIFF, or sidecar; review related assets before quarantine."
                    ),
                )
            )

    phash_tree = HashTree()
    source_phash_tree = HashTree()
    phash_requested = getattr(args, "visual_check", True)
    phash_available = phash_requested and phash_supported()
    decode_failures = 0
    visual_target_pairs: set[tuple[str, str]] = set()
    visual_total = (
        sum(path.suffix.lower() in IMAGE_EXTENSIONS for path in target_files)
        + (sum(path.suffix.lower() in IMAGE_EXTENSIONS for path in source_files) if mode != "folder" else 0)
        if phash_available
        else 0
    )
    visual_done = 0
    visual_progress = ProgressThrottle(args, "Phase 5/5: comparing images", max(visual_total, 1))
    for path in target_files:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not phash_available:
            break
        visual_done += 1
        visual_progress.update(visual_done)
        digest = target_digest_by_path[path]
        if len(target_hashes[digest]) > 1 and path != sorted(
            target_hashes[digest], key=lambda item: relative_text(item, target_root).casefold()
        )[0]:
            continue
        image_hash = load_phash(path)
        if image_hash is None:
            decode_failures += 1
            continue
        for distance, match in phash_tree.find(image_hash, args.phash_distance):
            if target_digest_by_path[match] == digest:
                continue
            pair_items = sorted((str(path), str(match)), key=str.casefold)
            pair = (pair_items[0], pair_items[1])
            if pair in visual_target_pairs:
                continue
            visual_target_pairs.add(pair)
            rows.append(
                new_row(
                    "review_target_visual",
                    "pending",
                    path,
                    digest=digest,
                    matched_path=match,
                    matched_sha256=target_digest_by_path[match],
                    distance=distance,
                    reason="Visual similarity is only a review candidate; no automatic removal is safe.",
                )
            )
        phash_tree.add(image_hash, path)

    source_hash_owner: dict[str, Path] = {}
    source_groups = source_hashes.items() if mode != "folder" else ()
    for digest, paths in source_groups:
        for source_path in sorted(paths, key=lambda item: relative_text(item, source_root).casefold()):
            target_matches = target_hashes.get(digest, [])
            if target_matches:
                rows.append(
                    new_row(
                        "skip",
                        "skip",
                        source_path,
                        digest=digest,
                        matched_path=target_matches[0],
                        matched_sha256=digest,
                        reason="Exact SHA-256 match already exists on target.",
                    )
                )
                continue

            previous_source = source_hash_owner.get(digest)
            if previous_source is not None:
                rows.append(
                    new_row(
                        "skip",
                        "skip",
                        source_path,
                        digest=digest,
                        matched_path=previous_source,
                        reason="Byte-identical source file is already represented by another source path.",
                    )
                )
                continue

            image_hash = None
            matches: list[tuple[int, Path, str]] = []
            if source_path.suffix.lower() in IMAGE_EXTENSIONS and phash_available:
                visual_done += 1
                visual_progress.update(visual_done)
                image_hash = load_phash(source_path)
                if image_hash is None:
                    decode_failures += 1
                else:
                    matches.extend(
                        (distance, match, target_digest_by_path[match])
                        for distance, match in phash_tree.find(image_hash, args.phash_distance)
                    )
                    matches.extend(
                        (distance, match, source_digest_by_path[match])
                        for distance, match in source_phash_tree.find(image_hash, args.phash_distance)
                    )
                    matches.sort(key=lambda item: (item[0], not is_within(item[1], target_root), str(item[1]).casefold()))

            destination = target_root / source_path.relative_to(source_root)
            if destination.exists() or is_within(destination, quarantine_root):
                rows.append(
                    new_row(
                        "collision",
                        "keep",
                        source_path,
                        digest=digest,
                        matched_path=destination,
                        reason=(
                            "The relative target path is reserved for quarantine; no copy is planned."
                            if is_within(destination, quarantine_root)
                            else "A different file already occupies the relative target path; no overwrite is planned."
                        ),
                    )
                )
                if image_hash is not None:
                    source_phash_tree.add(image_hash, source_path)
                continue

            source_hash_owner[digest] = source_path
            if matches:
                distance, match, match_digest = matches[0]
                rows.append(
                    new_row(
                        "review_source_visual",
                        "pending",
                        source_path,
                        digest=digest,
                        matched_path=match,
                        matched_sha256=match_digest,
                        distance=distance,
                        reason="Possible visual duplicate in D or F; choose copy or skip after review.",
                    )
                )
            else:
                row = new_row(
                    "copy",
                    "copy",
                    source_path,
                    digest=digest,
                    reason="No exact target match or visual candidate was found.",
                )
                row["destination"] = str(destination)
                rows.append(row)
            if image_hash is not None:
                source_phash_tree.add(image_hash, source_path)

    header = {
        "type": "header",
        "version": VERSION,
        "mode": mode,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "quarantine_root": str(quarantine_root),
        "created_by": Path(__file__).name,
        "phash_distance": args.phash_distance,
        "phash_available": phash_available,
        "visual_check_requested": phash_requested,
        "content_identifier_check_requested": content_id_check,
        "content_identifier_warning": content_id_warning,
        "decode_failures": decode_failures,
        "source_file_count": len(source_files),
        "target_file_count": len(target_files),
        "safety_note": "No media was changed. ContentIdentifier pairs are reported as evidence only; no pair is automatically altered.",
    }
    rows.extend(live_pair_rows(content_ids))
    write_jsonl(report_path, header, rows)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["operation"]] = counts.get(row["operation"], 0) + 1
    print(f"Preview report: {report_path}")
    print(f"Planned entries: {len(rows)}")
    for operation, count in sorted(counts.items()):
        print(f"  {operation}: {count}")
    live_pairs = sum(row["operation"] == "live_pair_info" for row in rows)
    if live_pairs:
        print(f"  Live Photo pairs verified by ContentIdentifier: {live_pairs}")
    if decode_failures:
        print(f"Images that could not be decoded: {decode_failures}")
    if not phash_requested:
        print("Visual comparison was turned off (pHash checkbox / --no-visual); no similarity candidates were generated.")
    elif not phash_available:
        print("Perceptual comparison unavailable; install requirements-photo-backup.txt to enable it.")
    if content_id_warning:
        print(f"ContentIdentifier metadata unavailable: {content_id_warning}")
    return 0


def path_from_manifest(value: str, root: Path) -> Path:
    path = resolved(Path(value), strict=True)
    if not is_within(path, root) or path == root or path.is_symlink():
        raise BackupError(f"Manifest path is outside its allowed root: {path}")
    return path


def destination_from_manifest(value: str, root: Path) -> Path:
    path = resolved(Path(value))
    if not is_within(path, root) or path == root:
        raise BackupError(f"Manifest destination is outside its allowed root: {path}")
    return path


def copy_new_file(source: Path, destination: Path) -> None:
    if destination.exists():
        raise BackupError(f"Refusing to overwrite existing destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, CHUNK_SIZE)
            output_file.flush()
            os.fsync(output_file.fileno())
        shutil.copystat(source, destination)
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def quarantine_file(source: Path, destination: Path) -> None:
    if destination.exists():
        raise BackupError(f"Refusing to overwrite quarantine file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A same-volume rename needs no hard-link support (exFAT/FAT32 backup disks lack it) and fails if the target exists on Windows.
    try:
        os.rename(source, destination)
    except OSError as error:
        raise BackupError(f"Could not move {source} to quarantine: {error}") from error


def apply_report(args: argparse.Namespace) -> int:
    report_path = resolved(Path(args.report), strict=True)
    with report_path.open("r", encoding="utf-8") as report:
        try:
            header = json.loads(next(report))
        except (StopIteration, json.JSONDecodeError) as error:
            raise BackupError("Report is empty or malformed.") from error
        if header.get("type") != "header" or header.get("version") != VERSION:
            raise BackupError("Unsupported report format or version.")

        source_root = resolved(Path(header["source_root"]), strict=True)
        target_root = resolved(Path(header["target_root"]), strict=True)
        requested_source = resolved(Path(args.source), strict=True)
        requested_target = resolved(Path(args.target), strict=True)
        if requested_source != source_root or requested_target != target_root:
            raise BackupError("The supplied --source/--target paths do not match the reviewed report.")
        if not source_root.is_dir() or not target_root.is_dir():
            raise BackupError("The report source and target must still be existing directories.")
        mode = header.get("mode", "backup")
        if mode == "folder":
            if source_root != target_root:
                raise BackupError("Folder-scan report must use the same source and target root.")
        elif mode == "backup":
            require_disjoint(source_root, target_root)
        else:
            raise BackupError(f"Unsupported report mode: {mode}")
        quarantine_root = resolved(Path(args.quarantine or header["quarantine_root"]))
        if mode == "folder":
            if quarantine_root == source_root or is_within(source_root, quarantine_root):
                raise BackupError("Quarantine cannot be the folder-scan root or contain it.")
        elif (
            is_within(quarantine_root, source_root)
            or is_within(source_root, quarantine_root)
            or quarantine_root == target_root
            or is_within(target_root, quarantine_root)
        ):
            raise BackupError("Quarantine cannot overlap the source or contain the target directory.")

        rows = [json.loads(line) for line in report if line.strip()]

    pending = [row for row in rows if row.get("decision") == "pending"]
    if pending:
        raise BackupError(
            f"{len(pending)} visual review entries are unresolved. Set each decision to copy, skip, quarantine, or keep."
        )

    selected: list[tuple[dict[str, Any], Path, Path | None]] = []
    visual_quarantines = 0
    quarantine_count = 0

    # Fail on path, permission, and filesystem problems before any file is read.
    log_path = resolved(Path(args.log))
    if is_within(log_path, source_root) or is_within(log_path, target_root) or log_path.exists():
        raise BackupError("Audit log must be a new file outside the source and target folders.")
    for row in rows:
        operation = row.get("operation")
        if operation != "live_pair_info" and row.get("decision") not in VALID_DECISIONS.get(operation, ()):
            raise BackupError(
                f"Unsupported decision '{row.get('decision')}' for {operation}: {row.get('source', '')}"
            )
    wants_quarantine = any(
        row.get("decision") == "quarantine" and row.get("operation") != "live_pair_info" for row in rows
    )
    wants_copy = any(row.get("decision") == "copy" for row in rows)
    if (wants_quarantine or wants_copy) and not args.confirm:
        raise BackupError("Applying a report requires --confirm.")
    if wants_quarantine:
        if not args.allow_quarantine:
            raise BackupError("Quarantine requires --allow-quarantine.")
        if any(row.get("decision") == "quarantine" and row.get("operation") == "review_target_visual" for row in rows) \
                and not args.allow_visual_quarantine:
            raise BackupError("Visual quarantine requires --allow-visual-quarantine.")
        if any(row.get("decision") == "quarantine" and source_suffix_is_video(row.get("source", "")) for row in rows) \
                and not args.allow_video_quarantine:
            raise BackupError("Video quarantine requires --allow-video-quarantine.")
        existing_parent = quarantine_root
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        if existing_parent.stat().st_dev != target_root.stat().st_dev:
            raise BackupError("Quarantine must be on the same filesystem as the target for safe moves.")

    quarantine_keepers: dict[Path, Path] = {}

    verify_progress = ProgressThrottle(args, "Preparing plan", max(len(rows), 1))
    for row_number, row in enumerate(rows, 1):
        verify_progress.update(row_number)
        operation = row.get("operation")
        decision = row.get("decision")
        if operation == "live_pair_info":
            continue
        if operation == "review_source_visual":
            source = path_from_manifest(row["source"], source_root)
            match_path = resolved(Path(row["matched_path"]), strict=True)
            if is_within(match_path, target_root):
                match_root = target_root
            elif is_within(match_path, source_root):
                match_root = source_root
            else:
                raise BackupError(f"Visual match is outside D and F: {match_path}")
            match_path = path_from_manifest(str(match_path), match_root)
            if decision == "skip":
                continue
            if decision != "copy":
                raise BackupError(f"Unsupported visual-source decision: {decision}")
            destination = target_root / source.relative_to(source_root)
            if is_within(destination, quarantine_root):
                raise BackupError(f"Copy destination is inside quarantine: {destination}")
            selected.append((row, source, destination))
            continue
        if operation == "skip":
            source = path_from_manifest(row["source"], source_root)
            match = resolved(Path(row["matched_path"]), strict=True)
            if is_within(match, target_root):
                allowed_root = target_root
            elif is_within(match, source_root):
                allowed_root = source_root
            else:
                raise BackupError(f"Skipped-file match is outside D and F: {match}")
            match = path_from_manifest(str(match), allowed_root)
            continue
        if decision in {"skip", "keep"}:
            continue
        if operation == "copy" and decision == "copy":
            source = path_from_manifest(row["source"], source_root)
            destination = destination_from_manifest(row["destination"], target_root)
            expected_destination = target_root / source.relative_to(source_root)
            if destination != expected_destination:
                raise BackupError(f"Unexpected copy destination in report: {destination}")
            if is_within(destination, quarantine_root):
                raise BackupError(f"Copy destination is inside quarantine: {destination}")
            selected.append((row, source, destination))
        elif operation in {"move_exact", "review_target_exact", "review_target_visual"} and decision == "quarantine":
            is_visual = operation == "review_target_visual"
            source = path_from_manifest(row["source"], target_root)
            keeper = path_from_manifest(row["matched_path"], target_root)
            if keeper == source:
                raise BackupError(f"A file cannot be its own kept copy: {source}")
            quarantine_keepers[source] = keeper
            if is_visual:
                visual_quarantines += 1
            quarantine_count += 1
            relative = source.relative_to(target_root)
            destination = destination_from_manifest(str(quarantine_root / relative), quarantine_root)
            selected.append((row, source, destination))
        elif operation == "review_source_visual" and decision == "skip":
            continue
        elif operation == "review_target_visual" and decision == "keep":
            continue
        elif operation in {"move_exact", "review_target_exact"} and decision == "keep":
            continue
        else:
            raise BackupError(f"Unsupported operation/decision combination: {operation}/{decision}")

    for start in quarantine_keepers:
        seen = {start}
        current = quarantine_keepers[start]
        while current in quarantine_keepers:
            if current in seen:
                raise BackupError(f"The plan would quarantine every copy in a duplicate group, including {start}.")
            seen.add(current)
            current = quarantine_keepers[current]

    planned_destinations: set[Path] = set()
    for _, _, destination in selected:
        if destination is None:
            continue
        if destination in planned_destinations:
            raise BackupError(f"Multiple report entries target the same destination: {destination}")
        planned_destinations.add(destination)
        if destination.exists():
            raise BackupError(f"Destination appeared or already exists; nothing was changed: {destination}")

    copied = 0
    quarantined = 0
    apply_progress = ProgressThrottle(args, "Applying changes", max(len(selected), 1))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("x", encoding="utf-8", newline="\n") as audit_log:
            audit_log.write(json.dumps({
                "type": "header",
                "report": str(report_path),
                "report_sha256": sha256_file(report_path),
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            audit_log.flush()
            os.fsync(audit_log.fileno())
            for step, (row, source, destination) in enumerate(selected, 1):
                if destination is None:
                    raise BackupError("Selected operation is missing its destination.")
                if row["operation"] in {"copy", "review_source_visual"}:
                    copy_new_file(source, destination)
                    copied += 1
                    action = "copied"
                else:
                    quarantine_file(source, destination)
                    quarantined += 1
                    action = "moved_to_quarantine"
                audit_log.write(json.dumps({
                    "type": "action",
                    "action": action,
                    "operation": row["operation"],
                    "decision": row["decision"],
                    "source": str(source),
                    "destination": str(destination),
                    "sha256": row["sha256"],
                    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")
                audit_log.flush()
                os.fsync(audit_log.fileno())
                apply_progress.update(step)
    except FileExistsError as error:
        raise BackupError(f"Audit log already exists: {log_path}") from error

    summary = {"copied": copied, "quarantined": quarantined, "log": str(log_path), "quarantine_root": str(quarantine_root)}
    result_callback = getattr(args, "result_callback", None)
    if result_callback is not None:
        result_callback(summary)
    print(f"Copied to target: {copied}")
    print(f"Moved to quarantine: {quarantined}")
    if visual_quarantines:
        print(f"Visual candidates quarantined after review: {visual_quarantines}")
    print("No file was permanently deleted.")
    return 0


KEEP_CRITERIA = {
    "type": "Preferred file type (order below)",
    "resolution": "Higher resolution",
    "size_large": "Larger file size",
    "size_small": "Smaller file size",
    "created_new": "Newer creation time",
    "created_old": "Older creation time",
}
DEFAULT_TYPE_ORDER = (".dng", ".heic", ".heif", ".tiff", ".tif", ".png", ".jpg", ".jpeg", ".webp")
EXACT_OPERATIONS = {"move_exact", "review_target_exact"}


@dataclass
class KeepPreference:
    # Earlier criteria dominate; later ones only break ties. Remaining ties fall back to the path name.
    criteria: tuple[str, ...] = ("type", "resolution", "size_large", "created_old")
    type_order: tuple[str, ...] = DEFAULT_TYPE_ORDER


@dataclass
class KeepPlan:
    changes: dict[int, dict[str, Any]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


def _created_ns(stat: os.stat_result) -> int:
    birth = getattr(stat, "st_birthtime", None)
    return int(birth * 1_000_000_000) if birth else stat.st_ctime_ns


def plan_keep_decisions(
    rows: list[dict[str, Any]],
    indices: Iterable[int],
    preference: KeepPreference,
    only_pending: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> KeepPlan:
    """Choose which file of each duplicate pair/group to keep and return the rewritten rows."""
    unknown = [name for name in preference.criteria if name not in KEEP_CRITERIA]
    if unknown:
        raise BackupError(f"Unknown keep criteria: {', '.join(unknown)}")

    stats = {"quarantine": 0, "keep": 0, "copy": 0, "skip": 0, "unreadable": 0, "left_manual": 0}
    changes: dict[int, dict[str, Any]] = {}
    ranks: dict[str, tuple[int, ...] | None] = {}
    need_resolution = "resolution" in preference.criteria
    type_order = tuple(item.lower() for item in preference.type_order)

    def rank(path_text: str) -> tuple[int, ...] | None:
        if path_text in ranks:
            return ranks[path_text]
        path = Path(path_text)
        try:
            stat = path.stat()
        except OSError:
            ranks[path_text] = None
            return None
        pixels = 0
        if need_resolution:
            dims = read_resolution(path)
            pixels = dims[0] * dims[1] if dims else 0
        values: list[int] = []
        for name in preference.criteria:
            if name == "type":
                suffix = path.suffix.lower()
                values.append(-(type_order.index(suffix) if suffix in type_order else len(type_order)))
            elif name == "resolution":
                values.append(pixels)
            elif name == "size_large":
                values.append(stat.st_size)
            elif name == "size_small":
                values.append(-stat.st_size)
            elif name == "created_new":
                values.append(_created_ns(stat))
            elif name == "created_old":
                values.append(-_created_ns(stat))
        ranks[path_text] = tuple(values)
        return ranks[path_text]

    def best_of(path_texts: Iterable[str]) -> str | None:
        candidates = []
        for text in path_texts:
            key = rank(text)
            if key is None:
                return None
            candidates.append((tuple(-value for value in key), text.casefold(), text))
        return min(candidates)[2] if candidates else None

    eligible_ops = EXACT_OPERATIONS | {"review_target_visual", "review_source_visual"}
    selected = [
        index for index in indices
        if rows[index].get("operation") in eligible_ops and (not only_pending or rows[index].get("decision") == "pending")
    ]
    selected_set = set(selected)
    claimed = {
        row["source"] for index, row in enumerate(rows)
        if index not in selected_set and row.get("decision") == "quarantine" and row.get("operation") != "live_pair_info"
    }
    done = 0

    def tick(count: int = 1) -> None:
        nonlocal done
        done += count
        if progress is not None:
            progress(min(done, len(selected)), len(selected))

    groups: dict[str, list[int]] = {}
    visual: list[int] = []
    for index in selected:
        row = rows[index]
        if row["operation"] in EXACT_OPERATIONS:
            groups.setdefault(row["sha256"], []).append(index)
        elif row["operation"] == "review_target_visual":
            visual.append(index)
        else:
            source_key, match_key = rank(row["source"]), rank(row["matched_path"])
            if source_key is None or match_key is None:
                stats["unreadable"] += 1
            else:
                best = best_of((row["source"], row["matched_path"]))
                decision = "copy" if best == row["source"] else "skip"
                changes[index] = {**row, "decision": decision}
                stats[decision] += 1
            tick()

    for sha, indexes in groups.items():
        path_texts = {rows[i]["source"] for i in indexes} | {rows[i]["matched_path"] for i in indexes}
        best = best_of(path_texts)
        if best is None:
            stats["unreadable"] += len(indexes)
            tick(len(indexes))
            continue
        signatures = {rows[i]["source"]: rows[i].get("source_sig") for i in indexes}
        signatures.update({rows[i]["matched_path"]: rows[i].get("matched_sig") for i in indexes})
        rewritten: dict[int, dict[str, Any]] = {}
        for i in indexes:
            row = dict(rows[i])
            if row["source"] == best:
                row["source"], row["source_sig"] = row["matched_path"], row.get("matched_sig")
            row.update(matched_path=best, matched_sig=signatures.get(best), matched_sha256=sha, decision="quarantine")
            rewritten[i] = row
        sources = [row["source"] for row in rewritten.values()]
        if len(set(sources)) != len(sources) or claimed.intersection(sources) or best in claimed:
            stats["left_manual"] += len(indexes)
        else:
            changes.update(rewritten)
            claimed.update(sources)
            stats["quarantine"] += len(rewritten)
        tick(len(indexes))

    pairs: dict[int, tuple[str, str]] = {}
    for index in visual:
        row = rows[index]
        best = best_of((row["source"], row["matched_path"]))
        if best is None:
            stats["unreadable"] += 1
            tick()
            continue
        pairs[index] = (best, row["matched_path"] if best == row["source"] else row["source"])
    losers = {loser for _, loser in pairs.values()} | claimed
    for index, (winner, loser) in pairs.items():
        row = dict(rows[index])
        if winner in losers:
            stats["left_manual"] += 1  # The kept file is itself removed elsewhere; review this pair by hand.
        elif loser in claimed:
            changes[index] = {**row, "decision": "keep"}
            stats["keep"] += 1
        else:
            if loser == row["matched_path"]:
                row.update(
                    source=row["matched_path"], source_sig=row.get("matched_sig"),
                    sha256=row["matched_sha256"], matched_path=row["source"],
                    matched_sig=rows[index].get("source_sig"), matched_sha256=rows[index]["sha256"],
                )
            row["decision"] = "quarantine"
            changes[index] = row
            claimed.add(loser)
            stats["quarantine"] += 1
        tick()
    return KeepPlan(changes=changes, stats=stats)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservative D-to-F photo backup and duplicate review.")
    commands = parser.add_subparsers(dest="command", required=True)

    preview_parser = commands.add_parser("preview", help="Hash media and create a reviewable JSONL plan.")
    preview_parser.add_argument("--source", required=True, help="Working-copy directory (D).")
    preview_parser.add_argument("--target", help="Backup directory; omit in folder mode.")
    preview_parser.add_argument("--mode", choices=("backup", "folder"), default="backup")
    preview_parser.add_argument("--report", required=True, help="New JSONL report path outside D and F.")
    preview_parser.add_argument("--quarantine", help="Quarantine directory; defaults to <parent of target>\\Quarantine.")
    preview_parser.add_argument("--phash-distance", type=int, default=4, help="Review threshold from 0 to 64 (default: 4).")
    preview_parser.add_argument("--no-visual", action="store_true", help="Disable pHash review candidates.")
    preview_parser.add_argument("--no-content-identifiers", action="store_true", help="Disable ExifTool Live Photo metadata inspection.")
    preview_parser.set_defaults(run=preview)

    apply_parser = commands.add_parser("apply", help="Apply reviewed copy/quarantine decisions from a report.")
    apply_parser.add_argument("--source", required=True, help="Confirm the source directory (D) recorded in the report.")
    apply_parser.add_argument("--target", required=True, help="Confirm the target directory (F) recorded in the report.")
    apply_parser.add_argument("--report", required=True)
    apply_parser.add_argument("--log", required=True, help="New audit JSONL path outside D, F, and quarantine.")
    apply_parser.add_argument("--quarantine", help="Override quarantine directory recorded in the report.")
    apply_parser.add_argument("--confirm", action="store_true", help="Confirm filesystem changes.")
    apply_parser.add_argument("--allow-quarantine", action="store_true", help="Allow reversible moves into quarantine.")
    apply_parser.add_argument("--allow-visual-quarantine", action="store_true", help="Allow reviewed visual-similarity moves.")
    apply_parser.add_argument("--allow-video-quarantine", action="store_true", help="Allow reviewed video moves after checking Live Photo pairing.")
    apply_parser.set_defaults(run=apply_report)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "preview" and args.mode == "backup" and not args.target:
        parser.error("--target is required in backup mode")
    if args.command == "preview" and args.mode == "folder" and not args.target:
        args.target = args.source
    if args.command == "preview":
        args.visual_check = not args.no_visual
        args.content_id_check = not args.no_content_identifiers
    if args.command == "preview" and not 0 <= args.phash_distance <= 64:
        parser.error("--phash-distance must be between 0 and 64")
    try:
        return args.run(args)
    except (BackupError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())