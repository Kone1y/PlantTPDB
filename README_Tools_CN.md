# PTDB 分析工具模块

本文档介绍 PTDB 平台的三个主要分析工具：**系统发育分析（Phylogenetic Analysis）**、**进化 / 序列相似性分析（Evolution / Sequence Identity）** 以及 **基因家族扩张收缩分析（Gene Family Expansion & Contraction）**。

---

## 1. 系统发育分析（Phylogenetic Analysis）

**页面：** `view/index/phylogenetic.html`

### 概述

对用户选择的转运蛋白基因执行最大似然（ML）系统发育推断。分析流程固定：先使用 **MAFFT** 进行多序列比对，再使用 **FastTree (v2.1.11)** 构建系统发育树。用户可在该流程中选择氨基酸替代模型和位点速率异质性模型。

### 前端展示

- **输入表单：** TC 编号（如 `2.A.45`）、物种选择（多选，最多 5 个物种）、替代模型（JTT / WAG / LG）、速率异质性模型（CAT / Gamma）。
- **基因列表表格：** Bootstrap Table 展示匹配的蛋白（超家族名、家族名、物种、蛋白 ID、TC 编号、Pfam）。用户可通过复选框选择特定行，然后点击 **Submit Selected Rows** 触发分析。
- **系统发育树与跨膜螺旋图：** 左侧为 D3.js 系统发育树，右侧为基因结构图，展示从 DeepTM GFF 注释解析的跨膜螺旋位置。
- 树使用 `d3.phylogram.js` 渲染，基因结构使用 `d3.struct.js` 渲染。

### 数据流程

1. 用户提交 TC 编号并选择物种。
2. **`Table::get_ptdb_seq_identity_data()`** — 查询 `ptdb_tc_info` 获取家族信息，然后查询各物种表（`ptdb_{speID}_protein_annotations`）检索匹配的蛋白。
3. 用户选择蛋白行并点击提交。
4. **`Php::get_ptdb_phylogenetic_data()`** — 从各物种表中获取蛋白序列（`pep`）和跨膜注释（`DeepTM_gff`），然后：
   - 运行 **MAFFT**（`mafft --quiet --maxiterate 1000`）进行多序列比对。
   - 在比对结果上运行 **FastTree**（`FastTree [-wag|-lg] [-gamma]`）构建 ML 树。
   - 返回 Newick 格式的树字符串和蛋白结构域注释。
5. 前端解析 Newick 树和 DeepTM 注释，渲染 D3 系统发育树和跨膜螺旋图。

### 使用的生物信息学工具与参数

| 步骤 | 工具 | 参数 |
|------|------|------|
| 序列比对 | MAFFT | `--quiet --maxiterate 1000` |
| 建树 | FastTree v2.1.11 | `-wag` / `-lg`（WAG 或 LG 模型；JTT 为默认），`-gamma`（Gamma 速率；CAT 为默认） |

---

## 2. 进化 / 序列相似性分析（Evolution / Sequence Identity）

**页面：** `view/index/evolution.html`

### 概述

展示指定 TC 编号在所选物种中的同源基因信息，包括跨膜螺旋可视化和多序列比对结果。支持单物种和多物种视图。

### 前端展示

- **输入表单：** TC 编号（如 `2.A.45`）、物种选择（多选）。
- **基因列表表格：** Bootstrap Table，列与系统发育分析工具相同。用户选择行后点击 **Submit Selected Rows**。
- **跨膜螺旋信息：** 为每个选中的蛋白渲染 HTML5 Canvas 图，展示 TM 螺旋位置（来自 DeepTM 预测）。螺旋以蓝色矩形绘制并带编号标签。悬停时高亮显示螺旋并弹出提示框，显示位置/长度信息。
- **多序列比对序列信息：** 以等宽预格式化文本块展示原始 CLUSTAL 格式的多序列比对结果。
- **树与结构图**（仅单物种）：D3.js 系统发育树配合基因结构和 Pfam 结构域可视化。
- **多序列比对查看器（MSA Viewer）：** 全功能的 MSA 查看器，提供三种视图模式 —— **msa view**（含一致性/缺失统计的比对视图）、**image view**（可配置着色方案的彩色比对图）、**stats view**（缺失/一致性分布、成对一致性热图）。

