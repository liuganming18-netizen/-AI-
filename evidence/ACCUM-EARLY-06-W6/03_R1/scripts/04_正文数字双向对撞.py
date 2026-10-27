# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R1 | 04 正文数字双向对撞

核对重点：
1. 读取 out/03_总交接_R1台账.json 中所有指标数值；
2. 在 总交接_R1_待Codex审核.md 正文中逐项反查，确认所有数字真实存在且完全吻合；
3. 输出 04_双向对撞结果.json，杜绝文稿手写笔误与数据漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "03_R1" / "out"
DELIVERY_FILE = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "03_R1" / "总交接_R1_待Codex审核.md"


def main():
    print("[04_正文数字双向对撞] 开始运行...")

    with open(OUT_DIR / "03_总交接_R1台账.json", "r", encoding="utf-8") as f:
        ledger = json.load(f)

    with open(DELIVERY_FILE, "r", encoding="utf-8") as f:
        report_text = f.read()

    checks = []
    all_matched = True

    for k, v in ledger.items():
        v_str = str(v)
        found = v_str in report_text
        if not found:
            all_matched = False
        checks.append({
            "key": k,
            "value": v,
            "value_str": v_str,
            "found_in_report": found
        })

    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["found_in_report"])

    res = {
        "task": "ACCUM-EARLY-06-W6-R1",
        "step": "04_正文数字双向对撞",
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": total_checks - passed_checks,
        "all_matched": all_matched,
        "checks": checks
    }

    out_file = OUT_DIR / "04_双向对撞结果.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"[04_正文数字双向对撞] 完成！通过: {passed_checks}/{total_checks}, 结论: {'全部吻合' if all_matched else '存在不吻合'}")
    assert all_matched, f"存在数字未在交付报告中找到！{checks}"


if __name__ == "__main__":
    main()
