#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公式型 PDF 的「版面重建」—— 当前环境没有视觉能力时，替代识图模式的正解。

为什么不是普通文本提取
----------------------
PPT / Word 导出的公式型 PDF，公式是排版对象：分式是「横线 + 上下两行文本」，
上下标是「更小的字号 + 偏移的基线」，希腊字母走 Symbol 字体、Unicode 落在
U+F0xx 私有区（pypdf 抽出来是一片空白或乱码）。普通提取必然散架，硬按
物理含义「重排」会得到「结果对、推导过程全错」的笔记。

本脚本改成读**版面几何**：
  1. 用 Adobe Symbol 官方编码表（references/symbol_map.json）把私有区码位
     还原成 ∑ ∫ ε Φ λ θ ρ σ ⋅ × ≈ ∂ ∇ ∞ 等真字符；
  2. 用细长水平线 + 上下相邻文本的坐标关系还原分式；
  3. 用字号与基线偏移还原上下标；
  4. 用「浮在字母上方的空 span」识别矢量箭头（\\vec）。
几何是确定性的，不靠猜。

同时能定位插图：把跨页重复出现的模板装饰（背景网格、页脚）剔掉之后，
剩下的矢量笔画包围盒就是插图 —— 因为公式是文字对象，不是笔画。

用法
----
    PY="$PY"   # 见 SKILL.md「环境」一节；换机器时按该节重新确认解释器路径
    $PY pdf_layout.py "<材料.pdf>" --pages 24-42                # 重建正文
    $PY pdf_layout.py "<材料.pdf>" --pages 24-42 --geom         # 附坐标，用于核对
    $PY pdf_layout.py "<材料.pdf>" --figs 3-40                  # 定位插图区域
    $PY pdf_layout.py "<材料.pdf>" --figs 18,21 --extract "<目录>"

注意
----
- 这只是**替代**方案。一旦有视觉模型，仍然应该渲染成 PNG 逐页用眼睛核一遍
  （`renderpages.py`），本脚本主要用来快速拿到可读的公式草稿 + 定位插图。
- 重建结果里的 `\\frac{}{}`、`_{}`、`^{}` 是结构还原，仍要自己判断物理含义，
  尤其是分式的分子分母归属、θ 的定义、坐标系朝向。
