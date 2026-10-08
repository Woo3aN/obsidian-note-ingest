---
name: obsidian-note-ingest
description: 把学习材料（PPT/PPTX、PDF、Word/DOCX、录音转写稿、字幕稿、图片截图）整理成符合 Obsidian 语法的笔记，追加进课程主笔记或新建笔记写入 Obsidian vault。触发场景：用户说「记笔记」「帮我记一下」「整理成笔记」「整理一下这份」「写进 Obsidian」「归档」「存进笔记」「更新笔记」「这门课的笔记」，或发来课件、讲义、录音稿、字幕稿、课堂笔记草稿、课堂截图需要整理归档时。也用于整理 Clippings 目录里积累的未整理素材。
description_en: 'Turn lecture slides (PPT/PPTX), PDFs, Word/DOCX, transcripts, subtitles, or screenshots into Obsidian-formatted notes: extract, dedupe, and append into a course note or create a new one. Use when the user says "take notes", "organize this into notes", "write into Obsidian", "archive this", or sends courseware, handouts, transcripts, subtitles, or lecture screenshots to be organized.'
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
| 重建目录区 | §3.5 `toc`（**先 `--dry-run`**） |
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
PY="<你的 Python 解释器>"        # 建议 3.10+
```

脚本只依赖标准库 + `pypdf / python-pptx / python-docx / pymupdf / Pillow / numpy / PyYAML / jieba`。

```bash
$PY -m pip install -r requirements.txt
$PY -c "import importlib.util as u; [print(f'{m:9} ' + ('OK' if u.find_spec(m) else '缺失')) for m in ['pypdf','pptx','docx','pymupdf','PIL','numpy','yaml','jieba']]"
```

（`find_spec` 只探测不导入，缺包时打印「缺失」而不是抛异常。`jieba` 缺失不会让脚本崩，
但查重会退化成 n-gram、关键词质量下降。）

**`S` 用相对定位即可**：脚本之间靠 `sys.path` 相互引用，只要**整个 skill 目录**一起搬，内部关系不受影响。

**库（vault）位置不用写死**：`vaultio.py` 按平台自动定位 Obsidian 的配置文件：

| 平台 | 配置文件位置 |
|---|---|
| Windows | `%APPDATA%\obsidian\obsidian.json` |
| macOS | `~/Library/Application Support/obsidian/obsidian.json` |
| Linux | `$XDG_CONFIG_HOME/obsidian/obsidian.json`（默认 `~/.config/obsidian/`，兼容 Snap / Flatpak） |

不在上述位置（便携版、自定义安装）时，用环境变量指定**包含 `obsidian.json` 的目录**：

```bash
export OBSIDIAN_CONFIG_DIR="<目录>"     # Windows PowerShell: $env:OBSIDIAN_CONFIG_DIR="<目录>"
```

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

**最容易埋雷的一步**：课件类 PPTX 用普通提取会**静默漏掉表格和公式**，而它们正是笔记的骨架。
凡课件材料，提取后必须再跑一遍 `pptx_deep.py`（见 `references/extraction.md` 路线 A）。

## 3.3 整理成笔记正文

先读 `references/note-format.md` 拿格式规范，严格按它写。

**删掉**：开场寒暄、口头禅、重复表述、跑题闲聊、结尾告别语、人物轶事与历史背景、老师的自我感想。
**保留**：概念与定义、原理与推导、例题与解法、代码、易错点、能帮解题 / 帮理解的判断与方法。
**课程事务不删**：作业、考试范围 / 时间 / 分值收进章节末尾的 `> [!warning]` callout。
**补全**：字幕稿按语义补标点；术语补英文原词；散落的知识点归并成小节。

**两把尺子（缺一不可）**：三个月后回看还有用吗？删掉它会丢知识点 / 易错点 / 方法 / 记忆钩子吗？
都不会 → 删。**「老师说的」≠「该进笔记的」**，细则见 `references/transcripts.md`。

**色块（callout）要节制——这是最重要的一条写作约定。** 整页都是彩色块 = 重点等于没有重点。
**只有四类才套 callout**：**重要定义**、**核心公式**、**易错点**、**结论/要点**。
**例题、推导过程、普通说明、补充旁注一律用正文**（`####` 小标题 + `**加粗**` + 公式块），不要再套一层。
经验值：**一个最小小节里 2–3 个最舒服，超过 5 个就该把一部分降级成正文**（lint 按 5 提示），
且按整个 `####` 通盘数——它下面所有 `#####` 例题里的色块也算在内。
（这是**默认风格**、不是硬性规范：`obsidian_lint.py` 的阈值可调。细则见 `references/note-format.md` 第五节。）

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

