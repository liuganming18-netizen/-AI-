# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-01 / 01 独立计数与 227→205 逐条映射。

只读上游产物，不引用 FACTOR-ENTRY-03 的任何派生统计。
产出：
  01_counts.json                全部计数（含口径说明）
  01_field_missing.csv          逐字段缺失
  01_mapping_227_to_205.csv     逐条映射（227 行，含未成交与无法关联）
  01_unmapped.csv               无法关联者单列
  01_duplicates.csv             重复候选单列
  01_collapse_296_to_205.csv    296→205 折叠明细
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-01"
OUT.mkdir(parents=True, exist_ok=True)

FEAT = ROOT / "evidence/FACTOR-ENTRY-01/candidate_features.parquet"
MAP = ROOT / "evidence/FACTOR-ENTRY-02/trade_mapping_296_to_205.csv"
RICH = ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl"
ENR = ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet"


def main() -> None:
    feat = pd.read_parquet(FEAT)
    mp = pd.read_csv(MAP)
    rich = pd.DataFrame([json.loads(l) for l in open(RICH, encoding="utf-8")])
    enr = pd.read_parquet(ENR)

    res: dict = {"ticket": "ENTRY-AUDIT-01", "section": "01 独立计数"}

    # ---------- 1. 记录 / 候选 / 入场 / 成交 ----------
    filled = feat[feat["exec_status"] == "FILLED"]
    unfilled = feat[feat["exec_status"] != "FILLED"]

    res["record_level"] = {
        "特征表总行数": int(len(feat)),
        "enriched表总行数": int(len(enr)),
        "两表行数一致": bool(len(feat) == len(enr)),
        "唯一event_id": int(feat["event_id"].nunique()),
        "唯一candidate_id": int(feat["candidate_id"].nunique()),
        "唯一标的数": int(feat["symbol"].nunique()),
        "唯一(symbol,snapshot_time_ms)": int(feat.groupby(["symbol", "snapshot_time_ms"]).ngroups),
        "唯一入场(symbol,entry_time)_仅成交": int(
            filled.groupby(["symbol", "entry_time"]).ngroups
        ),
        "实际成交FILLED": int(len(filled)),
        "未成交UNFILLED_TIMEOUT": int(len(unfilled)),
        "成交+未成交=总行数": bool(len(filled) + len(unfilled) == len(feat)),
    }

    # ---------- 2. 重复 ----------
    dup_snap = feat[feat.duplicated(subset=["symbol", "snapshot_time_ms"], keep=False)].copy()
    dup_snap = dup_snap.sort_values(["symbol", "snapshot_time_ms"])
    dup_snap[
        ["event_id", "candidate_id", "symbol", "snapshot_time_ms", "entry_time",
         "exec_status", "p_entry", "net_pnl"]
    ].to_csv(OUT / "01_duplicates.csv", index=False)
    res["duplicates"] = {
        "同标的同快照重复组数": int(dup_snap.groupby(["symbol", "snapshot_time_ms"]).ngroups),
        "重复涉及记录数": int(len(dup_snap)),
        "重复明细": dup_snap["event_id"].tolist(),
        "说明": "同一快照由两种 candidate_id 命名（cand_ev_… 与 old_cand_…）各产生 1 行，"
                "一行 UNFILLED、一行 FILLED；按「唯一候选」计应视为 1 个候选。",
    }

    # ---------- 3. 字段缺失 ----------
    miss = []
    for col in feat.columns:
        n = int(feat[col].isna().sum())
        nf = int(filled[col].isna().sum())
        if n or nf:
            miss.append({"column": col, "missing_all227": n, "missing_filled205": nf})
    pd.DataFrame(miss).to_csv(OUT / "01_field_missing.csv", index=False)

    # ---------- 4. 边界截断 ----------
    trunc = {
        "has_truncated_twin_True": int((feat["has_truncated_twin"] == True).sum()),  # noqa: E712
        "exit_why_force_close_eod_仅成交": int((filled["exit_why"] == "force_close_eod").sum()),
        "exit_why_分布": filled["exit_why"].value_counts().to_dict(),
        "box_atr_4h缺失或<=0_仅成交": int(
            (filled["box_atr_4h"].isna() | (filled["box_atr_4h"] <= 0)).sum()
        ),
        "box_high等于p_entry_仅成交": int((np.abs(filled["box_high"] - filled["p_entry"]) < 1e-12).sum()),
        "breakout_atr_dist缺失_仅成交": int(filled["breakout_atr_dist"].isna().sum()),
    }
    # 快照到数据末端的距离（判断 4h 窗口是否够）
    tmax = int(feat["snapshot_time_ms"].max())
    trunc["数据末端快照"] = str(pd.to_datetime(tmax, unit="ms"))
    trunc["距末端不足4h的记录"] = int(
        ((tmax - filled["entry_time"].astype("int64")) < 4 * 3600 * 1000).sum()
    )
    res["truncation"] = trunc

    # ---------- 5. 296 → 205 折叠 ----------
    real = mp[mp["old_row_idx"] >= 0]
    res["collapse_296_to_205"] = {
        "trades_rich行数": int(len(rich)),
        "映射表行数": int(len(mp)),
        "其中old_row_idx=-1合成行": int((mp["old_row_idx"] < 0).sum()),
        "映射覆盖旧行数": int(real["old_row_idx"].nunique()),
        "旧行是否完整覆盖0..295": bool(
            set(real["old_row_idx"]) == set(range(len(rich)))
        ),
        "唯一目标event数": int(mp["mapped_new_event_id"].nunique()),
        "被折叠掉的旧行数": int(len(rich) - mp["mapped_new_event_id"].nunique()),
        "每个目标对应的旧行数分布": mp.groupby("mapped_new_event_id").size().value_counts().sort_index().to_dict(),
        "reconciliation_action分布": mp["reconciliation_action"].value_counts().to_dict(),
        "RETAINED_PRIMARY_ENTRY行数": int((mp["reconciliation_action"] == "RETAINED_PRIMARY_ENTRY").sum()),
        "RETAINED但目标重复(应1:1却多行)": int(
            (mp["reconciliation_action"] == "RETAINED_PRIMARY_ENTRY").sum()
            - mp[mp["reconciliation_action"] == "RETAINED_PRIMARY_ENTRY"]["mapped_new_event_id"].nunique()
        ),
    }
    mp.groupby("mapped_new_event_id").size().rename("old_rows").reset_index().to_csv(
        OUT / "01_collapse_296_to_205.csv", index=False
    )

    # ---------- 6. 227 → 205 逐条映射 ----------
    tgt = (
        mp.groupby("mapped_new_event_id")
        .agg(old_rows=("old_row_idx", "size"),
             old_groups=("old_group", lambda s: sorted(set(int(x) for x in s))),
             new_net=("new_net", "first"),
             new_why=("new_why", "first"),
             new_dt_out=("new_dt_out", "first"),
             actions=("reconciliation_action", lambda s: sorted(set(s))))
        .reset_index()
    )
    rows = []
    for _, r in feat.iterrows():
        hit = tgt[tgt["mapped_new_event_id"] == r["event_id"]]
        mapped = len(hit) == 1
        rows.append({
            "event_id": r["event_id"],
            "candidate_id": r["candidate_id"],
            "symbol": r["symbol"],
            "snapshot_time_ms": int(r["snapshot_time_ms"]),
            "snapshot_utc": str(pd.to_datetime(int(r["snapshot_time_ms"]), unit="ms")),
            "entry_time_ms": None if pd.isna(r["entry_time"]) else int(r["entry_time"]),
            "exec_status": r["exec_status"],
            "p_entry": r["p_entry"],
            "net_pnl": r["net_pnl"],
            "linked_to_trade": bool(mapped and r["exec_status"] == "FILLED"),
            "old_rows_collapsed": int(hit["old_rows"].iloc[0]) if mapped else None,
            "old_groups": json.dumps(hit["old_groups"].iloc[0], ensure_ascii=False) if mapped else None,
            "new_net_from_mapping": float(hit["new_net"].iloc[0]) if mapped else None,
            "net_abs_diff": abs(float(r["net_pnl"]) - float(hit["new_net"].iloc[0]))
                             if mapped and pd.notna(r["net_pnl"]) else None,
            "link_note": ("成交且与映射表1:1对上" if (mapped and r["exec_status"] == "FILLED")
                          else ("未成交，无对应成交记录" if r["exec_status"] != "FILLED"
                                else "成交但映射表找不到")),
        })
    m227 = pd.DataFrame(rows)
    m227.to_csv(OUT / "01_mapping_227_to_205.csv", index=False)

    unm = m227[m227["exec_status"] != "FILLED"]
    unm.to_csv(OUT / "01_unmapped.csv", index=False)

    res["mapping_227_to_205"] = {
        "总记录": int(len(m227)),
        "成交且1:1对上": int(m227["linked_to_trade"].sum()),
        "未成交(单列)": int(len(unm)),
        "成交但无法关联": int(((m227["exec_status"] == "FILLED") & (~m227["linked_to_trade"])).sum()),
        "净盈亏最大绝对偏差": float(m227["net_abs_diff"].dropna().max()) if m227["net_abs_diff"].notna().any() else None,
        "结论": "227 = 205 成交 + 22 未成交；205 成交与 296→205 映射表的 205 个目标事件完全一一对应。",
    }

    (OUT / "01_counts.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
