# -*- coding: utf-8 -*-
"""FT-METRICS-02 一键核对入口。

用法：
    /Users/boos/Desktop/威科夫因子共振趋势/evidence/FT-BASELINE-01/.venv/bin/python \
        evidence/FT-METRICS-02/code/run_all.py

行为：依次执行 前置检查 → 指标重算 → 逐笔核对 → 出报告，
全过程写入 logs/实际核对日志_*.log；前置不满足时立即停止，不产出正式绩效。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
LOGS = BASE / "logs"

STEPS = [
    ("10_manifest.py", "前置检查：数据包/版本/窗口/缓存"),
    ("20_metrics.py", "独立重算：A 共享账户指标"),
    ("30_trades.py", "逐笔核对：差异表与止损异常"),
    ("40_report.py", "汇总：结论与报告"),
]


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS / f"实际核对日志_{stamp}.log"
    buf: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        buf.append(s)

    emit(f"# FT-METRICS-02 实际核对日志  {datetime.now():%Y-%m-%d %H:%M:%S}")
    emit(f"# 解释器 {sys.executable}")
    emit(f"# 目标目录 {BASE.parent.parent / 'evidence' / 'FT-BASELINE-03'}")
    emit("")

    for script, desc in STEPS:
        emit(f"=== [{script}] {desc} ===")
        r = subprocess.run([sys.executable, str(HERE / script)],
                           capture_output=True, text=True)
        if r.stdout.strip():
            emit(r.stdout.rstrip())
        if r.stderr.strip():
            emit("[stderr] " + r.stderr.strip()[:2000])
        emit(f"[exit] {r.returncode}")
        emit("")
        if r.returncode == 2:
            emit(">>> 前置未满足，按要求停止：不计算正式绩效，不使用旧基线诊断结果代替。")
            break
        if r.returncode not in (0, 3, 4):
            emit(">>> 脚本异常退出，停止。")
            break

    log_path.write_text("\n".join(buf) + "\n", encoding="utf-8")
    print(f"\n日志已写入 {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