### 数据流程

1. 用户提交 TC 编号并选择物种。
2. **`Table::get_ptdb_seq_identity_data()`** — 与系统发育分析工具相同：查询 `ptdb_tc_info` 和各物种注释表检索匹配的蛋白。
3. 用户选择蛋白行并点击提交。
4. **`Table::get_seq_trans_multi_data()`** — 获取选中蛋白的蛋白序列（`pep`）和跨膜注释（`DeepTM_gff`）。
5. 前端使用 Canvas 渲染 TM 螺旋图，并从序列生成 FASTA 数据。
6. **`Select::get_multi_alignment_seq_data()`** — 接收 FASTA 数据，运行 **MAFFT**（`mafft --auto --clustalout`）进行比对，返回解析后的序列和原始 CLUSTAL 输出。
7. 前端展示原始 CLUSTAL 比对文本。
8. 此外，**`Php::evo_data()`** — 单物种时查询预计算的树（`tree` 表）、结构（`struct_json`）和 MSA 数据（`multiseq` 表）。多物种时查询跨物种基因映射（`spe_gene_information`、`seq` 表），实时运行 MAFFT，将结果输入 MSA Viewer。

### 使用的生物信息学工具与参数

| 步骤 | 工具 | 参数 |
|------|------|------|
| 序列比对 | MAFFT | `--auto --clustalout`（来自 `get_multi_alignment_seq_data`）；`--quiet --maxiterate 1000`（来自 `evo_data`，多物种分支） |

### 与系统发育分析工具共享的后端接口

- `Table::get_ptdb_all_spe()` — 填充物种下拉选择框。
- `Table::get_ptdb_seq_identity_data()` — 按 TC 编号检索蛋白注释。
- 两个工具均查询相同的各物种表（`ptdb_{speID}_protein_annotations`）和 `ptdb_tc_info`。

---

## 3. 基因家族扩张收缩分析（Gene Family Expansion & Contraction）

**页面：**
- `view/index/gf_contraction_expansion_submit.html` — 提交表单页面。
- `view/index/gf_contraction_expansion.html` — 结果展示页面。

### 概述

使用 **CAFE5** 分析所选植物物种间的基因家族扩张和收缩。用户选择物种并提供邮箱地址。工具首先同步生成基因家族计数矩阵，然后将完整的 CAFE5 分析流程作为异步后台任务提交。结果通过邮件发送。

### 前端展示

