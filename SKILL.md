---
name: obsidian-note-ingest
display_name: Obsidian 笔记收录
display_name_en: Obsidian Note Ingest
description: 把学习材料（PPT/PPTX、PDF、Word/DOCX、录音转写稿、字幕稿、图片截图）整理成符合 Obsidian 语法的笔记，追加进课程主笔记或新建笔记写入 Obsidian vault。触发场景：用户说「记笔记」「帮我记一下」「整理成笔记」「整理一下这份」「写进 Obsidian」「归档」「存进笔记」「更新笔记」「这门课的笔记」，或发来课件、讲义、录音稿、字幕稿、课堂笔记草稿、课堂截图需要整理归档时。也用于整理 Clippings 目录里积累的未整理素材。
description_zh: 把课件、PDF、录音稿、字幕等学习材料整理成 Obsidian 笔记，自动去重后追加进课程主笔记。
description_en: 'Turn lecture slides (PPT/PPTX), PDFs, Word/DOCX, transcripts, subtitles, or screenshots into Obsidian-formatted notes: extract, dedupe, and append into a course note or create a new one. Use when the user says "take notes", "organize this into notes", "write into Obsidian", "archive this", or sends courseware, handouts, transcripts, subtitles, or lecture screenshots to be organized.'
category: writing
version: 1.0.5
author: Woo3aN
agent_created: true
---

# Obsidian 笔记收录

把原始学习材料变成能直接读的 Obsidian 笔记，写进目标 vault。

这套 skill 是**纯工具链 + 工作流**，不绑定任何特定 Agent 框架：核心能力都在 `scripts/*.py`
（独立命令行脚本，任何能跑 Python 的环境都能用），正文只描述**做什么**，
具体用哪个工具、哪个模型来完成，由你所处的环境决定。

> 搬家 / 换机看 `README.md`；依赖清单在 `requirements.txt`；仓库文件清单见**文末**。

## 0. 五分钟上手

第一次用，先跑通这条最短路径：

```bash
$PY -m pip install -r requirements.txt                        # 1. 装依赖
$PY $S/vaultio.py vaults                                      # 2. 确认能看到你的库
$PY $S/extract.py "<材料.pptx>" --stats --out draft.md         # 3. 提取（临时稿放当前目录即可）
# 4. 通读 draft.md，按 references/note-format.md 的规范整理成正文 → note.md
$PY $S/vaultio.py check "<笔记>" --content-file note.md        # 5. 查重（必做，不能跳）
$PY $S/vaultio.py new "<vault>/<课程>/<笔记>.md" --title "标题" --body-file note.md
$PY $S/obsidian_lint.py "<笔记>"                               # 6. 体检，错误必须为 0
```

**任务导航**

| 你要做的事 | 去哪 |
|---|---|
| 首次使用 / 换机器 | §2 环境；仓库文件清单见文末 |
| 整理一份新材料 | §3 工作流（1→7 顺序走） |
| **同一份材料出了新版**（答案版 / 修订版） | §3.4；`$S/pdfdiff.py 旧.pdf 新.pdf` 定位改动页 |
| 一处笔记要改很多地方 | §3.5 `patch`（一次落盘 = 一次备份 + 一次 diff） |
| 目录区 | **不建**（Obsidian 自带大纲）；旧笔记要更新才跑 §3.5 `toc` |
| 提取失败 / PPTX 漏表格公式 / 公式型 PDF / 老 `.ppt` | `references/extraction.md` |
| 材料是录音稿 / 字幕稿 | `references/transcripts.md` |
| 要把课件图放进笔记 / 重画图表 | `references/figures.md` |
| 笔记格式（frontmatter / 标题层级 / callout） | `references/note-format.md` |
| 写入报错 / 笔记改坏了 / lint 报怪行号 | `references/pitfalls.md` |

## 1. 三条硬规则

1. **原材料只读。** 用户发来的任何文件、`Clippings/` 里的字幕稿，只读、不改、不删、不移动。
2. **改动已有内容前先给用户看。**
   - 往已有笔记**追加新章节**（原有文字一行不动）→ 直接做，做完给 diff 摘要。
   - **修改 / 删除 / 重排已有文字** → 先给变更方案（`replace --dry-run` 生成预览），确认后再动手。
   - 把「待学」占位块换成正式内容也属修改，照走确认流程；但一次把「动第几行到第几行、换成什么」列清楚问一遍，别反复来回。
