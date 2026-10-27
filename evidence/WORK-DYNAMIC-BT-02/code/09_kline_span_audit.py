#!/usr/bin/env python3
"""BT-KLINE-CENTER-01｜「前后能看多少天 K 线」的机械核对。

回答用户提问（「前后可以查看多少天的K线」）时**不靠印象**：
逐 run 读 `inputs.json.data_summary.time_range_1h`（= 该 run 实际消费的 1h 区间），
它就是页面上 K 线可查看范围的上界（后端还会再裁一次，见 `backtest_api._finalize_chart_meta`）。

产出：
  out/21_K线居中/23_可查看K线跨度_全库.csv   全部 run 的区间与三周期理论根数
  out/21_K线居中/23_可查看K线跨度_汇总.json  分位数 + 用户截图那个 run 的逐项数字

用法（工作目录须在 框架/backend，或设 PYTHONPATH）：
  python 09_kline_span_audit.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
OUT = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中")
FOCUS_RUN = "run_FT_ENTRY_09_C1_Vs04_Z02_ACHUSDT"

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    sys.path.insert(0, str(BACKEND))
    import backtest_store as S

    root = S.get_runs_root()
    rows = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        inp = d / "inputs.json"
        if not inp.exists():
            continue
        try:
            j = json.loads(inp.read_text(encoding="utf-8"))
        except Exception:
            continue
        tr = (j.get("data_summary") or {}).get("time_range_1h") or {}
        a, b = tr.get("start_ms"), tr.get("end_ms")
        if not isinstance(a, int) or not isinstance(b, int) or b <= a:
            continue
        span_ms = b - a
        rows.append({
            "run_id": d.name,
            "symbol": j.get("symbol"),
            "start_utc": utc(a),
            "end_utc": utc(b),
            "span_hours": round(span_ms / 3_600_000, 2),
            "span_days": round(span_ms / 86_400_000, 3),
            "bars_15m": span_ms // TF_MS["15m"] + 1,
            "bars_1h": span_ms // TF_MS["1h"] + 1,
            "bars_4h": span_ms // TF_MS["4h"] + 1,
        })

    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "23_可查看K线跨度_全库.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    days = sorted(r["span_days"] for r in rows)
    def q(p: float) -> float:
        return round(days[min(len(days) - 1, int(len(days) * p))], 2)

    focus = next((r for r in rows if r["run_id"] == FOCUS_RUN), None)
    summary = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "每个 run 的 inputs.json → data_summary.time_range_1h（run 实际消费区间）",
        "runs_counted": len(rows),
        "span_days": {
            "min": days[0], "p25": q(0.25), "median": round(statistics.median(days), 2),
            "p75": q(0.75), "max": days[-1],
        },
        "rule": {
            "可查看范围": "= 该 run 实际消费的数据区间（time_range_1h）；前端不额外取历史、不取回测之后的行情",
            "后端读取窗口": "run 窗口 ∪ 全部标记点时刻，各自 ± 48 小时（FT_REPLAY_CANDLE_PAD_MS = 2 天）",
            "按笔聚焦": "带 focus_ms 时收窄到「该时刻前后各 10000 × 15 分钟」再与上面求交",
            "单次上限": "FT_REPLAY_CANDLE_CAP = 20000 根；超出取末 20000 根并置 candles_truncated=true",
            "最终裁剪": "统一裁到 time_range_1h（candles_clipped_to_run_data），避免画进回测没消费的行情",
        },
        "focus_run": focus,
        "note": "15m/4h 与 1h 同一时间跨度，只是根数不同；根数为「跨度 ÷ 周期 + 1」的理论值，实际返回见接口",
    }
    json_path = OUT / "23_可查看K线跨度_汇总.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n写出 {csv_path}（{len(rows)} 行）与 {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
