#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10_two_books_parity.py — 证明「两套账底座不一致」是真缺陷，而不是我读代码的推断。

背景（只读核对已确认的事实）：
  · `paper_state.cash` = 4995.6545，`realized_pnl` = -4.3455，`fees_paid` = 1.4744
  · 那 -4.3455 来自 3 笔 `paper_realized`，**全部 `voided=1`**（`synthetic_test_isolation`）
  · `paper_engine.account_view()` 默认口径 = `初始权益 + Σnet_pnl(voided=0)` = 5000.00
  · 所以接口 `/paper/accounts` 报 5000.00

两条口径各自自洽，但数值差 4.3455。**真正的问题不是「数字不一样」**，
而是 `_set_cash()`（增减现金的唯一入口）读的是 `paper_state.cash` 这个旧底座。
⇒ 下一笔真实交易发生时，两边会以不同起点各自加减，差额永久保留并随每笔传递。

本脚本做三件事（全部在**生产库副本**上做，生产库只读）：
  ① 记录当前两套账的值与差额
  ② 在副本上模拟一笔 `voided=0` 的真实平仓（-10.00），**按生产代码同一逻辑**改 `paper_state`
  ③ 再看两套账的差额：若仍为 4.3455 且方向不变 ⇒ 证明「差额会传递」，缺陷成立

用法：
    python 10_two_books_parity.py
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "out"
LEDGER = ROOT / "数据/paper/paper_3a.sqlite3"

ARM = "3a"
ACC = f"paper43_{ARM}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def books(con: sqlite3.Connection) -> dict:
    """把两套账各自的算法照抄一遍（与生产代码逐行对应，不调生产函数，避免被同一 bug 污染）。"""
    init = con.execute(
        "SELECT initial_equity FROM paper_account WHERE account_id=?", (ACC,)).fetchone()
    init_eq = float(init[0]) if init and init[0] is not None else 5000.0

    st = con.execute("SELECT * FROM paper_state WHERE account_id=?", (ACC,)).fetchone()

    # 旧底座：paper_state（含 voided=1 的作废记录）
    old = {
        "cash": float(st["cash"]),
        "realized_pnl": float(st["realized_pnl"]),
        "fees_paid": float(st["fees_paid"]),
    }

    # 正式口径：初始权益 + 只算 voided=0 的净额（account_view 默认分支）
    row = con.execute(
        "SELECT COALESCE(SUM(net_pnl),0), COALESCE(SUM(entry_fee+exit_fee),0) "
        "FROM paper_realized WHERE account_id=? AND COALESCE(voided,0)=0", (ACC,)).fetchone()
    new = {
        "cash": init_eq + float(row[0] or 0.0),
        "realized_pnl": float(row[0] or 0.0),
        "fees_paid": float(row[1] or 0.0),
    }
    # 含作废的净额（修复前 `recover_report` 用的口径）
    allnet = con.execute(
        "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized WHERE account_id=?",
        (ACC,)).fetchone()[0]
    # 只算有效记录的净额（修复后 `recover_report` 用的口径，与 account_view 一致）
    offnet = con.execute(
        "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized "
        "WHERE account_id=? AND COALESCE(voided,0)=0", (ACC,)).fetchone()[0]

    return {
        "initial_equity": init_eq,
        "旧底座_paper_state": old,
        "正式口径_account_view": new,
        "差额_cash": round(new["cash"] - old["cash"], 6),
        "含作废净额": round(float(allnet), 6),
        "未作废净额": round(float(offnet), 6),
        # 修复前的自洽检查口径（含作废）—— 留着看历史
        "自洽检查_旧口径_expect": round(init_eq + float(allnet), 6),
        # 修复后的自洽检查口径（排除作废）—— 与 paper_engine.recover_report 现状一致
        "自洽检查_expect": round(init_eq + float(offnet), 6),
        "自洽检查_cash": round(old["cash"], 6),
        "自洽检查通过": abs((init_eq + float(offnet)) - old["cash"]) < 1e-6,
    }


