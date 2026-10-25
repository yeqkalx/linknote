; ---------------------------------------------------------------------------
; LinkNote 0.3.0 — Inno Setup 6 script (per-user, no admin required)
;
; Built by desktop\packaging\build-desktop.ps1, which passes
;   /DNoEngine=1            omit the optional bundled BiliNote engine payload
;   /DMyAppVersion=0.3.0    version string (default below)
;
; Expected inputs (all produced by the build script):
;   ..\..\dist-desktop\LinkNote\*   PyInstaller onedir output (LinkNote.exe + _internal)
;   ..\build\engine\*               staged BiliNote engine (optional component)
;   ..\linknote_desktop\resources\icon.ico
;
; Install layout:  %LOCALAPPDATA%\Programs\LinkNote
;   LinkNote.exe, _internal\, engine\ (optional), LICENSE, NOTICE
; User data:       %LOCALAPPDATA%\LinkNote   (settings.json, agent.json, logs,
;                   notes, outbox — NEVER touched by the uninstaller)
; ---------------------------------------------------------------------------

#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif
#ifndef MyVersionInfoVersion
  #define MyVersionInfoVersion "0.3.0.0"
#endif
; The optional bundled BiliNote engine component is included unless the build
; passes /DNoEngine=1 (desktop\packaging\build-desktop.ps1 -SkipEngine /
; missing desktop\build\engine staging).
#ifdef NoEngine
  #define IncludeEngine 0
#else
  #define IncludeEngine 1
#endif
#define MyAppName "LinkNote"
#define MyAppNameCN "链记"
#define MyAppPublisher "LinkNote"
#define MyAppExeName "LinkNote.exe"
; Neutral placeholder for self-hosted builds: release owner replaces this with the
; project repository/docs URL. Must not contain any author-operated domain.
#define MyAppURL "https://github.com/"

[Setup]
; Keep this AppId stable forever: upgrades and uninstall depend on it.
AppId={{B7E3A1C4-2D9F-4A62-8E11-5C7F0B6D3A29}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyVersionInfoVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} {#MyAppNameCN} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyVersionInfoVersion}
; ---- per-user install, no UAC prompt ----
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
; ---- payload / output ----
OutputDir=..\..\dist-desktop
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
SetupIconFile=..\linknote_desktop\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
DisableProgramGroupPage=yes
AllowNoIcons=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
; Default ON: LinkNote is a tray app that must stay resident, so ordinary users
; expect "start with Windows". Writes the same HKCU Run value the app's own
; "开机自启" toggle uses (value name "LinkNote", command "<exe>" --minimized),
; so installer and in-app toggle are idempotent.
Name: "autostart"; Description: "开机自动启动 {#MyAppName}（最小化到托盘）"; GroupDescription: "附加任务："

[Components]
Name: "app"; Description: "{#MyAppName} 桌面应用（必需）"; Types: full compact custom; Flags: fixed
#if IncludeEngine
; Bundled by default: ordinary users must get a working engine out of the box
; (only ffmpeg + LLM Key + Whisper model download are left to first-run setup).
Name: "engine"; Description: "内置 BiliNote 引擎（约 300 MB，离线开箱可用，必需）"; Types: full compact custom; Flags: fixed
#endif

[Files]
Source: "..\..\dist-desktop\LinkNote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\NOTICE"; DestDir: "{app}"; Flags: ignoreversion
; GPL-3.0 / LGPL-3.0 full texts (FFmpeg LGPL shared build download + Qt LGPL notice)
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
#if IncludeEngine
Source: "..\build\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: engine
Source: "THIRD-PARTY-NOTICES.txt"; DestDir: "{app}\engine"; Flags: ignoreversion; Components: engine
#endif

[Icons]
Name: "{group}\{#MyAppName} {#MyAppNameCN}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName} {#MyAppNameCN}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Optional autostart (HKCU Run). Value name/command match linknote_desktop/services/autostart.py
; so toggling it later in the app updates exactly the same value instead of duplicating.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LinkNote"; ValueData: """{app}\{#MyAppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; Clean upgrade: drop the previous PyInstaller runtime before laying down the new one.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"

[UninstallDelete]
; Intentionally empty: %LOCALAPPDATA%\LinkNote (settings.json, agent.json, pairing
; token, logs, notes, outbox) must survive uninstall. Runtime data that the bundled
; BiliNote engine writes under {app}\engine is not tracked by the uninstaller and is
; therefore left behind as well.

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;







