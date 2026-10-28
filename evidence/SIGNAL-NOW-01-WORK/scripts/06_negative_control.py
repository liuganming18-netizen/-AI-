# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤6：负向对照（证明 18/18 不是恒真）。

做法：把**改动前**的 `paper_engine.py`（`baseline/src_before/`）用 `importlib` 顶替
`paper_engine` 模块，其余模块仍从生产 backend 加载（避免副本路径导致 `project_paths`
推导错位）。用**同一批用例**再跑一遍，期望：
  · G1b（4 天前确认 + 今日请求）旧代码**成交**、新代码**拒**；
  · G3a（停机期间收盘的执行点）旧代码**成交**、新代码**拒**；
  · G2c（正常确认 + 正常处理）两版**都成交**（证明新判据没有误杀正常单）。

生产库只读；账本一律用隔离副本。
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
BACKEND = PROJ / "框架/backend"
OUT = ROOT / "out"
OLD_PE = ROOT / "baseline" / "src_before" / "paper_engine.py"
EFFECTIVE_VIEW = (PROJ / "数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11"
                  / "5000U收尾与回测条件终验6"
                  / "10_研究参数生效视图_v2_2_user5000_shared.json")
# 旧 `paper_engine` 用 `__file__.parents[2]` 找规格 CSV，副本不在原深度 ⇒ 必须显式给
CONTRACT_SPEC_CSV = (PROJ / "数据/研究输入/外部迁移_2026-10-16"
                     / "本次离线决策_2026-10-12" / "07_526币保守执行近似规格.csv")

spec = importlib.util.spec_from_file_location("iso05", ROOT / "scripts" / "05_iso_assert.py")
iso05 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iso05)

OLD_HEAD = r'''
import json, os, sys, importlib.util
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L
_spec = importlib.util.spec_from_file_location("paper_engine", os.environ["OLD_PE"])
PE = importlib.util.module_from_spec(_spec)
sys.modules["paper_engine"] = PE
_spec.loader.exec_module(PE)
'''

OLD_OPEN = OLD_HEAD + r'''
kw = dict(signal_uid=os.environ["UID"], direction="long", atr=800.0,
          event_time=int(os.environ["EV"]), available_at=None,
          now_ms=int(os.environ["NOW"]), requested_at=int(os.environ["REQ"]),
          origin="replay", epoch_ms=int(os.environ["FLOOR"]),
          stop_mode="uniform", entry_mode="three_buy_points")
try:
    res = PE.open_position("3a", "BTCUSDT", **kw)
except Exception as exc:
    res = {"exc": f"{type(exc).__name__}: {exc}"}
print("__JSON__" + json.dumps({"res": res, "pe_file": PE.__file__},
                              ensure_ascii=False, default=str))
'''

OLD_PEND = OLD_HEAD + r'''
con = L.paper_conn("3a")
try:
    con.execute(
        "INSERT INTO paper_pending(account_id,arm,symbol,direction,signal_uid,origin,atr,"
        "stop_price,event_time,available_at,requested_at,earliest_fill_ms,status,reason,"
        "created_at,market_source,first_request_ms,epoch_ms,stoploss_s,trail_d,model_version,"
        "config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',NULL,?,?,?,?,?,?,?,?)",
        ("paper43_3a", "3a", "BTCUSDT", "long", os.environ["UID"], "signal", 800.0, None,
         int(os.environ["EV"]), None, int(os.environ["REQ"]), int(os.environ["EF"]),
         int(os.environ["REQ"]), "binance.um.candles_15m.is_closed.open",
         int(os.environ["REQ"]), int(os.environ["FLOOR"]), 0.05, 0.05,
         "FT-LOW4H-EXP-V2.2", "82dec362c182"))
    con.commit()
finally:
    con.close()
adv = PE.advance_pending(T=int(os.environ["ADV_T"]), log=lambda *a: None)
con = L.paper_conn("3a")
try:
    out = {"adv": adv,
           "pending": [dict(r) for r in con.execute(
               "SELECT pending_id,status,reason FROM paper_pending")],
           "positions": [dict(r) for r in con.execute(
               "SELECT position_id,status,entry_ts FROM paper_position")]}
finally:
    con.close()
print("__JSON__" + json.dumps(out, ensure_ascii=False, default=str))
'''


