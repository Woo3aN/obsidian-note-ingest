#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冒烟测试 —— 全部在临时目录里跑，不碰你的 vault 和原始材料。

用法：
    python tests/smoke.py

覆盖「外部用户第一次用最容易翻车」的几条硬约定：
  1. extract --out 必须写 LF（全仓库硬约定，别的脚本都 assert 过）
  2. 同一秒内两次备份不能互相覆盖（「全程可回滚」的地基）
  3. insert 遇到会造成锚点冲突的内容必须拦截，而不是静默写下去
  4. insert 的锚点不唯一时必须报错（与 replace 行为一致）
  5. --dry-run 必须真的不落盘
  6. 整理稿是 GBK 时不能抛栈
  7. restore 能回滚
  8. SKILL.md 的 frontmatter 是合法 YAML（描述里的半角冒号不加引号会让 skill 加载不了）
  9. lint 不误报表格里的转义竖线 \\|，但对真·列数不一致仍要报警
 10. lint 对非 UTF-8 笔记要明确报错，而不是静默当乱码
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Windows 上 stdout 默认编码可能是 cp1252 / cp936，打印中文会直接 UnicodeEncodeError
# （GitHub 的 windows runner 就是 cp1252）。必须在任何输出之前设置。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
PY = sys.executable

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or not detail else f"   [{detail}]"))
    if not ok:
        failures.append(name)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="obsidian_smoke_"))
    vault = work / "vault"
    vault.mkdir()

    # 1) extract --out 只写 LF
    src = work / "a.txt"
    src.write_text("第一行\n第二行\n", encoding="utf-8", newline="\n")
    out = work / "a.md"
    run(str(SCRIPTS / "extract.py"), str(src), "--out", str(out))
    raw = out.read_bytes()
    crlf = raw.count(b"\r\n")
    check("extract --out 只写 LF（无 CRLF）", crlf == 0, f"CRLF={crlf}")

    # 2) 同一秒内两次备份互不覆盖
    sys.path.insert(0, str(SCRIPTS))
    import vaultio  # noqa: E402

    note = vault / "n.md"
    note.write_text("v1\n", encoding="utf-8", newline="\n")
    b1 = vaultio.backup(note, vault)
    note.write_text("v2\n", encoding="utf-8", newline="\n")
    b2 = vaultio.backup(note, vault)
    kept = sorted((vault / ".obsidian-note-backups").rglob("*.md"))
    check("同秒两次备份互不覆盖", b1 != b2 and len(kept) == 2,
          f"路径相同={b1 == b2} 留存={len(kept)}")

    # 3) insert 拦截锚点冲突
    doc = vault / "d.md"
    base = "## 第一章\n\nA\n\n## 第二章\n\nB\n"
    doc.write_text(base, encoding="utf-8", newline="\n")
    dup = work / "dup.md"
    dup.write_text("## 第二章\n\n重复标题\n", encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "insert", str(doc),
            "--anchor", "## 第一章", "--content-file", str(dup))
    check("insert 拦截重名标题且不落盘",
          r.returncode == 1 and doc.read_text(encoding="utf-8") == base,
          f"exit={r.returncode}")

    # 4) 笔记自身锚点不唯一 → 必须拦
    dupdoc = vault / "e.md"
    dupbase = "## 第二章\n\nA\n\n## 第二章\n\nB\n"
    dupdoc.write_text(dupbase, encoding="utf-8", newline="\n")
    txt = work / "t.md"
    txt.write_text("追加段\n", encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "insert", str(dupdoc),
            "--anchor", "## 第二章", "--content-file", str(txt))
    check("insert 锚点不唯一时报错",
          r.returncode == 1 and dupdoc.read_text(encoding="utf-8") == dupbase,
          f"exit={r.returncode}")

    # 5) --dry-run 不落盘
    doc.write_text(base, encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "insert", str(doc),
            "--anchor", "## 第一章", "--content-file", str(txt), "--dry-run")
    check("insert --dry-run 不落盘",
          r.returncode == 0 and doc.read_text(encoding="utf-8") == base,
          f"exit={r.returncode}")

    # 6) 正常写入 + restore 回滚
    r = run(str(SCRIPTS / "vaultio.py"), "insert", str(doc),
            "--anchor", "## 第一章", "--content-file", str(txt))
    wrote = "追加段" in doc.read_text(encoding="utf-8")
    check("insert 正常写入", r.returncode == 0 and wrote, f"exit={r.returncode}")
    r = run(str(SCRIPTS / "vaultio.py"), "restore", str(doc))
    check("restore 可回滚",
          "追加段" not in doc.read_text(encoding="utf-8"),
          f"exit={r.returncode}")

    # 7) GBK 整理稿不抛栈
    gbk = work / "gbk.md"
    gbk.write_bytes("这是 GBK 编码的中文整理稿。\n".encode("gbk"))
    r = run(str(SCRIPTS / "vaultio.py"), "check", str(doc), "--content-file", str(gbk))
    check("check 接受 GBK 整理稿",
          r.returncode in (0, 10, 11) and "UnicodeDecodeError" not in r.stderr,
          f"exit={r.returncode}")

    # 8) lint 可运行
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(doc))
    check("obsidian_lint 可运行",
          "Traceback" not in r.stderr, f"exit={r.returncode}")

    # 9) SKILL.md frontmatter 必须是合法 YAML
    #    （描述里出现半角 ": " 而不加引号，会让整个 frontmatter 解析失败 → skill 加载不了）
    txt = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        print("  SKIP  SKILL.md frontmatter（未安装 PyYAML）")
    else:
        ok, detail = True, ""
        try:
            fm = txt.split("---", 2)[1]
            data = yaml.safe_load(fm)
            ok = isinstance(data, dict) and "name" in data and "description" in data
            detail = f"keys={list(data) if isinstance(data, dict) else data}"
        except Exception as e:
            ok, detail = False, str(e).splitlines()[0][:60]
        check("SKILL.md frontmatter 是合法 YAML", ok, detail)

    # 10) lint 不把表格里的转义竖线 \| 误判成「列数不一致」
    #     （extract.py 的 _table_to_md 就是这么转义单元格内竖线的）
    t1 = work / "esc.md"
    t1.write_text("# T\n\n| A | B |\n| --- | --- |\n| x\\|y | z |\n",
                  encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(t1))
    check("lint 不误报转义竖线 \\|",
          "列数不一致" not in (r.stdout + r.stderr))

    # 11) 真·列数不一致仍要报出来
    t2 = work / "bad.md"
    t2.write_text("# T\n\n| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n",
                  encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(t2))
    check("lint 仍能发现真列数不一致",
          "列数不一致" in (r.stdout + r.stderr))

    # 12) 非 UTF-8 笔记要明确报错，而不是当乱码静默通过
    t3 = work / "notutf8.md"
    t3.write_bytes("# 标题\n\n正文。\n".encode("gbk"))
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(t3))
    out = r.stdout + r.stderr
    check("lint 报非 UTF-8 且不崩",
          "不是 UTF-8" in out and "Traceback" not in r.stderr)

    print()
    if failures:
        print(f"✗ {len(failures)} 项失败: {failures}")
        return 1
    print("✓ 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
