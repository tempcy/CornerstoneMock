---
summary: "Agent 长期记忆 — Cornerstone 工具与现场经验"
read_when:
  - 调用仪器工具前
  - 排故 / 连不上仪器时
---

## 工具设置

### Cornerstone 仪器编排（C1，Agent **0.2.0**）

- Orchestrator（公司侧入口）：`http://<PF_SENSE>:8090`（pfSense DNAT → DMZ `<AGENT_HOST>:8090`）
- 健康检查：`GET /health` → `cornerstone-agent-orchestrator` / `version: 0.2.0`
- 边缘主机：`<AGENT_HOST>`（Win10 IoT）；代码 `C:\CornerstoneMock\CornerstoneAgent`
- 持久化：计划任务 `CornerstoneAgent-Orchestrator`（登录自启，`--host 0.0.0.0 --port 8090`）
- Skill：`cornerstone_instrument`
- Tool schema：`skills/cornerstone_instrument/baoclaw-tools.v1.json`（与仓库 `CornerstoneAgent/schemas/baoclaw-tools.v1.json` 对齐）
- 本机开发机起编排：`python -m cornerstone_agent run`（目录 `CornerstoneAgent/`）；生产以现场 Agent 为准

### 公司对话入口（智宝，原 BaoClaw 品牌）

详细存档见仓库 **[docs/智宝.md](../docs/智宝.md)**（门户、SSO、实例、配置变更/迁移、chat-tasks、P0/P1 实测）。摘要：

| 项 | 值 |
|----|-----|
| 门户 | `http://<ZHIBAO_HOST>:8015/`（智宝数字助手） |
| 健康 | `GET /actuator/health` → `UP` |
| 认证 | 公司 SSO（`eplattest.baogang.info`）；**勿用** `/api/auth/mock-login` |
| 默认实例 | `qwenpaw-mfg-025`（制造管理部-检化验（一）-个性化智能体）→ `http://<QWENPAW_HOST>:32199` |
| 另可访问 | `qwenpaw-mfg-008` → `http://<QWENPAW_HOST>:32171` |
| Cornerstone 助手 | `agentId=CornerstoneMock`（显示名：Cornerstone气体分析仪助手） |
| 对话 API | `POST /api/chat-tasks/{instanceCode}`；`content` 须为 `[{type:text,text:...}]` |
| 配置 | `GET/POST .../agent-configuration/...`；Skill/Agent 变更已验证；迁移 zip 包 version 2 |
| 仪器调用 | 仍只打编排 DNAT `http://<PF_SENSE>:8090`，禁止直连 Bridge |

2026-08-16：经智宝助手对 `lab-2lg` 六台跑通 P0/P1（含 collect 四档），任务示例 `f6a2bb71…` → SUCCEEDED / 全 PASS。

### 二炼钢（lab 英文名 `2lg`）

默认对话仪器仍为 **GC8**；同 lab 六台均已在 P0/P1 矩阵验证可查（2026-08-16）。

| 字段 | 值 |
|------|-----|
| `lab_id` | `lab-2lg` |
| `instrument_id` | `GC8`（默认）；亦可 GC6 / GC7 / GO6 / GO7 / GO9 |
| 默认 `agent_id` | `agent-2lg-gc8`（多仪器注册时为 `agent-2lg-<id小写>`） |
| 编排 DNAT | `http://<PF_SENSE>:8090`（Agent `0.0.0.0:8090`；`instruments[]` 六台嵌入式心跳） |
| 机型（GC8） | CS844 |

| 仪器 | Bridge（Agent 侧） | 远程选项 | 查环境/结果 |
|------|---------------------|----------|-------------|
| GC6 | `http://<GC6_IP>:8080` | RQ | ✅ |
| GC7 | `http://<GC7_IP>:8080` | RQ（8080 已放行） | ✅ |
| GC8 | `http://<GC8_IP>:8080` | RQ | ✅ 默认 |
| GO6 | `http://<GO6_IP>:8080` | 许可多为 RSL+NQ；**实测 RQ/RC 查询可用** | ✅ |
| GO7 | `http://<GO7_IP>:8080` | **RC（含 RQ）** | ✅ |
| GO9 | `http://<GO9_IP>:8080` | 查询可用 | ✅ |

