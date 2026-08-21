---
name: cornerstone_instrument
description: "Query LECO Cornerstone gas analyzers via company orchestrator P0/P1 tools: list_instruments, get_instrument_status, get_analysis_sets, get_set_reps, collect_instrument. Use when user asks about instrument online/status, analysis sets, reps/stats, troubleshoot snapshots, or lab instrument inventory."
metadata:
  version: "0.2.0"
  qwenpaw:
    emoji: "🔬"
    requires: {}
---

# Cornerstone 仪器查询（C1 P0 + P1）

当用户询问 **仪器是否在线 / 能否跑样 / 分析结果 / 某次 set 的重复性 / 排故用成套快照** 时使用本技能。

**不要**直连仪器机 Bridge REST（`127.0.0.1:8081`）或工控网 `<L1_HOST>:8080`。对话侧只调编排 DNAT。现场基址见本机 `BaoClaw/skill.json` 的 `orchestrator_base_url`（该文件不入库）；下文示例用占位符 `http://<PF_SENSE>:8090`。

完整 schema 见同目录 [`baoclaw-tools.v1.json`](baoclaw-tools.v1.json)（与仓库 `CornerstoneAgent/schemas/baoclaw-tools.v1.json` / [INTERFACE.md](../../../CornerstoneAgent/INTERFACE.md) 对齐）。边缘 Agent 版本：**0.2.0**。

## 前置

编排需在二炼钢 Agent 主机运行（生产）或本机开发机：

```bash
# 健康检查（公司网）
curl -s http://<PF_SENSE>:8090/health
# 期望：ok=true, service=cornerstone-agent-orchestrator, version=0.2.0
```

## Tool 调用方式

对每个 tool：`POST http://<PF_SENSE>:8090/v1/tools/{name}`，JSON body = parameters。  
（Windows 下示例用 `^` 续行；Linux/macOS 用 `\`。）

### 1. list_instruments（P0）

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/list_instruments ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"online_only\": true}"
```

记下返回的 `lab_id` / `instrument_id`，后续必填。二炼钢常见：`GC6` `GC7` `GC8` `GO6` `GO7` `GO9`。

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

需要上一步拿到的 `set_key`：

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/get_set_reps ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"set_key\":\"YOUR_SET_KEY\",\"include_stats\":true}"
```

### 5. collect_instrument（P1）

成套只读快照（比多次 get_* 更重）。profile：

| profile | 用途 |
|---------|------|
| `status_light` | status + status-check |
| `analysis_recent` | status + sets + set-stats |
| `troubleshoot` | status / system-parameters / status-check / counters / set-stats |
| `custom` | 自选白名单别名：`status` `system-parameters` `counters` `automation-status` `instrument-info` `status-check` `digital-io` `ambients` `sets` `set-stats` `set-reps` `queue` |

```bash
curl -s -X POST http://<PF_SENSE>:8090/v1/tools/collect_instrument ^
  -H "Content-Type: application/json" ^
  -d "{\"lab_id\":\"lab-2lg\",\"instrument_id\":\"GC8\",\"profile\":\"troubleshoot\"}"
```

关注 `result.data.snapshot_id` 与各 endpoint 结果。

## CLI 等价（无 HTTP 时，在 Agent 主机）

```bash
python -m cornerstone_agent tool list_instruments
python -m cornerstone_agent tool get_instrument_status
python -m cornerstone_agent tool get_analysis_sets --number 5
python -m cornerstone_agent tool get_set_reps --set-key YOUR_SET_KEY
python -m cornerstone_agent tool collect_instrument --profile status_light
```

## 规则

1. **先 list（或会话已绑定仪器），再查状态/分析。** 默认 `lab-2lg` / `GC8`，用户点名其它仪器则改 `instrument_id`。
2. 写仪器（发样、改方法）**不在本技能范围**；引导用户用 Queue / Web。
3. 若返回 `agent_offline` / `instrument_not_found` / bridge 失败：说明编排/注册表/边缘链路问题，**不要编造**仪器数据。
4. 回复用户时引用 `result.data` 中的结构化字段，并说明来自哪台 `instrument_id`。
5. **P2 未实现**：不要调用 `eval_instrument_rules` / `tail_instrument_logs`（无 HTTP tool）。
6. 身份与边界见工作区 `PROFILE.md` / `SOUL.md` / `AGENTS.md`；连接笔记见 `MEMORY.md`。

## 默认仪器（二炼钢）

会话未指定时可用：`lab_id=lab-2lg`，`instrument_id=GC8`。

## Tool 一览

| name | 优先级 | 用途 |
|------|--------|------|
| `list_instruments` | P0 | 在线/注册仪器清单 |
| `get_instrument_status` | P0 | 状态 / 可选 status-check |
| `get_analysis_sets` | P0 | 近期分析集 |
| `get_set_reps` | P0 | 指定 set 的 reps + stats |
| `collect_instrument` | P1 | 成套快照（profile → snapshot_id） |
