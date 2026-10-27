# -*- coding: utf-8 -*-
"""04｜在**隔离模拟环境**里跑完整执行链：候选 → 订单 → 成交 → 持仓 → 平仓 → 已实现。

四个 pass（各自一套隔离账本，互不干扰；生产账本一行都不碰）：

  `prod_epoch`   —— **严格保留生产 paper epoch**（不传 `epoch_ms`）。
                    预期：65/65 被 `before_paper_epoch_no_backfill` 拒绝
                    ⇒ 这是「真实规则下没有自然成交」的机械证据，**不是缺陷**。
  `replay`       —— 隔离回放：`origin="replay"` + 显式 `epoch_ms` = 回放窗口起点
                    （引擎为 replay 提供的正式参数，不是改规则、不是改门槛）。
                    跑通候选→订单→成交→持仓→跟踪止损→平仓→已实现。
  `gate_closed`  —— 闸门关闭 ⇒ `new_entry_blocked_pending_closure_review`（有效拒绝）。
  `duplicate`    —— 同一 `signal_uid` 重复提交 ⇒ `duplicate_signal_uid`，
                    账本不得新增订单/成交（重复输入不重复下单）。

纪律：
  · 所有下单/平仓/跟踪/出场检查**一律调用 `paper_engine` 既有函数**，本脚本不含撮合逻辑；
  · `now_ms` 一律取「该 15m bar 的 close_time」（已收盘才可决策）⇒ 无前视；
  · 拒绝原因原样记录，**不改写、不重试绕过**。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

ARM = "3a"
ACCOUNT = f"paper43_{ARM}"
#: 隔离回放的 epoch（= 本单回放窗口起点，早于全部候选）。
# 为什么需要：生产 epoch = 2026-10-14T01:00Z，而 180 天里 65 个真实候选**全部早于它**
# ⇒ 严格保留生产 epoch 时不可能有任何成交（见 `prod_epoch` pass）。
# 隔离账本没有「在跑的实盘账户」可保护，故按引擎既有的 `epoch_ms` 参数把下限下移到回放窗口起点。
# **这是隔离回放，不是自然成交**；报告里必须分开写。
REPLAY_EPOCH_MS = cm.parse_iso("2026-06-01T00:00:00Z")


def _con(ledger_dir: Path) -> sqlite3.Connection:
    p = ledger_dir / f"paper_{ARM}.sqlite3"
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _dump_position(ledger_dir: Path, pid: int) -> dict:
    c = _con(ledger_dir)
    pos = c.execute("SELECT * FROM paper_position WHERE position_id=?", (pid,)).fetchone()
    ords = [dict(r) for r in c.execute(
        "SELECT * FROM paper_order WHERE event_uid IN "
        "(SELECT event_uid FROM paper_order WHERE symbol=? ) ORDER BY order_id", (pos["symbol"],))]
    fills = [dict(r) for r in c.execute(
        "SELECT * FROM paper_fill WHERE position_id=? ORDER BY fill_id", (pid,))]
    real = [dict(r) for r in c.execute(
        "SELECT * FROM paper_realized WHERE position_id=?", (pid,))]
    stops = [dict(r) for r in c.execute(
        "SELECT * FROM paper_stop_history WHERE position_id=? ORDER BY id", (pid,))]
    fund = [dict(r) for r in c.execute(
        "SELECT * FROM paper_funding WHERE position_id=? ORDER BY id", (pid,))]
    c.close()
    return {"position": dict(pos) if pos else None, "fills": fills,
            "realized": real, "stop_history": stops, "funding": fund,
            "orders_same_symbol": ords}


def _case_row(cand, res, ledger_dir, extra=None) -> dict:
    row = {
        "symbol": cand["symbol"],
        "candidate_id": cand["candidate_id"],
        "cross_run_key": cand["cross_run_key"],
        "snapshot_time_utc": cand["snapshot_time_utc"],
        "available_at_utc": cand["available_at_utc"],
        "execution_earliest_utc": cand["execution_earliest_utc"],
        "box_atr_a": cand["box_atr_a"],
        "initial_stop_price": cand["initial_stop_price"],
        "breakout_close": cand["breakout_close"],
        "open_ok": bool(res.get("ok")),
        "open_reason": res.get("reason"),
        "position_id": res.get("position_id"),
        "engine_order_id": res.get("order_id"),
        "exchange_order_id": None,
        "exchange_order_absent_reason": "模拟盘不下真单，无交易所回报（见 01 段 A）",
        "open_detail": {k: v for k, v in res.items()
                        if k in ("reason", "position_id", "order_id", "entry_price", "qty",
                                 "stop_price", "signal_uid", "model_version",
                                 "bar_open_time", "fill_price", "notional", "fee",
                                 "pending_id", "epoch_ms", "first_request_ms",
                                 "order_request_ms", "earliest_fill_ms", "attempt_ms",
                                 "signal_bar_right_ms", "lag_ms", "note")},
    }
    if extra:
        row.update(extra)
    return row


def drive_exit(PE, sym: str, pid: int, ledger_dir: Path, con_mkt,
               bars15: list[dict], bars4: list[dict], entry_ts: int,
               max_steps: int = 4000) -> dict:
    """按**生产同一顺序**推进：4h 收盘 → 更新跟踪线；15m 收盘 → 检查出场。"""
    # 只从入场时刻起扫（14k 根全扫 × 65 笔会被前台超时杀掉；切片不改变语义）
    import bisect
    _o15 = [int(b["open_time"]) for b in bars15]
    i15 = bisect.bisect_left(_o15, int(entry_ts))
    i4 = 0
    n15 = n4 = 0
    last = {}
    gap_cases = []
    for b15 in bars15[i15:]:
        now = int(b15["close_time"])
        # ① 4h 跟踪（只喂「已收盘」的 4h；close_time <= now 才算收盘）
        while i4 < len(bars4) and int(bars4[i4]["close_time"]) <= now:
            b4 = dict(bars4[i4]); b4["symbol"] = sym
            adv = PE.advance_position_trailing(ARM, pid, candle_4h=b4,
                                               now_ms=now, decision_at=now)
            i4 += 1
            n4 += 1
            last["advance"] = adv
            if adv.get("exit_triggered"):
                return {"closed_by": "4h_trailing_stop", "n_advance": n4, "n_bar15": n15,
                        "last": last, "gap_cases": gap_cases}
        # ② 15m 出场检查
        b = dict(b15); b["symbol"] = sym
        r = PE.check_position_exit_on_bar(ARM, pid, bar_detail=b, now_ms=now, decision_at=now)
        n15 += 1
        last["exit_check"] = r
        if r.get("exit_triggered"):
            # 记录跳空：bar 开盘已穿止损，而引擎仍按 stop 价成交（已知口径偏差，只记录不改）
            cur_stop = float(r.get("cur_stop") or 0)
            if cur_stop > 0 and float(b["open"]) < cur_stop:
                gap_cases.append({"bar_open_time": int(b["open_time"]),
                                  "bar_open": float(b["open"]), "stop": cur_stop})
            return {"closed_by": "15m_stop", "n_advance": n4, "n_bar15": n15,
                    "last": last, "gap_cases": gap_cases}
        if n15 >= max_steps:
            break
    return {"closed_by": "not_closed_data_exhausted", "n_advance": n4, "n_bar15": n15,
            "last": last, "gap_cases": gap_cases}


def run_pass(tag: str, gate_allow: bool, epoch_mode: str, dup_of: str | None = None,
             limit: int | None = None) -> dict:
    """跑一个 pass。`epoch_mode`: `prod`（不传 epoch_ms）/ `replay`（传 REPLAY_EPOCH_MS）。"""
    ledger_dir = cm.ISO_LEDGER_DIR / tag
    if ledger_dir.exists():
        for p in ledger_dir.glob("paper_3a.sqlite3*"):
            p.unlink()
    env = cm.iso_env(gate_allow=gate_allow, ledger_sub=tag)
    iso = cm.assert_isolated()

    import live_store as L
    import paper_engine as PE

    scan = cm.read_json(cm.OUT / "02_候选扫描.json")
    cands = sorted(scan["candidates"], key=lambda c: (c["available_at_ms"], c["symbol"]))
    if limit:
        cands = cands[:limit]
    if dup_of:
        cands = [c for c in cands if c["candidate_id"] == dup_of]
        if not cands:
            raise SystemExit(f"dup_of 未在扫描结果中找到：{dup_of}")

    con_mkt = L.market_conn()
    t0 = time.time()
    rows, detail = [], {}
    for cand in cands:
        sym = cand["symbol"]
        # ① 先算「第一根合格已闭合 15m bar」，把它当作本次尝试时刻（回放时钟）
        earliest = max(int(cand["snapshot_time_ms"]), int(cand["available_at_ms"])) + cm.M15
        bar = PE.bar_at_or_after(con_mkt, sym, earliest)
        if bar is None:
            rows.append(_case_row(cand, {"ok": False, "reason": "no_closed_15m_after_earliest"},
                                  ledger_dir))
            continue
        fill_bar_open = int(bar["open_time"])
        kw = dict(signal_uid=cand["candidate_id"], direction="long",
                  atr=float(cand["box_atr_a"]), stop_price=float(cand["initial_stop_price"]),
                  now_ms=fill_bar_open, event_time=int(cand["snapshot_time_ms"]),
                  available_at=int(cand["available_at_ms"]),
                  requested_at=int(cand["available_at_ms"]),
                  origin="replay", signal_payload=cand)
        if epoch_mode == "replay":
            kw["epoch_ms"] = REPLAY_EPOCH_MS
        res = PE.open_position(ARM, sym, **kw)

        if not res.get("ok"):
            rows.append(_case_row(cand, res, ledger_dir,
                                  extra={"attempt_fill_bar_utc": cm.iso(fill_bar_open)}))
            continue

        pid = int(res["position_id"])
        # ② 推进到平仓
        con = cm.market_conn()
        bars15 = cm.load_bars(con, sym, "15m")
        bars4 = cm.load_bars(con, sym, "4h")
        con.close()
        d = drive_exit(PE, sym, pid, ledger_dir, con_mkt, bars15, bars4, fill_bar_open)
        snap = _dump_position(ledger_dir, pid)
        pos = snap["position"] or {}
        # 该笔的订单（开仓 + 平仓）：用 event_uid / signal_uid 真实关联
        c = _con(ledger_dir)
        ords = [dict(r) for r in c.execute(
            "SELECT * FROM paper_order WHERE account_id=? AND (event_uid=? OR event_uid LIKE ?) "
            "ORDER BY order_id", (ACCOUNT, cand["candidate_id"], f"exit:{ARM}:{pid}:%"))]
        c.close()
        rows.append(_case_row(cand, res, ledger_dir, extra={
            "attempt_fill_bar_utc": cm.iso(fill_bar_open),
            "position_status": pos.get("status"),
            "exit_ts_utc": cm.iso(pos.get("exit_ts")),
            "exit_price": pos.get("exit_price"),
            "exit_reason": pos.get("exit_reason"),
            "entry_price": pos.get("entry_price"),
            "qty": pos.get("qty"),
            "entry_fee": pos.get("entry_fee"),
            "exit_fee": pos.get("exit_fee"),
            "stop_price": pos.get("stop_price"),
            "closed_by": d["closed_by"],
            "n_advance_4h": d["n_advance"], "n_bar15": d["n_bar15"],
            "gap_cases": d["gap_cases"],
            "engine_orders": ords,
            "fills": snap["fills"], "realized": snap["realized"],
            "stop_history": snap["stop_history"], "funding": snap["funding"],
        }))
        detail[cand["candidate_id"]] = {"drive": {k: v for k, v in d.items() if k != "last"},
                                        "snapshot": snap}
    con_mkt.close()

    # 账本全量（供 05 段核对）
    c = _con(ledger_dir)
    ledger = {}
    for t in ("paper_order", "paper_fill", "paper_position", "paper_realized",
              "paper_blocked", "paper_processed", "paper_pending", "paper_stop_history",
              "paper_funding", "paper_request_anchor"):
        try:
            ledger[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t}")]
        except Exception as exc:                                    # noqa: BLE001
            ledger[t] = [{"__error__": str(exc)}]
    c.close()

    out = {
        "pass": tag,
        "generated_at_utc": cm.utc_now(),
        "gate_allow": gate_allow,
        "epoch_mode": epoch_mode,
        "epoch_ms_used": (REPLAY_EPOCH_MS if epoch_mode == "replay" else None),
        "epoch_note": ("`replay` = 显式传 `epoch_ms`（引擎为回放提供的正式参数）"
                       if epoch_mode == "replay" else
                       "`prod` = 不传 `epoch_ms` ⇒ 引擎用生产 epoch（fail-closed 下限）"),
        "isolated_paths": iso,
        "n_cases": len(rows),
        "n_open_ok": sum(1 for r in rows if r["open_ok"]),
        "n_closed": sum(1 for r in rows if r.get("position_status") == "closed"),
        "reason_histogram": _hist([r["open_reason"] for r in rows if not r["open_ok"]]),
        "cases": rows,
        "ledger": ledger,
        "ledger_counts": {k: len(v) for k, v in ledger.items()},
        "elapsed_sec": round(time.time() - t0, 1),
        "downloaded_anything": False,
        "wrote_production": False,
        "detail": detail,
    }
    cm.write_json(f"04_{tag}.json", out)
    return out


def _hist(vals) -> dict:
    h = {}
    for v in vals:
        h[str(v)] = h.get(str(v), 0) + 1
    return dict(sorted(h.items(), key=lambda kv: -kv[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="tag", required=True,
                    choices=["prod_epoch", "replay", "gate_closed", "duplicate"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dup-of", default=None)
    a = ap.parse_args()

    if a.tag == "prod_epoch":
        r = run_pass("prod_epoch", gate_allow=True, epoch_mode="prod", limit=a.limit)
    elif a.tag == "replay":
        r = run_pass("replay", gate_allow=True, epoch_mode="replay", limit=a.limit)
    elif a.tag == "gate_closed":
        r = run_pass("gate_closed", gate_allow=False, epoch_mode="replay", limit=a.limit)
    else:
        if not a.dup_of:
            raise SystemExit("--dup-of 必须给（要在已成交的那个 candidate_id 上重复提交）")
        r = run_pass("duplicate", gate_allow=True, epoch_mode="replay", dup_of=a.dup_of)
    print(f"[{r['pass']}] cases={r['n_cases']} open_ok={r['n_open_ok']} "
          f"closed={r['n_closed']} {r['elapsed_sec']}s")
    print(f"  reasons={json.dumps(r['reason_histogram'], ensure_ascii=False)}")
    print(f"  ledger={json.dumps(r['ledger_counts'], ensure_ascii=False)}")
    print(f"  isolated={json.dumps(r['isolated_paths'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
