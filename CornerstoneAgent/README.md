# CornerstoneAgent

仪器驻场**边缘执行服务** + C1 编排（P0/P1 tools）+ **A1 长周期 SQLite 时序**。

- 架构：[AGENT.md](AGENT.md)、[ENTERPRISE.md](ENTERPRISE.md)
- **接口表**：[INTERFACE.md](INTERFACE.md)
- **已实现功能测试**：[TESTING.md](TESTING.md)（二炼钢 GC8 Bridge `<GC8_IP>:8080` / CLI `54321`）
- 本地 LLM 开发：[LOCAL-DEV.md](LOCAL-DEV.md)
- **二炼钢网络 / pfSense**：[docs/二炼钢实验室网络.md](../docs/二炼钢实验室网络.md)

## C1 快速开始

```bash
cd CornerstoneAgent
python3 -m pip install -e .
# 使用本机 cornerstone-agent.config.json（含现场 Bridge 地址，不入库；示例见 *.example.json）
python3 -m cornerstone_agent run
```

| 地址 | 说明 |
|------|------|
| `http://<GC8_IP>:8080` | GC8 Bridge REST（仪器数据面；见网络说明 §3.1） |
| `<GC8_IP>:54321` | GC8 CLI TCP 网关（连通性 / 排故） |
| `http://127.0.0.1:8090/health` | 编排健康检查 |
| `http://127.0.0.1:8090/ui/` | 运维台（状态 + 时序 + 配置/采集任务） |
| `GET /api/ui/overview` | 状态总览 JSON |
| `GET /api/ui/config` | 当前配置（采集任务可写） |
| `GET /api/ui/timeseries` | A1 时序查询（`instrument_id`、`hours`、可选 `metric` / `job_id`） |
| `GET /api/ui/timeseries/export.csv` | 时序 CSV 导出 |
| `POST /api/ui/timeseries/collect-once` | 立即采集一票写入 SQLite（可选 `job_id`） |
| `PUT /api/ui/timeseries/jobs` | 保存采集任务并热加载（不改 `instruments[]`） |
| `POST /api/ui/instruments/ping` | 探测单台 Bridge |
| `POST /v1/agents/register` | Agent 注册 |
| `POST /v1/agents/heartbeat` | 心跳 |
| `GET /v1/instruments` | 注册表仪器列表 |
| `POST /v1/tools/{name}` | P0/P1 tools（见 INTERFACE） |

默认 `embed_local_agent=true`：进程内按配置 identity 自动 register + 周期 heartbeat，并对配置中的 Bridge（`<GC8_IP>:8080`）执行 job。

BaoClaw 技能：`BaoClaw/skills/cornerstone_instrument/`（schema 与 `schemas/baoclaw-tools.v1.json` 一致）。

冒烟：`python3 scripts/smoke_c1.py`（步骤详见 [TESTING.md](TESTING.md)）

**当前阶段**：C1 步骤 1–4 已落地（含 P1 `collect_instrument`）；**A1 长周期 SQLite 时序已落地**；规则引擎（A2）与 P2 `rules_eval` / `log_tail` 未启动。
