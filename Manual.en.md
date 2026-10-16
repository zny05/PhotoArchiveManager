# Photo Archive Manager User Manual

This manual covers comparison settings, result previews, filters, search, per-entry decisions, and quarantine operations. The application accepts any accessible folders; it does not require paths on D: or F:.

## 0. Language and distribution

The GUI starts in Chinese. Use the language button in the upper-right corner to switch between Chinese and English. This changes display text only; stable operation values in JSONL reports, such as `copy` and `quarantine`, do not change.

For the Windows portable version, extract the package and run `PhotoArchiveManager.exe`. Python is not required on the destination computer. The package includes the application's Python dependencies, ExifTool, FFmpeg/ffprobe, and the icon. Keep the entire `PhotoArchiveManager` folder together; do not move the EXE by itself.

To rebuild from source, run `build_windows.ps1`. The build machine must have FFmpeg's `bin` folder on PATH or set `PHOTO_ARCHIVE_FFMPEG_BIN` to that folder.

## 1. Launch and dependencies

From the project root, install dependencies and launch the GUI:

```powershell
python -m pip install -r requirements-photo-backup.txt
python photo_backup_gui.py
```

Pillow, ImageHash, and pillow-heif are used to read still images and calculate pHash. The ExifTool Python wrapper is included in the requirements. The project also bundles ExifTool 13.59 in `exiftool-13.59_64`. The application looks for `exiftool.exe` in this order: the bundled project copy, the `PHOTO_BACKUP_EXIFTOOL` environment variable, then PATH. Usually you do not need to install ExifTool separately. If you update this directory, keep its `exiftool_files` folder. `exiftool(-k).exe` is the interactive/double-click launcher; the project also includes `exiftool.exe` as the command-line entry point.

Video first-frame previews use FFmpeg. Ensure `ffmpeg.exe` is available on PATH when running from source. The GUI decodes the first video frame in the background and keeps it in memory; it does not write preview images into media folders. If FFmpeg is unavailable, video properties may still be shown, but the preview pane will explain that a frame could not be extracted.

## 2. Choose a scan mode

### Compare source folder to target folder

Use this mode to back up new files from one folder to another and check for duplicates already in the target. `Source` and `Target` can be any two folders that do not contain one another. Source files are read only; apply copies files without deleting or moving the originals.

Files with identical SHA-256 hashes are treated as exact copies. Visually similar images with different hashes are listed as candidates for review; pHash does not automatically skip or quarantine them.

### Find duplicates inside one folder

Use this mode to scan one folder for duplicate photos or videos. After you select the scan folder, `Target` is locked to that same folder. During preview, quarantine subfolders are skipped so previously quarantined files are not scanned again.

In this mode, only rows explicitly authorized for quarantine during apply are moved from the scan folder into Quarantine. Scanning itself does not change files. By default, the quarantine folder, report, and log are placed in a `Quarantine` folder one level above the scan folder.

## 3. Preview options

### Find visual-similarity candidates (pHash)

When enabled, the application calculates perceptual hashes for images that Pillow can decode and lists candidates according to their hash-bit distance. If an image cannot be decoded, a RAW format is unsupported, or a codec is unavailable, the file can still be included in exact SHA-256 comparison and backup, but not in visual comparison.

When disabled, the application still scans files and calculates SHA-256 hashes, but it does not generate pHash candidates. For very large collections, disabling this option can reduce image-decoding work. The console reports `Visual comparison was turned off ...`. A missing-dependency message appears only when visual comparison is enabled but its dependencies cannot be imported. Loading an older report does not change this checkbox, so verify it before starting a scan.

### pHash review distance (0-64)

pHash uses a 64-bit image fingerprint. This value is the maximum Hamming distance allowed between two fingerprints: the number of differing bits out of 64.

- `0`: Only identical pHash fingerprints are listed. This still does not prove byte identity or identical image content.
- `4`: The default. It permits up to four differing bits and is a relatively strict starting point for candidate review.
- Higher values: More candidates are listed, increasing the chance that unrelated images appear for review.
- `64`: Not recommended for normal use because it creates a very broad candidate set.

The threshold controls which candidates are listed, not automatic deletion. A pHash result is never proof that two photos are the same. A low threshold can miss cropped, rotated, edited, or heavily compressed versions; a high threshold increases false positives. Start at `4` or lower, inspect the candidates, and adjust only after reviewing the results.

### Read Live Photo ContentIdentifier (ExifTool)

When enabled, the application attempts to read `ContentIdentifier` values from photos and videos and records any detected still-image/motion-file relationship as evidence in the report. It does not automatically pair, copy, move, or delete files, and it does not pair files based only on their names. The application prefers the ExifTool bundled with the project.

## 4. Results and image preview

After preview finishes, the table on the right shows each decision, operation type, file, matched file, and pHash distance. Select a row to see its image and matched image side by side, along with properties for both files.

