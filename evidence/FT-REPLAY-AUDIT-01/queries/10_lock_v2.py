# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 10：输入与版本指纹锁定（v2）。

锁定对象：
  1) 源账本：FT-ENTRY-09 的 27 组 trades_*.csv、合并表、18 组确认表、27 组订单表
  2) 本批索引：FT-REPLAY-01 R1_01 / R1_02、FT-REPLAY-02 交付文件
  3) 系统登记：两套批次目录（组级 27 / 币级 1065）的 batch_meta 与逐笔文件
  4) 代码版本：后端三个适配文件 + 前端回测页组件

只读。输出 out/10_输入指纹_v2.csv、out/10_锁定摘要_v2.json。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
R01 = os.path.join(ROOT, "evidence/FT-REPLAY-01")
R02 = os.path.join(ROOT, "evidence/FT-REPLAY-02")
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
BT = os.path.join(ROOT, "回测日志/event_trend_v0_1")
GRID_DIR = os.path.join(BT, "runs_ft_entry_09_research")
SYM_DIR = os.path.join(BT, "runs_ft_replay_01")
MAIN_RUNS = os.path.join(BT, "runs")

GRIDS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
         "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
MODES = ["C", "C1", "C2"]


def sha256_file(p):
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def add(rows, cat, path, note=""):
    rows.append({
        "类别": cat,
        "路径": os.path.relpath(path, ROOT) if path.startswith(ROOT) else path,
        "字节": os.path.getsize(path) if os.path.isfile(path) else None,
        "sha256": sha256_file(path),
        "存在": os.path.isfile(path),
        "说明": note,
    })


def dir_digest(paths):
    """目录整体指纹：按「解析软链后的真实相对路径」排序，对「路径|sha256」串取 SHA-256。

    用 realpath 而不是软链路径，才能判定「批次目录里的逐笔」与「主运行根里的逐笔」
    是不是同一份内容（本批批次目录全部是相对软链）。
    """
    items = []
    for p in sorted(paths):
        real = os.path.realpath(p)
        items.append("%s|%s" % (os.path.relpath(real, ROOT), sha256_file(p) or ""))
    h = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
    return h, len(items)


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []

    # ── 1. 源账本 ────────────────────────────────────────────────
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            add(rows, "源账本-逐组成交", os.path.join(E09, "out", "trades_%s.csv" % st), st)
    add(rows, "源账本-合并表", os.path.join(E09, "out", "00_all_27_trades_consolidated.csv"),
        "27 组合并")
    for mode in ("C1", "C2"):
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            add(rows, "源账本-候选确认表", os.path.join(E09, "out", "audit_confirmation_%s.csv" % st), st)
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            add(rows, "源账本-订单表", os.path.join(E09, "out", "audit_orders_%s.csv" % st), st)

    # ── 2. 本批索引与交付 ────────────────────────────────────────
    for fn in ("R1_00_接入说明与使用入口.md", "R1_01_研究期运行登记索引.csv", "R1_02_核对记录.csv"):
        add(rows, "本批索引", os.path.join(R01, fn))
    for fn in ("00_修改清单与修复总览.md", "01_实验运行与批次登记索引.csv",
               "02_实际API核对与数据契约校验.csv", "03_页面操作指引与复盘核验报告.md"):
        add(rows, "修正交付", os.path.join(R02, fn))

    # ── 3. 系统登记 ──────────────────────────────────────────────
    add(rows, "系统登记-组级批次", os.path.join(GRID_DIR, "batch_meta.json"))
    add(rows, "系统登记-币级批次", os.path.join(SYM_DIR, "batch_meta.json"))
    grid_runs = sorted(glob.glob(os.path.join(GRID_DIR, "run_FT_ENTRY_09_*")))
    sym_runs = sorted(glob.glob(os.path.join(SYM_DIR, "run_FT_ENTRY_09_*")))
    main_runs = sorted(glob.glob(os.path.join(MAIN_RUNS, "run_FT_ENTRY_09_*")))
    for p in grid_runs:
        add(rows, "系统登记-组级逐笔", os.path.join(p, "trades.jsonl"), os.path.basename(p))
    dg, ng = dir_digest([os.path.join(p, "trades.jsonl") for p in sym_runs])
    dm, nm = dir_digest([os.path.join(p, "trades.jsonl") for p in main_runs])
    dc, nc = dir_digest([os.path.join(p, "chart.json") for p in sym_runs])
    # 批次目录里是软链：确认软链指向主运行根，不占双份
    links = {}
    for p in sym_runs:
        real = os.path.realpath(p)
        links[p] = real
    symlink_ok = sum(1 for p, r in links.items() if r.startswith(os.path.realpath(MAIN_RUNS)))
    for p in main_runs[:1]:
        add(rows, "系统登记-主运行样例", os.path.join(p, "run.json"), os.path.basename(p))

    # ── 4. 代码版本 ──────────────────────────────────────────────
    for fn in ("backtest_api.py", "backtest_batches.py", "ledger_links.py"):
        add(rows, "代码-后端", os.path.join(ROOT, "框架/backend", fn))
    for fn in ("components/BacktestWorkbench.tsx", "api/labBacktest.ts", "api/backtestReview.ts"):
        add(rows, "代码-前端", os.path.join(ROOT, "UI界面/web/src", fn))

    import csv as _csv
    p = os.path.join(OUT, "10_输入指纹_v2.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = _csv.DictWriter(fh, fieldnames=["类别", "路径", "字节", "sha256", "存在", "说明"])
        w.writeheader()
        w.writerows(rows)

    missing = [r for r in rows if not r["存在"]]
    summary = {
        "锁文件数": len(rows),
        "缺失数": len(missing),
        "缺失清单": [r["路径"] for r in missing],
        "组级run目录数": len(grid_runs),
        "币级run目录数": len(sym_runs),
        "主运行根币级run数": len(main_runs),
        "币级批次软链指向主运行根": symlink_ok,
        "币级逐笔合并指纹": {"sha256": dg, "文件数": ng},
        "主运行根逐笔合并指纹": {"sha256": dm, "文件数": nm},
        "币级K线合并指纹": {"sha256": dc, "文件数": nc},
        "币级与主运行根逐笔一致": dg == dm,
        "关键指纹": {
            "源合并表": sha256_file(os.path.join(E09, "out", "00_all_27_trades_consolidated.csv")),
            "组级batch_meta": sha256_file(os.path.join(GRID_DIR, "batch_meta.json")),
            "币级batch_meta": sha256_file(os.path.join(SYM_DIR, "batch_meta.json")),
            "backtest_api.py": sha256_file(os.path.join(ROOT, "框架/backend/backtest_api.py")),
            "backtest_batches.py": sha256_file(os.path.join(ROOT, "框架/backend/backtest_batches.py")),
            "ledger_links.py": sha256_file(os.path.join(ROOT, "框架/backend/ledger_links.py")),
        },
    }
    with open(os.path.join(OUT, "10_锁定摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("锁定文件 %d 个（缺失 %d）" % (len(rows), len(missing)))
    print("组级 run %d 个 ｜ 币级 run %d 个 ｜ 主运行根 %d 个 ｜ 软链指向主根 %d"
          % (len(grid_runs), len(sym_runs), len(main_runs), symlink_ok))
    print("币级逐笔合并指纹 == 主运行根：%s" % (dg == dm))
    print(json.dumps(summary["关键指纹"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