先写出整理稿的临时文件，再跑查重，按退出码决定下一步：

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

**永远不要只看百分比。** 拿到 10/11 时，先挑 2~3 个**本章特有**（不是通用词）的术语搜一遍：

```bash
grep -n "本章特有术语A\|术语B\|术语C" "<笔记>"
```

| 命中位置 | 判定 |
|---|---|
| 命中在**正文**里 | 真的已覆盖，只补 missing |
| 只在**占位符 / 预告 / 目录**里 | 按「新内容」正常写入 |
| 一个都没命中 | 按「新内容」正常写入 |

**四种**典型的虚高（占位符、口述增量、通用词、第二次课串讲——
共同结论：**别信百分比，信 grep 与逐段自问**）：

1. **占位符虚高。** `[!todo]` / 预告 / 骨架块为了说清要学什么，本身就把术语写进去了
   （列出该节的关键名词、公式名、定理名）。这些词一命中就报 80%~95%、判「不要写」，
   **而笔记里一个字的实质内容都没有**。按上表判定即可。
2. **同批知识点的两种讲述。** 同一章的课件 vs 课堂录音讲的是同一回事，术语必然全部重合，报 98%。
   但老师口述的**比喻、解题技巧、动机、金句**课件里一个字都没有。挑 3~5 个只有口述才会出现的说法
   grep 一遍，全为 0 就说明有增量。**这类口述增量恰是最值钱的部分（记忆钩子），别因为覆盖率 98% 就丢掉**——
   按 `references/transcripts.md` 的做法**拆散融进对应知识点**。
3. **通用词虚高。** 虚高往往来自「系统、函数、结构、模型、过程、响应」这类全篇通用的词。同样按上表判定。

**同主题的第二次课（复习 / 串讲）连 grep 也救不了**：主题词本来就在笔记里，百分比高得理直气壮，
**而实质新增的内容一个关键词都抽不出来**——老师补充的口述类比与直观、课件没有的补充应用、老师自己的引申。
**动作**：别信百分比，**逐段自问「这段话的实质内容，笔记里有吗」**；这类材料应**并入**已有章节
（§3.5 `replace`：content 写成「原段落 + 新增内容」），不是当新材料从头再写一遍。

### 3.4.2 整理完要清掉占位块

**占位块直接删掉，不要改写成「还剩 XX 待整理」。** 整理完一个新章节后，连同这几处一起清：

1. `[!todo] 待整理` 块 —— **整块删除**，不要保留、也不要改成「仅剩 §X 待整理」；
2. 顶部「本笔记范围」callout 里那一行待整理描述 → 换成**已整理内容的实际覆盖范围**；
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
$PY $S/vaultio.py toc "<笔记>" --dry-run   # 有目录区时：先看会改什么
$PY $S/vaultio.py toc "<笔记>"             # 确认后落盘（默认沿用笔记现有排版）
```

- `--at end-of-section`（默认）：追加到该章节**末尾**，适合新增小节
- `--at after-heading`：紧跟标题插入，适合补在章节开头
- **锚点必须唯一**：笔记里已存在同名标题时**直接报错退出、不落盘**（与 `replace` 一致）——
  先修掉重名标题，或换一个更完整的锚点。
- **插入内容里若含笔记已有的标题**同样会被拦截（会制造锚点冲突）；
  确认无问题（如多章笔记确实要新增同名小节）才加 `--allow-duplicate`。
- 内容与现有章节无关、直接接在文件末尾时用 `append`：
  `$PY $S/vaultio.py append "<笔记>" --content-file "<整理稿>"`

> `--dry-run` 三个写入命令都支持（`insert` / `append` / `replace`）；
> 三者默认都打印 diff，不想看加 `--no-diff`。

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

**改写单行（frontmatter、callout 内一行、正文某句）必须配 `--until`，值取同一个 anchor**：

```bash
$PY $S/vaultio.py replace "<笔记>" --anchor "X" --until "X" --content-file "<新内容>" --dry-run
```

这类行**不受「下一个标题」约束**，省略 `--until` 会一路替换到下一个 `##` 之前，把中间若干行全吃成一行。
**症状是 `--dry-run` 的 diff 里出现你没打算动的行。**
**动作：凡是改单行，一律 `--dry-run` 先看「第 X–Y 行」，`X != Y` 就说明范围超了，立刻补 `--until`。**

**`--until` 是「子串包含」匹配，不是整行匹配**，所以：

