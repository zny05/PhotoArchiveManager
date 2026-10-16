"""File discovery, duplicate comparison, and safe archive operations."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw",
    ".orf", ".rw2", ".raf",
}
VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp", ".mts", ".m2ts",
}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
ACTION_VALUES = ("copy", "skip", "quarantine", "keep")
REPORT_FIELDS = (
    "SourcePath",
    "SourceRoot",
    "MatchPath",
    "ActionPath",
    "RootPath",
    "TargetRoot",
    "Type",
    "SHA256",
    "pHashDistance",
    "SourceContentIdentifier",
    "MatchContentIdentifier",
    "Action",
)
PHASH_DISTANCE_THRESHOLD = 8


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _iter_media(root: Path) -> Iterable[Path]:
    def ignore_walk_error(_: OSError) -> None:
        return None

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=ignore_walk_error):
        dirs[:] = [
            name for name in dirs
            if not (Path(directory) / name).is_symlink()
            and not (Path(directory) == root and name.casefold() == "quarantine")
        ]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in MEDIA_EXTENSIONS and not path.is_symlink():
                yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_identifier(path: Path) -> str:
    """Read common XMP/QuickTime ContentIdentifier metadata without loading a video."""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            samples = [stream.read(1024 * 1024)]
            if size > 1024 * 1024:
                stream.seek(max(0, size - 1024 * 1024))
                samples.append(stream.read(1024 * 1024))
    except OSError:
        return ""

    marker = re.compile(rb"content[\._\x00]?identifier", re.IGNORECASE)
    uuid_pattern = re.compile(
        rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    for sample in samples:
        for match in marker.finditer(sample):
            context = sample[match.end():match.end() + 512]
            found_uuid = uuid_pattern.search(context)
            if found_uuid:
                try:
                    return str(uuid.UUID(found_uuid.group().decode("ascii")))
                except ValueError:
                    pass
            text = context.replace(b"\x00", b"").decode("utf-8", errors="ignore")
            value = re.search(r"""[\s:=><"']+([A-Za-z0-9_-]{8,})""", text)
            if value:
                return value.group(1)
    return ""


