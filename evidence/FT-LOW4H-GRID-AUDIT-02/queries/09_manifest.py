# -*- coding: utf-8 -*-
"""09 生成本轮产物清单（含 SHA-256、字节数、行数），供 Codex 独立验收核对。

输出：
  out/09_产物清单.csv    逐文件：相对路径 / 类型 / 字节 / 行数 / SHA-256
  out/09_产物清单.md     人读版，按用途分组
  out/09_144组与产物对应.csv   144 组 → 策略文件 / zip / 逐笔 / 确认表 / 订单表 / 日志 的一一对应
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

import _lib as L

HERE, OUT, UP = L.HERE, L.OUT, L.UP


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def nline(p: Path) -> int | None:
    if p.suffix.lower() in {".json", ".csv", ".md", ".py", ".log"}:
        try:
            return sum(1 for _ in open(p, "rb"))
        except Exception:
            return None
    return None


GROUPS = [
    ("报告", [HERE / "00_报告.md"]),
    ("冻结与分析计划", [OUT / "01_冻结分析计划.json"]),
    ("一 运行来源核实", sorted(OUT.glob("01_*"))),
    ("二 逐笔执行核对", sorted(OUT.glob("02_*"))),
    ("三 账户与指标", sorted(OUT.glob("03_*"))),
    ("四 稳定性", sorted(OUT.glob("04_*"))),
    ("五 配对与终裁", sorted(OUT.glob("05_*"))),
    ("六 系统核对", sorted(OUT.glob("06_*"))),
    ("七 上游报告复核", sorted(OUT.glob("07_*"))),
    ("对账与自检", [OUT / "07b_报告数值对账.json", OUT / "07b_报告数值对账.csv", OUT / "08_自检.json"]),
    ("脚本", sorted((HERE / "queries").glob("*.py"))),
]

rows = []
seen = set()
for grp, files in GROUPS:
    for p in files:
        if not p.exists() or p in seen:
            continue
        seen.add(p)
        rows.append({"分组": grp, "相对路径": str(p.relative_to(HERE)), "类型": p.suffix.lstrip("."),
                     "字节": p.stat().st_size, "行数": nline(p), "SHA256": sha(p)})
df = pd.DataFrame(rows)
df.to_csv(OUT / "09_产物清单.csv", index=False, encoding="utf-8-sig")

# ---- 144 组与产物对应
runs = pd.read_csv(OUT / "01_运行时证据逐组.csv", encoding="utf-8-sig")
keep = [c for c in ["策略", "组合", "参数组", "stoploss_s", "trail_d", "笔数", "派生类SHA一致",
                    "zip内嵌类名", "日志文件", "日志首行时间", "日志末行时间", "zip文件"] if c in runs.columns]
map144 = runs[keep].copy() if keep else runs.copy()
map144["策略文件"] = map144["策略"].map(lambda s: f"user_data/strategies/{s}.py")
map144["逐笔CSV"] = map144["策略"].map(lambda s: f"out/trades_{s}.csv")
map144["确认表CSV"] = map144["策略"].map(lambda s: f"out/audit_confirmation_{s}.csv")
map144["订单表CSV"] = map144["策略"].map(lambda s: f"out/audit_orders_{s}.csv")
map144["结果包zip"] = map144["策略"].map(lambda s: f"out/backtest-result-{s}.zip")
map144.to_csv(OUT / "09_144组与产物对应.csv", index=False, encoding="utf-8-sig")

# ---- 人读版
lines = ["# FT-LOW4H-GRID-AUDIT-02 产物清单", "",
         f"共 {len(df)} 个文件，合计 {df['字节'].sum():,} 字节。SHA-256 由 `queries/09_manifest.py` 现算。", ""]
for grp, sub in df.groupby("分组", sort=False):
    lines += [f"## {grp}（{len(sub)} 个）", "", "| 文件 | 字节 | 行数 | SHA-256（前 16） |", "|---|---|---|---|"]
    for _, r in sub.iterrows():
        lines.append(f"| `{r['相对路径']}` | {r['字节']:,} | {r['行数'] if pd.notna(r['行数']) else ''} "
                     f"| `{r['SHA256'][:16]}` |")
    lines.append("")
lines += ["## 144 组与产物对应", "",
          "逐组明细见 `out/09_144组与产物对应.csv`（144 行）。每组对应：", "",
          "- 派生类策略文件 `user_data/strategies/<策略>.py`",
          "- 结果包 `out/backtest-result-<策略>.zip`",
          "- 逐笔 `out/trades_<策略>.csv`、确认表 `out/audit_confirmation_<策略>.csv`、订单表 `out/audit_orders_<策略>.csv`",
          "- 日志 `logs/<策略>_backtest.log`", "",
          "另有 2 个**非正式**结果包（`test_d05-*`、`test_d10-*`），是修好基类后的单组试跑，"
          "早于正式批次，已排除、未删除。", ""]
(OUT / "09_产物清单.md").write_text("\n".join(lines), encoding="utf-8")

print(f"产物清单：{len(df)} 个文件，{df['字节'].sum():,} 字节")
print(df.groupby("分组", sort=False).agg(个数=("相对路径", "size"), 字节=("字节", "sum")).to_string())
print(f"144 组对应表：{len(map144)} 行 → out/09_144组与产物对应.csv")
