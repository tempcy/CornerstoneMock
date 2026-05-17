# Cornerstone 远程控制（Python CLI + Mock 网关）

本仓库为 **Python CLI（`CornerstoneCLI`）** 与 **Mock 网关（`CornerstoneMock`）**。原始 C# WPF 客户端请放在本地 **`Cornerstone_RemoteControlClient/`** 目录自行对照（该目录已列入 `.gitignore`，**不会**推送到 Git）。

- **`CornerstoneCLI/`**：Python 分发 **`cornerstone-cli`**（导入用模块 `cornerstone_cli`，命令行入口 **`cornerstone-cli`**）。
- **`CornerstoneMock/`**：Python 包 **`cornerstone-mock`**（TCP 网关、网页队列、`cornerstone-mock` / `cornerstone-mock-dev`），依赖 **`cornerstone-cli`**。

下文所述 **Python 版通信内核** 与配套工具位于上述两个子目录中，用于替代/复用原客户端中的核心通信逻辑：

- **TCP 通道**：4 字节小端长度前缀 + payload（默认 UTF-16LE，对应 C# `Encoding.Unicode`）
- **HTTP 通道**：向云端 `https://remote.lecosoftware.com/...` 发送 `text/xml` POST（对应 C# `WebRequestor`）

> 说明：当前版本聚焦“通信层可用、协议一致、可跑通基本命令”。WPF UI/ViewModel 迁移会在此基础上继续推进。

## CLI 与 Mock 概览

| 工具 | 入口命令 | 作用 |
|------|-----------|------|
| **CLI** | `cornerstone-cli` | 直连仪器 TCP 或走云端 HTTP：发协议 XML、长连接会话、Remote Query / RSL 等子命令，适合脚本、调试与协议验证。 |
| **Mock 网关** | `cornerstone-mock` | 多客户端共用的 **TCP 转发网关**：上游只连一台真实 Cornerstone；可截留 `AddSamples`、用网页管理队列、REST 与新版 Web UI；可选网页账号补全客户端 `<Logon>`。 |
| **Mock 本地开发** | `cornerstone-mock-dev` 或 `python -m cornerstone_mock.dev_mock_web` | 在仓库内加载示例配置启动网关+网页，便于不改安装参数做前端/联调。 |

**典型组合**：本机先起 **Mock**（客户端连网关端口），再起 **`cornerstone-cli tcp … --host/--port` 指向网关**；需要直连仪器时则把 host/port 改为仪器监听地址。不连仪器时也可用 `cornerstone-cli --help` / `tcp version` 等对 mock 做冒烟。

## 安装

需要 Python 3.8+。在仓库根目录 **先** 安装 `CornerstoneCLI`（提供 `cornerstone_cli` 包与 `cornerstone-cli`），**再** 安装 `CornerstoneMock`（提供 `cornerstone-mock` / `cornerstone-mock-dev`）。Mock 在运行时会 `import cornerstone_cli`，因此未安装 CLI 时单独装 Mock 会导入失败。

```bash
python -m pip install -e ./CornerstoneCLI
python -m pip install -e ./CornerstoneMock
```

仅需命令行、不跑网关时，可只执行第一行。

## cornerstone-cli 全部命令

以下为安装后 `cornerstone-cli` 的完整子命令树。除另有说明外，**TCP 子命令**均形如：

`cornerstone-cli tcp <子命令> --host <地址> --port <端口> [其它选项]`

公共 TCP 选项（多数子命令可用）：`--culture`、`--encoding`（utf16/utf8/ascii）、`--timeout`。

### `tcp` 子命令一览

第四列表示 **`cornerstone-mock` 是否除 TCP 透传外，还集成了与该子命令等价的 XML 调用及应答的结构化解读**（用于网页 `/api/...`、队列 UI 或网关内部状态）。图例见表下说明。

