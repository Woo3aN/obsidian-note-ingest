#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""flatten_callouts.py — 把笔记里过量的色块降级为正文（给笔记"减肥"）。

为什么需要它
------------
色块套多了会满页彩色、重点反而不成重点。
保留原则见 references/note-format.md 第五节：**只有「编号定义 / 定理 / 易错点 / 口诀」才套 callout**，
例题、推导、普通说明、旁注一律走正文。全文健康密度是**每 20–25 行一个色块**。

`obsidian_lint.py` 会报「色块过密」「小节『X』有 N 个色块」，本脚本就是它的执行手。

用法
----
    # 先看看有哪些色块（行号 + 类型 + 标题 + 所在小节），挑目标
    python flatten_callouts.py <笔记> --list

    # 整类降级（最常用）：example 与 note 本来就该是正文
    python flatten_callouts.py <笔记> --kinds example,note --dry-run
    python flatten_callouts.py <笔记> --kinds example,note

    # 按行号精确降级（行号取自 --list 或 lint 报告，指原始文件的行号）
    python flatten_callouts.py <笔记> --lines 74,240,260

    # 连标题一起去掉（适合「简记法」「关系」「要点理解」这类无信息量的标题）
    python flatten_callouts.py <笔记> --lines 758 --plain

标题处理规则
------------
    · 有实质标题            → 转成 **标题**
    · 首行是一整句话（含句号 / 超 30 字）→ 它其实是正文，不加粗
    · --plain               → 一律连标题去掉，内容直接落成正文

落盘前会自动备份到库内 `.obsidian-note-backups/`。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from vaultio import backup, read_note, which_vault_for, write_note
except ImportError:  # 兜底：脚本被单独拷走时
    backup = which_vault_for = None

    def read_note(p: Path) -> str:
        return p.read_text(encoding="utf-8-sig")

    def write_note(p: Path, t: str) -> None:
        p.write_text(t, encoding="utf-8", newline="\n")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CO_RE = re.compile(r"^> \[!([A-Za-z-]+)\]([+-]?)\s*(.*)$")
HEAD_RE = re.compile(r"^(#{2,4})\s+(.*?)\s*$")


def looks_like_title(s: str) -> bool:
    """callout 首行后面那段文字，是标题还是正文？"""
    return bool(s) and len(s) <= 30 and "。" not in s


def flatten_block(lines: list[str], i: int, plain: bool) -> tuple[list[str], int]:
    """把 lines[i] 起的 callout 块转成正文行，返回 (新行, 块结束行号)。"""
    m = CO_RE.match(lines[i])
    title = m.group(3).strip()
    j, body = i + 1, []
    while j < len(lines) and lines[j].startswith(">"):
        body.append(re.sub(r"^>\s?", "", lines[j]))
        j += 1
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    out: list[str] = []
    if title and not plain:
        out.append(f"**{title}**" if looks_like_title(title) else title)
    out.extend(body)
    return out, j


def cmd_list(lines: list[str]) -> int:
    cur2 = cur3 = ""
    n = 0
    for i, l in enumerate(lines, 1):
        h = HEAD_RE.match(l)
        if h:
            lv, t = len(h.group(1)), h.group(2)
            if lv == 2:
                cur2, cur3 = t, ""
            else:
                cur3 = t
            continue
        m = CO_RE.match(l)
        if m:
            n += 1
            print(f"{i:>5}  [{m.group(1):<10}] {m.group(3).strip()[:46]:<48} ← {cur2[:16]}"
                  f"{' › ' + cur3[:22] if cur3 else ''}")
    print(f"\n共 {n} 个色块。用 --kinds 整类降级，或 --lines 挑行号精确降级。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把过量的 callout 降级为正文")
    ap.add_argument("note")
    ap.add_argument("--kinds", help="整类降级的类型，逗号分隔，如 example,note")
    ap.add_argument("--lines", help="按行号降级，逗号分隔（原始文件行号，取自 --list）")
    ap.add_argument("--list", action="store_true", help="列出所有色块后退出")
    ap.add_argument("--plain", action="store_true", help="连标题一起去掉")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    args = ap.parse_args()

    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 笔记不存在: {p}", file=sys.stderr)
        return 1
    text = read_note(p)
    lines = text.split("\n")

    if args.list:
        return cmd_list(lines)

    targets: set[int] = set()
    if args.kinds:
        kinds = {k.strip().lower() for k in args.kinds.split(",") if k.strip()}
        for i, l in enumerate(lines):
            m = CO_RE.match(l)
            if m and m.group(1).lower() in kinds:
                targets.add(i)
    if args.lines:
        for s in args.lines.split(","):
            s = s.strip()
            if not s:
                continue
            i = int(s) - 1
            ok = 0 <= i < len(lines) and CO_RE.match(lines[i])
            if not ok:
                print(f"[错误] 第 {s} 行不是 callout 起始行："
                      f"{(lines[i] if 0 <= i < len(lines) else '(越界)')!r}", file=sys.stderr)
                return 1
            targets.add(i)

    if not targets:
        print("[错误] 没指定目标：用 --kinds 或 --lines（先用 --list 看看有哪些）", file=sys.stderr)
        return 1

    new = list(lines)
    preview: list[tuple[int, str]] = []
    for i in sorted(targets, reverse=True):   # 从后往前改，避免行号漂移
        first = lines[i].strip()
        out, j = flatten_block(new, i, args.plain)
        preview.append((i + 1, first + "   →   " + (out[0] if out else "(整块去掉)")))
        new[i:j] = out
    preview.sort()          # 打印时按行号正序，读起来顺

    result = "\n".join(new)
    print(f"将降级 {len(targets)} 个色块 | {len(text)} -> {len(result)} 字符")
    if args.dry_run:
        for ln, msg in preview:
            print(f"  第 {ln} 行: {msg}")
        print("\n[试运行] 未写入。确认后去掉 --dry-run。")
        return 0

    if backup and which_vault_for:
        b = backup(p, which_vault_for(p))
        if b:
            print(f"[备份] {b}")
    else:
        print("[警告] 备份模块未加载，本次改动没有备份——回滚请依赖版本控制或手工副本。",
              file=sys.stderr)
    write_note(p, result)
    print(f"[完成] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
