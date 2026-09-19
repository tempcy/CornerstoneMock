# Agent 已实现功能测试说明（C1）

针对 **当前已落地** 的 C1 能力：注册表 / 心跳、编排 HTTP、P0 查数 tools、P1 `collect_instrument`。仪器侧连接面：

| 服务 | 地址 | 用途 |
|------|------|------|
| **Bridge REST** | `http://<GC8_IP>:8080` | 二炼钢 GC8 数据面（P0 tools 实际调用） |
| **CLI TCP** | `<GC8_IP>:54321` | 仪器 TCP 网关；连通性自检 / 排故备用（C1 查数不经此端口） |
| **Agent 编排** | `http://127.0.0.1:8090` | 本机 Orchestrator（`run` 启动） |

**未实现（不必测）**：规则引擎（P2 `rules_eval`）、`log_tail`、Queue / 仪器日志。

A1 长周期时序（SQLite）**已实现**，见下文 §7。

配置文件：本机 `cornerstone-agent.config.json`（含现场 IP，已加入 `.gitignore`，勿提交）。地址占位符见 [docs/二炼钢实验室网络.md](../docs/二炼钢实验室网络.md)。

**二炼钢 Agent 主机（已部署）**：`<AGENT_HOST>`（SSH `user@<PF_SENSE> -p 4322`），代码目录 `C:\CornerstoneMock\CornerstoneAgent`，Python 3.12，编排监听 `127.0.0.1:8090`。

---

## 0. 前置

```bash
cd CornerstoneAgent
python3 -m pip install -e .
```

确认配置中 Bridge 为：

```json
"bridge": {
  "base_url": "http://<GC8_IP>:8080",
  "timeout_s": 90
}
```

身份（与注册表一致）：

| 字段 | 值 |
|------|-----|
| `org_id` | `baowu` |
| `lab_id` | `lab-2lg` |
| `instrument_id` | `GC8`（默认；`instruments[]` 另含 GC6/GC7/GO6/GO7/GO9） |
| `agent_id` | `agent-2lg-gc8`（同 lab 六台均有独立 `agent-2lg-*`） |

---

## 1. 仪器连通性（不经 Agent）

### 1.1 Bridge REST `:8080`

```bash
curl.exe -s http://<GC8_IP>:8080/api/status
```

期望：JSON 含 `upstreamConnected` / `instrumentOnline` / `businessOnline` 等字段；HTTP 200。

可选：

```bash
curl.exe -s "http://<GC8_IP>:8080/api/instrument/sets?number=3&start_at=-1&filter_key=0"
curl.exe -s http://<GC8_IP>:8080/api/diagnostic/status-check
```

### 1.2 CLI TCP `:54321`

需已安装 `cornerstone-cli`：

```bash
python3 -m cornerstone_cli tcp --host <GC8_IP> --port 54321 version
```

或任意只读子命令（如 `instrument-info` / `remote-control-state`）。期望：能连上并返回协议响应。

> Agent C1 的 P0 tools **只走 Bridge REST**；本节用于确认仪器侧两端口均可达。

---

## 2. 启动 Agent 编排

另开终端：

```bash
cd CornerstoneAgent
python3 -m cornerstone_agent run
```

期望日志：监听 `127.0.0.1:8090`；`embed_local_agent=true` 时自动 register，并周期 heartbeat（含 Bridge ping）。

健康检查：

```bash
curl.exe -s http://127.0.0.1:8090/health
```

期望：`{"ok":true,"service":"cornerstone-agent-orchestrator",...}`

---

## 3. 基建：注册表 / 心跳 / 仪器列表

### 3.1 注册表中的仪器

```bash
curl.exe -s "http://127.0.0.1:8090/v1/instruments?online_only=true"
```

或：

```bash
python3 -m cornerstone_agent list-instruments
```

期望：出现 `lab-2lg` / `GC8` / `agent-2lg-gc8`，`online=true`，`bridge_url` 为 `http://<GC8_IP>:8080`，`bridge_reachable=true`（Bridge 可达时）。

### 3.2 手动 register（可选）

