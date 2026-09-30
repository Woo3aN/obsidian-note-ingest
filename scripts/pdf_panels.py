#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「纯色面板」定位课件插图并裁出 PNG —— 目前最可靠的框选办法。

为什么要有它
------------
按**笔画包围盒**估框会切掉图里的**文字标注**（标注是文字对象、不是笔画）：
切掉的往往正是公式符号与标号，而且只有在有视觉时逐张看才发现。
而 PPT 课件常把插图装在一块**纯色面板**里（常见深绿 `(0,102,0)`，页面底色深蓝）
——「该颜色的连通域」就是面板的精确边界，按它裁一张都不会切边。

用法
----
    PY="$PY"   # 见 SKILL.md「环境」一节；换机器时按该节重新确认解释器路径
    $PY pdf_panels.py "<材料.pdf>" --probe 2-12                 # 先看各页主色，确认面板色
    $PY pdf_panels.py "<材料.pdf>" --find 2-12                  # 列出每页的面板框
    $PY pdf_panels.py "<材料.pdf>" --crop 2,3,10 --out "<目录>"  # 裁出来（自动去底色边）
    $PY pdf_panels.py "<材料.pdf>" --crop 25 --merge --out "<目录>"   # 同页多块面板并排拼一张
    $PY pdf_panels.py "<材料.pdf>" --scan 17,18 --region 440,55,645,175  # 图不在面板里时按亮度找

参数
----
    --color R,G,B   面板颜色，默认 0,102,0（深绿）；先用 --probe 确认
    --tol N         颜色容差（通道差绝对值之和），默认 110
    --which N       同页多块面板时取第几块（按面积从大到小，0=最大，默认 0）
    --dpi N         输出分辨率，默认 190
    --min-cells N   连通域最小格数，默认 200（滤掉小色块）
    --scan PAGES    按「比背景亮」找图块，给页号；用于插图**不在纯色面板里**的情况
    --region x0,y0,x1,y1  --scan 的粗略范围（pt，可选）；先给个大概范围能减少文字干扰
    --span N        --scan 的亮度阈值，默认 45

注意
----
1. 裁完**必须亲眼看图复核**：面板有时会把「页面里的公式高亮小框」也当成面板，
   而真正的示意图并不在里面。
2. 裁出来是深色底（面板本身是深绿）——要不要转白底先问使用者，盲转会把白色线条一起吃掉。
3. 内嵌位图（`doc.extract_image(xref)`）能拿原生分辨率，比按 dpi 裁更好；位图优先用那条路。
4. `--scan` 只找**成片的亮块**（浅蓝 / 白色填充的图）。对「白线条画在深底上」的图无效，
   那种图要手工量坐标或直接按整页裁。
   `--scan` 给的是粗定位，裁完仍要肉眼复核。
