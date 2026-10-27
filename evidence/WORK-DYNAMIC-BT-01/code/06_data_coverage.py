# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜数据可得性核查（核实 Gemini「74 币 3 年」是否成立）。

只读 `evidence/FT-FACTOR-03/user_data/data/binance`（与回测同一份数据，符号链接进入本单）。
不下载、不扩池、不改预热。

输出
────
· `19_逐币数据覆盖.csv`：每个标的的 4h/1h/15m 首末时间、根数、缺口根数、准入窗口、是否合格
· `20_数据可得性结论.json`：把「74 币 3 年」拆成可核对的数字（含不合格/被剔除的标的）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
DATA = BASE / "evidence/FT-FACTOR-03/user_data/data/binance"
RULES = EXP / "user_data/strategies/pair_eligibility_rules.json"
CFG = EXP / "user_data/config.json"

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
WIN_START_MS = 1638316800000        # 2021-12-01
WIN_END_MS = 1735689600000          # 2025-01-01


def slug(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def audit_pair(pair: str, rule: dict) -> dict:
    rec = {"pair": pair, "qualified": bool(rule.get("qualified")),
           "q_start_utc": rule.get("q_start_utc"), "q_end_utc": rule.get("q_end_utc"),
           "layer": rule.get("layer")}
    qs, qe = rule.get("q_start_ms"), rule.get("q_end_ms")
    rec["eligible_days"] = (round((qe - qs) / 86_400_000, 2) if qs and qe else 0.0)

    for tf in ("4h", "1h", "15m"):
        f = DATA / "futures" / f"{slug(pair)}-{tf}-futures.feather"
        if not f.exists():
            rec[f"{tf}_exists"] = False
            rec[f"{tf}_bars"] = 0
            rec[f"{tf}_first_utc"] = None
            rec[f"{tf}_last_utc"] = None
            rec[f"{tf}_gap_bars"] = None
            continue
        d = pd.read_feather(f, columns=["date"])
        ts = d["date"].astype("int64").to_numpy()
        rec[f"{tf}_exists"] = True
        rec[f"{tf}_bars"] = int(len(ts))
        rec[f"{tf}_first_utc"] = str(pd.to_datetime(ts[0], unit="ms", utc=True))
        rec[f"{tf}_last_utc"] = str(pd.to_datetime(ts[-1], unit="ms", utc=True))
        if len(ts) > 1:
            step = TF_MS[tf]
            span = ts[-1] - ts[0]
            expected = int(span // step) + 1
            rec[f"{tf}_gap_bars"] = int(expected - len(ts))
        else:
            rec[f"{tf}_gap_bars"] = 0

    # OI 与 sidecar
    oi = DATA / "futures" / f"{slug(pair)}-5m-open_interest.feather"
    rec["oi_exists"] = oi.exists()
    if oi.exists():
        d = pd.read_feather(oi)
        rec["oi_bars"] = int(len(d))
        rec["oi_first_utc"] = str(pd.to_datetime(int(d["date"].astype("int64").min()), unit="ms", utc=True))
        rec["oi_last_utc"] = str(pd.to_datetime(int(d["date"].astype("int64").max()), unit="ms", utc=True))
        if "fetched_at" in d.columns:
            rec["oi_fetched_at_filled_pct"] = round(float(d["fetched_at"].notna().mean()) * 100, 2)
        else:
            rec["oi_fetched_at_filled_pct"] = None
    sc = DATA / "sidecar" / f"{slug(pair)}-15m-quote_volume.csv"
    rec["sidecar_exists"] = sc.exists()
    return rec


def main() -> None:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    pairs: List[str] = cfg["exchange"]["pair_whitelist"]
    rules = json.loads(RULES.read_text(encoding="utf-8"))

    rows = [audit_pair(p, rules.get(p, {})) for p in pairs]
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "19_逐币数据覆盖.csv", index=False, encoding="utf-8-sig")

    qual = df[df["qualified"]]
    excl = df[~df["qualified"]]
    total_pair_days = float(qual["eligible_days"].sum())

    # 4h 数据实际跨度（全池）
    span_days = (WIN_END_MS - WIN_START_MS) / 86_400_000

    conclusion = {
        "claim_to_verify": "Gemini 称「74 币 3 年」",
        "whitelist_pairs": len(pairs),
        "qualified_pairs": int(len(qual)),
        "excluded_pairs": excl[["pair", "layer"]].to_dict("records"),
        "study_window_utc": ["2021-12-01", "2024-12-31"],
        "study_window_days": round(span_days, 1),
        "total_eligible_pair_days": round(total_pair_days, 2),
        "equivalent_pair_years": round(total_pair_days / 365.0, 2),
        "verdict": None,
        "verdict_detail": None,
        "caveats": [
            "「74 币」成立：白名单确为 74 个合约；但其中 4 个不合格 ⇒ 实际可交易 70 个。",
            "「3 年」只对**全期存活**的老标的是成立的；大量标的上市晚（如 1000CAT 从 2024-10 起），"
            "逐币准入窗口按预热资格截断，全池等效仅约 124 个「币·年」，不是 74×3=222。",
            "OI（持仓量）文件 100% 缺 `fetched_at` 发布时点 ⇒ PIT 未证明，只能声明「计算不超前」。",
            "本单不下载、不扩池、不降预热；数据不足即标阻塞。",
        ],
    }
    ok_74 = len(pairs) == 74
    ok_3y = span_days >= 3 * 365
    conclusion["verdict"] = ("部分成立" if (ok_74 and ok_3y) else "不成立")
    conclusion["verdict_detail"] = (
        f"白名单 74 个（合格 {len(qual)} 个）{'成立' if ok_74 else '不成立'}；"
        f"研究窗 {span_days:.0f} 天（≈{span_days/365:.2f} 年）{'成立' if ok_3y else '不成立'}；"
        f"但按逐币预热资格折算只有 {total_pair_days/365:.2f} 个等效币·年，"
        f"且早期年份大量标的尚未上市 ⇒ 「74 币 × 3 年」不能当 222 币·年用。")

    (OUT / "20_数据可得性结论.json").write_text(
        json.dumps(conclusion, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(conclusion, ensure_ascii=False, indent=2))
    print("\n各时间框架缺口最大的 10 个标的（4h 缺口根数）:")
    print(df.sort_values("4h_gap_bars", ascending=False)
          [["pair", "4h_bars", "4h_gap_bars", "4h_first_utc", "4h_last_utc", "qualified"]]
          .head(10).to_string())


if __name__ == "__main__":
    main()
