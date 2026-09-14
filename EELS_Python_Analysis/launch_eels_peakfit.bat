@echo off
setlocal
set "PROJECT=%~dp0"
set "PYTHON=%PROJECT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Python environment not found.
    echo Run this in PowerShell from the project folder:
    echo   py -3.12 -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

"%PYTHON%" "%PROJECT%eels_peakfit.py" %*
endlocal
