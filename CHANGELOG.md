# Changelog

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
