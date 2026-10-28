# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤2：`test_probe_succ` 按 UID 只读溯源 + 时序判定 + 字段语义表。

纪律：
- 只读快照副本 + 行情库 `mode=ro`；不写任何生产库、不改任何账本行。
- 不把该挂单称为「自然策略成交」。
- 若发现错误，只提「保留原行的最小追加更正方案」，交主代理决定。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
BASE = ROOT / "baseline"
OUT = ROOT / "out"
OUT.mkdir(parents=True, exist_ok=True)

UID = "test_probe_succ"
M15 = 15 * 60 * 1000
MAX_FILL_LAG_MS = 3600_000


def iso(ms) -> str | None:
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(ms) / 1000)) + \
        f".{int(ms) % 1000:03d}Z"


def ms_of(s: str | None):
    if not s:
        return None
    import calendar
    base = s.replace("Z", "")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in base else "%Y-%m-%dT%H:%M:%S"
    return int(calendar.timegm(time.strptime(base, fmt)) * 1000) + (
        int(base.split(".")[1][:3]) if "." in base else 0)


def q(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def market_bars(symbol: str, open_times: list[int]) -> dict:
    """从生产行情库只读取指定 open_time 的 15m bar（含 is_closed 与 available_at）。"""
    db = PROJ / "数据/市场数据/db/market_v2.sqlite3"
    uri = f"file:{db}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=20.0)
    out = {}
    try:
        for ot in open_times:
            rows = q(con,
                     "SELECT open_time,close_time,open,high,low,close,quote_volume,is_closed,"
                     "available_at,source_id FROM candles WHERE exchange='binance' AND market='um' "
                     "AND symbol=? AND timeframe='15m' AND open_time=?",
                     (symbol, int(ot)))
            out[str(ot)] = rows
    finally:
        con.close()
    return out


def main() -> int:
    paper = sqlite3.connect(str(BASE / "paper_3a_snapshot.sqlite3"))
    live = sqlite3.connect(str(BASE / "live_signals_snapshot.sqlite3"))

    pend = q(paper, "SELECT * FROM paper_pending WHERE signal_uid=?", (UID,))
    pos = q(paper, "SELECT * FROM paper_position WHERE signal_uid=?", (UID,))
    fill = q(paper, "SELECT * FROM paper_fill WHERE signal_uid=? ORDER BY fill_id", (UID,))
    order = q(paper, "SELECT * FROM paper_order WHERE event_uid=?", (UID,))
    proc = q(paper, "SELECT * FROM paper_processed WHERE signal_uid=?", (UID,))
    anchor = q(paper, "SELECT * FROM paper_request_anchor WHERE event_uid=?", (UID,))
    sig = q(live, "SELECT * FROM live_signal WHERE signal_uid=?", (UID,))
    paper.close()
    live.close()

    assert len(pend) == 1, f"挂单行数异常 {len(pend)}"
    p = pend[0]
    f = fill[0] if fill else {}
    ps = pos[0] if pos else {}

    t = {
        "event_time_ms": p["event_time"],
        "available_at_ms": p["available_at"],
        "requested_at_ms": p["requested_at"],
        "first_request_ms": p["first_request_ms"],
        "earliest_fill_ms": p["earliest_fill_ms"],
        "created_at_ms": p["created_at"],
        "epoch_ms": p["epoch_ms"],
        "bar_open_time_ms": f.get("bar_open_time"),
        "bar_close_time_ms": f.get("bar_close_time"),
        "fill_ts_ms": f.get("fill_ts"),
        "entry_ts_ms": ps.get("entry_ts"),
        "resolved_at_ms": p.get("resolved_at"),
    }
    t_iso = {k.replace("_ms", ""): iso(v) for k, v in t.items()}

    # ── 派生量（全部用整数毫秒，不做浮点时间运算）────────────────────────────
    eco_terms = [x for x in (t["event_time_ms"], t["available_at_ms"]) if x is not None]
    d = {
        "signal_age_at_first_request_ms": (
            t["first_request_ms"] - max(eco_terms) if eco_terms else None),
        "current_order_request_ms": max(eco_terms + [t["first_request_ms"]]),
        "fixed_order_request_ms": (max(eco_terms) if eco_terms else t["first_request_ms"]),
        "current_earliest_fill_ms": t["earliest_fill_ms"],
        "lag_after_bar_open_ms": (t["fill_ts_ms"] - t["bar_open_time_ms"]),
        "lag_after_bar_close_ms": (t["fill_ts_ms"] - t["bar_close_time_ms"]),
        "entry_ts_minus_fill_ts_ms": (t["entry_ts_ms"] - t["fill_ts_ms"]),
        "bar_len_ms": (t["bar_close_time_ms"] - t["bar_open_time_ms"] + 1),
        "bar_closed_before_fill": t["bar_close_time_ms"] < t["fill_ts_ms"],
        "entry_ts_equals_bar_open": t["entry_ts_ms"] == t["bar_open_time_ms"],
    }
    d["fixed_earliest_fill_ms"] = d["fixed_order_request_ms"] + M15
    d["fixed_lag_at_fill_ms"] = t["fill_ts_ms"] - d["fixed_earliest_fill_ms"]
    d["fixed_would_reject_missed"] = d["fixed_lag_at_fill_ms"] > MAX_FILL_LAG_MS
    d["current_within_max_fill_lag"] = d["lag_after_bar_open_ms"] <= MAX_FILL_LAG_MS

    # ── 行情库核对：应执行 bar 是否就是 16:15 那根，open 是否等于 raw_price ────
    bars = market_bars(ps.get("symbol") or p["symbol"],
                       [t["bar_open_time_ms"], t["bar_open_time_ms"] - M15])
    bar_check = {}
    for k, rows in bars.items():
        bar_check[k] = {
            "iso": iso(int(k)),
            "rows": rows,
            "open_matches_raw_price": (
                bool(rows) and f.get("raw_price") is not None
                and float(rows[0]["open"]) == float(f["raw_price"])),
        }

    # ── 判定（三条，逐条给依据）──────────────────────────────────────────────
    verdict = {
        "A_闭bar规则_成交价取自已闭合bar": {
            "pass": bool(d["bar_closed_before_fill"]),
            "依据": f"bar_close={iso(t['bar_close_time_ms'])} 早于 fill_ts={iso(t['fill_ts_ms'])}，"
                    f"差 {d['lag_after_bar_close_ms']} ms；成交价 raw={f.get('raw_price')} "
                    f"取自该 bar 的 open",
        },
        "B_执行点未真实发生_属收盘后回填开盘价": {
            "backfilled": bool(d["entry_ts_equals_bar_open"]
                               and d["lag_after_bar_open_ms"] >= M15),
            "依据": f"entry_ts=bar_open={iso(t['bar_open_time_ms'])}，"
                    f"而实际处理时刻 fill_ts={iso(t['fill_ts_ms'])}，"
                    f"两者相差 {d['lag_after_bar_open_ms']} ms（= {d['lag_after_bar_open_ms']/60000:.3f} 分钟）。"
                    f"说明成交动作发生在 bar 收盘之后，账本把入场时刻回填到该 bar 开盘。"
                    f"按工单口径这属于「先等整根 K 线收盘，再利用已知整根行情冒充其开盘时已成交」",
        },
        "C_信号时效_首次请求远晚于信号确认": {
            "stale": bool(d["signal_age_at_first_request_ms"] is not None
                          and d["signal_age_at_first_request_ms"] > MAX_FILL_LAG_MS),
            "signal_age_ms": d["signal_age_at_first_request_ms"],
            "signal_age_days": (d["signal_age_at_first_request_ms"] / 86400000
                                if d["signal_age_at_first_request_ms"] is not None else None),
            "依据": f"event_time={iso(t['event_time_ms'])}（= live_start+1s），"
                    f"first_request={iso(t['first_request_ms'])}，"
                    f"相差 {d['signal_age_at_first_request_ms']} ms。"
                    f"当前实现 order_request_ms=max(event_time, available_at, first_request) "
                    f"⇒ 被首次请求时刻续期到 {iso(d['current_order_request_ms'])}；"
                    f"若按信号自身时刻，earliest_fill={iso(d['fixed_earliest_fill_ms'])}，"
                    f"处理时刻滞后 {d['fixed_lag_at_fill_ms']} ms "
                    f"> MAX_FILL_LAG_MS={MAX_FILL_LAG_MS} ⇒ 本应拒单 missed_fill_moment_no_backfill",
        },
    }

    rep = {
        "ticket": "SIGNAL-NOW-01-WORK",
        "step": "2 test_probe_succ 按 UID 只读溯源",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "uid": UID,
        "rows": {"paper_pending": pend, "paper_position": pos, "paper_fill": fill,
                 "paper_order": order, "paper_processed": proc,
                 "paper_request_anchor": anchor, "live_signal": sig},
        "timestamps_ms": t,
        "timestamps_utc": t_iso,
        "derived": d,
        "market_bar_check": bar_check,
        "verdict": verdict,
        "创建来源": {
            "在_框架backend_内搜索_uid": "0 命中（本单复核一致：来源不在仓库代码里）",
            "同批探针": "paper_order id=22 event_uid='test_probe' 于 "
                        f"{iso(1793116001859)} 被 before_paper_epoch_no_backfill 拒；"
                        f"5.5 秒后 test_probe_succ 以 event_time=live_start+1000ms 创建成功",
            "结论": "外部测试探针所建；本单只接管、不追溯、不删除、不强平",
        },
        "禁语": ["自然策略成交", "自然候选闭环", "成交闭环已打通"],
    }
    pth = OUT / "02_test_probe_succ_溯源.json"
    pth.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("== 时间线（UTC）==")
    for k, v in t_iso.items():
        print(f"  {k:22s} {v}")
    print("\n== 派生量 ==")
    for k, v in d.items():
        print(f"  {k:34s} {v}")
    print("\n== 判定 ==")
    for k, v in verdict.items():
        print(f"  {k}")
        for kk, vv in v.items():
            print(f"      {kk}: {vv}")
    print("\n== 行情库核对 ==")
    for k, v in bar_check.items():
        print(f"  {v['iso']} rows={len(v['rows'])} open==raw_price={v['open_matches_raw_price']}")
        for r in v["rows"]:
            print(f"      {r}")
    print(f"\n产物：{pth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
