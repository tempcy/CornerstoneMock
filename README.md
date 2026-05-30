# Cornerstone 远程控制（Python CLI + Bridge + Web）

**当前版本：0.1.8**（变更见 [CHANGELOG.md](CHANGELOG.md)）

后续开发路线图见 [PLAN.md](PLAN.md)。

本仓库为 **Python CLI（`CornerstoneCLI`）**、**Bridge（`CornerstoneBridge`）** 与 **Web UI（`CornerstoneWeb` 目录，包名 `cornerstone-web`）**。原始 C# WPF 客户端请放在本地 `**Cornerstone_RemoteControlClient/`** 目录自行对照（该目录已列入 `.gitignore`，**不会**推送到 Git）。

- `**CornerstoneCLI/`**：`cornerstone-cli`（协议与 TCP 通信内核）。
- `**CornerstoneBridge/`**：`cornerstone-bridge`（TCP 网关、XML 解析、`/api/`* REST）；`cornerstone-bridge-ui`（可选 PySide6 托盘控制台，见 `[ui]` 额外依赖）。
- `**CornerstoneWeb/`**：`cornerstone-web`（静态页 + 将 `/api/`* 代理到 Bridge）；`cornerstone-web-dev` 一键起 Bridge + Web。
- `**CornerstoneQueue/`**：WinUI 3 桌面悬浮窗（缓存样品队列，HTTP 调 Bridge REST；可选仪器 UI 自动点击确认）。

下文所述 **Python 版通信内核** 与配套工具位于上述三个子目录中，用于替代/复用原客户端中的核心通信逻辑：

- **TCP 通道**：4 字节小端长度前缀 + payload（默认 UTF-16LE，对应 C# `Encoding.Unicode`）
- **HTTP 通道**：向云端 `https://remote.lecosoftware.com/...` 发送 `text/xml` POST（对应 C# `WebRequestor`）

> 说明：当前版本聚焦“通信层可用、协议一致、可跑通基本命令”。WPF UI/ViewModel 迁移会在此基础上继续推进。

## CLI / Bridge / Web 概览


| 工具            | 入口命令                              | 作用                                                     |
| ------------- | --------------------------------- | ------------------------------------------------------ |
| **CLI**       | `cornerstone-cli`                 | 直连仪器 TCP 或云端 HTTP；脚本与协议调试。                             |
| **Bridge**    | `cornerstone-bridge`              | TCP 网关 + `/api/`* REST（队列、instrument_rq、解析 JSON）。      |
| **Bridge UI** | `cornerstone-bridge-ui`           | 桌面控制台（系统托盘）：配置、日志（级别筛选/智能滚动）、连接/队列监控、服务启停；连 Bridge REST API。      |
| **Web**       | `cornerstone-web`                 | 静态 SPA；`/api/`* 反向代理到 Bridge。                          |
| **Queue**     | `CornerstoneQueue`（VS 生成 exe）     | 精简悬浮窗：队列查看/发送、状态一行、连 Bridge `:8081`；可选 FlaUI 自动点击仪器确认。 |
| **本地开发**      | `cornerstone-web-dev` / `dev.ps1` | 同进程启动 Bridge + Web（读 Bridge + Web 两份配置，或兼容旧版单文件）。      |


**典型组合**：`cornerstone-web-dev` 或分开起 Bridge + Web；TCP 客户端与 `cornerstone-cli tcp …` 的 `--host/--port` 指向配置中的 `**host`/`port`**（网关端口，非 `web_port`）。

### 运行时架构（当前）

```
浏览器 ──► cornerstone-web :8080  (/ 静态页, /api/* 代理)
                    │
CornerstoneQueue ───┤
  (悬浮窗 REST)     ▼
            cornerstone-bridge :8081  (/api/* REST)
                    ├── TCP 网关 :54321  ◄── 远程客户端 / CLI
                    └── 上游 TCP ──► Cornerstone 仪器
```

配置文件拆为两份（复制对应示例为本地配置后修改）：


| 文件                                                         | 职责                                                                                                                                                |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CornerstoneBridge/cornerstone-bridge.config.example.toml` | **Bridge**（`#` 注释）；复制为 `cornerstone-bridge.config.toml`。仍兼容旧版 `.json`。安装包复制到 `%APPDATA%\CornerstoneMock\` |
| `CornerstoneWeb/cornerstone-web.config.example.toml`       | **Web**（`#` 注释）；复制为 `cornerstone-web.config.toml`。仍兼容旧版 `.json`                                                                                    |


## 安装

需要 **Python 3.14+**（建议安装后执行 `py -3.14` 验证，并设为默认；仓库根目录含 `.python-version` 供 pyenv/IDE 识别）。

在仓库根目录依次安装（Bridge/Web 依赖 `cornerstone-cli`）：

```powershell
# Windows：若默认 python 仍较旧，可显式指定 3.14
py -3.14 -m pip install -e ./CornerstoneCLI
py -3.14 -m pip install -e ./CornerstoneBridge
py -3.14 -m pip install -e "./CornerstoneBridge[ui]"
py -3.14 -m pip install -e ./CornerstoneWeb
```

```bash
# 已将 python 设为 3.14 时
python -m pip install -e ./CornerstoneCLI
python -m pip install -e ./CornerstoneBridge
python -m pip install -e ./CornerstoneWeb
```

仅需命令行时，可只安装 `CornerstoneCLI`。

## Windows 安装程序（exe + 安装包）

使用 `installer/` 目录可打包**全部可执行程序**并生成 Inno Setup 安装向导：