def _phash(path: Path) -> int | None:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    try:
        with Image.open(path) as image:
            pixels = list(ImageOps.exif_transpose(image).convert("L").resize((32, 32)).getdata())
    except (OSError, ValueError):
        return None

    # Compute the low-frequency 8x8 DCT coefficients from a 32x32 grayscale image.
    coefficients: list[float] = []
    for v in range(8):
        cv = 1 / math.sqrt(2) if v == 0 else 1
        for u in range(8):
            cu = 1 / math.sqrt(2) if u == 0 else 1
            total = 0.0
            for y in range(32):
                for x in range(32):
                    total += (
                        pixels[y * 32 + x]
                        * math.cos((2 * x + 1) * u * math.pi / 64)
                        * math.cos((2 * y + 1) * v * math.pi / 64)
                    )
            coefficients.append(total * cu * cv / 4)

    median_values = sorted(coefficients[1:])
    median = median_values[len(median_values) // 2]
    value = 0
    for coefficient in coefficients:
        value = (value << 1) | int(coefficient > median)
    return value


def _distance(first: int, second: int) -> int:
    return (first ^ second).bit_count()


def _hash_records(root: Path) -> list[dict[str, object]]:
    records = []
    for path in _iter_media(root):
        try:
            records.append({
                "path": path,
                "sha256": _sha256(path),
                "phash": _phash(path),
                "content_identifier": _content_identifier(path),
            })
        except OSError:
            continue
    return records


def _row(
    *,
    source: Path,
    source_root: Path,
    match: Path | None,
    action_path: Path,
    root: Path,
    target_root: Path | None,
    kind: str,
    sha256: str,
    distance: int | None,
    source_identifier: str,
    match_identifier: str,
    action: str,
) -> dict[str, str]:
    return {
        "SourcePath": str(source),
        "SourceRoot": str(source_root),
        "MatchPath": str(match) if match else "",
        "ActionPath": str(action_path),
        "RootPath": str(root),
        "TargetRoot": str(target_root) if target_root else "",
        "Type": kind,
        "SHA256": sha256,
        "pHashDistance": str(distance) if distance is not None else "",
        "SourceContentIdentifier": source_identifier,
        "MatchContentIdentifier": match_identifier,
        "Action": action,
    }


def scan_backup(source_root: str | Path, target_root: str | Path) -> list[dict[str, str]]:
    """Preview media in a source folder against an existing destination."""
    source = _absolute(source_root)
    target = _absolute(target_root)
    if not source.is_dir() or not target.is_dir():
        raise ValueError("Source and target must both be existing directories.")
    if source == target or _inside(source, target) or _inside(target, source):
        raise ValueError("Source and target directories must not overlap.")

    target_records = _hash_records(target)
    by_hash: dict[str, list[dict[str, object]]] = {}
    for record in target_records:
        by_hash.setdefault(str(record["sha256"]), []).append(record)
    target_images = [record for record in target_records if record["phash"] is not None]

    rows = []
    for record in _hash_records(source):
        source_path = record["path"]
        digest = str(record["sha256"])
        exact = by_hash.get(digest, [])
        if exact:
            match = exact[0]
            rows.append(_row(
                source=source_path,
                source_root=source,
                match=match["path"],
                action_path=match["path"],
                root=target,
                target_root=target,
                kind="exact",
                sha256=digest,
                distance=None,
                source_identifier=str(record["content_identifier"]),
                match_identifier=str(match["content_identifier"]),
                action="skip",
            ))
            continue

        source_hash = record["phash"]
        candidates = []
        if source_hash is not None:
            for match in target_images:
                if match["phash"] is not None:
                    distance = _distance(int(source_hash), int(match["phash"]))
                    if distance <= PHASH_DISTANCE_THRESHOLD:
                        candidates.append((distance, match))
        if candidates:
            distance, match = min(candidates, key=lambda pair: pair[0])
            rows.append(_row(
                source=source_path,
                source_root=source,
                match=match["path"],
                action_path=match["path"],
                root=target,
                target_root=target,
                kind="phash",
                sha256=digest,
                distance=distance,
                source_identifier=str(record["content_identifier"]),
                match_identifier=str(match["content_identifier"]),
                action="keep",
            ))
        else:
            rows.append(_row(
                source=source_path,
                source_root=source,
                match=None,
                action_path=source_path,
                root=source,
                target_root=target,
                kind="new",
                sha256=digest,
                distance=None,
                source_identifier=str(record["content_identifier"]),
                match_identifier="",
                action="copy",
            ))
    return rows


def scan_duplicates(root_path: str | Path) -> list[dict[str, str]]:
    """Return exact duplicate and visually similar image pairs within one folder."""
    root = _absolute(root_path)
    if not root.is_dir():
        raise ValueError("Folder must be an existing directory.")
    records = _hash_records(root)
    rows = []
    exact_pairs: set[frozenset[Path]] = set()
    groups: dict[str, list[dict[str, object]]] = {}
    for record in records:
        groups.setdefault(str(record["sha256"]), []).append(record)

    for digest, group in groups.items():
        for duplicate in group[1:]:
            canonical = group[0]
            exact_pairs.add(frozenset((canonical["path"], duplicate["path"])))
            rows.append(_row(
                source=duplicate["path"],
                source_root=root,
                match=canonical["path"],
                action_path=duplicate["path"],
                root=root,
                target_root=None,
                kind="exact",
                sha256=digest,
                distance=None,
                source_identifier=str(duplicate["content_identifier"]),
                match_identifier=str(canonical["content_identifier"]),
                action="skip",
            ))

    images = [record for record in records if record["phash"] is not None]
    for index, first in enumerate(images):
        for second in images[index + 1:]:
            if first["sha256"] == second["sha256"]:
                continue
            distance = _distance(int(first["phash"]), int(second["phash"]))
            if distance <= PHASH_DISTANCE_THRESHOLD:
                rows.append(_row(
                    source=first["path"],
                    source_root=root,
                    match=second["path"],
                    action_path=first["path"],
                    root=root,
                    target_root=None,
                    kind="phash",
                    sha256=str(first["sha256"]),
                    distance=distance,
                    source_identifier=str(first["content_identifier"]),
                    match_identifier=str(second["content_identifier"]),
                    action="keep",
                ))
    return rows


def write_report(path: str | Path, rows: Iterable[dict[str, str]]) -> None:
    report_path = _absolute(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if row.get("Action") not in ACTION_VALUES:
                raise ValueError(f"Unsupported operation: {row.get('Action')!r}")
            writer.writerow(row)


def read_report(path: str | Path) -> list[dict[str, str]]:
    with _absolute(path).open("r", newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row.get("Action") not in ACTION_VALUES:
            raise ValueError(f"Unsupported operation: {row.get('Action')!r}")
    return rows


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an unused path for {path}")


def _safe_child(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not _inside(resolved, root.resolve()):
        raise ValueError(f"Path is outside its selected folder: {path}")
    return resolved


def _append_log(log_path: Path, entry: dict[str, str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def apply_report(rows: list[dict[str, str]], report_path: str | Path) -> list[str]:
    """Apply approved copies and quarantine moves, recording reversible move entries."""
    results = []
    for row in rows:
        action = row["Action"]
        if action in ("skip", "keep"):
            continue
        if action not in ACTION_VALUES:
            raise ValueError(f"Unsupported operation: {action!r}")

        if action == "copy":
            source_root = _absolute(row["SourceRoot"])
            source = _absolute(row["SourcePath"])
            target_root = _absolute(row["TargetRoot"])
            source = _safe_child(source_root, source)
            try:
                relative = source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"Invalid source path: {source}") from exc
            if not source.is_file() or _sha256(source) != row.get("SHA256"):
                raise ValueError(f"Source file has changed since the report was created: {source}")
            if source == target_root or _inside(source, target_root):
                raise ValueError(f"Refusing to copy a file from inside the target: {source}")
            destination = target_root / relative
            if destination.is_file() and _sha256(destination) == row.get("SHA256"):
                results.append(f"Already present {destination}")
                continue
            destination = _unique_path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            results.append(f"Copied {source} to {destination}")
        elif action == "quarantine":
            root = _absolute(row["RootPath"])
            action_path = _safe_child(root, _absolute(row["ActionPath"]))
            if not action_path.is_file():
                results.append(f"Skipped missing file {action_path}")
                continue
            relative = action_path.relative_to(root)
            if relative.parts and relative.parts[0].casefold() == "quarantine":
                raise ValueError(f"File is already in Quarantine: {action_path}")
            quarantine_root = root / "Quarantine"
            destination = _unique_path(quarantine_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if action_path.stat().st_dev != destination.parent.stat().st_dev:
                raise OSError("Quarantine must be on the same disk as the original file.")
            shutil.move(str(action_path), str(destination))
            log_path = quarantine_root / "action_log.jsonl"
            _append_log(log_path, {
                "operation": "quarantine",
                "root_path": str(root),
                "original_path": str(action_path),
                "quarantine_path": str(destination),
                "sha256": _sha256(destination),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            results.append(f"Quarantined {action_path} to {destination}")

    write_report(report_path, rows)
    return results


def restore_from_log(log_path: str | Path) -> list[str]:
    """Restore quarantined files to their original locations using a JSONL action log."""
    path = _absolute(log_path)
    with path.open("r", encoding="utf-8") as stream:
        entries = [json.loads(line) for line in stream if line.strip()]
    results = []
    for entry in reversed(entries):
        if entry.get("operation") != "quarantine":
            continue
        original = _absolute(entry["original_path"])
        quarantined = _absolute(entry["quarantine_path"])
        root = _absolute(entry.get("root_path", path.parent.parent))
        if path.parent.name.casefold() != "quarantine":
            raise ValueError("The selected action log must be inside a Quarantine folder.")
        if not _inside(quarantined, path.parent.resolve()) or not _inside(original, root):
            raise ValueError("Action log contains a path outside the archive folder.")
        if not quarantined.is_file():
            results.append(f"Skipped missing quarantined file {quarantined}")
            continue
        if original.exists():
            results.append(f"Could not restore because the original path exists: {original}")
            continue
        if _sha256(quarantined) != entry.get("sha256"):
            results.append(f"Could not restore because the quarantined file changed: {quarantined}")
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        if quarantined.stat().st_dev != original.parent.stat().st_dev:
            raise OSError("Restore destination must be on the same disk as the quarantined file.")
        shutil.move(str(quarantined), str(original))
        results.append(f"Restored {quarantined} to {original}")
    return results
