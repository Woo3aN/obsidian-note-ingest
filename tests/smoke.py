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
 11. toc 必须保留「按章分组」的目录，不能拉平成平表（曾经会静默毁掉组标题）
 12. toc 认不出目录排版时必须拒绝重建，而不是猜一个
 13. patch 任一原文没唯一命中就整批不写
 14. patch 正常时一次落盘多处改动
 15. check 要能把「版本修订」和「换种讲法」区分开（退出码 13 vs 10）
 16. 备份按内容去重：内容没变时不再堆一份
 17. insert 空内容要被拦下（与 replace 一致），不能假成功还白写备份
 18. pptx_deep 的表格要带头分隔行、并转义单元格里的竖线
 19. renderpages --pages 要能接 extract --stats 打印的方括号列表
 20. extract --as-text 要原样保留纯文本笔记
 21. pdfdiff 能定位到有改动的页
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
    b1, _ = vaultio.backup(note, vault)
    note.write_text("v2\n", encoding="utf-8", newline="\n")
    b2, _ = vaultio.backup(note, vault)
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

    # 11) toc 不得把「按章分组」的目录拉平成平表（曾经的静默数据损失）
    toc_doc = vault / "toc.md"
    toc_doc.write_text(
        "# 某课程\n\n## 目录\n\n"
        "### 第 1 章 甲\n1. [[#1.1 甲一]]\n\n"
        "---\n\n"
        "# 第 1 章 甲\n\n## 1.1 甲一\n\nA\n\n## 1.2 甲二\n\nB\n\n"
        "---\n\n# 第 2 章 乙\n\n## 2.1 乙一\n\nC\n",
        encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "toc", str(toc_doc))
    got = toc_doc.read_text(encoding="utf-8")
    check("toc 保留分组式目录，不拉平成平表",
          r.returncode == 0 and "### 第 2 章 乙" in got and "3. [[#2.1 乙一]]" in got,
          f"exit={r.returncode}")
    check("toc 不吞掉目录后面的 --- 分隔线", "\n---\n\n# 第 1 章 甲" in got)

    # 12) toc 认不出排版时必须拒绝，而不是猜一个
    toc2 = vault / "toc_odd.md"
    odd = "# T\n\n## 目录\n\n- [[#甲]]\n\n## 甲\n\nA\n"
    toc2.write_text(odd, encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "toc", str(toc2))
    check("toc 认不出排版时拒绝重建、一字不动",
          r.returncode == 1 and toc2.read_text(encoding="utf-8") == odd,
          f"exit={r.returncode}")

    # 13) patch 正常：一次落盘多处改动
    pat = vault / "pat.md"
    pat.write_text("# N\n\nalpha\n\nbeta\n\ngamma\n", encoding="utf-8", newline="\n")
    ed_ok = work / "ed_ok.txt"
    ed_ok.write_text(
        "<<<<<<< OLD\nalpha\n=======\nALPHA\n>>>>>>> NEW\n\n"
        "<<<<<<< OLD\nbeta\n=======\nbeta\nplus\n>>>>>>> NEW\n",
        encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "patch", str(pat), "--edits-file", str(ed_ok))
    after = pat.read_text(encoding="utf-8")
    check("patch 一次落盘多处改动",
          r.returncode == 0 and "ALPHA" in after and "beta\nplus" in after,
          f"exit={r.returncode}")

    # 14) patch 有任何一条不合格 → 整批不写（连前面合格的也不落盘）
    ed_bad = work / "ed_bad.txt"
    ed_bad.write_text(
        "<<<<<<< OLD\ngamma\n=======\nGAMMA\n>>>>>>> NEW\n\n"
        "<<<<<<< OLD\n根本不存在的一行\n=======\nY\n>>>>>>> NEW\n",
        encoding="utf-8", newline="\n")
    guard = pat.read_text(encoding="utf-8")
    r = run(str(SCRIPTS / "vaultio.py"), "patch", str(pat), "--edits-file", str(ed_bad))
    check("patch 有原文不唯一命中时整批不写",
          r.returncode == 1 and pat.read_text(encoding="utf-8") == guard and "GAMMA" not in guard,
          f"exit={r.returncode}")

    # 15) check 要把「照抄原句的修订版」和「换种讲法」分开
    s_a = "这是一个关于集合与映射的说明，用来测试查重判定是否工作。"
    s_b = "由定义可知集合的元素满足映射关系，这一步不需要额外证明。"
    body_txt = "\n\n".join([s_a] * 3 + [s_b] * 3)
    ck = vault / "ck.md"
    ck.write_text(f"# 某笔记\n\n## 某节\n\n{body_txt}\n", encoding="utf-8", newline="\n")
    draft = work / "draft.md"
    draft.write_text(body_txt + "\n", encoding="utf-8", newline="\n")
    r = run(str(SCRIPTS / "vaultio.py"), "check", str(ck), "--content-file", str(draft))
    check("check 把「原句照抄的新版本」判为 13（逐处替换，不是 10）",
          r.returncode == 13, f"exit={r.returncode}")

    # 16) 备份按内容去重
    dedup = vault / "dedup.md"
    dedup.write_text("同内容\n", encoding="utf-8", newline="\n")
    _, w1 = vaultio.backup(dedup, vault)
    b2, w2 = vaultio.backup(dedup, vault)
    check("备份内容相同则跳过，不再堆一份快照",
          w1 == "ok" and b2 is None and w2 == "same", f"{w1}/{w2}")

    # 17) insert 空内容必须被拦下
    empty = work / "empty.md"
    empty.write_text("   \n\n", encoding="utf-8", newline="\n")
    guard2 = doc.read_text(encoding="utf-8")
    r = run(str(SCRIPTS / "vaultio.py"), "insert", str(doc),
            "--anchor", "## 第一章", "--content-file", str(empty))
    check("insert 空内容被拦下且不落盘",
          r.returncode == 1 and doc.read_text(encoding="utf-8") == guard2,
          f"exit={r.returncode}")

    # 18) pptx_deep 的表格要能直接贴进笔记
    import re as _re  # noqa: E402

    import pptx_deep  # noqa: E402

    md_rows = pptx_deep._md_table([["表头A|B", "表头C"], ["甲", "1"], ["乙"]])
    md_txt = "\n".join(md_rows)

    def _cells(row: str) -> list[str]:
        return _re.split(r"(?<!\\)\|", row.strip())[1:-1]

    check("pptx 表格有分隔行、转义竖线、补齐列数",
          "| --- | --- |" in md_txt and "\\|" in md_txt
          and all(len(_cells(r)) == 2 for r in md_rows), repr(md_rows))

    # 19) renderpages --pages 要能吃下 extract --stats 打印的方括号列表
    try:
        import renderpages  # noqa: E402
    except SystemExit:
        print("  SKIP  renderpages（未安装 pymupdf）")
    else:
        got = renderpages.parse_pages("[4, 26, 54, 80]", 112)
        check("renderpages --pages 接受方括号列表（可直接粘贴）",
              got == [4, 26, 54, 80], str(got))

    # 20) extract --as-text 要原样保留纯文本笔记
    plain = work / "plain.md"
    plain.write_text("# 我的笔记\n\n复习要点如下：\n\n1:20 例题：求特征值\n\n- 注意矩阵可逆\n",
                     encoding="utf-8", newline="\n")
    out2 = work / "plain.out.md"
    run(str(SCRIPTS / "extract.py"), str(plain), "--as-text", "--out", str(out2))
    txt = out2.read_text(encoding="utf-8")
    check("extract --as-text 原样保留纯文本笔记（不丢时间戳之前的内容）",
          "# 我的笔记" in txt and "复习要点如下" in txt, repr(txt[:50]))

    # 21) pdfdiff 能定位到有改动的页
    try:
        import pymupdf  # noqa: E402
    except ImportError:
        print("  SKIP  pdfdiff（未安装 pymupdf）")
    else:
        def mkpdf(target, texts):
            d = pymupdf.open()
            for t in texts:
                d.new_page().insert_text((72, 100), t, fontsize=14)
            d.save(str(target))
            d.close()

        p1, p2 = work / "v1.pdf", work / "v2.pdf"
        mkpdf(p1, ["Alpha content here.", "Beta content here."])
        mkpdf(p2, ["Alpha content here.", "Beta content CHANGED."])
        r = run(str(SCRIPTS / "pdfdiff.py"), str(p1), str(p2))
        out = _re.sub(r"\s+", "", r.stdout + r.stderr)   # 输出里有补白，先抹平再比
        check("pdfdiff 指认出被改动的那一页",
              "第2页" in out and "第1页" not in out, out[-60:])

    # 22) 内容文件路径写错时给友好提示，而不是抛一整段 traceback
    #     （回归：main() 早期只 catch ValueError，FileNotFoundError 会漏出去）
    #     注意笔记本身要存在——否则命中的是「笔记不存在」(rc=1)，测不到这一条
    note = work / "note.md"
    note.write_text("# T\n\n## 某节\n\n正文\n", encoding="utf-8", newline="\n")
    missing = work / "根本不存在.md"
    for cmd, extra in (("check", ["--content-file"]),
                       ("insert", ["--anchor", "## 某节", "--content-file"]),
                       ("patch", ["--edits-file"])):
        r = run(str(SCRIPTS / "vaultio.py"), cmd, str(note), *extra, str(missing))
        blob = r.stdout + r.stderr
        check(f"{cmd} 遇到不存在的文件给友好错误（非 traceback）",
              r.returncode == 2 and "Traceback" not in blob and "[错误]" in blob,
              f"rc={r.returncode}")

    # 23) LaTeX 检查：KaTeX 不支持的宏/环境要报出来，正常公式与代码块不误报
    #     （回归：笔记里写过 \nsubset，Obsidian 渲染成红色原文才被发现）
    math_note = work / "math.md"
    math_note.write_text(
        "---\ntitle: T\ntags:\n  - x\n---\n\n# T\n\n"
        "$$a \\nsubset b \\qquad \\begin{cases} x \\end{cases}$$\n\n"
        "$$\\begin{nosuchenv} x \\end{nosuchenv}$$\n\n"
        "$$x \\not\\subset y \\qquad \\mathbb{N}\\subseteq\\mathbb{Z}$$\n\n"
        "```\n$fake \\nsubset code$\n```\n",
        encoding="utf-8", newline="\n",
    )
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(math_note))
    out = r.stdout + r.stderr
    check("lint 抓出 KaTeX 不支持的宏 \\nsubset", "\\nsubset" in out and "LaTeX 宏" in out)
    check("lint 抓出 KaTeX 不支持的环境", "nosuchenv" in out and "LaTeX 环境" in out)
    # 只能有这 2 条 LaTeX 告警：合法公式（\not\subset / cases / \mathbb）和代码块里的假公式都不能报
    n_math = out.count("[警告]")           # 该文件除 LaTeX 外没有别的告警
    check("lint 不误报合法公式与代码块内容", n_math == 2, f"LaTeX 告警 {n_math} 条（应为 2）")

    # 24) 强调符号检查：加粗没生效的行要报出来，代码/公式/填空/正常加粗都不能误报
    #     （回归：中文笔记里 `**术语（English）**后面` 会把 ** 原样显示出来）
    emph_note = work / "emph.md"
    emph_note.write_text(
        "---\ntitle: T\ntags:\n  - x\n---\n\n# T\n\n"
        "**工具调用（Tool Calling）**机制升级。\n\n"      # 坏：闭合 ** 前是标点、后紧跟文字
        "一句话是**「推理即计算」**：规范化。\n\n"          # 坏：开启 ** 前是文字、后紧跟标点
        "**正常的加粗**没问题。\n\n"                        # 好
        "`**代码里的**` 不算。\n\n"                         # 好：行内代码
        "填空 ____。\n\n"                                   # 好：填空空位
        "$$A_nA_{n-1}$$\n",                                 # 好：公式里的下标
        encoding="utf-8", newline="\n",
    )
    r = run(str(SCRIPTS / "obsidian_lint.py"), str(emph_note))
    out = r.stdout + r.stderr
    if "markdown_it" in out or "跳过" in out:
        print("  SKIP  强调符号检查（未安装 markdown-it-py）")
    else:
        n_emph = out.count("渲染成了字面量")
        check("lint 抓出没生效的 ** 加粗", n_emph == 2, f"报了 {n_emph} 条（应为 2）")
        check("lint 不误报正常加粗 / 代码 / 填空 / 公式",
              "第 13 行" not in out and "第 15 行" not in out)

    print()
    if failures:
        print(f"✗ {len(failures)} 项失败: {failures}")
        return 1
    print("✓ 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
