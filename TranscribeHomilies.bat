@echo off
setlocal

if "%~1"=="" (
    echo Usage: %~nx0 [file-path]
    goto :eof
)

set "inputFile=%~1"
set "inputDir=%~dp1"

echo Processing "%inputFile%"...
whisper "%inputFile%" --language en --output_dir "%inputDir%"

echo Done.
exit /b
