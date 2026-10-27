# -*- coding: utf-8 -*-
"""05｜本单改动清单（用文件时刻核，不用 git status）。

本工程 git HEAD 长期落后工作树，`git status` 不能当基线（既有约定）。
所以「本单改了哪些文件」用**文件 mtime 与门槛时刻**核，并排除运行中进程持续重写的
采集快照 / 锁 / 日志目录。

输出：out/05_改动清单.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib08 as L  # noqa: E402

# 本单开始时刻（本地）：第一个证据脚本落盘之前
START_LOCAL = "2026-10-27 16:43:00"

EXCLUDE_PARTS = ("/快照/", "/locks/", "/logs/", "/__pycache__/", "/.pytest_cache/")
CODE_SUFFIX = (".py", ".ts", ".tsx", ".sh", ".sql", ".json", ".md", ".html")


def scan(root: Path, since: float) -> list[dict]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        s = str(p)
        if any(x in s for x in EXCLUDE_PARTS):
            continue
        if p.suffix not in CODE_SUFFIX:
            continue
        st = p.stat()
        if st.st_mtime > since:
            out.append({"path": s, "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                          time.localtime(st.st_mtime)),
                        "size": st.st_size})
    return out


def main() -> int:
    since = time.mktime(time.strptime(START_LOCAL, "%Y-%m-%d %H:%M:%S"))
    rep = {"生成时刻": L.now_utc(), "门槛时刻_本地": START_LOCAL,
           "排除目录": list(EXCLUDE_PARTS),
           "口径": "文件 mtime 晚于门槛时刻；git status 不作为基线"}

    prod = {}
    for name, root in (("框架/backend", L.BACKEND),
                       ("策略", L.PROJECT / "策略"),
                       ("UI界面/web/src", L.PROJECT / "UI界面" / "web" / "src")):
        hits = scan(root, since)
        prod[name] = {"changed_n": len(hits), "items": hits[:50]}
    rep["生产代码与配置"] = prod
    rep["生产改动合计"] = sum(v["changed_n"] for v in prod.values())

    own = scan(L.BASE, since)
    rep["本单新增文件"] = {"root": str(L.BASE), "n": len(own),
                     "items": sorted([i["path"] for i in own])}

    rep["结论"] = ("本单未改动任何生产代码、配置、策略包或前端源码；"
                "只在 evidence/ACCUM-EARLY-08-WORK/ 下新增脚本与证据。"
                if rep["生产改动合计"] == 0 else
                f"检测到 {rep['生产改动合计']} 个生产文件在本单期间被改动，需逐个人工确认归属")

    p = L.dump("05_改动清单.json", rep)
    print("== 05 改动清单 ==")
    for k, v in prod.items():
        print(f"  {k}: {v['changed_n']} 个")
    print("本单新增文件:", rep["本单新增文件"]["n"])
    for i in rep["本单新增文件"]["items"]:
        print("   ", i.replace(str(L.PROJECT) + "/", ""))
    print(rep["结论"])
    print("->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