进程已 `run` 且 embed 时一般不必手动注册。单次写入注册表：

```bash
python3 -m cornerstone_agent register
```

### 3.3 心跳（可选手工）

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/agents/heartbeat ^
  -H "Content-Type: application/json" ^
  -d "{\"agent_id\":\"agent-2lg-gc8\",\"bridge_reachable\":true}"
```

PowerShell：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8090/v1/agents/heartbeat `
  -ContentType "application/json" `
  -Body '{"agent_id":"agent-2lg-gc8","bridge_reachable":true}'
```

期望：`ok=true`，`last_seen` 更新。

---

## 4. P0 Tools（已实现查数）

以下两种方式等价：HTTP `POST /v1/tools/{name}`，或 CLI `python3 -m cornerstone_agent tool …`。

### 4.1 `list_instruments`

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/list_instruments ^
  -H "Content-Type: application/json" ^
  -d "{\"online_only\":true}"
```

```bash
python3 -m cornerstone_agent tool list_instruments
```

期望：`ok=true`，列表含 GC8。

### 4.2 `get_instrument_status`

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/get_instrument_status ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"include_status_check\":true}"
```

```bash
python3 -m cornerstone_agent tool get_instrument_status
```

期望：

- 外层 `ok=true`
- `result.status=ok`，`bridge_reachable=true`
- `data.summary` 含联机摘要；`data.status` 为 Bridge `/api/status` 原文
- 默认带 `status_check`（或 `status_check_error`）

可选带系统参数：

```bash
python3 -m cornerstone_agent tool get_instrument_status --args-json "{\"include_system_parameters\":true}"
```

### 4.3 `get_analysis_sets`

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/get_analysis_sets ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"number\":3}"
```

```bash
python3 -m cornerstone_agent tool get_analysis_sets --number 3
```

期望：`ok=true`，`data.sets` 为 Bridge 返回；记下其中某条的 `set_key`（字段名以实际 JSON 为准）供下一步。

### 4.4 `get_set_reps`

将 `<SET_KEY>` 换成上一步得到的键：

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/get_set_reps ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"set_key\":\"<SET_KEY>\",\"include_stats\":true}"
```

```bash
python3 -m cornerstone_agent tool get_set_reps --set-key "<SET_KEY>"
```

期望：`ok=true`，`data.reps` 有内容；默认尝试附带 `stats`（失败时为 `stats_error`，仍可判定 tool 通路正常）。

### 4.5 P1 `collect_instrument`

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/collect_instrument ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"profile\":\"status_light\"}"
```

```bash
python3 -m cornerstone_agent tool collect_instrument --profile status_light
python3 -m cornerstone_agent tool collect_instrument --profile troubleshoot
```

期望：`ok=true`，`result.data.snapshot_id` 形如 `snap-…`，`endpoints` 各别名含 `ok`；快照落盘于配置旁 `acquisition_snapshots/`。

---

## 5. Job 信封通路（可选）

编排也可直接投递 typed job（与 tool 映射一致）：

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/jobs ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"job\":{\"schema_version\":\"agent-job.v1\",\"job_id\":\"test-status-001\",\"type\":\"get_status\",\"created_at\":\"2026-07-26T06:00:00Z\",\"timeout_s\":60,\"params\":{\"include_status_check\":true}}}"
```

期望：`result.status=ok`，`meta.bridge_base_url` 为 `http://<GC8_IP>:8080`。

---

## 6. 一键冒烟

Agent 不必先 `run`（脚本直连配置 + 注册表文件）：

```bash
cd CornerstoneAgent
python3 scripts/smoke_c1.py
```

覆盖：register → Bridge ping → `list_instruments` / `get_instrument_status` / `get_analysis_sets`。

期望：

- `bridge_reachable: true`
- 各 tool 打印 `ok=true`（Bridge 离线时查数可为 `bridge_unreachable`，注册仍应成功）

---

## 7. A1 长周期时序（SQLite）

`run` 时若配置 `timeseries.enabled`（默认 true），后台按 **采集任务** 独立调度：

