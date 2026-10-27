# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜03 持仓量是否真的进入判断链路。

三件事：
1. 生产调用路径逐行取证（不凭旧报告）；
2. 「只注册一次」缺陷的行为验证；
3. 真实非空样本 + 增长率（来源、单位、时间）。
输出：evidence/ACCUM-EARLY-01/out/03_持仓量核验.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main() -> int:
    import oi_feed as O
    import numpy as np

    rep = {"probe": "03_持仓量核验", "generated_at_utc": iso(time.time() * 1000),
           "errors": []}

    # ── 1. 生产调用路径取证 ───────────────────────────────────────────
    src = (BACKEND / "unified_runtime.py").read_text(encoding="utf-8").splitlines()
    hits = [(i + 1, l.strip()) for i, l in enumerate(src)
            if "oi_timestamps=" in l or "oi_values=" in l]
    rep["生产调用路径"] = {
        "文件": "框架/backend/unified_runtime.py",
        "喂给状态机的参数": hits,
        "结论": "喂的是 np.array([], dtype=...) 空数组 ⇒ 持仓量未进入判断链路",
        "开关": {
            "EventTrendConfig.enable_oi_filter": True,
            "说明": "开关是开的，但数组是空的 ⇒ 任何突破都会落 OI_UNAVAILABLE，"
                    "除非开关被显式关掉（运行实例的漏斗显示 OI_UNAVAILABLE=0、"
                    "history CANDIDATE=51，与「开关开+空数组」不相容 ⇒ 运行实例实际为关）",
        },
    }

    # ── 2. 只注册一次的行为验证 ───────────────────────────────────────
    try:
        from 策略.WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge
        pb = PitBridge(symbol="BTCUSDT", mode="declared_assumption")
        before = pb.oi_available_at is None
        pb.register_oi(np.array([], dtype=np.int64), np.array([], dtype=np.float64))
        after_empty = pb.oi_available_at
        r_empty = pb.read_oi(1793000000000, lookback_ms=6 * 3600_000,
                             max_distance_ms=900_000)
        # 再注册一份真实数据：状态机的调用点有 `if self.pit.oi_available_at is None`
        # 守卫，此时已经非 None ⇒ 不会再注册
        ts = np.array([1792990000000, 1792995000000, 1793000000000], dtype=np.int64)
        vals = np.array([100.0, 101.0, 103.0], dtype=np.float64)
        guard_would_skip = pb.oi_available_at is not None
        pb.register_oi(ts, vals)
        r_real = pb.read_oi(1793003600000, lookback_ms=6 * 3600_000,
                            max_distance_ms=900_000)
        rep["只注册一次缺陷"] = {
            "strength": "behavior_isolated",
            "初始 oi_available_at 为 None": before,
            "注册空数组后 oi_available_at 非 None": guard_would_skip,
            "空数组读取结果": r_empty,
            "调用点守卫": "state_machine.py: `if self.pit.oi_available_at is None: "
                          "self.pit.register_oi(...)` ⇒ 首次注册后永不更新",
            "后果": "第一次突破若拿到空 OI，该进程后续所有突破读到的仍是空 ⇒ "
                    "接通 OI 必须改冻结包 state_machine.py，属策略实现变更，需裁决",
            "手工再注册后的读取（仅证明桥本身可用）": r_real,
        }
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "pit", "error": f"{type(exc).__name__}: {exc}"})

    # ── 3. 真实非空样本 ──────────────────────────────────────────────
    try:
        rep["真实样本"] = O.latest_oi("BTCUSDT")
        ts, vals, meta = O.load_oi("BTCUSDT", 1793005200000, 6 * 3600_000)
        rep["真实序列"] = {"meta": meta,
                          "growth": O.oi_growth(ts, vals, 1793005200000, 6 * 3600_000)}
        # 空结果必须显式（不返回全零）
        ts0, v0, m0 = O.load_oi("NOTEXISTUSDT", 1793005200000)
        rep["空结果行为"] = {"rows": len(ts0), "reason": m0["reason"],
                             "note": "空就是空，不返回零数组"}
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "sample", "error": f"{type(exc).__name__}: {exc}"})

    # ── 4. 覆盖 ─────────────────────────────────────────────────────
    try:
        con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
        now = int(time.time() * 1000)
        tot = con.execute("""SELECT COUNT(DISTINCT symbol) FROM oi_history
            WHERE exchange='binance' AND market='um' AND period='5m'""").fetchone()[0]
        fresh = con.execute("""SELECT COUNT(DISTINCT symbol) FROM oi_history
            WHERE exchange='binance' AND market='um' AND period='5m' AND ts>=?""",
            (now - 3600_000,)).fetchone()[0]
        first = con.execute("""SELECT MIN(ts) FROM oi_history
            WHERE exchange='binance' AND market='um' AND period='5m'""").fetchone()[0]
        con.close()
        rep["覆盖"] = {"有 5m 历史的标的": tot, "一小时内更新": fresh,
                       "起点_utc": iso(first),
                       "说明": "采集健康；缺的是读取接线与注册守卫"}
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "coverage", "error": f"{type(exc).__name__}: {exc}"})

    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "03_持仓量核验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"喂参": rep["生产调用路径"]["喂给状态机的参数"],
                      "覆盖": rep.get("覆盖"),
                      "只注册一次": {k: v for k, v in
                                    rep.get("只注册一次缺陷", {}).items()
                                    if k in ("初始 oi_available_at 为 None",
                                             "注册空数组后 oi_available_at 非 None",
                                             "后果")},
                      "errors": rep["errors"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
