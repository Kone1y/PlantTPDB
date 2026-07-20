# PTDB — 植物转运蛋白数据库

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20593739.svg)](https://doi.org/10.5281/zenodo.20593739)

一个综合性植物转运蛋白数据库，提供系统分类、进化分析、跨物种比较和在线预测工具。

**网站地址：** [https://yanglab.hzau.edu.cn/ptdb/index/home](https://yanglab.hzau.edu.cn/ptdb/index/home)

## 项目简介

PTDB 整合了来自多个植物物种的转运蛋白信息，主要功能包括：

- **转运蛋白分类**：TC 分类系统、Pfam 结构域、基因家族（ABC、MFS 等）
- **跨物种比较**：共线性分析、系统发育树构建、Ka/Ks 选择压力计算
- **功能注释**：底物搜索、通路映射、文献整合
- **在线工具**：BLAST 序列搜索、转运蛋白预测、基因家族扩张收缩分析

## 分析工具脚本

### 系统发育分析（Phylogenetic Analysis）

基于最大似然法的转运蛋白基因系统发育推断。使用 **MAFFT** 进行多序列比对，再使用 **FastTree (v2.1.11)** 构建系统发育树。用户可选择氨基酸替代模型（JTT / WAG / LG）和位点速率异质性模型（CAT / Gamma）。

```bash
bash Tools/phylogenetic_analysis.sh -i input.fasta -t wag -r gamma -o output/
```

| 参数 | 说明 | 可选值 | 默认值 |
|------|------|--------|--------|
| `-i` | 输入 FASTA 文件 | — | （必填） |
| `-t` | 替代模型 | jtt, wag, lg | jtt |
| `-r` | 速率异质性模型 | cat, gamma | cat |
| `-o` | 输出目录 | — | ./phylogenetic_output |

**依赖工具：** MAFFT、FastTree

**输出文件：**
- `aligned_sequences.fa` — MAFFT 多序列比对结果
- `phylogenetic_tree.nwk` — Newick 格式的最大似然系统发育树

---

### 进化 / 序列相似性分析（Evolution / Sequence Identity）

跨物种同源基因的多序列比对分析。接受多序列 FASTA 文件，运行 **MAFFT** 进行比对，输出 CLUSTAL 格式比对结果及解析后的独立序列。

```bash
bash Tools/evolution_analysis.sh -i input.fasta -o output/
```

| 参数 | 说明 | 可选值 | 默认值 |
|------|------|--------|--------|
| `-i` | 输入 FASTA 文件 | — | （必填） |
| `-o` | 输出目录 | — | ./evolution_output |

**依赖工具：** MAFFT

**输出文件：**
- `alignment.clustal` — CLUSTAL 格式的多序列比对
- `parsed_sequences.fasta` — 解析后的独立序列

---

### 基因家族扩张收缩分析（Gene Family Expansion & Contraction）

使用 **CAFE5** 分析植物物种间基因家族的扩张与收缩。支持两种模式：

**矩阵生成**（同步模式，约 5 分钟）：生成基因家族计数矩阵，不运行完整的扩张收缩分析。

```bash
bash Tools/gene_family_expansion_contraction.sh \
    --species Arabidopsis_thaliana,Oryza_sativa,Glycine_max \
    --matrix-type tc \
    --outdir output/
```

**完整流程**（异步模式，数小时）：运行包含 BUSCO 过滤、IQ-TREE 物种树构建、MCMCtree 年代估算和 CAFE5 扩张收缩分析的完整流程。

```bash
bash Tools/gene_family_expansion_contraction.sh \
    --species Arabidopsis_thaliana,Oryza_sativa,Populus_trichocarpa,Zea_mays \
    --matrix-type tc \
    --outdir output/ \
    --full \
    --email user@example.com
```

| 参数 | 说明 | 可选值 | 默认值 |
|------|------|--------|--------|
| `--species` | 逗号分隔的物种列表（至少 3 个） | — | （必填） |
| `--matrix-type` | 基因家族类型 | tc, symbol | （必填） |
| `--family-list` | 自定义基因家族列表文件 | 文件路径 | （内置列表） |
| `--outdir` | 输出目录 | — | ./cafe_output |
| `--full` | 运行完整异步流程 | — | （关闭） |
| `--email` | 结果通知邮箱 | — | （--full 时必填） |
| `--label` | 自定义任务标签 | — | （自动生成） |

**依赖工具：** planttpdb-cafe（CAFE5 封装工具）；完整模式额外需要 BUSCO、IQ-TREE、MCMCtree (PAML)

**输出文件（矩阵模式）：**
- `results/04_cafe_input/tc.filtered.tsv`（或 `symbol.filtered.tsv`）— 基因家族计数矩阵

---

## 项目结构

```
ptdb/
├── Tools/
│   ├── phylogenetic_analysis.sh             # 系统发育树推断流程
│   ├── evolution_analysis.sh                 # 多序列比对流程
│   └── gene_family_expansion_contraction.sh # CAFE5 基因家族分析流程
├── Readme.md
├── README_CN.md
├── README_Tools.md
├── README_Tools_CN.md
├── LICENSE
├── CITATION.cff
└── ...
```

> 各分析工具的详细文档（数据流程、生物信息学工具及参数、可视化方法）请参见 [README_Tools_CN.md](README_Tools_CN.md)。

## 数据可用性与可重现性

所有核心数据集、预测转运蛋白表格、结构模型、置信度指标、分析脚本和流程配置均存放在稳定的公共存储库中，并提供版本化发布。

- **源代码**：本 GitHub 仓库
- **版本控制**：基于 Git 的版本管理，带有标签发布和更新日志
- **数据存档**：核心数据集可通过 [下载页面](https://yanglab.hzau.edu.cn/ptdb/index/download)和仓库 Releases 批量获取

## 引用

如果您在研究中使用了 PTDB，请按以下格式引用：

```
Liang, G., Huang, W., & Luo, C. (2026). PTDB: Plant Transporter Database.
Zenodo. https://doi.org/10.5281/zenodo.20593739
```

## 许可证

本项目按照 LICENSE 文件中指定的条款发布。

## 联系方式

如有问题、Bug 报告或数据需求，请在本仓库中提交 Issue。