"""
import argparse
import atexit
import os
import sys
from collections import Counter

import pymupdf
from PIL import Image

try:  # Windows 控制台默认 cp936，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CELL = 6


def hex2rgb(s):
    return tuple(int(x) for x in s.split(','))


def probe(doc, pages, dpi=100):
    for p in pages:
        pix = doc[p - 1].get_pixmap(dpi=dpi)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        px = im.load()
        cnt = Counter()
        for y in range(0, im.height, 3):
            for x in range(0, im.width, 3):
                cnt[px[x, y]] += 1
        tot = sum(cnt.values())
        top = [(c, round(n / tot * 100, 1)) for c, n in cnt.most_common(4)]
        print(f"p{p:03d}  {top}")


def panels(doc, pno, color, tol, dpi=120, min_cells=200):
    pix = doc[pno - 1].get_pixmap(dpi=dpi)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    px = im.load()
    w, h = im.size
    gw, gh = w // CELL, h // CELL
    r0, g0, b0 = color
    grid = [[bool(px[gx * CELL, gy * CELL]) and
             abs(px[gx * CELL, gy * CELL][0] - r0) +
             abs(px[gx * CELL, gy * CELL][1] - g0) +
             abs(px[gx * CELL, gy * CELL][2] - b0) < tol
             for gx in range(gw)] for gy in range(gh)]
    seen, out = set(), []
    for gy in range(gh):
        for gx in range(gw):
            if not grid[gy][gx] or (gy, gx) in seen:
                continue
            stack, comp = [(gy, gx)], []
            seen.add((gy, gx))
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < gh and 0 <= nx < gw and grid[ny][nx] and (ny, nx) not in seen:
                            seen.add((ny, nx))
                            stack.append((ny, nx))
            if len(comp) < min_cells:
                continue
            ys = [c[0] for c in comp]
            xs = [c[1] for c in comp]
            out.append((min(xs) * CELL, min(ys) * CELL,
                        (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    s = dpi / 72.0
    res = [(round(b[0] / s, 1), round(b[1] / s, 1), round(b[2] / s, 1), round(b[3] / s, 1))
           for b in out]
    res.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return res


def scan(doc, pno, region=None, dpi=140, span=45, min_cells=150):
    """按「比背景亮」找图块 —— 用于插图**不在纯色面板里**的情况（浅蓝 / 白色图块）。

    并非所有插图都装在面板里，有些直接画在底色或浅色块上，
    只能先用眼睛估一个大致范围（region），再用它收紧到精确边界。

    region: (x0, y0, x1, y1) pt，可选；先粗框一块能显著减少页面文字的干扰
    span:   亮度阈值 —— 像素 RGB 之和 > 背景中位数 + span 才算作「图块」
    """
    try:
        import numpy as np
    except ImportError:
        sys.exit("--scan 需要 numpy，请先装：pip install numpy")
    clip = pymupdf.Rect(region) if region else None
    pix = doc[pno - 1].get_pixmap(dpi=dpi, clip=clip)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    a = np.asarray(im).astype(int).sum(axis=2)
    # 背景基准取**低分位数**而不是中位数：region 框得较紧时图会占大半面积，
    # 中位数会落在图内部，判据就失效了。
    bg = int(np.percentile(a, 10))
    h, w = a.shape
    gw, gh = max(1, w // CELL), max(1, h // CELL)
    grid = [[bool(a[gy * CELL, gx * CELL] > bg + span) for gx in range(gw)]
            for gy in range(gh)]
    seen, out = set(), []
    for gy in range(gh):
        for gx in range(gw):
            if not grid[gy][gx] or (gy, gx) in seen:
                continue
            stack, comp = [(gy, gx)], []
            seen.add((gy, gx))
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < gh and 0 <= nx < gw and grid[ny][nx] and (ny, nx) not in seen:
                            seen.add((ny, nx))
                            stack.append((ny, nx))
            if len(comp) < min_cells:
                continue
            ys = [c[0] for c in comp]
            xs = [c[1] for c in comp]
            out.append((min(xs) * CELL, min(ys) * CELL,
                        (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    s = dpi / 72.0
    ox, oy = (clip.x0, clip.y0) if clip else (0.0, 0.0)
    res = [(round(ox + b[0] / s, 1), round(oy + b[1] / s, 1),
            round(ox + b[2] / s, 1), round(oy + b[3] / s, 1)) for b in out]
    res.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return res


def trim_bg(im, tol=48, pad=10):
    px = im.convert("RGB").load()
    w, h = im.size
    cnt = Counter()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            cnt[px[x, y]] += 1
    bg = cnt.most_common(1)[0][0]
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) > tol:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, bg
    return im.crop((max(0, min(xs) - pad), max(0, min(ys) - pad),
                    min(w, max(xs) + 1 + pad), min(h, max(ys) + 1 + pad))), bg


def parse(s):
    out = []
    for part in str(s).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-")
            out += list(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser(description="按纯色面板定位并裁出课件插图")
    ap.add_argument("pdf")
    ap.add_argument("--probe", help="先看主色，如 2-12")
    ap.add_argument("--find", help="列出面板框，如 2-12")
    ap.add_argument("--scan", help="按亮度找图块（不在纯色面板里的插图），如 17,18,34")
    ap.add_argument("--region", help="--scan 的粗略范围 pt，如 430,180,560,400（可选，能减少文字干扰）")
    ap.add_argument("--span", type=int, default=45, help="--scan 的亮度阈值，默认 45")
    ap.add_argument("--crop", help="裁切这些页，如 2,3,10")
    ap.add_argument("--out", help="输出目录（--crop 必填）")
    ap.add_argument("--merge", action="store_true", help="同页多块面板并排拼成一张")
    ap.add_argument("--which", type=int, default=0, help="取第几大面板，默认 0")
    ap.add_argument("--color", default="0,102,0", help="面板颜色 R,G,B")
    ap.add_argument("--tol", type=int, default=110)
    ap.add_argument("--dpi", type=int, default=190)
    ap.add_argument("--min-cells", type=int, default=200)
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    atexit.register(doc.close)   # 进程退出时释放句柄（Windows 上不释放会挡住删除/覆盖该 PDF）
    color = hex2rgb(a.color)

    if a.probe:
        probe(doc, parse(a.probe))
    if a.find:
        for p in parse(a.find):
            ps = panels(doc, p, color, a.tol, min_cells=a.min_cells)
            msg = "   ".join(f"({b[0]},{b[1]})-({b[2]},{b[3]}) {b[2]-b[0]:.0f}x{b[3]-b[1]:.0f}pt"
                             for b in ps[:3])
            print(f"p{p:03d}  {msg or '无面板'}")
    if a.scan:
        reg = tuple(float(v) for v in a.region.split(",")) if a.region else None
        for p in parse(a.scan):
            bs = scan(doc, p, reg, span=a.span)
            msg = "   ".join(f"({b[0]},{b[1]})-({b[2]},{b[3]}) {b[2]-b[0]:.0f}x{b[3]-b[1]:.0f}pt"
                             for b in bs[:3])
            print(f"p{p:03d}  {msg or '未找到亮块（试试放宽 --span 或给 --region）'}")
    if a.crop:
        if not a.out:
            sys.exit("--crop 需要 --out")
        os.makedirs(a.out, exist_ok=True)
        for p in parse(a.crop):
            ps = panels(doc, p, color, a.tol, min_cells=a.min_cells)
            if not ps:
                print(f"p{p:03d}  ✗ 无面板")
                continue
            use = ps if a.merge else [ps[min(a.which, len(ps) - 1)]]
            use = sorted(use, key=lambda b: b[0])
            ims = []
            for b in use:
                pix = doc[p - 1].get_pixmap(dpi=a.dpi, clip=pymupdf.Rect(b))
                im, _ = trim_bg(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                if im is None:
                    print("    [跳过] 该面板整块都是底色，没有可裁的内容")
                    continue
                ims.append(im)
            if len(ims) == 1:
                out = ims[0]
            else:
                H = max(i.height for i in ims)
                ims = [i.resize((round(i.width * H / i.height), H), Image.LANCZOS) for i in ims]
                gap = 30
                out = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) - 1), H), "white")
                x = 0
                for i in ims:
                    out.paste(i, (x, 0))
                    x += i.width + gap
            fp = os.path.join(a.out, f"p{p:03d}.png")
            out.save(fp)
            print(f"p{p:03d}  → {fp}  {out.size[0]}x{out.size[1]}px  面板{len(use)}块")


if __name__ == "__main__":
    main()