| 子命令 | 需 `--username` / `--password`（先 Logon） | 说明摘要 | Mock 集成（调用+解读） |
|--------|------------------------------------------|----------|------------------------|
| `session` | 可选（可自动 Logon） | 长连接 + 可选心跳与空闲超时；交互式 `>` 提示符 | 透传 |
| `version` | 否 | `<Version/>` | 透传 |
| `supported-cultures` | 否 | `<SupportedCultures/>` | 透传 |
| `instrument-info` | 否 | `<InstrumentInfo/>` | 网页：`GET /api/instrument/instrument-info`，解析字段与版本摘要 |
| `remote-control-state` | 否 | `<RemoteControlState/>` | 内部：上游连接/重连后自动问询并解析，供 `/api/status` 与顶栏展示（无单独同名 REST） |
| `logon` | 否（使用 `--user` / `--password`） | `<Logon/>` | 网关：凭据补全、首登转发、可选后续合成应答 |
| `logoff` | 否 | `<Logoff/>` | 透传 |
| `send` | 否 | 自定义 XML（自动注入 Cookie/Culture），`--xml` 必填 | 透传 |
| `last-remote-added-sets` | 是 | RSL：最近一次 `AddSamples` 创建的 set Key | 网页（组合）：`GET /api/instrument/remote-import-sets` **第 1 步** — 下发 `<LastRemoteAddedSets/>`，解析各 `<Set Key="…"/>` 为 `keys[]`；单独经 CLI/透传 TCP 时无结构化解读 |
| `add-samples` | 是 | RSL：`<AddSamples>`；可 `--xml` 或省略走交互问答 | 网关：TCP 默认截留 FIFO；`GET /api/queue` 解读队列项（`sampleName`/`sampleDescription` 等自 XML 提取）；`POST /api/queue/send` 转发选中条目的 AddSamples 至上游，**不从队列删除**（`queueKept: true`）；privileged 主机 TCP 可直通上游 |
| `ambients` | 是 | Remote Query：所有 ambient 摘要 | 网页：`GET /api/environment/ambients`，解析为卡片列表 |
| `ambient` | 是 | Remote Query：单个 ambient，`--key` | 透传 |
| `automation-status` | 是 | `--id` 可选 | 网页：`GET /api/instrument/automation-status`，解析 `<AutomationStatus/>`（AutoCleaner 等展示行） |
| `available-logs` | 是 | | 透传 |
| `counter` | 是 | `--key` | 网页：`GET /api/instrument/counter?key=`，解析单条 `<Counter/>`（维护计数器详情） |
| `counters` | 是 | | 网页：`GET /api/instrument/counters`，请求并解析 `<Counters/>`（维护计数器） |
| `detectors` | 是 | | 透传 |
| `double-value` | 是 | `--key` | 透传 |
| `double-values` | 是 | | 透传 |
| `exception-directory` | 是 | | 透传 |
| `field` | 是 | `--key` | 透传 |
| `fields` | 是 | | 透传 |
| `filters` | 是 | | 透传 |
| `gas-state` | 是 | | 透传 |
| `log-data` | 是 | `--log` / `--start` / `--end` / `--max-entries`，可省略走问答 | 透传 |
| `log-directory` | 是 | | 透传 |
| `message-history` | 是 | | 透传 |
| `method` | 是 | `--key` | 网页：`GET /api/settings/method?key=`，解析单条 `<Method/>`（含 `Sections` 树） |
| `methods` | 是 | | 网页：`GET /api/settings/methods`，解析 `<Methods/>` 列表 |
| `mondo-data` | 是 | `--pic-id` 等，可省略走问答 | 透传 |
| `mondo-directory` | 是 | | 透传 |
| `next-to-analyze` | 是 | | 透传 |
| `prerequisite` | 是 | `--key` | 透传 |
| `prerequisites` | 是 | | 透传 |
| `qc-status` | 是 | `--method-key` | 透传 |
| `rep-detail` | 是 | `--set-key`、`--tag` | 网页：`GET /api/instrument/rep-detail`，解析详情字段 |
| `rep-plot` | 是 | `--set-key`、`--tag` | 网页：`GET /api/instrument/rep-plot`，解析谱图/序列与内嵌图 |
| `report` | 是 | `--key` | 透传 |
| `reports` | 是 | | 透传 |
| `sequence` | 是 | `--name` | 透传 |
| `sequences` | 是 | | 透传 |
| `set` | 是 | `--key` | 透传（单条 set **详情**） |
| `set-keys-ex2` | 是 | | 透传（返回仪器上各 set 的 **Key** 与 **AnalysisDate**；C# Remote Control Client 中同名命令） |
| `set-reps` | 是 | `--key` / `--include-detail-data` / `--tag`，可省略走问答 | 网页：`GET /api/instrument/set-reps`，解析 replicates 等 |
| `sets` | 是 | `--filter-key` / `--number` / `--start-at`，可省略走问答 | 网页：`GET /api/instrument/sets`，解析列表与分页窗口 |
| `sets-ex` | 是 | `--key`（可多个），可省略走问答 | 网页（组合）：`GET /api/instrument/remote-import-sets` **第 2 步** — 下发 `<SetsEx><Set Key="…"/>…</SetsEx>`，解析为与 `sets` 同构的 `items[]`/`analyteDefs[]`（应答常为外层 `<Set>` 包内层含 `HeaderFields` 的 `<Set>`）；单独 CLI 为透传 |
| `solenoid` | 是 | `--key` | 透传 |
| `solenoids` | 是 | | 网页（部分）：`GET /api/diagnostic/digital-io` 会请求并解析 `<Solenoids/>`（数字输出） |
| `standard` | 是 | `--key` | 网页：`GET /api/settings/standard?key=`，解析单条 `<Standard/>`（含 `Analytes`） |
| `standards` | 是 | | 网页：`GET /api/settings/standards`，解析 `<Standards/>` 列表 |
| `status` | 是 | gauges / system-check / leak-check 等布尔项，可省略走问答 | 网页（部分）：`GET /api/instrument/status-widgets` 发送 `Status`（`IncludeGauges=true`，不含系统检查/漏气结果）解析 Widgets；`GET /api/diagnostic/status-check` 发送 `Status`（`IncludeGauges=false`、`IncludeSystemCheckResults=true`、`IncludeLeakCheckResults=true`）解析 Elements/Odometers/SystemCheck/LeakCheck |
| `string-value` | 是 | `--key` | 透传 |
| `string-values` | 是 | | 透传 |
| `switch` | 是 | `--key` | 透传 |
| `switches` | 是 | | 网页（部分）：`GET /api/diagnostic/digital-io` 会请求并解析 `<Switches/>`（数字输入） |
| `system-parameters` | 是 | | 网页：`GET /api/instrument/system-parameters`，解析 `<SystemParameters/>` 分区字段 |
| `transport` | 是 | `--key` | 网页：`GET /api/settings/transport?key=`，解析单条 `<Transport/>`（含 SetBeginFields 等分区） |
| `transports` | 是 | | 网页：`GET /api/settings/transports`，解析 `<Transports/>` 列表 |
| `valve-states` | 是 | | 网页（部分）：`GET /api/diagnostic/digital-io` 会附带请求 `<ValveStates/>`，用于显示当前 `Active=true` 阀门 |

