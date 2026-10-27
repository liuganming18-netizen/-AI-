# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜成交与止损行为核验（只读 + 隔离账本，不碰生产）。

跑法：
    /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
        evidence/ACCUM-EARLY-01/scripts/06_成交止损行为核验.py

设计原则
--------
1. **行为优先于字段**：能跑出结果的都跑，跑不了的（需要构造完整信号契约的）
   明确标 `strength=code_assertion`，并写清「未跑到哪一步」。
2. **隔离**：`PAPER_LEDGER_DIR` 指向临时目录，账本自动建档；生产
   `数据/paper/paper_3a.sqlite3` 只读、不打开写。
3. **不静默**：任何异常都落进 `errors`，不吞。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"

TMP = Path(tempfile.mkdtemp(prefix="acc_exec_"))
os.environ["PAPER_LEDGER_DIR"] = str(TMP / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ.setdefault("WX_HISTORY_SOURCE", "hot")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))      # `策略` 包在工程根，执行链取整要用到
sys.dont_write_bytecode = True

H, M15 = 3_600_000, 900_000
T0 = 1_700_006_400_000          # 2023-11-15T00:00:00Z，纯合成锚点


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def hour_bars(n=60):
    out = []
    for i in range(n):
        o = 100.0 + i * 0.1
        c = o + (0.4 if i % 2 else -0.3)
        out.append({"open_time": T0 + i * H, "close_time": T0 + i * H + H - 1,
                    "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2,
                    "close": c, "volume": 100.0, "quote_volume": 2_000_000.0})
    return out


def bars15(hours, symbol="SYN"):
    out = []
    for h in hours:
        for k in range(4):
            ot = int(h["open_time"]) + k * M15
            out.append({"exchange": "binance", "market": "um", "symbol": symbol,
                        "timeframe": "15m", "is_closed": 1,
                        "open_time": ot, "close_time": ot + M15 - 1,
                        "open": float(h["open"]), "high": float(h["high"]),
                        "low": float(h["low"]), "close": float(h["close"]),
                        "volume": 10.0, "quote_volume": 500_000.0})
    return out


def mem_market(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE candles(exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,"
        "is_closed INT, open_time INT, close_time INT, open REAL, high REAL, low REAL,"
        "close REAL, volume REAL, quote_volume REAL)")
    for b in rows:
        con.execute("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (b["exchange"], b["market"], b["symbol"], b["timeframe"],
                     b["is_closed"], b["open_time"], b["close_time"], b["open"],
                     b["high"], b["low"], b["close"], b["volume"], b["quote_volume"]))
    con.commit()
    return con