The properties panel first shows values available locally, such as file name, type, size, and modification time. When ExifTool is available, it can also show MIME type, capture time, camera make/model, image dimensions, video duration/compressor, orientation, and ContentIdentifier when present. Image and video resolution are read directly from the file and do not depend on ExifTool: Pillow reads still-image dimensions (HEIC requires pillow-heif and dimensions are adjusted for EXIF orientation); ffprobe reads the first video stream, including rotation metadata. If those methods fail, the application falls back to ffmpeg output and then ExifTool width/height tags. Resolution is displayed as `Resolution: 4032 x 3024 px (12.2 MP)`; failed reads show `Unavailable`.

Paths containing Chinese, Japanese, or emoji characters are supported. Modification times are converted from the file timestamp to UTC+08:00. Capture times with a timezone are converted to UTC+08:00; camera times without a timezone are treated as local UTC+08:00 values. Invalid zero dates are omitted. ExifTool reads run in the background; stale results are ignored when you switch rows quickly, and results for unchanged files are cached.

When a video row is selected, the preview pane shows its first frame. Whether RAW, HEIC, and other formats can be previewed depends on Pillow and the available codec plugins. If FFmpeg is unavailable, a format is unsupported, the file is damaged, or the file has moved since preview, the pane displays the reason the preview is unavailable. Previews are only for visual review and do not change the report's SHA-256 or pHash results.

The GUI attempts to maximize at startup. Drag the separators between the left settings pane and the results workspace, and between the results table and the details/preview pane, to adjust their sizes. If the window is not maximized, maximize it before resizing the panes.

`Selected entry` shows full paths, the matched path, and the reason. For Live Photo information rows, it lists the still-image and motion-file paths. The preview can show the still image and an available video first frame; it does not play MOV/MP4 files.

## 5. Filter menu

The Filter menu changes only which rows are shown in the table. It does not change report decisions or scan results.

| Option | Purpose |
| --- | --- |
| `All entries` | Show every entry in the report. |
| `Needs review` | Show entries whose `decision` is still `pending`. Resolve these before applying a plan. |
| `copy` | Show files planned to be copied from Source to Target. |
| `skip` | Show files skipped because an exact SHA-256 copy already exists. |
| `move_exact` | Show byte-identical duplicates of common still-image formats in the target; the default decision is `quarantine`, but apply authorization is still required. |
| `review_target_exact` | Show exact duplicate videos, RAW, TIFF, or sidecar files in the target; these default to pending and require a human decision to quarantine or keep. |
| `review_source_visual` | Show source files visually similar to a Source/Target candidate; choose whether to copy or skip. Source files are never quarantined. |
| `review_target_visual` | Show visually similar image candidates in the target or single-folder scan; choose whether to quarantine the row's file or keep it. |
| `collision` | Show entries whose target relative path already contains different content, so overwrite was refused. Resolve the path conflict manually; this type can only be kept. |
| `live_pair_info` | Show still-image/motion-file relationships found from ContentIdentifier by ExifTool. These are informational and do not trigger file operations. |

The table shows up to 400 rows per page. Use `Previous` and `Next` to navigate. The selected filter and search query apply together.

## 6. Search paths / notes

Type any text in the search box to filter the current report. Search covers file paths, matched paths, reasons, operation types, current decisions, and, when available, ContentIdentifier and Live Photo still/motion paths.

Search does not change files or decisions. Clear the search box to restore all rows under the current Filter.

## 7. Selected decision

1. Select a row in the table.
2. The `Selected decision` menu shows the decisions allowed for that operation.
3. Choose a decision and click `Set`. This changes only the in-memory report; it does not copy or move files.
4. Click `Save decisions`. The GUI can also save automatically before apply.

Allowed decisions by operation:

| Operation | Decisions | Meaning |
| --- | --- | --- |
| `copy` | `copy` / `skip` | Whether to copy the new file to Target. |
| `review_source_visual` | `copy` / `skip` | Whether to back up the visual candidate; Source is never quarantined. |
| `move_exact` | `quarantine` / `keep` | Whether to move an exact duplicate still image into quarantine. |
| `review_target_exact` | `quarantine` / `keep` | Whether to quarantine an exact duplicate video, RAW, TIFF, or sidecar. |
| `review_target_visual` | `quarantine` / `keep` | Whether to quarantine a visually similar target candidate. |
| `collision` | `keep` | Content differs at the target path; the application will not overwrite it. |
| `skip` | `skip` | An exact copy already exists, so no copy is needed. |
| `live_pair_info` | `keep` | Informational metadata relationship; no file operation is performed. |

Apply is blocked while any report entry remains `pending`. Select `Needs review`, resolve each entry with `Set`, then save and apply.

### Bulk decisions (thousands of entries)