def set_cash_like_production(con: sqlite3.Connection, *, cash=0.0, realized=0.0,
                             fees=0.0, funding=0.0) -> float:
    """与 paper_engine._set_cash 完全同一逻辑（读 paper_state 旧底座再加 delta）。"""
    st = con.execute("SELECT * FROM paper_state WHERE account_id=?", (ACC,)).fetchone()
    base = con.execute("SELECT initial_equity FROM paper_account WHERE account_id=?",
                       (ACC,)).fetchone()
    base_eq = float(base[0]) if base and base[0] is not None else 5000.0
    c = float(st["cash"]) if st else base_eq
    rp = float(st["realized_pnl"]) if st else 0.0
    fp = float(st["fees_paid"]) if st else 0.0
    fd = float(st["funding_paid"]) if st else 0.0
    c += cash
    rp += realized
    fp += fees
    fd += funding
    con.execute(
        "INSERT INTO paper_state(account_id,cash,realized_pnl,fees_paid,funding_paid,updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET cash=excluded.cash, "
        "realized_pnl=excluded.realized_pnl, fees_paid=excluded.fees_paid, "
        "funding_paid=excluded.funding_paid, updated_at=excluded.updated_at",
        (ACC, c, rp, fp, fd, 1792700000000))
    return c


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_paper3a_copy.sqlite3"
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(LEDGER, tmp)

    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    # ── ① 当前两套账 ──────────────────────────────────────────────────────
    before = books(con)

    # 作废记录明细（证明 -4.3455 全部来自 voided=1）
    voided_rows = [dict(r) for r in con.execute(
        "SELECT id,symbol,net_pnl,exit_reason,voided,void_reason FROM paper_realized "
        "WHERE account_id=? ORDER BY id", (ACC,))]
    voided_sum = sum(float(r["net_pnl"]) for r in voided_rows if int(r["voided"] or 0) == 1)
    live_sum = sum(float(r["net_pnl"]) for r in voided_rows if int(r["voided"] or 0) == 0)

    # ── ② 在副本上模拟一笔真实平仓 -10.00 ─────────────────────────────────
    SIM_NET = -10.0
    SIM_FEE = 0.50
    # 2a 写一条 voided=0 的已实现记录（真实交易的写法）
    con.execute(
        "INSERT INTO paper_realized(account_id,position_id,symbol,qty,entry_price,exit_price,"
        "gross_pnl,entry_fee,exit_fee,funding_paid,net_pnl,entry_ts,exit_ts,exit_reason,ts,"
        "voided,void_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)",
        (ACC, 99, "SIMUSDT", 1.0, 100.0, 90.0, SIM_NET + SIM_FEE * 2, SIM_FEE, SIM_FEE,
         0.0, SIM_NET, 1792700000000, 1792703600000, "probe_simulated_real_trade",
         1792703600000))
    # 2b 按生产 `_set_cash` 同逻辑改 paper_state
    #    真实平仓（paper_engine.py:1753）传的是 `cash=(gross - exit_fee - new_fund)`，
    #    而开仓时已单独扣过 entry_fee（:1217）。累计现金变化 = net。
    #    所以这里传 cash=SIM_NET（**不是** SIM_NET - SIM_FEE —— 初版我这么传，
    #    等于把手续费扣了两遍，差额从 4.3455 变成 4.8455，得出「差额会放大」的错误结论）。
    set_cash_like_production(con, cash=SIM_NET, realized=SIM_NET, fees=SIM_FEE)
    con.commit()

    after = books(con)
    con.close()
    tmp.unlink()                      # 副本用完即删，生产库从未被写

    drift_before = before["差额_cash"]
    drift_after = after["差额_cash"]

    res = {
        "probe": "PAPER-LEDGER-PARITY-11/two_books",
        "generated_utc": now_iso(),
        "生产库": str(LEDGER.relative_to(ROOT)),
        "本次操作": "只读生产库；所有模拟都在副本 `out/_paper3a_copy.sqlite3` 上做，做完即删",
        "①当前": before,
        "作废记录明细": voided_rows,
        "作废净额合计": round(voided_sum, 6),
        "未作废净额合计": round(live_sum, 6),
        "②模拟": {"净亏损": SIM_NET, "手续费": SIM_FEE,
                 "方式": "插入 1 条 voided=0 的 paper_realized + 按 _set_cash 同逻辑改 paper_state"},
        "③模拟后": after,
        "差额_模拟前": drift_before,
        "差额_模拟后": drift_after,
        "差额是否传递": abs(drift_after - drift_before) < 1e-9,
        "两套账是否一致": abs(drift_after) < 1e-9,
        "结论": (
            "两套账已一致（差额 0，模拟真实交易后仍为 0）：作废亏损不再留在账本底座里"
            if abs(drift_after) < 1e-9 and abs(drift_before) < 1e-9
            else ("缺陷成立：两套账底座不同，作废亏损 4.3455 永久留在一套账里，"
                  "真实交易发生后差额不收敛（不会自动抹平），且接口永远比账本多报这笔钱"
                  if abs(drift_after - drift_before) < 1e-9 and abs(drift_before) > 1e-9
                  else "差额在变，需重查（模拟参数或生产代码口径又变了）")),
        "修复方向": {
            "数据": "把 paper_state 的 cash/realized_pnl/fees_paid 校正到正式口径，并用 paper_correction 留痕",
            "代码": "recover_report 里 SUM(net_pnl) 同步改为只算 voided=0，否则校正后自洽检查会报错",
            "为什么必须两边一起改": "只改数据 ⇒ 自洽检查立刻失败；只改代码 ⇒ 差额仍在",
        },
    }

    p = OUT / "10_two_books_parity.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== ① 当前两套账 ==")
    print(f"  初始权益          {before['initial_equity']}")
    print(f"  旧底座 paper_state cash={before['旧底座_paper_state']['cash']:.6f} "
          f"realized={before['旧底座_paper_state']['realized_pnl']:.6f}")
    print(f"  正式口径 account_view cash={before['正式口径_account_view']['cash']:.6f} "
          f"realized={before['正式口径_account_view']['realized_pnl']:.6f}")
    print(f"  差额（正式 − 旧）  {before['差额_cash']:+.6f}")
    print(f"  作废净额 {voided_sum:.6f} / 未作废净额 {live_sum:.6f}")
    print(f"  自洽检查（旧口径） expect={before['自洽检查_expect']:.6f} "
          f"cash={before['自洽检查_cash']:.6f} → {before['自洽检查通过']}")
    print("\n== ② 副本上模拟一笔真实平仓 −10.00（含 0.50 手续费）==")
    print(f"  旧底座 cash  {after['旧底座_paper_state']['cash']:.6f}")
    print(f"  正式口径 cash {after['正式口径_account_view']['cash']:.6f}")
    print(f"  差额（正式 − 旧） {after['差额_cash']:+.6f}")
    print(f"\n== ③ 结论 ==\n  差额传递 = {res['差额是否传递']}（{drift_before:+.6f} → {drift_after:+.6f}）")
    print(f"  {res['结论']}")
    print(f"\n→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
