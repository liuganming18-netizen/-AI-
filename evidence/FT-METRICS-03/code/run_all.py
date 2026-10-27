# -*- coding: utf-8 -*-
"""FT-METRICS-03 一键重跑：锁定 → 资金费 → P0 → 指标 → 结论。

每一步都独立可执行，失败即停，不跳过、不兜底。
实际核对日志按步骤合并写入 logs/。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
LOGS = BASE / "logs"
PY = sys.executable

STEPS = [
    ("10_lock.py", "锁定结果包与版本"),
    ("20_funding.py", "资金费事件级核对"),
    ("30_p0.py", "P0 绑定核对"),
    ("40_metrics.py", "唯一指标表"),
    ("50_report.py", "逐笔核对表与结论"),
]


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = []
    out.append(f"FT-METRICS-03 实际核对日志  开始 {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append(f"解释器：{PY}")
    out.append("=" * 78)

    for i, (script, title) in enumerate(STEPS, 1):
        out.append("")
        out.append(f"### 步骤 {i}/{len(STEPS)}  {title}  ({script})")
        out.append("-" * 78)
        p = subprocess.run([PY, str(HERE / script)], capture_output=True, text=True)
        out.append(p.stdout.rstrip())
        if p.returncode != 0:
            out.append(p.stderr.rstrip())
            out.append(f"!! 步骤失败，返回码 {p.returncode}，后续步骤不执行")
            (LOGS / f"实际核对日志_{stamp}.log").write_text("\n".join(out), encoding="utf-8")
            print(f"步骤 {i} 失败：{script} (rc={p.returncode})")
            return p.returncode
        if p.stderr.strip():
            out.append("[stderr] " + p.stderr.strip())

    out.append("")
    out.append("=" * 78)
    out.append(f"全部步骤完成 {datetime.now():%Y-%m-%d %H:%M:%S}")
    txt = "\n".join(out)
    (LOGS / f"实际核对日志_{stamp}.log").write_text(txt, encoding="utf-8")
    # 分步临时日志已并入总日志，清掉避免与正式交付混淆
    for tmp in (BASE / "out").glob("_*_log.txt"):
        tmp.unlink(missing_ok=True)
    print(txt[-1800:])
    print(f"\n日志：logs/实际核对日志_{stamp}.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
