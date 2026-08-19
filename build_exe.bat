@echo off
REM Build a standalone Plex2iPod.exe — no Python install needed to run it.
REM Double-click this file to build. The .exe lands in the "dist" folder.

cd /d "%~dp0"

echo.
echo === Plex2iPod build script ===
echo.

REM 1. Make sure PyInstaller is installed
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo.
        echo ERROR: Could not install PyInstaller. Make sure Python is installed
        echo and on your PATH.
        pause
        exit /b 1
    )
)

REM 2. Make sure the icon exists
if not exist "Plex2iPod.ico" (
    echo Generating icon...
    python make_icon.py
)

REM 3. Make sure the ffmpeg binaries exist. They are not stored in git
REM    (~100 MB each), so a fresh clone needs to fetch them first.
if not exist "ffmpeg\ffmpeg.exe" goto :needffmpeg
if not exist "ffmpeg\ffprobe.exe" goto :needffmpeg
goto :haveffmpeg

:needffmpeg
echo.
echo ffmpeg binaries not found in the ffmpeg folder.
echo Plex2iPod bundles ffmpeg for 24-bit -^> 16-bit FLAC conversion.
echo.
echo Fetching them now with download_ffmpeg.py...
echo.
python download_ffmpeg.py
if errorlevel 1 (
    echo.
    echo ERROR: Could not download ffmpeg automatically.
    echo See ffmpeg\README.txt for manual download instructions.
    pause
    exit /b 1
)
if not exist "ffmpeg\ffmpeg.exe" (
    echo.
    echo ERROR: ffmpeg\ffmpeg.exe still not found after download.
    echo See ffmpeg\README.txt for manual download instructions.
    pause
    exit /b 1
)
if not exist "ffmpeg\ffprobe.exe" (
    echo.
    echo ERROR: ffmpeg\ffprobe.exe still not found after download.
    echo See ffmpeg\README.txt for manual download instructions.
    pause
    exit /b 1
)

:haveffmpeg

REM 4. Build
echo.
echo Building Plex2iPod.exe ...
echo.
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name Plex2iPod ^
    --icon Plex2iPod.ico ^
    --paths . ^
    --collect-submodules plex2ipod ^
    --add-data "Plex2iPod.ico;." ^
    --add-binary "ffmpeg\ffmpeg.exe;ffmpeg" ^
    --add-binary "ffmpeg\ffprobe.exe;ffmpeg" ^
    Plex2iPod.pyw

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo === Build complete ===
echo Your executable is at: dist\Plex2iPod.exe
echo.
echo You can copy Plex2iPod.exe anywhere and run it. config.json will
echo be created next to the .exe on first run.
echo.
pause
