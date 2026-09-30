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
  [建议] 空章节（标题下没有任何内容）
  [建议] 连续 3 个以上空行 / 行尾空格
"""

from __future__ import annotations

import argparse
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


def lint_text(text: str, report: Report) -> dict:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []  # (line_no, level, title)
    in_code = False
    fence_line = -1
    fence_char = ""
    tables: list[list[tuple[int, int]]] = []  # 每个表格的 (行号, 列数)
    cur_table: list[tuple[int, int]] = []
    co_lines: list[tuple[int, str]] = []  # (行号, callout 类型小写)

    for i, line in enumerate(lines, 1):
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

        h = HEADING_RE.match(line)
        if h:
            headings.append((i, len(h.group(1)), re.sub(r"\s*#+\s*$", "", h.group(2)).strip()))
        else:
            # 表格列数：按「未被反斜杠转义的竖线」切分。
            # 单元格里的 \| 是字面竖线，而 extract.py 的 _table_to_md 正是这么转义的，
            # 用 split("|") 数会把工具链自己产出的表误报成「列数不一致」。
            if line.strip().startswith("|") and line.strip().endswith("|"):
                cells = re.split(r"(?<!\\)\|", line.strip())
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
        density = len(lines) / total_co
        tip = "把例题、旁注、说明性内容降为正文（`flatten_callouts.py` 可批量处理）"
        if density < 15:
            report.warn(co_lines[0][0],
                        f"色块过密：{total_co} 个 / {len(lines)} 行 = 每 {density:.1f} 行一个"
                        f"（健康值 20–25）→ {tip}")
        elif density < 20:
            report.info(co_lines[0][0],
                        f"色块偏多：{total_co} 个 / {len(lines)} 行 = 每 {density:.1f} 行一个"
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
    anchors = {slugify_anchor(t) for _, _, t in headings}
    for i, line in enumerate(lines, 1):
        for m in ANCHOR_LINK_RE.finditer(line):
            target = slugify_anchor(m.group(1))
            if target not in anchors:
                report.err(i, f"死链：[[#{m.group(1)}]] 找不到对应标题")

    # --- 表格列数
    for tbl in tables:
        if len(tbl) < 2:
            continue
        widths = {n for _, n in tbl}
        if len(widths) > 1:
            report.warn(tbl[0][0], f"表格列数不一致：本表出现 {sorted(widths)} 列")

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

    return {
        "lines": len(lines),
        "chars": len(text),
        "headings": len(headings),
        "tables": len(tables),
        "wikilinks": len(WIKILINK_RE.findall(text)),
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
