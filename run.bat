@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install Python 3.11 and enable the Python Launcher.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating the local Python environment...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Could not create the environment. Confirm Python 3.11 is installed.
        pause
        exit /b 1
    )
)
echo Checking required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Could not download or update packages. Checking whether the installed ones are enough...
    ".venv\Scripts\python.exe" -c "import kokoro, sounddevice, soundfile, numpy, websockets, keyring" >nul 2>nul
    if errorlevel 1 (
        echo Package setup failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo Using the packages that are already installed.
)
echo Starting ChatVoice Local...
".venv\Scripts\python.exe" main.py
if errorlevel 1 echo ChatVoice Local stopped because of an error.
pause
