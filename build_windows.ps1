$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistRoot = Join-Path $ProjectRoot 'dist'
$WorkRoot = Join-Path $ProjectRoot 'build'
$FfmpegBin = $env:PHOTO_ARCHIVE_FFMPEG_BIN
if ([string]::IsNullOrWhiteSpace($FfmpegBin)) {
    $FfmpegCommand = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($FfmpegCommand) { $FfmpegBin = Split-Path -Parent $FfmpegCommand.Source }
}
if ([string]::IsNullOrWhiteSpace($FfmpegBin)) {
    throw 'Set PHOTO_ARCHIVE_FFMPEG_BIN to the directory containing ffmpeg.exe and ffprobe.exe, or add FFmpeg to PATH.'
}
$Python = (Get-Command python).Source
$BuildEnvironment = Join-Path $env:LOCALAPPDATA 'PhotoArchiveManagerBuildVenv'
$BuildPython = Join-Path $BuildEnvironment 'Scripts\python.exe'

if (-not (Test-Path $BuildPython)) {
    & $Python -m venv $BuildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Could not create build environment at $BuildEnvironment." }
}
& $BuildPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements-photo-backup.txt') pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies into $BuildEnvironment." }

$ExeToolSource = Join-Path $ProjectRoot 'exiftool-13.59_64\exiftool(-k).exe'
$ExeToolTarget = Join-Path $ProjectRoot 'exiftool-13.59_64\exiftool.exe'
if (-not (Test-Path $ExeToolSource)) { throw "Bundled ExifTool source missing: $ExeToolSource" }
if (-not (Test-Path (Join-Path $ProjectRoot 'icon.ico'))) { throw 'icon.ico is missing.' }
foreach ($Name in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Test-Path (Join-Path $FfmpegBin $Name))) { throw "Bundled media tool missing: $FfmpegBin\$Name" }
}

# The Windows download uses the -k launcher name; create the command-line entry once and preserve it thereafter.
if (-not (Test-Path $ExeToolTarget)) {
    Copy-Item -LiteralPath $ExeToolSource -Destination $ExeToolTarget
}

if (Test-Path $DistRoot) { Remove-Item -LiteralPath $DistRoot -Recurse -Force }
if (Test-Path $WorkRoot) { Remove-Item -LiteralPath $WorkRoot -Recurse -Force }

$Args = @(
    '--noconfirm', '--clean', '--onedir', '--windowed',
    '--name', 'PhotoArchiveManager',
    '--distpath', $DistRoot,
    '--workpath', $WorkRoot,
    '--specpath', $ProjectRoot,
    '--icon', (Join-Path $ProjectRoot 'icon.ico'),
    '--add-data', "$(Join-Path $ProjectRoot 'exiftool-13.59_64');exiftool-13.59_64",
    '--add-data', "$(Join-Path $ProjectRoot 'icon.ico');.",
    '--add-binary', "$(Join-Path $FfmpegBin 'ffmpeg.exe');.",
    '--add-binary', "$(Join-Path $FfmpegBin 'ffprobe.exe');.",
    '--hidden-import', 'PIL._tkinter_finder',
    '--hidden-import', 'pillow_heif',
    '--hidden-import', 'imagehash',
    (Join-Path $ProjectRoot 'photo_backup_gui.py')
)
& $BuildPython -m PyInstaller @Args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Output = Join-Path $DistRoot 'PhotoArchiveManager'
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'Manual.md') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot '使用说明书.md') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot '使用说明书.pdf') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'requirements-photo-backup.txt') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE') -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'THIRD_PARTY_NOTICES.md') -Destination $Output -Force
$Zip = Join-Path $DistRoot 'PhotoArchiveManager-Windows-x64.zip'
if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -Path (Join-Path $Output '*') -DestinationPath $Zip -CompressionLevel Optimal
Write-Host "Build complete: $(Join-Path $Output 'PhotoArchiveManager.exe')"
Write-Host "Portable package: $Zip"
