# Cornerstone Mock 安装程序

生成 **Bridge（必选）**、**Web / Queue / CLI（可选）** 的 Windows 安装包；支持将 **Bridge**、**Web** 注册为 Windows 服务（默认勾选）。

`staging/`、`pydist/`、`pywork/`、`.venv-build/`、`_qtest/` 及 `dist/` 安装包为本地构建产物，已列入仓库根目录 `.gitignore`，**不会**提交到 GitHub。

## 前置条件

| 工具 | 用途 |
| --- | --- |
| Python 3.8+ | PyInstaller 打包 `cornerstone-bridge` / `cornerstone-web` / `cornerstone-cli` |
| .NET 8 SDK | `dotnet publish` 打包 `CornerstoneQueue` |
| [Inno Setup 6](https://jrsoftware.org/isinfo.php) | 生成 `CornerstoneMock-Setup-0.1.0.exe` |
| Visual Studio 2022+（仅 Queue） | WinUI 3 + Windows App SDK |

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
- `%LOCALAPPDATA%\CornerstoneMock\installer-dist\CornerstoneMock-Setup-0.1.0.exe` — **请运行此安装包**（构建默认输出，避免云同步占位符）
- `installer\dist\CornerstoneMock-Setup-0.1.0.exe` — 可选副本（若仓库在 OneDrive/网盘同步，此文件可能是**占位符**，双击会报 *The setup files are corrupted*；请在资源管理器中对该文件选「始终保留在此设备上」，或只使用上面 LocalAppData 路径）

仅构建 exe、不编译安装包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1 -SkipInstaller
```

若清理 `staging` 时报 `libcrypto-1_1.dll` / Access denied，说明 **CornerstoneBridge/Web 服务或进程仍占用** `installer\staging`（常见于曾用 staging 注册过服务）。`build-release.ps1` 会自动停止相关服务；仍失败时可手动执行：

```powershell
Stop-Service CornerstoneBridge, CornerstoneWeb -Force -ErrorAction SilentlyContinue
```

## 安装向导选项

| 组件 | 默认 | 说明 |
| --- | --- | --- |
| **Bridge** | 必选（不可取消） | TCP 网关 + REST `:8081` |
| **Web** | 选中 | 静态页 `:8080`，代理 `/api/*` |
| **Queue** | 选中 | WinUI 悬浮窗（自包含 .NET 8 + WASDK；语言资源仅 **en-us** / **zh-CN** / **zh-Hans**） |
| **CLI** | 选中 | `cornerstone-cli` 命令行 |
| **Bridge 系统服务** | 选中 | 服务名 `CornerstoneBridge`（以 **LocalSystem** 运行，满足管理员权限要求） |
| **Web 系统服务** | 选中 | 服务名 `CornerstoneWeb` |

**单实例（Bridge / Web / Queue）**

同一应用同时只保留一个进程。再次启动时会先结束已有实例（按 PID 锁文件与进程名），再启动新进程。锁文件目录：`%ProgramData%\CornerstoneMock\run\`。

**管理员权限（Bridge / Queue）**

- **Bridge**：`cornerstone-bridge.exe` 嵌入 `requireAdministrator` 清单；直接双击或命令行启动时会弹出 UAC。注册为 Windows 服务时由 NSSM 以 **LocalSystem** 账户运行（无需交互 UAC，权限等同管理员）。
- **Queue**：`CornerstoneQueue.exe` 嵌入 `requireAdministrator` 清单，从快捷方式或直接运行 exe 时均会弹出 UAC。

配置文件与样品队列持久化（首次安装从安装包内 `config\*.example.json` 复制，默认为**与 Cornerstone 同机**的 `127.0.0.1`；现场 IP 请在安装后编辑 Roaming 下 JSON，勿改仓库 example）：

`%APPDATA%\CornerstoneMock\cornerstone-bridge.config.json`  
`%APPDATA%\CornerstoneMock\cornerstone-web.config.json`  
`%APPDATA%\CornerstoneMock\cornerstone-bridge.add-samples-queue.json`（样品队列持久化）

服务与安装日志（同配置目录下）：

`%APPDATA%\CornerstoneMock\logs\`

## 安装目录结构

默认安装路径：`C:\Program Files\CornerstoneMock\`

```text
C:\Program Files\CornerstoneMock\
  Bridge\cornerstone-bridge.exe
  Bridge\_internal\...
  Web\cornerstone-web.exe
  Queue\CornerstoneQueue.exe
  CLI\cornerstone-cli.exe
  config\*.example.json
  tools\nssm.exe
  scripts\*.ps1
```

安装结束前会检测本机端口（`port` / `bridge_api_port` / `web_port`）及 `privileged_add_samples_host`，如有冲突会弹出对话框并打开 `%APPDATA%\CornerstoneMock\` 配置目录供修改。

## 手动编译 Inno Setup

```powershell
.\build-release.ps1 -SkipInstaller
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DStagingRoot="$PWD\staging" Cornerstone.iss
```

## 安装包提示 “The setup files are corrupted”？

多为 **安装 exe 是云同步占位符**（文件带「云」图标、未完全下载到本机），CRC 校验失败。处理：

1. 运行 `%LOCALAPPDATA%\CornerstoneMock\installer-dist\CornerstoneMock-Setup-0.1.0.exe`（`build-release.ps1` 的默认输出）。
2. 或重新构建后，在资源管理器中对该 exe 选 **始终保留在此设备上**。
3. 勿从尚未同步完成的 `installer\dist\` 占位符直接安装。

## Queue 打不开？

| 现象 | 处理 |
| --- | --- |
| 退出码 `0xC0000135` | 旧包未自包含 .NET 8 → 用当前脚本重打安装包并覆盖安装，或安装 [.NET 8 桌面运行时](https://dotnet.microsoft.com/download/dotnet/8.0) |
| Debug 能开、Release 安装包不能开 | 多为裁剪掉 `zh-Hans` 语言目录或 `PublishTrimmed`；请用**最新** `build-release.ps1`（会保留 en-us/zh-CN/zh-Hans 并做启动冒烟测试） |
| 仍闪退 | 查看 `%LocalAppData%\CornerstoneQueue\startup-crash.log` |

快捷方式工作目录为 `{app}\Queue`，请勿只复制 exe 到其它路径。

## 服务在 services.msc 里找不到？

安装向导里若勾选了「将 Bridge/Web 安装为 Windows 服务」，应出现：

| 服务名（内部名） | 显示名 |
| --- | --- |
| `CornerstoneBridge` | Cornerstone Mock Bridge |
| `CornerstoneWeb` | Cornerstone Mock Web |

在 `services.msc` 中可用 **C** 排序后找 **Cornerstone**，不要只搜 “bridge” 小写。

**安装完成但 services.msc 里没有服务**

1. 查看日志：`%APPDATA%\CornerstoneMock\logs\post-install.log`。若出现 `install-services.ps1 exit=-196608`，多为旧版安装包在**已管理员安装**时又弹二次 UAC（静默安装会直接失败）。请用**最新**脚本重打安装包，或手动运行 `register-services-here.cmd`。
2. 查看 `install-services.log`。若出现 `Can't open service!`，多为 NSSM 未以管理员运行。
2. 日志中应有 `InstallBridgeSvc=1`、`Registered service: CornerstoneBridge`、`Status=Running`（或 Stopped 但服务已存在）。
3. 确认向导里勾选了「将 Bridge 安装为 Windows 服务」。
4. 须用**当前仓库**重打安装包后覆盖安装。
5. 手动注册服务（**不要**在普通 PowerShell 里 `Copy-Item` 到 Program Files，会「访问被拒绝」）：

**推荐（无需复制脚本）** — 在资源管理器中双击：

`C:\work\CornerstoneMock\installer\register-services-here.cmd`

会用仓库里**最新**的 `install-services.ps1` 注册服务（自动弹 UAC）。

**若要覆盖安装目录下的旧脚本** — 必须用提升权限复制：

```powershell
# 在普通 PowerShell 中执行，会自动弹 UAC：
& "C:\work\CornerstoneMock\installer\scripts\deploy-scripts-elevated.ps1"
```

窗口标题须含 **管理员** 字样后再 `Copy-Item`，否则仍会拒绝访问。

服务起不来时看日志：`%APPDATA%\CornerstoneMock\logs\bridge-stderr.log`、`web-stderr.log`。

**控制台中文乱码、`Task exception ... ConnectionResetError`**

- 乱码：Bridge 以 UTF-8 打印中文，控制台若为西欧代码页会显示成 `Mnö` 等；服务安装已设 `PYTHONUTF8=1`，**手动运行 exe** 前可执行 `chcp 65001` 或设置用户环境变量 `PYTHONUTF8=1`，或改用最新安装包。
- `WinError 64` / `network name is no longer available`：Cornerstone 断开了上游 TCP（仅允许单远程会话、读循环异常等）。新版 Bridge 会丢弃僵死连接并自动重连；客户端 **Logon 若不带 `Cookie`** 会导致应答无法回路由（已自动补 Cookie）。
- 仪器本机部署时 `upstream_host` 应为 **`127.0.0.1`**、`upstream_port` 与 Cornerstone「Remote Access」端口一致（常见 `12345`）。`privileged_add_samples_host` 填**实际连网关的客户端 IP**（日志里 `client connected` 的地址），否则 `AddSamples` 会进队列而非直通。

**服务状态为 Paused / 反复重启**

多为进程启动后立即崩溃。若 stderr 出现 `UnicodeEncodeError: 'charmap' codec can't encode`（cp1252），说明旧版 exe 在 NSSM 下打印中文失败；请用**最新** `build-release.ps1` 重打安装包（已强制 UTF-8 控制台并设置 `PYTHONUTF8`）。修复后：

```powershell
Stop-Service CornerstoneBridge, CornerstoneWeb -Force -ErrorAction SilentlyContinue
# 覆盖安装或重新运行 install-services.ps1
Start-Service CornerstoneBridge, CornerstoneWeb
```

## 卸载

安装程序会卸载时停止并移除 `CornerstoneBridge` / `CornerstoneWeb` 服务（若已注册）。
