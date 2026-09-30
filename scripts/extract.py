#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
extract.py — 把各类学习材料统一提取成结构化 Markdown，供后续整理成 Obsidian 笔记。

支持：
  .pptx / .ppt   幻灯片（标题、正文、表格、备注页；可选导出图片）
  .docx          文档（标题层级、段落、表格）
  .pdf           讲义/论文（分页文本；自动识别扫描页）
  .srt / .vtt    标准字幕
  .txt / .md     录音转写稿 / B站字幕稿 / 纯文本笔记
  图片           png/jpg/... 本脚本只登记路径，交由 agent 直接读图

用法：
  python extract.py <材料路径> [选项]

选项：
  --out FILE          结果写入文件（默认打印到 stdout）
  --gap SECONDS       字幕分段的停顿阈值，默认 2.5
  --extract-images D  把 PPT/PDF 内嵌图片导出到目录 D，并在文本里插入引用
  --keep-ts           保留原始时间戳（默认丢弃）
  --no-despeak        关闭口语词清洗（默认开启轻度清洗）
  --stats             额外输出材料统计摘要（时长、段落数、疑似话题切换点）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 通用工具

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
DOC_EXT = {".pptx", ".ppt", ".docx", ".doc", ".pdf", ".srt", ".vtt", ".txt", ".md", ".markdown"}