3. **机械的交给脚本，判断的留给自己。** 时间戳、断行合并、查重统计归脚本；「哪些内容无关、怎么分层、补什么标点」必须亲自读、亲自判断。

## 2. 环境

```bash
S="<本 skill 目录>/scripts"      # SKILL.md 所在目录下的 scripts/
```

**`PY` 优先用托管环境，别随手挑一个解释器**（2026-10-11 踩过）：

| 平台 | 推荐解释器 |
|---|---|
| Windows | `%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe` |
| macOS / Linux | `~/.workbuddy/binaries/python/envs/default/bin/python` |

托管环境已装齐全部依赖。**随手挑的裸解释器（如 `binaries/python/versions/<版本>/python.exe`）
往往缺 jieba** —— 此时 `check` **不报错、只淡淡提一句就静默降级**，
吐出 `ABA`、`ABC`、`BAB` 这类噪声当「尚未出现的术语」，
**据此判断「有没有增量」会完全走偏**。所以**先自检、再干活**：

```bash
PY="<上表的解释器>"
$PY -c "import importlib.util as u; print('jieba', 'OK' if u.find_spec('jieba') else '缺失（查重不可信！）')"
```

脚本只依赖标准库 + `pypdf / python-pptx / python-docx / pymupdf / Pillow / numpy / PyYAML / jieba`。

```bash
$PY -m pip install -r requirements.txt
$PY -c "import importlib.util as u; [print(f'{m:9} ' + ('OK' if u.find_spec(m) else '缺失')) for m in ['pypdf','pptx','docx','pymupdf','PIL','numpy','yaml','jieba']]"
```

（`find_spec` 只探测不导入，缺包时打印「缺失」而不是抛异常。`jieba` 缺失不会让脚本崩，
但查重会退化成 n-gram、关键词质量下降。）

**`S` 用相对定位即可**：脚本之间靠 `sys.path` 相互引用，只要**整个 skill 目录**一起搬，内部关系不受影响。

**库（vault）位置不用写死**：`vaultio.py` 按平台自动定位 Obsidian 的 `obsidian.json`
（Windows `%APPDATA%\obsidian\`、macOS `~/Library/Application Support/obsidian/`、
Linux `~/.config/obsidian/`）。便携版 / 自定义安装用环境变量指定**包含 `obsidian.json` 的目录**：
`export OBSIDIAN_CONFIG_DIR="<目录>"`。
`vaultio.py vaults` 找不到库时会打印它尝试过的所有位置，照着排查即可。

---

# 3. 工作流

七步顺序执行：**定位 → 提取 → 成篇 → 查重 → 写入 → 校验 → 汇报**。

## 3.1 定位目标笔记

```bash
$PY $S/vaultio.py vaults                    # 本机有哪些库
$PY $S/vaultio.py find <vault> <关键词>      # 按关键词找笔记
$PY $S/vaultio.py outline "<笔记路径>"       # 看标题树，决定插到哪个锚点
```

库里没有对应笔记 → 先问用户「这是 XX 的材料，库里没有对应笔记，要新建吗？」

## 3.2 提取材料

```bash
$PY $S/extract.py "<材料路径>" --stats --out "<临时文件>"
```

- 提取完**通读全文**再动手。不理解内容就必然删错东西。
- PPTX 课件、公式型 PDF、老 `.ppt` 都要额外处理 —— **完整做法读 `references/extraction.md`**。
- 公式型 PDF 先分清是**字体坏了**还是**版面散架**：前者（中文数字正常、只有数学字母乱）
  **文本层无解，只能渲染成图**，别去折腾提取工具 —— 见 `extraction.md` 路线 **B0**。

**最容易埋雷的一步**：课件类 PPTX 用普通提取会**静默漏掉表格和公式**，而它们正是笔记的骨架。
凡课件材料，提取后必须再跑一遍 `pptx_deep.py`（见 `references/extraction.md` 路线 A）。

## 3.3 整理成笔记正文

先读 `references/note-format.md` 拿格式规范，严格按它写；**要配图再读 `references/figures.md`**
（别凭记忆写绘图代码 —— `xlim`/`ylim` 静默裁掉图元、相对累加导致整片错位，这两条已反复踩过）。

**删掉**：开场寒暄、口头禅、重复表述、跑题闲聊、结尾告别语、人物轶事与历史背景、老师的自我感想。
**保留**：概念与定义、原理与推导、例题与解法、代码、易错点、能帮解题 / 帮理解的判断与方法。
**课程事务不删**：作业、考试范围 / 时间 / 分值收进章节末尾的 `> [!warning]` callout。
**补全**：字幕稿按语义补标点；术语补英文原词；散落的知识点归并成小节。

**标题照抄材料的页 / 节标题**（`一、某某变换`、`某某的性质`），别概括成「XX 是怎么引进来的」
这类自造句；材料确实没有（口头补充、复习串讲）才自拟，且要短。细则见 `references/note-format.md` §三。

**两把尺子（缺一不可）**：三个月后回看还有用吗？删掉它会丢知识点 / 易错点 / 方法 / 记忆钩子吗？
都不会 → 删。**「老师说的」≠「该进笔记的」**，细则见 `references/transcripts.md`。

**色块（callout）要节制——最重要的一条写作约定。** 只有**重要定义 / 核心公式 / 易错点 / 结论**四类才套；
例题、推导过程、普通说明、旁注一律走正文。一个最小小节（`####` 级）**2–3 个最舒服、超过 5 个就该降级一部分**。
细则见 `references/note-format.md` 第五节。

