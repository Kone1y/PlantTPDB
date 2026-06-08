# PTDB — 植物转运蛋白数据库

一个综合性植物转运蛋白数据库，提供系统分类、进化分析、跨物种比较和在线预测工具。

## 项目简介

PTDB 整合了来自多个植物物种的转运蛋白信息，主要功能包括：

- **转运蛋白分类**：TC 分类系统、Pfam 结构域、基因家族（ABC、MFS 等）
- **跨物种比较**：共线性分析、系统发育树构建、Ka/Ks 选择压力计算
- **功能注释**：底物搜索、通路映射、文献整合
- **在线工具**：BLAST 序列搜索、转运蛋白预测、交互式基因家族浏览器
- **数据可视化**：基于 Highcharts/ECharts 的交互式图表

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | ThinkPHP 5 |
| 数据库 | MySQL |
| Web 服务器 | Apache |
| 前端可视化 | Highcharts、ECharts |
| 序列比对 | MAFFT |
| 系统发育树 | FastTree |
| 序列搜索 | SequenceServer |

## 项目结构

```
ptdb/
├── common.php              # 公共工具函数
├── config.php              # 应用配置
├── database.php            # 数据库连接配置
├── controller/
│   ├── Index.php           # 主页路由（首页、物种、基因家族等）
│   ├── Table.php           # 数据表查询（基因家族、物种等）
│   ├── Select.php          # 数据筛选接口
│   ├── Prediction.php     # 在线转运蛋白预测
│   ├── Agent.php           # AI Agent 智能查询接口
│   ├── Neo4j.php           # 图数据库集成
│   ├── Php.php             # 通用 PHP 工具
│   ├── Other_additon.php   # 补充功能模块
│   ├── Table_copy.php      # 数据表导出/复制
│   └── Footer_nav.php      # 导航组件
└── view/
    ├── index/              # 主要页面
    │   ├── home.html       # 首页
    │   ├── search.html     # 基因搜索
    │   ├── blast.html      # BLAST 搜索界面
    │   ├── species.html    # 物种浏览器
    │   ├── gene_family.html       # 基因家族概览
    │   ├── gene_family_super.html # 基因家族超家族视图
    │   ├── gene_family_phylogenetic.html # 基因家族系统发育分析
    │   ├── phylogenetic.html      # 系统发育树查看器
    │   ├── synteny.html           # 共线性分析
    │   ├── evolution.html         # 进化分析
    │   ├── kaks.html              # Ka/Ks 选择压力分析
    │   ├── pathway.html          # 通路映射
    │   ├── prediction.html       # 转运蛋白预测
    │   ├── download.html         # 数据下载
    │   ├── tc_system.html        # TC 分类系统
    │   ├── tc_code.html          # TC 编码浏览器
    │   ├── abcde.html            # ABC 转运蛋白亚家族
    │   ├── transporter_k_d.html  # 转运蛋白 Kd 值
    │   └── methods.html          # 方法学描述
    ├── footer_nav/         # 公共导航模板
    └── other_addition/     # 补充页面（花图等）
```

## 功能模块

### 浏览与搜索
- **基因搜索**：支持按 Protein ID、Gene ID、mRNA ID、物种等多字段检索
- **物种浏览器**：浏览各植物物种的转运蛋白组成
- **基因家族**：探索转运蛋白家族（ABC、MFS、OPT 等）及其成员详情
- **Pfam 家族**：基于结构域的分类浏览
- **TC 系统**：基于转运蛋白分类（TC）编码的浏览

### 进化与比较基因组学
- **系统发育树**：基于 FastTree 的进化关系可视化
- **共线性分析**：跨物种共线性基因块可视化
- **Ka/Ks 分析**：基因对的选择压力估算
- **进化搜索**：跨谱系进化事件检索

### 功能分析
- **通路映射**：将转运蛋白映射到代谢通路
- **底物搜索**：按底物特异性查找转运蛋白
- **文献整合**：经整理的文献引用

### 在线工具
- **BLAST**：针对 PTDB 数据集的序列相似性搜索
- **转运蛋白预测**：提交蛋白序列进行转运蛋白分类预测
- **AI Agent**：支持自然语言提问的智能查询接口

### 数据获取
- **交互式浏览**：基于 Web 的数据探索
- **批量下载**：通过下载页面获取数据集

## API 接口

PTDB 提供 RESTful 风格的可编程数据访问接口：

### Agent 智能查询接口
```
GET /ptdb/agent/get_ptdb_agent_base_info_by_id?id=<Protein_ID|PTPGID|AthID|GeneID|mRNAID>
```
返回转运蛋白的综合信息，包括基因符号、物种、基因家族、TC 编码、Pfam 结构域和预测底物。

### 数据查询接口
```
GET /ptdb/table/gene_family_table             # 所有基因家族
GET /ptdb/table/gene_family_species_table?family=<名称>  # 指定家族的物种分布
GET /ptdb/table/gene_family_member_table?family=<名称>   # 指定家族的成员信息
```

### 预测接口
```
POST /ptdb/prediction/submit_prediction   # 提交预测任务
GET  /ptdb/prediction/get_task_status?task_id=<任务ID>  # 查询任务状态
```

## 数据库结构

核心 MySQL 数据库 `PTDB` 包含以下数据表：
- 基因/蛋白质信息及交叉引用
- 基因家族分类
- Pfam 结构域注释
- TC 分类映射
- 物种元数据
- 通路注释
- Ka/Ks 计算结果
- 文献引用
- 预测任务记录

## 安装部署

### 环境要求
- PHP >= 7.0
- MySQL >= 5.6
- Apache（需启用 mod_rewrite）
- ThinkPHP 5

### 部署步骤
1. 克隆本仓库
2. 将 PTDB 数据库结构及数据导入 MySQL
3. 在 `database.php` 中配置数据库连接信息
4. 部署至 Apache Web 服务器，配置相应的虚拟主机
5. 确保已安装 MAFFT、FastTree 和 SequenceServer，且后端分析工具可正常调用

## 数据可用性与可重现性

所有核心数据集、预测转运蛋白表格、结构模型、置信度指标、分析脚本和流程配置均存放在稳定的公共存储库中，并提供版本化发布。

- **源代码**：本 GitHub 仓库
- **版本控制**：基于 Git 的版本管理，带有标签发布和更新日志
- **数据存档**：核心数据集可通过下载页面和仓库 Releases 批量获取
- **长期维护**：本仓库作为 PTDB 代码库的持久化、版本控制记录

## 许可证

本项目按照 LICENSE 文件中指定的条款发布。

## 联系方式

如有问题、Bug 报告或数据需求，请在本仓库中提交 Issue。
