; Optional Windows installer for the same portable PyInstaller SectorRec.exe.
; Run ISCC on Windows after build-windows.ps1 has produced dist/SectorRec.exe.
; This installs per-user, without elevating the installer; raw disk operations
; still request Windows UAC only when needed by SectorRec.
#ifndef AppVersion
  #error AppVersion must be provided from src.__version__ using /DAppVersion=...
#endif

#define AppName "SectorRec"
#define AppExe "SectorRec.exe"

[Setup]
AppId={{4E16810B-4E3C-4B4D-9B78-E378E572A67B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SectorRec Community
DefaultDirName={localappdata}\Programs\SectorRec
DefaultGroupName=SectorRec
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=SectorRec-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
ChangesAssociations=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\windows.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\windows-ddrescue-licenses.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "dist\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SectorRec"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\SectorRec"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch SectorRec"; Flags: nowait postinstall skipifsilent