**提交页面（`gf_contraction_expansion_submit.html`）：**
- **输入表单：** 物种多选（默认 19 个代表性物种，按 BUSCO 完整度 >= 90% 过滤）、邮箱输入。
- **初步结果（提交后立即展示）：**
  - **基因家族计数表格：** Bootstrap Table 展示各物种的家族计数，含物种特异性指数（τ）列。τ = Σ(1 - xᵢ/xₘₐₓ) / (n-1)，范围 0（广泛分布）到 1（高度特异）。表格支持分页、搜索、排序和 Excel 导出。
  - **基因家族计数热图：** ECharts 热图可视化，配有家族筛选侧边栏。颜色范围：深蓝 (#355F8D) → 绿色 (#2CA981) → 黄色 (#F1E628)。
  - **物种特异性指数（τ）柱状图：** ECharts 横向柱状图，展示选定家族在各物种中的原始计数或相对特异性。按排名（top/mid/low）着色。
- **CAFE 分析状态消息：** 显示任务是否使用预计算数据（19 个默认物种）还是自定义分析。

**结果页面（`gf_contraction_expansion.html`）：**
- 包含提交页面的所有可视化（表格、热图、τ 柱状图）。
- **祖先状态重建树（ASR Tree）：** D3.js 渲染的系统发育树，分支按扩张（红色）和收缩（蓝色）着色。p < 0.05/0.01/0.001 的分支标注 */\*\*/\*\*\*。悬停显示分支长度、p 值和变化数量。
- **基因家族系统发育树：** SVG 图像查看器。用户选择一个家族（TC 编号或 Symbol），加载对应的预渲染 SVG 树。

### 数据流程

1. **`OtherAaddition::get_tc_symbol_list()`** — 从平面文件读取预定义的 TC 编号列表（156 个家族）或 Symbol 列表。
2. **`Table::get_ptdb_all_spe_by_busco()`** — 查询 `ptdb_all_species_by_busco`，过滤 BUSCO 完整度 >= 90% 的物种。
3. 提交时：
   - **预计算情况**（19 个默认物种）：前端直接从静态服务器获取预计算的 TSV 和编号列表文件。
   - **自定义情况：** **`OtherAaddition::run_cafe_matrix_only()`** — 同步运行三个 `planttpdb-cafe` 子命令（`init` → `prepare-input` → `make-cafe-matrix`）生成基因家族计数矩阵。返回 TSV 数据和家族 ID 供即时展示。
4. **`OtherAaddition::run_cafe_analysis()`** — 将完整 CAFE5 流程作为异步后台任务提交：
   - 创建监控脚本，每 20 秒轮询 `job.status.json`，最长 48 小时。
   - 发送邮件通知（任务已提交、任务完成/失败）。
   - 结果存储在服务器上，通过邮件中的 URL 链接访问。
   - 预计算结果立即提供永久 URL。
   - 结果 14 天后自动清理。
5. 结果页面（`gf_contraction_expansion.html`）根据 URL 参数 `?variable={label}` 从静态 URL 加载数据，包括 TSV、家族编号列表、ASR 树文件（NEXUS 格式）以及分支概率/变化统计数据。

### 使用的生物信息学工具与参数

| 步骤 | 工具 | 参数 / 说明 |
|------|------|-------------|
| 物种过滤 | BUSCO | BUSCO 完整度 >= 90% |
| 物种树构建 | IQ-TREE | 异步 CAFE5 流程的一部分 |
| 分歧时间估算 | MCMCtree (PAML) | 为 CAFE5 构建超度量树 |
| 基因家族分析 | CAFE5 (`planttpdb-cafe`) | 子命令：`init`、`prepare-input`、`make-cafe-matrix`、`run-all` |
| 矩阵生成 | planttpdb-cafe | `init --force` → `prepare-input --force` → `make-cafe-matrix --matrix-type {tc\|symbol} --force` |
| 完整流程 | planttpdb-cafe | `run-all --species {文件} --config {yaml} --outdir {目录} --matrix-type {类型} --list {列表} --label {标签} --force` |

---

## 前端技术栈（所有工具通用）

| 组件 | 技术 |
|------|------|
| CSS 框架 | Bootstrap 3.3.7 |
| 数据表格 | Bootstrap Table（含分页、导出、树形表格扩展） |
| 下拉选择 | Select2 |
| 系统发育树 | D3.js v3 + d3.phylogram.js（系统发育分析工具）；D3.js v3 自定义渲染（ASR 树） |
| 基因结构图 | d3.struct.js |
| 热图与柱状图 | ECharts 5.3.2 |
| 跨膜螺旋图 | HTML5 Canvas（进化分析工具） |
| MSA 查看器 | 自定义 JavaScript（seqlib.js、SequenceLogoDiagramD3.js 等） |
| Excel 导出 | TableExport + XLSX.js + FileSaver.js |
| 数据格式 | TSV 文件、Newick 树字符串、GFF 风格注释（DeepTM）、CLUSTAL 比对、NEXUS（ASR 树） |