**Mock 集成列说明**

- **透传**：TCP 客户端经网关按 `Cookie` 转发上游，mock **未**对该命令的应答做专用结构化解析；与直连仪器行为一致，网页亦无对应该子命令的 REST。
- **网页**：`GatewayHub.instrument_rq`（或专用 `forward_add_samples_web`）下发**与 CLI 同族**的 XML，经 `_parse_*` 转为 JSON，供 `GET /api/...` 与新版 Web UI 使用（实现见 `mock_server.py` 中 `fetch_*_json`）。
- **网页（部分）**：与 CLI 子命令同名但 **参数/语义仅为子集**（如 `status` 拆成 Widgets 与 status-check 两路）。
- **网页（组合）**：**一条 REST 串联多条 XML**（无单独 CLI 子命令），例如分析页「远程录入 Sets」= `LastRemoteAddedSets` + `SetsEx`。
- **网关**：TCP 路由、Logon 补全/合成、`AddSamples` 截留与队列；非 `AddSamples` 的 RQ/RSL 在配置 `--web-user`/`--web-password` 后也可由网页侧 `instrument_rq` 访问上游。
- **内部**：无与 CLI 子命令一一对应的公开 REST，但网关在进程内主动问询并解析（如上游重连后的 `RemoteControlState` → `/api/status`）。

**调用路径（网页 → 仪器）**

1. 浏览器请求 `GET/POST /api/...`（需网关已配置仪器账号）。
2. `mock_server` 调用 `fetch_*_json` → `instrument_rq`（默认复用网关**上游长连接** + 网页 Logon；`--instrument-short-connection` 时改为每次短 TCP + Logon，与 CLI 一致）。
3. 检查应答根节点 `ErrorCode`（非 `0` 则 JSON 中 `ok: false` + `error`）。
4. `_parse_*` 提取字段；失败时 `error` 含解析异常说明，部分接口带 `rawPreview`（应答 XML 前缀）。

**网页 REST 与解读对照**（第四列「网页」/「网页（组合）」的展开）

