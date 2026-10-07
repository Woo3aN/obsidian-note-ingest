#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 PDF 页面渲染成 PNG —— 「识图模式」的入口。

什么时候用（满足任意一条就该用）：
  * `extract.py` 抽出来的正文里公式散架、上下标分家、表格错位（课件、讲义最常见）
  * 报告里出现「疑似扫描页 = [...]」，这些页 pypdf 一个字都抽不出来
  * 材料本身以图为主：示意图、装置图、坐标图、手写答案

多数环境的「读文件」能力把 PDF 当字节流读、**不渲染页面**，所以不能直接读一个 PDF 看。
正确做法是用本脚本把目标页渲染成 PNG，再逐页看图，**以图为准**。

用法：
    # 1) 先体检：只看哪些页没有文本层，判断要不要开识图模式（不渲染）
    python renderpages.py 材料.pdf --list

    # 2) 渲染第 3–23 页
    python renderpages.py 材料.pdf --pages 3-23 --out <目录>

    # 3) 混合写法；公式密集的页可以加分辨率
    python renderpages.py 材料.pdf --pages 1,5,7-9 --out <目录> --dpi 140

    # 4) 省略 --pages 则整本渲染（页多时慎用）
    python renderpages.py 材料.pdf --out <目录>

依赖 pymupdf（skill 的 venv 里已有）。
"""
import argparse
import atexit
import os
import re
import sys

try:  # Windows 控制台默认 cp936，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import pymupdf
except ImportError:  # pragma: no cover
    sys.exit("[错误] 缺少 pymupdf：pip install pymupdf")


# 「这一页几乎没有文本层」的判定阈值，与 extract.py 保持一致：
# 两边都按「去空白后不足 20 字符」判定，免得同一份 PDF 给出两份不同的
# 「疑似扫描页」名单。抽取库不同（pymupdf vs pypdf），结果可能有细微出入。
EMPTY_TEXT_CHARS = 20


def parse_pages(spec, total):
    """'1,5,7-9' → [1,5,7,8,9]；越界页会被丢弃并提示。

    容忍从 `extract.py --stats` 直接复制过来的形式，带方括号也认
    （它打印的是 Python 列表：`疑似扫描页=[4, 26, 54, 80]`）。
    """
    spec = spec.strip().strip("[](){}")          # 去掉整体方括号
    out = []
    for part in spec.split(","):
        part = part.strip().strip("[](){}")      # 也去掉每个片段上残留的括号
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            sys.exit(f"[错误] 无法解析页码片段: {part!r}（可用形式：3-23 / 1,5,7-9 / [1, 5, 7]）")
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        sys.exit("[错误] --pages 解析后为空")
    bad = [p for p in uniq if p < 1 or p > total]
    if bad:
        print(f"[警告] 超出范围（共 {total} 页），已忽略: {bad}", file=sys.stderr)
        uniq = [p for p in uniq if 1 <= p <= total]
    if not uniq:
        sys.exit("[错误] 没有合法页码")
    return uniq


def main():
    ap = argparse.ArgumentParser(
        description="把 PDF 页面渲染成 PNG，供逐页看图核对",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf", help="材料 PDF 路径")
    ap.add_argument("--pages", help="页码范围，如 3-23 / 1,5,7-9；省略则整本")
    ap.add_argument("--out", help="输出目录（--list 时不需要）")
    ap.add_argument("--dpi", type=int, default=120,
                    help="渲染分辨率，默认 120；公式密集可提到 140（默认: %(default)s)")
    ap.add_argument("--list", action="store_true",
                    help="只体检：列出每页有无文本层，不渲染")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        sys.exit(f"[错误] 找不到文件: {args.pdf}")

    doc = pymupdf.open(args.pdf)
    atexit.register(doc.close)   # 进程退出时释放句柄（Windows 上不释放会挡住删除/覆盖该 PDF）
    total = doc.page_count

    if args.list:
        empty, withtext = [], []
        for i in range(total):
            txt = doc[i].get_text().strip()
            (withtext if len(txt) >= EMPTY_TEXT_CHARS else empty).append(i + 1)
        print(f"页数: {total}")
        print(f"有文本层 {len(withtext)} 页；无/几乎无文本层 {len(empty)} 页")
        if empty:
            print(f"疑似扫描页={empty}")     # 与 extract.py --stats 同格式，可直接粘给 --pages
            print("→ 这些页必须渲染成图来看，别跳过")
        print("判定: " + (
            "文本层不完整，建议对上述页开识图模式"
            if empty else "全页都有文本层（但公式仍可能散架，公式多的页仍建议看图）"
        ))
        return 0

    if not args.out:
        sys.exit("[错误] 需要 --out <目录>（或用 --list 只体检）")
    pages = parse_pages(args.pages, total) if args.pages else list(range(1, total + 1))

    os.makedirs(args.out, exist_ok=True)
    saved = []
    for p in pages:
        path = os.path.join(args.out, f"p{p:03d}.png")
        doc[p - 1].get_pixmap(dpi=args.dpi).save(path)
        saved.append(path)

    print(f"[完成] 渲染 {len(saved)} 页（dpi={args.dpi}）→ {args.out}")
    print("接下来逐页看图，以图为准；文本层只当检索用的草稿。")
    for path in saved:
        print("  " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
