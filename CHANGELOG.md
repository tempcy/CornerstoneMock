# Changelog

## 0.1.6

**定位**：上游僵死 TCP 回收/重连可靠性修复（建议现场升级安装包）。

### Bridge

- **上游回收/重连**：修复首次回收后不再重复回收、重连 worker 静默退出；回收时缩短持锁、读循环 cancel 超时；transport 已 down 时仍调度重连；僵死连接上 `instrument offline` 强制 drop 再连；转发超时 watcher 随回收代际失效；定时 activity_stale 巡检（默认 30s）。
- **配置**：新增 `upstream_heartbeat_wait_timeout`、`upstream_activity_stale_seconds`、`upstream_read_cancel_timeout`、`upstream_stale_check_interval`（控制台表单可编辑）。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | 0.1.6 |
| cornerstone-web | 0.1.6 |
| cornerstone-cli | 0.1.6 |

---

## 0.1.5

**定位**：0.1.4 后配置默认值与控制台标识小版本。

### Bridge

- **配置默认**：`upstream_recv_idle_clear` **5**、`upstream_client_forward_timeout` **10**（example、CLI/Hub 缺省、控制台表单回退一致）。
- **控制台**：窗口标题显示 `cornerstone-bridge` 版本与打包时间（`build-info.json`）。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | 0.1.5 |
| cornerstone-web | 0.1.5 |
| cornerstone-cli | 0.1.5 |

---

## 0.1.4

**定位**：0.1.3 现场联调修复（TCP 248 粘包尾段、合成 Logon、指令失败计数）。

### Bridge

- **修复**：`tcp_outer_len=248` 类布局（`inner152` Heartbeat + `RemoteControlState` 前缀）不再把 92 字节残缺 XML 当整包下发，避免 `truncated_tags` 断连。
- **修复**：上游 TCP 重连后 TCP 客户端 Logon 走合成应答（保留 `_logon_seen_upstream_success`）；Logon 转发超时不计入 `command_fail_streak`。
- **修复**：`should_synthesize_client_logon()` 统一合成 Logon 判定。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | 0.1.4 |
| cornerstone-web | 0.1.4 |
| cornerstone-cli | 0.1.4 |

### 配置默认值（example / 无配置项时）

- `upstream_recv_idle_clear`：`30` → **`5`**（与 `upstream_inner_reassembly_timeout` 同量级，减少断流后残留半包）
- `upstream_client_forward_timeout`：`120` → **`10`**（更快发现转发无应答，避免长时间挂死 Cookie 表）

---

## 0.1.3

**定位**：上游仪器报文解析与业务在线判定重构（Issue #2 粘包/分包、心跳误判断线）。安装包带 **build_id** 后缀。

### Bridge

- **UpstreamRecvBuffer**：全局 recv 缓冲 + `inner_len` 循环解包（粘包/拆包）；无 inner 头的整段 UTF-16 XML（如 Logon 应答）单独识别。
- **业务在线**：`instrumentOnline` / `businessOnline`；连续心跳失败（默认 2 次）或连续指令失败（默认 3 次）回收上游 TCP；入站 Heartbeat 不要求 Cookie 回显。
- **配置**：`upstream_recv_idle_clear`、`upstream_heartbeat_fail_max`、`upstream_command_fail_max`、`upstream_client_forward_timeout`。
- **API**：`/api/status`、`/api/monitor` 增加失败计数与 recv 缓冲诊断字段。

### Web / Queue

- 顶栏连接状态优先 `businessOnline`；Queue 状态行显示失败计数与缓冲字节。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | 0.1.3 |
| cornerstone-web | 0.1.3 |
| cornerstone-cli | 0.1.3 |

---

## 0.1.2

**定位**：在 0.1.1 基础上的小版本，增加 **Bridge 桌面控制台** 与连接/队列监控 API。组件与安装包统一为 **0.1.2**。

### Bridge