def env_of(ws: dict, **kw) -> dict:
    base = {**iso05.ws_env(ws), "DATA_HUB_PROJECT": str(PROJ),
            "EFFECTIVE_VIEW": str(EFFECTIVE_VIEW), "OLD_PE": str(OLD_PE),
            "CONTRACT_SPEC_CSV": str(CONTRACT_SPEC_CSV),
            "BACKEND": str(BACKEND)}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def main() -> int:
    rows = []

    # 对照1：G1b 旧信号续期
    ws = iso05.new_ws("old_g1b")
    r = iso05.run_child(OLD_OPEN, env_of(ws, UID="old_g1b", EV=1792748845240,
                                         NOW=1793118614728, REQ=1793116007361,
                                         FLOOR=iso05.FLOOR), "old_g1b")
    res = (r.get("res") or {})
    rows.append({"case": "G1b 4天前确认+今日请求",
                 "old_module": r.get("pe_file"),
                 "old_ok": res.get("ok"), "old_reason": res.get("reason"),
                 "new_reason": "missed_fill_moment_no_backfill",
                 "expected_difference": True,
                 "differs": res.get("reason") != "missed_fill_moment_no_backfill"})

    # 对照2：G3a 停机追补
    ws = iso05.new_ws("old_g3a")
    r = iso05.run_child(OLD_PEND, env_of(ws, UID="old_g3a", EV=iso05.T_CONFIRM,
                                         REQ=iso05.T_CONFIRM, EF=iso05.T_BAR,
                                         ADV_T=iso05.T_RESTART,
                                         FLOOR=iso05.FLOOR), "old_g3a")
    pend = r.get("pending") or []
    rows.append({"case": "G3a 停机期间收盘的执行点",
                 "old_status": pend[0].get("status") if pend else None,
                 "old_reason": pend[0].get("reason") if pend else None,
                 "old_positions": len(r.get("positions") or []),
                 "new_status": "rejected / fill_point_closed_before_process_start",
                 "expected_difference": True,
                 "differs": (pend[0].get("status") if pend else None) != "rejected"})

    # 对照3：G2c 正常路径（两版都应成交）
    ws = iso05.new_ws("old_g2c")
    r = iso05.run_child(OLD_OPEN, env_of(ws, UID="old_g2c", EV=iso05.T_CONFIRM,
                                         NOW=iso05.T_NORMAL, REQ=iso05.T_CONFIRM,
                                         FLOOR=iso05.FLOOR), "old_g2c")
    res = (r.get("res") or {})
    rows.append({"case": "G2c 正常确认+正常处理",
                 "old_ok": res.get("ok"), "old_reason": res.get("reason"),
                 "new_ok": True, "expected_difference": False,
                 "differs": res.get("ok") is not True})

    ok = all(row["differs"] == row["expected_difference"] for row in rows)
    rep = {"ticket": "SIGNAL-NOW-01-WORK", "step": "6 负向对照（旧 paper_engine 同批用例）",
           "generated_at_utc": iso05.iso(int(time.time() * 1000)),
           "old_paper_engine": str(OLD_PE), "rows": rows,
           "verdict": ("通过：新判据对两条缺陷用例产生差异、对正常用例无差异"
                       if ok else "未通过：差异与预期不符，需复核"),
           "ok": ok}
    (OUT / "06_负向对照.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for row in rows:
        print(f"  {row['case']}: 旧={row.get('old_reason') or row.get('old_status')} "
              f"预期差异={row['expected_difference']} 实际差异={row['differs']}")
    print("\n" + rep["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
