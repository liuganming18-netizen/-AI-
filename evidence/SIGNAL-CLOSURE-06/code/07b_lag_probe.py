# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜07b 逐币水位探针（补齐 `07` 的字段路径缺陷）。

为什么需要它：`07` 的首轮运行把 `lag_1h_max` 读在 `watermark_check` 顶层，
而真实位置是 `watermark_check.per_symbol.lag_1h_max` ⇒ 采到 None ⇒ 判据被
`or 0` 兜成「合格」。首轮进程已在跑，改代码救不回它已经采到的样本，
所以用本件**独立、只读**地补一份正确的水位证据。

本件不改任何状态，只做：
  ① 等到库内 1h 最新已闭合 open_time 超过给定基线（默认自动取当前）；
  ② 采完整 `watermark_check` + 逐币 lag 分布 + 逐币阶段 + 漏斗 + 账本计数；
  ③ 校验「锚点没被重启后移」（与 07 记录的锚点比）。

用法：python code/07b_lag_probe.py [--wait-min 70] [--baseline 2026-10-25T06:00:00Z]
产出：out/07b_水位探针.json
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

STATUS = cm.BACKEND / "locks" / "paper_runtime_3a_status.json"
LEDGER = cm.BASE / "数据" / "paper" / "paper_3a.sqlite3"


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


def db_1h_max() -> int | None:
    con = cm.market_conn()
    try:
        r = con.execute("SELECT MAX(open_time) FROM candles WHERE exchange='binance' "
                        "AND market='um' AND timeframe='1h' AND is_closed=1").fetchone()
        return int(r[0]) if r and r[0] is not None else None
    finally:
        con.close()


