# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-05 驱动：等待 FT-ENTRY-04 全部 10 组产出后，依次运行审计脚本。

后台运行；全部完成后写入 logs/run_all.log。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY04 = os.path.join(ROOT, "evidence/FT-ENTRY-04")
OUT04 = os.path.join(ENTRY04, "out")

STRATS = ["WyckoffBaseline"] + [
    f"Wyckoff_A_{o}_{v}" for o in ("L06", "L04", "L02") for v in ("V08", "V10", "V12")
]


def all_ready() -> bool:
    have = [s for s in STRATS if os.path.isfile(os.path.join(OUT04, f"trades_{s}.csv"))]
    return len(have) == len(STRATS)


def main() -> None:
    # 轮询等待（最多约 50 分钟）
    waited = 0
    while not all_ready() and waited < 3000:
        have = sum(1 for s in STRATS if os.path.isfile(os.path.join(OUT04, f"trades_{s}.csv")))
        print(f"[{time.strftime('%H:%M:%S')}] 已产出 {have}/{len(STRATS)} 组，等待...", flush=True)
        time.sleep(30)
        waited += 30
    if not all_ready():
        print("超时：FT-ENTRY-04 仍未全部产出，仅对已有组生成报表", flush=True)

    for step in ("03_verify_and_recompute.py", "04_rejection_attribution.py",
                "05_stats_robustness.py", "06_final_verdict.py"):
        print(f"\n===== 运行 {step} =====", flush=True)
        logp = os.path.join(HERE, "..", "logs", f"run_{step.replace('.py', '')}.log")
        with open(logp, "w", encoding="utf-8") as lf:
            proc = subprocess.run([sys.executable, os.path.join(HERE, step)],
                                  stdout=lf, stderr=subprocess.STDOUT, text=True, cwd=HERE)
        print(f"  exit={proc.returncode}", flush=True)

    print("\n全部审计脚本执行完毕", flush=True)


if __name__ == "__main__":
    main()
