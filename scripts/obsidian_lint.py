#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
obsidian_lint.py — Obsidian 笔记语法体检。

每次写入笔记后跑一遍，确保没有制造死链、重名锚点或格式破损。

用法：
  python obsidian_lint.py <笔记路径 或 vault目录> [--strict]

检查项：
  [错误] frontmatter YAML 无法解析 / 未闭合
  [错误] [[#锚点]] 指向不存在的标题（死链）
  [错误] 代码块围栏 ``` 未成对
  [警告] 同级重复标题（Obsidian 锚点会冲突，跳转定位错乱）
  [警告] 标题层级跳跃（## 直接到 ####）
  [警告] 表格列数不一致
  [警告] callout 类型拼写可疑
  [警告] LaTeX 宏 / 环境不在 KaTeX 支持列表（会渲染成红色原文，源码里看不出）
         白名单 references/katex_commands.json，用 scripts/gen_katex_allowlist.py 生成
  [警告] `**` 渲染成字面量（加粗没生效）—— 需 markdown-it-py，缺了自动跳过
         典型：`**术语（English）**后面`、`是**「引文」**：`
  [建议] 空章节（标题下没有任何内容）
  [建议] 连续 3 个以上空行 / 行尾空格
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Obsidian 内置类型 + 库内实际在用的自定义类型。
# 学科笔记（理论类、导论类、工程类）大量使用 definition / theorem /
# summary / abstract，不登记就会刷屏误报。
KNOWN_CALLOUTS = {
    "note", "abstract", "summary", "tldr", "info", "todo", "tip", "hint",
    "important", "success", "check", "done", "question", "help", "faq",
    "warning", "caution", "attention", "failure", "fail", "missing", "danger",
    "error", "bug", "example", "quote", "cite",
    # 学科笔记常用
    "definition", "theorem", "lemma", "corollary", "proposition", "axiom",
    "proof", "derivation", "formula", "idea", "insight", "seealso", "reference",
}
FENCE_RE = re.compile(r"^\s*(```|~~~)(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
ANCHOR_LINK_RE = re.compile(r"\[\[#([^\]|]+)(?:\|[^\]]*)?\]\]")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!([A-Za-z-]+)\][+-]?")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]")
# 引用块前缀（callout 里每一行都带 `> `，可能嵌套成 `> > `）
QUOTE_PREFIX_RE = re.compile(r"^\s*(?:>\s*)+")
# LaTeX 宏：反斜杠 + 字母（`\\` 换行、`\,` 这类间距命令都不匹配，正好跳过）
MATH_CMD_RE = re.compile(r"\\([a-zA-Z@]+)")
# \begin{xxx} / \end{xxx}
MATH_ENV_RE = re.compile(r"\\(?:begin|end)\s*\{\s*([A-Za-z*]+)\s*\}")
# 渲染后仍残留的强调定界符。排除连续 4 个以上下划线——`____` 是填空空位，本来就该显示成字面量。
EMPH_LEFTOVER = re.compile(r"\*\*|(?<![*_])__(?![*_])")
# KaTeX 支持的宏清单（由 gen_katex_allowlist.py 从 katex/src 生成）
MATH_ALLOWLIST_PATH = Path(__file__).resolve().parent.parent / "references" / "katex_commands.json"
_math_allowlist: tuple[set[str], set[str]] | None = None


class Report:
    def __init__(self):
        self.errors: list[tuple[int, str]] = []
        self.warns: list[tuple[int, str]] = []
        self.infos: list[tuple[int, str]] = []

    def err(self, line: int, msg: str):
        self.errors.append((line, msg))

    def warn(self, line: int, msg: str):
        self.warns.append((line, msg))

    def info(self, line: int, msg: str):
        self.infos.append((line, msg))

    @property
    def ok(self) -> bool:
        return not self.errors


def slugify_anchor(title: str) -> str:
    """Obsidian 锚点匹配时基本是原样标题，这里只做去空白归一化。"""
    return re.sub(r"\s+", " ", title).strip()