- 想匹配**代码块收尾围栏**时，它会先命中开头的 ` ```text ` 行（那行本身含三个反引号），
  于是只替换开头一行、把代码块拆散。正解：把 `--anchor` 往前挪到代码块的**上一段**，再配 `--until "```"`。
- **`--until "---"` 会吃掉一大段**：笔记里的**表格分隔行 `|------|------|` 也包含 `---`**，会被先命中。
  **`--until` 只用于独一无二的文字锚点**；`---` / ` ``` ` / `|` 这类全文反复出现的字符一律换成一句独有的正文。
  argparse 会把 `---` 当选项，必须写成 `--until=---`。

**`replace` 的 content 要分清「追加」还是「改写」**（写错很隐蔽）：

- **追加**（往范围 callout 补一行、往某段后加一段）：`content = 原行 + "\n" + 新行`
- **改写**（改 frontmatter 日期、换掉「下一章」链接）：`content = 新行`，**绝不要把原行也带上**

图省事把两种情况统一写成「原行 + 新行」，会导致 frontmatter 出现**两个 `updated:` 键**、
「相关链接」出现**两行「下一章」**；YAML 重复键不报错，Obsidian 也只会静默取其一。
**改完务必 `diff` 一遍，看到「本该被替换的行还在」就要警觉。**

**在段落尾部「追加」时极易把句子焊死。** 若把某句当成追加锚点，新内容会在句号处接上旧句子的剩余部分，
产生「……价值估计。：无论是一般搜索问题还是……」这种双标点乱句。
**lint 不会报**、diff 也只显示「删 1 行加 1 行」，**只有自己再读一遍才看得见**。
**动作**：凡是在段落尾部追加，追加完把那一整段连同**下一段开头**一起读一遍；
或者干脆改掉整段（`content = 新全文`）而不是在尾巴上接。

**`insert --at end-of-section` 会插到章节末尾那条 `---` 之后**（`section_bounds` 只回退连续空行，
不回退分隔线）。想插在分隔线**之前**，用下面 §3.5.1 的字节级替换。

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

**任一原文没有唯一命中 → 整批不落盘**（连前面合格的也不写），并逐条报出命中次数。
好处是**一次备份、一次 diff、只问用户一遍**。

### 3.5.1 挪动段落：字节级精确替换

vaultio 的 anchor/until 模型表达不了「把某段搬到别处」。**先试 `patch`**：删一处 + 在别处插一处，
两条各写一节就行，而且照样有自动备份。只有 `patch` 也表达不了时，才手写字节级替换：

```python
s = io.open(p, 'rb').read().decode('utf-8')
assert '\r\n' not in s, 'CRLF 混入'            # 先确认行尾没被改
assert s.count(old) == 1, '模式不唯一，中止'   # 唯一性断言是关键，错了就报错而不是乱改
io.open(p, 'wb').write(s.replace(old, new).encode('utf-8'))
```

注意 `'rb'` 读、`'wb'` 写——这样不经过 Windows 的换行翻译，LF 不会被改成 CRLF。
**这条路绕过了 vaultio，没有自动备份**，动手前先手工备份一份。

### 3.5.2 重排 / 重编号一整章：切段重写

给「原来只有无编号标题、现在要正式编成 §3.1~§3.10」这类章节排号时，
`insert` / `replace` 的 anchor-until 模型表达不了「旧标题改名 + 新块插入 + 后续编号整体位移」。
patch 式做法的典型翻车：新旧编号撞车（文件里同时存在两个 `## 3.1`），lint 报「同级重复标题」。

**正解：把笔记切成三段 —— head（章前）/ 本章 / tail（章后的小结+相关链接），本章整段重写成干净文本，
再三段拼回。**

```python
N = "<笔记路径>"
s = io.open(N, 'rb').read().decode('utf-8')
i, j = s.index('# 第三章 某主题'), s.index('## 第三章小结')

os.makedirs('_parts', exist_ok=True)
io.open('_parts/head.md', 'wb').write(s[:i].encode('utf-8'))    # 章前
io.open('_parts/tail.md', 'wb').write(s[j:].encode('utf-8'))    # 章后

# ① 把本章重写成干净文本 → _parts/ch.md（旧的 s[i:j] 由你根据需求重写）
# ② 拼回 head + ch + tail
new = (open('_parts/head.md', encoding='utf-8').read()
       + open('_parts/ch.md',   encoding='utf-8').read()
       + open('_parts/tail.md', encoding='utf-8').read())
assert '\r\n' not in new, 'CRLF 混入'
io.open(N, 'wb').write(new.encode('utf-8'))                     # 'wb' 写入，LF 不被换成 CRLF

# ③ 逐行核对 head / tail 与备份完全一致（只允许 updated: 那类预期改动）
assert open('_parts/head.md', encoding='utf-8').read() == s[:i], 'head 被意外改动'
assert open('_parts/tail.md', encoding='utf-8').read() == s[j:], 'tail 被意外改动'
```