```powershell
cd installer
.\build-release.ps1
```

生成 `CornerstoneMock-Setup-<版本>-<构建标识码>.exe`（默认在 `%LOCALAPPDATA%\CornerstoneMock\installer-dist\`）。说明见 [installer/README.md](installer/README.md)。

默认程序目录 `C:\Program Files\CornerstoneMock\`；配置与队列缓存 `%APPDATA%\CornerstoneMock\`（安装时从包内 example 复制）。安装结束前会检测端口与特权 IP，并引导打开配置目录修改。


| 安装组件            | 默认           | 说明                                                           |
| --------------- | ------------ | ------------------------------------------------------------ |
| **Bridge**      | **必选**（不可取消） | `cornerstone-bridge.exe`（PyInstaller 目录发行版）                  |
| **Bridge 控制台**  | 选中           | `Bridge\cornerstone-bridge-ui.exe`（托盘：配置、日志筛选、连接/队列监控、服务启停） |
| **Web**         | 选中           | `cornerstone-web.exe`                                        |
| **Queue**       | 选中           | `CornerstoneQueue.exe`（自包含 .NET 8 + WASDK，仪器机无需另装运行时）        |
| **CLI**         | 选中           | `cornerstone-cli.exe`                                        |
| **Bridge 系统服务** | 选中           | 服务名 `CornerstoneBridge`，配置 `%APPDATA%\CornerstoneMock\`      |
| **Web 系统服务**    | 选中           | 服务名 `CornerstoneWeb`                                         |


安装后请编辑 `%APPDATA%\CornerstoneMock\cornerstone-bridge.config.toml`（或旧版 `.json`）：上游仪器地址、`privileged_add_samples_host`、端口等。开发调试仍可用 pip 可编辑安装，不必使用安装包。

## Bridge 桌面控制台

`cornerstone-bridge-ui` 与 Bridge 同包（`cornerstone_bridge.ui`），需安装 `**[ui]`** 额外依赖（PySide6）。托盘应用与 `cornerstone-web` 浏览器界面互补：

- **连接与监控**：上游仪器 TCP、已连接远程客户端列表、`GET /api/monitor`；左下角持续显示 Bridge REST 连接状态；可启停上游连接与 TCP 网关、停止/重启 Bridge 服务（安装版需管理员权限）。
- **通讯队列**：待发送 AddSamples 列表（`GET /api/queue`）
- **配置**：编辑 `cornerstone-bridge.config.toml`（或 `.json`）；「**应用到运行中的 Bridge**」可**不重启**更新：上游地址、队列上限、特权 IP、`log_verbose_gateway`、控制台 `log_level`；TCP/REST 监听、长连接模式等须重启服务。
- **日志**：尾随 `log_file`；可按 **DEBUG / INFO / WARNING / ERROR** 级别筛选；向上查看历史时新日志不会强制滚到底部（仅在已处于底部时自动跟随）。**RQ 类 XML** 默认不进日志文件，须勾选详细日志并「应用」后才会出现在此页。
- **托盘**：单击图标打开主窗口；支持登录时自启动（安装包可选任务）

```powershell
# 安装控制台依赖后（在仓库根或 CornerstoneBridge 目录）
py -3.14 -m pip install -e "./CornerstoneBridge[ui]"
# Bridge 服务/进程须已在运行；控制台只连 REST（默认 8081），不会占用 54321
cornerstone-bridge-ui
```

> **勿与网关重复启动**：控制台**不会**也**不应**替代 `cornerstone-bridge` / Bridge 服务。请运行 `cornerstone-bridge-ui`（或开始菜单「Bridge 控制台」），**不要**再双击 `cornerstone-bridge.exe`；否则端口 54321 冲突（WinError 10048）。打开控制台**无需**先停止 Bridge 服务。安装版控制台默认请求管理员权限（便于重启服务）。

## 启用 Web

Web 界面由 **Bridge**（网关 + `/api/`*）与 **Web**（静态页 + API 代理）两个进程组成。日常开发推荐一键启动；生产或分网部署时可分开起。

### 1. 准备配置

复制示例配置并按本机环境修改（至少核对 **上游仪器** 与 **网页登录账号**）：

```bash
cd CornerstoneBridge
copy cornerstone-bridge.config.example.toml cornerstone-bridge.config.toml
cd ..\CornerstoneWeb
copy cornerstone-web.config.example.toml cornerstone-web.config.toml
```

**Bridge**（`cornerstone-bridge.config.toml`，支持 `#` 注释；旧版 `.json` 仍可用）：


