# Changelog

本文件记录 obsidian-note-ingest 的主要变更。
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)：**0.x 表示尚未稳定**，
脚本参数、文件结构与文档都可能继续变动。

## [0.2.0] - 2026-10-07

**改动来自一次真实使用的复盘**：一次修订有 17 处散落改动，`vaultio` 的 anchor/until 表达不了，
只好手写脚本绕过它——于是没有自动备份；而 `toc` 照着文档跑，会把「按章分组」的目录静默拉平。

### 修复

- **`toc` 不再拉平分组式目录。** 旧实现把所有 `##` / `###` 拉成平表，`### 第 N 章 …` 组标题
  变成普通条目、编号全乱，退出码还是 0。现在默认沿用笔记现有排版，**认不出就拒绝重建、一字不动**；
  新增 `--group-by auto|0|1|2`、`--exclude 正则`、`--dry-run`。也不再吞掉目录后面的 `---`。
- **备份按内容去重。** 这是 `pitfalls.md` 早就承诺、实现里却没有的一条；内容与最近一份备份相同时跳过。
- **`insert` 空内容被拦下。** 原先报「成功」，还白写一份备份、刷新 mtime。
- **`pptx_deep.py` 的表格能直接用了**：补 `| --- |` 分隔行、转义单元格里的 `|`、补齐参差列数。
  原先贴进笔记不会渲染成表格，还会让 lint 误报列数不一致。
- **`renderpages.py --pages` 接受方括号。** `extract.py --stats` 打印的是 `[4, 26, 54, 80]`，
  原先粘过去会报「无法解析页码片段」。
- **`extract.py` 对 docx / pdf 的 `--extract-images` 明确报不支持**，原先 exit 0 空转。
- **`obsidian_lint.py` 认 callout 里的表格与标题。** `note-format` 教人把表格放进 callout，
  那里原先完全不被统计，导致「改前后比表格数」在规范要求的地方失效。
  callout 内的标题只提示、不判死链。

### 新增

- **`vaultio.py patch --edits-file`**：一次落盘多处改动，**任一原文没唯一命中就整批不写**，
  带自动备份 + diff。取代 `pitfalls.md` 里手写 `INS` / `REP` 的处方（那条路没有备份）。
- **`check` 退出码 13（版本修订）**：原先覆盖率高就判「不要写」，但拿到答案版 / 修订版时
  正确动作是逐处替换。现在用「句子级近重复占比」区分：≥60% 判 13，否则判 10。
- **`pdfdiff.py`**：两份同源 PDF 逐页比文本，给出变了哪几页和可粘贴的页码列表；
  两版都抽不出文本的页单独归类。取代 `extraction.md` 里在 Windows 下跑不通的 heredoc 脚本。
- **`backups --prune --keep N`**、**`restore --dry-run`**、**`toc --dry-run`**、**`new --dry-run`**。
- **`extract.py --as-text`**：`.md` / `.txt` 强制按纯文本处理。纯文本笔记里出现一行 `1:20 例题…`
  就会被当成字幕稿，时间戳之前的整段内容会被静默丢掉；现在这种丢弃会打警告。

### 文档

- `SKILL.md` 任务导航补上「同一份材料出了新版」「一处要改很多地方」「重建目录」三个入口。
- 色块阈值统一成**舒适区 2–3、红线 5**（四处原本是 2–3、2–5、5、3）。
- 去掉 POSIX-only 写法（`find -newermt`、heredoc），换成三个平台都能跑的命令。

### 测试

- 冒烟测试 12 → 25 项，覆盖 toc / patch / check 13 / 备份去重 / pptx 表格 / `--pages` /
  `--as-text` / pdfdiff。

## [0.1.2] - 2026-09-30

**笔记格式规范精简。** 脚本无改动，只改规则文档。

### 格式规范
- **范围 callout 收敛成一个格式**：一份笔记只有 H1 后面那一个 `[!info]`，各章不再另开。
  课程主笔记逐章列一行（章号 + 章名）+ 末行标来源；专题笔记不写，或一句话带过。
- **目录与标题层级按章分组**，不写周次（`references/note-format.md` 第二、三、四、六节）。
- 组织单位默认「章」；材料按讲推进时全文换成「讲」，同一份笔记不混用。
- 章节收尾统一按章表述；作业 callout 由 `[!info]` 改为 `[!warning]`（避开唯一的那个 info callout）。

