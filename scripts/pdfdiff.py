#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pdfdiff.py — 两份同源 PDF 逐页比一比，指出**哪些页变了**。

什么时候用：同一份材料出了新版本（答案版、修订版、补充讲义）。
逐页比文本，直接给出「第 11、13、25… 页有改动」，
省掉人工通读两份提取稿、也省掉把整本书渲染成图。

注意它只告诉你**哪几页**变了，不告诉你**变了什么** ——
改了哪几行、改成什么，仍然要渲染那几页用眼睛核对（`renderpages.py --pages`）。

用法：
    python pdfdiff.py 旧.pdf 新.pdf
    python pdfdiff.py 旧.pdf 新.pdf --threshold 0.9      # 更宽松，「变了」的判定更灵敏
    python pdfdiff.py 旧.pdf 新.pdf --out changed.txt    # 结果写文件

依赖 pypdf（与 extract.py 一致，两边的「疑似扫描页」判定才对得上）。
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

try:  # Windows 控制台默认 cp936，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def page_texts(pdf: Path) -> list[str]:
    """逐页抽文本并归一化。

    空白差异必须抹平：同一页重新导出一次，PDF 里的空格 / 换行位置就可能变，
    不归一化的话整本书都会报「变了」。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("[错误] 缺少 pypdf：pip install pypdf")
    reader = PdfReader(str(pdf))
    out = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:                                     # noqa: BLE001
            txt = ""
        txt = re.sub(r"\s+", "", txt)                          # 去掉全部空白
        out.append(txt)
    return out


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _range(lo: int, hi: int) -> str:
    """'第 4 页' / '第 4-7 页'。"""
    return f"第 {lo} 页" if lo == hi else f"第 {lo}-{hi} 页"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="两份同源 PDF 逐页比对，列出有改动的页",
        epilog="改动的页再用 renderpages.py --pages <页码> 渲染出来逐页看。",
    )
    ap.add_argument("old", help="旧版 PDF")
    ap.add_argument("new", help="新版 PDF")
    ap.add_argument("--threshold", type=float, default=0.95,
                    help="页相似度低于它就判为「有改动」，默认 0.95")
    ap.add_argument("--out", help="把结果写入文件（同时仍打印到屏幕）")
    args = ap.parse_args()

    a_path, b_path = Path(args.old).expanduser(), Path(args.new).expanduser()
    for p in (a_path, b_path):
        if not p.is_file():
            sys.exit(f"[错误] 找不到文件: {p}")

    A, B = page_texts(a_path), page_texts(b_path)
    lines: list[str] = []
    add = lines.append

    add(f"旧版 {a_path.name}：{len(A)} 页")
    add(f"新版 {b_path.name}：{len(B)} 页")
    if len(A) != len(B):
        add(f"页数不同：{'+' if len(B) > len(A) else ''}{len(B) - len(A)} 页")
    add("")

    n = min(len(A), len(B))
    changed: list[tuple[int, float]] = []
    blank_old: list[int] = []
    blank_new: list[int] = []

    for i in range(n):
        r = similarity(A[i], B[i])
        if r < args.threshold:
            changed.append((i + 1, r))
        if len(A[i]) < 20:
            blank_old.append(i + 1)
        if len(B[i]) < 20:
            blank_new.append(i + 1)

    if not changed and len(A) == len(B):
        add("✓ 逐页文本一致，没有发现改动。")
    else:
        add(f"有改动的页 {len(changed)} 页（相似度 < {args.threshold:g}）：")
        for p, r in changed:
            add(f"    第 {p:>3} 页   相似 {r * 100:5.1f}%")
        if len(B) > n:
            add(f"    {_range(n + 1, len(B))}   新版新增")
        elif len(A) > n:
            add(f"    {_range(n + 1, len(A))}   新版删除")

    # 「这一页本来就抽不出字」的页要单独说：它们的相似度天然是 0，
    # 混在改动列表里会让人白跑一趟去看图。
    both_blank = sorted(set(blank_old) & set(blank_new))
    if both_blank:
        add("")
        add(f"另有 {len(both_blank)} 页两版都没有文本层（扫描件/纯图片），"
            f"相似度天然为 0，不一定是改动：")
        add("    " + ", ".join(str(p) for p in both_blank[:40])
            + (" …" if len(both_blank) > 40 else ""))

    pages = [str(p) for p, _ in changed] + [str(p) for p in range(n + 1, max(len(A), len(B)) + 1)]
    if pages:
        add("")
        add("下一步 —— 把这些页渲染出来逐页看图核对：")
        add(f"    python renderpages.py \"{b_path}\" --pages {','.join(pages)} --out <目录>")

    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"\n[完成] 已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
