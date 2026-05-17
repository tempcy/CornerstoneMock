# Cornerstone 远程控制（Python CLI + Bridge + Web）

后续开发路线图见 [PLAN.md](PLAN.md)。

本仓库为 **Python CLI（`CornerstoneCLI`）**、**Bridge（`CornerstoneBridge`）** 与 **Web UI（`CornerstoneWeb` 目录，包名 `cornerstone-web`）**。原始 C# WPF 客户端请放在本地 `**Cornerstone_RemoteControlClient/`** 目录自行对照（该目录已列入 `.gitignore`，**不会**推送到 Git）。

- `**CornerstoneCLI/`**：`cornerstone-cli`（协议与 TCP 通信内核）。
- `**CornerstoneBridge/`**：`cornerstone-bridge`（TCP 网关、XML 解析、`/api/*` REST）。
- `**CornerstoneWeb/**`：`cornerstone-web`（静态页 + 将 `/api/*` 代理到 Bridge）；`cornerstone-web-dev` 一键起 Bridge + Web。

下文所述 **Python 版通信内核** 与配套工具位于上述三个子目录中，用于替代/复用原客户端中的核心通信逻辑：

- **TCP 通道**：4 字节小端长度前缀 + payload（默认 UTF-16LE，对应 C# `Encoding.Unicode`）
- **HTTP 通道**：向云端 `https://remote.lecosoftware.com/...` 发送 `text/xml` POST（对应 C# `WebRequestor`）

> 说明：当前版本聚焦“通信层可用、协议一致、可跑通基本命令”。WPF UI/ViewModel 迁移会在此基础上继续推进。

## CLI / Bridge / Web 概览


| 工具 | 入口命令 | 作用 |
| --- | --- | --- |
| **CLI** | `cornerstone-cli` | 直连仪器 TCP 或云端 HTTP；脚本与协议调试。 |
| **Bridge** | `cornerstone-bridge` | TCP 网关 + `/api/*` REST（队列、instrument_rq、解析 JSON）。 |
| **Web** | `cornerstone-web` | 静态 SPA；`/api/*` 反向代理到 Bridge。 |
| **本地开发** | `cornerstone-web-dev` / `dev.ps1` | 同进程启动 Bridge + Web（读 `cornerstone-web.config.json`）。 |

`cornerstone-mock` / `cornerstone-mock-dev` 仍可用，内部转调 `cornerstone-web-dev`（**已弃用**，仅兼容旧脚本）。

**典型组合**：`cornerstone-web-dev` 或分开起 Bridge + Web；TCP 客户端与 `cornerstone-cli tcp …` 的 `--host/--port` 指向配置中的 **`host`/`port`**（网关端口，非 `web_port`）。

### 运行时架构（当前）

```
浏览器 ──► cornerstone-web :8080  (/ 静态页, /api/* 代理)
                    │
                    ▼
            cornerstone-bridge :8081  (/api/* REST)
                    ├── TCP 网关 :54321  ◄── 远程客户端 / CLI
                    └── 上游 TCP ──► Cornerstone 仪器
```

配置文件：`CornerstoneWeb/cornerstone-web.config.example.json`（复制为 `cornerstone-web.config.json` 后修改）。关键字段：`upstream_*`（仪器）、`host`/`port`（TCP 网关）、`bridge_api_*`（REST）、`web_*`（浏览器）、`web_user`/`web_password`（网页发令账号）。

## 安装

需要 Python 3.8+。在仓库根目录依次安装（Bridge/Web 依赖 `cornerstone-cli`）：

```bash
python -m pip install -e ./CornerstoneCLI
python -m pip install -e ./CornerstoneBridge
python -m pip install -e ./CornerstoneWeb
```

仅需命令行时，可只安装 `CornerstoneCLI`。

## 启用 Web

Web 界面由 **Bridge**（网关 + `/api/*`）与 **Web**（静态页 + API 代理）两个进程组成。日常开发推荐一键启动；生产或分网部署时可分开起。

### 1. 准备配置

复制示例配置并按本机环境修改（至少核对 **上游仪器** 与 **网页登录账号**）：

```bash
cd CornerstoneWeb
copy cornerstone-web.config.example.json cornerstone-web.config.json
```