### 文档
- **`references/` 去重**：pitfalls 的「多讲 / 多章笔记」节与 note-format 第六节重复（-42/+24 行）——
  格式规范统一归 note-format（含新并入的「30 秒自查」），pitfalls 只留「出错了怎么办」。

## [0.1.1] - 2026-09-30

**从实际使用合并的经验 + 发布自动化。** 脚本无功能改动，规则与文档增强。

### 新增
- `.github/workflows/release.yml`：推 tag 或发布 Release 时，自动把「解压即用」的 skill zip
  打包并挂到 Release 附件上；Release 不存在时会自动创建（标为 pre-release）。
  附件由 `git archive` 从对象库导出，因此只含被跟踪的文件、换行符天然是 LF。
- Release 说明自动取自 `CHANGELOG.md` 对应版本小节（工作流内完成），不再是英文提交列表。

### 从实际使用合并的经验
- **口述内容取舍**（录音稿）：人物轶事、历史背景、老师的自我感想默认删；
  课程事务（作业 / 考试）不删，收进章节末尾 `> [!info]`；取舍用两把尺子。
- **「老师的话」≠「该进笔记的话」**：两种口述的分界线与把关时机（`references/transcripts.md`）。
- **覆盖率虚高的第四种形态**：口述增量藏在 98% 里（其余三种：占位符、通用词、第二次课串讲）。
- **色块按整个 `####` 通盘计数**：小节里 `#####` 例题中的色块也计入 2–3 个的上限。
- **批量改动模式升级**：整行锚点 + `INS` / `REP` 字典 + 唯一性与完整性断言，
  比子串 `s.count()` 更安全（`references/pitfalls.md`）。
- **课程主笔记的第三种骨架**（H1 = 课程名、`##` = 章）：总范围 callout 逐章一行，
  整理完一章的五步固定动作（`references/note-format.md`）。
- **「继续」却没说材料在哪**：先按修改时间列下载目录（`references/pitfalls.md`）。

### 文档
- README 安装章节扩为三种方式：`npx skills add`（推荐）/ `npx openskills install` / `git clone`，
  附各 agent 的 skill 目录对照表；全文精简，只保留「是什么 / 怎么用 / 注意什么」。

## [0.1.0] - 2026-09-30

**首个公开测试版（pre-release）。**

### 修复（lint 可信度 / 资源释放 / 死代码）

- **`obsidian_lint.py` 的表格列数不认转义竖线**：原先用 `split("|")` 数，
  而 `extract.py` 的 `_table_to_md` 正是用 `\|` 转义单元格内竖线的——
  **工具链自己产出的表会被自己误报**。改为按「未被反斜杠转义的竖线」切分。
- **`obsidian_lint.py` 读文件用 `errors="replace"`**：GBK 笔记会被当成乱码正文，
  静默产出一份「看起来没问题」的报告（实测同一内容 UTF-8 报 24 字符、GBK 报 33 字符，
  两份都说「没有任何问题」）。改为严格解码，失败时报「不是 UTF-8」。
- **三个 PDF 脚本不释放文件句柄**（`renderpages` / `pdf_panels` / `pdf_layout`）：
  Windows 上句柄占着会挡住删除或覆盖同一个 PDF。用 `atexit.register(doc.close)` 释放。
- **`extract.py` 的 `.ppt` / `.doc` 分支已成死代码**（更早处已拦截并给出转换命令）→ 去掉冗余分支。
- **`flatten_callouts.py` 预览按行号倒序打印** → 改成正序，并去掉「只显示 5 条」的截断。
- **`vaultio.py` 备份目录后缀零填充**（`-02` / `-03`）：字符串序里 `-10 < -2`，
  不补零的话同一秒备份超过 9 份时 `restore` 的「倒序取最新」会取错。
- **`--force` 补上 `--help` 说明**（原先只在模块 docstring 里提到）。
- 删掉 `lint_file(strict=...)` 这个从未被使用的形参（`--strict` 实际在 `main` 里判断）。
- 文档：`SKILL.md` 的依赖清单漏了 `jieba`（已补）；`/tmp/draft.md` 这类路径改为相对路径
  （Windows 用户会真在盘根建 `\tmp`）。

