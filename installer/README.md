# Cornerstone Mock 安装程序

**发布版本**由仓库根目录 [`VERSION`](../VERSION) 决定（当前 **0.1.8**）；`build-release.ps1` 与 Inno Setup 均读取该文件。每次打包还会自动生成**构建标识码**（UTC 时间戳 + Git 短哈希，例如 `20250525143000-a1b2c3d`），输出 `CornerstoneMock-Setup-<版本>-<标识码>.exe`。

生成 **Bridge（必选）**、**Web / Queue / CLI（可选）** 的 Windows 安装包；支持将 **Bridge**、**Web** 注册为 Windows 服务（默认勾选）。

`staging/`、`pydist/`、`pywork/`、`.venv-build/`、`_qtest/` 及 `dist/` 安装包为本地构建产物，已列入仓库根目录 `.gitignore`，**不会**提交到 GitHub。

## 前置条件


| 工具                                                | 用途                                                                                                               |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Python 3.14+                                      | PyInstaller 打包 `cornerstone-bridge` / `cornerstone-web` / `cornerstone-cli`（`build-release.ps1` 优先使用 `py -3.14`） |
| .NET 8 SDK                                        | `dotnet publish` 打包 `CornerstoneQueue`                                                                           |
| [Inno Setup 6](https://jrsoftware.org/isinfo.php) | 生成 `CornerstoneMock-Setup-<版本>-<标识码>.exe`                                                                       |
| Visual Studio 2022+（仅 Queue）                      | WinUI 3 + Windows App SDK                                                                                        |


## 一键构建

在仓库根目录或本目录执行（若提示「未进行数字签名 / 无法运行脚本」，用下面带 `Bypass` 的方式，或先执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`）：

```powershell
cd installer
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1
```

等价于（本机会话已允许脚本时）：

```powershell
cd installer
.\build-release.ps1
```

产物：

- `installer\staging\` — 待打包文件树
- `%LOCALAPPDATA%\CornerstoneMock\installer-dist\CornerstoneMock-Setup-x.y.z-<标识码>.exe` — **请运行此安装包**（构建默认输出，避免云同步占位符）
- `installer\dist\CornerstoneMock-Setup-x.y.z-<标识码>.exe` — 可选副本（若仓库在 OneDrive/网盘同步，此文件可能是**占位符**，双击会报 *The setup files are corrupted*；请在资源管理器中对该文件选「始终保留在此设备上」，或只使用上面 LocalAppData 路径）
- 安装目录 `{app}\build-info.json` — 记录 `version`、`build_id`、`built_at`，便于现场确认所装包

仅构建 exe、不编译安装包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1 -SkipInstaller
```

若清理 `staging` 时报 `libcrypto-1_1.dll` / Access denied，说明 **CornerstoneBridge/Web 服务或进程仍占用** `installer\staging`（常见于曾用 staging 注册过服务）。`build-release.ps1` 会自动停止相关服务；仍失败时可手动执行：

```powershell
Stop-Service CornerstoneBridge, CornerstoneWeb -Force -ErrorAction SilentlyContinue
```

## 安装向导选项


| 组件              | 默认       | 说明                                                                      |
| --------------- | -------- | ----------------------------------------------------------------------- |
| **Bridge**      | 必选（不可取消） | TCP 网关 + REST `:8081`                                                   |
| **Bridge 控制台** | 选中       | `Bridge\cornerstone-bridge-ui.exe`（托盘：配置、日志筛选、连接/队列监控、服务启停）              |
| **Web**         | 选中       | 静态页 `:8080`，代理 `/api/`*                                                 |
| **Queue**       | 选中       | WinUI 悬浮窗（自包含 .NET 8 + WASDK；语言资源仅 **en-us** / **zh-CN** / **zh-Hans**） |
| **CLI**         | 选中       | `cornerstone-cli` 命令行                                                   |
| **Bridge 系统服务** | 选中       | 服务名 `CornerstoneBridge`（以 **LocalSystem** 运行，满足管理员权限要求）                 |
| **Web 系统服务**    | 选中       | 服务名 `CornerstoneWeb`                                                    |

可选任务（勾选 **Bridge 控制台** 时可见）：

- **登录时启动 Bridge 控制台**：写入 `HKCU\...\Run\CornerstoneBridgeUI`，系统托盘常驻。

**单实例（Bridge / Web / Queue / Bridge 控制台）**

同一应用同时只保留一个进程。再次启动时会先结束已有实例（按 PID 锁文件与进程名），再启动新进程。锁文件目录：`%ProgramData%\CornerstoneMock\run\`。

**管理员权限（Bridge / Queue / Bridge 控制台）**

- **Bridge**：打包 exe **不**嵌入 `requireAdministrator`（监听端口 >1024，无需 UAC）；否则 NSSM 以 LocalSystem 启动服务时会**立即退出**（services.msc 显示 Paused/Stopped、双击闪退）。需管理员时可对快捷方式选「以管理员身份运行」。服务由 NSSM 以 **LocalSystem** 运行。
- **Bridge 控制台**：安装版 `cornerstone-bridge-ui.exe` 嵌入 `requireAdministrator`（便于停止/重启 Bridge 服务）；开发版 pip 安装默认不要求 UAC。
- **Queue**：`CornerstoneQueue.exe` 嵌入 `requireAdministrator` 清单，从快捷方式或直接运行 exe 时均会弹出 UAC。

配置文件与样品队列持久化（首次安装从安装包内 `config\*.example.toml` 复制为 TOML；若 Roaming 下仍有旧版 `.json` 会自动迁移并删除 JSON。样品队列**仍为 JSON**，不转换）：

`%APPDATA%\CornerstoneMock\cornerstone-bridge.config.toml`（仍兼容未迁移的 `.json`）  
`%APPDATA%\CornerstoneMock\cornerstone-web.config.toml`（仍兼容未迁移的 `.json`）  
`%APPDATA%\CornerstoneMock\cornerstone-bridge.add-samples-queue.json`（样品队列持久化）

服务与安装日志（同配置目录下）：

`%APPDATA%\CornerstoneMock\logs\`

## 安装目录结构

默认安装路径：`C:\Program Files\CornerstoneMock\`

```text
C:\Program Files\CornerstoneMock\
  Bridge\cornerstone-bridge.exe
  Bridge\cornerstone-bridge-ui.exe
  Bridge\_internal\...
  Web\cornerstone-web.exe
  Queue\CornerstoneQueue.exe
  CLI\cornerstone-cli.exe
  config\*.example.toml
  build-info.json
  tools\nssm.exe
  scripts\*.ps1
```

安装最后一步会**弹出 PowerShell 窗口**（标题「Cornerstone Mock - 安装后配置与服务」），依次执行配置合并、`validate-install`、NSSM 服务注册；请勿关闭该窗口直至显示「安装后步骤完成」。端口检查在 `-NonInteractive` 下仅写 `logs\validate-install.log`，不弹 WinForms 对话框。

## 手动编译 Inno Setup

```powershell
.\build-release.ps1 -SkipInstaller
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DStagingRoot="$PWD\staging" Cornerstone.iss
```

## 升级 / 重装

安装程序启动时若检测到**同 AppId 已安装**或仍存在 `C:\Program Files\CornerstoneMock\Bridge\cornerstone-bridge.exe`，会提示**先卸载原版本**：

- 可选「是」自动运行已注册的卸载程序（静默停止服务、结束进程后删除程序目录）。
- **不会删除** `%APPDATA%\CornerstoneMock\` 下的 `cornerstone-bridge.config.toml`（或 `.json`）、`cornerstone-web.config.toml`（或 `.json`）、样品队列 JSON 等用户配置。
- 卸载完成后请**重新运行**安装包；若目录仍存在，安装程序会退出并提示手动检查。

新安装或覆盖安装时，`post-install.ps1` 会：

1. 若存在旧版 `%ProgramData%\CornerstoneMock\` 配置，先迁移到 `%APPDATA%\CornerstoneMock\`。
2. 若 Roaming 下仍有旧版 `.json`，则与安装包内 `config\*.example.toml` **合并**并迁移为 TOML（保留 IP/端口/账号等，仅补全新增项；**样品队列 JSON 不转换**）。

## 卸载

卸载时会执行 `scripts\uninstall-services.ps1`：停止 `CornerstoneBridge` / `CornerstoneWeb` 服务、结束 `cornerstone-bridge` / `cornerstone-web` / `CornerstoneQueue` 等进程、移除 NSSM 服务，再删除 `C:\Program Files\CornerstoneMock\`。**用户配置目录 `%APPDATA%\CornerstoneMock\` 默认保留。**