| REST | 下发 XML（与 CLI 对应） | 解读（JSON 要点） |
|------|-------------------------|-------------------|
| `GET /api/queue` | — | 截留队列：`items[]` 含 `id`、`sampleName`、`sampleDescription`、`peer`、`xml` 等 |
| `POST /api/queue/send` | 选中条目的 `<AddSamples>…` | `results[]` 各条 `upstreamResponse`；**保留队列**（`queueKept: true`） |
| `GET /api/instrument/sets` | `<Sets FilterKey Number StartAt/>` | `items[]`（setKey、name、method、analyteAvgs、state…）；`analyteDefs[]`；`pagination`（翻页） |
| `GET /api/instrument/remote-import-sets` | `<LastRemoteAddedSets/>` → `<SetsEx>…` | 同上 `items`/`analyteDefs`；`keys[]`；无分页；`source`: `LastRemoteAddedSets+SetsEx` |
| `GET /api/instrument/set-reps` | `<SetReps Key IncludeDetailData Tag/>` | `replicates[]`；`repAnalyteColumns[]`；`elementStats[]` |
| `GET /api/instrument/rep-plot` | `<RepPlot SetKey Tag/>` | 曲线 `analytePlotSeries` / `series` / 内嵌图 base64 |
| `GET /api/instrument/rep-detail` | `<RepDetail SetKey Tag/>` | Replicate 详情字段列表 |
| `GET /api/instrument/set-stats` | 内部复用 `SetReps`（含 detail） | 选中 Set 的元素统计聚合 |
| `GET /api/instrument/status-widgets` | `<Status IncludeGauges=true …/>` | 主界面仪表 `widgets[]` |
| `GET /api/diagnostic/status-check` | `<Status … IncludeSystemCheckResults=true …/>` | `elements`、`odometers`、`systemCheck`、`leakChecks` |
| `GET /api/environment/ambients` | `<Ambients/>` | 环境卡片列表 |
| `GET /api/diagnostic/digital-io` | `<Solenoids/>` + `<Switches/>` + `<ValveStates/>` | 数字 IO；`valveStateDisplay` / `valveStateError` |
| `GET /api/instrument/instrument-info` | `<InstrumentInfo/>` | 版本与字段摘要（标题栏 **i** 弹窗） |
| `GET /api/instrument/counters` | `<Counters/>` | 维护计数器列表 |
| `GET /api/instrument/counter?key=` | `<Counter Key="…"/>` | 单条计数器详情 |
| `GET /api/instrument/automation-status` | `<AutomationStatus/>` 或带 `Id` | AutoCleaner 等键值行 |
| `GET /api/instrument/system-parameters` | `<SystemParameters/>` | 分区 + 字段（布尔/数值） |
| `GET /api/settings/transports` | `<Transports/>` | 传送列表 |
| `GET /api/settings/transport?key=` | `<Transport Key="…"/>` | 单条传送（含 SetBeginFields 等） |
| `GET /api/settings/methods` | `<Methods/>` | 方法列表 |
| `GET /api/settings/method?key=` | `<Method Key="…"/>` | 方法详情（`Sections` 树） |
| `GET /api/settings/standards` | `<Standards/>` | 标样列表（名称、碳/硫、修改时间等） |
| `GET /api/settings/standard?key=` | `<Standard Key="…"/>` | 标样详情（`Analytes`） |
| `GET /api/status` | 缓存 + 可选 `RemoteControlState` | 连接、队列、`remoteControl` 展示文案 |

### `http` 子命令一览

`cornerstone-cli http <子命令> --server … --user … --password … --labname … --labkey …`

| 子命令 | 说明 |
|--------|------|
| `instruments` | RegisteredInstruments.aspx |
| `request` | RequestData.aspx；需 `--instrument-id`、`--command-xml` |

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

进入 `tcp session` 后会出现 `> ` 提示符，可在同一连接上反复输入并发送命令：

- **命令名**（回车即发）：`version`、`supported-cultures`、`instrument-info`、`remote-control-state`、`logoff`；登录用 `logon 用户名 密码`（如 `logon demo demo`）。**Remote Sample Login / Remote Query**（如 `last-remote-added-sets`、`ambients`、`<AddSamples>...</AddSamples>`）需已认证：可先 `logon`，或启动 session 时加 `--username` / `--password` 在连接后自动 Logon。
- **原始 XML**：直接输入以 `<` 开头的整段 XML（如 `<Version/>`、`<InstrumentInfo/>`、`<AddSamples>...</AddSamples>`），回车发送；若为 RSL/RQ 相关 XML，同样需先完成登录。
- **退出**：输入空行或 `exit` / `quit` / `q`，或按 Ctrl+C。

会话中会自动忽略心跳（`<Heartbeat/>`）的响应，不再打印，避免打断 `> ` 输入；其他异步消息仍以 `[消息] ...` 显示。

