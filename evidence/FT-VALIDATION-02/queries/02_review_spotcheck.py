# -*- coding: utf-8 -*-
"""02 反重力复盘记录抽核：12 个固定案例 vs 源账本。

源账本（复盘记录自述来源）：
- evidence/FT-ENTRY-07/02_逐候选对照表.csv（P0、C0/C1 决策与盈亏）
- evidence/FT-ENTRY-07/out/trades_Wyckoff_C_<grid>.csv、trades_Wyckoff_C1_<grid>.csv
  （入场/出场时间戳、成交价、盈亏、退出原因）

核对项：
- 交易/事件 ID 是否存在；
- C0 入场时间 = T0 + 15m；出场时间 = 源账本 close_timestamp；
- 入场/出场价格、盈亏、退出原因；
- P0 = 源账本 P0_stop；
- C1 决策/原因；成交时入场时间 = T1 + 15m、价格/盈亏。
仅抽核，不重跑、不改账本。
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import OUT, ROOT, jdump, write_csv  # noqa: E402

E08 = os.path.join(ROOT, "evidence/FT-ENTRY-08")
E07 = os.path.join(ROOT, "evidence/FT-ENTRY-07")
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
CASES = os.path.join(E08, "01_选样清单表.csv")
REVIEW_MD = os.path.join(E08, "02_案例复盘与决策视图.md")


def load_trade(grid: str, mode: str, event_id: str):
    strat = f"Wyckoff_{mode}_{grid}"
    p = os.path.join(E07, "out", f"trades_{strat}.csv")
    if not os.path.isfile(p):
        return None
    d = pd.read_csv(p, encoding="utf-8-sig")
    x = d[d["enter_tag"].astype(str).str.contains(event_id, regex=False)]
    return x.iloc[0] if len(x) else None


def load_ref(grid: str, event_id: str):
    p = os.path.join(E07, "02_逐候选对照表.csv")
    d = pd.read_csv(p, encoding="utf-8-sig")
    x = d[(d["param_group"] == grid) & (d["event_id"] == event_id)]
    return x.iloc[0] if len(x) else None


def narrative_times():
    """按案例编号收集复盘正文里的 HH:MM 时间点。"""
    text = open(REVIEW_MD, encoding="utf-8").read()
    parts = re.split(r"### 案例\s*(\d+)", text)
    out = {}
    for i in range(1, len(parts), 2):
        n = int(parts[i])
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out[n] = sorted(set(re.findall(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", body)))
    return out


def main() -> None:
    cases = pd.read_csv(CASES, encoding="utf-8-sig")
    ntimes = narrative_times()
    rows = []
    for global_no, r in enumerate(cases.itertuples(), start=1):
        grid = r.target_param
        eid = r.event_id
        ref = load_ref(grid, eid)
        c0 = load_trade(grid, "C", eid)
        c1 = load_trade(grid, "C1", eid)
        t0 = pd.Timestamp(r.t0_signal_time)
        t1 = pd.Timestamp(r.t1_confirm_time)
        checks = {}

        def chk(name, ok, got, want):
            checks[name] = {"ok": bool(ok), "got": got, "want": want}

        chk("event_in_ref", ref is not None, ref is not None, True)
        chk("c0_filled", (c0 is not None) == bool(r.c0_filled), c0 is not None, bool(r.c0_filled))
        if c0 is not None:
            chk("c0_open_time_T0_plus_15m",
                int(c0["open_timestamp"]) == int(t0.timestamp() * 1000) + 15 * 60 * 1000,
                str(pd.Timestamp(int(c0["open_timestamp"]), unit="ms", tz="UTC")),
                str(t0 + pd.Timedelta(minutes=15)))
            chk("c0_open_rate", abs(float(c0["open_rate"]) - float(r.c0_open_rate)) < 1e-6,
                float(c0["open_rate"]), float(r.c0_open_rate))
            chk("c0_exit_rate", abs(float(c0["close_rate"]) - float(r.c0_exit_rate)) < 1e-6,
                float(c0["close_rate"]), float(r.c0_exit_rate))
            chk("c0_profit_abs", abs(float(c0["profit_abs"]) - float(r.c0_profit_abs)) < 1e-6,
                float(c0["profit_abs"]), float(r.c0_profit_abs))
            chk("c0_exit_reason", str(c0["exit_reason"]) == str(r.c0_exit_reason),
                str(c0["exit_reason"]), str(r.c0_exit_reason))
            close_hm = pd.Timestamp(int(c0["close_timestamp"]), unit="ms", tz="UTC").strftime("%H:%M")
            chk("c0_exit_time_in_narrative", close_hm in ntimes.get(global_no, []),
                close_hm, ntimes.get(global_no, []))
        if ref is not None:
            chk("P0", abs(float(ref["P0_stop"]) - float(r.p0_stop)) < 1e-6,
                float(ref["P0_stop"]), float(r.p0_stop))
            chk("c1_decision", str(ref["c1_decision"]) == str(r.c1_decision),
                str(ref["c1_decision"]), str(r.c1_decision))
            chk("c1_reason", str(ref["c1_reason"]) == str(r.c1_reason),
                str(ref["c1_reason"]), str(r.c1_reason))
            chk("c1_filled", (c1 is not None) == bool(ref["c1_filled"]),
                c1 is not None, bool(ref["c1_filled"]))
        if c1 is not None:
            chk("c1_open_time_T1_plus_15m",
                int(c1["open_timestamp"]) == int(t1.timestamp() * 1000) + 15 * 60 * 1000,
                str(pd.Timestamp(int(c1["open_timestamp"]), unit="ms", tz="UTC")),
                str(t1 + pd.Timedelta(minutes=15)))
            chk("c1_profit_abs", abs(float(c1["profit_abs"]) - float(r.c1_profit_abs)) < 1e-6,
                float(c1["profit_abs"]), float(r.c1_profit_abs))
        # 与 FT-ENTRY-09 对照（仅标记是否已随统一准入更新）
        c0_09 = None
        p09 = os.path.join(E09, "out", f"trades_Wyckoff_C_{grid}.csv")
        if os.path.isfile(p09):
            d9 = pd.read_csv(p09, encoding="utf-8-sig")
            m = d9[d9["enter_tag"].astype(str).str.contains(eid, regex=False)]
            c0_09 = float(m.iloc[0]["profit_abs"]) if len(m) else None
        rows.append({
            "case_id": r.case_id, "slot": r.slot_index, "symbol": r.symbol,
            "grid": grid, "event_id": eid,
            "checks_total": len(checks),
            "checks_ok": sum(1 for v in checks.values() if v["ok"]),
            "checks_failed": ";".join(k for k, v in checks.items() if not v["ok"]) or None,
            "c0_profit_07": float(c0["profit_abs"]) if c0 is not None else None,
            "c0_profit_09": c0_09,
            "c0_changed_under_09": (c0 is not None and c0_09 is not None
                                    and abs(float(c0["profit_abs"]) - c0_09) > 1e-6),
            "detail": checks,
        })
    res = pd.DataFrame(rows)
    write_csv(res.drop(columns=["detail"]), "02_复盘抽核_v1.csv")
    # 明细展开
    flat = []
    for r in rows:
        for k, v in r["detail"].items():
            flat.append({"case_id": r["case_id"], "check": k, "ok": v["ok"],
                         "got": v["got"], "want": v["want"]})
    write_csv(pd.DataFrame(flat), "02_复盘抽核明细_v1.csv")
    summary = {
        "cases": int(len(res)),
        "cases_all_pass": int((res["checks_failed"].isna()).sum()),
        "cases_with_failure": int(res["checks_failed"].notna().sum()),
        "total_checks": int(res["checks_total"].sum()),
        "passed_checks": int(res["checks_ok"].sum()),
        "failed_detail": res.loc[res["checks_failed"].notna(),
                                 ["case_id", "checks_failed"]].to_dict("records"),
        "c0_changed_under_09": int(res["c0_changed_under_09"].sum()),
        "note": "源账本为 FT-ENTRY-07（复盘记录自述来源）；FT-ENTRY-09 仅作是否随统一准入更新的对照。",
    }
    jdump(os.path.join(OUT, "02_复盘抽核摘要_v1.json"), summary)
    print(res[["case_id", "grid", "checks_total", "checks_ok", "checks_failed",
               "c0_changed_under_09"]].to_string(index=False))
    print("\n", summary)


if __name__ == "__main__":
    main()