**凡是数值结果，一律独立验算后再落笔。**

- **材料之间可能互相矛盾，甚至材料本身印错。** 同一个量出现两个不同值时，必须自己算一遍再决定采信哪个。
  **动作**：① 检查数值量级（可能整体错一个数量级）；② 确认变量指代是否一致（字母可能被对调）。
  **以更权威的那份为准，把不一致处报给使用者。**
- **材料自己的例题也会印错。** 凡是给定的计算结果（尤其带进制的换算、范围边界），
  落笔前都用另一种方法反算一遍，别因为「这是原文」就直接抄；
  算不通就按正确值写，并在笔记里留一条 `[!warning]` 说明原文笔误，汇报时一并告诉使用者。

## 3.4 写入前查重（必做，不能跳）

```bash
$PY $S/vaultio.py check "<笔记>" --content-file "<整理稿>"
```

先写出整理稿的临时文件，再跑查重，按退出码决定下一步。
**唯一豁免**：新章（章标题在 `outline` 里搜不到）直接写 —— 此时 `check` 报的高覆盖率是虚的；其余一律先跑。

| 退出码 | 覆盖率 | 含义 | 动作 |
|--------|--------|------|------|
| `13` | ≥80% 且**原句被照抄** | **同一份材料的新版本**（答案版 / 修订版） | **逐处替换**变了的地方，见下 |
| `10` | ≥80% | 已被覆盖，但句子对不上（换了措辞） | **不要写**，只挑 missing 里的术语做补充 |
| `11` | ≥50% | 部分重叠 | 只补 missing 里的新内容，别整段照搬 |
| `0` | <50% | 新内容 | 正常写入 |

`13` 和 `10` 的关键词覆盖率**都很高**，差别在句子：**原句被照抄 = 材料出了新版本**（该更新），
**换了措辞 = 重复整理**（该丢）。脚本用「句子级近重复占比」区分，≥60% 判 13。

**退出码 13 的动作**：

1. `$S/pdfdiff.py "<旧版.pdf>" "<新版.pdf>"` —— 直接给出「哪几页变了」和可粘贴的页码列表
2. 只把那几页渲染出来看图核对：`$S/renderpages.py "<新版.pdf>" --pages <上一步的列表> --out <目录>`
3. 把每条改动写进 `edits.txt`，**一次落盘**：`$S/vaultio.py patch "<笔记>" --edits-file edits.txt`
4. 上一轮「课件无答案 → 整理时自算」的答案，这轮拿到官方版后回头核对，
   **并明确告诉用户哪几题原本算错了**——用户最在意的就是这句

报告里的「★ 笔记里尚未出现的术语」同样是判断价值的关键：如果列出的都是「图片、看到、用到、讨论」这类虚词，
说明这份材料的实质内容早就整理进笔记了。**同一门材料被重复整理过是常见情况**，
跳过查重必然往笔记里灌重复章节、制造锚点冲突。

### 3.4.1 覆盖率会骗人：挑核心术语 grep 一遍