示例（先启动 mock，再在另一终端执行）：

```bash
cornerstone-cli tcp session --host 127.0.0.1 --port 12345 --heartbeat 5
# 出现 > 后输入：
# version
# instrument-info
# <RemoteControlState/>
# exit
```

## cornerstone-mock（TCP 网关 + AddSamples 网页队列）

`cornerstone-mock` 作为 **指令转发网关**：多台客户端连接网关；网关用 **一条** TCP 连接转发到真实 Cornerstone（单机通常只接受一个登录会话）。非 `AddSamples` 命令按 XML 的 `Cookie` 将上游应答路由回发起请求的客户端。

- **多用户登录**：首条 `Logon` 转发至 Cornerstone；上游应答 `ErrorCode=0` 后，对**后续客户端**的 `Logon` 默认在网关侧直接返回成功（可用 `--no-synthetic-logon` 改为全部转发上游）。
- **TCP 客户端与网页账号**：若已配置 **`--web-user` / `--web-password`**，连接网关的 TCP 客户端发来的 `<Logon>` 中 **未带或为空** 的 `<User>` / `<Password>` 会由网关用上述网页凭据补全后再转发上游；其它 XML 指令在转发前也会尽量先完成同一套上游网页登录，便于客户端不在本地持有仪器账号即可发令（网关本身不对 TCP 客户端做额外鉴权）。
- **AddSamples**：默认不立即转发，进入长度可配置（默认 8）的 FIFO 队列，浏览器打开 **网页**（默认 `http://127.0.0.1:8765/`）管理队列。若在 JSON / CLI 中配置 **`privileged_add_samples_host`**（或 `--privileged-add-samples-host`），则 **来自该主机 IP** 的 TCP `AddSamples` 会直接转发上游，其它客户端仍截留。
- **RemoteControlState**：网页顶栏展示仪器应答；网关在上游 TCP **新建连接或重连**后问询一次 `<RemoteControlState/>`（需已配置 `--web-user` / `--web-password`），**不参与** AddSamples 是否截留的判定。
- **网页 UI**：根路径 `/` 为前后端分离界面。主导航为 **分析**（样品缓存队列、**查询 Sets** / **远程录入 Sets**（`LastRemoteAddedSets`+`SetsEx`）、Replicates/谱图、可折叠 **仪器状态** Status · Widgets）、**诊断**（二级菜单：**环境参数**、**数字IO**、**状态检查**；数字 IO 页在同一次请求中附带 `<ValveStates/>`，**数字输出**标题下副标题显示当前 `Active=True` 的阀门状态；**状态检查**页请求 `<Status IncludeGauges="False" IncludeSystemCheckResults="True" IncludeLeakCheckResults="True"/>`，分 **Elements** / **Odometers**（分区表格）与 **LeakCheckResults**（多卡片+底栏摘要）/ **SystemCheckResults**（两列网格+汇总脚））、**设置**（二级菜单：**网关配置**、**传送**、**方法**、**标样**；**传送** / **方法** / **标样** 为独立页面，布局相同：左侧列表 + 行末 **▶** 展开右侧详情；**传送** 请求 `<Transports/>` / `<Transport Key="…"/>`；**方法** 请求 `<Methods/>` / `<Method Key="…"/>`；**标样** 请求 `<Standards/>` / `<Standard Key="…"/>`（列表含名称、说明、碳/硫、上次修改时间）；状态圆 **「-」** 表示 `Excluded=true`）、**仪器**（二级菜单：**维护计数器**、**自动**、**系统**；**维护计数器** 列表请求 `<Counters/>`，详情 ``<Counter Key="…"/>``，状态圆 `-` 表示 `Excluded=True`，`!` 表示 `IsExpired=True`；**自动** 页请求 `<AutomationStatus/>`；**系统** 页请求 `<SystemParameters/>`，按分区折叠展示参数（布尔项为双钮只读样式）；完整 InstrumentInfo 仍在标题栏 **i** 按钮）。环境拉数及网页「发送到仪器」需配置 **`--web-user` / `--web-password`**（与仪器远程账号一致）。旧版纯表格页：`/legacy`。
- **网页改配置**：点击 **设置 → 网关配置**、或顶栏下方状态条（本地网关 / 用户 / 上游与队列上限）打开「网关配置」；`PUT /api/settings` 可更新内存中的上游地址、仪器账号、截留队列上限、监听地址等。变更 **客户端/网页监听** 的 host、port 后需 **重启** `cornerstone-mock` 方生效；变更上游地址会尝试 **立即重连** 上游 TCP。以 **`--config` 某 JSON 启动** 时可将设置 **合并写回** 该文件（否则仅内存有效，进程结束即丢）。
- **REST**：`GET /api/queue`、`POST /api/queue/send`（JSON `{"ids":["..."]}`，发送后**保留**队列项）、`GET /api/environment/ambients`、`GET /api/diagnostic/digital-io`（应答中含 `valveStateDisplay` / `valveStateError`）、`GET /api/diagnostic/status-check`（解析 `Status` 下的 Elements、Odometers、SystemCheckResults、LeakCheckResults）、`GET /api/instrument/counters`、`GET /api/instrument/counter?key=`、`GET /api/instrument/automation-status`、`GET /api/instrument/system-parameters`、`GET /api/settings/transports`、`GET /api/settings/transport?key=`、`GET /api/settings/methods`、`GET /api/settings/method?key=`、`GET /api/settings/standards`、`GET /api/settings/standard?key=`、`GET /api/config`、`GET|PUT /api/settings`；分析页还使用 `GET /api/instrument/sets?number=&start_at=&filter_key=`、`GET /api/instrument/remote-import-sets`（网页「远程录入 Sets」：先 `LastRemoteAddedSets` 取 Key，再 `SetsEx` 批量取概要并刷新 Set 表）（**`number` 默认 10**；**`filter_key` 省略或空时按 `0` 发送**；应答中带 `window` / `pagination`：`nextOlderStartAt` / `prevNewerStartAt` 供网页翻页）、`GET /api/instrument/set-reps?set_key=&include_detail=&tag=`、`GET /api/instrument/rep-plot?set_key=&tag=`、`GET /api/instrument/set-stats?set_key=`（均经独立 TCP + Logon，与 `cornerstone-cli` 一致）；静态资源在 `/static/*`。

