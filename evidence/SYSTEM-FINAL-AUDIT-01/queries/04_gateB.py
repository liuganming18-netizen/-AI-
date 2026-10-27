# -*- coding: utf-8 -*-
"""04 B 关（风险关）：固定价格与 s，只改 ATR，看「数量」与「实际风险」怎么变。

工单 B 关要确认的核心：**是否存在「先按 ATR 定量、再更换百分比止损」的分叉**。
判据（判别性设计）：
  · 若定量用的是「风险预算 / (s×成交价)」⇒ 改 ATR 不应改变数量，实际风险恒等于预算；
  · 若定量用的是「风险预算 / (3×ATR)」而止损用 s ⇒ 改 ATR 会改变数量，实际风险随 ATR 漂移。
只读业务代码；用引擎自己的 open_position（回放时钟）各开一笔，从账本读回实际值。

产物：out/04_B关风险.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_final01")
PAPER = ROOT / "paper_B_risk"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
VIEW = BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json"
M15 = 900_000

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PAPER_LOCK_DIR": str(ROOT / "locks_risk"),
    "EFFECTIVE_VIEW": str(VIEW),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
}
os.environ.update(ENV)
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> None:
    import live_store as L
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    import paper_engine as PE

    envj = json.loads((OUT / "01_隔离环境.json").read_text(encoding="utf-8"))
    base = envj["合成行情"]["base"]
    sym = "BTCUSDT"
    T_sig = base - 3 * M15

    p = PE.approved_params()
    s = float(p["stoploss_s"])
    atr_mult = float(p["uniform_stop_atr_mult"])
    risk_frac = float(p["risk_per_trade_equity_fraction"])
    res: dict = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "配置": str(VIEW), "stoploss_s": s, "uniform_stop_atr_mult": atr_mult,
                 "risk_per_trade_equity_fraction": risk_frac,
                 "单名名义上限": p.get("single_name_notional_equity_cap"),
                 "用例": [], "判定": {}}

    # 同一标的同一臂只允许一笔（position_already_open_no_add）⇒ 每个 ATR 用一个独立臂，
    # 各臂账本/权益独立，避免互相影响
    for arm, atr in zip(("3a", "3b", "3c", "3d"), (0.5, 1.0, 2.0, 5.0)):
        r = PE.open_position(arm, sym, signal_uid=f"fin_b_atr{atr}", direction="long",
                             atr=atr, now_ms=base, event_time=T_sig, available_at=T_sig,
                             requested_at=T_sig, origin="replay")
        con = sqlite3.connect(PAPER / f"paper_{arm}.sqlite3")
        con.row_factory = sqlite3.Row
        rows = [dict(x) for x in con.execute(
            "SELECT position_id,status,entry_price,stop_price,qty FROM paper_position "
            "ORDER BY position_id DESC LIMIT 1").fetchall()]
        con.close()
        row = rows[0] if rows else {}
        rec = {"臂": arm, "atr": atr, "open_position_reason": r.get("reason"), "ok": r.get("ok"),
               "position_id": row.get("position_id"), "状态": row.get("status"),
               "成交价": row.get("entry_price"), "初始止损": row.get("stop_price"),
               "数量": row.get("qty")}
        if row:
            entry = float(row["entry_price"]); stop = float(row["stop_price"]); qty = float(row["qty"])
            rec["止损距离"] = round(entry - stop, 8)
            rec["s 反推"] = round((entry - stop) / entry, 8)
            rec["名义"] = round(qty * entry, 6)
            rec["实际风险"] = round(qty * (entry - stop), 8)
            rec["预算"] = round(5000.0 * risk_frac, 8)
            rec["实际/预算"] = round(rec["实际风险"] / rec["预算"], 6)
            rec["若按 ATR 定量的隐含数量"] = round(5000.0 * risk_frac / (atr_mult * atr), 6)
        res["用例"].append(rec)

    ok = [u for u in res["用例"] if u.get("数量")]
    if len(ok) >= 2:
        qtys = [u["数量"] for u in ok]
        ratios = [u["实际/预算"] for u in ok]
        # 判别：数量随 ATR 变化 ⇒ 定量吃 ATR；实际风险不恒等于预算 ⇒ 止损与定量不同源
        res["判定"] = {
            "数量随ATR变化": (max(qtys) - min(qtys)) > 1e-9,
            "数量范围": [min(qtys), max(qtys)],
            "实际风险/预算范围": [min(ratios), max(ratios)],
            "分叉判定": ("存在：定量吃 ATR（3×ATR），止损用 s（百分比），两者不同源"
                      if (max(qtys) - min(qtys)) > 1e-9 and (max(ratios) - min(ratios)) > 1e-6
                      else "未发现分叉"),
        }

    (OUT / "04_B关风险.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[B关] s={s} 3×ATR倍数={atr_mult} 单笔预算={5000*risk_frac}")
    for u in res["用例"]:
        print(f"  atr={u['atr']:>4}  数量={u.get('数量')}  成交={u.get('成交价')}  止损={u.get('初始止损')}  "
              f"实际风险={u.get('实际风险')}  实际/预算={u.get('实际/预算')}  名义={u.get('名义')}  "
              f"reason={u.get('open_position_reason')}")
    print("  判定:", json.dumps(res["判定"], ensure_ascii=False))


if __name__ == "__main__":
    main()