`Bulk decision for ALL filtered entries` applies a decision to every page matching the current Filter and Search, not just the visible page:

1. Narrow the scope with `Filter` (for example, `review_target_visual` or `move_exact`) and `Search paths / notes` (for example, a subfolder name).
2. Choose `copy`, `skip`, `quarantine`, or `keep` from the bulk menu.
3. When `Only pending` is checked, only `pending` rows change; existing manual decisions remain. Uncheck it to replace decisions for all compatible rows in scope.
4. Click `Set all filtered`. The confirmation dialog shows the number of entries to change, the filter scope, and how many incompatible rows will be skipped. For example, `quarantine` is skipped for `review_source_visual`, which allows only `copy` or `skip`.
5. Use `Undo last bulk` to revert the most recent bulk change. Loading another report clears the undo history.

Bulk decisions change only the in-memory report and do not move files. Afterward, click `Save decisions`, check the Apply safeguards, and apply. It is sensible to process one filter at a time and inspect a sample of the side-by-side previews.

Quarantine results can be reviewed manually in the Quarantine folder. Quarantined files retain their relative paths, and the Action log records each source and destination. To recover a file, move it back to its original path.

## 8. Apply safeguards

These four checkboxes provide additional authorization for apply. They do not replace per-row decisions and do not start an operation by themselves. Before changing files, confirm the Source, Target, Quarantine, Report, and Action log paths.

### Allow quarantine moves (master permission)

This is the master switch for quarantine operations. If any row is set to `quarantine`, this checkbox is required. It does not choose files for quarantine; set the relevant report rows to `quarantine` first.

### Allow reviewed visual-match quarantine

This separately authorizes visually similar `review_target_visual` entries to be quarantined. The row must be set to `quarantine`, and both this checkbox and the master quarantine authorization must be checked. It does not apply to `review_source_visual`; source-side visual candidates can only be copied or skipped.

### Allow reviewed video quarantine

This separately authorizes video files to be quarantined. Exact duplicate videos default to pending. Set the corresponding row to `quarantine`, then check this and the master quarantine authorization. Only the selected video file is moved; a paired still image is not moved automatically. If a Live Photo relationship is unclear or its motion component should be retained, choose `keep`.

### Confirm filesystem changes

Every copy or quarantine move requires this checkbox and confirmation in the dialog shown after clicking `Apply reviewed plan`. Without it, apply is refused. Copying keeps the Source file in place. Quarantine moves files out of their original folders, so authorize only after reviewing the report and knowing how to restore them.

## 9. Correctly quarantine files

Clicking `Create preview` only writes a JSONL report; it does not move photos. Seeing `move_exact` or another candidate does not mean that the file has already entered quarantine.

### Exact duplicate still images

1. Choose the appropriate backup or single-folder mode and verify the Source/Target and Quarantine paths.
2. Click `Create preview` and wait for scanning to finish.
3. Select the `move_exact` filter. Review each candidate's original path, matched path, and decision; change entries that should not be quarantined to `keep`.
4. Check `Allow quarantine moves (master permission)` and `Confirm filesystem changes`.
5. Click `Apply reviewed plan` and confirm the dialog.
6. Check the Action log shown in the completion message, then inspect both the Quarantine folder and the original folder.

### Visually similar candidates

`review_target_visual` entries default to `pending`. Under `Needs review`, inspect each pair of images. Set confirmed duplicates to `quarantine`; set different photos or edited versions to `keep`. When applying, check the master quarantine authorization, `Allow reviewed visual-match quarantine`, and the confirmation checkbox, then click Apply and confirm.

`review_source_visual` entries compare source candidates. They allow only `copy` or `skip`. Choosing `copy` copies the Source file to Target; it never moves or quarantines a Source file.

### Videos, RAW, TIFF, and sidecars

Even when their SHA-256 hashes match exactly, these types are listed as pending `review_target_exact` entries. To quarantine one, set its row to `quarantine` and check the master authorization. For a video, also check `Allow reviewed video quarantine`. Check any Live Photo still/motion relationship individually; the application does not automatically move a pair as one unit.

Quarantine uses a same-volume rename (move), not a hard link, so exFAT/FAT32 backup drives are supported. Quarantine must be on the same filesystem/volume as the folder being organized; apply refuses cross-volume paths before reading any files. The application never permanently deletes quarantined files. To restore one, move it from Quarantine to its original relative path and consult the Action log.

## 10. Why no files moved

After you click `Apply reviewed plan`, the application follows the decisions already set in the report: rows marked `quarantine` are moved into quarantine, rows marked `copy` are copied to Target, and all other rows are left alone. It does not reread, rehash, or verify media. Changes made to files after preview are therefore not detected; avoid changing the scanned folders between preview and apply.