**永远不要只看百分比。** 拿到 10/11 时，先挑几个**本章特有**（不是通用词）的术语搜一遍。
术语**不用现想**——把 `check` 报的「★ 笔记里尚未出现的术语」整条粘进来即可：

```bash
cd "<笔记目录>" && for w in 术语A 术语B 术语C; do printf "%-12s %s\n" "$w" "$(grep -c "$w" 笔记.md)"; done
```

多数为 0 → 新内容，照常写入；只有「系统、结构」这类**通用词**有命中而专业词全 0，同样算新内容。
（实测连续四次判定「不要写」的稿子，都是这么翻案的 —— 所以**别信百分比**。）
**脚本会帮你一把**（2026-10-16 起）：判定「正文覆盖」时已排除**标题 / 色块首行 /
占位预告块（`[!todo]`、`[!info]`）块内 / 文末「相关链接」导航段**这几类 ——
术语若只出现在那些地方，会落进 **⚠ 弱命中** 那一行，而不是被算成已覆盖。
另一坑：`check ... | head` 之后取 `$?` 拿到的是 **head 的**退出码、恒为 0，13/10/11 的判定通道等于废掉——要看退出码就别加管道。

| 命中位置 | 判定 |
|---|---|
| 命中在**正文**里 | 真的已覆盖，只补 missing |
| 只在**占位符 / 预告 / 目录**里 | 按「新内容」正常写入 |
| 一个都没命中 | 按「新内容」正常写入 |

**四种**典型的虚高 —— 共同结论：**别信百分比，信 grep 与逐段自问**：

| 虚高类型 | 症状 | 动作 |
|---|---|---|
| **占位符** | `[!todo]` / 预告块本身就把术语写进去了，报 80%~95% 而正文空空 | 按上表判定，正常写入 |
| **同批知识点的两种讲述** | 课件 vs 录音讲同一回事，术语必然全重合（报 98%） | 口述的**比喻 / 技巧 / 动机 / 金句**才是增量，**拆散融进知识点**（`references/transcripts.md`） |
| **通用词** | 命中的全是「系统 / 结构 / 过程」这类全篇通用的词 | 同样按上表判定 |
| **同主题第二次课** | 连 grep 也救不了——主题词本来就在笔记里，新增的是口述类比与引申 | **逐段自问「这段话的实质内容，笔记里有吗」**；**并入**已有章节，别从头再写 |

### 3.4.2 整理完要清掉占位块

**占位块直接删掉，不要改写成「还剩 XX 待整理」。** 整理完一个新章节后，连同这几处一起清：

1. `[!todo] 待整理` 块 —— **整块删除**，不要保留、也不要改成「仅剩 §X 待整理」；
2. 顶部「本笔记范围」callout 里那一行待整理描述 → 换成**已整理内容的实际覆盖范围**（**先改这里再删块**，两者同一个 `patch`）；
3. 「相关链接」里的 `下一节：…（待整理）` → 换成真正的下一节标题；
4. frontmatter 的 `updated:` → 改成当天日期。

改 1~4 都属于「修改已有文字」，按硬规则 2 要先问；一次把清单列清楚问一遍即可。
**3 和 4 别漏**：漏了就会出现「笔记已写完、末尾还写着待整理」的自相矛盾。

**占位块留着会永久谎报。** 像 `> [!todo] 本节录音尚未覆盖` 这类块，里面写着「某主题按课件整理」，
于是该主题的词在笔记里一搜就有；等后来真的补上了录音、**忘了删占位块，就等于永远在骗下一个
查重、下一个来读你的人**。**动作**：往带占位块的章节里补内容时，把删块写进**同一个脚本**、
和其他替换一起 assert 落盘。

## 3.5 写入

**追加新内容**（原有文字一行不动）：

```bash
# 先预览（推荐，硬规则 2 要求改已有内容时必做）
$PY $S/vaultio.py insert "<笔记>" --anchor "### 章节标题" \
    --content-file "<整理稿>" --dry-run
# 确认后落盘（自动备份 + 打印 diff）
$PY $S/vaultio.py insert "<笔记>" --anchor "### 章节标题" --content-file "<整理稿>"
```

- `--at end-of-section`（默认）：追加到该章节**末尾**，适合新增小节
- `--at after-heading`：紧跟标题插入，适合补在章节开头

- **锚点必须唯一**：笔记里已存在同名标题时**直接报错退出、不落盘**（与 `replace` 一致）——
  先修掉重名标题，或换一个更完整的锚点。
