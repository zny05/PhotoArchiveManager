# Changelog

All notable changes to Photo Archive Manager are listed here, newest first. Every new release is accompanied by an entry in this file.

## v2.0.0 - 2026-10-17

### Added
- Pause and Stop buttons below "Open quarantine folder". They control preview, apply, and keep-preference auto-pick tasks. Pause becomes Resume while paused.
- Stopping takes effect after the current file. A stopped preview writes no report, a stopped auto-pick changes no decisions, and a stopped apply keeps completed operations in the action log (create a new preview before applying again).
- Chinese and English text for the new controls and their status messages.

### Fixed
- ffprobe and ffmpeg no longer open console windows while resolution is read during keep-preference / auto-pick, previews, and video first-frame extraction on Windows.

### Tests
- Added tests for pause, resume, stop, and hidden-console subprocess launch.

## v1.9.9 - 2026-10-16

### Added
- Complete English user manual (`Manual.en.md`), shipped in the portable package and linked from the README.
- Bilingual call-to-action at the bottom of the left sidebar inviting users to star and help maintain the project, with a clickable GitHub link.

## v1.0.0 - 2026-10-16

### Added
- First public release: Windows desktop GUI, Chinese by default with an English toggle.
- Backup mode (source to target) and single-folder duplicate scan, with a reviewed preview, per-entry and bulk decisions, keep-preference auto-pick, and recoverable quarantine with an action log.
- Exact duplicate detection by SHA-256; pHash similarity and Live Photo ContentIdentifier are review evidence only.
- Buttons to open the quarantine folder, review report, and review log.
- Portable Windows package bundling Python dependencies, ExifTool, FFmpeg/ffprobe, and the supplied icon.
- Chinese user guide (Markdown and PDF), third-party notices, and `iphone-photo-dedup` referenced as a Git submodule.

### Security
- Removed machine-specific paths from build scripts and documentation; the FFmpeg location is now set through `PHOTO_ARCHIVE_FFMPEG_BIN` or PATH.
