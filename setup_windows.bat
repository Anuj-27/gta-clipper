@echo off
cd /d "%~dp0"
echo === GTA Clipper Setup ===
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv .venv
)
echo Installing Python dependencies...
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install "yt-dlp[default]"
echo.
echo Setup complete! Run run_gta_clipper.bat to launch the app.
pause