"""
import argparse
import atexit
import json
import os
import re
import sys
from collections import Counter

import pymupdf

try:  # Windows 控制台默认 cp936，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SYM_MAP = os.path.join(HERE, os.pardir, "references", "symbol_map.json")

CUS = {0xF8EB: "⎛", 0xF8EC: "⎜", 0xF8ED: "⎝", 0xF8EE: "⎡", 0xF8EF: "⎢",
       0xF8F0: "⎣", 0xF8F6: "⎞", 0xF8F7: "⎟", 0xF8F8: "⎠", 0xF8F9: "⎤",
       0xF8FA: "⎥", 0xF8FB: "⎦"}


def _load_sym():
    p = os.path.normpath(SYM_MAP)
    if not os.path.exists(p):
        raise SystemExit(
            f"缺少 Symbol 映射表：{p}\n"
            "从 https://www.unicode.org/Public/MAPPINGS/VENDORS/ADOBE/symbol.txt "
            "重新生成（见 SKILL.md「版面重建」一节）")
    return {int(k): v for k, v in json.load(open(p, encoding="utf-8")).items()}


SYM = _load_sym()


def decode(txt):
    out = []
    for ch in txt:
        o = ord(ch)
        if o == 0x20:
            out.append(" ")
        elif 0xF000 <= o <= 0xF0FF:
            out.append(SYM.get(o & 0xFF, ch))
        else:
            out.append(CUS.get(o, ch))
    return "".join(out)


# ---------------------------------------------------------------- 文本重建

def spans_of(page):
    out = []
    for b in page.get_text("rawdict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                if not s["chars"]:
                    continue
                txt = decode("".join(c["c"] for c in s["chars"]))
                if not txt.strip():
                    continue
                x0, y0, x1, y1 = s["bbox"]
                out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "size": s["size"], "text": txt})
    out.sort(key=lambda s: (round(s["y0"]), round(s["x0"])))
    keep = []                                    # 去掉动画造成的完全重叠副本
    for s in out:
        if any(k["text"] == s["text"] and abs(k["x0"] - s["x0"]) < 4
               and abs(k["y0"] - s["y0"]) < 5 for k in keep[-4:]):
            continue
        keep.append(s)

    arrows = [s for s in keep if s["text"] == "ϖ"]      # PPT 画在字母上方的矢量箭头
    if arrows:
        rest = [s for s in keep if s["text"] != "ϖ"]
        for a in arrows:
            best, bestov = None, 0
            for s in rest:
                d = s["y0"] - a["y0"]
                if 4 < d < 34:
                    ov = min(s["x1"], a["x1"]) - max(s["x0"], a["x0"])
                    if ov > bestov:
                        best, bestov = s, ov
            if best is not None and bestov > 2:
                best["text"] = "\\vec{%s}" % best["text"]
            else:
                rest.append(a)
        keep = rest
    return keep


def bars_of(page):
    bars = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 1.0 and 8 < abs(p1.x - p2.x) < 170:
                    bars.append((round(min(p1.y, p2.y), 1),
                                 round(min(p1.x, p2.x), 1),
                                 round(max(p1.x, p2.x), 1)))
            elif it[0] == "re":
                r = it[1]
                if r.height < 2.4 and 8 < r.width < 170:
                    bars.append((round((r.y0 + r.y1) / 2, 1), round(r.x0, 1), round(r.x1, 1)))
    return sorted(set(bars))


def join(sp):
    if not sp:
        return ""
    sp = sorted(sp, key=lambda s: s["x0"])
    out, prev = "", None
    for s in sp:
        if prev is not None and s["x0"] - prev > max(1.6, 0.17 * s["size"]):
            out += " "
        out += s["text"]
        prev = s["x1"] if prev is None else max(prev, s["x1"])
    return out


def rebuild(page):
    spans = spans_of(page)
    used, fracs = set(), []
    for by, bx0, bx1 in bars_of(page):
        nums = [s for s in spans if id(s) not in used
                and min(s["x1"], bx1) - max(s["x0"], bx0) > 1
                and -30 < s["y1"] - by < -0.3]
        dens = [s for s in spans if id(s) not in used
                and min(s["x1"], bx1) - max(s["x0"], bx0) > 1
                and 0.3 < s["y0"] - by < 30]
        if not nums or not dens:
            continue
        if by - max(s["y1"] for s in nums) > 8:
            continue
        if min(s["y0"] for s in dens) - by > 8:
            continue
        nx0, nx1 = min(s["x0"] for s in nums), max(s["x1"] for s in nums)
        dx0, dx1 = min(s["x0"] for s in dens), max(s["x1"] for s in dens)
        if min(nx1, dx1) - max(nx0, dx0) < 3:
            continue
        # 横线宽度要和分子/分母相当，否则是模板网格线被误当成分式线
        W = max(nx1 - nx0, dx1 - dx0)
        if W > 150 or not (0.45 * W <= bx1 - bx0 <= 1.45 * W):
            continue
        used.update(id(s) for s in nums)
        used.update(id(s) for s in dens)
        fracs.append({"x0": bx0, "x1": bx1, "y0": by, "y1": by,
                      "size": max(s["size"] for s in nums + dens),
                      "text": "\\frac{%s}{%s}" % (join(nums), join(dens))})

    items = [s for s in spans if id(s) not in used] + fracs
    for it in items:
        it["cy"] = (it["y0"] + it["y1"]) / 2
    items.sort(key=lambda s: s["cy"])

    lines = []
    for it in items:
        if lines and abs(it["cy"] - lines[-1]["cy"]) < 0.6 * max(it["size"], lines[-1]["size"]):
            L = lines[-1]
            L["items"].append(it)
            L["cy"] = sum(x["cy"] for x in L["items"]) / len(L["items"])
            L["size"] = max(L["size"], it["size"])
        else:
            lines.append({"cy": it["cy"], "size": it["size"], "items": [it]})

    out = []
    for L in lines:
        its = sorted(L["items"], key=lambda s: s["x0"])
        base = max(s["size"] for s in its)
        maj = [s for s in its if s["size"] > 0.82 * base]
        axis = sum(s["cy"] for s in maj) / len(maj) if maj else L["cy"]

        def emit(s):
            t = s["text"]
            if s["size"] > 0.82 * base or t.startswith("\\frac") or t.startswith("\\vec"):
                return t
            if s["cy"] < axis - 2:
                return "^{%s}" % t
            if s["cy"] > axis + 2:
                return "_{%s}" % t
            return t

        buf, i = "", 0
        while i < len(its):
            cur = emit(its[i])
            m = re.match(r"^([\^_])\{(.*)\}$", cur, re.S)
            if m:
                mark, body, j = m.group(1), m.group(2), i + 1
                while j < len(its):
                    m2 = re.match(r"^([\^_])\{(.*)\}$", emit(its[j]), re.S)
                    if m2 and m2.group(1) == mark:
                        body += m2.group(2)
                        j += 1
                    else:
                        break
                buf += mark + "{" + body + "}"
                i = j
            else:
                buf += cur
                i += 1
        out.append(re.sub(r"\s+", " ", buf).strip())
    return "\n".join(x for x in out if x)


def geom(page):
    lines = []
    for s in sorted(spans_of(page), key=lambda s: (round(s["y0"]), s["x0"])):
        lines.append(f"  y={s['y0']:7.1f} x={s['x0']:6.1f} sz={s['size']:5.2f} |{s['text']}|")
    lines.append("  --- 细长水平线（分式横线候选） ---")
    for b in bars_of(page):
        lines.append(f"    y={b[0]:7.1f}  x {b[1]:.1f}~{b[2]:.1f}  宽 {b[2]-b[1]:.1f}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 插图定位

def _items(page):
    out = []
    for d in page.get_drawings():
        for it in d["items"]:
            k = it[0]
            if k == "l":
                r = pymupdf.Rect(it[1].x, it[1].y, it[2].x, it[2].y).normalize()
            elif k == "re":
                r = pymupdf.Rect(it[1])
            elif k in ("c", "qu"):
                pts = [p for p in it[1:] if hasattr(p, "x")]
                if not pts:
                    continue
                r = pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts),
                                 max(p.x for p in pts), max(p.y for p in pts))
            else:
                continue
            out.append(r)
    return out


def _sig(r):
    return (int(r.x0 // 4), int(r.y0 // 4), int(r.x1 // 4), int(r.y1 // 4))


def fig_regions(doc):
    freq, per = Counter(), {}
    for p in range(doc.page_count):
        per[p] = _items(doc[p])
        for s in {_sig(r) for r in per[p]}:
            freq[s] += 1
    tmpl = {s for s, c in freq.items() if c >= max(6, doc.page_count * 0.12)}
    out = {}
    for p in range(doc.page_count):
        page = doc[p].rect
        live = [r for r in per[p] if _sig(r) not in tmpl and r.width + r.height > 3
                and page.contains(pymupdf.Point(r.x0, r.y0))
                and page.contains(pymupdf.Point(r.x1, r.y1))]
        if not live:
            out[p] = None
            continue
        box = pymupdf.Rect(live[0])
        for r in live[1:]:
            box |= r
        # 用中位数 + 3×MAD 甩掉个别离群笔画（少数杂笔画会把包围盒拉满整页）
        xc = sorted((r.x0 + r.x1) / 2 for r in live)
        yc = sorted((r.y0 + r.y1) / 2 for r in live)
        mx, my = xc[len(xc) // 2], yc[len(yc) // 2]
        madx = sorted(abs(v - mx) for v in xc)[len(xc) // 2] or 40
        mady = sorted(abs(v - my) for v in yc)[len(yc) // 2] or 40
        core = [r for r in live
                if abs((r.x0 + r.x1) / 2 - mx) <= 4 * madx + 30
                and abs((r.y0 + r.y1) / 2 - my) <= 4 * mady + 30]
        if not core:
            core = live
        cb = pymupdf.Rect(core[0])
        for r in core[1:]:
            cb |= r
        out[p] = {"all": box, "core": cb, "n": len(live), "ncore": len(core)}
    return out


def trim_bg(im, tol=48, pad=12):
    from PIL import Image
    w, h = im.size
    px = im.convert("RGB").load()
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


def main():
    ap = argparse.ArgumentParser(description="公式型 PDF 的版面重建 / 插图定位")
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="页码，如 24-42 或 18,21,29")
    ap.add_argument("--geom", action="store_true", help="附坐标明细，用于核对")
    ap.add_argument("--figs", metavar="PAGES", help="定位插图区域")
    ap.add_argument("--extract", metavar="DIR", help="把 --figs 指到的页的插图导出到目录")
    ap.add_argument("--dpi", type=int, default=175)
    a = ap.parse_args()

    def parse(s):
        out = []
        for part in s.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                lo, hi = part.split("-")
                out += list(range(int(lo), int(hi) + 1))
            else:
                out.append(int(part))
        return out

    doc = pymupdf.open(a.pdf)
    atexit.register(doc.close)   # 进程退出时释放句柄（Windows 上不释放会挡住删除/覆盖该 PDF）

    if a.pages:
        for p in parse(a.pages):
            print(f"########## 第 {p} 页 ##########")
            print(geom(doc[p - 1]) if a.geom else rebuild(doc[p - 1]))
            print()

    if a.figs:
        regs = fig_regions(doc)
        outdir = a.extract
        if outdir:
            os.makedirs(outdir, exist_ok=True)
        for p in parse(a.figs):
            r = regs.get(p - 1)
            if not r:
                print(f"p{p:03d}  无插图笔画")
                continue
            print(f"p{p:03d}  笔画 {r['n']} 条")
            print(f"          完整包围盒 ({r['all'].x0:.0f},{r['all'].y0:.0f})-"
                  f"({r['all'].x1:.0f},{r['all'].y1:.0f})  "
                  f"{r['all'].width:.0f}x{r['all'].height:.0f}   ← 按它裁图形一定不被切")
            print(f"          最密集处   ({r['core'].x0:.0f},{r['core'].y0:.0f})-"
                  f"({r['core'].x1:.0f},{r['core'].y1:.0f})  "
                  f"{r['core'].width:.0f}x{r['core'].height:.0f}   ← 仅参考，常只是图形一部分")
            # 同页大位图（优先无损提取，不要裁）
            for info in doc[p - 1].get_image_info(xrefs=True):
                bb = info["bbox"]
                if (bb[2] - bb[0]) * (bb[3] - bb[1]) > 15000:
                    print(f"          位图 xref={info['xref']}  "
                          f"({bb[0]:.0f},{bb[1]:.0f})-({bb[2]:.0f},{bb[3]:.0f})  "
                          f"{info['width']}x{info['height']}px")
            if outdir:
                box = r["core"]
                pix = doc[p - 1].get_pixmap(dpi=a.dpi, clip=box)
                from PIL import Image
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                t, bg = trim_bg(im)
                if t is not None:
                    fp = os.path.join(outdir, f"p{p:03d}.png")
                    t.save(fp)
                    print(f"          → {fp}  {t.size[0]}x{t.size[1]}px  底色{bg}")
    if not a.pages and not a.figs:
        ap.print_help()


if __name__ == "__main__":
    main()
