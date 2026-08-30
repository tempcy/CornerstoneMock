# 知识库使用反馈（Plan A）

反馈数据存放在 **BaoClaw 工作区 / 仓库**（公司侧），**不**写入实验室编排 SQLite。

| 路径 | 说明 |
|------|------|
| `feedback/YYYY-MM.jsonl` | 按月追加的反馈事件（一行一条 JSON） |
| `feedback/promote_log.jsonl` | 审核晋升记录（feedback_id → fault_case） |
| `../raw/fault_cases/` | 晋升后的结构化案例 |

实验室编排（8090）仅提供仪器证据：`snapshot_id`、时序摘要；反馈由智宝会话或人工 CLI 写入本目录。

## 快速使用

```bash
cd BaoClaw/knowledge

# 追加一条反馈（自动生成 feedback_id）
py -3 feedback_tool.py append --file feedback/_example_payload.json

# 列出待审核（有 proposed_case 或结案类且未 promote）
py -3 feedback_tool.py list --queue

# 晋升到 fault_cases
py -3 feedback_tool.py promote --feedback-id fb-20260830-demo-001 --by 浦辰雨
```

## JSONL 格式

每行符合 [`CornerstoneAgent/schemas/kb-feedback.v1.json`](../../../CornerstoneAgent/schemas/kb-feedback.v1.json)。

必填：`schema_version`、`feedback_id`、`kind`、`source.channel`、`created_at`。

## 三类写入入口

| 入口 | `source.channel` | 做法 |
|------|------------------|------|
| 智宝对话结案 | `zhibao` | 会话插件或人工整理 JSON → `append` |
| Bridge 建议 ack | `bridge_ui` | 从 Agent 审计整理为 `notice_ack` → `append` |
| 专家导入 | `manual_import` | 直接 `append` 或写 `fault_cases` |

## 与知识库关系

```
feedback/*.jsonl  →  promote  →  raw/fault_cases/<case_id>.json
                              →  promote_log.jsonl（追溯）
```

检索权重：**verified fault_cases > anomaly_calendar > manuals**；`useful_refs` / `outcome=wrong` 留在反馈 JSONL 供后续调权。

## Git 同步

- `feedback/*.jsonl` 与 `promote_log.jsonl` **纳入 Git**（体量小、需协作审核）。
- 智宝工作区同步时一并推送仓库 `BaoClaw/knowledge/feedback/`。

详细流程见 [`../schemas/FEEDBACK.md`](../schemas/FEEDBACK.md)。
