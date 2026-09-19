# 故障案例（fault_cases）

典型故障与已验证处置记录。用于智能体检索增强（C2+），权重高于历史日历原始条目。

## 文件约定

| 路径 | 说明 |
|------|------|
| `raw/fault_cases/examples/*.json` | 结构化案例（JSON，符合 schema） |
| `raw/fault_cases/<source>.pdf` 等 | 原始材料（可选） |
| `markdown/fault_cases/<case_id>.md` | 检索用摘要（可由脚本从 JSON 生成） |
| `schemas/fault-case.v1.json` | JSON Schema |

## 案例状态

| `status` | 含义 |
|----------|------|
| `draft` | 来自反馈/日历，待专家审核 |
| `verified` | 已审核，可进入检索 |
| `deprecated` | 机型下线或处置已过时，保留追溯 |

## 入库流程

1. **来源**：对话反馈结案、运维建议 ack、日历/周报提炼、现场手顺整理。
2. **编写**：复制 `examples/` 下模板，填写 `symptoms` → `diagnostics` → `resolution`。
3. **校验**（可选）：

```bash
cd BaoClaw/knowledge
py -3 -c "import json, pathlib; from jsonschema import Draft202012Validator as V; s=json.loads(pathlib.Path('schemas/fault-case.v1.json').read_text()); [V(s).validate(json.loads(p.read_text())) or print('OK', p.name) for p in pathlib.Path('raw/fault_cases/examples').glob('*.json')]"
```

> 无 `jsonschema` 时以字段完整性人工检查即可。

4. **生成 Markdown**（后续脚本）：`python build_fault_case_md.py`（待实现）。
5. **检索**：对话侧优先 `verified` 案例，再查 `anomaly_calendar` 与手册。

## 与反馈闭环

- 每条 `verified` 案例可在 `metadata.feedback_ids` 挂接 `kb-feedback.v1` 记录。
- 对话中引用案例时返回 `case_id`，便于操作人员反馈「有用/无用」。

## 示例

见 [`examples/`](examples/)：

| case_id | 主题 |
|---------|------|
| `fc-gc8-sulfur-tail-oxygun-001` | 硫拖尾 / 氧枪流量不足 |
| `fc-gc8-autoloader-stall-001` | 自动进样机械手卡死 |
| `fc-go6-furnacehead-leak-001` | 炉头漏气 / 动力气压力 |

Schema 字段说明见 [`../schemas/fault-case.v1.json`](../schemas/fault-case.v1.json)。
