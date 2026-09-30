# obsidian-note-ingest

> ## ⚠️ 测试版（Beta）· 当前版本 0.1.0
>
> 这个项目**仍在自用验证阶段**：0.x 表示尚未稳定——脚本参数、文件结构、文档都可能继续变动。
> 目前只发了 `0.1.0` 这个**预发布版**（GitHub Release 标注为 pre-release），还没有稳定版。
>
> 求稳的话建议先观望；要试的话，请只在**你能随时回滚**的笔记库上跑
> （所有写入都会自动备份到库内 `.obsidian-note-backups/`，可 `restore` 还原）。

**把课件、录音稿、讲义整理成能直接读的 Obsidian 笔记。**

一个面向 Obsidian 用户的 AI Agent skill：喂给它 PPTX / PDF / DOCX / 字幕稿 / 截图，
它提取内容、查重、剔除废话、按你的笔记风格成篇、写进你的 vault —— **全程先备份、可回滚**。

> **不绑定任何 Agent 框架。** 核心工具链（`scripts/*.py`）是**独立可用的命令行脚本**，
> 既能被 Claude Code / Codex / WorkBuddy / Trae 等任何能跑命令的 Agent 直接调用，
> 也能由你在终端里手动跑。`SKILL.md` 只描述「做什么」，不写死具体工具名，
> 因此换 harness 无需改动。

---

## 它解决什么问题

手动整理课件笔记的痛点，这个 skill 逐条对付：

| 痛点 | 做法 |
|------|------|
| PPTX 里的**表格和公式抽不出来** | `pptx_deep.py` 直接解析 `slideN.xml`，绕开 `AlternateContent` 拿到表格，解析 OMML 拿到公式 |
| 老课件公式是 **OLE 对象**，抽出来一片空白 | 从 PDF 文本层按**坐标几何**重建分式 / 上下标 / 矢量（`pdf_layout.py`） |
| 公式型 PDF 提取出来**完全散架** | `renderpages.py` 渲染成图让模型看，或以版面几何重建 |
| 课件插图**裁不干净**（切到文字、留一堆白边） | `pdf_panels.py` 按纯色面板找边界，比按笔画估框准得多 |
| 整理完发现**内容早就有了** / 灌了一堆重复 | `vaultio.py check` 查重，并附「覆盖率为什么会骗人」的判别法 |
| 改笔记时**误删、行尾被改、callout 被截断** | 所有写入走 `vaultio.py`（强制 LF、自动备份、`--dry-run` 预览） |
| 笔记写完**满页彩色块，重点反而没了** | `obsidian_lint.py` 量化色块密度 + `flatten_callouts.py` 一键降级 |

`references/` 里还记着**几十条实战踩坑**（表格丢列、图注配错、`--until` 吃掉一整段、
OCR 丢括号导致公式歧义……），都是真金白银换来的，不是理论。

---

## 快速开始

### 1. 安装依赖

```bash
PY="<你的 Python 解释器>"          # 建议 3.10+
$PY -m pip install -r requirements.txt
```

### 2. 作为 skill 安装

把整个目录放进你的 skill 目录：

| 框架 | 位置 |
|------|------|
| Claude Code | `~/.claude/skills/`（用户级）或 `<项目>/.claude/skills/` |
| WorkBuddy | `~/.workbuddy/skills/`（用户级）或 `<项目>/.workbuddy/skills/` |
| 其他 CLI Agent | 多数约定 `~/.<agent>/skills/`；找不到约定目录时，**不装也行**——把 `SKILL.md` 当文档喂给模型、命令让它直接跑即可 |

各框架的 skill 目录约定不同，但吃的是同一份 `SKILL.md` + `scripts/`。

### 3. 确认你的 vault 能被发现

```bash
$PY scripts/vaultio.py vaults
```

它读 Obsidian 自己的配置自动发现库，**不需要额外配置**：

| 平台 | 配置位置 |
|------|------|
| Windows | `%APPDATA%\obsidian\obsidian.json` |
| macOS | `~/Library/Application Support/obsidian/obsidian.json` |
| Linux | `~/.config/obsidian/obsidian.json` |

能列出你的库就说明通了。然后照 `references/vault-profile.md` 把库清单记下来。

### 4. 直接用命令行也很好用

```bash
# 提取任意材料成 Markdown
$PY scripts/extract.py "第3章.pptx" --stats --out ch3.md

# 课件 PDF 按面板裁插图
$PY scripts/pdf_panels.py "第5章.pdf" --probe 2-12

# 查重：这份整理稿和笔记里已有的内容重合多少
$PY scripts/vaultio.py check "某课程/某课程.md" --content-file draft.md

# 预览插入效果（不落盘）
$PY scripts/vaultio.py replace "某课程/某课程.md" \
    --anchor "### 某小节" --content-file draft.md --dry-run

# 体检一篇笔记
$PY scripts/obsidian_lint.py "某课程/某课程.md"
```

---

## 目录结构

这个 skill 按**渐进式披露**组织：`SKILL.md` 只放主干（约 400 行），细节按需从 `references/` 读取。

