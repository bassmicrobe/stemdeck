#define AppName "LayerLab"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\LayerLab-Windows-x64.NVIDIA"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist"
#endif
#ifndef OutputBaseFilename
#define OutputBaseFilename "LayerLab-Windows-x64.NVIDIA-Setup"
#endif

[Setup]
AppId={{A8EE5C18-8B1D-447C-9E3F-3BADDEC37136}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=LayerLab unofficial StemDeck fork test build
AppPublisherURL=https://github.com/bassmicrobe/stemdeck
AppSupportURL=https://github.com/bassmicrobe/stemdeck/issues
AppUpdatesURL=https://github.com/bassmicrobe/stemdeck/releases
DefaultDirName={localappdata}\Programs\LayerLab
DefaultGroupName=LayerLab
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
UninstallDisplayIcon={app}\LayerLab.exe
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
Name: "{group}\LayerLab"; Filename: "{app}\LayerLab.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\LayerLab"; Filename: "{app}\LayerLab.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LayerLab.exe"; Description: "{cm:LaunchProgram,LayerLab}"; Flags: nowait postinstall skipifsilent
