#define AppName "STEMDECK Enhanced"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\STEMDECK-Enhanced-Windows-x64.NVIDIA"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist"
#endif
#ifndef OutputBaseFilename
#define OutputBaseFilename "STEMDECK-Enhanced-Windows-x64.NVIDIA-Setup"
#endif

[Setup]
AppId={{A8EE5C18-8B1D-447C-9E3F-3BADDEC37136}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=STEMDECK Enhanced unofficial fork test build
AppPublisherURL=https://github.com/bassmicrobe/stemdeck
AppSupportURL=https://github.com/bassmicrobe/stemdeck/issues
AppUpdatesURL=https://github.com/bassmicrobe/stemdeck/releases
DefaultDirName={localappdata}\Programs\STEMDECK Enhanced
DefaultGroupName=STEMDECK Enhanced
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
UninstallDisplayIcon={app}\STEMDECK Enhanced.exe
LicenseFile={#SourceDir}\LICENSE
InfoBeforeFile={#SourceDir}\README-WINDOWS.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\STEMDECK Enhanced"; Filename: "{app}\STEMDECK Enhanced.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\STEMDECK Enhanced"; Filename: "{app}\STEMDECK Enhanced.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\STEMDECK Enhanced.exe"; Description: "{cm:LaunchProgram,STEMDECK Enhanced}"; Flags: nowait postinstall skipifsilent
