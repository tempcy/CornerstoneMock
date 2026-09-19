---
name: cornerstone_instrument
description: "Query LECO Cornerstone gas analyzers via company orchestrator: P0/P1 (list_instruments, get_instrument_status, get_analysis_sets, get_set_reps, collect_instrument), A1 timeseries, and D1 post_operator_notice (push advice to Bridge operator dialog). Use for online/status, analysis, troubleshoot snapshots, trends, and sending maintain/reject guidance to on-site staff."
metadata:
  version: "0.3.1"
  qwenpaw:
    emoji: "🔬"
    requires: {}
---

# Cornerstone 仪器查询（C1 P0/P1 + A1 时序 + D1 建议下行）

当用户询问 **仪器是否在线 / 能否跑样 / 分析结果 / 某次 set 的重复性 / 排故快照 / 压力流量温度趋势 / 环境点 / 最近采集**，或需要 **把运维/检修建议推到仪器机 Bridge 对话框给现场人员看** 时使用本技能。

**不要**直连仪器机 Bridge REST（`127.0.0.1:8081`）或工控网 `<L1_HOST>:8080`。对话侧只调编排 DNAT。现场基址见本机 `BaoClaw/skill.json` 的 `orchestrator_base_url`（该文件不入库）；下文示例用占位符 `http://<PF_SENSE>:8090`。

完整 schema 见同目录 [`baoclaw-tools.v1.json`](baoclaw-tools.v1.json)（与仓库 `CornerstoneAgent/schemas/baoclaw-tools.v1.json` / [INTERFACE.md](../../../CornerstoneAgent/INTERFACE.md) 对齐）。边缘 Agent 版本：**0.3.3+**。

## 前置

```bash
curl -s http://<PF_SENSE>:8090/health
# 期望：ok=true, service=cornerstone-agent-orchestrator, version≥0.3.3
```

## Tool 调用方式

对每个 tool：`POST http://<PF_SENSE>:8090/v1/tools/{name}`，JSON body = parameters。

### 1. list_instruments（P0）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/list_instruments ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"online_only\": true}"
```

记下 `lab_id` / `instrument_id`。二炼钢常见：`GC6` `GC7` `GC8` `GO6` `GO7` `GO9`。

### 2. get_instrument_status（P0）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_instrument_status ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"include_status_check\":true}"
```

关注 `result.data.summary`（`business_online`、`ready_hint`）。

### 3. get_analysis_sets（P0）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_analysis_sets ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"number\":10}"
```

### 4. get_set_reps（P0）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_set_reps ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"set_key\":\"YOUR_SET_KEY\",\"include_stats\":true}"
```

### 5. collect_instrument（P1）

| profile | 用途 |
|---------|------|
| `status_light` | status + status-check |
| `analysis_recent` | status + sets + set-stats |
| `troubleshoot` | status / system-parameters / status-check / counters / set-stats |
| `custom` | 自选白名单别名 |

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/collect_instrument ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"profile\":\"troubleshoot\"}"
```

### 6. get_timeseries_latest（A1）

最新采集点（默认 `gauge.*`）。任务：`widgets`≈10s 仪表；`ambients`≈5min 环境。

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_timeseries_latest ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"job_id\":\"widgets\",\"gauges_only\":true}"
```

### 7. list_timeseries_metrics（A1）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/list_timeseries_metrics ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"hours\":24,\"job_id\":\"widgets\"}"
```

### 8. query_timeseries（A1）

统计 + 最近样本；带 `metric` 时另给 `series_summary`（min/max/avg/last）与降采样 `series`。

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/query_timeseries ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"hours\":24,\"job_id\":\"widgets\",\"metric\":\"gauge.Back_Pressure\"}"
```

Ambients 长窗口示例：`hours=2160`（90 天）、`job_id=ambients`。

### 9. get_timeseries_sample（A1）

`sample_id` 来自 `query_timeseries` → `samples[].id`。

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_timeseries_sample ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"sample_id\":12345}"
```

## 何时用哪类工具

| 用户意图 | 工具 |
|----------|------|
| 在不在线 / 能否跑样 | `get_instrument_status` |
| 碳硫分析结果 / set | `get_analysis_sets` → `get_set_reps` |
| 排故要成套快照 | `collect_instrument` |
| **现在**压力/流量/柜温 | `get_timeseries_latest`（`widgets` 或 `ambients`） |
| **趋势 / 波动 / 近 N 小时** | `list_timeseries_metrics` → `query_timeseries` |
| 某次采集全部点 | `get_timeseries_sample` |

时序读 Agent 本地 SQLite，**不经 Bridge**；注册表有该仪器即可（不必要求当前 online）。  
**不要**用 `GET /api/ui/timeseries*` 代替上述 `/v1/tools/*`（运维台专用）。

## CLI 等价（Agent 主机）

```bash
python -m cornerstone_agent tool get_timeseries_latest --instrument-id GC8 --job widgets
python -m cornerstone_agent tool list_timeseries_metrics --instrument-id GC8 --hours 24 --job widgets
python -m cornerstone_agent tool query_timeseries --instrument-id GC8 --hours 24 --job widgets --metric gauge.Back_Pressure
python -m cornerstone_agent tool get_timeseries_sample --instrument-id GC8 --sample-id 12345
```

## 规则

1. **先 list（或会话已绑定仪器），再查状态/分析/时序。** 默认 `lab-2lg` / `GC8`。
2. 写仪器（发样、改方法、改采集任务）**不在本技能范围**。
3. 若返回 `agent_offline` / `instrument_not_found` / `timeseries_disabled` / bridge 失败：说明原因，**不要编造**数据。
4. 回复引用 `result` 结构化字段，并说明 `instrument_id`、任务（widgets/ambients）、时间窗口。
5. 曲线回答优先用 `series_summary`；不要把上百个 raw 点原样贴给用户。
6. **P2 未实现**：不要调用 `eval_instrument_rules` / `tail_instrument_logs`。
7. 身份与边界见 `PROFILE.md` / `SOUL.md` / `AGENTS.md`；连接笔记见 `MEMORY.md`。
8. **建议下行**：确认用户要通知现场后，用 `post_operator_notice`（`severity=reject|maintain` 会置顶弹窗）。**不会**自动改仪器或发样。
9. **智宝 `/chat-modern` 反馈芯片**：排故/结论类最终回复正文结束后空一行，末尾只输出一行 JSON（勿代码围栏）：`{"follow_ups":["有用","需改进","【结案】已解决","【结案】部分解决","【结案】建议不对"]}`（可按场景删减，最多 5 条）。细则见 `AGENTS.md`。

## Tool 一览

| name | 优先级 | 用途 |
|------|--------|------|
| `list_instruments` | P0 | 在线/注册仪器清单 |
| `get_instrument_status` | P0 | 状态 / 可选 status-check |
| `get_analysis_sets` | P0 | 近期分析集 |
| `get_set_reps` | P0 | 指定 set 的 reps + stats |
| `collect_instrument` | P1 | 成套快照 |
| `get_timeseries_latest` | A1 | 最新采集点 |
| `list_timeseries_metrics` | A1 | 可用 metric 列表 |
| `query_timeseries` | A1 | 统计 + 样本 + 曲线摘要 |
| `get_timeseries_sample` | A1 | 指定 sample 全部点 |
| `post_operator_notice` | D1 | 推送到 Bridge 操作员对话框 |
