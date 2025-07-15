@echo off
setlocal

:: Check if a pattern was provided
if "%~1"=="" (
    echo Usage: %~nx0 [file-pattern]
    echo Example: %~nx0 Mass-2025-01-18*.mp3
    goto :eof
)

set "pattern=%~1"

for %%f in (%pattern%) do (
    echo Processing %%f...
    whisper "%%f" --language en --output_dir ./
)

echo Batch processing complete!
pause