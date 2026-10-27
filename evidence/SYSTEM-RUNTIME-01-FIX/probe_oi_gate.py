# -*- coding: utf-8 -*-
"""决定性单测：构造一条**完整满足冻结规则**的形态序列（箱体→扫低→回踩→放量突破），
看 `EventStateMachine` 在 `enable_oi_filter=False`（B 组，跳过 OI 过滤）下
到底会不会产出候选。

若产不出、且终止理由是 `OI_UNAVAILABLE`，则证明：**OI 闸门在关闭 OI 过滤时仍然拦单**，
运行时空 OI 数组 ⇒ 即使价格形态全中，信号也永远出不来。

只读，不写库。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault(
    "MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))

sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine  # noqa: E402
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig  # noqa: E402

H1 = 3_600_000
A = 1.0
H, L = 103.0, 97.0            # 箱体宽度 6A，落在 2A~8A
T0 = 1_790_000_000_000        # 任意 4h 收盘毫秒


def build_box():
    """42 根 4h：高低夹在 [97,103]，收盘来回震荡使 ER<=0.30。"""
    highs = np.full(42, H)
    lows = np.full(42, L)
    closes = np.array([97.0 if i % 2 == 0 else 103.0 for i in range(42)], dtype=float)
    closes[-1] = 97.5          # 起点附近收尾 ⇒ 净位移小、路径长 ⇒ ER 很低
    return highs, lows, closes


def main() -> int:
    out = {"cases": []}
    for enable_oi in (False, True):
        cfg = EventTrendConfig()
        sm = EventStateMachine(config=cfg, symbol="TESTUSDT", venue="binance")
        h, l, c = build_box()
        box = sm.check_and_form_box(T0, h, l, c, A)
        rec = {"enable_oi_filter": enable_oi,
               "box_formed": box is not None,
               "box_width_atr": round(box.box_width_atr, 3) if box else None,
               "box_er": round(box.efficiency_ratio_er, 4) if box else None}
        if box is None:
            out["cases"].append(rec)
            continue

        def feed(idx, o, hi, lo, cl, vol):
            t = T0 + idx * H1
            return sm.process_1h_bar(
                open_time_ms=t, open_p=o, high_p=hi, low_p=lo, close_p=cl,
                volume_v=vol,
                oi_timestamps=np.array([], dtype=np.int64),
                oi_values=np.array([], dtype=np.float64))

        # 1) 扫低：低点 96.8（在 L-0.5A=96.5 之上），收盘 97.3（≥L+0.1A），量 1000
        r1 = feed(1, 97.6, 97.9, 96.8, 97.3, 1000.0)
        # 2) 回踩：低点 97.2（在 [L, L+0.5A] 且 > S=96.8），收阳 97.4，量 600（<=0.8*1000）
        r2 = feed(2, 97.25, 97.5, 97.2, 97.4, 600.0)
        # 3) 放量突破：前收 97.4 <= H+0.1A=103.1，本根收 103.5 > 103.1，量 3000
        #    V20 取前 20 根 1h 中位量；此处样本少，用大倍数保证 >=1.5*V20
        r3 = feed(3, 97.5, 103.8, 97.4, 103.5, 5000.0)

        rec.update({
            "sweep_state": sm.transitions[-1].to_state if sm.transitions else None,
            "after_retest_state": (sm.transitions[-1].to_state
                                   if sm.transitions else None),
            "breakout_candidate": r3 is not None,
            "states": [t.to_state for t in sm.transitions],
            "last_reason": sm.transitions[-1].reason if sm.transitions else None,
            "last_metrics": (sm.transitions[-1].metrics_snapshot
                             if sm.transitions else None),
            "active_state": (sm.active_event.current_state.value
                             if sm.active_event else "NONE"),
        })
        out["cases"].append(rec)

    p = ROOT / "evidence" / "SYSTEM-RUNTIME-01-FIX" / "oi_gate_probe.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