| 配置项 | 含义 |
| --- | --- |
| `upstream_host` / `upstream_port` | 真实 Cornerstone 仪器 TCP 地址 |
| `host` / `port` | 网关对 **TCP 客户端**（含 C# 远程客户端）的监听 |
| `bridge_api_host` / `bridge_api_port` | Bridge 对内 REST（默认 `8081`） |
| `web_host` / `web_port` | 浏览器访问的 Web 地址（默认 `http://127.0.0.1:8080/`） |
| `web_user` / `web_password` | 网页「发送到仪器」、环境/分析页拉数用的仪器远程账号 |

也可将 `cornerstone-web.config.json` 放在**仓库根目录**或任意工作目录；或通过环境变量 `CORNERSTONE_WEB_CONFIG` 指定绝对路径。

### 2. 启动（推荐：开发一键）

在 **`CornerstoneWeb`** 目录（或已放置 `cornerstone-web.config.json` 的目录）下执行：

```bash
python -m cornerstone_web.dev_web
```

若已把 Python 的 **Scripts** 目录加入系统 PATH，也可直接：

```bash
cornerstone-web-dev
```

Windows 下 Scripts 常见路径（`pip install` 后若提示 *not on PATH*，即为此目录）：

`%APPDATA%\Python\Python311\Scripts`

可临时加入当前 PowerShell 会话后再用短命令：

```powershell
$env:Path += ";$env:APPDATA\Python\Python311\Scripts"
cornerstone-web-dev
```

或在 `CornerstoneWeb` 目录执行仓库提供的脚本：

```powershell
.\dev.ps1
```

启动成功后：

- 浏览器打开 **`http://127.0.0.1:8080/`**（与配置中 `web_host` / `web_port` 一致）
- TCP 客户端连 **`host:port`**（示例为 `54321`）
- Bridge REST 在 **`http://127.0.0.1:8081/`**（供悬浮窗、脚本等直连；Web 会把 `/api/*` 代理到此端口）

### 3. 启动（分开：Bridge + Web）

需要先起 Bridge，再起 Web（Web 依赖 Bridge 的 REST）：

```bash
# 终端 1：网关 + API
cornerstone-bridge -c CornerstoneWeb/cornerstone-web.config.json

# 终端 2：静态页 + /api 代理
cornerstone-web -c CornerstoneWeb/cornerstone-web.config.json
```

命令行覆盖示例（仅改网页端口）：

```bash
cornerstone-web-dev --web-port 9000
```

### 4. 使用前检查

1. **`web_user` / `web_password` 已填写**：否则队列「发送至仪器」、环境/分析等页会失败。
2. **端口未被占用**：`web_port`（8080）、`bridge_api_port`（8081）、`port`（TCP 网关）互不冲突。
3. **能连上上游**：Bridge 控制台应出现 upstream 连接/Logon 相关日志；网页顶栏可查看连接与 `RemoteControlState`。
4. **仅内网使用**：当前 TCP/HTTP 未做鉴权，勿暴露到公网。

### 5. 常见问题

| 现象 | 处理 |
| --- | --- |
| `cornerstone-web-dev` 无法识别 | 先 `pip install -e ./CornerstoneWeb`，或改用 `python -m cornerstone_web.dev_web`；见上文 PATH |
| 页面能开但 `/api/*` 502 | 确认 Bridge 已启动且 `bridge_api_port` 与配置一致 |
| 发送样品失败 | 检查 `web_user` / `web_password` 是否与仪器远程账号一致 |
| 改 `web_port` / `port` 不生效 | 修改监听端口后需**重启**对应进程（`cornerstone-web-dev` 或 Bridge/Web） |
| 仍使用旧命令 | `cornerstone-mock-dev` 会转调 `cornerstone-web-dev`，建议改用新入口 |

## cornerstone-cli 全部命令

以下为安装后 `cornerstone-cli` 的完整子命令树。除另有说明外，**TCP 子命令**均形如：

`cornerstone-cli tcp <子命令> --host <地址> --port <端口> [其它选项]`

公共 TCP 选项（多数子命令可用）：`--culture`、`--encoding`（utf16/utf8/ascii）、`--timeout`。

### `tcp` 子命令一览

第四列表示 **`cornerstone-bridge` 是否除 TCP 透传外，还集成了与该子命令等价的 XML 调用及应答的结构化解读**（经 Bridge REST 供 Web `/api/...`、队列 UI 或网关内部状态）。图例见表下说明。


