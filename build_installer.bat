@echo off
setlocal
cd /d "%~dp0"
rem Turns the finished build in dist\ into a one-file installer. Run build_exe.bat first.
if not exist "dist\ChatVoice Local\ChatVoice Local.exe" (
    echo Run build_exe.bat first; there is no finished build in the dist folder.
    pause
    exit /b 1
)
set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist %%P set "ISCC=%%~P"
if not defined ISCC (
    echo Inno Setup 6 is needed to make the installer. It is free: https://jrsoftware.org/isdl.php
    echo Install it, then run this file again.
    pause
    exit /b 1
)
echo Building the installer. This can take several minutes...
"%ISCC%" installer.iss
if errorlevel 1 ( echo Installer build failed. & pause & exit /b 1 )
echo.
echo Done. The installer is in the installer_output folder.
pause
