; Inno Setup script. build_exe.py writes installer_version.iss; build_installer.bat compiles this file.
#include "installer_version.iss"
#define MyAppName "ChatVoice Local"
#define MyAppExe "ChatVoice Local.exe"

[Setup]
AppId={{7B1F2E3A-5C84-4D6A-9E0B-3A2F6C9D1E57}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
; Installs for the current user only (no administrator prompt). The program saves its settings next to its
; files, which works because this folder is writable.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ChatVoiceLocal_Setup_{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[InstallDelete]
Type: files; Name: "{app}\MultiChat TTS.exe"
Type: files; Name: "{autoprograms}\MultiChat TTS.lnk"
Type: files; Name: "{autodesktop}\MultiChat TTS.lnk"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

; Saved settings, voices and presets are deliberately kept when the program is uninstalled or updated.
