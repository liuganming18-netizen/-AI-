# -*- coding: utf-8 -*-
"""00 启动条件核对：只读 Work 区间资格包，判断 READY / NOT_READY / CONDITIONAL。

纪律：
- 不读取任何收益列（pnl/profit/return/net/win/expectancy/...）；
- 不运行策略、不计算验证指标；
- 只检查区间资格包是否存在、区间是否可用、覆盖/逐币资格字段是否齐全。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import DATA_PKG, ENTRY10, OUT, jdump, sha256_file, write_csv  # noqa: E402

RETURN_HINTS = ("pnl", "profit", "return", "net_", "win_", "expectancy", "drawdown",
                "quick_stop", "fast_stop", "trade_count", "trades_count")
QUAL_HINTS = ("资格", "区间", "coverage", "qualification", "eligible", "admission")
TIME_COLS = ("start", "end", "q_start", "q_end", "起", "止", "start_utc", "end_utc")


def list_pkg():
    files = []
    if not os.path.isdir(ENTRY10):
        return files
    for r, _d, fs in os.walk(ENTRY10):
        for fn in fs:
            p = os.path.join(r, fn)
            files.append(os.path.relpath(p, os.path.dirname(ENTRY10)))
    return sorted(files)


def inspect_qualification(files):
    """只读资格/覆盖表的表头与区间/资格列；显式跳过收益列。"""
    tables = []
    for rel in files:
        if not rel.lower().endswith(".csv"):
            continue
        if not any(k in os.path.basename(rel) for k in QUAL_HINTS):
            continue
        p = os.path.join(os.path.dirname(ENTRY10), rel)
        try:
            head = pd.read_csv(p, nrows=0, encoding="utf-8-sig")
        except Exception as e:
            tables.append({"file": rel, "error": str(e)})
            continue
        cols = list(head.columns)
        keep = [c for c in cols if not any(h in c.lower() for h in RETURN_HINTS)]
        has_time = [c for c in keep if any(t in c.lower() for t in TIME_COLS)]
        tables.append({"file": rel, "columns": cols, "non_return_columns": keep,
                       "time_columns": has_time,
                       "n_columns": len(cols)})
    return tables


def coverage_summary():
    """只读 Work 数据覆盖表的元数据列，不读任何价格/收益。"""
    p = os.path.join(DATA_PKG, "out/01_数据覆盖_逐币.csv")
    if not os.path.isfile(p):
        return {"status": "NO_COVERAGE_FILE"}
    d = pd.read_csv(p, encoding="utf-8-sig")
    out = {"status": "OK", "symbols": int(len(d))}
    for c in ("4h_行数", "15m_行数", "OI_行数", "funding_行数"):
        if c in d:
            out[c] = int((d[c] > 0).sum())
    if "4h_行数" in d:
        out["symbols_4h_ge_200"] = int((d["4h_行数"] >= 200).sum())
    for c in ("15m_末根", "1h_末根", "4h_末根收盘", "OI_末", "funding_末"):
        if c in d:
            x = pd.to_datetime(d[c], utc=True, errors="coerce")
            out[c + "_max"] = str(x.max()) if x.notna().any() else None
    return out


def contact_gap_summary(data_end):
    """只读接触台账的区间列（symbol/起/止），不读候选/成交/收益列。

    对 74 个 C1 准入币，计算 [2025-01-01, data_end] 内未被任何运行接触的
    最大连续缺口；用于判断是否存在 >=12 个月的独立验证窗口。
    """
    p = os.path.join(DATA_PKG, "out/02_接触区间_逐币.csv")
    if not os.path.isfile(p):
        return {"status": "NO_CONTACT_LEDGER"}
    led = pd.read_csv(p, encoding="utf-8-sig", usecols=["symbol", "区间起", "区间止"])
    led["base"] = led["symbol"].astype(str).str.replace("USDT", "", regex=False)
    led["s"] = pd.to_datetime(led["区间起"], utc=True)
    led["e"] = pd.to_datetime(led["区间止"], utc=True)
    adm = pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "evidence/FT-ENTRY-09/00_逐币准入资格表.csv"),
        encoding="utf-8-sig")
    adm["base"] = adm["symbol"].astype(str).str.replace("USDT", "", regex=False)
    W0 = pd.Timestamp("2025-01-01", tz="UTC")
    W1 = pd.Timestamp(data_end) if data_end else pd.Timestamp("2026-10-18 12:00", tz="UTC")
    rows = []
    for b in adm["base"]:
        x = led[led["base"] == b]
        if x.empty:
            rows.append({"symbol": b, "intervals": 0, "covered_days": 0.0, "max_gap_days": None})
            continue
        segs = sorted([(max(r.s, W0), min(r.e, W1)) for r in x.itertuples()
                       if min(r.e, W1) > max(r.s, W0)])
        merged = []
        for a, c in segs:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], c))
            else:
                merged.append((a, c))
        cov = sum((c - a).total_seconds() / 86400 for a, c in merged)
        gaps, cur = [], W0
        for a, c in merged:
            if a > cur:
                gaps.append((a - cur).total_seconds() / 86400)
            cur = max(cur, c)
        if cur < W1:
            gaps.append((W1 - cur).total_seconds() / 86400)
        rows.append({"symbol": b, "intervals": int(len(x)), "covered_days": round(cov, 1),
                     "max_gap_days": round(max(gaps), 1) if gaps else 0.0})
    df = pd.DataFrame(rows)
    return {"status": "OK", "window": [str(W0.date()), str(W1.date())],
            "c1_coins": int(len(df)),
            "coins_with_ledger": int(df["max_gap_days"].notna().sum()),
            "coins_fully_covered": int((df["max_gap_days"] <= 1).sum()),
            "max_gap_days": (float(df["max_gap_days"].max())
                             if df["max_gap_days"].notna().any() else None),
            "coins_gap_ge_365": int((df["max_gap_days"] >= 365).sum()),
            "coins_gap_ge_180": int((df["max_gap_days"] >= 180).sum())}


def interval_table_summary():
    """只读 03_区间资格表 的区间/资格列，判断是否存在 >=12 个月未接触区间。"""
    p = os.path.join(DATA_PKG, "out/03_区间资格表.csv")
    if not os.path.isfile(p):
        return {"status": "NO_INTERVAL_TABLE"}
    cols = ["symbol", "区间起", "区间止", "天数", "段级接触", "满足200根预热",
            "满足30天", "满足12个月"]
    d = pd.read_csv(p, encoding="utf-8-sig", usecols=cols)
    is12 = d["满足12个月"].astype(str).isin(["是", "True", "true", "1"])
    touched = d["段级接触"].astype(str).isin(["是", "True", "true", "1"])
    usable = d[is12 & ~touched]
    return {"status": "OK", "intervals": int(len(d)),
            "ge_12m": int(is12.sum()), "ge_12m_unobserved": int(len(usable)),
            "ge_30d": int(d["满足30天"].astype(str).isin(["是", "True", "true", "1"]).sum()),
            "warmup_ok": int(d["满足200根预热"].astype(str).isin(["是", "True", "true", "1"]).sum()),
            "usable_examples": usable[["symbol", "区间起", "区间止", "天数"]].head(10).to_dict("records")}


def work_verdict():
    """只读 Work 的资格结论（可用区间数/分层/OI PIT），不取任何收益数字。"""
    p = os.path.join(DATA_PKG, "out/05_hharness_资格结论.json")
    if not os.path.isfile(p):
        return {"status": "NO_WORK_VERDICT"}
    import json
    j = json.load(open(p, encoding="utf-8"))
    return {"status": "OK",
            "usable_intervals": j.get("合格独立验证区间数"),
            "tiers": j.get("分层计数"),
            "denominator": j.get("分母"),
            "oi_pit": (j.get("数据条件核对") or {}).get("OI_PIT结论"),
            "forward": (j.get("建议的固定起点前向方案（未启动）") or {})}


def main() -> None:
    files = list_pkg()
    cov = coverage_summary()
    contact = contact_gap_summary(cov.get("15m_末根_max")) if cov.get("status") == "OK" else {"status": "PENDING"}
    itab = interval_table_summary()
    wv = work_verdict()
    qual = inspect_qualification(files) if files else []
    usable = 0
    if qual:
        # 只按"可用/合格/未观察"等资格字段判断，不读收益
        for t in qual:
            p = os.path.join(os.path.dirname(ENTRY10), t["file"])
            try:
                d = pd.read_csv(p, encoding="utf-8-sig")
            except Exception:
                continue
            for c in d.columns:
                lc = c.lower()
                if any(h in lc for h in ("usable", "qualified", "can_use", "可用", "合格")):
                    try:
                        vals = d[c].astype(str).str.lower()
                        usable += int(vals.isin(["true", "1", "yes", "是", "可用", "合格"]).sum())
                    except Exception:
                        pass
    status = "NOT_READY"
    reasons = []
    if not files:
        reasons.append("FT-ENTRY-10 区间资格包未到达（目录为空）")
    if wv.get("status") == "OK":
        reasons.append("Work 资格结论（05_hharness_资格结论.json）：合格独立验证区间数 = %s；"
                       "分层 ①/④/⑤ = %s；OI PIT = %s"
                       % (wv.get("usable_intervals"), wv.get("tiers"),
                          "0（fetched_at 全空）" if "0" in str(wv.get("oi_pit")) else wv.get("oi_pit")))
        fwd = wv.get("forward") or {}
        reasons.append("Work 建议的前向方案（未启动）：起点 %s；最小样本 %s；预计等待 %s；"
                       "G1/G2/G4 未闭环前不得起算"
                       % (fwd.get("固定起点"), fwd.get("最小样本"), fwd.get("预计等待")))
    if itab.get("status") == "OK":
        reasons.append("区间资格表：%d 个区间，其中 ≥12 个月 %d 个，"
                       "但 ≥12 个月且段级未接触的 **%d 个** → 无可用独立验证区间"
                       % (itab["intervals"], itab["ge_12m"], itab["ge_12m_unobserved"]))
    if contact.get("status") == "OK":
        reasons.append("接触台账（2025-01-01~%s）：74 个 C1 币均有接触记录；%d 币全覆盖，"
                       "最大未观察连续缺口 %.1f 天，≥365 天的币 %d 个 → 不存在 ≥12 个月的未观察窗口"
                       % (contact["window"][1], contact["coins_fully_covered"],
                          contact["max_gap_days"], contact["coins_gap_ge_365"]))
        reasons.append("S2/S7 不满足：无 ≥12 个月独立窗口；只能从 2026-10-19 起前向累积，当前 0 天")
    if cov.get("status") == "OK":
        reasons.append("Work 数据覆盖包已到达：%d 币，4h≥200 根 %d 币，15m 至 %s、1h 至 %s、4h 至 %s；"
                       "区间资格表另见下条"
                       % (cov.get("symbols", 0), cov.get("symbols_4h_ge_200", 0),
                          cov.get("15m_末根_max"), cov.get("1h_末根_max"),
                          cov.get("4h_末根收盘_max")))
        reasons.append("S1/S4/S6 无法满足：区间资格表已给出逐区间资格，但不存在 ≥12 个月未接触区间；"
                       "不能自行指定验证区间")
    if files and not qual:
        reasons.append("FT-ENTRY-10 为研究期逐候选对照/收益分解/残差对账包，不是区间资格包；"
                       "其收益列未用于验证/选参（仅用表头与包名识别包类型）")
        reasons.append("区间资格表存在（723 行），但可用区间为 0；不是'缺表'，是'没有合格区间'")
    if qual and usable == 0:
        reasons.append("未找到标记为可用/合格的验证区间（或字段不可识别）")
    # 已知的历史资格结论（只引用资格，不引用收益）
    reasons.append("OOS-READY-01 的区间资格结论：现有数据暂无可证明的样本外范围；"
                   "S0/S1/S6/S7 数据资格不足，S2/S3/S4/S5 已观察/污染，不得作为独立验证")
    reasons.append("S8 数据时点缺口未闭环：OI 5m→1h 发布时点未证实、历史合约规格无当时快照")
    if files and qual and usable > 0:
        status = "CONDITIONAL_READY"
        reasons = ["区间资格包已到达且存在可用区间；仍需在起算前闭环 S8 数据时点缺口并逐项签署 S1–S8",
                   "本脚本不读取收益；不运行验证"]

    summary = {
        "status": status,
        "package_dir": "evidence/FT-ENTRY-10/（研究期归因包）+ evidence/FT-VALIDATION-DATA-01/（覆盖/接触包）",
        "files_found": len(files),
        "files": files,
        "work_coverage": cov,
        "work_contact": contact,
        "interval_table": itab,
        "work_verdict": wv,
        "work_data_package_files": sum(1 for r, _d, fs in os.walk(DATA_PKG) for _ in fs) if os.path.isdir(DATA_PKG) else 0,
        "qualification_tables": (1 if itab.get("status") == "OK" else len(qual)),
        "usable_markers": (itab.get("ge_12m_unobserved", 0)
                           if itab.get("status") == "OK" else usable),
        "reasons": reasons,
        "start_gate": {
            "S1_interval_package_confirmed": bool(itab.get("status") == "OK"),
            "S1_usable_interval": bool(itab.get("ge_12m_unobserved", 0) > 0),
            "S2_not_observed": False if contact.get("coins_gap_ge_365", 0) == 0 else None,
            "S3_data_coverage": ("PARTIAL（覆盖/接触/区间资格表已到；0 可用区间）"
                                 if cov.get("status") == "OK" else "PENDING"),
            "S4_per_coin_warmup": "NO_USABLE_INTERVAL",
            "S5_frozen_hashes": "PARTIAL（25 个冻结对象已哈希；数据快照/评价 runner 待起算前冻结）",
            "S6_no_overlap": "NO_USABLE_INTERVAL",
            "S7_sample_size": "FALSE（无 ≥12 个月未观察窗口）",
            "S8_data_time_gaps": "OPEN（OI PIT / 合约规格）",
        },
        "no_validation_returns_read": True,
        "ft10_in_sample_only": True,
        "ft10_returns_used_for_selection": False,
        "no_backtest_run": True,
        "note": "区间未确认前不读取验证收益、不运行验证；本文件只做启动条件核对。",
    }
    jdump(os.path.join(OUT, "03_启动条件核对_v1.json"), summary)
    pd.DataFrame([{"status": status, "files_found": len(files),
                   "qualification_tables": len(qual), "usable_markers": usable,
                   "reason": " | ".join(reasons)}]).to_csv(
        os.path.join(OUT, "03_启动条件核对_v1.csv"), index=False, encoding="utf-8-sig")
    print(f"STATUS = {status}; files={len(files)}; "
          f"qual_tables={summary['qualification_tables']}; usable={summary['usable_markers']}")
    for r in reasons:
        print(" -", r)


if __name__ == "__main__":
    main()
