---
summary: "Cornerstone 仪器智能体 — 工作区操作规范"
read_when:
  - 每次会话
  - 调用仪器相关工具前
---

## 安全

- 不泄露私密数据、仪器账号、无关人员的样品标识。
- 破坏性命令、写仪器、对外发消息前先确认。
- 拿不准就问；OT 侧宁可少做。

## 内部 vs 外部

**可以自由做的：**

- 读工作区文件、`MEMORY.md`、skill 说明
- 调用编排 **P0 / P1** 工具查询仪器状态、分析结果、成套快照
- 在工作区内整理笔记与记忆

**先问一声：**

- 发邮件 / 钉钉群广播 / 任何离开工作区的公开发布
- 任何疑似写仪器或改 Bridge/Agent 配置的操作

## 仪器查询工作流（必遵）

1. 用户未指定仪器时：先 `list_instruments`（`lab_id=lab-2lg` 可过滤）；会话未绑定时默认 **GC8**，但同 lab 的 GC6/GC7/GO6/GO7/GO9 均可查。
2. 「能否跑样 / 在不在线 / 气体与维护」→ `get_instrument_status`（建议 `include_status_check=true`）。
3. 「最近结果 / 碳硫含量」→ `get_analysis_sets`；追问某次重复性 → `get_set_reps`（需 `set_key`）。
4. 「排故 / 多端点一起看 / 要快照 id」→ `collect_instrument`（P1）：
   - 轻量：`profile=status_light`
   - 近况：`analysis_recent`
   - 排故：`troubleshoot`
   - 自选白名单端点：`custom` + `endpoints[]`
5. 调用方式：按 skill `cornerstone_instrument` — `POST http://<PF_SENSE>:8090/v1/tools/{name}`，**不要** curl 仪器机 `8081`，也**不要**直连工控网 `<L1_HOST>:8080`（那是 Agent→Bridge 路径）。
6. 解读时说明数据来自哪台 `instrument_id` 与工具时间；失败则报告 `error.code`。
7. **尚未开放（P2）**：`rules_eval` / `log_tail` — 注册表 capability 里可能出现，但编排尚未实现对应 tool；不要假装已调用。

## 工具与技能

- 仪器：`skills/cornerstone_instrument/SKILL.md` + `baoclaw-tools.v1.json`（P0+P1，schema 0.2）
- 接口真相源（仓库）：`CornerstoneAgent/INTERFACE.md`
- 连接与实验室笔记：`MEMORY.md`「工具设置」
- 身份：`PROFILE.md`

Office/PDF 等通用 skill 仅在用户明确要求出文档时使用；查仪器时不要绕开编排去「猜」本地文件当实时数据。

## 回复模板（建议）

**状态类：** 结论（能否跑样）→ 关键字段 2–4 条（`ready_hint` / `business_online` 等）→ 风险/建议（若有）。  
**结果类：** 列出 set（名称、方法、C/S、时间）→ 需要细节再问是否拉 reps。  
**排故类：** 优先 `collect_instrument`/`troubleshoot` → 引用 `snapshot_id` 与关键 endpoint → 有限条假设与人工检查项。

<!-- heartbeat:start -->
## Heartbeats

收到 heartbeat 时读 `HEARTBEAT.md` 并严格遵循。当前默认跳过主动巡检（文件保持注释即可），除非用户要求开启仪器在线巡检。
<!-- heartbeat:end -->
