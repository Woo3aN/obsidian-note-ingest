#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
vaultio.py — Obsidian vault 的安全读写层。

设计原则：任何对已有笔记的修改，先备份再落盘；一切写入都可通过 restore 回滚。

命令：
  vaults                                   列出本机所有 vault
  find <vault> <关键词>                     在 vault 里找候选笔记（文件名/标题/tag/正文）
  outline <笔记路径>                        打印标题树 + 行号 + 锚点（供定位插入点）
  check <笔记路径> --content-file f.md       写入前查重（评估与已有内容的重复度）
                                            退出码 13=版本修订 10=已被覆盖 11=部分重叠 0=新内容
  read <笔记路径> [--head N]                读取笔记
  new <笔记路径> --title T [--tags a,b] [--source S] [--force]
                                            新建笔记（带 frontmatter 模板；已存在则拒绝，加 --force 覆盖）
  insert <笔记路径> --anchor "## 第二章" --content-file f.md
         [--at end-of-section|after-heading] [--dry-run]
                                            在指定标题处插入内容；锚点不唯一、或内容会造成标题
                                            冲突时报错退出（不落盘）
  append <笔记路径> --content-file f.md [--dry-run]
                                            追加到文件末尾
  patch <笔记路径> --edits-file edits.txt [--dry-run]
                                            一次落盘多处改动；任一原文未唯一命中则整批不写
  toc <笔记路径> [--section 目录] [--group-by auto|0|1|2] [--exclude 正则] [--dry-run]
                                            重建目录区。默认沿用现有排版（分组式 / 平表式）；
                                            认不出排版就拒绝重建，不猜
  backup <笔记路径>                          手动备份（内容与上一份相同则跳过）
  backups <笔记路径> [--prune --keep N]      列出备份；--prune 清理旧备份
  restore <笔记路径> [--stamp STAMP] [--dry-run]
                                            从备份恢复
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import filecmp
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKUP_DIRNAME = ".obsidian-note-backups"


# ---------------------------------------------------------------- vault 发现