### 新增（CI）

- **`.github/workflows/ci.yml`**：ubuntu-latest + windows-latest × Python 3.10 / 3.12，
  跑语法检查 + `tests/smoke.py`。
- **`tests/smoke.py` 扩到 12 项**：新增「lint 不误报转义竖线」「lint 仍能发现真列数不一致」
  「lint 报非 UTF-8 且不崩」。

### 修复（文档与一致性）

逐文件复查（含各脚本 `--help` 实跑、JSON / YAML 解析验证、全仓库个人信息扫描）后修掉：

- **`SKILL.md` 的 frontmatter 不是合法 YAML** —— `description_en` 的值里含半角 `": "`
  却没有加引号，严格解析器会直接报 `mapping values are not allowed here`，
  **整个 skill 可能加载失败**。已用单引号包裹该值，并把这个检查加进 `tests/smoke.py` 防回归。
- **`references/figures.md` 小节编号风格混用**（`§1.10` 与 `§二` 并存）：
  一级标题改用阿拉伯数字，引用统一为 `§1.x` / `§2`。
- **`references/note-format.md` 残留具体上课日期**（`第一次课（9/7）`）→ 改为 `（M/D）` 占位。
- **`obsidian_lint.py` 在 PyYAML 缺失时静默跳过** frontmatter 检查 → 现在打出一行提示
  （与 `jieba` 的处理保持一致）。
- `CHANGELOG.md` 里的行数声明过期（423 → 实际 438）。

### 修复（代码级：外部用户首次使用会踩的坑）

一次逐文件代码审查 + 实测（在临时目录里跑，不碰真实 vault）后修掉的问题。
前 6 条都有 `tests/smoke.py` 覆盖。

- **`extract.py --out` 写出 CRLF**，违反全仓库 LF 硬约定（脚本里 `assert '\r\n' not in s` 会炸）。
  实测输出 `CRLF=7`，已补 `newline="\n"`。
- **同一秒内两次备份互相覆盖**：`backup()` 时间戳只到秒，`copy2` 直接覆盖——
  实测第二次写入后只剩 1 份备份（v1 永久丢失），直接击穿「全程可回滚」的承诺。
  现改为目录被占用时追加 `-2` / `-3`（不影响 `restore` 倒序取最新）。
- **`insert` 发现锚点冲突后仍落盘**：原先只打 `[警告]` 就继续写、exit=0。
  现改为报错退出、不落盘，与 `replace` 行为一致。
  **同时补上「笔记自身锚点不唯一」的拦截**（原先会静默插到第一个同名标题下面）。
- **`insert` / `append` 没有 `--dry-run`**：传了会被 argparse 直接拒（exit=2）。
  现已实现，三个写入命令行为统一；并加 `--no-diff` 以便关掉 diff 输出。
- **整理稿一律按裸 UTF-8 读**：GBK 整理稿（中文 Windows 记事本另存为 ANSI 的产物）会抛
  `UnicodeDecodeError` 整段栈。现统一走 `read_text_any()`（UTF-8 → GBK → UTF-16 回退），
  识别不了时给一行清楚提示并 exit 2。
- **`jieba` 不在 requirements、且降级是静默的**：查重会悄悄退化成 n-gram 而不告知。
  已加进 `requirements.txt`，降级时向 stderr 打出提示。
- **4 个脚本缺 stdio UTF-8 重配置**（pdf_layout / pdf_panels / pptx_deep / renderpages），
  Windows cp936 控制台下中文输出乱码；现已补齐，8/8 脚本一致。
- **docstring 与实现不符**：`before-heading` 未实现却写在用法里（已删）；
  `new` 的 `--force` 未文档化（已补）。

### 新增（工程门面）

- **`.gitattributes`**：`* text=auto eol=lf`。Windows 版 Git 默认 `core.autocrlf=true`，
  不声明的话别人一 clone 全是 CRLF，当场踩进 `references/pitfalls.md` 警告的那个坑。
- **`tests/smoke.py`**：9 项冒烟测试，全部在临时目录里跑（不碰用户 vault），
  覆盖上面大部分修复。一条命令验证环境：`python tests/smoke.py`。

### 变更（结构：改为渐进式披露）

