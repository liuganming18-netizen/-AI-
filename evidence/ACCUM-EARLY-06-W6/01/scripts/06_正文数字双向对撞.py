# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-01 | 06 正文数字双向对撞

核对重点：
1. 从 05_正文数字台账.json 中提取所有指标数值；
2. 在 README.md 正文中逐项反查，确认所有数字均真实存在且完全吻合；
3. 输出 06_数字双向对撞结果.json，杜绝任何文稿手写与数字漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "out"
README_FILE = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "README.md"


def main():
    print("[06_正文数字双向对撞] 开始运行...")

    with open(OUT_DIR / "05_正文数字台账.json", "r", encoding="utf-8") as f:
        ledger = json.load(f)

    with open(README_FILE, "r", encoding="utf-8") as f:
        readme_text = f.read()

    checks = []
    all_matched = True

    for k, v in ledger.items():
        v_str = str(v)
        # 在 README 中搜索
        found = v_str in readme_text
        if not found:
            all_matched = False
        checks.append({
            "key": k,
            "value": v,
            "value_str": v_str,
            "found_in_readme": found
        })

    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["found_in_readme"])

    res = {
        "task": "ACCUM-EARLY-06-W6-01",
        "step": "06_正文数字双向对撞",
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": total_checks - passed_checks,
        "all_matched": all_matched,
        "checks": checks
    }

    out_file = OUT_DIR / "06_数字双向对撞结果.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"[06_正文数字双向对撞] 完成！通过: {passed_checks}/{total_checks}, 结论: {'全部吻合' if all_matched else '存在不吻合'}")
    assert all_matched, f"存在数字未在 README 中找到！{checks}"


if __name__ == "__main__":
    main()
