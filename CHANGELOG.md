# Changelog

## 0.1.1

现场联调问题修复（在 0.1.0 基线上）。

### 网关 (Bridge)

- `instrument_rq` 长连接模式不再全局串行等待：仅 Logon 与短连接路径持 `_instrument_sidecar_lock`，避免网页多个 `/api/instrument/*` 排队超过 Web 代理 300s。
- HTTP 应答写入时对 `ConnectionResetError` / 断连静默处理，消除 `Unhandled exception in client_connected_cb` 刷屏。
- **分级日志**：`logging` 输出带时间戳与等级；RQ 类（Status/Prerequisites/Sets 等）默认 INFO 仅控制台（需 `log_verbose_gateway`），**不写入** `%APPDATA%\CornerstoneMock\logs\bridge.log`；业务事件与 WARNING+ 写入轮转文件。Heartbeat/重连类 WARNING 5 分钟限流。

### Web

- 代理 Bridge 超时返回 **504** JSON（`Bridge 应答超时`），不再未捕获 `TimeoutError`。
- 合并冲突与 UTF-8 控制台初始化（`configure_stdio_utf8`）恢复一致。

### 安装程序

- 版本号回退为 **0.1.1**（`VERSION` / Python 包 / `CornerstoneMock-Setup-0.1.1.exe`）。
- 检测到已安装时提示先卸载；卸载脚本先停服务/进程再删程序目录，**保留** `%APPDATA%\CornerstoneMock` 配置。
- 安装时合并已有 JSON 配置（含从 `%ProgramData%\CornerstoneMock` 迁移）。

---

## 0.2.0

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