`SKILL.md` 原本 1000+ 行，把工作流、配图、排查、格式规范全堆在一起。按 Agent skill 的通行做法
（元数据 → 主干 → 按需加载的 references）重组：

- **`SKILL.md` 精简约 60%**（1037 → 438 行）：只留硬规则、环境、七步工作流主干、任务导航。
  开头新增 **§0 五分钟上手**（六条命令跑通最短路径）。
- **细节拆到 `references/`，正文按需指向**：
  | 新文件 | 内容 | 原位置 |
  |---|---|---|
  | `references/extraction.md` | 提取：路线 A 文本提取 / 路线 B 从 PDF 重建 | §3.2 全节 |
  | `references/figures.md` | 配图：截取还是重画 | §4.2 + §4.3 |
  | `references/transcripts.md` | 录音稿 / 字幕稿 | §4.1 |
  | `references/pitfalls.md` | 故障排查 | §5 全节 |
- 文末新增**仓库文件清单**，读者一眼看到仓库里有什么。
- 结构对照参考了同类 skill 的组织方式（主干 + references）。

### 变更（框架无关化）

目标是让 skill 在 Claude Code / Codex / WorkBuddy / Trae 等**任意 Agent harness** 下都能用：

- 正文**不再写死工具名**（不说「用 Read 工具 / 用 Bash 工具 / 用 Write 工具」），
  改为描述**动作**（「看一眼 / 跑一条命令 / 直接写文件」），由所处环境决定用什么工具完成。
- 删掉框架特有参数与概念（`run_in_background`、`model 档位`、`子代理`）。
- frontmatter 采用 **`description`（中文）+ `description_en`（英文）双字段**结构。
  `agent_created: true` 保留——它对部分 harness 是管理 skill 的必需字段，其余 harness 会忽略未知字段。
- README 补充「不绑定任何 Agent 框架」的说明与多框架安装位置。
- **`scripts/*.py` 的注释与 docstring 同样做了清理**：去掉「本环境的 Read 工具」这类工具名
  与平台假设（`renderpages` / `pdf_layout` / `pdf_panels` / `pptx_deep`），改成「用读图能力 /
  亲眼看一遍」；并删掉未使用的 import（`extract.py` 的 `os` / `unicodedata`、
  `pdf_layout.py` / `pdf_panels.py` 的 `io`）。

### 修复（文档自相矛盾）

- **§3.4.1 与 §4.1 冲突**：前者曾建议把口述内容整理成 `### 课堂实录要点` 单独成节，
  后者明确说这是错误做法。现统一为「**拆散融进对应知识点**」。
- **速览与工作流的步骤顺序不一致**（速览少了「定位/汇报」、查重与成篇位置对调）：统一为
  「定位 → 提取 → 成篇 → 查重 → 写入 → 校验 → 汇报」。
- **§4.3.2 标题写「五条硬要求」而正文有九条**：已改为「九条」，并补齐漏掉的编号。