| 配置项                                   | 含义                                                                     |
| ------------------------------------- | ---------------------------------------------------------------------- |
| `upstream_host` / `upstream_port`     | 真实 Cornerstone 仪器 TCP 地址                                               |
| `host` / `port`                       | 网关对 **TCP 客户端**（含 C# 远程客户端）的监听                                         |
| `bridge_api_host` / `bridge_api_port` | Bridge 对内 REST（默认 `8081`）                                              |
| `upstream_heartbeat_interval`         | 上游静默超过该秒数时 Bridge 才主动发 Heartbeat（秒，默认 `0` = 禁用；若仪器已对 TCP 客户端有应答或其它上行报文则视为在线、不发心跳） |
| `upstream_inner_reassembly_timeout`   | 拆包续读等待（秒，默认 `5`；`0`= 不等待）                                              |
| `upstream_recv_idle_clear`            | recv 缓冲空闲超过该秒数后下次数据到达前清空（默认 `5`）                                       |
| `upstream_heartbeat_fail_max`         | 连续心跳无应答达此次数则回收上游 TCP（默认 `2`）                                           |
| `upstream_command_fail_max`           | 连续仪器指令超时/异常达此次数则回收上游 TCP（默认 `3`）                                       |
| `upstream_client_forward_timeout`     | TCP 客户端经网关转发后等待上游应答（秒，默认 `10`）                                         |
| `upstream_heartbeat_wait_timeout`     | Bridge 主动 Heartbeat 等待应答（秒；`0` = `max(TCP转发超时, 15)`）                        |
| `upstream_activity_stale_seconds`     | 无上行活动超过该秒数则回收上游 TCP（`0` = `max(3×心跳间隔, 90)`）                            |
| `upstream_read_cancel_timeout`        | 回收 TCP 时等待读循环 cancel 的最长时间（秒，默认 `5`）                                     |
| `upstream_stale_check_interval`       | 定时检查 activity_stale 的间隔（秒，默认 `30`；`0` = 关闭）                                |
| `upstream_auto_reconnect`             | 上游断线后自动重连（配置里为 `true`/`false`；CLI 用 `--no-upstream-auto-reconnect` 关闭） |
| `instrument_long_connection`          | 长连接复用上游 TCP（`true` 默认；对应 CLI `--instrument-short-connection` 的反义）      |
| `no_synthetic_logon`                  | `false`（默认）= 上游曾 Logon 成功后，后续 TCP 客户端 Logon/Logoff 由网关合成应答、不占用仪器会话 |
| `web_user` / `web_password`           | 网页「发送到仪器」、环境/分析页拉数用的仪器远程账号                                             |


**Web**（`cornerstone-web.config.toml`，支持 `#` 注释；旧版 `.json` 仍可用）：


| 配置项                                   | 含义                                   |
| ------------------------------------- | ------------------------------------ |
| `web_host` / `web_port`               | 浏览器访问地址（默认 `http://127.0.0.1:8080/`） |
| `bridge_api_host` / `bridge_api_port` | Web 将 `/api/`* 代理到的 Bridge REST      |


环境变量：`CORNERSTONE_BRIDGE_CONFIG`、`CORNERSTONE_WEB_CONFIG`（或兼容旧名 `CORNERSTONE_MOCK_CONFIG`）。也可将 `.config.{toml,json}` 放在**当前工作目录**。`cornerstone-web-dev` 会合并两份配置；若仍只有旧版单文件 `cornerstone-web.config.{toml,json}`，会自动兼作 Bridge 配置并提示拆分。

### 2. 本地开发用仓库内配置

`cornerstone-web-dev` **优先**读取（未设置环境变量时）：

- `CornerstoneBridge/cornerstone-bridge.config.toml`（或 `.json`；上游仪器等）
- `CornerstoneWeb/cornerstone-web.config.toml`（或 `.json`；浏览器 `8080`、Bridge API `8081`）

不会自动使用 `%APPDATA%\CornerstoneMock\` 下的安装版配置。若要用其它文件，请设置 `CORNERSTONE_BRIDGE_CONFIG` / `CORNERSTONE_WEB_CONFIG`。

### 3. 本地无仪器时：上游占位 Mock（可选）

Bridge 配置里 `upstream_host` / `upstream_port`（常见 `127.0.0.1:12345`）必须有人监听。若无真实 Cornerstone，可在**另一个终端**先起占位服务：

```powershell
cd D:\work\CornerstoneMock
python scripts\dev-instrument-mock.py
```

开发前请**停止**已安装的 CornerstoneBridge / CornerstoneWeb Windows 服务，否则会占用 `54321` / `8081` 端口。仅 upstream 连不上时 Bridge 会打警告，但 Web 仍应能打开；若进程直接退出，请更新到含 Python 3.14 关闭修复的代码后再试。

### 4. 启动（推荐：开发一键）

在仓库根目录或 **`CornerstoneWeb`** 目录（已放置两份 `.config.toml` 或旧版 `.config.json` / 单文件）下执行：

```bash
python -m cornerstone_web.dev_web
```

若已把 Python 的 **Scripts** 目录加入系统 PATH，也可直接：

```bash
cornerstone-web-dev
```

Windows 下 Scripts 常见路径（`pip install` 后若提示 *not on PATH*，即为此目录）：

`%APPDATA%\Python\Python314\Scripts`（或 `Python314` 安装目录下的 `Scripts`）

可临时加入当前 PowerShell 会话后再用短命令：

```powershell
$env:Path += ";$env:APPDATA\Python\Python314\Scripts"
cornerstone-web-dev
```

或在 `CornerstoneWeb` 目录执行仓库提供的脚本：

```powershell
.\dev.ps1
```

启动成功后：

- 浏览器打开 `**http://127.0.0.1:8080/**`（与配置中 `web_host` / `web_port` 一致）
- TCP 客户端连 `**host:port**`（示例为 `54321`）
- Bridge REST 在 `**http://127.0.0.1:8081/**`（供悬浮窗、脚本等直连；Web 会把 `/api/*` 代理到此端口）

### 3. 启动（分开：Bridge + Web）

需要先起 Bridge，再起 Web（Web 依赖 Bridge 的 REST）：

```bash
# 终端 1：网关 + API
cornerstone-bridge -c CornerstoneBridge/cornerstone-bridge.config.toml

# 终端 2：静态页 + /api 代理
cornerstone-web -c CornerstoneWeb/cornerstone-web.config.toml
```