- **Widgets**（`status-widgets`）：默认 **10 秒**，有效期 **3 天**
- **Ambients**（`ambients`）：默认 **5 分钟**，有效期 **90 天**

范围可选本 Agent 下全部设备，或指定单台。采集项清单来自白名单查询命令，可在运维台「配置」页保存并热加载。

单元测试（无仪器）：

```bash
cd CornerstoneAgent
python3 -m unittest tests.test_timeseries_a1 tests.test_timeseries_tools tests.test_collect_p1 tests.test_ui_api
```

BaoClaw / 对话侧 tool（读 SQLite，不经 Bridge）：

```bash
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/get_timeseries_latest -H "Content-Type: application/json" -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"job_id\":\"widgets\"}"
curl.exe -s -X POST http://127.0.0.1:8090/v1/tools/query_timeseries -H "Content-Type: application/json" -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"hours\":24,\"job_id\":\"widgets\",\"metric\":\"gauge.Back_Pressure\"}"
```

立即采集一次（不需要先 `run`）：

```bash
python3 -m cornerstone_agent collect once --instrument-id GC8
python3 -m cornerstone_agent collect once --instrument-id GC8 --job widgets
python3 -m cornerstone_agent collect once --all
```

查询 24h / 导出 CSV：

```bash
python3 -m cornerstone_agent timeseries query --instrument-id GC8 --hours 24
python3 -m cornerstone_agent timeseries query --instrument-id GC8 --job widgets --metric gauge.上端气流压力
python3 -m cornerstone_agent timeseries query --instrument-id GC8 --job ambients --hours 2160
python3 -m cornerstone_agent timeseries export --instrument-id GC8 --hours 24 --out gc8.csv
```

编排已启动时：

- 运维台 `http://127.0.0.1:8090/ui/` → **时序**
- `GET /api/ui/timeseries?instrument_id=GC8&hours=24&job_id=widgets`
- `GET /api/ui/timeseries/export.csv?instrument_id=GC8&hours=24&job_id=ambients`
- `POST /api/ui/timeseries/collect-once` body `{"instrument_id":"GC8","job_id":"widgets"}`
- `PUT /api/ui/timeseries/jobs` body `{ "jobs": [ ... ] }`（热加载，不改 instruments）

期望：`stats.samples >= 1`（采集成功后）；CSV 含 `ts,instrument_id,metric,...`。

---

## 8. 验收清单

| # | 项 | 通过条件 |
|---|----|----------|
| 1 | Bridge REST | `<GC8_IP>:8080/api/status` 200 |
| 2 | CLI TCP | `cornerstone-cli tcp --host <GC8_IP> --port 54321 …` 可连 |
| 3 | Orchestrator | `/health` → `ok` |
| 4 | 嵌入式注册/心跳 | `list-instruments` 见 GC8 且 `online` / `bridge_reachable` |
| 5 | P0 status | `get_instrument_status` → `result.status=ok` |
| 6 | P0 sets | `get_analysis_sets` 返回 sets |
| 7 | P0 reps | `get_set_reps` 用真实 `set_key` 成功 |
| 8 | 冒烟脚本 | `smoke_c1.py` 退出码 0 且查数成功 |
| 9 | A1 时序 | `collect once` 后 `timeseries query` 24h 有样本；或运维台时序页有曲线 |

---

## 9. 常见失败

| 现象 | 排查 |
|------|------|
| `bridge_unreachable` / `bridge_reachable=false` | 本机到 `<GC8_IP>:8080` 网络/防火墙；先做 §1.1 |
| `instrument_not_found` / `agent_offline` | 先 `run`（embed）或 `register`；心跳 TTL 默认 90s |
| `get_set_reps` → `invalid_params` | 缺少 `set_key` |
| TCP `:54321` 超时 | 仅影响 CLI 备用通道；P0 REST 仍可独立通过 |
| 端口冲突 `8090` | `python3 -m cornerstone_agent run --port 8091` |

接口细节见 [INTERFACE.md](INTERFACE.md)；架构边界见 [AGENT.md](AGENT.md)。
