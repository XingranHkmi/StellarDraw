# StellarDraw · 首次上传 GitHub 与发布 Release 操作指南

> 面向「GitHub 网页用得很熟，但从没敲过 `git commit` / `git push`」的情况。
> 命令行与 PyCharm 两种方式都给了完整步骤，任选其一即可，**不要混着做**。
> 所有命令中的 `<用户名>` 换成你自己的 GitHub 用户名。

---

## 0. 现状体检（2026-09-26 复核）

| 检查项 | 实测结果 | 结论 |
| --- | --- | --- |
| 是否已是 Git 仓库 | **已是**：`main` 分支 1 次提交，已推送至 `origin/main` | 无需再做 `git init` |
| 远程仓库 | `origin` = `git@github.com:XingranHkmi/StellarDraw.git`（**SSH 方式**） | 已绑定，之后直接 `git push` 即可 |
| `.gitignore` | 已存在并生效（`data/`、`.venv/`、`*.exe` 均已忽略） | 无需处理 |
| `user.name` / `user.email` | 已配置（`XingranHkmi` / `jinglekwii@outlook.com`） | 无需处理 |
| 标签 | 尚无任何 tag | 发布时创建第一个：`v1.0.0` |
| git 版本 | `2.55.0.windows.3`（随 WorkBuddy 自带） | 可用 |
| `gh` 命令行 | 未安装 | Release 走网页或 PyCharm，不影响 |
| 隐私风险 | `data/rosters/高三12班.json` 有真实学生姓名 | 已在 `.gitignore` 中排除 `data/` |
| 体积风险 | `.venv` 30MB、`__pycache__`、`.idea`、`.workbuddy` | 已全部排除 |
| 杂项 | `README.md` **已补齐**；`=1.2.0` 垃圾文件**已删除**；`抽号器.exe`（737KB，2019 年时间戳）仍在 | 见第 6 节「推送前的清理建议」 |

**`.gitignore` 已就位**，并用真实目录树验证过：执行 `git add -A` 后，只有 `.gitignore`、`README.md`、`pyproject.toml`、`uv.lock`、`src/**`、`tests/**`、`docs/**`、`main.py` 会入库，其余全部被忽略。**本机已按本文流程完整走过一遍并成功推送**，下面保留全部步骤，同时可作为「换电脑 / 重建仓库」时的操作手册。

---

## 1. 准备工作（只做一次，两种方式都要做）

### 1.1 在 GitHub 网页上创建**空仓库**

1. 右上角 `+` → **New repository**。
2. **Repository name**：`StellarDraw`（本地目录名仍为 `ClassRand`，两者不一致不影响使用）。
3. **Description**：`课堂抽号机 · Python + pywebview`。
4. **Public / Private**：
   - 推荐 **Private**——即便 `data/` 已被忽略，也避免误传学生信息；
   - 若要 Public，务必确认 `data/` 真的没被提交（见第 3.4 步的 `git status` 检查）。
5. **⚠️ 三个勾选项全部不要选**：不要 Add README、不要 Add .gitignore、不要 Choose license。
   理由：勾了会让远端仓库自带一次提交，本地仓库是另一条独立历史，第一次 `git push` 必然被拒（`non-fast-forward`），新手最容易卡在这里。**空仓库最省事。**
6. 点 **Create repository**，停在那个「Quick setup」页面，把 HTTPS 地址抄下来：
   `https://github.com/<用户名>/StellarDraw.git`

### 1.2 配置 Git 身份（必须，否则提交报错）

```bash
git config --global user.name  "你的GitHub用户名或昵称"
git config --global user.email "你GitHub绑定的邮箱@example.com"
git config --global core.quotepath false    # 让 git status 正常显示中文文件名，不转义成 \344\275\240
```

- 邮箱要和 GitHub 账号里的邮箱一致，否则贡献图不计数（用 GitHub 的 `noreply` 邮箱也行）。
- 这只影响提交记录的署名，不会用来登录。

### 1.3 选择认证方式（二选一）

> **本机现状**：已配置 **SSH**（远程地址 `git@github.com:XingranHkmi/StellarDraw.git`），下面的 HTTPS + Token 步骤可以跳过。换机或改回 HTTPS 时再照下表操作。

| 方式 | 做法 | 适合 |
| --- | --- | --- |
| **HTTPS + 个人访问令牌（推荐新手）** | GitHub 网页 → 右上角头像 → **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token** → 勾选 `repo`（私有库还需 `workflow`）→ 生成后**立刻复制保存**（只显示一次）。推送时用户名填 GitHub 用户名，密码框**粘贴这个 token**，不是登录密码。 | 最通用，PyCharm 也用同一套 |
| **SSH 密钥** | `ssh-keygen -t ed25519 -C "你的邮箱"` → 一路回车 → `cat ~/.ssh/id_ed25519.pub` 复制 → GitHub **Settings → SSH and GPG keys → New SSH key** → 粘贴。远程地址改用 `git@github.com:<用户名>/StellarDraw.git` | 长期用 git、想彻底免密 |