def ledger_open() -> int | None:
    import sqlite3
    if not LEDGER.exists():
        return None
    con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT COUNT(*) FROM paper_position "
                        "WHERE status='open' AND COALESCE(voided,0)=0").fetchone()
        return int(r[0]) if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def main() -> int:
    argv = sys.argv[1:]
    wait_min = 70
    baseline = None
    settle_sec = 180
    now_only = False
    for i, a in enumerate(argv):
        if a == "--wait-min" and i + 1 < len(argv):
            wait_min = int(argv[i + 1])
        if a == "--baseline" and i + 1 < len(argv):
            baseline = cm.parse_utc_ms(argv[i + 1])
        if a == "--settle-sec" and i + 1 < len(argv):
            settle_sec = int(argv[i + 1])
        if a == "--now":
            now_only = True
    if baseline is None:
        baseline = db_1h_max()
    print(f"[cfg] 基线 1h open={cm.utc(baseline)} 最多等 {wait_min} 分钟 "
          f"settle={settle_sec}s now_only={now_only}")

    st = read_status()
    se = st.get("signal_engine") or {}
    anchor0 = ((se.get("bootstrap") or {}).get("history") or {}).get("state_anchor_utc")
    ready0 = (se.get("bootstrap") or {}).get("ready")
    print(f"[pre] 锚点={anchor0} ready={ready0}")

    cur = db_1h_max()
    if not now_only:
        deadline = time.time() + wait_min * 60
        while time.time() < deadline:
            cur = db_1h_max()
            if cur is not None and baseline is not None and cur > baseline:
                break
            time.sleep(20)
        print(f"[wait] 库内 1h open 已到 {cm.utc(cur)}")
        # **关键**：新 1h 刚入库时，运行时还没到它的轮询周期，此刻必然 lag=1。
        # 必须等一个处理周期再看，否则会把「正常的采样时序」误判成「漏处理」。
        print(f"[settle] 等 {settle_sec}s 让运行时处理这根")
        time.sleep(settle_sec)
        cur = db_1h_max()
    print(f"[now] 库内 1h open={cm.utc(cur)}")

    st = read_status()
    se = st.get("signal_engine") or {}
    wm = se.get("watermark_check") or {}
    wper = wm.get("per_symbol") or {}
    hist = ((se.get("bootstrap") or {}).get("history") or {})
    anchor1 = hist.get("state_anchor_utc")

    snap = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "baseline_1h_open_utc": cm.utc(baseline),
        "db_1h_max_open_utc": cm.utc(cur),
        "watermark_1h_utc": cm.utc(se.get("watermark_1h")),
        "watermark_4h_utc": cm.utc(se.get("watermark_4h")),
        "processed_1h_max_utc": cm.utc(wm.get("processed_1h_max")),
        "db_1h_max_in_status_utc": cm.utc(wm.get("db_1h_max")),
        "lag_1h_bars": wm.get("lag_1h_bars"),
        "lag_4h_bars": wm.get("lag_4h_bars"),
        "aligned_1h": wm.get("aligned_1h"),
        "aligned_4h": wm.get("aligned_4h"),
        "per_symbol": {
            "n": wper.get("n"),
            "lag_1h_max": wper.get("lag_1h_max"),
            "lag_1h_histogram": wper.get("lag_1h_histogram"),
            "n_lag_1h_gt1": wper.get("n_lag_1h_gt1"),
            "lagging": wper.get("lagging"),
        },
        "n_engines_with_1h": wm.get("n_engines_with_1h"),
        "n_engines_with_4h": wm.get("n_engines_with_4h"),
        "replay_mode": hist.get("replay_mode"),
        "state_anchor_utc": anchor1,
        "anchor_before_utc": anchor0,
        "state_counter": se.get("state_counter"),
        "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
        "funnel_history": (se.get("funnel") or {}).get("history"),
        "candidates_since_start": (se.get("funnel") or {}).get("candidates_since_start"),
        "signals_generated_total": se.get("signals_generated_total"),
        "scan_errors_total": se.get("scan_errors_total"),
        "skipped_reasons": se.get("skipped_reasons"),
        "n_per_symbol_state": len(se.get("per_symbol_state") or {}),
        "per_symbol_state": se.get("per_symbol_state") or {},
        "ledger_open_positions": ledger_open(),
    }

    checks = {
        # `--now` 模式下基线就是当前值，`cur > baseline` 恒不成立 —— 那是模式的
        # 必然结果，不是缺陷。所以这里只要求「读到了且不早于基线」。
        "水位已过基线": (cur is not None and baseline is not None
                   and (cur >= baseline if now_only else cur > baseline)),
        "重建模式=绝对锚点": snap["replay_mode"] == "absolute_anchor",
        "锚点未后移": (anchor1 is not None and anchor1 == anchor0),
        "逐币lag_max已读到": snap["per_symbol"]["lag_1h_max"] is not None,
        "逐币lag_max<=1": (snap["per_symbol"]["lag_1h_max"] is not None
                        and int(snap["per_symbol"]["lag_1h_max"]) <= 1),
        "全池无落后币": ((snap["per_symbol"]["lag_1h_histogram"] or {}).get("0")
                    == snap["per_symbol"]["n"]),
        "处理水位追平库内": (wm.get("processed_1h_max") is not None
                      and wm.get("db_1h_max") is not None
                      and int(wm["processed_1h_max"]) == int(wm["db_1h_max"])),
        "逐币阶段齐备": snap["n_per_symbol_state"] == (ready0 or -1) and ready0,
        "候选计数可查": snap["candidates_since_start"] is not None,
        "扫描无错误": (snap["scan_errors_total"] in (0, None)),
    }
    snap["checks"] = checks
    snap["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    cm.dump("07b_水位探针.json", snap)

    print(f"[水位] processed={snap['processed_1h_max_utc']} db={snap['db_1h_max_in_status_utc']} "
          f"lag_1h_bars={snap['lag_1h_bars']}")
    print(f"[逐币] n={snap['per_symbol']['n']} lag_max={snap['per_symbol']['lag_1h_max']} "
          f"hist={snap['per_symbol']['lag_1h_histogram']}")
    print(f"[阶段] {snap['state_counter']}")
    print(f"[漏斗] 启动后 {snap['funnel_since_start']}  候选={snap['candidates_since_start']}")
    print(f"[verdict] {snap['verdict']}")
    for k, v in checks.items():
        print(f"    {'OK ' if v else 'BAD'} {k}")
    return 0 if snap["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