def load_math_allowlist() -> tuple[set[str], set[str]]:
    """返回 (KaTeX 支持的宏, 支持的环境)。清单缺失时返回两个空集（跳过检查，不误报）。"""
    global _math_allowlist
    if _math_allowlist is None:
        try:
            data = json.loads(MATH_ALLOWLIST_PATH.read_text(encoding="utf-8"))
            _math_allowlist = (set(data.get("commands", [])), set(data.get("environments", [])))
        except Exception:
            _math_allowlist = (set(), set())
    return _math_allowlist


def blank_code_fences(lines: list[str]) -> list[str]:
    """把代码围栏内的内容换成空行（保留行数，行号不漂）。"""
    out: list[str] = []
    in_code, fence = False, ""
    for line in lines:
        m = FENCE_RE.match(line)
        if m:
            if not in_code:
                in_code, fence = True, m.group(1)
            elif m.group(1) == fence:
                in_code = False
            out.append("")
            continue
        out.append("" if in_code else line)
    return out


def find_math_spans(text: str) -> list[tuple[int, str]]:
    """抽出 $$...$$ / $...$，返回 [(起始偏移, tex)]。

    按配对定界符扫描，不能用 `[^$]+` —— `\\text{$P(x)$ …}` 里的嵌套 `$` 会被截断成假公式。
    """
    spans: list[tuple[int, str]] = []
    n, i = len(text), 0
    while i < n:
        if text[i] != "$":
            i += 1
            continue
        is_disp = text.startswith("$$", i)
        delim = "$$" if is_disp else "$"
        start = i + len(delim)
        end = text.find(delim, start)
        if end == -1:                       # 没配对，跳过
            i = start
            continue
        tex = text[start:end]
        if not is_disp and "\n" in tex:     # 行内公式不跨行
            i = end + len(delim)
            continue
        spans.append((start, tex))
        i = end + len(delim)
    return spans


def check_math(text: str, lines: list[str], report: Report) -> int:
    """报出 KaTeX 不认识的宏 / 环境 —— 它们会渲染成红色原文。"""
    cmd_allow, env_allow = load_math_allowlist()
    if not cmd_allow:
        return 0
    blanked = "\n".join(blank_code_fences(lines))
    bad_cmds: dict[str, list[int]] = {}
    bad_envs: dict[str, list[int]] = {}
    for pos, tex in find_math_spans(blanked):
        ln = blanked.count("\n", 0, pos) + 1
        for m in MATH_CMD_RE.finditer(tex):
            name = "\\" + m.group(1)
            if name not in cmd_allow:
                bad_cmds.setdefault(name, []).append(ln)
        for m in MATH_ENV_RE.finditer(tex):
            if m.group(1) not in env_allow:
                bad_envs.setdefault(m.group(1), []).append(ln)

    for name, lns in bad_cmds.items():
        where = f"第 {lns[0]} 行" if len(lns) == 1 else f"共 {len(lns)} 处，首个在第 {lns[0]} 行"
        report.warn(lns[0], f"LaTeX 宏 {name} 不在 KaTeX 支持列表 → 渲染成红色原文（{where}）；"
                            f"否定关系符用 \\not + 原符号（如 \\not\\subset）")
    for name, lns in bad_envs.items():
        where = f"第 {lns[0]} 行" if len(lns) == 1 else f"共 {len(lns)} 处，首个在第 {lns[0]} 行"
        report.warn(lns[0], f"LaTeX 环境 {{{name}}} 不在 KaTeX 支持列表 → 渲染成红色原文（{where}）；"
                            f"改用 cases / aligned / matrix 等")
    return len(bad_cmds) + len(bad_envs)