命令行覆盖示例（仅改网页端口）：

```bash
cornerstone-web-dev --web-port 9000
```

### 4. 使用前检查

1. `**web_user` / `web_password` 已填写**：否则队列「发送至仪器」、环境/分析等页会失败。
2. **端口未被占用**：`web_port`（8080）、`bridge_api_port`（8081）、`port`（TCP 网关）互不冲突。
3. **能连上上游**：Bridge 控制台应出现 upstream 连接/Logon 相关日志；网页顶栏可查看连接与 `RemoteControlState`。
4. **仅内网使用**：当前 TCP/HTTP 未做鉴权，勿暴露到公网。

### 5. 常见问题


| 现象                                                                                                | 处理                                                                                   |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `cornerstone-web-dev` 无法识别                                                                        | 先 `pip install -e ./CornerstoneWeb`，或改用 `python -m cornerstone_web.dev_web`；见上文 PATH |
| `cornerstone-web-dev` 启动即 `TypeError: run_bridge() missing ... upstream_inner_reassembly_timeout` | 更新到含修复的 `CornerstoneWeb` 后 `pip install -e ./CornerstoneWeb` 再试                      |
| 页面能开但 `/api/`* 502                                                                                | 确认 Bridge 已启动且 `bridge_api_port` 与配置一致                                               |
| 发送样品失败                                                                                            | 检查 `web_user` / `web_password` 是否与仪器远程账号一致                                           |
| 改 `web_port` / `port` 不生效                                                                         | 修改监听端口后需**重启**对应进程（`cornerstone-web-dev` 或 Bridge/Web）                               |


## cornerstone-cli 全部命令

以下为安装后 `cornerstone-cli` 的完整子命令树。除另有说明外，**TCP 子命令**均形如：

`cornerstone-cli tcp <子命令> --host <地址> --port <端口> [其它选项]`

公共 TCP 选项（多数子命令可用）：`--culture`、`--encoding`（utf16/utf8/ascii）、`--timeout`。

### `tcp` 子命令一览

第四列表示 `**cornerstone-bridge` 是否除 TCP 透传外，还集成了与该子命令等价的 XML 调用及应答的结构化解读**（经 Bridge REST 供 Web `/api/...`、队列 UI 或网关内部状态）。图例见表下说明。


