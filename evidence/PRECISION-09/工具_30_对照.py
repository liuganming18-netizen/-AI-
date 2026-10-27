#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-09 工具 30：修复前后逐字段对照（读两份探针 JSON，不重跑）。

判据（写死在这里，先声明后看结果）
────────────────────────────────
  必须翻转：S1/S2/S3 —— invalid_reasons 由空变非空，run_valid 由 True 变 False；
                       其余「无成交」字段（成交数/持仓/现金/费用/账本恒等）**不变**。
  必须不变：S4 策略取消（run_valid 仍 True、invalid_reasons 仍空）、
            S5 正常成交（逐字段一致）、S6 出场拒绝（原因与仓位保留一致）、
            S7 规格阻断（formal_pass_allowed 仍 False）、S8 缺规格仍抛错。
"""
from __future__ import annotations

import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

FLIP = ["S1_名义额低于最小", "S2_数量低于最小", "S3_原价非正"]
SAME = ["S4_跌回箱体取消", "S5_正常成交", "S6_出场拒绝", "S7_历史规格不可证", "S8_缺规格"]

FIELDS = ["trades_count", "open_positions_remaining", "final_cash", "final_equity",
          "fees_total", "rejected_reasons", "rejects_count", "invalid_reasons",
          "run_valid", "unclosed_positions", "formal_pass_allowed",
          "cash_identity_ok", "equity_identity_ok", "first_trade"]


def load(label: str) -> dict:
    with open(os.path.join(HERE, f"探针_{label}.json"), encoding="utf-8") as f:
        return json.load(f)["scenarios"]


def main() -> int:
    before, after = load("修复前"), load("修复后")
    rows, problems = [], []

    for name in FLIP + SAME:
        b, a = before.get(name, {}), after.get(name, {})
        for f in FIELDS:
            if f not in b and f not in a:
                continue
            vb, va = b.get(f), a.get(f)
            changed = vb != va
            rows.append({"场景": name, "字段": f,
                         "修复前": json.dumps(vb, ensure_ascii=False),
                         "修复后": json.dumps(va, ensure_ascii=False),
                         "变化": "变" if changed else "—"})
            # 判定
            if name in FLIP:
                if f in ("invalid_reasons", "run_valid", "rejects_count"):
                    continue          # 允许/要求变，另行校验
                if changed:
                    problems.append(f"{name}.{f} 不该变：{vb} → {va}")
            else:
                if changed:
                    problems.append(f"{name}.{f} 不该变：{vb} → {va}")

    # 翻转场景的正向校验
    for name in FLIP:
        b, a = before[name], after[name]
        if b["invalid_reasons"] or b["run_valid"] is not True:
            problems.append(f"{name} 修复前基线不符合预期（应为空/True）")
        if not a["invalid_reasons"] or a["run_valid"] is not False:
            problems.append(f"{name} 修复后未翻转（invalid_reasons={a['invalid_reasons']}, "
                            f"run_valid={a['run_valid']}）")
        if a["trades_count"] != 0 or a["open_positions_remaining"] != []:
            problems.append(f"{name} 修复后出现虚假成交/持仓")
        if not (a["cash_identity_ok"] and a["equity_identity_ok"]):
            problems.append(f"{name} 修复后账本恒等被破坏")
        if a["final_cash"] != a["initial_equity"]:
            problems.append(f"{name} 修复后现金与初始不一致")

    # 策略取消必须仍为有效
    s4 = after["S4_跌回箱体取消"]
    if s4["invalid_reasons"] or s4["run_valid"] is not True:
        problems.append("S4 策略取消被误判无效")

    with open(os.path.join(HERE, "对照_修复前后.csv"), "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=["场景", "字段", "修复前", "修复后", "变化"])
        w.writeheader()
        w.writerows(rows)

    lines = ["PRECISION-09 修复前后对照（同一脚本两次运行，差异只可能来自被测代码）", "=" * 78]
    for name in FLIP + SAME:
        b, a = before[name], after[name]
        tag = "必须翻转" if name in FLIP else "必须不变"
        lines.append(f"\n[{name}]（{tag}）")
        if "raised" in b or "raised" in a:
            lines.append(f"  抛出: {b.get('raised')} → {a.get('raised')}")
            continue
        lines.append(f"  run_valid      : {b['run_valid']} → {a['run_valid']}")
        lines.append(f"  invalid_reasons: {b['invalid_reasons']} → {a['invalid_reasons']}")
        lines.append(f"  成交/持仓      : {b['trades_count']}/{b['open_positions_remaining']}"
                     f" → {a['trades_count']}/{a['open_positions_remaining']}")
        lines.append(f"  现金/费用      : {b['final_cash']}/{b['fees_total']}"
                     f" → {a['final_cash']}/{a['fees_total']}")
        lines.append(f"  formal_pass    : {b['formal_pass_allowed']} → {a['formal_pass_allowed']}")
    lines.append("")
    lines.append("判定：" + ("全部符合预期（0 处偏差）" if not problems
                             else f"{len(problems)} 处偏差"))
    for p in problems:
        lines.append("  ✗ " + p)
    txt = "\n".join(lines)
    with open(os.path.join(HERE, "对照_修复前后.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
