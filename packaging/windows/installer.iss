#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\outputs\windows-public\dist\EnglishPracticeMachinePublic"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\outputs\windows-public\installer"
#endif

#define MyAppName "英语刷题机公测版"
#define MyAppExeName "EnglishPracticeMachinePublic.exe"

[Setup]
AppId={{47A53D08-77DB-46D4-AB5B-5B52EA435C2F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=往事随风k
DefaultDirName={localappdata}\Programs\EnglishPracticeMachinePublic
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=EnglishPracticeMachinePublic-{#MyAppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\frontend\public\assets\icons\brand-mark.ico
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=英语刷题机公测版安装程序
VersionInfoProductName={#MyAppName}

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

; No UninstallDelete entries: user data in LOCALAPPDATA/EnglishPracticeMachinePublic is retained.