def mask_markup(text: str) -> str:
    """把代码、公式、转义的强调符换成同长度的 x（保留换行与行数）。

    顺序关键：**先屏蔽行内代码，再屏蔽转义**。否则 `` `\\` `` 这种「代码里正好是反斜杠」
    会被「反斜杠 + 反引号」的转义规则先吃掉反引号，代码段配不成对、整行解析错乱。
    """

    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", "x", m.group(0))

    text = re.sub(r"^[ \t]*(```|~~~)[\s\S]*?^[ \t]*\1[^\n]*$", blank, text, flags=re.M)
    text = re.sub(r"`[^`\n]*`", blank, text)
    text = re.sub(r"\$\$[\s\S]*?\$\$", blank, text)
    text = re.sub(r"\$[^$\n]*\$", blank, text)
    text = re.sub(r"\\[*_]", blank, text)
    return text


def check_emphasis(text: str, lines: list[str], report: Report) -> int:
    """报出「渲染后仍是字面量」的 ** / __ —— 需要 markdown-it-py。

    这是中文笔记的高频坑，而且**光看源码看不出来**：
      `**工具调用（Tool Calling）**机制`  → 闭合 ** 前是标点、后紧跟文字 → 无法闭合
      `是**「推理即计算」**：`            → 开启 ** 前是文字、后紧跟标点 → 无法开启
    两处都会把 `**` 原样显示出来。直接渲染一遍再找残留，比用正则猜规则可靠得多。
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        return -1                       # -1 = 没装解析器，跳过（不误报）
    md = MarkdownIt("commonmark")
    masked = mask_markup(text).splitlines()
    bad: list[tuple[int, str]] = []

    for i, mline in enumerate(masked, 1):
        if "**" not in mline and "__" not in mline:
            continue
        leftover = False
        snippet = ""

        def walk(toks):
            nonlocal leftover, snippet
            for t in toks:
                if t.type == "inline" and t.children:
                    for c in t.children:
                        if c.type == "text" and c.content and EMPH_LEFTOVER.search(c.content):
                            leftover = True
                            if not snippet:
                                snippet = c.content
                if t.children:
                    walk(t.children)

        try:
            walk(md.parse(mline))
        except Exception:
            continue
        if leftover:
            bad.append((i, snippet))

    for ln, seg in bad:
        msg = ("`**` 渲染成了字面量（行内加粗没生效）→ "
               "把紧贴 ** 的标点移到外面（`**术语**（English）`、`「**术语**」`），"
               "或在其外侧补一个空格")
        seg = " ".join(seg.split())
        if seg:
            msg += f"；残留片段：{seg[:70]}"
        report.warn(ln, msg)
    return len(bad)


def lint_text(text: str, report: Report) -> dict:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []  # (line_no, level, title)
    quoted_titles: set[str] = set()            # 只写在 callout 里的标题
    plain_titles: set[str] = set()             # 正文里的标题
    in_code = False
    fence_line = -1
    fence_char = ""
    tables: list[list[tuple[int, int]]] = []  # 每个表格的 (行号, 列数)
    cur_table: list[tuple[int, int]] = []
    co_lines: list[tuple[int, str]] = []  # (行号, callout 类型小写)

    for i, line in enumerate(lines, 1):
        # note-format 明确教人把表格放进 callout，而 `> ### 甲` 也是能当锚点的标题。
        # 所以先剥掉引用块前缀再判标题 / 表格 —— 否则规范自己推荐的地方反而成了盲区。
        bare = QUOTE_PREFIX_RE.sub("", line)
        in_quote = bare != line

        m = FENCE_RE.match(line)
        if m:
            if not in_code:
                in_code, fence_line, fence_char = True, i, m.group(1)
            elif m.group(1) == fence_char:
                in_code = False
                fence_line = -1
            continue
        if in_code:
            continue

        h = HEADING_RE.match(bare)
        if h:
            title = re.sub(r"\s*#+\s*$", "", h.group(2)).strip()
            headings.append((i, len(h.group(1)), title))
            if in_quote:
                quoted_titles.add(slugify_anchor(title))
            else:
                plain_titles.add(slugify_anchor(title))
        else:
            # 表格列数：按「未被反斜杠转义的竖线」切分。
            # 单元格里的 \| 是字面竖线，而 extract.py 的 _table_to_md 正是这么转义的，
            # 用 split("|") 数会把工具链自己产出的表误报成「列数不一致」。
            stripped = bare.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = re.split(r"(?<!\\)\|", stripped)
                if cells and not cells[0].strip():
                    cells = cells[1:]
                if cells and not cells[-1].strip():
                    cells = cells[:-1]
                cur_table.append((i, len(cells)))
            elif cur_table:
                tables.append(cur_table)
                cur_table = []

        cm = CALLOUT_RE.match(line)
        if cm:
            co_lines.append((i, cm.group(1).lower()))
            if cm.group(1).lower() not in KNOWN_CALLOUTS:
                report.warn(i, f"callout 类型 '[!{cm.group(1)}]' 不在常见列表中，Obsidian 会渲染成默认样式")

        if line.rstrip() != line:
            report.info(i, "行尾有多余空格")
    if cur_table:
        tables.append(cur_table)
    if in_code:
        report.err(fence_line, f"代码块围栏 '{fence_char}' 未闭合（从第 {fence_line} 行开始）")

    # --- frontmatter
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            report.err(1, "frontmatter 缺少闭合的 '---'")
        else:
            fm = "\n".join(lines[1:end])
            try:
                import yaml
                data = yaml.safe_load(fm) or {}
                if not isinstance(data, dict):
                    report.err(2, "frontmatter 不是键值对结构")
                elif "tags" in data and data["tags"] is not None:
                    tags = data["tags"]
                    if isinstance(tags, str):
                        report.info(2, f"tags 是字符串而非列表: {tags!r}（Obsidian 会按整句解析）")
                    elif isinstance(tags, list) and any(
                        isinstance(t, str) and (" " in t and ":" not in t) for t in tags
                    ):
                        report.info(2, "部分 tag 含空格，Obsidian 标签不支持空格")
            except ImportError:
                report.info(1, "未安装 PyYAML，已跳过 frontmatter 结构检查（pip install PyYAML）")
            except Exception as e:
                report.err(2, f"frontmatter YAML 解析失败: {str(e).splitlines()[0]}")

    # --- 重复标题（同级同名）
    seen: dict[tuple[int, str], int] = {}
    for ln, lvl, title in headings:
        key = (lvl, slugify_anchor(title))
        if key in seen:
            report.warn(ln, f"同级重复标题 '{title}'（首次出现在第 {seen[key]} 行）→ 锚点冲突，[[#...]] 会跳到第一处")
        else:
            seen[key] = ln

    # --- 层级跳跃
    prev_lvl = 0
    for ln, lvl, title in headings:
        if prev_lvl and lvl > prev_lvl + 1:
            report.warn(ln, f"标题层级跳跃：从 H{prev_lvl} 直接到 H{lvl}（'{title}'）")
        prev_lvl = lvl

    # --- 色块密度（经验标准：全文每 20–25 行一个为健康）
    total_co = len(co_lines)
    if total_co and len(lines) > 60:
        # 显示与判断都用同一位小数，避免出现「显示 20.0 却报偏多」的边界抖动
        density = round(len(lines) / total_co, 1)
        tip = "把例题、旁注、说明性内容降为正文（`flatten_callouts.py` 可批量处理）"
        if density < 15:
            report.warn(co_lines[0][0],
                        f"色块过密：{total_co} 个 / {len(lines)} 行 = 每 {density} 行一个"
                        f"（健康值 20–25）→ {tip}")
        elif density < 20:
            report.info(co_lines[0][0],
                        f"色块偏多：{total_co} 个 / {len(lines)} 行 = 每 {density} 行一个"
                        f"（健康值 20–25）→ {tip}")

    # --- 单个「最小小节」色块超标（note-format 的定义：有 #### 就按 #### 算，否则按 ### 算）
    all_h = [(ln, lvl, t) for ln, lvl, t in headings if lvl in (2, 3, 4)]
    for n3, (ln, lvl, title) in enumerate(all_h):
        if lvl not in (3, 4):
            continue
        nxt = next((l for l, _, _ in all_h[n3 + 1:]), len(lines) + 1)
        # ### 之下还有 #### 时，真正的"最小小节"是 ####，这里跳过，交给下面逐条检查
        if lvl == 3 and any(l2 == 4 for l2, _, _ in all_h[n3 + 1:] if l2 < nxt):
            continue
        kinds = [k for l, k in co_lines if ln < l < nxt]
        if not kinds:
            continue
        # 练习区的 question + success 成对出现，只占一份视觉重量
        eff = len(kinds) - min(kinds.count("question"), kinds.count("success"))
        # 阈值有意放宽：内容重要的章节多一两个色块没关系，
        # 一般 2–5 个都算正常，超过 5 个才提示收口。
        if eff > 5:
            report.warn(ln, f"小节「{title}」有 {eff} 个色块（一般 2–5 个为宜）→ "
                             f"把例题、旁注、说明性内容降为正文（`flatten_callouts.py` 可批量处理）")

    # --- 死锚点
    # callout 里的标题只提示、不判错：不同 Obsidian 版本对「引用块内的标题算不算锚点」
    # 并不一致，报成错误会逼着把一条可能有效的链接删掉。
    anchors = plain_titles | quoted_titles
    for i, line in enumerate(lines, 1):
        for m in ANCHOR_LINK_RE.finditer(line):
            target = slugify_anchor(m.group(1))
            if target not in anchors:
                report.err(i, f"死链：[[#{m.group(1)}]] 找不到对应标题")
            elif target not in plain_titles:
                report.info(i, f"[[#{m.group(1)}]] 指向的标题写在 callout 里 —— "
                               f"部分 Obsidian 版本不把它当锚点，建议把标题提到正文")

    # --- 表格列数
    for tbl in tables:
        if len(tbl) < 2:
            continue
        widths = {n for _, n in tbl}
        if len(widths) > 1:
            msg = f"表格列数不一致：本表出现 {sorted(widths)} 列"
            # 单元格里的公式若含 | ，会被当成列分隔符 —— 直接给出方向，省掉一轮排查。
            for ln, _w in tbl:
                if 1 <= ln <= len(lines) and re.search(r"\$[^$\n]*\|[^$\n]*\$", lines[ln - 1]):
                    msg += ("；本表有单元格内的公式含 `|`（会被算作列分隔符）—— "
                            "改成 `\\lvert…\\rvert` 或 `\\mid`（注意 `\\|` 是双竖线，不是「给定」）")
                    break
            report.warn(tbl[0][0], msg)

    # --- 空章节
    body_start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        body_start = (end + 1) if end is not None else 0
    for idx, (ln, lvl, title) in enumerate(headings):
        nxt = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines) + 1
        content = [l for l in lines[ln:nxt - 1] if l.strip()]
        if content or lvl < 2 or title in ("目录",):
            continue
        # 只有更深层子标题、自己没有正文的，是有意为之的分组节点，不算空
        if idx + 1 < len(headings) and headings[idx + 1][1] > lvl:
            continue
        report.info(ln, f"空章节：'{title}' 下面还没有内容")

    # --- LaTeX 宏（KaTeX 白名单）
    n_bad_math = check_math(text, lines, report)

    # --- 加粗没生效（`**` 渲染成字面量）
    n_bad_emph = check_emphasis(text, lines, report)

    return {
        "lines": len(lines),
        "chars": len(text),
        "headings": len(headings),
        "tables": len(tables),
        "wikilinks": len(WIKILINK_RE.findall(text)),
        "bad_math": n_bad_math,
        "bad_emph": n_bad_emph,
    }


def compress(report: Report) -> None:
    """降噪：把刷屏级的同类问题聚合成一条摘要。

    题解集、代码库这类笔记天然会有大量同名标题（每道题都有"题目描述/
    输入/输出"）和行尾空格，逐行罗列会淹没真正重要的死链错误。
    """
    # 1) 行尾空格 → 一条汇总
    trail = [x for x in report.infos if "行尾有多余空格" in x[1]]
    if len(trail) > 3:
        report.infos = [x for x in report.infos if "行尾有多余空格" not in x[1]]
        report.infos.append(
            (trail[0][0], f"行尾多余空格共 {len(trail)} 处（首个在第 {trail[0][0]} 行）"))

    # 2) 模板化重复标题 → 按名字聚合
    dup = [x for x in report.warns if x[1].startswith("同级重复标题")]
    if dup:
        from collections import Counter
        names: Counter = Counter()
        first: dict[str, int] = {}
        for ln, msg in dup:
            m = re.search(r"同级重复标题 '([^']+)'", msg)
            if m:
                names[m.group(1)] += 1
                first.setdefault(m.group(1), ln)
        rest = [x for x in report.warns if not x[1].startswith("同级重复标题")]
        for name, cnt in names.most_common():
            if cnt + 1 >= 3:
                rest.append((first[name],
                             f"标题 '{name}' 共出现 {cnt + 1} 次 —— 模板化重复，"
                             f"[[#{name}]] 只能跳到第一次出现的位置"))
            else:
                rest.append((first[name], f"同级重复标题 '{name}'（另一处重名）"))
        report.warns = rest


def lint_file(path: Path) -> tuple[Report, dict]:
    """读一篇笔记并体检。

    非 UTF-8 直接报错——原先的 `errors="replace"` 会把 GBK 内容当成乱码正文，
    静默产出一份「看起来没问题」的报告，反而更危险。
    """
    report = Report()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        report.err(1, "文件不是 UTF-8 编码（中文 Windows 下常见的是 GBK / ANSI）"
                      "——先转成 UTF-8 再体检")
        return report, {"lines": 0, "chars": 0, "headings": 0, "tables": 0, "wikilinks": 0}
    stats = lint_text(text, report)
    compress(report)
    return report, stats


def print_report(name: str, report: Report, stats: dict) -> None:
    total = len(report.errors) + len(report.warns) + len(report.infos)
    status = "✗ 有错误" if report.errors else ("! 有警告" if report.warns else "✓ 通过")
    print(f"\n{'='*70}")
    print(f"{status}  {name}")
    print(f"  行数 {stats['lines']} | 字符 {stats['chars']} | 标题 {stats['headings']} | 表格 {stats['tables']} | 双链 {stats['wikilinks']}")
    if total == 0:
        print("  没有任何问题")
    MAX_SHOW = 12
    for label, items in (("错误", report.errors), ("警告", report.warns), ("建议", report.infos)):
        for ln, msg in items[:MAX_SHOW]:
            print(f"  [{label}] 第 {ln} 行: {msg}")
        if len(items) > MAX_SHOW:
            print(f"  [{label}] ...另有 {len(items) - MAX_SHOW} 条同类问题未逐条列出")


def main() -> int:
    ap = argparse.ArgumentParser(description="Obsidian 笔记语法体检")
    ap.add_argument("target", help="笔记文件或 vault 目录")
    ap.add_argument("--strict", action="store_true", help="有警告也返回非零")
    ap.add_argument("--quiet", action="store_true", help="只输出有问题的笔记")
    args = ap.parse_args()

    target = Path(args.target).expanduser().resolve()
    files: list[Path] = []
    if target.is_dir():
        for dp, dn, fn in os.walk(target):
            dn[:] = [d for d in dn if not d.startswith(".")]
            files += [Path(dp) / f for f in fn if f.lower().endswith(".md")]
        files.sort()
    elif target.is_file():
        files = [target]
    else:
        print(f"[错误] 路径不存在: {target}", file=sys.stderr)
        return 2

    if not files:
        print("[无] 没有找到 .md 文件")
        return 0

    n_err = n_warn = 0
    for f in files:
        report, stats = lint_file(f)
        n_err += len(report.errors)
        n_warn += len(report.warns)
        if args.quiet and not report.errors and not report.warns:
            continue
        try:
            name = f.relative_to(target).as_posix() if target.is_dir() else f.name
        except ValueError:
            name = f.name
        print_report(name, report, stats)

    print(f"\n{'='*70}")
    print(f"汇总：扫描 {len(files)} 篇，错误 {n_err} 个，警告 {n_warn} 个")
    if n_err:
        return 1
    if args.strict and n_warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
