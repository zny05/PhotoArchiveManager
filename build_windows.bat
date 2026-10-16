@echo off
setlocal

py -3 -m venv .venv
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PhotoArchiveManager.spec
if errorlevel 1 exit /b %errorlevel%
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PhotoArchiveManager\*' -DestinationPath 'PhotoArchiveManager-Windows.zip' -Force"
if errorlevel 1 exit /b %errorlevel%
echo Created PhotoArchiveManager-Windows.zip