def read_text(path: Path) -> str:
    """按常见编码尝试读取文本，兼容 UTF-8 / UTF-8-BOM / GBK / UTF-16。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def split_frontmatter(text: str) -> tuple[dict, str]:
    """拆出 YAML frontmatter（Clippings 导出的字幕稿前面有一段）。"""
    meta: dict = {}
    if not text.lstrip().startswith("---"):
        return meta, text
    body = text.lstrip()
    end = body.find("\n---", 3)
    if end == -1:
        return meta, text
    fm = body[3:end]
    rest = body[end + 4:]
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            if val:
                meta[key] = val
    return meta, rest


def destutter(lines: list[str]) -> list[str]:
    """去掉连续重复行——字幕稿里很常见。"""
    out: list[str] = []
    for ln in lines:
        if out and ln.strip() and ln.strip() == out[-1].strip():
            continue
        out.append(ln)
    return out


def clean_inline(text: str) -> str:
    """清理单行文本：全角转半角空白、压缩空格、去掉零宽字符。"""
    # 注意：这里刻意不做 NFKC 归一化——NFKC 会把中文全角标点
    # （，：；（）？！）转成半角，对中文笔记是破坏性的。
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------- 字幕 / 录音稿

TS_PATTERNS = [
    # B站字幕：`00:00` 文本  或  00:00:12 文本
    re.compile(r"^`?\[?(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\]?`?\s*(.*)$"),
    # [00:00:12] 文本
    re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$"),
]


def parse_ts(token: str) -> float:
    """把 00:01:23,456 / 00:01:23.456 / 00:23 转成秒。"""
    token = token.replace(",", ".").strip()
    parts = token.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return -1.0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] if nums else -1.0


def fmt_ts(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


SRT_TIME = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_subtitles(text: str) -> list[tuple[float, str]]:
    """把字幕/转写稿解析成 [(起始秒, 文本)]。兼容 B站样式、SRT、VTT、纯时间戳行。"""
    meta, body = split_frontmatter(text)
    cues: list[tuple[float, str]] = []

    # --- SRT / VTT：靠 "时间 --> 时间" 定位
    if SRT_TIME.search(body):
        blocks = re.split(r"\n\s*\n", body)
        for blk in blocks:
            m = SRT_TIME.search(blk)
            if not m:
                continue
            start = parse_ts(m.group(1))
            lines = [l for l in blk[m.end():].splitlines() if l.strip()]
            txt = " ".join(l.strip() for l in lines)
            txt = re.sub(r"<[^>]+>", "", txt)  # 去 VTT 内联标签
            if txt.strip():
                cues.append((start, clean_inline(txt)))
        if cues:
            return cues

    # --- 逐行时间戳样式（B站字幕最常见）
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        for pat in TS_PATTERNS:
            m = pat.match(line)
            if m:
                t = parse_ts(m.group(1))
                txt = clean_inline(m.group(2))
                if txt:
                    cues.append((t, txt))
                break
        else:
            # 没有时间戳但已有上下文：当作上一句的续行
            if cues and line and not line.startswith(("#", "!", "|", ">", "<")):
                pt, ptxt = cues[-1]
                cues[-1] = (pt, (ptxt + line) if _cjk(ptxt[-1:]) else (ptxt + " " + line))

    return cues


def _cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


SENT_END = "。！？!?…；;"
FILLERS = [
    r"嗯+", r"啊+", r"呃+", r"哦+", r"好[的吧]?", r"那么", r"这个这个", r"那个那个",
    r"就是说", r"就是", r"对吧", r"你知道吧", r"这样一来", r"我们来看一下",
]


def despeak(text: str) -> str:
    """轻度口语清洗：只在句首/句尾/标点旁出现时删除填充词，避免误伤正文。"""
    for f in FILLERS:
        text = re.sub(r"(?<=[\s，。！？、；：])" + f + r"(?=[\s，。！？、；：])", "", text)
        text = re.sub(r"^" + f + r"(?=[，、])", "", text)
    text = re.sub(r"[，、]{2,}", "，", text)
    text = re.sub(r"^[，、。]+", "", text)
    return text.strip()


def join_cn(a: str, b: str) -> str:
    """拼接两段字幕文本：中文直接接，中英/数字之间补空格。"""
    if not a:
        return b
    if not b:
        return a
    if _cjk(a[-1]) or _cjk(b[0]):
        return a + b
    return a + " " + b


def auto_gap(cues: list[tuple[float, str]]) -> float:
    """自动推断段落停顿阈值。

    B站字幕常按固定 5 秒一行切分，这个间隔只反映显示节奏而非语义停顿，
    因此不能直接用固定阈值。这里以行间隔中位数为基准放大取阈值。
    """
    diffs = [cues[i + 1][0] - cues[i][0] for i in range(len(cues) - 1)]
    diffs = [d for d in diffs if 0 < d < 60]
    if not diffs:
        return 5.0
    import statistics
    med = statistics.median(diffs)
    return max(med * 1.8, 5.0)


def rebuild_paragraphs(
    cues: list[tuple[float, str]],
    gap: float | None = None,
    keep_ts: bool = False,
    do_despeak: bool = True,
    max_para: int = 220,
) -> list[tuple[float, str]]:
    """把字幕行重建成自然段：按语义停顿分组，并在超长处以标点切段。"""
    if not cues:
        return []
    if gap is None:
        gap = auto_gap(cues)

    paras: list[tuple[float, str]] = []
    cur, cur_start, last_t = "", None, None
    for t, txt in cues:
        if cur and last_t is not None and (t - last_t) >= gap:
            paras.append((cur_start, cur))
            cur, cur_start = "", None
        if cur_start is None:
            cur_start = t
        cur = join_cn(cur, txt)
        last_t = t

        # 超长段落在最近的句末标点处切开，避免出现几百字一坨的巨段
        if len(cur) >= max_para:
            cuts = list(re.finditer(r"[。！？；]", cur))
            if cuts and cuts[-1].end() >= max_para * 0.6:
                cut = cuts[-1].end()
                paras.append((cur_start, cur[:cut]))
                cur, cur_start = cur[cut:], t
    if cur.strip():
        paras.append((cur_start, cur))

    out: list[tuple[float, str]] = []
    for t, p in paras:
        p = clean_inline(p)
        if do_despeak:
            p = despeak(p)
        if not p:
            continue
        out.append((t, f"[{fmt_ts(t)}] {p}" if keep_ts else p))
    return out


# ---------------------------------------------------------------- PPTX

def extract_pptx(path: Path, img_dir: Path | None, keep_ts: bool) -> tuple[str, dict]:
    from pptx import Presentation  # 延迟导入，未装库时不影响其他格式
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    slides_out: list[str] = []
    n_img = 0
    img_dir and img_dir.mkdir(parents=True, exist_ok=True)
    total_chars = 0

    for idx, slide in enumerate(prs.slides, 1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = clean_inline(slide.shapes.title.text)
        except Exception:
            title = ""

        body: list[str] = []
        tables: list[str] = []
        notes = ""
        pics: list[str] = []

        shapes = sorted(
            slide.shapes,
            key=lambda s: (getattr(s, "top", 0) or 0, getattr(s, "left", 0) or 0),
        )
        for shp in shapes:
            try:
                if shp.has_table:
                    tables.append(_table_to_md(shp.table))
                    continue
            except Exception:
                pass
            try:
                if shp.shape_type == MSO_SHAPE_TYPE.PICTURE and img_dir is not None:
                    n_img += 1
                    image = shp.image
                    ext = image.ext or "png"
                    fn = img_dir / f"s{idx:03d}_img{n_img}.{ext}"
                    fn.write_bytes(image.blob)
                    pics.append(f"![[{fn.name}]]")
                    continue
            except Exception:
                pass
            if shp == getattr(slide.shapes, "title", None):
                continue
            try:
                if shp.has_text_frame:
                    t = clean_inline(shp.text_frame.text)
                    # 过滤页码、装饰性短文本
                    if t and not re.fullmatch(r"[\d\s./-]+", t):
                        for para in t.split("\n"):
                            para = clean_inline(para)
                            if para:
                                body.append(para)
            except Exception:
                pass

        try:
            if slide.has_notes_slide:
                notes = clean_inline(slide.notes_slide.notes_text_frame.text)
        except Exception:
            notes = ""

        total_chars += len(title) + sum(len(b) for b in body) + len(notes)
        blk = [f"<!-- slide {idx} -->"]
        blk.append(f"## {title}" if title else f"## （第 {idx} 页）")
        body = destutter(body)
        blk += [f"- {b}" for b in body]
        blk += tables
        blk += pics
        if notes:
            blk.append("")
            blk.append("> **备注页**：" + notes)
        slides_out.append("\n".join(blk))

    meta = {"页数": len(prs.slides), "字符数": total_chars, "图片数": n_img}
    return "\n\n".join(slides_out), meta


def _table_to_md(table) -> str:
    rows = []
    for r in table.rows:
        rows.append([clean_inline(c.text).replace("|", "\\|") for c in r.cells])
    if not rows:
        return ""
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join([head, sep] + body)


# ---------------------------------------------------------------- DOCX

def extract_docx(path: Path, img_dir: Path | None, keep_ts: bool) -> tuple[str, dict]:
    import docx

    doc = docx.Document(str(path))
    out: list[str] = []
    total = 0
    for para in doc.paragraphs:
        txt = clean_inline(para.text)
        if not txt:
            continue
        total += len(txt)
        style = (getattr(para.style, "name", None) or "").lower()
        m = re.search(r"heading\s*(\d)", style)
        if m:
            lvl = min(int(m.group(1)) + 1, 6)
            out.append("#" * lvl + " " + txt)
        elif "title" in style:
            out.append("# " + txt)
        else:
            out.append(txt)
    for t in doc.tables:
        md = _table_to_md(t)
        if md:
            out.append("")
            out.append(md)
    return "\n\n".join(out), {"段落数": len(out), "字符数": total}


# ---------------------------------------------------------------- PDF

def extract_pdf(path: Path, img_dir: Path | None, keep_ts: bool) -> tuple[str, dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    empty_pages: list[int] = []
    total = 0
    for i, page in enumerate(reader.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = re.sub(r"[ \t]{2,}", " ", txt)
        txt = "\n".join(clean_inline(l) for l in txt.splitlines() if l.strip())
        total += len(txt)
        if len(txt) < 20:
            empty_pages.append(i)
            pages.append(f"<!-- 第 {i} 页：无文本层，疑似扫描件或纯图片 -->\n（第 {i} 页需要直接看图）")
        else:
            pages.append(f"<!-- 第 {i} 页 -->\n{txt}")
    meta = {
        "页数": len(reader.pages),
        "字符数": total,
        "疑似扫描页": empty_pages,
    }
    return "\n\n".join(pages), meta


# ---------------------------------------------------------------- 入口

def stats_block(cues: list[tuple[float, str]], paras: list[tuple[float, str]], gap: float) -> str:
    """给 agent 的材料统计摘要，帮助定位章节边界。"""
    if not cues:
        return ""
    dur = cues[-1][0] - cues[0][0]
    lines = [
        "<!-- ===== 材料统计 =====",
        f"    时长: {fmt_ts(dur)} ({dur:.0f}s)",
        f"    字幕行数: {len(cues)}",
        f"    重建段落数: {len(paras)}",
        f"    平均段落长度: {sum(len(p) for _, p in paras) // max(1, len(paras))} 字",
        f"    分段停顿阈值: {gap:.1f}s（按材料节奏自动推断）",
        f"    大停顿点(>={gap * 1.5:.0f}s，疑似话题切换/章节边界):",
    ]
    threshold = gap * 1.5
    prev = cues[0][0]
    hits = 0
    for t, txt in cues:
        if t - prev >= threshold:
            lines.append(f"      {fmt_ts(t)} (+{t - prev:.0f}s)  … {txt[:44]}")
            hits += 1
            if hits >= 25:
                lines.append("      ...（更多停顿点已省略）")
                break
        prev = t
    if hits == 0:
        lines.append("      （无明显停顿，内容节奏均匀）")
    lines.append("     ===================== -->")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="学习材料 → 结构化 Markdown")
    ap.add_argument("path", help="材料文件路径")
    ap.add_argument("--out", help="输出文件（默认 stdout）")
    ap.add_argument("--gap", default="auto",
                    help="字幕分段停顿阈值(秒)，默认 auto 按材料节奏自动推断")
    ap.add_argument("--max-para", type=int, default=320, help="单段落最大字数")
    ap.add_argument("--extract-images", help="导出内嵌图片的目录")
    ap.add_argument("--keep-ts", action="store_true", help="保留时间戳")
    ap.add_argument("--no-despeak", action="store_true", help="关闭口语词清洗")
    ap.add_argument("--stats", action="store_true", help="输出材料统计摘要")
    args = ap.parse_args()

    gap_override = None
    if str(args.gap).strip().lower() not in ("auto", "none", ""):
        try:
            gap_override = float(args.gap)
        except ValueError:
            print(f"[错误] --gap 需要数字或 auto，收到: {args.gap}", file=sys.stderr)
            return 3

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"[错误] 文件不存在: {path}", file=sys.stderr)
        return 2

    ext = path.suffix.lower()
    img_dir = Path(args.extract_images).resolve() if args.extract_images else None

    # 老版二进制格式：python-pptx / python-docx 打不开。先给一条能照做的提示，
    # 而不是让用户迎面撞上库内部的 PackageNotFoundError。
    if ext in (".ppt", ".doc"):
        to = "pptx" if ext == ".ppt" else "docx"
        print(f"[错误] {ext} 是老版二进制格式，无法直接解析。", file=sys.stderr)
        print("       请先转换格式（见 references/extraction.md 路线 A3）：", file=sys.stderr)
        print(f'         soffice --headless --convert-to {to} --outdir "<输出目录>" "{path}"',
              file=sys.stderr)
        return 3

    if ext in IMG_EXT:
        body = (
            f"<!-- 图片材料，需要 agent 直接用读图能力查看，不要试图用脚本 OCR -->\n"
            f"![[{path.name}]]\n"
        )
        meta = {"类型": "图片", "说明": "需 agent 视觉读取"}
    elif ext == ".pptx":
        body, meta = extract_pptx(path, img_dir, args.keep_ts)
    elif ext == ".docx":
        body, meta = extract_docx(path, img_dir, args.keep_ts)
    elif ext == ".pdf":
        body, meta = extract_pdf(path, img_dir, args.keep_ts)
    elif ext in (".srt", ".vtt", ".txt", ".md", ".markdown"):
        text = read_text(path)
        fm, _ = split_frontmatter(text)
        cues = parse_subtitles(text)
        if cues:
            gap_val = gap_override if gap_override is not None else auto_gap(cues)
            paras = rebuild_paragraphs(cues, gap=gap_val, keep_ts=args.keep_ts,
                                       do_despeak=not args.no_despeak,
                                       max_para=args.max_para)
            body = "\n\n".join(p for _, p in paras)
            if args.stats:
                body = stats_block(cues, paras, gap_val) + "\n\n" + body
            # 字数过少的"字幕"往往是普通笔记，直接原样返回更安全
            if len(body) < 50:
                body = text
            meta = {
                "类型": "字幕/转写稿",
                "字幕行数": len(cues),
                "重建段落数": len(paras),
                "时长": fmt_ts(cues[-1][0] - cues[0][0]) if cues else "0",
            }
            if fm:
                meta["来源元数据"] = fm
        else:
            body = text
            meta = {"类型": "纯文本"}
    else:
        print(f"[错误] 暂不支持的格式: {ext}", file=sys.stderr)
        print(f"[提示] 支持: {', '.join(sorted(DOC_EXT | IMG_EXT))}", file=sys.stderr)
        return 3

    header = [f"<!-- 来源: {path.name} -->"]
    if meta:
        header.append("<!-- " + "; ".join(f"{k}={v}" for k, v in meta.items()) + " -->")
    result = "\n".join(header) + "\n\n" + body + "\n"

    if args.out:
        Path(args.out).write_text(result, encoding="utf-8", newline="\n")
        print(f"[完成] 已写入 {args.out}  ({len(result)} 字符)")
        for k, v in meta.items():
            print(f"        {k}: {v}")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
