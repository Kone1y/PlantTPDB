# PTDB GitHub 仓库部署 & Zenodo 归档指南

本文件记录了将 PTDB 文档文件上传至 GitHub 并通过 Zenodo 获取 DOI 的完整操作流程，以便在另一台设备上继续执行。

---

## 一、待上传文件清单

仓库目录：`ptdb_for_github/`

```
需要上传的文件（共 11 个）：
├── .gitignore
├── CITATION.cff          ← 需要补全作者信息和 DOI
├── LICENSE
├── CHANGELOG.md
├── Readme.md             ← 英文 README
├── README_CN.md           ← 中文 README
├── data_description.md
├── database_schema.md
├── api_documentation.md
├── API_README.md
├── DEPLOY_GUIDE.md       ← 本文件（可选上传）

配置文件（3 个）：
├── common.php
├── config.php
├── database.php

不上传的目录（前后端代码）：
├── controller/
├── model/
└── view/
```

> 注意：`ptdb_for_github/view/index/api_documentation.html` 是 API 文档网页，如果需要放入仓库，请将其复制到根目录或其他位置后再上传。view 目录整体不上传。

---

## 二、上传至 GitHub

### 2.1 在 GitHub 上创建仓库

1. 打开 https://github.com/new
2. 仓库名：`PTDB`
3. 描述：`Plant Transporter Database`
4. 选择 **Public**
5. **不要**勾选 Initialize with README / .gitignore / License（已有这些文件）
6. 点击 Create repository

### 2.2 本地初始化并推送

将 `ptdb_for_github/` 整个目录复制到另一台设备后，在该目录下执行：

```bash
# 1. 初始化 Git
git init
git checkout -b main

# 2. 添加远程仓库
git remote add origin https://github.com/Kone1y/PTDB.git

# 3. 添加文件（只添加文档和配置，排除代码目录）
git add .gitignore
git add CITATION.cff
git add LICENSE
git add CHANGELOG.md
git add Readme.md
git add README_CN.md
git add data_description.md
git add database_schema.md
git add api_documentation.md
git add API_README.md
git add common.php
git add config.php
git add database.php

# 4. 提交
git commit -m "Initial release: PTDB documentation and API reference

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"

# 5. 推送
git push -u origin main
```

> 如果推送时要求登录，使用 GitHub Personal Access Token（PAT）作为密码：
> GitHub → Settings → Developer settings → Personal access tokens → 生成一个带 repo 权限的 token

---

## 三、创建 GitHub Release

推送成功后，在 GitHub 仓库页面创建版本发布：

1. 打开 https://github.com/Kone1y/PTDB/releases/new
2. **Tag**: `v1.0.0`
3. **Release title**: `v1.0.0 - Initial Release`
4. **Description**: 填入 CHANGELOG.md 中的内容
5. 点击 **Publish release**

---

## 四、Zenodo 获取 DOI

### 4.1 注册 Zenodo

1. 打开 https://zenodo.org/signup
2. 用 **GitHub 账号**登录（推荐，方便后续同步）

### 4.2 关联 GitHub 仓库

1. 登录 Zenodo 后，点击右上角用户名 → **Settings**
2. 左侧菜单选择 **GitHub**
3. 点击 **Connect GitHub account** → 授权访问
4. 在仓库列表中找到 `Kone1y/PTDB`，开启右侧的 **Toggle**

### 4.3 触发自动归档

完成 4.2 后，Zenodo 会在你创建 GitHub Release 时自动抓取。

1. 确保 GitHub Release（v1.0.0）已发布
2. 回到 Zenodo → 点击右上角用户名 → **Uploads**
3. 等待几分钟，会出现一条来自 GitHub 的归档记录（状态为 Draft）
4. 点击该记录，检查信息是否正确
5. 点击 **Publish** → 系统分配永久 DOI

DOI 格式通常为：`10.5281/zenodo.XXXXXXX`

### 4.4 手动上传数据文件（可选）

如果核心数据文件（TSV、Newick 树等）太大不适合放 GitHub，可以手动上传到 Zenodo：

1. Zenodo 首页 → **New Upload**
2. 填写：
   - **Title**: PTDB: Plant Transporter Database - Core Datasets
   - **Description**: Core transporter annotation datasets, phylogenetic trees, structural prediction metrics
   - **Authors**: 填入论文作者
   - **Keywords**: plant transporter, membrane protein, TC system, database
   - **Related identifiers**: 类型选 `isSupplementTo`，填入 GitHub 仓库 URL
3. 上传数据文件
4. 点击 **Publish** → 获得数据集的 DOI

---

## 五、补全 CITATION.cff

拿到 Zenodo DOI 后，编辑 `CITATION.cff`：

```yaml
# 1. 填入作者信息（每位作者一个条目）
authors:
  - given-names: "名"
    family-names: "姓"
    affiliation: "单位"
    orcid: "https://orcid.org/0000-0000-0000-0000"  # 没有 ORCID 就删掉这行

# 2. 替换 DOI 占位符
identifiers:
  doi: "10.5281/zenodo.XXXXXXX"  # ← 替换 TODO_ZENODO_DOI
```

修改后再次提交推送：

```bash
git add CITATION.cff
git commit -m "Add DOI and author info to CITATION.cff"
git push
```

---

## 六、后续维护流程

每次 PTDB 有数据更新时：

1. 更新 GitHub 仓库文件（CHANGELOG.md 等）
2. 创建新的 GitHub Release（v1.1.0, v1.2.0 ...）
3. Zenodo 自动为新 Release 生成新 DOI
4. 如果有新数据文件，手动上传到 Zenodo

---

## 七、完成检查清单

- [ ] GitHub 仓库 `Kone1y/PTDB` 已创建并推送文件
- [ ] GitHub Release v1.0.0 已发布
- [ ] Zenodo 已关联 GitHub 并自动归档
- [ ] Zenodo 归档已 Publish，获得 DOI
- [ ] CITATION.cff 已填入作者信息和 DOI
- [ ] （可选）核心数据文件已手动上传 Zenodo 并获得 DOI
- [ ] Readme.md 中的仓库地址已确认正确
