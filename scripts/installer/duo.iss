; Duo Windows 安装包脚本（Inno Setup 6.4+，简体中文 + English）。
; 构建：scripts/installer/build_setup.sh（WSL 侧一键交叉编译 + iscc）。
; 设计约束见 docs/windows-setup.md §安装包：
;   每用户安装免管理员、安装目录可选（默认 %LOCALAPPDATA%\Duo，可改 D 盘）、
;   卸载只清自己写入的文件；用户数据 ~/.local/share/duo 默认保留，
;   交互卸载时可选勾选一并删除，静默卸载恒保留。

#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
#ifndef SourceDir
#define SourceDir "."
#endif

[Setup]
AppId={{D2AF0125-AD11-4406-A73D-9D4C6ED2D3E7}
AppName=Duo
AppVersion={#AppVersion}
AppPublisher=Duo
DefaultDirName={localappdata}\Duo
DisableProgramGroupPage=yes
; 每用户安装：不弹 UAC、卸载项写 HKCU；选 D:\ 等用户目录同样免管理员。
PrivilegesRequired=lowest
; 安装/卸载前若 Duo 在运行，弹窗请求关闭（优雅退出，Job Object 带走子进程）。
AppMutex=Local\DuoPanelSingleInstance
SetupIconFile={#SourceDir}\Duo.ico
UninstallDisplayName=Duo
UninstallDisplayIcon={app}\Duo.exe
LicenseFile={#SourceDir}\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#SourceDir}
OutputBaseFilename=Duo-{#AppVersion}-setup
CloseApplications=no

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chs.DesktopTask=创建桌面快捷方式(&D)
chs.LaunchProgram=运行 Duo(&R)
chs.DeleteDataQ=是否同时删除用户数据？
chs.DeleteDataHint=包括设备图标缓存、会话记录与日志。选[是]一并删除，选[否]保留供重新安装后继续使用。
chs.DeleteDataPathPrefix=数据位置：
chs.UninstallDataDeleted=卸载完成。%n%n用户数据（设备图标缓存、会话记录、日志）已一并删除。
chs.UninstallKeepData=卸载完成。%n%n用户数据（设备图标缓存、会话记录、日志）已保留在：%n%USERPROFILE%\.local\share\duo%n如需彻底清理，可手动删除该文件夹。
en.DesktopTask=Create a &desktop shortcut
en.LaunchProgram=&Run Duo
en.DeleteDataQ=Also delete user data?
en.DeleteDataHint=Device icon cache, session records and logs. Choose [Yes] to delete, [No] to keep for reinstall.
en.DeleteDataPathPrefix=Data location:
en.UninstallDataDeleted=Uninstall complete.%n%nYour user data (device icon cache, session records, logs) was deleted as requested.
en.UninstallKeepData=Uninstall complete.%n%nYour user data (device icon cache, session records, logs) was kept at:%n%USERPROFILE%\.local\share\duo%nDelete that folder manually if you want a full cleanup.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopTask}"; Flags: unchecked

[InstallDelete]
; 收编旧脚本/开发期遗留产物，安装时定点清除（仅限本应用目录内已知文件名）。
Type: files; Name: "{app}\uninstall.ps1"
Type: files; Name: "{app}\update.bat"
Type: files; Name: "{app}\Duo-updated.exe"
Type: filesandordirs; Name: "{app}\cache"

[Files]
Source: "{#SourceDir}\Duo.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\duo-core.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\Duo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Duo"; Filename: "{app}\Duo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Duo.ico"
Name: "{autodesktop}\Duo"; Filename: "{app}\Duo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Duo.ico"; Tasks: desktopicon

[Registry]
; 收编旧 PowerShell 安装脚本写入的卸载项，避免控制面板出现两个 Duo。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\Duo"; Flags: deletekey

[Run]
Filename: "{app}\Duo.exe"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
var
  gDeleteData: Boolean;

function DataDirPath: String;
begin
  Result := ExpandConstant('{%USERPROFILE}') + '\.local\share\duo';
end;

function ConfirmDeleteData: Boolean;
var
  Question: String;
begin
  // CreateCustomForm 在卸载上下文不可用；用双按钮确认框，默认按钮=保留（安全缺省）。
  Question := ExpandConstant('{cm:DeleteDataQ}') + #13#10#13#10 +
    ExpandConstant('{cm:DeleteDataHint}') + #13#10#13#10 +
    ExpandConstant('{cm:DeleteDataPathPrefix}') + DataDirPath();
  Result := MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if not UninstallSilent then
      gDeleteData := ConfirmDeleteData();
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    if gDeleteData and DirExists(DataDirPath()) then
      DelTree(DataDirPath(), True, True, True);
    if gDeleteData then
      MsgBox(ExpandConstant('{cm:UninstallDataDeleted}'), mbInformation, MB_OK)
    else
      MsgBox(ExpandConstant('{cm:UninstallKeepData}'), mbInformation, MB_OK);
  end;
end;
