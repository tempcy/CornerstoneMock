# 仪器知识库（C2 草稿）

用 **MarkItDown** 将说明书 / PPT / Word / PDF / Excel 转为 Markdown，供 BaoClaw 检索增强。  
对装配图 / 线路图等**大图页**，另按章渲染 PNG 并生成图录（纯文字 MD 读不出图）。

## 目录

| 路径 | 用途 |
|------|------|
| `raw/` | 原始文档（勿改；可按仪器分子目录，如 `raw/CS844/`） |
| `markdown/` | 转换后的 `.md`（可入库 / 挂载给智能体阅读） |
| `figures/` | 大图 PNG：`figures/<手册文件名>/<章slug>/fig_X_Y.png` |
| `catalogs/` | 按章图录 + [`INDEX.md`](catalogs/INDEX.md) 总索引 |

## 环境

本机已用 Python 3.14 安装：

```text
markitdown==0.1.6
+ mammoth / openpyxl / python-pptx / pdfminer-six / pdfplumber …
pypdfium2 / Pillow   # 大图渲染
```

> 注意：`markitdown[all]` 在 Python 3.14 上会因依赖版本钉死失败；当前为核心包 + 文档类依赖，**不含** YouTube 等无关 extra。

验证：

```bash
py -3.14 -m markitdown --help
py -3.14 -c "from markitdown import MarkItDown; MarkItDown(); print('ok')"
```

## 单文件转换

```bash
py -3.14 -m markitdown path\to\manual.pdf -o knowledge/markdown/manual.md
```

或批量：

```bash
py -3.14 knowledge/convert_docs.py
```

## 大图按章图录（CS844 手册）

全文扫描稀疏文字页 / 图题主导页，渲染 PNG，并写 `catalogs/chNN_figures.md`：

```bash
cd knowledge
py -3.14 extract_figures_by_chapter.py                 # 检测 + 渲染
py -3.14 extract_figures_by_chapter.py --catalog-only  # 只重建图录
py -3.14 extract_figures_by_chapter.py --dry-run       # 只检测
```

当前 CS844 v3.3（578 页）约检出 **186** 页大图（第 10 章装配图最多）。入口：[`catalogs/INDEX.md`](catalogs/INDEX.md)。

## 与智能体

- 转换后的 Markdown 可供 BaoClaw 用 `file_reader` / 检索 skill 阅读。
- 装配 / 布线 / 大插图：先查章图录，再打开 `figures/` 下对应 PNG（必要时视觉读图）。
- 实时仪器数据仍走 `cornerstone_instrument` → 编排 `8090`，**不要**把手册当实时状态。
