# Photo Archive Manager

Windows desktop tool for previewing photo backups and reviewing duplicates. The GUI defaults to Chinese and can switch to English. Preview generates a report; copying and quarantine moves require reviewed decisions and explicit confirmation.

## Download

The Windows x64 portable package is published in [GitHub Releases](https://github.com/zny05/PhotoArchiveManager/releases). The package includes the Python runtime dependencies, ExifTool, FFmpeg/ffprobe, and the application icon. Keep the extracted `PhotoArchiveManager` folder together when launching the executable.

The Chinese user guide is available as [Markdown](使用说明书.md) and [PDF](使用说明书.pdf). The GUI manual is available in [Chinese](Manual.md) and [English](Manual.en.md).

## From source

Install Python 3.12 and the dependencies:

```powershell
python -m pip install -r requirements-photo-backup.txt
python photo_backup_gui.py
```

To build the portable Windows package, install FFmpeg and set `PHOTO_ARCHIVE_FFMPEG_BIN` to its `bin` folder, then run:

```powershell
$env:PHOTO_ARCHIVE_FFMPEG_BIN = 'C:\ffmpeg\bin'
.\build_windows.ps1
```

Run the tests with `python -m unittest discover -s tests -v`.

## Safety

- SHA-256 equality identifies byte-identical files. Perceptual-hash matches are review candidates, not proof of identity.
- Preview does not modify media. Apply requires a reviewed report and confirmation; quarantine moves are recoverable and files are not permanently deleted.
- Do not use matching names, timestamps, or Live Photo identifiers alone as evidence that files can be removed. Review related still and motion assets together.

## Related project

[`iphone-photo-dedup`](https://github.com/dcliftreaves/iphone-photo-dedup) is included as a Git submodule; its source is maintained in its own repository and is not copied into this project. Clone both repositories with:

```powershell
git clone --recurse-submodules https://github.com/zny05/PhotoArchiveManager.git
```

The parent project is licensed under GPL-3.0. The submodule retains its upstream Apache-2.0 license. Third-party package notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).