> **易错点**：2021 年起 GitHub 已不支持用账号密码在命令行推送。弹窗要密码却填了登录密码 → 一定报 `Authentication failed`。

---

## 2. 方式一：Git 命令行（推荐，出问题最好排查）

> **本机已完成 ②~⑧**（仓库已初始化并推送至 `origin/main`）。以下命令完整保留，供换机 / 重建仓库时照做；日常提交只看本节末尾的「常用后续命令」。

在项目根目录打开 Git Bash（或 PyCharm 自带终端）逐条执行：

```bash
# ① 进入项目
cd /d/100_Projects/ClassRand

# ② 初始化仓库，并直接把默认分支命名为 main（GitHub 现在的默认分支名）
git init -b main

# ③ 确认忽略规则生效：先看一眼，确认没有 data/、.venv/、*.exe 混进来
git status
#   看到 "Untracked files" 列表里只有源码和文档即正确

# ④ 加入暂存区
git add -A

# ⑤ 再确认一次（这一步是最后一道防线）
git status
#   若发现 data/config.json 之类，说明 .gitignore 没生效 → 停下来排查，先别 commit

# ⑥ 提交
git commit -m "feat: 初始化 StellarDraw 课堂抽号机项目"

# ⑦ 绑定远程仓库
git remote add origin https://github.com/<用户名>/StellarDraw.git
git remote -v          # 确认打印出 fetch/push 两行地址

# ⑧ 首次推送（-u 建立上游关联，之后只需 git push）
git push -u origin main
```

首次 HTTPS 推送会弹出 GitHub 登录窗口 / 终端要你输用户名密码 → 密码处粘贴 **token**。
成功后刷新 GitHub 网页，代码就出现了。

**常用后续命令**

```bash
git status                 # 看看改了什么
git add -A && git commit -m "fix: 修复翻牌音效只响一次的问题"
git push                   # 已 -u 过，之后不需要再写 origin main
git log --oneline -5       # 看最近 5 条提交
```

---

## 3. 方式二：PyCharm（全程图形界面）

> 前置：PyCharm 需要能找到 git.exe。你这台机器的 git 在
> `C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe`
> 配置位置：**File → Settings → Version Control → Git → Path to Git executable** → 填上面的路径 → 点 **Test**，出现版本号即成功。

### 3.1 创建本地仓库
菜单 **VCS → Enable Version Control Integration...**（新版叫 **VCS → Create Git Repository...**）→ 选 **Git** → 目录选 `D:\100_Projects\ClassRand` → OK。
之后左下角会出现 **Commit** 工具窗（或 `Alt+0` 打开），顶部出现 Git 分支标签 `main`。

> 如果默认分支是 `master`，想改成 `main`：Git 工具窗（`Alt+9`）→ 分支名右键 → **Rename** → `main`。

### 3.2 忽略文件
PyCharm 会自动读取我建好的 `.gitignore`。Commit 工具窗里被忽略的文件**不会出现在默认列表**中——如果看到 `data/`、`抽号器.exe`、`.venv`，说明 `.gitignore` 没读到，先回命令行确认。

### 3.3 首次提交
1. `Alt+0` 打开 **Commit** 窗。
2. 勾选要提交的文件（**不要**勾 `=1.2.0`；正常它已被忽略）。
3. 在下方输入框写提交信息：`feat: 初始化 StellarDraw 课堂抽号机项目`。
4. 点 **Commit**（先别点 `Commit and Push...`——还没配远程，会多弹一次框）。

### 3.4 绑定远程地址
**Git → Manage Remotes...**（老版本在 **VCS → Git → Remotes**）→ `+` →
Name: `origin`，URL: `https://github.com/<用户名>/StellarDraw.git` → OK。

### 3.5 推送
`Ctrl+Shift+K`（或 **Git → Push**）→ 确认分支与远程 → **Push**。
弹登录框时选 **Use Token**，把第 1.3 步生成的 token 粘进去；也可提前在
**File → Settings → Version Control → GitHub → + → Log in with Token** 存好账号。

### 3.6 验证
PyCharm 右下角弹出 `Pushed 1 commit to origin/main`，网页刷新即可看到。

---

## 4. 发布 Release（版本号 / 标签 / 发布说明）

**概念先分清**：

- **Tag（标签）**：指向某一次提交的不可变指针，是版本的「锚点」，命名如 `v1.0.0`。
- **Release（发布）**：GitHub 在 Tag 之上加的一层包装——标题、发布说明、可下载附件（你打包出的 `StellarDraw.exe`）。**一个 Tag 只能有一个 Release。**

