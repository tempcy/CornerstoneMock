# 仪器知识库 — 入库工作流与使用约定

仓库**主要保存工作流**（本 README、转换脚本、依赖清单、目录骨架）。  
原始 PDF、转换后的 Markdown、大图 PNG 体积大，默认本机保留（见根目录 `.gitignore`）；需要入库时再显式放开。

## 实用做法（必遵）

| 角色 | 做什么 |
|------|--------|
| **模型** | 按类别目录检索：正文 Markdown、图录标题/编号、案例摘要；给出章节与图号定位 |
| **人** | 打开对应 PNG **详细读图**（装配爆炸图、管路/气路、原理图、电路图） |
| **不要** | 默认把全部大图塞进多模态；不要把手册当实时仪器状态（实时数据走 `cornerstone_instrument`） |

流程建议：用户问题 → 定类别（说明书 / 手顺 / 案例 / 日历）→ 检索正文或图录 → 引用 Figure/章节 → **需要细看图时交给人** → 结案反馈（见 [schemas/FEEDBACK.md](schemas/FEEDBACK.md)）。

## 目录结构（按内容分类）

```text
knowledge/
  raw/                      # 原始文件（勿改）
    manuals/                # 说明书（CS844 / ON836 …）
    procedures/             # 手顺书 / SOP（预留）
    fault_cases/            # 故障案例（JSON + 示例）
    anomaly_calendar/       # 设备异常日历
  schemas/                  # fault-case.v1、FEEDBACK 约定
  feedback/                 # Plan A：使用反馈 JSONL（公司侧，入 Git）
  feedback_tool.py          # append / list / promote CLI
  markdown/                 # MarkItDown 转换结果（镜像 raw 相对路径）
    manuals/
    procedures/
    fault_cases/
    anomaly_calendar/
  figures/manuals/<手册id>/ # 按章大图 PNG
  catalogs/
    INDEX.md                # 全部手册图录入口
    manuals/<手册id>/       # 每手册 INDEX + chNN_figures.md
    procedures/ | fault_cases/ | anomaly_calendar/
```

| 路径 | 入库？ | 用途 |
|------|--------|------|
| `README.md`、`*.py`、`requirements-*.txt`、分类 README | 是 | 工作流与约定 |
| `feedback/*.jsonl` | 是 | 使用反馈（Plan A） |
| `raw/**` PDF/Office | 否（本机） | 原文 |
| `markdown/**` | 否（本机） | 检索用正文 |
| `figures/**` | 否（本机） | 大图 PNG |
| `catalogs/**` | 建议本机；索引可同步 | 图录（模型检索用） |

## 已处理手册

| 仪器 | 源 PDF（`raw/manuals/`） | Markdown | 图录 |
|------|--------------------------|----------|------|
| CS844（碳硫，GC） | `844 Series … Instruction Manual … June 2024.pdf` | `markdown/manuals/CS844_Instruction_Manual_v3.3_2024-06.md` | `catalogs/manuals/CS844_Instruction_Manual_v3.3_2024-06/` |
| ON836（氧氮氢，GO） | `836 Series … Instruction Manual … June 2024.pdf` | `markdown/manuals/ON836_Instruction_Manual_v3.3_2024-06.md` | `catalogs/manuals/ON836_Instruction_Manual_v3.3_2024-06/` |

## 环境

```bash
cd BaoClaw/knowledge
python -m pip install -r requirements-markitdown.txt
```

> 不要用 `markitdown[all]`（部分 Python 版本会因无关 extra 失败）。

## 入库步骤

1. 把文件放到对应 `raw/<类别>/`。
2. 转 Markdown：

```bash
cd BaoClaw/knowledge
python convert_docs.py
```

3. 说明书大图图录（844/836 系列页脚可识别）：

```bash
python extract_figures_by_chapter.py ^
  --pdf "raw/manuals/836 Series OxygenNitrogenHydrogen Analyzer Instruction Manual Version 3.3.x June 2024.pdf" ^
  --toc-md markdown/manuals/ON836_Instruction_Manual_v3.3_2024-06.md ^
  --doc-id ON836_Instruction_Manual_v3.3_2024-06
```

仅重建图录不重渲图：加 `--catalog-only`。

## 故障案例（fault_cases）

- Schema：`schemas/fault-case.v1.json`；示例：`raw/fault_cases/examples/*.json`
- 状态：`draft` → `verified`（审核后检索）→ `deprecated`
- 权重：**verified 案例 > 异常日历原始条目 > 手册通用描述**

## 使用反馈（C2+ · Plan A）

- **存储**：`feedback/YYYY-MM.jsonl` + `promote_log.jsonl`（**公司侧 BaoClaw 工作区**，不入实验室编排）
- Schema：`CornerstoneAgent/schemas/kb-feedback.v1.json`
- CLI：`py -3 feedback_tool.py append|list|promote`（见 [`feedback/README.md`](feedback/README.md)）
- 流程：[`schemas/FEEDBACK.md`](schemas/FEEDBACK.md)
- 实验室仅回传 `snapshot_id` / 时序摘要；Bridge ack 整理为 `notice_ack` 后 append 到本目录

## 与智能体

- 对话侧优先读 `fault_cases`（verified）→ `anomaly_calendar` → `catalogs/` 图录 + `markdown/` 正文。
- 装配 / 布线：给出图录链接与路径，**请人打开 PNG**。
- 实时状态 / 时序 / 分析结果：走编排 tools，不查手册代替。