| 子命令                      | 需 `--username` / `--password`（先 Logon） | 说明摘要                                                   | Bridge 集成（调用+解读）                                                                                                                                                                                                                                                                     |
| ------------------------ | -------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `session`                | 可选（可自动 Logon）                          | 长连接 + 可选心跳与空闲超时；交互式 `>` 提示符                            | 透传                                                                                                                                                                                                                                                                                   |
| `version`                | 否                                      | `<Version/>`                                           | 透传                                                                                                                                                                                                                                                                                   |
| `supported-cultures`     | 否                                      | `<SupportedCultures/>`                                 | 透传                                                                                                                                                                                                                                                                                   |
| `instrument-info`        | 否                                      | `<InstrumentInfo/>`                                    | 网页：`GET /api/instrument/instrument-info`，解析字段与版本摘要                                                                                                                                                                                                                                   |
| `remote-control-state`   | 否                                      | `<RemoteControlState/>`                                | 内部：上游连接/重连后自动问询并解析，供 `/api/status` 与顶栏展示（无单独同名 REST）                                                                                                                                                                                                                                 |
| `logon`                  | 否（使用 `--user` / `--password`）          | `<Logon/>`                                             | 网关：凭据补全、首登转发、可选后续合成应答                                                                                                                                                                                                                                                                |
| `logoff`                 | 否                                      | `<Logoff/>`                                            | 网关：上游会话已由 Bridge 持有时合成成功应答（不转发仪器，避免误释放会话）                                                                                                                                                                                                                                          |
| `send`                   | 否                                      | 自定义 XML（自动注入 Cookie/Culture），`--xml` 必填                | 透传                                                                                                                                                                                                                                                                                   |
| `last-remote-added-sets` | 是                                      | RSL：最近远程添加的 set                                        | 透传                                                                                                                                                                                                                                                                                   |
| `add-samples`            | 是                                      | RSL：`<AddSamples>`；可 `--xml` 或省略走交互问答                  | 网关：默认截留队列、列表元数据解析；网页发送时调用上游并保留应答                                                                                                                                                                                                                                                     |
| `ambients`               | 是                                      | Remote Query：所有 ambient 摘要                             | 网页：`GET /api/environment/ambients`，解析为卡片列表                                                                                                                                                                                                                                           |
| `ambient`                | 是                                      | Remote Query：单个 ambient，`--key`                        | 透传                                                                                                                                                                                                                                                                                   |
| `automation-status`      | 是                                      | `--id` 可选                                              | 透传                                                                                                                                                                                                                                                                                   |
| `available-logs`         | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `counter`                | 是                                      | `--key`                                                | 网页：`GET /api/instrument/counter?key=`，解析单条 `<Counter/>`（维护计数器详情）                                                                                                                                                                                                                     |
| `counters`               | 是                                      |                                                        | 网页：`GET /api/instrument/counters`，请求并解析 `<Counters/>`（维护计数器）                                                                                                                                                                                                                         |
| `detectors`              | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `double-value`           | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `double-values`          | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `exception-directory`    | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `field`                  | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `fields`                 | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `filters`                | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `gas-state`              | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `log-data`               | 是                                      | `--log` / `--start` / `--end` / `--max-entries`，可省略走问答 | 透传                                                                                                                                                                                                                                                                                   |
| `log-directory`          | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `message-history`        | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `method`                 | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `methods`                | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `mondo-data`             | 是                                      | `--pic-id` 等，可省略走问答                                    | 透传                                                                                                                                                                                                                                                                                   |
| `mondo-directory`        | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `next-to-analyze`        | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `prerequisite`           | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `prerequisites`          | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `qc-status`              | 是                                      | `--method-key`                                         | 透传                                                                                                                                                                                                                                                                                   |
| `rep-detail`             | 是                                      | `--set-key`、`--tag`                                    | 网页：`GET /api/instrument/rep-detail`，解析详情字段                                                                                                                                                                                                                                           |
| `rep-plot`               | 是                                      | `--set-key`、`--tag`                                    | 网页：`GET /api/instrument/rep-plot`，解析 `analytePlotSeries` / 旧版序列 / 内嵌图；**分析页**用 ECharts 绘制各元素谱图                                                                                                                                                                                       |
| `report`                 | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `reports`                | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `sequence`               | 是                                      | `--name`                                               | 透传                                                                                                                                                                                                                                                                                   |
| `sequences`              | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `set`                    | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `set-keys-ex2`           | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `set-reps`               | 是                                      | `--key` / `--include-detail-data` / `--tag`，可省略走问答     | 网页：`GET /api/instrument/set-reps`，解析 replicates 等                                                                                                                                                                                                                                    |
| `sets`                   | 是                                      | `--filter-key` / `--number` / `--start-at`，可省略走问答      | 网页：`GET /api/instrument/sets`，解析列表与分页窗口                                                                                                                                                                                                                                              |
| `solenoid`               | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `solenoids`              | 是                                      |                                                        | 网页（部分）：`GET /api/diagnostic/digital-io` 会请求并解析 `<Solenoids/>`（数字输出）                                                                                                                                                                                                                  |
| `standard`               | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `standards`              | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `status`                 | 是                                      | gauges / system-check / leak-check 等布尔项，可省略走问答         | 网页（部分）：`GET /api/instrument/status-widgets` 发送 `Status`（`IncludeGauges=true`，不含系统检查/漏气结果）解析 Widgets；`GET /api/diagnostic/status-check` 发送 `Status`（`IncludeGauges=false`、`IncludeSystemCheckResults=true`、`IncludeLeakCheckResults=true`）解析 Elements/Odometers/SystemCheck/LeakCheck |
| `string-value`           | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `string-values`          | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `switch`                 | 是                                      | `--key`                                                | 透传                                                                                                                                                                                                                                                                                   |
| `switches`               | 是                                      |                                                        | 网页（部分）：`GET /api/diagnostic/digital-io` 会请求并解析 `<Switches/>`（数字输入）                                                                                                                                                                                                                   |
| `system-parameters`      | 是                                      |                                                        | 透传                                                                                                                                                                                                                                                                                   |
| `transport`              | 是                                      | `--key`                                                | 网页：`GET /api/settings/transport?key=`，解析单条 `<Transport/>`（含 SetBeginFields 等分区）                                                                                                                                                                                                      |
| `transports`             | 是                                      |                                                        | 网页：`GET /api/settings/transports`，解析 `<Transports/>` 列表                                                                                                                                                                                                                              |
| `valve-states`           | 是                                      |                                                        | 网页（部分）：`GET /api/diagnostic/digital-io` 会附带请求 `<ValveStates/>`，用于显示当前 `Active=true` 阀门                                                                                                                                                                                               |


**Bridge 集成列说明**

- **透传**：TCP 客户端经 Bridge 网关按 `Cookie` 转发上游，Bridge **未**对该命令的应答做专用结构化解析；与直连仪器行为一致，网页亦无对应该子命令的 REST。
- **网页**：`GatewayHub.instrument_rq`（`hub.py`）下发与 CLI 同族的 XML，`parsers.py` 解析为 JSON，由 `http_api.py` 暴露 `GET /api/...`；Web 仅代理，不重复解析。
- **网页（部分）**：与 CLI 子命令同名但 **参数/语义仅为子集**（表中已注明差异）。
- **网关**：TCP 路径上的路由、队列、合成应答（`Logon`/`Logoff`/`AddSamples`）、凭据补全等；`add-samples` 另含队列条目的展示用元数据提取。
- **内部**：无与 CLI 子命令一一对应的公开 REST，但 Bridge 在进程内主动下发并解析该 XML，用于状态展示等。

### `http` 子命令一览

`cornerstone-cli http <子命令> --server … --user … --password … --labname … --labkey …`


| 子命令           | 说明                                                   |
| ------------- | ---------------------------------------------------- |
| `instruments` | RegisteredInstruments.aspx                           |
| `request`     | RequestData.aspx；需 `--instrument-id`、`--command-xml` |


快速查看帮助：

```bash
cornerstone-cli --help
cornerstone-cli tcp --help
cornerstone-cli http --help
```

## cornerstone-cli：TCP 示例

发送 `Version` 命令：

```bash
cornerstone-cli tcp version --host 127.0.0.1 --port 12345
```

发送自定义 XML（会自动注入 `Cookie` 与 `Culture` 属性）：

```bash
cornerstone-cli tcp send --host 127.0.0.1 --port 12345 --xml "<InstrumentInfo/>"
```

切换编码（与原客户端一致：utf16/utf8/ascii）：

```bash
cornerstone-cli tcp version --host 127.0.0.1 --port 12345 --encoding utf16
```