### 4.1 版本号怎么定（语义化版本 SemVer）

格式 `主版本.次版本.修订号`，例如 `1.0.0`：

| 位 | 何时 +1 | 例子 |
| --- | --- | --- |
| 主版本 | 不兼容的大改 | 1.x → 2.0.0 |
| 次版本 | 新增功能，向下兼容 | 1.0.0 → 1.1.0 |
| 修订号 | 只修 bug | 1.1.0 → 1.1.1 |

**本项目已统一为 `1.0.0`（2026-09-26 落实）**，四处保持一致：

| 位置 | 当前值 |
| --- | --- |
| `src/stellardraw/__init__.py` | `__version__ = "1.0.0"` |
| `pyproject.toml` | `version = "1.0.0"` |
| `uv.lock` | 由 `uv sync` 同步为 `1.0.0` |
| Tag（待打） | `v1.0.0` |

> **注意区分两个版本号**：`docs/PRD.md` 表头的 **v1.10 是「文档版本」**（记录需求迭代到第几轮），**不等于产品版本**。产品版本以 `__version__` 为单一数据源（PRD §4.2 的「"关于"页内容规格」表已明确该要求），"关于"页显示的也正是它。

改完记得 `git commit` 并 `git push`，**让 Tag 打在包含这次改动的提交上**。

### 4.2 命令行创建 Tag 并推送

```bash
# 带注释的标签（推荐，可写清版本含义）
git tag -a v1.0.0 -m "v1.0.0：首个正式版（抽号 / 氛围 / 特殊效果 全部交付）"

# ⚠️ tag 不会随 git push 自动上传，必须单独推
git push origin v1.0.0
# 或一次推所有本地标签：git push --tags

git tag            # 查看本地标签
git ls-remote --tags origin   # 确认远端已收到
```

> **最容易踩的坑**：`git push` 只推分支，不推标签。命令行打完 tag 就去网页发 Release，会找不到这个 tag。

### 4.3 PyCharm 里打 Tag

**Git 工具窗（`Alt+9`）→ 选中提交 → 右键 → New Tag**（或菜单 **Git → New Tag**）→ Tag name `v1.0.0`，Message 写版本说明 → Create。
推送时：`Ctrl+Shift+K` → 左下角勾选 **Push Tags**（或选 `All`），否则标签留在本地。

### 4.4 在网页上发布 Release

1. 仓库页面右侧 **Releases** → **Create a new release**（或 `... → Draft a new release`）。
2. **Choose a tag**：下拉选 `v1.0.0`；若下拉里没有，直接输入 `v1.0.0`，下方会出现 `+ Create new tag: v1.0.0 on publish`（网页会顺带帮你建标签，适合不打命令行的情况）。
3. **Previous tag**：**首次发布留空**（没有更早的版本可比对）。
4. **Release title**：`v1.0.0 · 首个正式版`（写人话，别只写版本号）。
5. **描述区**：右上角有 **Generate release notes** 按钮，可自动把自上次 tag 以来的 PR / 提交列出来，再手动改成面向老师的语言。推荐结构：

```markdown
## 本次更新（首个正式版）
- 抽号：抽取 1 次 / 5 次、去重模式、多班级名单管理、CSV 导入与模板
- 氛围：牌堆滑出、点击 3D 翻转、看板娘三态、Web Audio 合成音效
- 特殊效果：增大概率 / 结果漂移 / 课堂叫醒
- 兼容：高 DPI（150% / 200%）下窗口按物理像素折算并在工作区居中

## 使用说明
双击 StellarDraw.exe 即可运行，需 Windows 10/11 且已安装 WebView2 运行时。

## 已知问题
- 仅在 1920×1080@100% 缩放下完成实测，其它缩放比例欢迎反馈。
```

6. **附加二进制文件**：把打包好的 exe **拖进下方虚线框**上传（这就是 GitHub 上发布 exe 的标准做法，而不是把 exe 提交进仓库）。
7. 想先给别人预览就勾 **Set as a draft**；当前版本稳定可不勾 **pre-release**。
8. 点 **Publish release**。之后这个 Release 会有一个固定地址：
   `https://github.com/XingranHkmi/StellarDraw/releases/tag/v1.0.0`

> 若要更新 exe：删掉旧附件、重新上传同名文件即可，不必删 Release；若要改版本号，必须新建 tag。

---

## 5. 首次操作常见注意事项与易错点（速查清单）