def main() -> int:
    rep = {
        "probe": "ACCUM-EARLY-01/06_成交止损行为核验",
        "generated_at_utc": iso(time.time() * 1000),
        "isolation": {"PAPER_LEDGER_DIR": os.environ["PAPER_LEDGER_DIR"],
                      "production_touched": False},
        "sections": {},
        "errors": [],
    }
    try:
        import paper_engine as PE
        import live_store as L
    except Exception as exc:                                  # noqa: BLE001
        rep["errors"].append({"stage": "import", "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 2
    LW = None
    try:
        import live_worker as LW                          # noqa: F401
        rep["live_worker_importable"] = True
    except Exception as exc:                              # noqa: BLE001
        rep["live_worker_importable"] = False
        rep["live_worker_import_error"] = f"{type(exc).__name__}: {exc}"
        rep["live_worker_note"] = (
            "live_worker 顶层 `import _warmup43`，该冻结研究件不在本工程内 ⇒ "
            "该模块在本工程**无法导入**，其中的 240 小时到期出场分支不构成生效路径。")

    params = PE.approved_params()
    rep["approved_params"] = {k: params.get(k) for k in (
        "fee_each_side", "slippage_each_side", "signal_execution_delay_minutes",
        "signal_exit_delay_minutes", "max_holding_hours", "stoploss_s", "trail_d",
        "max_positions", "risk_per_trade_equity_fraction")}
    rep["approved_params_source"] = params.get("_source")

    # ═══ A. 确认时刻 → +15 分钟首根已闭合 15m 开盘成交 ═══════════════
    # 标签一律用算出来的实际时刻，不写死「12:00／12:15」——
    # 写死过一次，结果断言标签与实际数据对不上（11:00／11:15），
    # 读者会以为断言在测别的东西。
    try:
        hours = hour_bars(20)
        b15 = bars15(hours)
        mkt = mem_market(b15)
        # 决策时刻 = 第 10 根 1h 的收盘时刻（整点）
        confirm_ms = int(hours[10]["close_time"]) + 1
        delay_ms = int(params["signal_execution_delay_minutes"]) * 60_000
        earliest = confirm_ms + delay_ms
        bars = PE.closed_15m(mkt, "SYN", confirm_ms - H, confirm_ms + 3 * H)
        eligible = [b for b in bars if int(b["open_time"]) >= earliest]
        chosen = eligible[0] if eligible else None
        fill_price = float(chosen["open"]) if chosen else None
        rep["sections"]["A_执行时刻"] = {
            "strength": "behavior_on_synthetic_bars",
            "confirm_utc": iso(confirm_ms),
            "earliest_fill_utc": iso(earliest),
            "chosen_bar_open_utc": iso(int(chosen["open_time"])) if chosen else None,
            "chosen_bar_open": float(chosen["open"]) if chosen else None,
            "chosen_bar_close": float(chosen["close"]) if chosen else None,
            "fill_price": fill_price,
            "assertions": {
                "成交时刻 >= 确认+15分钟": bool(chosen and int(chosen["open_time"]) >= earliest),
                f"成交时刻 == 确认+15分钟（{iso(earliest)}）而非确认时刻": bool(
                    chosen and int(chosen["open_time"]) == earliest),
                # 先证明「开盘价与收盘价不同」，这条断言才有区分力；
                # 曾经写成 open 与 open 自比 ⇒ 恒真，等于没测。
                "该根开盘价与收盘价不同（断言本身可区分）": bool(
                    chosen and abs(float(chosen["open"]) - float(chosen["close"])) > 1e-12),
                "记录的成交价 == 该根开盘价（不是收盘价）": bool(
                    chosen and fill_price is not None
                    and abs(fill_price - float(chosen["open"])) < 1e-12
                    and abs(fill_price - float(chosen["close"])) > 1e-12),
                f"未用确认时刻（{iso(confirm_ms)}）那根": bool(
                    chosen and int(chosen["open_time"]) != confirm_ms),
            },
            "说明": "本段验「选哪根 + 取开盘价」；引擎实际成交价与真实行情开盘价的一致性见 07 脚本"
                    "（entry_price_raw 与原始 15m bar 开盘价逐个相同）。",
            "fill_policy": {k: PE.FILL_POLICY.get(k) for k in
                            ("fill_price", "no_fill_before", "on_missed_moment",
                             "max_fill_lag_seconds")},
        }
        rep["sections"]["A_执行时刻"]["verdict"] = (
            "pass" if all(rep["sections"]["A_执行时刻"]["assertions"].values())
            else "fail")
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "A", "error": f"{type(exc).__name__}: {exc}"})

    # ═══ B. 初始止损 5% + 跟踪只上移、向后生效 ═════════════════════════
    try:
        con = L.paper_conn("3a")
        entry_px = 100.0
        entry_ts = T0 + 12 * H
        con.execute(
            """INSERT INTO paper_position(position_id, account_id, symbol, qty,
                   entry_price, entry_ts, status, arm, stop_price, atr_at_entry,
                   stoploss_s, trail_d, model_version, origin, market_source)
               VALUES(1,'paper43_3a','SYN',1.0,?,?, 'open','3a',?,?,?,?,?,'replay','synthetic')""",
            (entry_px, entry_ts, round(entry_px * (1 - float(params["stoploss_s"])), 8),
             1.0, float(params["stoploss_s"]), float(params["trail_d"]),
             PE.MODEL_VERSION))
        con.commit()
        con.close()

        # 第一根新收盘 4h：low=98 → 候选 98*0.95=93.1（低于初始 95 ⇒ 不上移）
        c1 = {"symbol": "SYN", "open_time": entry_ts, "close_time": entry_ts + 4 * H - 1,
              "open": 100.0, "high": 101.0, "low": 98.0, "close": 99.0}
        r1 = PE.advance_position_trailing("3a", 1, c1, now_ms=entry_ts + 4 * H,
                                          decision_at=entry_ts + 4 * H)
        # 第二根：low=104 → 候选 98.8（高于初始 95 ⇒ 上移）
        c2 = {"symbol": "SYN", "open_time": entry_ts + 4 * H,
              "close_time": entry_ts + 8 * H - 1, "open": 104.0, "high": 106.0,
              "low": 104.0, "close": 105.0}
        r2 = PE.advance_position_trailing("3a", 1, c2, now_ms=entry_ts + 8 * H,
                                          decision_at=entry_ts + 8 * H)
        # 第三根：low=90 → 候选 85.5（更低 ⇒ 不得下移）
        c3 = {"symbol": "SYN", "open_time": entry_ts + 8 * H,
              "close_time": entry_ts + 12 * H - 1, "open": 105.0, "high": 105.0,
              "low": 90.0, "close": 92.0}
        r3 = PE.advance_position_trailing("3a", 1, c3, now_ms=entry_ts + 12 * H,
                                          decision_at=entry_ts + 12 * H)

        con = L.paper_conn("3a")
        cur = con.execute("SELECT stop_price FROM paper_position WHERE position_id=1").fetchone()
        hist = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%stop%'"
        ).fetchall()
        con.close()
        final_stop = float(cur["stop_price"])
        rep["sections"]["B_止损"] = {
            "strength": "behavior_isolated_ledger",
            "initial_stop": round(entry_px * (1 - float(params["stoploss_s"])), 8),
            "initial_stop_rule": f"实际入场价 × (1 - {params['stoploss_s']})",
            "r1": r1, "r2": r2, "r3": r3,
            "final_stop": final_stop,
            "stop_tables": [h["name"] for h in hist],
            "assertions": {
                "初始止损=入场价×0.95": abs(round(entry_px * 0.95, 8)
                                          - round(entry_px * (1 - float(params["stoploss_s"])), 8)) < 1e-9,
                "低点更低时不下移": final_stop > 90.0 * 0.95 + 1e-6,
                "高点抬升后止损上移": final_stop >= 104.0 * 0.95 - 1e-6,
                "三次推进均 ok": all(x.get("ok") for x in (r1, r2, r3)),
            },
        }
        rep["sections"]["B_止损"]["verdict"] = (
            "pass" if all(rep["sections"]["B_止损"]["assertions"].values()) else "fail")
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "B", "error": f"{type(exc).__name__}: {exc}"})

    # ═══ C. 240 小时：先判「哪条路径真的在跑」，再谈行为 ══════════════
    try:
        pe_src = (BACKEND / "paper_engine.py").read_text(encoding="utf-8")
        tail = pe_src.split("def advance_position_trailing")[1][:9000]
        trailing_has_expiry = "expiry_exit_ts" in tail or "max_holding_hours" in tail
        # 谁调用了 expiry_exit_ts？只数源码引用，不做推断
        callers = []
        for p in BACKEND.glob("*.py"):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if "expiry_exit_ts(" in txt and p.name != "paper_engine.py":
                callers.append(p.name)
        # 行为：把持仓推进到 241 小时之后，看跟踪推进会不会强平
        con = L.paper_conn("3a")
        old_entry = T0 + 5 * H
        con.execute(
            """INSERT OR REPLACE INTO paper_position(position_id, account_id, symbol,
                   qty, entry_price, entry_ts, status, arm, stop_price, atr_at_entry,
                   stoploss_s, trail_d, model_version, origin, market_source)
               VALUES(2,'paper43_3a','SYN',1.0,100.0,?,'open','3a',95.0,1.0,0.05,0.05,?,
                      'replay','synthetic')""",
            (old_entry, PE.MODEL_VERSION))
        con.commit()
        con.close()
        far_ms = old_entry + 241 * H
        cfar = {"symbol": "SYN", "open_time": far_ms - 4 * H,
                "close_time": far_ms - 1, "open": 100.0, "high": 101.0,
                "low": 99.0, "close": 100.0}
        r_far = PE.advance_position_trailing("3a", 2, cfar, now_ms=far_ms,
                                            decision_at=far_ms)
        con = L.paper_conn("3a")
        row = con.execute(
            "SELECT status, exit_reason, exit_ts FROM paper_position WHERE position_id=2"
        ).fetchone()
        con.close()
        rep["sections"]["C_240小时"] = {
            "strength": "behavior_isolated_ledger + code_assertion",
            "生效路径判定": {
                "运行中的入口": "unified_runtime → paper_engine.advance_pending / "
                                "advance_position_trailing / check_position_exit_on_bar",
                "advance_position_trailing 含到期出场分支": trailing_has_expiry,
                "调用 expiry_exit_ts 的模块": callers,
                "live_worker 可导入": rep.get("live_worker_importable"),
            },
            "行为实测": {
                "推进到入场后 241 小时": True,
                "推进调用返回": r_far,
                "position_status": row["status"],
                "exit_reason": row["exit_reason"],
                "exit_ts_utc": iso(row["exit_ts"]) if row["exit_ts"] else None,
                "结果": ("未强平（仓位仍 open）"
                        if (row["status"] == "open" and r_far.get("ok"))
                        else f"推进失败或被强平：{r_far.get('reason') or row['exit_reason']}"),
            },
            "conclusion": (
                "十天强平**不在生效路径上**：paper_engine 的跟踪推进没有到期分支，"
                "实测推进到 241 小时仓位仍 open。但 `live_worker.py` 里保留着 240 小时"
                "到期出场分支，且该模块在本工程**无法导入**（缺 `_warmup43`）⇒ 一旦有人"
                "把这个模块跑起来，十天强平会立刻生效。保留字段本身没有触发强平，"
                "但代码分支还在，属于待清理的隐患，不是「已满足不设十天强平」。"),
        }
        rep["sections"]["C_240小时"]["verdict"] = (
            "not_on_active_path_but_latent"
            if (not trailing_has_expiry and row["status"] == "open"
                and r_far.get("ok"))
            else ("unexpected_or_call_failed"))
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "C", "error": f"{type(exc).__name__}: {exc}"})

    # ═══ D. 字段级防未来数据 ══════════════════════════════════════════
    try:
        hours = hour_bars(10)
        b15 = bars15(hours)
        mkt = mem_market(b15)
        cut = int(b15[8]["open_time"]) + M15          # 第 9 根刚开盘
        bars = PE.closed_15m(mkt, "SYN", T0, cut)
        last_close = max(int(b["close_time"]) for b in bars) if bars else None
        rep["sections"]["D_防未来数据"] = {
            "strength": "behavior_on_synthetic_bars",
            "decision_ms": cut, "decision_utc": iso(cut),
            "bars_returned": len(bars),
            "last_bar_close_utc": iso(last_close),
            "assertions": {
                "返回的bar全部已闭合": all(int(b["close_time"]) <= cut for b in bars),
                "不含决策时刻尚未收盘的bar": last_close is not None and last_close <= cut,
                "只看开盘时间不够": "本项同时核了 close_time，不只比 open_time",
            },
            "field_level_check": {
                "candle": "close_time <= 决策时刻",
                "oi": "ts + 5min <= 决策时刻（见 03 脚本）",
                "funding": "按结算事件时刻（settled）",
                "available_at": "为空即标记缺失，不用 close_time 冒充",
            },
        }
        rep["sections"]["D_防未来数据"]["verdict"] = (
            "pass" if all(list(rep["sections"]["D_防未来数据"]["assertions"].values())[:2])
            else "fail")
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "D", "error": f"{type(exc).__name__}: {exc}"})

    # ═══ E. 入场模型与止损模型分离（用生效路径的逐根退出判定）═════════
    try:
        def _mk_pos(pid, stop, entry_ts, symbol="SYN"):
            con = L.paper_conn("3a")
            con.execute(
                """INSERT OR REPLACE INTO paper_position(position_id, account_id,
                       symbol, qty, entry_price, entry_ts, status, arm, stop_price,
                       atr_at_entry, stoploss_s, trail_d, model_version, origin,
                       market_source, earliest_fill_ms)
                   VALUES(?, 'paper43_3a', ?, 1.0, 100.0, ?, 'open', '3a', ?, 1.0,
                          0.05, 0.05, ?, 'replay', 'synthetic', ?)""",
                (pid, symbol, entry_ts, stop, PE.MODEL_VERSION, entry_ts))
            con.commit()
            con.close()

        # 缺口：开盘 90 直接低于止损 95
        _mk_pos(7, 95.0, T0 - H, symbol="BTCUSDT")
        gap_bar = {"symbol": "BTCUSDT", "open_time": T0, "close_time": T0 + M15 - 1,
                   "open": 90.0, "high": 92.0, "low": 89.0, "close": 91.0}
        r_gap = PE.check_position_exit_on_bar("3a", 7, gap_bar,
                                             now_ms=T0 + M15, decision_at=T0 + M15)
        # 盘中触及：开盘 96 高于止损 95，bar 内低点 94
        _mk_pos(8, 95.0, T0 - H, symbol="BTCUSDT")
        touch_bar = {"symbol": "BTCUSDT", "open_time": T0, "close_time": T0 + M15 - 1,
                     "open": 96.0, "high": 97.0, "low": 94.0, "close": 95.5}
        r_touch = PE.check_position_exit_on_bar("3a", 8, touch_bar,
                                               now_ms=T0 + M15, decision_at=T0 + M15)

        def _raw(r):
            er = (r or {}).get("exit_result") or {}
            v = er.get("raw_price")
            return None if v is None else float(v)

        p_gap, p_touch = _raw(r_gap), _raw(r_touch)
        rep["sections"]["E_模型分离"] = {
            "strength": "behavior_isolated_ledger",
            "入场模型": {
                "规则": "确认时刻 + 15 分钟之后首根已闭合 15m bar 的**开盘价**",
                "实测": "见 A 段",
            },
            "止损模型": {
                "函数": "paper_engine.check_position_exit_on_bar",
                "触发条件": "bar 低点 <= 当前止损",
                "缺口": {"exit_price": p_gap, "exit_triggered": (r_gap or {}).get("exit_triggered"),
                         "对照": "开盘 90 已低于止损 95；若按缺口模型应以 90 成交"},
                "盘中触及": {"exit_price": p_touch,
                             "exit_triggered": (r_touch or {}).get("exit_triggered"),
                             "对照": "开盘 96 高于止损 95，低点 94 下穿 ⇒ 应以 95 成交"},
            },
            "assertions": {
                "盘中触及按止损价成交": bool(p_touch is not None and abs(p_touch - 95.0) < 1e-6),
                "缺口也按止损价成交（未按开盘价）": bool(
                    p_gap is not None and abs(p_gap - 95.0) < 1e-6),
                "两种情形成交价相同 ⇒ 缺口未单独建模": bool(
                    p_gap is not None and p_touch is not None
                    and abs(p_gap - p_touch) < 1e-9),
            },
            "缺陷": ("生效路径的止损成交一律取止损价，**没有**按缺口开盘价做更差处理；"
                    "`live_worker._scan_position_exit` 里有缺口处理，但该模块在本工程"
                    "无法导入。⇒ 真实跳空时会高估成交价（偏乐观），属未修缺陷，"
                    "改它会改盈亏口径，需裁决后修。"),
        }
        rep["sections"]["E_模型分离"]["verdict"] = (
            "partial_gap_unmodeled"
            if rep["sections"]["E_模型分离"]["assertions"]["缺口也按止损价成交（未按开盘价）"]
            else "pass")
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "E", "error": f"{type(exc).__name__}: {exc}"})

    # ═══ F. 实时恢复 vs 历史回放 ══════════════════════════════════════
    try:
        src = (BACKEND / "paper_engine.py").read_text(encoding="utf-8")
        rep["sections"]["F_恢复与回放"] = {
            "strength": "code_assertion",
            "回放允许指定时钟": 'is_replay = (origin in ("replay", "test")' in src,
            "恢复禁止回填": "reject_missed_fill_moment_no_backfill" in src,
            "未来成交挂单化": "paper_pending_advanced_by_real_process_clock" in src,
            "说明": "回放（origin=replay）可用历史时钟按历史顺序模拟；"
                    "实时恢复（origin=live/recovered）错过成交时刻只拒单不回填。"
                    "两者不能互相代替，也不能一概称「补成交即未来数据」。",
            "未跑到": "open_position 全链路（需构造完整信号契约），本段为代码断言",
        }
        rep["sections"]["F_恢复与回放"]["verdict"] = (
            "pass" if (rep["sections"]["F_恢复与回放"]["回放允许指定时钟"]
                       and rep["sections"]["F_恢复与回放"]["恢复禁止回填"]) else "fail")
    except Exception as exc:                                   # noqa: BLE001
        rep["errors"].append({"stage": "F", "error": f"{type(exc).__name__}: {exc}"})

    rep["tmp_dir"] = str(TMP)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "06_成交止损行为核验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
