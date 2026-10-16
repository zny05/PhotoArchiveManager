# Photo Archive Manager

Photo Archive Manager is a Windows desktop utility for previewing photo and video backups and finding duplicates. The interface defaults to Chinese and can be switched to English. Report and action-log operation values always remain `copy`, `skip`, `quarantine`, or `keep`.

## Run from source

Install Python 3.10 or newer, then run:

```powershell
py -3 -m pip install -r requirements.txt
py -3 main.py
```

Tkinter is included with the standard Windows Python installer. Pillow is used to preview common images and calculate perceptual hashes. Unsupported image codecs can still be scanned for exact SHA-256 duplicates.

## Build a portable Windows folder

On Windows, run `build_windows.bat`. It builds a self-contained `dist\PhotoArchiveManager` folder and packages its contents as `PhotoArchiveManager-Windows.zip`. Unzip the archive and launch `PhotoArchiveManager.exe`; Python does not need to be installed on the destination computer.

## Preview, review, and apply

1. Select **Back up and check destination** to compare a source directory with a destination, or **Scan one folder for duplicates** to find duplicates within one directory. The selected directories must not overlap. A destination is optional in single-folder mode and is only needed if you mark a row `copy`.
2. Generate and save the CSV preview. Preview only reads files and writes the report; it does not copy, move, or delete archive files.
3. Review each row and choose `copy`, `skip`, `quarantine`, or `keep`. Select multiple rows to assign an action in bulk. Double-clicking a row cycles its action.
4. Select **Authorize and Apply** and confirm the prompt before any copy or quarantine move is performed.

SHA-256 matches are exact duplicates. Similar images within a pHash Hamming distance of 8 are listed as review candidates and default to `keep`. The preview panes show image pairs when the image format is supported. `ContentIdentifier` values found in XMP/QuickTime metadata are included in report rows to help identify Live Photo pairs.

Copies retain the source directory's relative paths. Existing files are not overwritten. Quarantined files are moved under a `Quarantine` folder on the same disk and retain their relative paths. Each move is recorded in `Quarantine\action_log.jsonl`; use **Restore from action log** to return quarantined files to their original paths. Restore will not overwrite a file that has since appeared at the original path.

Supported file extensions include common JPEG, PNG, GIF, TIFF, WebP, HEIC/HEIF, camera RAW, MOV, MP4, M4V, AVI, MKV, 3GP, and MTS/M2TS files. Actual visual preview and pHash support depends on Pillow's available image codecs. Video files are checked by SHA-256 and scanned for Live Photo metadata, not compared perceptually.