等价的命令行示例（与下方 `cornerstone-mock.config.example.json` 一致）：Cornerstone `127.0.0.1:54321`，网关接客户端 `12345`，网页 `8080`，账号 `remote` / `control`：

```bash
cornerstone-mock --host 127.0.0.1 --port 12345 --upstream-host 127.0.0.1 --upstream-port 54321 --web-port 8080 --web-user remote --web-password control
```

也可用 **`--config` / `-c`** 指定 JSON 配置文件（键名与 CLI 的 dest 一致，如 `web_host`、`web_port`）；**命令行参数会覆盖文件中的同名字段**。仓库内示例：`CornerstoneMock/cornerstone-mock.config.example.json`，其中约定为：**Cornerstone** 监听 `127.0.0.1:54321`（`upstream_host` / `upstream_port`），**网关**在 `12345` 接客户端（`host` / `port`），**网页**在 `8080`（`web_host` / `web_port`），网页发往仪器用的账号为 **`remote` / `control`**（`web_user` / `web_password`）。若本机 `8080` 被占用，可在命令行加 `--web-port` 等覆盖。

```bash
cornerstone-mock -c cornerstone-mock.config.example.json
```

**用配置文件做网页（及网关）本地开发**：在仓库根目录或 `CornerstoneMock` 下可直接启动并加载示例 JSON（默认 **http://127.0.0.1:8080/**，见配置文件）；额外参数仍会覆盖文件，例如 `--web-port 9000`。

```bash
python -m cornerstone_mock.dev_mock_web
```

安装包后也可使用入口 **`cornerstone-mock-dev`**（与上一行等价）。若使用自己的 JSON，可设置环境变量 **`CORNERSTONE_MOCK_CONFIG`** 指向该文件，或在当前目录放置 **`cornerstone-mock.config.json`**（优先于示例文件名）。

CLI 指向**网关**：

```bash
cornerstone-cli tcp version --host 127.0.0.1 --port 12345
cornerstone-cli tcp logon --host 127.0.0.1 --port 12345 --user demo --password demo
```

向各连接定时推送异步消息（秒）：

```bash
cornerstone-mock --host 127.0.0.1 --port 12345 --upstream-host 127.0.0.1 --upstream-port 54321 --web-port 8080 --async-message-interval 2
```

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
- `sets-ex` — 典型：`--key 56 57`（多个 Key），或问答；session 中可 `sets-ex 56 57`。
- `set-keys-ex2` — 无参，返回全部 set 的 Key 与分析日期（常与 `sets-ex` 配合：先取 Key 再批量查概要）。
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