**代价要交代清楚**：切段重写会让 `diff` 里本章的**所有行都显示为改过**，看不出「只动了哪几处」。所以务必：

1. 动手前**手工备份**一份（此时绕过 vaultio，没有自动备份）；
2. 拼回后**逐行核对 head/tail 与备份完全一致**（只允许 `updated:` 那类预期改动）；
3. 汇报时明确说「本章为整段重写」。

**判断尺子：只是往章节里加内容 → 照常用 `insert`（diff 干净）；要动章节的编号或顺序 → 切段重写。**

## 3.6 校验

```bash
$PY $S/obsidian_lint.py "<笔记>"
```

**[错误]** 必须修（死链、代码围栏未闭合）；**[警告]** 视情况；**[建议]** 可忽略。

**lint 盯四件与「好读」有关的事**：

1. **色块密度**：全文平均每 20–25 行一个才算健康；< 20 报建议，< 15 报警告。
2. **单个最小小节超标**：一个 `####`（没有 `####` 时才算 `###`）里的 callout 超过 5 个才提示。
   阈值有意放宽——舒适区 2–3 个，内容重要、知识点密集的小节可到 5；
   练习区的 `[!question]` + `[!success]-` 成对出现只占一份视觉重量，不会误报。
3. callout 类型是否常见（`definition` / `theorem` 等学科常用类型已加进白名单）。
4. **LaTeX 宏 / 环境是否 KaTeX 支持** —— 不支持的会**渲染成红色原文**，源码里看不出。
   白名单 `references/katex_commands.json`，写法见 `references/note-format.md`。

被报了密度或小节超标，用 `flatten_callouts.py` 收口（**别手改，它带备份和预览**）：

```bash
$PY $S/flatten_callouts.py "<笔记>" --list                            # 列全部色块（行号+类型+标题+所在小节）
$PY $S/flatten_callouts.py "<笔记>" --kinds example,note --dry-run    # 整类降级：先预览
$PY $S/flatten_callouts.py "<笔记>" --kinds example,note              # 落盘（自动备份）
$PY $S/flatten_callouts.py "<笔记>" --lines 74,240,758 --plain        # 按行号挑
```

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

写入报错、笔记被改坏、lint 报了看不懂的行号时 → **`references/pitfalls.md`**。
里面按「写入 / 行尾编码 / shell 写文件 / 围栏结构 / 多章 / 其他」分类，
包含**用 `replace` 写 callout 时最容易踩的截断坑**和还原备份的办法。

---

## 附：仓库里有什么

```
obsidian-note-ingest/
├── SKILL.md                ← 你正在读的：工作流主干
├── README.md               安装 / 换机 / 命令行用法
├── CHANGELOG.md            变更记录
├── LICENSE                 MIT
├── requirements.txt        依赖清单
├── .gitattributes          强制 LF（跨平台一致，见文件内注释）
├── .gitignore
├── scripts/                独立命令行工具（核心能力都在这）
│   ├── vaultio.py          ★ 库发现 / 定位 / 查重 / 插入替换 / 批量改 / 备份回滚
│   ├── extract.py          材料统一提取（pptx / docx / pdf / srt / vtt / txt → Markdown）
│   ├── pptx_deep.py        PPTX 深层提取：表格 + OMML 公式 + 形状 + 按页编号图片
│   ├── renderpages.py      PDF 整页渲染成 PNG
│   ├── pdfdiff.py          两份同源 PDF 逐页比，指出改了哪几页
│   ├── pdf_layout.py       版面几何重建公式 + 定位插图
│   ├── pdf_panels.py       按纯色面板裁课件插图
│   ├── obsidian_lint.py    笔记体检（死链 / 围栏 / 色块密度 / 表格列数 / 锚点）
│   └── flatten_callouts.py 色块降级收口
├── tests/
│   └── smoke.py            冒烟测试：`python tests/smoke.py`（临时目录里跑，不碰你的 vault）
└── references/             按需阅读的细节文档
    ├── extraction.md       提取：两条路线（文本提取 / PDF 重建）
    ├── figures.md          配图：截取还是重画
    ├── transcripts.md      录音稿 / 字幕稿
    ├── pitfalls.md         故障排查
    ├── note-format.md      笔记格式规范
    ├── vault-profile.md    库档案**模板**（首次使用请先生成你自己的）
    └── symbol_map.json     Symbol 字体私有区码位表（公式重建用）
```