## 长连接与心跳（`tcp session`）

保持 TCP 长连接，按间隔发送 `<Heartbeat/>` 保活，并可选择在“空闲超时”未收到任何数据时自动断开（用于检测连接是否存活）。按 Ctrl+C 退出。

```bash
# 每 5 秒发一次心跳，不检测空闲超时
cornerstone-cli tcp session --host 127.0.0.1 --port 12345 --heartbeat 5

# 每 5 秒心跳，超过 15 秒未收到任何数据则断开
cornerstone-cli tcp session --host 127.0.0.1 --port 12345 --heartbeat 5 --heartbeat-idle-timeout 15

# 不发送心跳，仅保持连接直到 Ctrl+C（--heartbeat 0）
cornerstone-cli tcp session --host 127.0.0.1 --port 12345 --heartbeat 0
```

协议与 C# `CommunicationEngine` 一致：心跳命令为 `<Heartbeat/>`，不等待响应（仅保活与检测连接）。

### 长连接建立后如何继续发送命令

进入 `tcp session` 后会出现 `>`  提示符，可在同一连接上反复输入并发送命令：

- **命令名**（回车即发）：`version`、`supported-cultures`、`instrument-info`、`remote-control-state`、`logoff`；登录用 `logon 用户名 密码`（如 `logon demo demo`）。**Remote Sample Login / Remote Query**（如 `last-remote-added-sets`、`ambients`、`<AddSamples>...</AddSamples>`）需已认证：可先 `logon`，或启动 session 时加 `--username` / `--password` 在连接后自动 Logon。
- **原始 XML**：直接输入以 `<` 开头的整段 XML（如 `<Version/>`、`<InstrumentInfo/>`、`<AddSamples>...</AddSamples>`），回车发送；若为 RSL/RQ 相关 XML，同样需先完成登录。
- **退出**：输入空行或 `exit` / `quit` / `q`，或按 Ctrl+C。

会话中会自动忽略心跳（`<Heartbeat/>`）的响应，不再打印，避免打断 `>`  输入；其他异步消息仍以 `[消息] ...` 显示。

示例（先启动 Bridge / `cornerstone-web-dev`，再在另一终端执行）：

```bash
cornerstone-cli tcp session --host 127.0.0.1 --port 12345 --heartbeat 5
# 出现 > 后输入：
# version
# instrument-info
# <RemoteControlState/>
# exit
```

## cornerstone-bridge（TCP 网关 + REST）

`cornerstone-bridge` 承担原 Mock 中的 **网关与会话** 职责：多台 TCP 客户端 → **单条**上游 Cornerstone 连接；非 `AddSamples` 按 `Cookie` 回路由；`AddSamples` 默认截留队列；`instrument_rq` + `parsers.py` 提供全部 `/api/`*。

- **多用户登录 / 登出**：首条 `Logon` 转发上游；成功后后续客户端 `Logon` / `Logoff` 可合成成功应答（`no_synthetic_logon: false`，默认），不占用仪器会话。
- **凭据补全**：配置 `web_user` / `web_password` 后，TCP 客户端空 `<Logon>` 由网关补全；网页发令与仪器 API 共用该上游登录。
- **AddSamples**：默认入 FIFO 队列；`privileged_add_samples_host` 匹配来源 IP 时直通上游。
- **RemoteControlState**：上游连接/重连后自动问询，供 `/api/status` 与 Web 顶栏。
- **上游报文解包**：外层 TCP 长度帧正文进入全局 `recv` 缓冲，按 `[inner_len][UTF-16 XML]` 循环切分（粘包/拆包）；无 inner 头的整段 XML（如部分 Logon 应答）按单条处理。`upstream_inner_reassembly_timeout` 控制拆包续读；`upstream_recv_idle_clear` 控制断流清缓冲。
- **业务在线**：`GET /api/status` 返回 `instrumentOnline`、`businessOnline`、`heartbeatFailStreak`、`commandFailStreak`；Web 顶栏与 Queue 状态行据此显示（不再仅依赖 Cookie 心跳应答）。
- **上游回收**：连续 `upstream_heartbeat_fail_max` 次心跳无应答，或连续 `upstream_command_fail_max` 次指令失败，或过久无上行活动 → 自动断开并重连上游；回收后强制重连（含僵死 TCP）、定时 activity 巡检、转发超时代际失效。
- **监听**：TCP `host`/`port`；REST `bridge_api_host`/`bridge_api_port`（示例 8081）。
- **配置写回**：以 `-c` 指定配置文件时，`PUT /api/settings` 可合并写回文件；改 TCP/Web **监听端口** 须重启进程。

仅起 Bridge（无浏览器 UI）：

```bash
cornerstone-bridge -c CornerstoneBridge/cornerstone-bridge.config.toml
```

CLI 经网关访问仪器：

```bash
cornerstone-cli tcp version --host 127.0.0.1 --port 54321
cornerstone-cli tcp logon --host 127.0.0.1 --port 54321 --user demo --password demo
```

定时向各 TCP 客户端推送 `<CornerstoneMessage/>`（`async_message_interval`，秒）。

## cornerstone-web（静态 UI + API 代理）

