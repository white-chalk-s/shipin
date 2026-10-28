#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "视频录制工作台"
#define AppExe "VideoCaptureWorkbench.exe"
[Setup]
AppId={{C04D74CE-DC60-409B-AB93-7A2D3478D891}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=VideoCaptureWorkbench
DefaultDirName={localappdata}\Programs\VideoCaptureWorkbench
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=视频录制工作台_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "启动视频录制工作台"; Flags: nowait postinstall skipifsilent