| 子命令                      | 需 `--username` / `--password`（先 Logon） | 说明摘要                                                   | Bridge 集成（调用+解读）                                                                                                                                                                                                                                                                       |
| ------------------------ | -------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `session`                | 可选（可自动 Logon）                          | 长连接 + 可选心跳与空闲超时；交互式 `>` 提示符                            | 透传                                                                                                                                                                                                                                                                                   |
| `version`                | 否                                      | `<Version/>`                                           | 透传                                                                                                                                                                                                                                                                                   |
| `supported-cultures`     | 否                                      | `<SupportedCultures/>`                                 | 透传                                                                                                                                                                                                                                                                                   |
| `instrument-info`        | 否                                      | `<InstrumentInfo/>`                                    | 网页：`GET /api/instrument/instrument-info`，解析字段与版本摘要                                                                                                                                                                                                                                   |
| `remote-control-state`   | 否                                      | `<RemoteControlState/>`                                | 内部：上游连接/重连后自动问询并解析，供 `/api/status` 与顶栏展示（无单独同名 REST）                                                                                                                                                                                                                                 |
| `logon`                  | 否（使用 `--user` / `--password`）          | `<Logon/>`                                             | 网关：凭据补全、首登转发、可选后续合成应答                                                                                                                                                                                                                                                                |
| `logoff`                 | 否                                      | `<Logoff/>`                                            | 透传                                                                                                                                                                                                                                                                                   |
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
| `rep-plot`               | 是                                      | `--set-key`、`--tag`                                    | 网页：`GET /api/instrument/rep-plot`，解析谱图/序列与内嵌图                                                                                                                                                                                                                                        |
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
- **网关**：TCP 路径上的路由、队列、合成应答、凭据补全等；`add-samples` 另含队列条目的展示用元数据提取。
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

`cornerstone-bridge` 承担原 Mock 中的 **网关与会话** 职责：多台 TCP 客户端 → **单条**上游 Cornerstone 连接；非 `AddSamples` 按 `Cookie` 回路由；`AddSamples` 默认截留队列；`instrument_rq` + `parsers.py` 提供全部 `/api/*`。

- **多用户登录**：首条 `Logon` 转发上游；成功后后续客户端 `Logon` 可合成成功（`no_synthetic_logon: false`，默认）。
- **凭据补全**：配置 `web_user` / `web_password` 后，TCP 客户端空 `<Logon>` 由网关补全；网页发令与仪器 API 共用该上游登录。
- **AddSamples**：默认入 FIFO 队列；`privileged_add_samples_host` 匹配来源 IP 时直通上游。
- **RemoteControlState**：上游连接/重连后自动问询，供 `/api/status` 与 Web 顶栏。
- **监听**：TCP `host`/`port`；REST `bridge_api_host`/`bridge_api_port`（示例 8081）。
- **配置写回**：以 `-c` 指定 JSON 时，`PUT /api/settings` 可合并写回文件；改 TCP/Web **监听端口** 须重启进程。

仅起 Bridge（无浏览器 UI）：

```bash
cornerstone-bridge -c CornerstoneWeb/cornerstone-web.config.json
```

CLI 经网关访问仪器：

```bash
cornerstone-cli tcp version --host 127.0.0.1 --port 54321
cornerstone-cli tcp logon --host 127.0.0.1 --port 54321 --user demo --password demo
```

定时向各 TCP 客户端推送 `<CornerstoneMessage/>`（`async_message_interval`，秒）。

## cornerstone-web（静态 UI + API 代理）

- **静态资源**：`CornerstoneWeb/src/cornerstone_web/web_static/`（`/static/*`、`/`）。
- **API**：浏览器请求 `/api/*` 由 `http_server.py` **原样代理**到 Bridge（`bridge_api_port`），Web 进程内无 `GatewayHub`。
- **启动**：见上文 [启用 Web](#启用-web)；`cornerstone-web-dev` 或 `python -m cornerstone_web.dev_web`。
- **网页功能**（与分析/诊断/设置/仪器各页）：与拆分前一致；REST 清单见 Bridge `http_api.py`。`/legacy` 旧版队列页已重定向到 `/`。

**主要 REST**（均由 Bridge 提供，Web 代理）：`GET /api/queue`、`POST /api/queue/send`、`GET /api/status`、`GET|PUT /api/settings`、`GET /api/config`、`GET /api/environment/ambients`、`GET /api/diagnostic/*`、`GET /api/instrument/*`、`GET /api/settings/transports` 等。

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