```
obsidian-note-ingest/
├── SKILL.md               ★ 主干：硬规则 + 环境 + 七步工作流 + 任务导航
├── README.md              本文件：安装 / 换机 / 命令行用法
├── CHANGELOG.md           变更记录
├── requirements.txt       依赖清单
├── LICENSE                MIT
├── .gitattributes         强制 LF（Windows 上 clone 不会变 CRLF）
├── .gitignore
├── references/            细节文档，按需阅读
│   ├── extraction.md      提取：两条路线（文本提取 / 从 PDF 重建）
│   ├── figures.md         配图：截取还是重画
│   ├── transcripts.md     录音稿 / 字幕稿
│   ├── pitfalls.md        故障排查（写入 / 行尾 / 围栏 / 多章）
│   ├── note-format.md     笔记格式规范（frontmatter / 标题层级 / callout 上限 / 收尾三件套）
│   ├── vault-profile.md   库档案**模板**（首次使用请先生成你自己的）
│   └── symbol_map.json    Adobe Symbol 字体私有区码位表（公式重建用）
├── scripts/               独立命令行工具（核心能力都在这，可不依赖 Agent 直接跑）
│   ├── vaultio.py         ★ 核心：库发现 / 定位 / 查重 / 插入替换 / 备份回滚
│   ├── extract.py         材料统一提取（pptx / docx / pdf / srt / vtt / txt → Markdown）
│   ├── pptx_deep.py       PPTX 深层提取：表格 + OMML 公式 + 形状 + 按页编号图片
│   ├── renderpages.py     PDF 整页渲染成 PNG
│   ├── pdf_layout.py      版面几何重建公式 + 定位插图区域
│   ├── pdf_panels.py      按纯色面板裁课件插图
│   ├── obsidian_lint.py   笔记体检（死链 / 围栏 / 色块密度 / 表格列数 / 锚点）
│   └── flatten_callouts.py 色块降级收口
└── tests/
    └── smoke.py           冒烟测试：临时目录里跑，不碰你的 vault
```

`.github/workflows/ci.yml` 会在 Linux + Windows 上自动跑这套冒烟测试。

## 自检

```bash
$PY tests/smoke.py      # 12 项冒烟测试，全部在临时目录里跑，不碰你的 vault
```

推送到 GitHub 后，`.github/workflows/ci.yml` 会在 **Linux + Windows** 上自动跑这套测试。

## scripts 速查

| 脚本 | 一句话 |
|------|--------|
| `vaultio.py` | 读写 vault 的唯一入口。**所有写入都自动备份，可 `restore` 回滚** |
| `extract.py` | 一行命令把 pptx/docx/pdf/字幕 → Markdown |
| `pptx_deep.py` | 课件 PPTX 专用：普通提取会漏的公式、表格、图，它全拿到 |
| `renderpages.py` | PDF → PNG，用于「看图核对」 |
| `pdf_layout.py` | 没有视觉模型时，用坐标把散架的公式拼回去 |
| `pdf_panels.py` | 按面板颜色裁插图，切边最少 |
| `obsidian_lint.py` | 写完跑一遍，死链和围栏错误必须为 0 |
| `flatten_callouts.py` | 色块太多时批量降级成正文 |

---

## 换个系统要注意

1. **`.ppt` 转换默认用 LibreOffice（跨平台）：**

   ```bash
   soffice --headless --convert-to pptx --outdir "<输出目录>" "<材料>.ppt"
   soffice --headless --convert-to pdf  --outdir "<输出目录>" "<材料>.ppt"
   ```

   Windows 上装了 PowerPoint 的话，可以改走 COM 转换，对复杂公式和专用字体的还原通常更准
   （命令见 `references/extraction.md` 路线 A3）。

2. **重画的配图要显式指定中文字体**（在脚本顶部定成一个常量）：

   | 平台 | 字体 |
   |------|------|
   | Windows | `Microsoft YaHei` |
   | macOS | `PingFang SC` |
   | Linux | `Noto Sans CJK SC` |

   不设或字体缺失时，matplotlib 会**静默画成方框**（只在 stderr 丢一条 glyph 警告，
   所以规范要求跑完必查警告，见 `references/figures.md`）。

3. **vault 自动发现三平台都支持。** 若你的 Obsidian 配置文件不在标准位置
   （便携版、自定义安装），用环境变量指路：

   ```bash
   export OBSIDIAN_CONFIG_DIR="<包含 obsidian.json 的目录>"
   ```

4. **`references/vault-profile.md` 是模板。** 首次使用按第 3 步生成自己的。

---

## 设计取向

这个 skill 有明确立场，不打算讨好所有人：

- **宁可多问，不擅自改。** 改动已有笔记的正文前一定先给用户看变更预览。
- **机械的交给脚本，判断的留给人。** 时间戳、断行合并、查重统计归脚本；
  「哪些内容无关、怎么分层」必须真读材料。
- **不隐瞒不确定性。** 读不了图就说读不了，不把推断说成核对。
- **原素材只读。** 绝不改动、删除、移动用户的原始材料。

## 贡献

欢迎提 Issue / PR。最有价值的贡献是 **`references/` 里的踩坑记录** ——
如果你用这个 skill 踩到了新的坑、或者发现某条规则过时了，欢迎一并更新。

## License

MIT © Woo3aN