- **静态资源**：`CornerstoneWeb/src/cornerstone_web/web_static/`（`index.html`、`app.js`、`styles.css`、`echarts.min.js`；`/static/`*、`/`）。
- **API**：浏览器请求 `/api/`* 由 `http_server.py` **原样代理**到 Bridge（`bridge_api_port`），Web 进程内无 `GatewayHub`。
- **启动**：见上文 [启用 Web](#启用-web)；`cornerstone-web-dev` 或 `python -m cornerstone_web.dev_web`（与 Bridge 共用配置字段，含 `upstream_inner_reassembly_timeout` 等）。
- **分析页**：Set 列表 / Replicate 表格；选中 Set 后展示各元素均值±1σ、n、RSD%；选中 Replicate 后拉 `rep-plot` / `rep-detail`，各元素卡片内以 **ECharts** 绘制 RepPlot 时间–强度曲线（可点「详情」切换 RepDetail 小卡片）。
- **其它页**（诊断/设置/仪器等）：与拆分前一致；REST 清单见 Bridge `http_api.py`。`/legacy` 旧版队列页已重定向到 `/`。

**主要 REST**（均由 Bridge 提供，Web 代理）：`GET /api/queue`、`POST /api/queue/send`、`GET /api/status`、`GET|PUT /api/settings`、`GET /api/config`、`GET /api/environment/ambients`、`GET /api/diagnostic/`*、`GET /api/instrument/`*、`GET /api/settings/transports` 等。

## CornerstoneQueue（缓存样品悬浮窗）

独立 **WinUI 3** 程序（`CornerstoneQueue/CornerstoneQueue.sln`），仅通过 HTTP 消费 Bridge API，不持有 TCP 网关。详细阶段见 [PLAN.md §1](PLAN.md#1-缓存样品指令悬浮窗独立程序)。

### 已实现（M1–M3 + 仪器 UI 自动点击）


| 能力         | 说明                                                                                                         |
| ---------- | ---------------------------------------------------------------------------------------------------------- |
| 队列只读       | `GET /api/queue`，自动/手动刷新；数据未变时不重绘列表（避免闪烁）                                                                  |
| 发送至仪器      | 多选 + `POST /api/queue/send`；底部单行结果摘要                                                                       |
| 状态一行       | `GET /api/status`（`businessOnline`、失败计数、队列/RCS；未配置 web 账号见 `/api/config`）                                  |
| 精简 UI      | 顶栏状态一行、试样一行（`样品名 → 说明`）、底栏结果一行；默认小窗                                                                        |
| 设置（M3）     | Bridge URL、状态/队列轮询间隔、置顶、透明度、字号/窗体缩放、断线重连                                                                   |
| 贴边收纳       | 拖至屏幕上/左/右边缘可滑出隐藏或显示细条唤回（`EdgeDockController`）                                                              |
| 仪器 UI 自动点击 | 发送成功后可选：FlaUI 点击 Cornerstone「消息」→「添加试样」（设置中开关、AutomationId、延时；**Inspect 检查控件** / **测试点击**）。默认关闭，需按本机仪器版本校准 |


用户配置保存在 `%LocalAppData%\CornerstoneQueue\settings.json`（与 Bridge 的 `cornerstone-bridge.config.toml` 无关）。相关字段：`autoClickInstrumentUi`、`instrumentWindowTitleContains`、`notificationButtonAutomationId`、`addSampleButtonAutomationId`、`uiClickDelay`* 等。

**不在范围内（已取消）**：Windows 系统通知（发送失败/队列满 Toast）、全局快捷键唤起悬浮窗。

### 构建与运行

需要 **Visual Studio 2026**（或 2022）+ **.NET 8 SDK** + **Windows App SDK 1.6**（本机可 `winget install Microsoft.WindowsAppRuntime.1.6`）。工程已启用 `WindowsAppSDKSelfContained`，请从生成输出目录运行 exe，或在 VS 中 **F5**。

**VS Release 调试**：若提示“符号未加载”属正常（Release 优化 +“仅我的代码”）。若进程退出 `0xC000027B`，多为工作目录不是输出目录或 XAML 启动异常；工程已设置 `LocalDebuggerWorkingDirectory=$(TargetDir)`。请取消“仅我的代码”或改用 Debug 配置调试；崩溃详情见 `%LocalAppData%\CornerstoneQueue\startup-crash.log`。

```text
CornerstoneQueue\CornerstoneQueue\bin\x64\Debug\net8.0-windows10.0.19041.0\win-x64\CornerstoneQueue.exe
```

联调前须先启动 Bridge（`bridge_api_port`，默认 `8081`），例如：

```bash
cornerstone-bridge -c CornerstoneBridge/cornerstone-bridge.config.toml
```

悬浮窗默认连 `http://127.0.0.1:8081`，可在「设置」中修改。

## Remote Sample Login Commands（远程样品登录命令）

需仪器支持 RSL（Remote Sample Login）。**单次命令**下，CLI 会先发送 `<Logon/>`（使用 `--username` / `--password`），仅当应答为 `<Logon ErrorCode="0" ErrorMessage="Success"/>`（或等价属性）时才继续发送后续 RSL 命令。

### LastRemoteAddedSets

获取最近 RSL 添加的 set 的 key（无参数，但必须带登录参数）：

```bash
cornerstone-cli tcp last-remote-added-sets --host 127.0.0.1 --port 12345 --username demo --password demo
```

长连接 `tcp session` 中：可先 `logon 用户名 密码`，或启动时传入 `--username` / `--password` 自动登录后再输入 `last-remote-added-sets`。

### AddSamples

向现有 set 或新 set 添加 replicates，需提供完整 `<AddSamples>` XML：

```bash
cornerstone-cli tcp add-samples --host 127.0.0.1 --port 12345 --username demo --password demo --xml "<AddSamples><Set><Field Id=\"SampleType\">Sample</Field><Field Id=\"Name\">MySet</Field><Field Id=\"MethodKey\">0</Field></Set><Replicates><Replicate><Field Id=\"Mass\">1.0</Field><Field Id=\"Comments\"></Field></Replicate></Replicates></AddSamples>"
```

PowerShell 请用**单引号**包裹整段 XML（不要用 `\"` 转义）：

```powershell
# 最稳妥：从文件读取（避免 PowerShell 剥掉参数里的双引号）
cornerstone-cli tcp add-samples --host 127.0.0.1 --port 54321 --username remote --password control --xml-file add.xml
```

或把属性改成**单引号**：

```powershell
cornerstone-cli tcp add-samples --host 127.0.0.1 --port 54321 --username remote --password control --xml "<AddSamples><Set><Field Id='SampleType'>Sample</Field><Field Id='Name'>MySet</Field><Field Id='MethodKey'>0</Field></Set><Replicates><Replicate><Field Id='Mass'>1</Field></Replicate></Replicates></AddSamples>"
```

**添加到已有 set**：根节点下直接写 `<SetKey>...</SetKey>`，并包含 `<Replicates>...</Replicates>`。  
**新 set**：根节点下为 `<Set>`，内含若干 `<Field Id=\"...\">value</Field>`（如 SampleType、Name、Description、MethodKey、StandardKey），再加 `<Replicates>`。  
每个 `<Replicate>` 内为 `<Field Id=\"Mass\">`、`<Field Id=\"Comments\">`、`<Field Id=\"Location\">`（可选）。  

长连接 session 中可直接粘贴整段 `<AddSamples>...</AddSamples>` XML 发送。

#### 交互式填写 AddSamples（推荐给不熟悉 XML 的用户）

- **单次命令**：`--xml` 可省略，CLI 会通过问答方式帮你生成 AddSamples XML（须同时提供 `--username` / `--password`）：  
  ```bash
  cornerstone-cli tcp add-samples --host 127.0.0.1 --port 12345 --username demo --password demo
  ```
  - 首先询问：`Add Replicates to an [e]xisting Set or Add Replicates to a [n]ew Set? (e/n)`  
    - 选 `e`（existing）：依次输入 `SetKey`、`Replicates` 数量、每个 replicate 的 `Mass`、`Comments`、`Location`，自动生成：  
    `<AddSamples><SetKey>...</SetKey><Replicates>...</Replicates></AddSamples>`。
    - 选 `n`（new）：依次输入 `SampleType`（Blank/GasDose/Sample/Standard）、`Name`（仅对 Blank/Sample 有效）、`Description`、`MethodKey`、`StandardKey`（仅对 GasDose/Standard 必填）、`Replicates` 数量以及每个 replicate 的 `Mass`、`Comments`、`Location`，自动生成：  
    `<AddSamples><Set>...</Set><Replicates>...</Replicates></AddSamples>`。
- **长连接 session**：在 `tcp session` 中直接输入命令名 `add-samples`（不带 XML），也会进入同样的问答流程；如仍然希望手写 XML，也可以直接粘贴完整 `<AddSamples>...</AddSamples>`。

## Remote Query Commands（远程查询命令）

与 RSL 相同：**单次命令**须带 `--username` / `--password`，CLI 会先 Logon 并校验成功后再发送 Remote Query XML。具体子命令名称见上文 **「cornerstone-cli 全部命令」** 表格。

### Ambients

检索仪器上所有 ambient 的一般信息（无业务参数，须带登录参数）：

```bash
cornerstone-cli tcp ambients --host 127.0.0.1 --port 12345 --username demo --password demo
```

在 `tcp session` 中可直接输入：`ambients`。

### Ambient

根据指定 Key 检索单个 ambient 的详细信息：

```bash
cornerstone-cli tcp ambient --host 127.0.0.1 --port 12345 --username demo --password demo --key 1
```

在 `tcp session` 中可直接输入：`ambient 1`（其中 `1` 为 ambient 的 Key，前导 0 可省略）。

### 可交互提示参数的命令

以下命令在单次命令与 `tcp session` 中均支持“只输入命令名 → 逐行提示参数”（详见 `--help`）：

- `log-data` — 典型：`cornerstone-cli tcp log-data --host 127.0.0.1 --port 12345 --log Main --max-entries 1000`，或省略业务参数进入问答。
- `mondo-data` — 典型：`--pic-id`、`--max-entries` 等，或问答。
- `set-reps` — 典型：`--key`、`--include-detail-data`、`--tag`，或问答。
- `sets` — 典型：`--filter-key`、`--number`、`--start-at`，或问答。
- `status` — 典型：`--include-gauges`、`--include-system-check-results`、`--include-leak-check-results`，或问答。

在 `tcp session` 中，无参/带参 Remote Query 的输入习惯与原文档一致：例如 `counter KEY`、`sets` 后按提示输入等。

## HTTP 示例

获取已注册仪器列表：

```bash
cornerstone-cli http instruments --server remote.lecosoftware.com --user "u" --password "p" --labname "lab" --labkey "key"
```

## 协议要点（与 C# 版对齐）

- **发送**：`len = int32_le(payload_bytes_len)`，先写 4 字节长度，再写 payload bytes
- **接收**：同样按长度前缀读满一帧，再按编码解码为字符串
- **Cookie/Culture**：若发送内容是 XML，会在根节点上设置 `Cookie` 与 `Culture`