- **插入内容里若含笔记已有的标题**同样会被拦截（会制造锚点冲突）；
  确认无问题（如多章笔记确实要新增同名小节）才加 `--allow-duplicate`。
- 内容与现有章节无关、直接接在文件末尾时用 `append`：
  `$PY $S/vaultio.py append "<笔记>" --content-file "<整理稿>"`
- **追加新章 / 新讲，同时要改文末「相关链接」导航时**：别 `append` 完再单独改链接（两次备份、两次 diff）。
  直接把「新章正文 + 新版导航」拼进同一个 content 文件，
  用 `replace --anchor "## 相关链接"` **一次替换掉文末那一块** —— 一次落盘、一次备份。

> `--dry-run` 四个写入命令都支持（`insert` / `append` / `replace` / `patch`）；
> 都默认打印 diff，不想看加 `--no-diff`。

**替换已有内容块**（把「待学」占位块换成正式内容、重写某一节）：

```bash
# 先预览（硬规则 2 要求）
$PY $S/vaultio.py replace "<笔记>" --anchor "### 某小节（待学）" \
    --content-file "<整理稿>" --dry-run
# 确认后落盘（自动备份 + 打印 diff）
$PY $S/vaultio.py replace "<笔记>" --anchor "### 某小节（待学）" \
    --content-file "<整理稿>"
```

- `--anchor` 起始行**整行精确匹配**，必须唯一，否则报错并列出所有命中行（含全角括号的标题尤其要核对）。
- 不给 `--until` → 替换到**下一个同级/更高级标题前**；给了 `--until` → 替换到该标记所在行（含该行）。
- 值以 `-` 开头时用 `--anchor="---"` 等号写法，否则 argparse 会当成选项。





**目标笔记存在但为空（0 字节）**——常见于用户先手工建了课程主笔记、再让整理第一份材料：

- 空文件没有「已有文字」可动，**不算修改已有内容**，不需要确认流程；直接 `append` 写入整篇
  （frontmatter + H1 + 范围 callout 一起带上）。
- 顺手确认文件名与 H1、frontmatter `title` 三者一致（这个文件通常是用户按课程名建的）。
- `check` 会报覆盖率 0% / 退出码 0，照常写入。
- `backups` 会显示一份 0 字节的备份，这是正常的，不要以为备份失败。

**新建笔记**：

```bash
$PY $S/vaultio.py new "<vault>/<课程>/<名称>.md" --title "标题" \
    --tags "计算机,课程笔记" --body-file "<整理稿>"
```

**一处笔记要改很多地方 → 用 `patch`，一次落盘**（别拆成十几次 `replace`）：

```bash
$PY $S/vaultio.py patch "<笔记>" --edits-file edits.txt --dry-run   # 先预览
$PY $S/vaultio.py patch "<笔记>" --edits-file edits.txt             # 落盘（自动备份 + diff）
```

`edits.txt` 每处改动用三行隔开，多行内容不用转义；「追加」就把原文一起写进新块：

```
<<<<<<< OLD
（笔记里现有的原文，必须逐字符一致且全文只出现一次）
=======
（换成什么）
>>>>>>> NEW
```

（`patch` 的 `OLD` 必须从 Read 结果复制、不能凭记忆 —— 见 `references/pitfalls.md`。）

**任一原文没有唯一命中 → 整批不落盘**（连前面合格的也不写），并逐条报出命中次数。
好处是**一次备份、一次 diff、只问用户一遍**。

**vaultio 表达不了的编辑**（把某段搬到别处、给一整章重新排号）：先试 `patch`（删一处 + 别处插一处）；
只有它也不行时才用**字节级替换**或**切段重写** —— 两条路都绕过 vaultio、没有自动备份，
动手前先手工备份，做法见 `references/pitfalls.md` §绕路方案。
**判断尺子：只往章节里加内容 → 用 `insert`（diff 干净）；要动编号或顺序 → 切段重写。**

## 3.6 校验

**先对整理稿跑一遍，再落盘。** `obsidian_lint.py` 不要求文件是完整笔记——没有 frontmatter、
没有 H1、只有一节片段也能跑（实测通过，不会误报）。所以**把刚写好的整理稿临时文件直接丢给它**：

