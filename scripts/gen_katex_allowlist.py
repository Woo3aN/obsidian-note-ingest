"""从 KaTeX 源码抽取支持的宏 / 环境，生成 obsidian_lint 用的白名单。

一次性工具，KaTeX 升级后重跑即可。原理：源码里每个宏都以字符串字面量出现
（`defineMacro("\\xxx")`、`names: ["\\xxx"]`），全抓出来即得白名单。
注意 JS 源码里写的是**双反斜杠**（`"\\equiv"`）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

def find_katex_src() -> str | None:
    """定位 katex 的 src 目录。优先环境变量 KATEX_SRC，其次常见安装位置。"""
    env = os.environ.get("KATEX_SRC")
    if env:
        return env
    home = os.path.expanduser("~")
    candidates = [
        # npm 全局 / 本地
        os.path.join(home, "AppData", "Roaming", "npm", "node_modules", "katex", "src"),
        os.path.join(home, ".npm-global", "lib", "node_modules", "katex", "src"),
        "/usr/local/lib/node_modules/katex/src",
        "/usr/lib/node_modules/katex/src",
        # WorkBuddy 托管 workspace
        os.path.join(home, ".workbuddy", "binaries", "node", "workspace",
                     "node_modules", "katex", "src"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None

# 引号内的 \command：源码里是 \\command（两个反斜杠），也容忍一个
CMD_LITERAL = re.compile(r"""["']\\{1,2}([a-zA-Z@]+)["']""")
NAMES_KEY = re.compile(r"""names:\s*""")
STR_LITERAL = re.compile(r"""["']((?:\\.|[^"'\\])*)["']""")


def scan_names_arrays(text: str):
    """把源码里所有 `names: [...]` 数组里的字符串抠出来（数组可能跨行）。"""
    out: list[str] = []
    for m in NAMES_KEY.finditer(text):
        i = m.end()
        if i >= len(text):
            continue
        if text[i] == "[":                       # 数组形式
            depth, j = 0, i
            while j < len(text):
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out += [s.group(1) for s in STR_LITERAL.finditer(text, i, j)]
        elif text[i] in "\"'":                   # 单字符串形式
            s = STR_LITERAL.match(text, i)
            if s:
                out.append(s.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="输出 JSON 路径（默认打印到 stdout）")
    ap.add_argument("--katex-src", help="katex/src 目录（默认自动查找，或用环境变量 KATEX_SRC）")
    args = ap.parse_args()

    src = args.katex_src or find_katex_src()
    if not src or not os.path.isdir(src):
        print("[错误] 找不到 katex/src。先装 KaTeX，再用 --katex-src 或环境变量 KATEX_SRC 指定：",
              file=sys.stderr)
        print("       npm install katex        # 然后在 node_modules/katex/src", file=sys.stderr)
        return 1

    cmds: set[str] = set()
    envs: set[str] = set()

    for dp, _, files in os.walk(src):
        for f in files:
            if not f.endswith((".ts", ".js")):
                continue
            text = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()

            cmds |= {"\\" + m.group(1) for m in CMD_LITERAL.finditer(text)}

            for name in scan_names_arrays(text):
                if name.startswith("\\"):
                    cmds.add(re.sub(r"^\\+", "\\\\", name))
                elif re.fullmatch(r"[a-zA-Z*]+", name):
                    envs.add(name)

    data = {
        "_source": "katex/src（由 gen_katex_allowlist.py 生成，勿手改）",
        "commands": sorted(cmds),
        "environments": sorted(envs),
    }
    blob = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(blob)
        print(f"写入 {args.out}：宏 {len(cmds)} 个、环境 {len(envs)} 个")
    else:
        sys.stdout.write(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
