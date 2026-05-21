; Cornerstone Mock 安装程序（Inno Setup 6）
; 编译: iscc /DStagingRoot=绝对路径\installer\staging Cornerstone.iss
; 或由 build-release.ps1 自动调用

#ifndef StagingRoot
  #define StagingRoot "staging"
#endif

#define MyAppName "Cornerstone Mock"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "LECO"
#define MyAppId "{{A7B3C2E1-9F4D-4A2B-8C1E-5D6F7A8B9C0D}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\CornerstoneMock
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=CornerstoneMock-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Queue\CornerstoneQueue.exe
SetupLogging=yes

[Languages]
; 官方安装包默认不含简体中文，使用仓库内 languages\ChineseSimplified.isl（来自 issrc Unofficial）
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "完整安装（推荐）"
Name: "custom"; Description: "自定义安装"; Flags: iscustom

[Components]
Name: "bridge"; Description: "Cornerstone Bridge（TCP 网关 + REST API）— 必选"; Types: full custom; Flags: fixed
Name: "web"; Description: "Cornerstone Web（浏览器管理界面）"; Types: full; Flags: checkablealone
Name: "queue"; Description: "Cornerstone Queue（样品队列悬浮窗）"; Types: full
Name: "cli"; Description: "Cornerstone CLI（命令行工具）"; Types: full

[Tasks]
; 任务默认勾选；仅需取消勾选时用 Flags: unchecked（6.7 起不再支持 Flags: checked）
Name: "svcbridge"; Description: "将 Bridge 安装为 Windows 服务（开机自动启动）"; GroupDescription: "Windows 服务:"; Components: bridge
Name: "svcweb"; Description: "将 Web 安装为 Windows 服务（开机自动启动）"; GroupDescription: "Windows 服务:"; Components: web
Name: "desktopicon"; Description: "创建桌面快捷方式（Queue）"; GroupDescription: "快捷方式:"; Components: queue

[Files]
; Bridge（必选）— exe 与 _internal 直接在 Bridge\ 下
Source: "{#StagingRoot}\Bridge\*"; DestDir: "{app}\Bridge"; Components: bridge; Flags: ignoreversion recursesubdirs createallsubdirs
; Web
Source: "{#StagingRoot}\Web\*"; DestDir: "{app}\Web"; Components: web; Flags: ignoreversion recursesubdirs createallsubdirs
; Queue
Source: "{#StagingRoot}\Queue\*"; DestDir: "{app}\Queue"; Components: queue; Flags: ignoreversion recursesubdirs createallsubdirs
; CLI
Source: "{#StagingRoot}\CLI\*"; DestDir: "{app}\CLI"; Components: cli; Flags: ignoreversion recursesubdirs createallsubdirs
; 配置示例与工具
Source: "{#StagingRoot}\config\*"; DestDir: "{app}\config"; Flags: ignoreversion
Source: "{#StagingRoot}\tools\nssm.exe"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "{#StagingRoot}\scripts\*.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{group}\Cornerstone Queue"; Filename: "{app}\Queue\CornerstoneQueue.exe"; WorkingDir: "{app}\Queue"; Components: queue
Name: "{group}\Cornerstone Web"; Filename: "http://127.0.0.1:8080/"; Components: web
Name: "{group}\打开配置目录"; Filename: "{userappdata}\CornerstoneMock"
Name: "{autodesktop}\Cornerstone Queue"; Filename: "{app}\Queue\CornerstoneQueue.exe"; WorkingDir: "{app}\Queue"; Tasks: desktopicon; Components: queue

[Run]
Filename: "powershell.exe"; Parameters: "{code:GetPostInstallPsArgs}"; Flags: runhidden waituntilterminated; StatusMsg: "正在完成配置与服务注册..."; Description: "配置与服务"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\uninstall-services.ps1"" -AppDir ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "UninstallServices"

[Code]
function BoolToStr(Value: Boolean): String;
begin
  if Value then
    Result := '1'
  else
    Result := '0';
end;

function GetPostInstallPsArgs(Param: String): String;
begin
  Result := ExpandConstant('-ExecutionPolicy Bypass -File "{app}\scripts\post-install.ps1" ') +
    '-AppDir "' + ExpandConstant('{app}') + '" ' +
    '-ConfigDir "' + ExpandConstant('{userappdata}\CornerstoneMock') + '" ' +
    '-InstallBridgeSvc ' + BoolToStr(WizardIsComponentSelected('bridge') and WizardIsTaskSelected('svcbridge')) + ' ' +
    '-InstallWebSvc ' + BoolToStr(WizardIsComponentSelected('web') and WizardIsTaskSelected('svcweb')) + ' ' +
    '-InstallBridge ' + BoolToStr(WizardIsComponentSelected('bridge')) + ' ' +
    '-InstallWeb ' + BoolToStr(WizardIsComponentSelected('web'));
end;
