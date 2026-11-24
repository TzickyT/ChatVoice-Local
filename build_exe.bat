@echo off
setlocal
cd /d "%~dp0"
rem Builds a standalone "ChatVoice Local.exe". Run "build_exe.bat debug" to keep a console window for error messages.
where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install Python 3.11 and enable the Python Launcher.
    pause
    exit /b 1
)
rem A running copy of the program locks its files and makes the build fail.
taskkill /f /im "ChatVoice Local.exe" >nul 2>nul
taskkill /f /im "MultiChat TTS.exe" >nul 2>nul
if not exist ".venv\Scripts\python.exe" (
    echo Creating the local Python environment...
    py -3.11 -m venv .venv
    if errorlevel 1 ( echo Could not create the environment. & pause & exit /b 1 )
)
echo Installing the app packages and PyInstaller...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 ( echo Package setup failed. & pause & exit /b 1 )
rem The English text engine needs this language model; an .exe cannot download it later.
".venv\Scripts\python.exe" -c "import spacy.util,sys; sys.exit(0 if spacy.util.is_package('en_core_web_sm') else 1)" >nul 2>nul
if errorlevel 1 (
    echo Installing the English language model...
    ".venv\Scripts\python.exe" -m spacy download en_core_web_sm
)
echo Building the .exe. This takes several minutes and the result is large...
".venv\Scripts\python.exe" build_exe.py %1
if errorlevel 1 ( echo. & echo Build failed or the check found problems. & pause & exit /b 1 )
pause