The GUI briefly shows `Preparing plan` (checks paths, authorization, target conflicts, and whether a plan would quarantine every copy in a group; it does not read file contents), then `Applying changes` (performs the actual copies or moves). At completion, a dialog lists the number copied, the number moved into quarantine, and the full quarantine path. You can also click `Open quarantine folder` in the left pane.

Common reasons no file moved:

- You created a preview but have not applied it.
- You checked the wrong folder. The current default is `Quarantine` one level above the folder being organized; older versions used `PhotoQuarantine` inside the scan folder. Use the path shown in the dialog and `Quarantine folder` field.
- The dialog reports `moved to quarantine 0`: no row was set to `quarantine` (perhaps all are `keep`, `skip`, or `copy`). The dialog explains this case.
- Some entries are still `pending`; apply is disabled or refused until they are resolved.
- All table decisions are `keep`, `skip`, or `copy`; none requests quarantine.
- `Allow quarantine moves (master permission)` is unchecked.
- A visual candidate lacks its separate visual-match authorization, or a video lacks the separate video authorization.
- `Confirm filesystem changes` is unchecked, or you canceled the confirmation dialog.
- The Quarantine path is outside the allowed scope, already occupied, or on a different filesystem from the target being organized.
- Apply found a target conflict (a destination already exists or multiple rows share a destination) or a plan that would quarantine every copy in a group. It stops before moving any file.
- The selected row is not a duplicate, such as `collision`; this operation cannot be quarantined, and different content is never overwritten.

## 11. Default outputs, progress bars, and keep preferences

### Default output locations

After you select the folder being organized (the scan folder in single-folder mode, or Target in backup mode), `Quarantine folder`, `Review report`, and `Action log` default to a `Quarantine` folder one level above it. For a scan of `E:\Photos`, the defaults are:

- `E:\Quarantine\`
- `E:\Quarantine\review-<timestamp>.jsonl`
- `E:\Quarantine\actions-<timestamp>.jsonl`

Manually edited paths are preserved. If a report or log filename already exists, a new timestamped name is chosen. A drive root such as `D:\` has no parent folder; choose output paths manually in that case.

### Progress bars

Two progress bars appear below the table. `Preview` shows the five `Create preview` phases: scanning folders, reading Live Photo identifiers, hashing Target, hashing Source, and image similarity comparison. It also shows the number processed and a percentage. `Apply / auto-pick` shows Apply's `Preparing plan` and `Applying changes` stages, as well as progress for keep-preference auto-pick.

### Selected entry

When you select a row, `Selected entry` shows the action, current decision, pHash distance, what Apply will do for the row (move to quarantine, copy, do nothing, or pending), quarantine destination, full selected and matched paths, both SHA-256 hashes with an indication of byte identity, and the reason. Live Photo rows show the ContentIdentifier and still/motion paths.

### Keep preference / auto-pick

Click `Keep preference / auto-pick...` to automatically choose which file to keep across all pages in the current Filter and Search results, and mark the others for quarantine. You can set up to four criteria. Earlier criteria take priority; later criteria break ties. If all criteria tie, the path name is the final tie-breaker.

| Criterion | Meaning |
| --- | --- |
| Preferred file type | Prefer the order in `File type order`. The default is `.dng, .heic, .heif, .tiff, .tif, .png, .jpg, .jpeg, .webp`. |
| Higher resolution | Prefer the file with more total pixels. |
| Larger / Smaller file size | Prefer the larger or smaller file. |
| Newer / Older creation time | Prefer the newer or older creation time. |

The default priority is file type, resolution, file size, then creation time (older first). Rules:

- Exact duplicates (`move_exact`, `review_target_exact`): keep only the best file in each group and mark the rest `quarantine`.
- Visual similarity (`review_target_visual`): keep the better file in each pair and mark the other `quarantine`. If the better file in one pair is marked for removal as the worse file in another pair, leave that pair unchanged for manual review. The completion summary reports these as `left for manual review`.
- `review_source_visual`: choose `copy` if the Source file is better, or `skip` if the matched file is better.
- When `Only pending` is checked, only pending rows are processed. Exact duplicate decisions are not pending by default; uncheck this option to rerun the preference rules for them.
- Results change only the in-memory report and can be reverted with `Undo last bulk`. Then save decisions, authorize, and apply.
- Apply has an additional safeguard and refuses any plan that would quarantine every copy in the same group.

## 12. Reports and logs

The Preview report stores scan results and per-entry decisions. Apply requires a new Action log path and records the source, destination, SHA-256, and time for each successful copy or quarantine move. Keep reports and logs outside media folders and retain a backup copy for traceability and recovery.

Each preview recalculates SHA-256 for supported files in collections of up to hundreds of thousands of items. Start with a small folder to verify thresholds, results, and quarantine paths, then process larger collections in batches. Never move a file based only on a matching filename, capture time, or pHash result.