| # | 坑 | 后果 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 建仓库时勾了 README / .gitignore | 首次 push 被拒：`non-fast-forward` / `refusing to merge unrelated histories` | 建**空仓库**；已勾了就先 `git pull --rebase origin main` 再 push（别一上来 `--force`） |
| 2 | 没配 `user.name` / `user.email` | `Please tell me who you are` | 第 1.2 步先配置（你本机目前正是这个状态） |
| 3 | 用登录密码推送 | `Authentication failed` | 用 **Personal Access Token** 或配 SSH |
| 4 | 忘了 `.gitignore`，把 `.venv`、`data/` 提交了 | 仓库臃肿 + **学生姓名泄露** | 先建 `.gitignore` 再 `git add`；本项目已就绪 |
| 5 | 敏感数据已经提交 | 删文件也没用，历史里仍在 | 立刻改私密信息、用 `git filter-repo`/BFG 清历史，或干脆删库重来（首次上传，重来成本最低） |
| 6 | `git push` 以为把 tag 也推了 | 网页找不到 tag | `git push origin <tag>` 或 `git push --tags` |
| 7 | 默认分支叫 `master` | 与 GitHub 的 `main` 不一致，出现两条分支 | `git init -b main` 或 `git branch -M main` |
| 8 | 首次 push 忘了 `-u` | 每次都要写 `git push origin main` | 第一次用 `git push -u origin main` |
| 9 | 中文文件名显示成 `\344\275\240...` | 列表看不懂 | `git config --global core.quotepath false` |
| 10 | 单个文件 > 100MB | push 被 GitHub 拒绝 | 走 Git LFS 或挂到 Release 附件；本项目 exe 仅 737KB，安全 |
| 11 | 提交信息乱码 | GitHub 上显示问号 | 全程 UTF-8；PyCharm 默认 UTF-8 无需处理 |
| 12 | 换行符告警 `LF will be replaced by CRLF` | 只是提示，不是错误 | Windows 下默认 `core.autocrlf=true` 属正常；纯 Windows 项目想消除警告可 `git config --global core.autocrlf false` |
| 13 | 在 `.gitignore` 生效前就 `git add` 了 | 忽略规则对已暂存文件无效 | `git rm -r --cached .` 然后重新 `git add -A`（只动索引，不删本地文件） |
| 14 | 把打包 exe 提交进仓库 | 仓库每次发版都膨胀几十 MB，且 diff 无意义 | exe 只作为 **Release 附件** |
| 15 | 公开仓库上传了班级名单 | 学生隐私泄露 | `data/` 已忽略；发布前用 `git ls-files \| grep data` 复核 |

---

## 6. 推送前的清理建议（可选，但推荐）

**已完成（2026-09-26 复核）**：

1. ~~**`=1.2.0`**（0 字节，疑似 `pip install pywebview >=1.2.0` 少写引号被 shell 当成重定向产生的垃圾文件）~~ → **已删除**。
2. ~~**`README.md` 0 字节**~~ → **已补齐**（简介、老师用法、开发接手、打包指引、素材授权、文档索引）。

**仍建议处理**：

3. **`抽号器.exe`**：737KB、时间戳是 2019 年，与本项目打包产物存疑，已按 `*.exe` 忽略。确认无用后可删；真正发布时请把打包出的 **`StellarDraw.exe`** 拖进 Release 附件区（exe 不进仓库）。

---

## 7. 三条最常用的「后悔药」

```bash
git restore --staged <文件>      # 把误 add 的文件撤出暂存区（不丢改动）
git commit --amend -m "新说明"   # 改刚提交但还没 push 的说明（已 push 就不要用）
git reset --soft HEAD~1          # 撤销上一次提交，改动保留在工作区（未 push 时用）
```

**已 push 的内容不要用 reset/force 覆盖**，改成再提交一个新的修复提交——这是 git 的公共约定。

---

## 8. 最省事的一条完整流水线（照抄即可）

```bash
cd /d/100_Projects/ClassRand
# —— 首次建仓（本机已完成，仅在换机 / 重建时执行）——
git config --global user.name  "<你的名字>"
git config --global user.email "<你的邮箱>"
git config --global core.quotepath false
git init -b main
git remote add origin git@github.com:XingranHkmi/StellarDraw.git   # 本机走 SSH
git push -u origin main
# —— 日常提交（本机现在从这里开始）——
git add -A
git status                       # ⚠️ 确认没有 data/ .venv *.exe
git commit -m "fix: 修复翻牌音效只响一次的问题"
git push
# —— 发版 ——
git tag -a v1.0.0 -m "v1.0.0：首个正式版（抽号 / 氛围 / 特殊效果 全部交付）"
git push origin v1.0.0           # ⚠️ tag 要单独推
# 然后去网页 Releases → Draft a new release → 选 v1.0.0 → 写说明 → 拖入 StellarDraw.exe → Publish
```