### 修复（跨平台）
- **`vaultio.py` 此前只支持 Windows**：vault 发现硬编码 `%APPDATA%\obsidian\obsidian.json`，
  在 macOS / Linux 上 `APPDATA` 为空 → 路径退化成相对路径 → **永远找不到任何库**。
  现改为按平台探测配置目录，并支持环境变量覆盖：
  - Windows `%APPDATA%\obsidian\`、macOS `~/Library/Application Support/obsidian/`、
    Linux `$XDG_CONFIG_HOME/obsidian/`（兼容 Snap / Flatpak）；
  - `OBSIDIAN_CONFIG_DIR` 可指定任意包含 `obsidian.json` 的目录（便携版 / 自定义安装）；
  - `vaults` 找不到时打印**尝试过的全部位置**，不再是笼统的一句报错。

### 变更（去平台绑定）
- `SKILL.md` §2 环境：WorkBuddy 托管 Python 路径降为「可选」注记，主流程改为通用的
  `pip install -r requirements.txt`；库位置改为三平台表格 + 环境变量兜底。
- `SKILL.md` §3.2.1：`.ppt` 转换**改为 LibreOffice 优先**（跨平台），
  Windows PowerPoint COM 降为「想要更准还原时的可选加速」。
- `SKILL.md` §4.1：删除宿主框架私有的文档路由约定，改为通用表述
  （「某些 Agent 框架的文档工具读接口有单段长度上限」），并给出量级自查办法。
- `SKILL.md` §4.3.2：字体、配色抽成脚本顶部的常量（`FONT` / `BLUE, RED, GREY, GREEN`），
  换平台只改一行；「没有下标字形」的表述从单一字体推广到常见中文字体。
- `SKILL.md` frontmatter：英文触发词拆成独立的 `description_en` 字段（原先挤在 `description` 里）。
  **`agent_created` 保留**——它是 skill 管理机制要求的字段（见上「变更（结构）」一节），
  不属于要剥离的平台绑定。

### 变更（个人偏好标为默认而非硬规则）
- callout 四类 / 每小节色块上限：注明「默认风格，阈值可调」。
- 挑图标准：演示照片等改为「默认不要，使用者明确要求时照办」。
- 章末要点位置：改为「推荐的 vault 组织方式」，并说明一致性靠自检而非硬性位置。

### 新增
- `README.md`：面向使用者的完整说明（痛点对照表、快速开始、脚本速查、跨系统注意事项）
- `LICENSE`：MIT
- `.gitignore`
- `CHANGELOG.md`
- `requirements.txt`：显式声明 7 个依赖及版本下限

### 变更（可移植性）
- **移除全部个人化与机器绑定内容**，使 skill 可直接被他人使用：
  - 清除硬编码的本机 Python 解释器路径，改为文档化的 `PY` 变量 + 换机器确认清单
  - `references/vault-profile.md` 从个人库档案改写为**通用模板**（含各平台 Obsidian 配置路径）
  - 正文中的个人称谓与课程/教师信息改为中性表述

### 变更（通用化）
- **把课程专属术语抽象为通用说法**，使 skill 面向**各类工科专业**都能直接套用：
  - 原各专业课的具体术语（信号名、逻辑学名词、场量名、人名定理等）一律改为
    「某术语」「概念 A / 概念 B」这类中性占位；
  - 课件图按用途描述（结构图 / 关系图 / 多子图对比图 / 装置图）而非按学科命名；
  - 示例中的具体课程名、应用领域名（如某类图像处理应用）也一并泛化。
- 日期一律改为 `20XX-...` 形式，避免与使用者自己的真实笔记日期混淆。

### 变更（结构化重写）
- **`SKILL.md` 重写为操作手册结构**：
  - 新增 §0 速览与任务导航表，开头即可定位「我要做的事在哪一节」；
  - 统一编号层级：正文按「硬规则 → 环境 → 工作流（1–7 步）→ 材料类型专题 → 故障排查」组织，
    原散落的补丁式小节（如「6.5」「#### 别靠目测」）全部收编进正式层级；
  - **经验结论融入对应步骤的祈使句**，删去全部「血泪教训 / 实测」叙事块——
    同样的教训改写成「先看图，再动笔」「凡数值结果都独立验算」这类可直接执行的动作；
  - 每步给出明确的**判定表 / 对照表**（查重退出码、术语 grep 命中位置、三类症状对策等），
    减少阅读时的心智负担；
  - 故障排查集中到 §5，按「写入 / 行尾编码 / 写文件 / 围栏结构 / 多章笔记 / 其他」分类。
- **补齐若干通用经验**（原只在个人版，去标识化后并入）：
  - 深色课件截图统一转 JPEG（体积可降至 PNG 的三分之一量级），裁图避开页脚；
  - 整章重排 / 重编号应「切段重写」，而非 patch 式增删（附代价说明与判断尺子）；
  - 段落尾部追加易把句子焊死，diff 与 lint 都看不出来，需回读上下文；
  - 占位块滞留会永久谎报覆盖率；
  - 重画硬要求扩充到 9 条（含 `xlim`/`ylim` 静默裁图、固定坐标表、图例锚定、调试残留自查）。
- `references/note-format.md`：示例与目录样例泛化，叙事性表述改为规则陈述。
- `scripts/*.py`：注释中的学科示例与「实测」叙事改为中性描述。

### 说明
- 本版本的能力与内部工作流**未做功能改动** —— 仅调整可移植性、通用性与文档结构。
  所有踩坑结论、规则、脚本行为与个人版完全一致；脚本已通过语法校验。