```bash
$PY $S/obsidian_lint.py "<整理稿.md>"    # 落盘前：把 ** 残留、KaTeX 宏、表格列数一次挑干净
$PY $S/obsidian_lint.py "<笔记>"         # 落盘后：整体再体检一次
```



> [!warning] 整理稿**不要以 `---` 开头**
> 片段稿若第一行是 `---`，lint 会把它当成 frontmatter 开始，报
> 「frontmatter YAML 解析失败: expected '<document start>'」。整理稿从 `###` 标题或正文起头，
> 章与章之间的 `---` 等落盘后再由 `patch` 的 NEW 块补上（2026-10-13 踩）。

**[错误]** 必须修（死链、代码围栏未闭合）；**[警告]** 视情况；**[建议]** 可忽略。

**lint 会查**：色块密度（全文每 20–25 行一个才健康，< 20 报建议、< 15 报警告）、单个 `####` 里
callout 超过 5 个、callout 类型、**KaTeX 宏 / 环境是否支持**、**`**` 是否被渲染成字面量**。
后两条**只认渲染结果、源码里看不出来**，所以写完必须跑 lint。细则见 `references/note-format.md`。

被报了密度或小节超标，用 `flatten_callouts.py` 收口（**别手改，它带备份和预览**）：

```bash
$PY $S/flatten_callouts.py "<笔记>" --list                            # 列全部色块（行号+类型+标题+所在小节）
$PY $S/flatten_callouts.py "<笔记>" --kinds example,note --dry-run    # 整类降级：先预览
$PY $S/flatten_callouts.py "<笔记>" --kinds example,note              # 落盘（自动备份）
$PY $S/flatten_callouts.py "<笔记>" --lines 74,240,758 --plain        # 按行号挑
```

⚠️ `--plain` 会**连标题一起删** —— 想保留标题就手写 `patch` 把 `> [!tip] 标题` 改成 `**标题**` + 正文；
`--list` 只列清单、不给密度汇总，要看「按小节分布 / 超 5 个的小节」跑 `note-format.md` 第六节的统计脚本。

**校验结果要和插图前对比「标题数 / 表格数」。** 围栏一旦错配（哪怕只多一个孤立的 ` ``` `），
lint 会把它之后的所有内容当成代码块，标题数、表格数会**成片下跌**，而报错可能只有一条。
只看「有没有报错」容易漏判——**先记下改动前的标题数与表格数，改完再比一遍**。
（lint 会把 callout 里的表格和 `> ### 标题` 一并算进去。）

## 3.7 汇报

说清四件事：改了哪个文件、加了什么、备份在哪、怎么回滚。三五行，别写长篇报告。

```bash
$PY $S/vaultio.py backups "<笔记>"     # 列出备份
$PY $S/vaultio.py restore "<笔记>"     # 恢复最近一次
$PY $S/vaultio.py restore "<笔记>" --stamp 20260914-222743   # 恢复到指定时间点
$PY $S/vaultio.py backups "<笔记>" --prune --keep 10   # 清理旧备份（每次写入都存一份，会越堆越多）
```

---

# 4. 其他材料类型

这两类材料有专门的处理要点，**遇到时按需打开**：

| 材料 | 读这个 | 一句话 |
|---|---|---|
| 录音稿 / 字幕稿 | `references/transcripts.md` | 补标点；口述内容**拆散融进对应知识点**，不要单列一节 |
| 课件插图 / 需要重画 | `references/figures.md` | 能干净裁就裁；图要**贴在它解释的公式旁边** |

# 5. 故障排查

写入报错、笔记被改坏、lint 报了看不懂的行号，或要做「挪动段落 / 整章重排」这类 vaultio
表达不了的编辑时 → **`references/pitfalls.md`**：按「写入相关 / 写入细节（含绕路方案）/
行尾与编码 / 围栏与结构 / 其他」分类，含**还原备份的办法**。

---

## 附：文件地图

- **`README.md`** —— 目录结构、各脚本用途、安装与换机（**文件清单的唯一来源**，不在别处再抄一份）
- **`references/`** —— 按需读取的细节，主流程不必全看：
  `extraction.md`（提取两条路线）· `figures.md`（配图：截取还是重画）·
  `transcripts.md`（录音稿 / 字幕稿）· `pitfalls.md`（故障排查）·
  `note-format.md`（笔记格式规范）· `vault-profile.md`（库档案）
- **`tests/smoke.py`** —— 冒烟测试，临时目录里跑，不碰你的 vault
