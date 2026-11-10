; Inno Setup Script untuk Fileora
[Setup]
AppName=Fileora
AppVersion=1.0.0
AppPublisher=Fileora
DefaultDirName={autopf}\Fileora
DefaultGroupName=Fileora
SetupIconFile=assets\fileora.ico
UninstallDisplayIcon={app}\Fileora.exe
OutputDir=dist
OutputBaseFilename=Fileora_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "indonesian"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Buat pintasan di Desktop"; GroupDescription: "Ikon tambahan:"

[Files]
Source: "dist\Fileora.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Fileora"; Filename: "{app}\Fileora.exe"
Name: "{autodesktop}\Fileora"; Filename: "{app}\Fileora.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Fileora.exe"; Description: "Jalankan Fileora"; Flags: nowait postinstall skipifsilent

