#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第41窗口 03：**旧模型（norm4h）只读重核第37四臂账本**。

对 `取消成交额硬门槛与375新准入37/临时账本37/ledger_{3a,3b,3c,3d}.sqlite3`（**只读**）：

· 材料用**旧模型** `build_symbol_data(..., model="norm4h")`（固定 4h + 严格过去 last-known，
  **不缩放**）重建，并与 37 记录的 `funding_model` 身份逐字节核对；
· **不使用** `norm4h_approx` 动态折算（新公式）审旧账本；
· 逐笔核 `amount == -qty*mark*rate`：**单笔绝对误差 ≤0.01U、全账本累计绝对误差 ≤0.50U**；
· 逐笔双向审查（应扣集合/重复/出持仓外）走真实入口 `audit_funding_usage`；
· 产出摘要 + 逐笔差异 CSV；**不覆盖**旧 DB/JSON/结论；**不跑**新收益回放。
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path("/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究")
ROOT = BASE / "最新唐奇安威科夫接通与收尾_2026-10-11"
W30 = ROOT / "375实际运行接通30"
W37 = ROOT / "取消成交额硬门槛与375新准入37"
W41 = Path(__file__).parent
BACKEND = BASE / "后端实现"
for _p in (str(W30), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
sys.dont_write_bytecode = True

from strategies import wx_daily_375 as D            # noqa: E402
from strategies import wx_execution as X            # noqa: E402

UTC = dt.timezone.utc
ARMS = ("3a", "3b", "3c", "3d")
DAILY_DOC_37 = W37 / "11_375逐日名单_DEV_37.json"
RESULT_37 = {a: W37 / f"20_结果_norm4h_{a}.json" for a in ARMS}
LEDGER_37 = {a: W37 / "临时账本37" / f"ledger_{a}.sqlite3" for a in ARMS}
WS = "2020-12-15T00:00:00Z"
WE = "2023-11-12T00:00:00Z"
TOL_SINGLE = 0.01
TOL_TOTAL = 0.50


def ms(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def iso(t):
    if t is None:
        return None
    return dt.datetime.fromtimestamp(int(t) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def main():
    t0 = time.time()
    rep = {"schema": "work41_old37_readonly_recheck/v1",
           "generated_real_utc": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "window": {"start": WS, "end_excl": WE, "model": "norm4h(旧模型,不缩放)"},
           "auditor": "wx_execution.audit_funding_usage (第41 版本)",
           "tolerance": {"single_abs_usdt": TOL_SINGLE, "total_accumulated_abs_usdt": TOL_TOTAL},
           "note": "只读重核；不覆盖旧 DB/JSON/结论；费用核验 ≠ 正式通过；不套 norm4h_approx 新公式"}
    rep["source_sha"] = {
        "wx_execution.py_after_41": sha256_file(BACKEND / "strategies" / "wx_execution.py"),
        "wx_daily_375.py_after_41": sha256_file(BACKEND / "strategies" / "wx_daily_375.py")}
    rep["ledger_sha"] = {a: sha256_file(LEDGER_37[a]) for a in ARMS}

    rk, adm, meta = D.load_daily_maps(doc_path=str(DAILY_DOC_37), panel="DEV")
    syms = sorted({s for _d, m in rk.items() for (_ex, s) in m})
    t1 = time.time()
    sd = D.build_symbol_data(syms, ms(WS), ms(WE), model="norm4h")
    mats = X.complete_funding_materials(sd)
    ident = X.funding_materials_identity("norm4h", mats)
    rep["materials"] = {"n_sources": mats["n_sources"],
                        "n_events": sum(s["n_events"] for s in mats["sources"]),
                        "n_marks": sum(s["n_marks"] for s in mats["sources"]),
                        "identity": ident, "build_seconds": round(time.time() - t1, 1),
                        "symbols": len(syms)}
    rep["identity_vs_37_recorded"] = {}
    for a in ARMS:
        doc = json.loads(RESULT_37[a].read_text(encoding="utf-8"))
        rep["identity_vs_37_recorded"][a] = {
            "recorded_funding_model": doc["run"]["run_binding"]["funding_model"],
            "rebuilt_identity": ident,
            "match": doc["run"]["run_binding"]["funding_model"] == ident}

    rows_csv = []
    per_arm = {}
    for a in ARMS:
        rid = f"w37base_norm4h_{a}"
        con = sqlite3.connect("file:%s?mode=ro" % LEDGER_37[a], uri=True)
        con.row_factory = sqlite3.Row
        t2 = time.time()
        aud = X.audit_funding_usage(mats, con=con, run_id=rid, model="norm4h")
        dt_s = round(time.time() - t2, 1)
        # 独立逐笔金额误差（旧模型公式 amount == -qty*mark*rate；不用新折算公式）
        led_rows = X._ledger_funding_rows(con, rid)
        errs = []
        bad_rows = []
        for r in led_rows:
            try:
                want = -float(r["qty"]) * float(r["mark"]) * float(r["rate"])
                e = abs(want - float(r["amount"]))
            except (TypeError, ValueError):
                bad_rows.append({"position_id": r.get("position_id"),
                                 "funding_time": r.get("funding_time"),
                                 "reason": "non_numeric_fields"})
                continue
            errs.append(e)
            if e > TOL_SINGLE:
                bad_rows.append({"position_id": r.get("position_id"),
                                 "funding_time": r.get("funding_time"),
                                 "funding_time_utc": iso(r.get("funding_time")),
                                 "abs_err": e})
        total = sum(errs)
        per_arm[a] = {
            "run_id": rid, "ledger": str(LEDGER_37[a]),
            "n_ledger_rows": len(led_rows),
            "new_ok": aud["ok"], "new_auditable": aud["auditable"],
            "new_problems": aud["problems"],
            "n_expected": aud["n_expected"], "n_charged": aud["n_charged"],
            "audit_total_abs_error": aud["total_abs_error"],
            "recomputed_total_abs_error": total,
            "recomputed_max_single_abs_error": (max(errs) if errs else 0.0),
            "n_single_over_tol": sum(1 for e in errs if e > TOL_SINGLE),
            "total_within_tol": total <= TOL_TOTAL,
            "forward_fail_n": len(aud["forward"]["fail"]),
            "reverse": {k: (len(v) if isinstance(v, list) else v)
                        for k, v in aud["reverse"].items()},
            "bad_rows": bad_rows[:50], "audit_seconds": dt_s}
        pos = {r["position_id"]: dict(r) for r in con.execute(
            "SELECT position_id,exchange,symbol,entry_time,exit_time,quantity,status "
            "FROM research_position WHERE run_id=?", (rid,))}
        for f in aud["forward"]["fail"]:
            p = pos.get(f["position_id"]) or {}
            rows_csv.append({"arm": a, "run_id": rid, "position_id": f["position_id"],
                             "exchange": f["exchange"], "symbol": f["symbol"],
                             "funding_time_ms": f["funding_time"],
                             "funding_time_utc": iso(f["funding_time"]),
                             "entry_utc": iso(p.get("entry_time")),
                             "exit_utc": iso(p.get("exit_time")),
                             "pos_qty": p.get("quantity"), "pos_status": p.get("status"),
                             "diff_kind": "forward", "reasons": "|".join(f["reasons"])})
        for kind, items in (("missing", aud["reverse"]["missing"]),
                            ("extra", aud["reverse"]["extra"]),
                            ("duplicate", aud["reverse"]["duplicate"]),
                            ("blocked_in_expected", aud["reverse"]["blocked_in_expected"])):
            for it in items:
                pid = it[1] if isinstance(it, (list, tuple)) and len(it) > 1 else it
                ft = it[2] if isinstance(it, (list, tuple)) and len(it) > 2 else None
                p = pos.get(pid) or {}
                rows_csv.append({"arm": a, "run_id": rid, "position_id": pid,
                                 "exchange": p.get("exchange"), "symbol": p.get("symbol"),
                                 "funding_time_ms": ft, "funding_time_utc": iso(ft),
                                 "entry_utc": iso(p.get("entry_time")),
                                 "exit_utc": iso(p.get("exit_time")),
                                 "pos_qty": p.get("quantity"), "pos_status": p.get("status"),
                                 "diff_kind": "reverse:" + kind, "reasons": kind})
        con.close()
    rep["per_arm"] = per_arm
    rep["n_rows_diff"] = len(rows_csv)
    rep["all_new_ok"] = all(per_arm[a]["new_ok"] for a in ARMS)
    rep["all_total_within_tol"] = all(per_arm[a]["total_within_tol"] for a in ARMS)
    rep["all_single_within_tol"] = all(per_arm[a]["n_single_over_tol"] == 0 for a in ARMS)
    rep["verdict"] = ("四臂费用核验通过（单笔≤0.01U、累计≤0.50U）；仍非正式全系统通过"
                      if (rep["all_new_ok"] and rep["all_total_within_tol"]
                          and rep["all_single_within_tol"])
                      else "存在未通过项：数字待复核（见逐笔 CSV）")
    rep["elapsed_s"] = round(time.time() - t0, 1)

    (W41 / "03_四臂重核摘要_41.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with open(W41 / "03_四臂重核逐笔_41.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["arm", "run_id", "position_id", "exchange", "symbol",
                                          "funding_time_ms", "funding_time_utc", "entry_utc",
                                          "exit_utc", "pos_qty", "pos_status", "diff_kind",
                                          "reasons"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_csv)
    print(json.dumps({"per_arm": {a: {k: per_arm[a][k] for k in
                                      ("new_ok", "n_ledger_rows", "n_expected", "n_charged",
                                       "recomputed_total_abs_error",
                                       "recomputed_max_single_abs_error", "n_single_over_tol",
                                       "new_problems", "forward_fail_n")} for a in ARMS},
                      "identity_match": {a: rep["identity_vs_37_recorded"][a]["match"]
                                         for a in ARMS},
                      "n_rows_diff": rep["n_rows_diff"], "verdict": rep["verdict"],
                      "elapsed_s": rep["elapsed_s"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