def obsidian_config_candidates() -> list[Path]:
    """按平台列出 Obsidian 配置目录的候选位置（环境变量可覆盖）。

        Windows   %APPDATA%\\obsidian
        macOS     ~/Library/Application Support/obsidian
        Linux     $XDG_CONFIG_HOME/obsidian（默认 ~/.config/obsidian），兼容 Snap / Flatpak
        覆盖      $OBSIDIAN_CONFIG_DIR（设为包含 obsidian.json 的目录，优先级最高）
    """
    cands: list[Path] = []
    env = os.environ.get("OBSIDIAN_CONFIG_DIR")
    if env:
        cands.append(Path(env).expanduser())
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            cands.append(Path(appdata) / "obsidian")
    elif sys.platform == "darwin":
        cands.append(Path.home() / "Library" / "Application Support" / "obsidian")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        cands.append((Path(xdg) if xdg else Path.home() / ".config") / "obsidian")
        # Snap / Flatpak 里的 Obsidian 把配置放在自身沙箱目录下
        cands.append(Path.home() / "snap" / "obsidian" / "current" / ".config" / "obsidian")
        cands.append(Path.home() / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian")
    return cands


def find_vaults_json() -> Path | None:
    """返回本机实际存在的 obsidian.json；一个都没有时返回 None。"""
    for d in obsidian_config_candidates():
        p = d / "obsidian.json"
        if p.exists():
            return p
    return None


def list_vaults() -> list[dict]:
    vj = find_vaults_json()
    if vj is None:
        return []
    try:
        data = json.loads(vj.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for vid, info in (data.get("vaults") or {}).items():
        p = Path(info.get("path", ""))
        out.append({
            "id": vid,
            "name": p.name,
            "path": str(p),
            "exists": p.exists(),
            "open": bool(info.get("open")),
        })
    out.sort(key=lambda v: (not v["exists"], not v["open"], v["name"]))
    return out


def resolve_vault(token: str) -> Path | None:
    """token 可以是 vault 名、名称片段或完整路径。"""
    p = Path(token).expanduser()
    if p.exists() and p.is_dir():
        return p.resolve()
    cands = [v for v in list_vaults() if v["exists"]]
    exact = [v for v in cands if v["name"] == token]
    if exact:
        return Path(exact[0]["path"])
    fuzzy = [v for v in cands if token in v["name"]]
    if len(fuzzy) == 1:
        return Path(fuzzy[0]["path"])
    if fuzzy:
        names = ", ".join(v["name"] for v in fuzzy)
        print(f"[错误] vault 名 '{token}' 有歧义，匹配到: {names}", file=sys.stderr)
        return None
    return None


# ---------------------------------------------------------------- 笔记读写

def read_note(path: Path) -> str:
    """读 vault 里的笔记。utf-8-sig 兼容带 BOM 的文件。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"笔记不是 UTF-8 编码: {path}") from e


def read_text_any(path) -> str:
    """读外部文本（整理稿 / body-file），按常见编码依次尝试。

    中文 Windows 下「记事本另存为 ANSI」得到的就是 GBK，直接按 UTF-8 读会抛
    UnicodeDecodeError。这里给所有 content-file / body-file 一个统一入口。
    """
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法识别编码（试过 UTF-8 / GBK / UTF-16）: {path}")


def write_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def backup(path: Path, vault: Path) -> tuple[Path | None, str]:
    """把笔记备份到 <vault>/.obsidian-note-backups/<时间戳>/<相对路径>。

    返回 `(备份文件路径或 None, 说明)`，说明取值：

      "ok"      已存一份新备份
      "same"    内容与**最近一份**备份完全相同 —— 跳过，不重复堆快照
      "absent"  源文件不存在

    去重是必要的：同一秒连写几次、或写入没实际改动内容时，
    旧实现会原地堆一串一模一样的快照；vault 在同步盘里时还会被重复上传。
    """
    if not path.exists():
        return (None, "absent")
    try:
        rel = path.relative_to(vault)
    except ValueError:
        rel = Path(path.name)

    root = vault / BACKUP_DIRNAME
    if root.exists():
        for d in sorted((x for x in root.iterdir() if x.is_dir()), reverse=True):
            prev = d / rel
            if prev.exists():
                if filecmp.cmp(prev, path, shallow=False):
                    return (None, "same")
                break        # 只跟最近一份比，够了

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    # 同一秒内多次写入不能互相覆盖：目录名被占用时追加 -02 / -03 …
    # 零填充是必要的：字符串序里 "-10" < "-2"，不补零的话同一秒备份超过 9 份时
    # restore 的「倒序取最新」会取错。
    base_dir = vault / BACKUP_DIRNAME / stamp
    dest_dir, n = base_dir, 2
    while dest_dir.exists():
        dest_dir = vault / BACKUP_DIRNAME / f"{stamp}-{n:02d}"
        n += 1
    dest = dest_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return (dest, "ok")


def which_vault_for(path: Path) -> Path:
    """推断笔记属于哪个 vault（用于放备份目录）。"""
    for v in list_vaults():
        if not v["exists"]:
            continue
        vp = Path(v["path"])
        try:
            path.relative_to(vp)
            return vp
        except ValueError:
            continue
    return path.parent


# ---------------------------------------------------------------- 标题解析

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def parse_headings(text: str) -> list[dict]:
    """解析标题，跳过代码块内的 # 。返回 [{line, level, title, raw}]。line 从 0 开始。"""
    heads: list[dict] = []
    in_code = False
    for i, line in enumerate(text.splitlines()):
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING.match(line)
        if m:
            title = re.sub(r"\s*#+\s*$", "", m.group(2)).strip()
            heads.append({"line": i, "level": len(m.group(1)), "title": title, "raw": line})
    return heads


def frontmatter_span(text: str) -> tuple[int, int]:
    """返回 frontmatter 占用的行区间 [start, end)，无则 (0, 0)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (0, 0)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return (0, i + 1)
    return (0, 0)


def section_bounds(text: str, target: str, mode: str = "end-of-section") -> tuple[int, int] | None:
    """定位标题所在章节的行区间。返回 (插入行号, 章节结束行号)。"""
    heads = parse_headings(text)
    lines = text.splitlines()
    if not heads:
        return None

    idx = None
    for i, h in enumerate(heads):
        if h["raw"].strip() == target.strip() or h["title"] == target.strip():
            idx = i
            break
    if idx is None:  # 退化为子串匹配
        for i, h in enumerate(heads):
            if target.strip() and target.strip() in h["title"]:
                idx = i
                break
    if idx is None:
        return None

    cur = heads[idx]
    if mode == "after-heading":
        return (cur["line"] + 1, cur["line"] + 1)

    end = len(lines)
    for h in heads[idx + 1:]:
        if h["level"] <= cur["level"]:
            end = h["line"]
            break
    return (cur["line"] + 1, end)


def insert_text(text: str, at: int, content: str) -> str:
    lines = text.splitlines()
    content = content.strip("\n")
    block = content.splitlines()
    # 保证与上下文有空行分隔
    before = at > 0 and lines[at - 1].strip() != ""
    pad_before = [""] if before else []
    after_needed = at < len(lines) and lines[at].strip() != ""
    pad_after = [""] if after_needed else []
    new = lines[:at] + pad_before + block + pad_after + lines[at:]
    return "\n".join(new).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 目录重建

# 目录区里的分组标题统一写成 '### <章名>'（note-format.md 第四节）。
TOC_GROUP_PREFIX = "### "


def find_toc_block(text: str, section: str = "目录") -> tuple[int, int] | None:
    """定位 '## 目录' 区块，返回 (标题行号, 区块结束行号)，行号从 0 开始。"""
    heads = parse_headings(text)
    idx = next((i for i, h in enumerate(heads) if h["title"] == section), None)
    if idx is None:
        return None
    cur, end = heads[idx], len(text.splitlines())
    for h in heads[idx + 1:]:
        if h["level"] <= cur["level"]:
            end = h["line"]
            break
    return (cur["line"], end)


def detect_toc_style(text: str, section: str = "目录") -> tuple[str, int | None]:
    """识别已有目录的排版风格，好在重建时照原样来。

    返回 (style, group_level)：

      ("absent",  None)  还没有目录区 —— 可以按规范新建
      ("grouped", 1|2)   分组式：'### 第 N 章 …' 作组标题，条目跨组连续编号
      ("flat",    None)  平表式：H2 主项 'N.'、H3 子项 'N.M'
      ("unknown", None)  认不出的排版 —— **不重建**，宁可留给用户手工维护
    """
    span = find_toc_block(text, section)
    if span is None:
        return ("absent", None)
    block = text.splitlines()[span[0] + 1:span[1]]

    # 组标题在目录区里本身就是 '#' 开头的行；要拿它的文字回正文里查真实层级
    # （注意排除目录区自己那几行，否则查到的是目录里的 '###'，恒为 3）。
    group_titles = []
    for ln in block:
        m = HEADING.match(ln.strip())
        if m:
            group_titles.append(re.sub(r"\s*#+\s*$", "", m.group(2)).strip())
    if group_titles:
        lvl = {h["title"]: h["level"] for h in parse_headings(text) if h["line"] >= span[1]}
        for t in group_titles:
            if t in lvl:
                return ("grouped", lvl[t])
        return ("unknown", None)

    items = [ln for ln in block if "[[" in ln]
    numbered = [ln for ln in items if re.match(r"^\s*\d+(?:\.\d+)*\.?\s*\[\[#", ln)]
    if items and len(numbered) == len(items):
        return ("flat", None)
    return ("unknown", None)


def toc_body_end(lines: list[str], span: tuple[int, int], section_level: int) -> int:
    """目录区里「属于目录」的内容到哪一行为止。

    区块尾部的 `---` 分隔线、空行之后的小节说明都不属于目录，
    重建时要原样留着 —— 老实现按「到下一个标题为止」整段替换，会把 `---` 吃掉。
    """
    last = span[0]
    for i in range(span[0] + 1, span[1]):
        s = lines[i].strip()
        if s == "" or "[[" in s:
            last = i
            continue
        m = HEADING.match(s)
        if m and len(m.group(1)) > section_level:   # 目录里的 '### 组标题'
            last = i
            continue
        break                                        # 遇到 --- 等非目录内容，就此打住
    return last


def build_flat_toc(text: str, levels=(2, 3), exclude: re.Pattern | None = None) -> str:
    """平表式目录：H2 主项 'N.'、H3 子项 'N.M'。

    旧版 `toc` 的产物，库内还有笔记是这个排版，所以保留；新笔记应走分组式。
    """
    heads = parse_headings(text)
    fm_end = frontmatter_span(text)[1]
    items = []
    counters: dict[int, int] = {}
    for h in heads:
        if h["line"] < fm_end or h["level"] not in levels:
            continue
        if h["title"] in ("目录", "Contents", "Table of Contents"):
            continue
        if h["title"].startswith("<!--"):
            continue
        if exclude and exclude.search(h["title"]):
            continue
        counters[h["level"]] = counters.get(h["level"], 0) + 1
        for deeper in [k for k in counters if k > h["level"]]:
            counters.pop(deeper, None)
        num = ".".join(str(counters[l]) for l in sorted(counters) if l <= h["level"])
        indent = "  " * (h["level"] - min(levels))
        suffix = "" if "." in num else "."
        items.append(f"{indent}{num}{suffix} [[#{h['title']}]]")
    return "\n".join(items)


def build_grouped_toc(text: str, group_level: int, section: str = "目录",
                      exclude: re.Pattern | None = None) -> str:
    """分组式目录：'### <章名>' 作组标题 + 跨组连续编号的条目。

    这才是 `references/note-format.md` 第四节的规范格式。
    """
    heads = parse_headings(text)
    span = find_toc_block(text, section)
    if span is not None:
        cut = span[0]
    else:
        # 还没有目录区：跳过笔记标题那一个 H1，别把它当成一个分组
        cut = heads[0]["line"] if heads and heads[0]["level"] == 1 else -1
    hs = [h for h in heads
          if h["line"] > cut and h["title"] != section
          and not h["title"].startswith("<!--")]

    groups: list[list] = []
    cur: list | None = None
    for h in hs:
        if h["level"] < group_level:
            cur = None                    # 比分组更浅的标题（一般是笔记标题），不归入任何分组
        if exclude and exclude.search(h["title"]):
            if h["level"] <= group_level:
                cur = None                # 整组被排除，它的子标题要一并跳过
            continue
        if h["level"] == group_level:
            cur = [h["title"], []]
            groups.append(cur)
        elif h["level"] == group_level + 1 and cur is not None:
            cur[1].append(h["title"])

    out: list[str] = []
    n = 0
    for title, items in groups:
        if not items:      # 没有子条目的分组不出现，免得留下孤立的组标题
            continue
        if out:
            out.append("")
        out.append(TOC_GROUP_PREFIX + title)
        for t in items:
            n += 1
            out.append(f"{n}. [[#{t}]]")
    return "\n".join(out)


def rebuild_toc(text: str, section: str = "目录", group_by: str = "auto",
                exclude: re.Pattern | None = None):
    """重建 '## 目录' 区块。返回 (新文本, 行数, 拒绝原因或 None)。

    默认沿用笔记现有目录的排版；认不出来时**拒绝重建**而不是猜一个，
    因为把分组式目录拉平成平表会静默丢掉组标题和分组结构。
    """
    span = find_toc_block(text, section)
    if span is None:
        return (text, 0, f"笔记里没有 '## {section}' 区块")
    heads = parse_headings(text)
    section_level = next(h["level"] for h in heads if h["title"] == section)

    if group_by == "auto":
        style, lvl = detect_toc_style(text, section)
        if style == "unknown":
            return (text, 0, "目录排版认不出来（既没有 '### 组标题'，条目也不是编号列表）")
        group_level = lvl if style == "grouped" else 0
    else:
        group_level = int(group_by)        # 0 = 平表

    if group_level:
        toc = build_grouped_toc(text, group_level, section, exclude)
    else:
        toc = build_flat_toc(text, exclude=exclude)
    if not toc:
        return (text, 0, "没有可收录的标题")

    lines = text.splitlines()
    body_end = toc_body_end(lines, span, section_level)
    tail = lines[body_end + 1:]
    while tail and not tail[0].strip():     # 目录与后续内容之间只留一个空行
        tail.pop(0)
    new_lines = lines[:span[0] + 1] + [""] + toc.splitlines() + [""] + tail
    return ("\n".join(new_lines).rstrip("\n") + "\n", len(toc.splitlines()), None)


# ---------------------------------------------------------------- find

def note_title(path: Path) -> str:
    try:
        heads = parse_headings(read_note(path))
        if heads and heads[0]["level"] == 1:
            return heads[0]["title"]
    except Exception:
        pass
    return path.stem


def iter_notes(vault: Path):
    for dp, dn, fn in os.walk(vault):
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.lower().endswith((".md", ".markdown")):
                yield Path(dp) / f


def find_notes(vault: Path, query: str, limit: int = 12) -> list[dict]:
    q = query.strip().lower()
    results = []
    for p in iter_notes(vault):
        try:
            text = read_note(p)
        except Exception:
            continue
        rel = p.relative_to(vault).as_posix()
        score, why = 0, []
        if q in p.stem.lower():
            score += 10
            why.append("文件名")
        heads = parse_headings(text)
        if any(q in h["title"].lower() for h in heads):
            score += 8
            why.append("标题")
        fm = text[:600].lower()
        if q in fm and "tags" in fm:
            score += 5
            why.append("标签")
        body_hits = text.lower().count(q)
        if body_hits:
            score += min(body_hits, 6)
            why.append(f"正文x{body_hits}")
        if not score:
            r = difflib.SequenceMatcher(None, q, p.stem.lower()).ratio()
            if r > 0.5:
                score += int(r * 5)
                why.append("近似文件名")
        if score:
            results.append({
                "path": str(p),
                "relative": rel,
                "title": note_title(p),
                "score": score,
                "why": "+".join(why),
                "chars": len(text),
                "headings": len(heads),
            })
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


# ---------------------------------------------------------------- 内容查重

_PUNCT = "，。！？、；：\"''（）【】《》·—… \t\r\n"
_MD_NOISE = re.compile(r"```.*?```|`[^`]*`|!?\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)", re.S)


def strip_md(text: str) -> str:
    """去掉 markdown 标记，只留可比较的文字。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    # 提取稿头部带有 <!-- 来源: 文件名 --> 这类注释，会把 bilibili、720P
    # 之类的噪音词送进关键词，必须先去干净
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    # 去掉 LaTeX 公式，否则 bmatrix / begin / end 会被当成术语关键词
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"\$[^$\n]*\$", " ", text)
    text = _MD_NOISE.sub(" ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    return text


def _shingles(text: str, n: int = 4) -> set[str]:
    """字符 n-gram 集合，对中文比词袋更稳。"""
    t = re.sub("[" + re.escape(_PUNCT) + "]", "", text)
    t = re.sub(r"[#>*|_\-\[\]()`]", "", t)
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _sim(a: str, b: str) -> float:
    A, B = _shingles(a), _shingles(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _units(text: str, min_len: int = 20, win: int = 70, step: int = 35) -> list[str]:
    """切成定长比较单元。

    超长段落要滑窗切开——字幕稿重建成段后常是上千字一坨，
    直接跟笔记里的短句比相似度会假性偏低。
    """
    parts = re.split(r"(?<=[。！？；])|\n{2,}", text)
    out = []
    for p in parts:
        p = re.sub(r"\s+", "", p)
        if len(p) < min_len:
            continue
        if len(p) <= win * 1.5:
            out.append(p)
        else:
            for i in range(0, len(p) - win + 1, step):
                out.append(p[i:i + win])
            out.append(p[-win:])
    return out


def keywords(text: str, topk: int = 40) -> list[str]:
    """抽取术语关键词。优先 jieba TF-IDF，缺失时退化为 n-gram 频次。"""
    text = strip_md(text)
    try:
        import logging
        import jieba
        import jieba.analyse
        jieba.setLogLevel(logging.ERROR)   # 屏蔽 "Building prefix dict" 之类的日志
        kws = jieba.analyse.extract_tags(text, topK=topk * 2)
    except ImportError:
        # 降级要出声——否则用户不知道自己用的是次优路径
        print("[提示] 未安装 jieba，查重退化为 n-gram 频次（关键词质量下降）。"
              " 安装： %s -m pip install jieba" % Path(sys.executable).name, file=sys.stderr)
        kws = _fallback_keywords(text, topk * 2)
    except Exception as e:
        print(f"[警告] jieba 分词失败（{e}），退化为 n-gram 频次", file=sys.stderr)
        kws = _fallback_keywords(text, topk * 2)
    seen, out = set(), []
    for k in kws:
        k = k.strip()
        if not _valid_kw(k) or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out[:topk]


_STOP = set((
    "我们 你们 他们 这个 那个 可以 就是 什么 这样 那样 所以 但是 因为 "
    "如果 而且 并且 以及 然后 现在 这里 里面 一个 一些 非常 重要 时候 "
    "需要 进行 使用 因此 或者 不是 没有 这些 那些 可能 已经 就是 那么 "
    "接下来 下面 上面 上面 其中 这种 各种 同时 由于 关于 对于 通过 经过 "
    "根据 按照 作为 出现 发生 存在 成为 具有 属于 包括 包含 比如 例如 "
    "另外 此外 尤其 特别 十分 比较 几乎 大约 也许 当然 显然 其实 实际上 "
    "总之 最后 首先 其次 这样 那样 是不是 有没有 会觉得 来看 看一下"
).split())


def _valid_kw(k: str) -> bool:
    """过滤数字、页码、视频元数据等噪音关键词。"""
    if len(k) < 2:
        return False
    if re.fullmatch(r"[\d\s.、%\-]+", k):          # 纯数字
        return False
    if re.fullmatch(r"[A-Za-z]{1,2}", k):          # 过短英文
        return False
    if re.fullmatch(r"\d{2,4}[PpKk]", k):          # 720P / 1080P
        return False
    if re.fullmatch(r"(cc|bilibili|subtitle|clippings)", k, re.I):
        return False
    return k not in _STOP


def _fallback_keywords(text: str, topk: int) -> list[str]:
    from collections import Counter
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    cnt: Counter = Counter()
    for n in (2, 3, 4):
        for i in range(len(t) - n + 1):
            g = t[i:i + n]
            if g in _STOP:
                continue
            cnt[g] += 1
    return [w for w, c in cnt.most_common(topk * 4) if c > 1][:topk]


def check_duplicate(note_path: Path, content: str, threshold: float = 0.55) -> dict:
    """评估待写入内容与笔记的重合度。

    字幕稿是口语、笔记是改写后的书面语，字面比对天然失灵，
    因此主指标用「关键词覆盖率」：材料的核心术语有多少已经在笔记里出现过。
    """
    result = {
        "note_exists": note_path.exists(),
        "dup_headings": [],
        "kws": [],
        "covered": [],
        "missing": [],
        "weak": [],          # 命中但只出现在标题 / 色块标题里的词（＝弱覆盖）
        "coverage": 0.0,
        "similar": [],
        "units": 0,
        "dup_ratio": 0.0,
    }
    if not note_path.exists():
        return result

    note_raw = read_note(note_path)
    note_clean = strip_md(note_raw)
    content_clean = strip_md(content)

    note_heads = {h["title"] for h in parse_headings(note_raw)}
    for h in parse_headings(content):
        if h["title"] in note_heads:
            result["dup_headings"].append(h["title"])

    kws = keywords(content)
    result["kws"] = kws
    for k in kws:
        (result["covered"] if k in note_clean else result["missing"]).append(k)
    result["coverage"] = len(result["covered"]) / max(1, len(kws))

    # 弱命中：词在笔记里能找到，但**正文里**找不到（只出现在标题行或 callout 首行）。
    # 这类"只提到名字"的命中会让覆盖率虚高（典型：笔记里一条「待学」预告就把术语写全了，
    # 或者术语只当小节标题用），所以单独列出来给人工复核。
    body_lines = [
        l for l in note_raw.splitlines()
        if not HEADING.match(l) and not re.match(r"^\s*>\s*\[!", l)
    ]
    body_clean = strip_md("\n".join(body_lines))
    result["weak"] = [k for k in result["covered"] if k not in body_clean]

    # 二级参考：字面高度重合的片段。
    # 由此得到的 dup_ratio（有近重复对应的内容单元占比）用来区分两件**动作完全相反**的事：
    #   同一份材料的修订版 / 答案版 —— 句子被照抄，比值高 → 逐处替换
    #   同批知识的另一种讲法        —— 换了措辞，比值低 → 融进已有章节
    # 光看关键词覆盖率这两者都是 95%+，但前者要 patch、后者要并入，不能混为一谈。
    note_units = _units(note_clean)
    content_units = _units(content_clean)
    for cu in content_units:
        best, match = 0.0, ""
        for nu in note_units:
            s = _sim(cu, nu)
            if s > best:
                best, match = s, nu
        if best >= threshold:
            result["similar"].append({"ratio": best, "new": cu, "old": match})
    result["units"] = len(content_units)
    result["dup_ratio"] = len(result["similar"]) / max(1, len(content_units))
    return result


def cmd_check(args) -> int:
    note = Path(args.note).expanduser().resolve()
    content = read_text_any(args.content_file)
    r = check_duplicate(note, content, args.threshold)

    if not r["note_exists"]:
        print(f"[新建] {note.name} 还不存在，无重复风险，可以直接写")
        return 0

    print(f"查重：{note.name}")
    print(f"  待写内容提取关键词 {len(r['kws'])} 个")

    if r["dup_headings"]:
        print(f"\n  ⚠ 标题已存在 {len(r['dup_headings'])} 个（会造成锚点冲突）:")
        for t in r["dup_headings"]:
            print(f"      - {t}")

    if r["missing"]:
        print(f"\n  ★ 笔记里尚未出现的术语 {len(r['missing'])} 个 —— 这些才值得补:")
        print("      " + "、".join(r["missing"][:24]))
    else:
        print("\n  ★ 材料的关键词在笔记里已全部出现过")

    if r["weak"]:
        print(f"\n  ⚠ 弱命中 {len(r['weak'])} 个（只在标题 / 色块标题里出现过，正文里没有）:")
        print("      " + "、".join(r["weak"][:20]))
        print("      → 这类「只提到名字」不算实质覆盖；若它们正是本次材料的重点，照常写入。")

    if r["similar"]:
        print(f"\n  字面高度重合片段 {len(r['similar'])} 处（节选）:")
        for s in sorted(r["similar"], key=lambda x: -x["ratio"])[:5]:
            print(f"      [{s['ratio']*100:.0f}%] {s['new'][:52]}")

    print(f"\n  关键词覆盖率：{r['coverage']*100:.0f}%"
          f"（{len(r['covered'])}/{len(r['kws'])} 个术语已在笔记里）")
    print(f"  句子级近重复：{r['dup_ratio']*100:.0f}%"
          f"（{len(r['similar'])}/{r['units']} 个比较单元能在笔记里找到近重复）")

    if r["coverage"] >= 0.8:
        if r["dup_ratio"] >= 0.6:
            print("\n  → 判定【版本修订 / 答案版】：覆盖面高，且原句被照抄")
            print("     这是一份材料的**新版**（答案版、修订版、补充版），不是重复整理。")
            print("\n  动作：别整篇重写、也别丢；逐处替换变了的那些地方。")
            print("    ① 两份提取文本 diff 一遍定位改动（PDF 用 pdfdiff.py 逐页比）")
            print("    ② 把每条改动写成 edits.tsv，一次落盘：")
            print(f'       $PY $S/vaultio.py patch "{note}" --edits-file edits.tsv')
            print("    ③ 官方答案到手时，回头核对上一轮自算的答案，"
                  "并**告诉用户哪几题原本算错了**")
            print("     若逐处比对下来没有任何差异，说明这份材料已经在笔记里了，什么都不用做。")
            return 13
        print("  → 判定：内容基本已被现有笔记覆盖，建议【不写】，只挑缺失术语补充")
        print("     （覆盖率虽高，但句子对不上 = 换了措辞的另一种讲法，不是新版）")
        print("\n  ⚠ 这个判定只基于关键词重合，三种情况下会虚高：")
        print("      ① 笔记里有「待学 / 下次课内容」这类占位预告——术语被写进预告了，正文却没有")
        print("      ② 同一批知识点的两种讲述（课件 vs 课堂录音）——术语必然全重合")
        print("      ③ 术语只被当成小节标题用过")
        print("     复核办法：挑 3~5 个只有口述 / 新版本才会出现的说法，逐一到笔记里搜——")
        print(f'       grep -n "说法1\\|说法2\\|说法3" "{note}"')
        print("     全为 0 → 确实有增量，正常写入（融入对应知识点，别单列小节）。")
        return 10
    if r["coverage"] >= 0.5:
        print("  → 判定：部分重叠，建议【只补 missing 里的新内容】")
        return 11
    print("  → 判定：基本是新内容，可以写入")
    return 0


# ---------------------------------------------------------------- CLI

def cmd_vaults(args) -> int:
    vs = list_vaults()
    if not vs:
        print("[错误] 没读到 obsidian.json，请确认 Obsidian 已安装并至少打开过一个库。")
        print("       已尝试以下位置：")
        for d in obsidian_config_candidates():
            print(f"         - {d / 'obsidian.json'}")
        print("       都不对时，用环境变量指定配置目录："
              "export OBSIDIAN_CONFIG_DIR=\"<包含 obsidian.json 的目录>\"")
        return 1
    print("本机 Obsidian 库：")
    for v in vs:
        flag = "当前打开" if v["open"] else ""
        state = "" if v["exists"] else "  [路径不存在!]"
        print(f"  - {v['name']:<12} {v['path']}{'  ← ' + flag if flag else ''}{state}")
    return 0


def cmd_find(args) -> int:
    vault = resolve_vault(args.vault)
    if not vault:
        return 1
    hits = find_notes(vault, args.query, args.limit)
    if not hits:
        print(f"[无结果] 在 {vault.name} 里没找到与 '{args.query}' 相关的笔记")
        return 1
    print(f"在「{vault.name}」中匹配 '{args.query}'：")
    for h in hits:
        print(f"  [{h['score']:>3}] {h['relative']}  ({h['chars']}字, {h['headings']}个标题)  ← {h['why']}")
        print(f"        {h['path']}")
    return 0


def cmd_outline(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 不存在: {p}", file=sys.stderr)
        return 1
    text = read_note(p)
    heads = parse_headings(text)
    lines = text.splitlines()
    print(f"# {p}")
    print(f"总行数 {len(lines)}, 字符 {len(text)}")
    fm = frontmatter_span(text)
    if fm[1]:
        print(f"\n--- frontmatter (行1-{fm[1]}) ---")
        print("\n".join(lines[:fm[1]]))
    print(f"\n--- 标题树 ---")
    for h in heads:
        indent = "  " * (h["level"] - 1)
        print(f"  行{h['line']+1:>5}  {indent}{'#'*h['level']} {h['title']}")
    return 0


def cmd_read(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 不存在: {p}", file=sys.stderr)
        return 1
    text = read_note(p)
    if args.head:
        print("\n".join(text.splitlines()[:args.head]))
    else:
        print(text)
    return 0


def cmd_new(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if p.exists() and not args.force:
        print(f"[拒绝] 笔记已存在，不覆盖: {p}")
        print("       如需追加内容请用 insert / append")
        return 1
    if not p.parent.exists():
        vault = which_vault_for(p)
        print(f"[提示] 目录不存在，将在 {vault.name} 内创建: {p.parent}")
    if args.dry_run:
        print(f"[试运行] 会新建 {p}（未写入；已存在时会被 --force 覆盖）")
        return 0
    vault = which_vault_for(p)
    if p.exists():
        backup(p, vault)
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    fm = ["---", f"title: {args.title or p.stem}", f"date: {dt.date.today().isoformat()}"]
    if tags:
        fm.append("tags:")
        fm += [f"  - {t}" for t in tags]
    if args.source:
        fm.append(f"source: {args.source}")
    fm.append("---")
    body = "\n".join(fm) + f"\n\n# {args.title or p.stem}\n"
    if args.body_file:
        body += "\n" + read_text_any(args.body_file).strip() + "\n"
    write_note(p, body)
    print(f"[完成] 已新建 {p}")
    return 0


def _apply_change(p: Path, new_text: str, what: str, do_diff: bool, dry_run: bool = False) -> int:
    vault = which_vault_for(p)
    old_text = read_note(p) if p.exists() else ""
    if dry_run:
        print(f"[试运行] {what} → {p}（未写入、无备份）")
        if old_text:
            d = list(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile="改前", tofile="改后", lineterm="", n=2))
            if d:
                print("\n--- 变更预览 ---")
                print("\n".join(d[:120]))
        return 0
    b, why = backup(p, vault)
    if b:
        print(f"[备份] {b}")
    elif why == "same":
        print("[备份] 内容与上一份备份相同，未新增")
    write_note(p, new_text)
    print(f"[完成] {what} → {p}")
    if do_diff and old_text:
        d = list(difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile="改前", tofile="改后", lineterm="", n=2))
        if d:
            print("\n--- 变更预览 ---")
            print("\n".join(d[:120]))
    return 0


def cmd_insert(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 笔记不存在: {p}", file=sys.stderr)
        return 1
    content = read_text_any(args.content_file)
    if not content.strip():
        print("[错误] 插入内容为空", file=sys.stderr)
        return 1
    text = read_note(p)
    # 锚点唯一性：与 replace 保持一致，别静默插到「第一个」同名标题下面去
    anchor_hits = [l for l in text.splitlines() if l.strip() == args.anchor.strip()]
    if len(anchor_hits) > 1:
        print(f"[错误] 锚点行不唯一（{len(anchor_hits)} 处），请给出更完整的行内容: {args.anchor}",
              file=sys.stderr)
        print("       笔记未改动。先修掉重名标题，或换一个唯一的锚点。", file=sys.stderr)
        return 1
    bounds = section_bounds(text, args.anchor, args.at)
    if not bounds:
        print(f"[错误] 在笔记里找不到锚点标题: {args.anchor}", file=sys.stderr)
        print("\n可用的锚点：")
        for h in parse_headings(text):
            if h["level"] <= 3:
                print(f"  {h['raw']}")
        return 1
    at, end = bounds
    # 重复标题检测：避免制造重名锚点
    new_heads = [h["title"] for h in parse_headings(content)]
    exist = {h["title"] for h in parse_headings(text)}
    dup = [t for t in new_heads if t in exist]
    if dup and not args.allow_duplicate:
        print("[错误] 插入内容含有笔记里已存在的标题，会造成锚点冲突：", file=sys.stderr)
        for t in dup:
            print(f"   - {t}", file=sys.stderr)
        print("       笔记未改动。确认无误请加 --allow-duplicate；"
              "或把内容合并进已有章节。", file=sys.stderr)
        return 1

    if args.at == "end-of-section":
        # 退回到章节内最后一个非空行之后，避免插在章节中间
        lines = text.splitlines()
        while end > at and not lines[end - 1].strip():
            end -= 1
        pos, what = end, f"已追加到「{args.anchor}」章节末尾（{len(content)} 字符）"
    else:
        pos, what = at, f"已插入「{args.anchor}」标题之后（{len(content)} 字符）"

    new_text = insert_text(text, pos, content)
    return _apply_change(p, new_text, what, args.diff, args.dry_run)


def cmd_replace(args) -> int:
    """替换笔记里的一个内容块：起始行 → 结束标记（或下一个同级标题之前）。

    用于把「待学」之类的占位块换成正式内容，避免手工写文件时改坏行尾符。
    """
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 笔记不存在: {p}", file=sys.stderr)
        return 1
    content = read_text_any(args.content_file)
    mid = content.strip("\n").splitlines()
    if not mid:
        print("[错误] 替换内容为空", file=sys.stderr)
        return 1

    text = read_note(p)
    lines = text.splitlines()

    # 1) 起始行：整行精确匹配
    if args.anchor:
        hits = [i for i, l in enumerate(lines) if l.strip() == args.anchor.strip()]
        if not hits:
            print(f"[错误] 找不到起始行: {args.anchor}", file=sys.stderr)
            return 1
        if len(hits) > 1:
            print(f"[错误] 起始行不唯一（{len(hits)} 处），请给出更完整的行内容:", file=sys.stderr)
            for i in hits:
                print(f"   第 {i + 1} 行: {lines[i]}")
            return 1
        start = hits[0]
    else:
        start = 0

    # 2) 结束行（含该行）
    if args.until:
        tail = [i for i in range(start, len(lines)) if args.until.strip() in lines[i]]
        if not tail:
            print(f"[错误] 找不到结束标记: {args.until}", file=sys.stderr)
            return 1
        end = tail[0]
    else:
        m = HEADING.match(lines[start])
        lvl = len(m.group(1)) if m else 6
        end = len(lines) - 1
        for i in range(start + 1, len(lines)):
            mm = HEADING.match(lines[i])
            if mm and len(mm.group(1)) <= lvl:
                end = i - 1
                break
        while end > start and not lines[end].strip():
            end -= 1

    prefix, suffix = lines[:start], lines[end + 1:]
    while suffix and not suffix[0].strip():
        suffix.pop(0)
    # 替换块插在引用块（callout）**内部**时绝不能补空行 —— 空行会把 callout 截成两半。
    # 判据：前缀末行与后缀首行都以 ">" 开头，说明前后仍在同一个引用块里。
    in_quote = (bool(prefix) and prefix[-1].lstrip().startswith(">")
                and bool(suffix) and suffix[0].lstrip().startswith(">"))
    if suffix and not in_quote:
        new_lines = prefix + mid + [""] + suffix
    else:
        new_lines = prefix + mid + suffix
    new_text = "\n".join(new_lines) + "\n"

    if in_quote:
        print("[提示] 替换发生在引用块内部，已省略分隔空行以免截断 callout")

    what = f"已替换第 {start + 1}–{end + 1} 行（{end - start + 1} 行 → {len(mid)} 行）"
    if args.dry_run:
        old = "\n".join(lines[start:end + 1])
        d = list(difflib.unified_diff(
            old.splitlines(), mid, fromfile="改前", tofile="改后", lineterm="", n=2))
        print(f"[试运行] {what}，未写入。变更预览：")
        print("\n".join(d[:160]) if d else "  （内容相同，无变化）")
        return 0
    rc = _apply_change(p, new_text, what, args.diff)
    # 兜底自检：万一还有别处把 callout 截断了，当场报出来
    nw = new_text.splitlines()
    brk = [i + 1 for i, l in enumerate(nw)
           if l.strip() == "" and 0 < i < len(nw) - 1
           and nw[i - 1].startswith(">") and nw[i + 1].startswith(">")
           and not nw[i + 1].startswith("> [!")]
    if brk:
        print(f"[警告] 替换后有 {len(brk)} 处 callout 被空行截断，行号: {brk[:10]}", file=sys.stderr)
    return rc


def cmd_append(args) -> int:
    p = Path(args.note).expanduser().resolve()
    content = read_text_any(args.content_file)
    old = read_note(p) if p.exists() else ""
    new_text = (old.rstrip("\n") + "\n\n" + content.strip("\n") + "\n") if old else content
    return _apply_change(p, new_text, f"已追加 {len(content)} 字符", args.diff, args.dry_run)


# ---------------------------------------------------------------- 批量改动

PATCH_OPEN, PATCH_SEP, PATCH_CLOSE = "<<<<<<< OLD", "=======", ">>>>>>> NEW"


def parse_edits(text: str) -> list[tuple[str, str]]:
    """解析批量编辑文件。

    每处改动写成一节，用 git 冲突标记那套分隔符隔开 ——
    多行内容不用转义，手写和阅读都最省事；也不必先想好往哪一行插：

        <<<<<<< OLD
        （笔记里现有的原文，必须唯一命中）
        =======
        （换成什么；想「追加」就把它连同原文一起写进来）
        >>>>>>> NEW
    """
    lines = text.splitlines()
    edits: list[tuple[str, str]] = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        if lines[i].strip() != PATCH_OPEN:
            raise ValueError(f"第 {i + 1} 行应为 '{PATCH_OPEN}'，实际是 {lines[i][:40]!r}")
        i += 1
        old: list[str] = []
        while i < n and lines[i].strip() != PATCH_SEP:
            old.append(lines[i])
            i += 1
        if i >= n:
            raise ValueError(f"第 {len(edits) + 1} 处改动缺少 '{PATCH_SEP}' 分隔行")
        i += 1
        new: list[str] = []
        while i < n and lines[i].strip() != PATCH_CLOSE:
            new.append(lines[i])
            i += 1
        if i >= n:
            raise ValueError(f"第 {len(edits) + 1} 处改动缺少 '{PATCH_CLOSE}' 结束行")
        i += 1
        edits.append(("\n".join(old), "\n".join(new)))
    return edits


def cmd_patch(args) -> int:
    """一次落盘多处改动：任一原文没唯一命中就整批不写。

    取代「手写 Python 逐条 s.replace」——那条路没有自动备份，
    还容易被 s.count(old) 的子串误命中坑到。
    """
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 笔记不存在: {p}", file=sys.stderr)
        return 1
    try:
        edits = parse_edits(read_text_any(args.edits_file))
    except ValueError as e:
        print(f"[错误] 编辑文件格式有问题：{e}", file=sys.stderr)
        return 1
    if not edits:
        print("[错误] 编辑文件里一处改动都没有", file=sys.stderr)
        return 1

    text = read_note(p)
    # 1) 全量校验：任一条不合格就整批不落盘（改一半最难收拾）
    bad = [(i, o, text.count(o)) for i, (o, _) in enumerate(edits, 1) if text.count(o) != 1]
    if bad:
        print(f"[错误] {len(bad)} 处原文没有唯一命中，整批未落盘：", file=sys.stderr)
        for i, o, c in bad:
            head = o.splitlines()[0][:56] if o.splitlines() else "(空)"
            print(f"   第 {i} 处：命中 {c} 次   {head!r}", file=sys.stderr)
        print("       原文要跟笔记里逐字符一致（缩进、全角括号、空行都算）。", file=sys.stderr)
        return 1

    # 2) 按位置从后往前替换，避免前一处改动影响后一处的定位
    spots = sorted((text.index(o), i, o, nw) for i, (o, nw) in enumerate(edits))
    prev_end = -1
    for start, i, o, _ in spots:
        if start < prev_end:
            print(f"[错误] 第 {i + 1} 处与上一处改动的区域重叠，整批未落盘", file=sys.stderr)
            return 1
        prev_end = start + len(o)
    new_text = text
    for start, _, o, nw in reversed(spots):
        new_text = new_text[:start] + nw + new_text[start + len(o):]

    return _apply_change(p, new_text, f"批量应用 {len(edits)} 处改动", True, args.dry_run)


def cmd_toc(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print(f"[错误] 笔记不存在: {p}", file=sys.stderr)
        return 1
    text = read_note(p)
    exclude = re.compile(args.exclude) if args.exclude else None
    new_text, n, why = rebuild_toc(text, args.section, args.group_by, exclude)
    if why:
        print(f"[拒绝重建] {why}", file=sys.stderr)
        print("       目录一个字节都没动。要么手工维护，要么明确指定排版后重来：", file=sys.stderr)
        print("         --group-by 0   平表：H2 主项 'N.'、H3 子项 'N.M'", file=sys.stderr)
        print("         --group-by 1   按 H1 分组：'### 章名' + 跨组连续编号", file=sys.stderr)
        print("         --group-by 2   按 H2 分组：'### 节名' + 跨组连续编号", file=sys.stderr)
        return 1
    if new_text == text:
        print(f"[跳过] 目录已是最新（{n} 行）")
        return 0
    return _apply_change(p, new_text, f"目录已重建（{n} 行）", True, args.dry_run)


def cmd_backup(args) -> int:
    p = Path(args.note).expanduser().resolve()
    if not p.exists():
        print("[跳过] 文件不存在")
        return 0
    vault = which_vault_for(p)
    b, why = backup(p, vault)
    if b:
        print(f"[备份] {b}")
    else:
        print("[跳过] 内容与上一份备份相同，未新增")
    return 0


def cmd_backups(args) -> int:
    p = Path(args.note).expanduser().resolve()
    vault = which_vault_for(p)
    try:
        rel = p.relative_to(vault)
    except ValueError:
        rel = Path(p.name)
    root = vault / BACKUP_DIRNAME
    if not root.exists():
        print("[无] 还没有任何备份")
        return 0
    found = sorted([d for d in root.iterdir() if d.is_dir()], reverse=True)
    mine = [d for d in found if (d / rel).exists()]
    print(f"「{rel}」的备份：")
    for d in mine:
        f = d / rel
        print(f"  {d.name}  ({f.stat().st_size} bytes)  {f}")

    if not args.prune:
        return 0

    # --prune：只保留最近 --keep 份。备份目录在 vault 里，vault 又常在同步盘上，
    # 不清理会一直堆（每次写入一份）并被反复上传。
    stale = mine[args.keep:]
    if not stale:
        print(f"\n[prune] 共 {len(mine)} 份，未超过保留数 {args.keep}，无需清理")
        return 0
    extra = [d for d in found if d not in mine]      # 目录里其它笔记的备份，不动
    print(f"\n[prune] 保留最近 {args.keep} 份，将删除 {len(stale)} 份"
          f"（其余 {len(extra) + len(mine) - len(stale)} 份目录未触碰）：")
    for d in stale:
        print(f"   - {d.name}")
    if args.dry_run:
        print("[prune] 试运行，未删除")
        return 0
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
    print(f"[prune] 已删除 {len(stale)} 份")
    return 0


def cmd_restore(args) -> int:
    p = Path(args.note).expanduser().resolve()
    vault = which_vault_for(p)
    try:
        rel = p.relative_to(vault)
    except ValueError:
        rel = Path(p.name)
    root = vault / BACKUP_DIRNAME
    if not root.exists():
        print("[错误] 没有备份目录")
        return 1
    stamps = sorted([d for d in root.iterdir() if d.is_dir()], reverse=True)
    stamps = [d for d in stamps if (d / rel).exists()]
    if not stamps:
        print("[错误] 该笔记没有可用备份")
        return 1
    pick = None
    if args.stamp:
        pick = next((d for d in stamps if d.name == args.stamp), None)
    else:
        pick = stamps[0]
    if not pick:
        print(f"[错误] 找不到备份 {args.stamp}；可用: {[d.name for d in stamps]}")
        return 1
    if args.dry_run:
        print(f"[试运行] 会用备份 {pick.name} 覆盖 {p}（未写入）")
        print(f"      备份文件: {pick / rel}")
        return 0
    if p.exists():
        backup(p, vault)  # 回滚前先存当前版本
    shutil.copy2(pick / rel, p)
    print(f"[完成] 已从 {pick.name} 恢复 {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Obsidian vault 安全读写")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("vaults", help="列出所有 vault")
    p.set_defaults(func=cmd_vaults)

    p = sub.add_parser("find", help="查找候选笔记")
    p.add_argument("vault")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("outline", help="打印标题树")
    p.add_argument("note")
    p.set_defaults(func=cmd_outline)

    p = sub.add_parser("read", help="读笔记")
    p.add_argument("note")
    p.add_argument("--head", type=int)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("new", help="新建笔记")
    p.add_argument("note")
    p.add_argument("--title")
    p.add_argument("--tags", help="逗号分隔")
    p.add_argument("--source")
    p.add_argument("--body-file")
    p.add_argument("--force", action="store_true",
                   help="笔记已存在时覆盖它（默认拒绝，不覆盖）")
    p.add_argument("--dry-run", action="store_true", help="只说明会建在哪，不写入")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("check", help="写入前查重：比对待写内容与笔记已有内容")
    p.add_argument("note")
    p.add_argument("--content-file", required=True)
    p.add_argument("--threshold", type=float, default=0.55, help="相似度阈值")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("insert", help="在标题处插入内容")
    p.add_argument("note")
    p.add_argument("--anchor", required=True, help='如 "### 某某小节标题"')
    p.add_argument("--content-file", required=True)
    p.add_argument("--at", choices=["end-of-section", "after-heading"], default="end-of-section")
    p.add_argument("--allow-duplicate", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="只打印变更预览，不写入")
    p.add_argument("--diff", action="store_true", default=True)
    p.add_argument("--no-diff", dest="diff", action="store_false", help="不打印变更预览")
    p.set_defaults(func=cmd_insert)

    p = sub.add_parser("append", help="追加到末尾")
    p.add_argument("note")
    p.add_argument("--content-file", required=True)
    p.add_argument("--dry-run", action="store_true", help="只打印变更预览，不写入")
    p.add_argument("--diff", action="store_true", default=True)
    p.add_argument("--no-diff", dest="diff", action="store_false", help="不打印变更预览")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("patch", help="一次落盘多处改动（任一原文不唯一命中则整批不写）")
    p.add_argument("note")
    p.add_argument("--edits-file", required=True,
                   help="改动清单；每处用 '<<<<<<< OLD / ======= / >>>>>>> NEW' 三行隔开")
    p.add_argument("--dry-run", action="store_true", help="只打印变更预览，不写入")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("replace", help="替换一个内容块")
    p.add_argument("note")
    p.add_argument("--anchor", help="起始行（整行精确匹配）；省略则从文件开头起")
    p.add_argument("--until", help="结束标记（子串匹配，该行一并替换）；省略则到下一个同级/更高级标题前")
    p.add_argument("--content-file", required=True)
    p.add_argument("--dry-run", action="store_true", help="只打印变更预览，不写入")
    p.add_argument("--diff", action="store_true", default=True)
    p.set_defaults(func=cmd_replace)

    p = sub.add_parser("toc", help="重建目录区（默认沿用现有排版；认不出就拒绝）")
    p.add_argument("note")
    p.add_argument("--section", default="目录")
    p.add_argument("--group-by", choices=["auto", "0", "1", "2"], default="auto",
                   help="auto=沿用现有排版；0=平表；1=按 H1 分组；2=按 H2 分组")
    p.add_argument("--exclude", help="标题正则，命中的分组/条目不进目录（如 '参考资料|相关链接'）")
    p.add_argument("--dry-run", action="store_true", help="只打印变更预览，不写入")
    p.set_defaults(func=cmd_toc)

    p = sub.add_parser("backup", help="手动备份")
    p.add_argument("note")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("backups", help="列出备份")
    p.add_argument("note")
    p.add_argument("--prune", action="store_true", help="只保留最近 N 份（默认 10），其余删除")
    p.add_argument("--keep", type=int, default=10, help="--prune 时保留的份数（默认 10）")
    p.add_argument("--dry-run", action="store_true", help="--prune 时只列出将删除的目录")
    p.set_defaults(func=cmd_backups)

    p = sub.add_parser("restore", help="从备份恢复")
    p.add_argument("note")
    p.add_argument("--stamp")
    p.add_argument("--dry-run", action="store_true", help="只说明会恢复哪一份，不写入")
    p.set_defaults(func=cmd_restore)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except ValueError as e:
        # 编码不对之类的输入问题：给一行清楚的提示，而不是抛一整段栈
        print(f"[错误] {e}", file=sys.stderr)
        return 2
    except OSError as e:
        # --content-file / --body-file / --edits-file 路径写错、无权限、是个目录……
        # 这些以前会抛出整段 FileNotFoundError 栈，让人以为脚本坏了。统一成一行提示。
        print(f"[错误] 读写文件失败：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