- **桌面控制台**（`cornerstone-bridge-ui`）：并入 `cornerstone_bridge.ui`（PySide6，可选依赖 `[ui]`）；系统托盘、配置编辑、日志尾随、连接与通讯队列监控。
- **REST**：`GET /api/monitor`（上游连接、TCP 客户端列表、队列概况）；Hub 记录 REST 实际监听地址 `api_listen_*`。
- **上游 TCP 分段拼接**：`upstream_inner_reassembly_timeout`（默认 5s）；当 inner 帧长度大于单条 TCP payload 时缓冲拼接后再解析；超时或异常时打 WARNING 并丢弃不完整帧（`0`= 不等待）。
- **修复**：控制台初始化时状态栏创建顺序（避免 `_status_config` 未定义）。

### Web

- **分析页谱图**：元素统计卡片内 RepPlot 曲线由 Canvas 手绘改为 **ECharts**（静态资源 `echarts.min.js` v5.5.1）；支持 tooltip、窗口/面板 resize；仍兼容旧版单序列与内嵌 PNG。
- **修复**：`cornerstone-web-dev` 启动 Bridge 时补传 `upstream_inner_reassembly_timeout`（此前缺参导致 `TypeError`）。

### 安装程序

- PyInstaller 在 `Bridge\` 目录同时输出 `cornerstone-bridge.exe` 与 `cornerstone-bridge-ui.exe`（共享 `_internal`）。
- 可选组件 **Bridge 控制台**、登录时启动托盘任务；开始菜单快捷方式指向 `Bridge\cornerstone-bridge-ui.exe`。

| 包 | 版本 |
| --- | --- |
| cornerstone-bridge | 0.1.2 |
| cornerstone-web | 0.1.2 |
| cornerstone-cli | 0.1.2 |

---

## 0.1.1

**定位**：在 0.1.0 可安装基线上的**现场联调 Bug 修正版**（非 0.2.0 功能分支）。组件与安装包统一为 **0.1.1**（`VERSION`、`cornerstone-bridge` / `cornerstone-web` / `cornerstone-cli`、`CornerstoneMock-Setup-0.1.1.exe`）。

### 网关 (Bridge)

- **并发与超时**：`instrument_rq` 长连接模式不再对整个请求全程持有 `_instrument_sidecar_lock`；仅上游 Web Logon 与短连接模式串行化，避免多个 `/api/instrument/*` 与 LIMS TCP 流量排队，导致 Web 代理 300s `TimeoutError`。
- **HTTP 断连**：Bridge REST 与上游转发在客户端提前断开时，对 `ConnectionResetError` / `BrokenPipeError` / WinError 64 等静默处理，不再刷 `Unhandled exception in client_connected_cb`。
- **分级日志**（`bridge_logging.py`）：
  - 统一格式：`时间戳` + `等级` + `[模块名]` + 消息。
  - **控制台**（NSSM `bridge-stdout.log`）：`StreamHandler` 显式绑定 **stdout**（修复此前默认 stderr 导致 stdout 为空、日志全进 stderr 的问题）。
  - **轮转文件** `bridge.log`：写入 INFO 及以上业务日志；**RQ 类**（`Status` / `Prerequisites` / `RemoteControlState` / `Sets` / `SetReps` 等）为 INFO 但**默认不写文件**；`log_verbose_gateway` 可在控制台查看 RQ。
  - 服务以 LocalSystem 运行时，`log_file` 中含 `%APPDATA%` 会错误落到 `systemprofile`；若通过 `-c` 指定用户配置目录，轮转日志改写到**该目录** `logs\bridge.log`。
  - `Heartbeat` 超时、上游重连失败等 WARNING **5 分钟限流**（`log_throttle_interval_s`，可配置）。
- **配置**：`cornerstone-bridge.config.json` 增加 `log_level`、`log_verbose_gateway`、`log_file`、`log_file_level`、`log_file_max_mb` 等项（example 已同步）。

### Web

- **代理超时**：转发 Bridge 超时返回 **504** JSON（`Bridge 应答超时`），不再未捕获 `TimeoutError` 导致安装/页面长时间无响应。
- **HTTP 断连**：代理回写与 `_http_send` 对客户端断开做与 Bridge 一致的处理。
- **启动**：恢复 `configure_stdio_utf8()`，修复合并冲突后控制台中文乱码风险；HTTP 回调外层捕获异常并写日志。

### CLI

- PyInstaller 入口与 Bridge/Web 一致，启动时调用 `configure_stdio_utf8()`。

### 安装程序

- **升级策略**：检测到同 AppId 已注册卸载项或仍存在 `Program Files\CornerstoneMock\Bridge\cornerstone-bridge.exe` 时，提示**先卸载**再继续；可选启动静默卸载（`InitializeSetup` 不使用 `IsUpgrade`，因该函数在 setup 早期不可用）。
- **卸载**（`uninstall-services.ps1`）：先 `Stop-Service` → NSSM stop/remove → 结束 `cornerstone-bridge` / `cornerstone-web` / `CornerstoneQueue` / `cornerstone-cli` 进程，再删除程序目录；**保留** `%APPDATA%\CornerstoneMock\` 下 JSON 与样品队列。
- **配置保留与合并**（`merge-config.ps1` + `post-install.ps1`）：
  - 首次或重装时，若存在 `%ProgramData%\CornerstoneMock\` 旧配置，迁移到 `%APPDATA%\CornerstoneMock\`。
  - 已有 Roaming 下 JSON 与 `*.example.json` **合并**（用户字段优先，模板仅补全新增键）。
- **安装卡住修复**：
  - `validate-install.ps1`：`-NonInteractive` 时**不再加载** `System.Windows.Forms`（隐藏会话会永久挂起）；端口检测改为 `TcpListener` 绑定，移除易挂起的 `Get-NetTCPConnection`。
  - `post-install.ps1`：不再嵌套隐藏子 PowerShell；安装最后一步 **显示 PowerShell 窗口**，分步输出 1/3 配置、2/3 校验、3/3 服务注册；失败时提示查看日志并按 Enter 关闭。
- **服务注册**：`install-services.ps1` 控制台输出 NSSM 步骤；删除旧服务等待缩短且超时不再阻断安装。
- **其它**：解决 `server.py` / `run_*.py` / `install-services.ps1` / `installer/README.md` 等合并冲突；安装脚本 UTF-8 BOM；`AppEnvironmentExtra` 设置 `PYTHONUTF8` / `PYTHONIOENCODING=utf-8`。

### 组件版本

| 组件 | 版本 |
|------|------|
| cornerstone-bridge | 0.1.1 |
| cornerstone-web | 0.1.1 |
| cornerstone-cli | 0.1.1 |
| CornerstoneQueue | 随安装包（无独立 semver） |

---

## 0.2.0

> 说明：仓库中曾短暂标为 0.2.0 的条目对应功能向迭代；**当前对外发布 Bug 修正版为 0.1.1**。下列内容保留作历史记录。

现场联调通过后的首个定稿安装包版本。

### 网关 (Bridge)

- 客户端 XML 无 `Cookie` 时自动注入并登记路由，修复 `Status` / `Prerequisites` / `RemoteControlState` 等应答被记为 `orphan upstream response`、无法回传客户端的问题。
- `AddSamples` 截留队列、多客户端 Logon 合成应答等行为与现场 LIMS 轮询兼容。

### Web

- 分析页「试样指令缓存」区域默认折叠，进入页面优先展示仪器样品记录。
- 仪器状态条默认折叠（与 0.1.x 一致）。

### 安装程序

- Inno Setup 安装包输出：`CornerstoneMock-Setup-0.2.0.exe`。
- 构建脚本从仓库根目录 `VERSION` 读取版本号；服务注册、管理员提升、UTF-8 无 BOM 等现场问题已在 0.1.x 迭代中收敛，本版作为安装程序定稿基线。

### 组件版本

- `cornerstone-bridge` / `cornerstone-web` / `cornerstone-cli` Python 包：**0.2.0**
- `CornerstoneQueue`（WinUI 悬浮窗）：随安装包分发，未单独 semver 标号

---

## 0.1.0

初始可安装版本：Bridge TCP 网关、Web 管理页、CLI、Queue 悬浮窗及 NSSM 服务安装脚本。