### 已实现工具（P0 + P1）

| 优先级 | BaoClaw tool | Agent job | 说明 |
|--------|--------------|-----------|------|
| P0 | `list_instruments` | （编排本地） | 注册表清单 |
| P0 | `get_instrument_status` | `get_status` | 在线/业务态；可选 status-check |
| P0 | `get_analysis_sets` | `get_sets` | 近期 sets |
| P0 | `get_set_reps` | `get_set_reps` | 指定 set 的 reps/stats |
| P1 | `collect_instrument` | `collect` | profile → acquisition-snapshot + `snapshot_id` |

`collect` profile：`status_light` · `analysis_recent` · `troubleshoot` · `custom`（白名单 endpoint 别名）。

写仪器（发样等）不开放；引导 Queue / Web。

### 尚未实现（P2，勿当已可用）

- `eval_instrument_rules` ↔ `rules_eval`
- `tail_instrument_logs` ↔ `log_tail`
- 告警上行 `alert`

（注册表 `capabilities` 可能仍声明 `rules_eval`/`log_tail`，但无对应 `/v1/tools/*`。）

## 经验教训

- REST 对外应走 **Web 8080**，不要要求防火墙开 8081（Bridge 绑 127.0.0.1 是刻意的）。
- 本机再起 Bridge 直连 `:12345` 会与 GC8 上已登录 Bridge 冲突：Logon 常 `ErrorCode=2`，随后 RQ `ErrorCode=5`。
- TCP `54321` 需 Bridge 允许登录/查询白名单；放行后 `Version` / `Sets` 可复用账号。
- 2026-07-24：8080 DNAT 通后，经编排 P0 全链路成功（status / sets / set-reps）。
- GO6 许可界面虽常显示无 RC，实测仍可发 RQ/RC 类查询；与 GO7 一样可作为查询目标。
- ON836（GO 系列）XML sets 可能为小写 `<set>`/`<field>`；Bridge 解析需大小写不敏感（已修）。
- 2026-08-16：二炼钢 Agent 编排必须绑 `0.0.0.0:8090`；SSH 会话里 `Start-Process`+重定向句柄会随会话退出带走进程 → 用计划任务持久化。
- 2026-08-16：`lab-2lg` 六台 × P0/P1（含 collect 四档）矩阵 **43/43 通过**；结果脚本可参考工作区 `scripts/test_p0_p1_lab2lg.py`（若已同步）。
- 2026-08-16：智宝门户/SSO/配置变更与迁移、chat-tasks 联调记录已存档 **`docs/智宝.md`**；经智宝 `CornerstoneMock` 对话实测六台亦全 PASS。
- 小模型若只会乱 `curl`，优先让其严格按 skill 示例调用；编排 URL 固定为 DNAT `<PF_SENSE>:8090`。

## 架构一句话

智宝/BaoClaw = 对话大脑；CornerstoneAgent = 执行面；Bridge = 仪器数据面。三者不要角色颠倒。

## 知识库（MarkItDown）

- 工具：`markitdown==0.1.6`（Python 3.14 全局环境已装；文档类依赖已补）
- 目录：`knowledge/raw/` → `py -3.14 knowledge/convert_docs.py` → `knowledge/markdown/`
- CLI：`py -3.14 -m markitdown file.pdf -o knowledge/markdown/file.md`
- 大图：`knowledge/extract_figures_by_chapter.py` → `figures/<手册名>/<章>/` + `catalogs/`（CS844 约 186 页；索引 `catalogs/INDEX.md`）
- 说明：`knowledge/README.md`；依赖清单：`knowledge/requirements-markitdown.txt`
