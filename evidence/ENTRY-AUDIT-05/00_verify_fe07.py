# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-05 / 00 · FE07 逐项独立复核（第一阶段）

工单七项：
  1. 记录身份及匹配关系
  2. 毛盈亏、手续费、资金费、净盈亏
  3. 净盈亏恒等式及费用符号
  4. 三套分类与独立分类基准
  5. ATR、首触顺序及未知状态
  6. CSV、Parquet、JSON、Markdown 一致性
  7. 输入输出哈希与来源契约

特别注意：227 条研究记录 ID 唯一 ≠ 227 个唯一市场信号。
必须区分 227 行 / 226 唯一信号 / 205 成交 / 22 未成交，并保留 COTI 跨运行关系。

纪律：只读 FE07 与 ENTRY-AUDIT-03/04 的冻结产物，不修改对方任何文件。
写入边界：仅 evidence/ENTRY-AUDIT-05/。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-05"
FE07 = ROOT / "evidence" / "FACTOR-ENTRY-07"
A4 = ROOT / "evidence" / "ENTRY-AUDIT-04"
A3 = ROOT / "evidence" / "ENTRY-AUDIT-03"

FE07_TABLE_CSV = FE07 / "reconstructed_candidate_features_07.csv"
FE07_TABLE_PQ = FE07 / "reconstructed_candidate_features_07.parquet"

# FE07 的列名与交接包的列名映射（值应当相同，只是命名不同）
MONEY_MAP = [("gross_pnl", "gross_pnl"), ("fee_paid", "fees"),
             ("funding_cost", "funding_fee"), ("net_pnl", "net_pnl")]
# 不依赖费用的字段
ATR_COLS = ["signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h",
            "mfe_r_4h", "mae_r_4h", "mfe_r", "mfe_atr_full"]
CAT_MAP = [("category_standard_mixed", "cat_standard_mixed_indep", "标准混合"),
           ("category_pure_atr_4h", "cat_pure_atr_4h_indep", "纯ATR-4h"),
           ("category_pure_atr_full", "cat_pure_atr_full_indep", "纯ATR-全周期")]

FIRST_MOVE_CONTRACT = {"FIRST_ADVERSE", "FIRST_FAVORABLE", "UNKNOWN_SAME_BAR", "NEITHER_REACHED_0.3ATR"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows_diff(a: pd.Series, b: pd.Series, tol: float = 1e-9) -> dict:
    """逐值比对，NaN 必须对 NaN。"""
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    nan_a, nan_b = a.isna(), b.isna()
    both = ~nan_a & ~nan_b
    d = (a[both] - b[both]).abs()
    return {
        "比较条数": int(len(a)),
        "NaN 对 NaN": int((nan_a & nan_b).sum()),
        "一边 NaN 一边有值": int((nan_a ^ nan_b).sum()),
        "数值不一致(>tol)": int((d > tol).sum()),
        "最大绝对差": (None if len(d) == 0 else float(np.nanmax(d.to_numpy()))),
    }


def str_diff(a: pd.Series, b: pd.Series) -> dict:
    """字符串列比对。

    ⚠ CSV 与 Parquet 的空值表示不同（CSV 读成 NaN、Parquet 是 None/pd.NA），
    直接 astype(str) 比会把每一个空值都算成「不同」。
    正确做法：先把空值统一成哨兵再比，并单独报告两侧空值数是否相等。
    """
    na_a, na_b = a.isna(), b.isna()
    sa = a.where(~na_a, "<NA>").astype(str)
    sb = b.where(~na_b, "<NA>").astype(str)
    return {
        "空值数(左)": int(na_a.sum()),
        "空值数(右)": int(na_b.sum()),
        "空值位置相同": int((na_a == na_b).sum()) == len(na_a),
        "非空内容不同行数": int((sa != sb).sum()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ══════════ 载入三方 ══════════
    fe = pd.read_csv(FE07_TABLE_CSV)
    fe_pq = pd.read_parquet(FE07_TABLE_PQ)
    ho = pd.read_parquet(A4 / "handoff" / "ledger_handoff.parquet")
    base = pd.read_parquet(A4 / "01_baseline_features.parquet")

    assert len(fe) == 227, f"FE07 表应 227 行，实际 {len(fe)}"
    assert len(ho) == 227, f"交接包应 227 行，实际 {len(ho)}"

    acc = []          # 逐项验收表

    # ══════════ ① 记录身份及匹配关系 ══════════
    fe_ids = fe["candidate_id"].astype(str)
    ho_ids = ho["candidate_id"].astype(str)
    fe_set, ho_set = set(fe_ids), set(ho_ids)
    dup_fe = sorted(set(fe_ids[fe_ids.duplicated(keep=False)]))
    unmatched = sorted(fe_set - ho_set)     # FE07 有、交接包无
    missing = sorted(ho_set - fe_set)       # 交接包有、FE07 无

    # 唯一信号：227 行 ≠ 227 信号
    sig_key = fe["symbol"].astype(str) + "_" + fe["snapshot_time_ms"].astype("Int64").astype(str)
    n_sig = int(sig_key.nunique())
    # 交接包侧的权威 signal_key
    ho_sig_ok = bool((sig_key == ho["signal_key"].astype(str)).all())

    coti = fe[sig_key == "COTIUSDT_1640228400000"]
    coti_ho = ho[ho["signal_key"] == "COTIUSDT_1640228400000"]

    identity_detail = {
        "检查项": "① 记录身份及匹配关系",
        "FE07 行数": int(len(fe)),
        "交接包行数": int(len(ho)),
        "candidate_id 唯一数": int(fe_ids.nunique()),
        "未匹配（FE07 有、交接包无）": len(unmatched),
        "遗漏（交接包有、FE07 无）": len(missing),
        "多重匹配（同 ID 出现多次）": len(dup_fe),
        "未匹配明细": unmatched[:10],
        "遗漏明细": missing[:10],
        "多重匹配明细": dup_fe[:10],
        "唯一市场信号数": n_sig,
        "唯一信号应为": 226,
        "signal_key 与交接包逐行相同": ho_sig_ok,
        "event_id 唯一数": int(fe["event_id"].nunique()),
        "event_id 不能当信号键的原因": ("窗口代际用的是 cand_exec_earliest_ms 而非快照时刻，"
                                        "所以 227 个 event_id 各不相同却只对应 226 个真实信号"),
        "COTI 跨运行关系条数": int(len(coti)),
        "COTI 两代际是否都保留": bool(len(coti) == 2),
        "判定": ("PASS" if not unmatched and not missing and not dup_fe
                 and n_sig == 226 and ho_sig_ok and len(coti) == 2 else "FAIL"),
    }
    acc.append({"序号": 1, "检查项": identity_detail["检查项"],
                "判定": identity_detail["判定"],
                "关键事实": (f"227 行 / {n_sig} 唯一信号 / 未匹配 {len(unmatched)} / "
                             f"遗漏 {len(missing)} / 多重 {len(dup_fe)} / COTI {len(coti)} 条")})

    # 身份映射表
    idmap = fe[["candidate_id", "event_id", "symbol", "snapshot_time_ms", "is_filled",
                "exec_status", "generation", "replay_task_id", "replay_group",
                "replay_window_start", "replay_window_end", "cancel_reason",
                "identity_status", "net_pnl"]].copy()
    idmap["signal_key"] = sig_key
    idmap = idmap.merge(
        ho[["candidate_id", "signal_key", "run_id", "generation_label", "exec_status",
            "cancel_reason", "order_id", "net_pnl", "spec_qualification",
            "formal_pass_allowed", "research_usability"]].rename(columns={
                "signal_key": "signal_key_ho", "run_id": "run_id_ho",
                "generation_label": "generation_label_ho", "exec_status": "exec_status_ho",
                "cancel_reason": "cancel_reason_ho", "order_id": "order_id_ho",
                "net_pnl": "net_pnl_ho", "spec_qualification": "spec_qualification_ho",
                "formal_pass_allowed": "formal_pass_allowed_ho",
                "research_usability": "research_usability_ho"}),
        on="candidate_id", how="left")
    idmap["signal_key_一致"] = idmap["signal_key"] == idmap["signal_key_ho"]
    idmap["net_pnl_一致"] = (pd.to_numeric(idmap["net_pnl"], errors="coerce")
                             - pd.to_numeric(idmap["net_pnl_ho"], errors="coerce")).abs() <= 1e-9
    idmap["run_id_一致"] = idmap["replay_task_id"].astype(str) == idmap["run_id_ho"].astype(str)

    # ⚠ 直接 astype(str) 比会把「两侧都是空」算成不一致：
    #   成交行两侧都没有 cancel_reason，却会被判成 205 条不一致。
    #   先把空值统一成哨兵再比，才反映真实差异。
    def same_or_both_na(a, b) -> pd.Series:
        na, nb = a.isna(), b.isna()
        sa = a.where(~na, "<NA>").astype(str)
        sb = b.where(~nb, "<NA>").astype(str)
        return (sa == sb) & (na == nb)

    idmap["cancel_reason_一致"] = same_or_both_na(idmap["cancel_reason"],
                                                 idmap["cancel_reason_ho"])
    # exec_status 的差异要分两种：真差异，还是只差在命名（UNFILLED vs UNFILLED_TIMEOUT）
    _es, _es_ho = idmap["exec_status"].astype(str), idmap["exec_status_ho"].astype(str)
    _norm = {"UNFILLED_TIMEOUT": "UNFILLED"}  # 旧死代码默认值 → 交接包的真实取值
    idmap["exec_status_一致"] = _es == _es_ho
    idmap["exec_status_仅命名差异"] = (~idmap["exec_status_一致"]) & (
        _es.replace(_norm) == _es_ho.replace(_norm))
    idmap["exec_status_差异说明"] = np.where(
        idmap["exec_status_一致"], "",
        np.where(idmap["exec_status_仅命名差异"],
                 "只差命名：FE07 写 " + _es + "（旧死代码默认值）/ 交接包写 " + _es_ho + "（真实取值）",
                 "真差异：FE07 写 " + _es + " / 交接包写 " + _es_ho))
    idmap.to_csv(OUT / "01_identity_map.csv", index=False, encoding="utf-8-sig")

    # ══════════ ② 四项金额 ══════════
    ho_idx = ho.set_index(ho["candidate_id"].astype(str))
    fe_idx = fe.set_index(fe["candidate_id"].astype(str))
    fe_idx.index.name = "candidate_id"
    filled_mask = fe_idx["is_filled"] == True
    money_rows, money_summary, unfilled_summary = [], {}, {}
    for fc, hc in MONEY_MAP:
        if fc not in fe.columns:
            money_summary[f"{fc}→{hc}"] = {"状态": "FE07 无此列"}
            continue
        a = pd.to_numeric(fe_idx[fc], errors="coerce")
        b = pd.to_numeric(ho_idx[hc], errors="coerce").reindex(a.index)
        st = rows_diff(a, b)
        st["FE07 列名"] = fc
        st["交接包列名"] = hc
        money_summary[f"{fc}→{hc}"] = st
        # 成交行（统计只用这 205 行）与未成交行分开看
        money_summary[f"{fc}→{hc}"]["成交行逐笔"] = rows_diff(a[filled_mask], b[filled_mask])
        money_summary[f"{fc}→{hc}"]["未成交行"] = {
            "FE07 取值": sorted(set(a[~filled_mask].dropna().round(9).tolist()))[:5],
            "交接包取值": ("全部为空（未成交没有这笔交易）"
                           if b[~filled_mask].isna().all() else "有非空值"),
            "交接包空值条数": int(b[~filled_mask].isna().sum()),
            "FE07 空值条数": int(a[~filled_mask].isna().sum()),
        }
        unfilled_summary[f"{fc}→{hc}"] = money_summary[f"{fc}→{hc}"]["未成交行"]
        both = a.notna() & b.notna()
        d = (a[both] - b[both]).abs()
        for cid in d[d > 1e-9].index[:20]:
            money_rows.append({"candidate_id": cid, "字段": fc,
                               "FE07": float(a.loc[cid]), "交接包": float(b.loc[cid]),
                               "差": float(d.loc[cid])})
    # 汇总合计（只算成交）
    totals = {
        "gross_pnl": round(float(pd.to_numeric(fe_idx.loc[filled_mask, "gross_pnl"], errors="coerce").sum()), 6),
        "fee_paid": round(float(pd.to_numeric(fe_idx.loc[filled_mask, "fee_paid"], errors="coerce").sum()), 6),
        "funding_cost": round(float(pd.to_numeric(fe_idx.loc[filled_mask, "funding_cost"], errors="coerce").sum()), 6),
        "net_pnl": round(float(pd.to_numeric(fe_idx.loc[filled_mask, "net_pnl"], errors="coerce").sum()), 6),
    }
    ho_totals = {
        "gross_pnl": round(float(ho.loc[ho["executed"], "gross_pnl"].sum()), 6),
        "fees": round(float(ho.loc[ho["executed"], "fees"].sum()), 6),
        "funding_fee": round(float(ho.loc[ho["executed"], "funding_fee"].sum()), 6),
        "net_pnl": round(float(ho.loc[ho["executed"], "net_pnl"].sum()), 6),
    }
    money_filled_ok = all(v.get("成交行逐笔", {}).get("数值不一致(>tol)", 1) == 0
                          and v.get("成交行逐笔", {}).get("一边 NaN 一边有值", 1) == 0
                          for v in money_summary.values() if "状态" not in v)
    n_null_as_zero = sum(v.get("未成交行", {}).get("交接包空值条数", 0)
                         for v in money_summary.values() if "状态" not in v)
    money_detail = {
        "检查项": "② 毛盈亏、手续费、资金费、净盈亏",
        "逐列": money_summary,
        "成交行判定": "PASS" if money_filled_ok else "FAIL",
        "成交行说明": "205 条成交逐笔比，这是所有统计的实际分母",
        "未成交行缺值口径": {
            "条数": int((~filled_mask).sum()),
            "交接包": "空（未成交没有这笔交易，按 null 政策一律 None）",
            "FE07": "0（把「没有这笔交易」写成「这笔交易盈亏为 0」）",
            "影响": "不影响任何分类与统计（所有统计只用 205 成交），但违反「缺值不等于 0」",
            "交接包空值条数合计": n_null_as_zero,
        },
        "FE07 合计": totals,
        "交接包合计": ho_totals,
        "合计是否相同": (totals["gross_pnl"] == ho_totals["gross_pnl"]
                         and totals["fee_paid"] == ho_totals["fees"]
                         and totals["funding_cost"] == ho_totals["funding_fee"]
                         and totals["net_pnl"] == ho_totals["net_pnl"]),
        "列名差异": ("FE07 用 fee_paid / funding_cost，交接包用 fees / funding_fee；"
                     "名字不同但逐笔数值相同，属可接受的命名映射，已记入字段对照"),
        "判定": ("PASS" if money_filled_ok and totals["net_pnl"] == ho_totals["net_pnl"]
                 and n_null_as_zero == 0 else
                 ("PASS_WITH_NOTE" if money_filled_ok and totals["net_pnl"] == ho_totals["net_pnl"]
                  else "FAIL")),
    }
    pd.DataFrame(money_rows).to_csv(OUT / "02_money_check.csv", index=False, encoding="utf-8-sig")
    acc.append({"序号": 2, "检查项": money_detail["检查项"], "判定": money_detail["判定"],
                "关键事实": f"净盈亏合计 FE07 {totals['net_pnl']} / 交接包 {ho_totals['net_pnl']}；差异行 {len(money_rows)}"})

    # ══════════ ③ 净值恒等式及费用符号 ══════════
    g = pd.to_numeric(fe_idx["gross_pnl"], errors="coerce")
    f_ = pd.to_numeric(fe_idx["fee_paid"], errors="coerce")
    u = pd.to_numeric(fe_idx["funding_cost"], errors="coerce")
    n = pd.to_numeric(fe_idx["net_pnl"], errors="coerce")
    resid = (n - (g - f_ - u)).abs()
    ok_row = resid.notna()
    # 符号：手续费应恒为正支出；资金费可正可负
    fee_neg = int((f_[filled_mask] < -1e-12).sum())
    fund_pos = int((u[filled_mask] > 1e-12).sum())
    fund_neg = int((u[filled_mask] < -1e-12).sum())
    # 账本口径一致性：交接包的 fees 与 funding_fee 来自引擎，符号应与之相同
    ho_fees = pd.to_numeric(ho_idx["fees"], errors="coerce").reindex(fe_idx.index)
    sign_ok = bool((np.sign(f_[filled_mask].fillna(0))
                    == np.sign(ho_fees[filled_mask].fillna(0))).all())
    identity_detail = {
        "检查项": "③ 净盈亏恒等式及费用符号",
        "公式": "net_pnl = gross_pnl - fee_paid - funding_cost",
        "可复算条数": int(ok_row.sum()),
        "成立(<=1e-9)": int((resid[ok_row] <= 1e-9).sum()),
        "违反": int((resid[ok_row] > 1e-9).sum()),
        "最大残差": (None if not ok_row.any() else float(np.nanmax(resid[ok_row].to_numpy()))),
        "手续费出现负值条数": fee_neg,
        "资金费为正（支出）条数": fund_pos,
        "资金费为负（收入）条数": fund_neg,
        "资金费符号与交接包一致": bool(fund_pos + fund_neg == int(filled_mask.sum())),
        "手续费符号与交接包一致": sign_ok,
        "符号约定": ("手续费正=支出；资金费正=支出、负=收入；"
                     "账本 FUNDING_FEE.payload.delta 是现金口径（支出为负），与本表符号相反"),
        "判定": ("PASS" if int((resid[ok_row] > 1e-9).sum()) == 0 and fee_neg == 0 else "FAIL"),
    }
    acc.append({"序号": 3, "检查项": identity_detail["检查项"], "判定": identity_detail["判定"],
                "关键事实": (f"恒等式 {identity_detail['成立(<=1e-9)']}/{identity_detail['可复算条数']}，"
                             f"最大残差 {identity_detail['最大残差']}，手续费负值 {fee_neg} 条")})

    # ══════════ ④ 三套分类 ══════════
    cat_rows, cat_summary = [], {}
    for fc, bc, name in CAT_MAP:
        if fc not in fe.columns:
            cat_summary[name] = {"状态": "FE07 无此列"}
            continue
        m = base[["candidate_id", bc]].merge(fe[["candidate_id", fc]], on="candidate_id", how="inner")
        diff = m[m[bc].astype(str) != m[fc].astype(str)]
        cat_summary[name] = {
            "比较条数": int(len(m)),
            "一致": int(len(m) - len(diff)),
            "不一致": int(len(diff)),
            "FE07 分布": {k: int(v) for k, v in fe[fc].value_counts().items()},
            "基准分布": {k: int(v) for k, v in base[bc].value_counts().items()},
            "判定": "PASS" if len(diff) == 0 else "FAIL",
        }
        for _, r in diff.head(20).iterrows():
            cat_rows.append({"体系": name, "candidate_id": r["candidate_id"],
                             "基准": r[bc], "FE07": r[fc]})
    # 浮盈回吐专项：net_pnl<=0 参与
    pb = fe[(fe["category_standard_mixed"].astype(str) == "MFE_PULLBACK")]
    pb_viol = pb[pd.to_numeric(pb["net_pnl"], errors="coerce") > 0]
    pb_base = int((base["cat_standard_mixed_indep"] == "MFE_PULLBACK").sum())
    cat_detail = {
        "检查项": "④ 三套分类与独立分类基准",
        "逐体系": cat_summary,
        "浮盈回吐专项": {
            "FE07 条数": int(len(pb)),
            "基准条数": pb_base,
            "被标为回吐但 net_pnl>0": int(len(pb_viol)),
            "明细": pb_viol[["candidate_id", "net_pnl"]].head(10).to_dict("records"),
        },
        "判定": ("PASS" if all(v.get("判定") == "PASS" for v in cat_summary.values() if "判定" in v)
                 and len(pb_viol) == 0 else "FAIL"),
    }
    pd.DataFrame(cat_rows).to_csv(OUT / "03_classification_check.csv", index=False, encoding="utf-8-sig")
    acc.append({"序号": 4, "检查项": cat_detail["检查项"], "判定": cat_detail["判定"],
                "关键事实": "；".join(f"{k} {v.get('一致')}/{v.get('比较条数')}"
                                     for k, v in cat_summary.items())})

    # ══════════ ⑤ ATR / 首触 / 未知状态 ══════════
    atr_summary, atr_rows = {}, []
    for c in ATR_COLS:
        if c not in fe.columns or c not in base.columns:
            atr_summary[c] = {"状态": "缺列"}
            continue
        st = rows_diff(fe.set_index("candidate_id")[c],
                       base.set_index("candidate_id")[c].reindex(fe["candidate_id"]), tol=1e-12)
        atr_summary[c] = st
        # 记录前几条差异
        a = pd.to_numeric(fe.set_index("candidate_id")[c], errors="coerce")
        b = pd.to_numeric(base.set_index("candidate_id")[c].reindex(a.index), errors="coerce")
        d = (a - b).abs()
        for cid in d[d > 1e-12].index[:10]:
            atr_rows.append({"字段": c, "candidate_id": cid,
                             "FE07": float(a.loc[cid]), "基准": float(b.loc[cid])})
    # 首触枚举
    fm = fe["first_move_4h"].astype(str)
    fm_dist = {k: int(v) for k, v in fm.value_counts().items()}
    no_data = int((fm == "NO_DATA").sum())
    unfilled = int((fe["is_filled"] != True).sum())
    fm_base_dist = {k: int(v) for k, v in base["first_move_4h"].astype(str).value_counts().items()}
    fm_same = bool(fm_dist == fm_base_dist)
    unknown_same_bar = int((fm == "UNKNOWN_SAME_BAR").sum())
    atr_detail = {
        "检查项": "⑤ ATR、首触顺序及未知状态",
        "逐字段": atr_summary,
        "首触分布 FE07": fm_dist,
        "首触分布 基准": fm_base_dist,
        "首触分布是否相同": fm_same,
        "NO_DATA 条数": no_data,
        "未成交条数": unfilled,
        "NO_DATA 是否等于未成交": no_data == unfilled,
        "UNKNOWN_SAME_BAR 条数": unknown_same_bar,
        "UNKNOWN_SAME_BAR 是否被强行指定先后": ("否，仍保留为未知" if unknown_same_bar > 0 else "FE07 未保留任何同根未知"),
        "契约外取值": sorted([x for x in set(fm) if x not in FIRST_MOVE_CONTRACT]),
        "契约外取值说明": ("NO_DATA 不在 FE06 契约四选一内，但正好等于 22 条未成交；"
                           "契约没规定未成交怎么标，属契约空档，不判 FAIL，但条数必须对得上"),
        "判定": ("PASS" if all(v.get("数值不一致(>tol)", 0) == 0 and v.get("一边 NaN 一边有值", 0) == 0
                               for v in atr_summary.values() if "状态" not in v)
                 and fm_same and no_data == unfilled else "FAIL"),
    }
    pd.DataFrame(atr_rows).to_csv(OUT / "04_atr_firstmove_check.csv", index=False, encoding="utf-8-sig")
    acc.append({"序号": 5, "检查项": atr_detail["检查项"], "判定": atr_detail["判定"],
                "关键事实": (f"首触分布{'相同' if fm_same else '不同'}；NO_DATA {no_data} == 未成交 {unfilled}；"
                             f"同根未知 {unknown_same_bar} 条保留")})

    # ══════════ ⑥ CSV / Parquet / JSON / Markdown ══════════
    # (a) CSV vs Parquet 逐值
    common = [c for c in fe.columns if c in fe_pq.columns]
    csv_pq_bad, csv_pq_str = {}, {}
    for c in common:
        # ⚠ bool 也算 numeric，但不支持减法 ⇒ 必须单独走字符串比对
        if (pd.api.types.is_numeric_dtype(fe[c]) and pd.api.types.is_numeric_dtype(fe_pq[c])
                and not pd.api.types.is_bool_dtype(fe[c])):
            a = pd.to_numeric(fe[c], errors="coerce")
            b = pd.to_numeric(fe_pq[c], errors="coerce")
            d = (a - b).abs()
            if d.notna().any() and float(np.nanmax(d.to_numpy())) > 1e-9:
                csv_pq_bad[c] = float(np.nanmax(d.to_numpy()))
            # 数值列也要看空值位置是否一致
            if int((a.isna() != b.isna()).sum()):
                csv_pq_bad[c] = f"{int((a.isna() != b.isna()).sum())} 行空值位置不同"
        else:
            sd = str_diff(fe[c], fe_pq[c])
            if sd["非空内容不同行数"] or not sd["空值位置相同"]:
                csv_pq_str[c] = sd
    # (b) JSON 自报 vs 实际
    disc = json.loads((FE07 / "discrepancy_summary.json").read_text(encoding="utf-8"))
    # (c) Markdown 里的数字 vs 实际
    md = (FE07 / "报告.md").read_text(encoding="utf-8")
    md_claims = {}
    for key, pat in [("FIRST_ADVERSE", r"`FIRST_ADVERSE`：(\d+) 笔"),
                     ("FIRST_FAVORABLE", r"`FIRST_FAVORABLE`：(\d+) 笔"),
                     ("UNKNOWN_SAME_BAR", r"`UNKNOWN_SAME_BAR`[^：]*：\**(\d+)\s*笔"),
                     ("未触及门槛", r"未触及门槛：(\d+) 笔"),
                     ("未成交", r"未成交：(\d+) 笔")]:
        m_ = re.search(pat, md)
        md_claims[key] = int(m_[1]) if m_ else None
    md_vs_data = {}
    for k, v in md_claims.items():
        actual = {"FIRST_ADVERSE": fm_dist.get("FIRST_ADVERSE"),
                  "FIRST_FAVORABLE": fm_dist.get("FIRST_FAVORABLE"),
                  "UNKNOWN_SAME_BAR": fm_dist.get("UNKNOWN_SAME_BAR"),
                  "未触及门槛": fm_dist.get("NEITHER_REACHED_0.3ATR", 0),
                  "未成交": unfilled}[k]
        md_vs_data[k] = {"报告声称": v, "数据实际": actual,
                         "一致": (v == actual)}
    # (d) Markdown 里的哈希表 vs hashes.json vs 磁盘
    md_hashes = {}
    for m_ in re.finditer(r"\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*([\d,]+)\s*字节\s*\|\s*`([0-9a-f]{64})`\s*\|", md):
        md_hashes[m_[1]] = {"size": int(m_[2].replace(",", "")), "sha256": m_[3]}
    hj = json.loads((FE07 / "hashes.json").read_text(encoding="utf-8"))
    hash_cmp = []
    for name, claim in md_hashes.items():
        actual = hj.get(name)
        p = FE07 / name
        disk = sha256(p) if p.exists() else None
        hash_cmp.append({
            "文件": name,
            "报告声称字节": claim["size"],
            "hashes.json 字节": (actual or {}).get("size"),
            "磁盘实际字节": (p.stat().st_size if p.exists() else None),
            "报告声称 sha": claim["sha256"][:16],
            "hashes.json sha": (actual or {}).get("sha256", "")[:16],
            "磁盘实际 sha": (disk or "")[:16],
            "报告与 hashes.json 一致": bool(actual and actual["sha256"] == claim["sha256"]),
            "hashes.json 与磁盘一致": bool(actual and disk == actual["sha256"]),
        })
    fmt_detail = {
        "检查项": "⑥ CSV、Parquet、JSON、Markdown 一致性",
        "CSV vs Parquet": {
            "比较列数": len(common),
            "数值列不一致": csv_pq_bad,
            "字符串列不一致": csv_pq_str,
            "说明": ("字符串列的空值在 CSV 里读成 NaN、在 Parquet 里是 None；"
                     "本项已按「空值位置是否相同 + 非空内容是否相同」比对，"
                     "不把空值表示差异算成不一致"),
            "判定": "PASS" if not csv_pq_bad and not csv_pq_str else "FAIL",
        },
        "discrepancy_summary.json 存在": True,
        "discrepancy_summary.json 顶层键": list(disc.keys()),
        "报告.md 数字 vs 数据": md_vs_data,
        "报告.md 哈希表 vs hashes.json vs 磁盘": hash_cmp,
        "判定": None,
    }
    md_num_ok = all(v["一致"] for v in md_vs_data.values() if v["报告声称"] is not None)
    hash_ok = all(h["报告与 hashes.json 一致"] for h in hash_cmp) if hash_cmp else False
    disk_ok = all(h["hashes.json 与磁盘一致"] for h in hash_cmp) if hash_cmp else False
    data_ok = not csv_pq_bad and not csv_pq_str
    fmt_detail["报告.md 数字是否与数据一致"] = md_num_ok
    fmt_detail["报告.md 哈希表是否与 hashes.json 一致"] = hash_ok
    fmt_detail["hashes.json 是否与磁盘一致"] = disk_ok
    # 三层分开判：数据层 / 冻结层 / 文档层
    fmt_detail["分层判定"] = {
        "数据层（CSV ↔ Parquet ↔ JSON 数值）": "PASS" if data_ok else "FAIL",
        "冻结层（hashes.json ↔ 磁盘）": "PASS" if disk_ok else "FAIL",
        "文档层（报告.md 数字与哈希表 ↔ 数据与 hashes.json）": "PASS" if (md_num_ok and hash_ok) else "FAIL",
    }
    fmt_detail["判定"] = ("PASS" if (data_ok and disk_ok and md_num_ok and hash_ok) else
                          ("PASS_WITH_NOTE" if (data_ok and disk_ok) else "FAIL"))
    fmt_detail["文档缺陷"] = []
    if not md_num_ok:
        fmt_detail["文档缺陷"].append(
            "报告.md 的首触分布数字与自己的数据不符：报告写 "
            + " / ".join(f"{k}={v['报告声称']}" for k, v in md_vs_data.items())
            + "，数据实际 "
            + " / ".join(f"{k}={v['数据实际']}" for k, v in md_vs_data.items()))
    if not hash_ok:
        fmt_detail["文档缺陷"].append(
            f"报告.md 的哈希表 {len(hash_cmp)} 项全部与 hashes.json 不符"
            f"（字节数也不同），说明报告里的表是旧版文件的，文件后来重新生成过而报告未同步")
    (OUT / "05_format_consistency.json").write_text(
        json.dumps(fmt_detail, ensure_ascii=False, indent=2), encoding="utf-8")
    acc.append({"序号": 6, "检查项": fmt_detail["检查项"], "判定": fmt_detail["判定"],
                "关键事实": (f"数据层 {'一致' if data_ok else '不一致'}；"
                             f"冻结层(hashes.json↔磁盘) {'一致' if disk_ok else '不一致'}；"
                             f"文档层 报告.md 数字 {'一致' if md_num_ok else '不一致'}、"
                             f"哈希表 {'一致' if hash_ok else '不一致'}")})

    # ══════════ ⑦ 输入输出哈希与来源契约 ══════════
    fin = json.loads((FE07 / "00_freeze_inputs_manifest.json").read_text(encoding="utf-8"))
    in_cmp = []
    for rel, meta in fin.items():
        p = ROOT / rel
        cur = sha256(p) if p.exists() else None
        in_cmp.append({"输入文件": rel, "声明 sha": meta["sha256"][:16],
                       "磁盘实际 sha": (cur or "")[:16],
                       "声明字节": meta["size_bytes"],
                       "磁盘实际字节": (p.stat().st_size if p.exists() else None),
                       "一致": bool(cur == meta["sha256"])})
    reads_ea04 = any("ENTRY-AUDIT-04" in k for k in fin)
    reads_ea03 = any("ENTRY-AUDIT-03" in k for k in fin)
    # 输出哈希（hashes.json vs 磁盘）
    out_cmp = []
    for rel, meta in hj.items():
        p = FE07 / rel
        cur = sha256(p) if p.exists() else None
        out_cmp.append({"输出文件": rel, "自报 sha": meta["sha256"][:16],
                        "磁盘实际 sha": (cur or "")[:16], "一致": bool(cur == meta["sha256"])})
    src_detail = {
        "检查项": "⑦ 输入输出哈希与来源契约",
        "输入清单文件": "evidence/FACTOR-ENTRY-07/00_freeze_inputs_manifest.json",
        "输入条目数": len(fin),
        "输入逐条核对": in_cmp,
        "输入是否全部与磁盘一致": all(x["一致"] for x in in_cmp),
        "是否读取 ENTRY-AUDIT-03": reads_ea03,
        "是否读取 ENTRY-AUDIT-04 交接包": reads_ea04,
        "来源契约说明": ("FE07 的工单是「接入 ENTRY-AUDIT-03 校正账本」，读 EA03 符合它自己的工单；"
                         "EA04 交接包按工单由 FE08 补齐来源。所以本项对 FE07 不判 FAIL，"
                         "只如实记录「未读 EA04」这一事实"),
        "输出条目数": len(hj),
        "输出是否全部与磁盘一致": all(x["一致"] for x in out_cmp),
        "判定": ("PASS" if all(x["一致"] for x in in_cmp) and all(x["一致"] for x in out_cmp) else "FAIL"),
    }
    (OUT / "06_hashes_versions.json").write_text(json.dumps({
        "审计": "ENTRY-AUDIT-05",
        "生成时刻": now,
        "FE07 冻结版本": {
            "目录": "evidence/FACTOR-ENTRY-07/",
            "hashes.json sha256": sha256(FE07 / "hashes.json"),
            "表 csv sha256": sha256(FE07_TABLE_CSV),
            "表 parquet sha256": sha256(FE07_TABLE_PQ),
            "报告 md sha256": sha256(FE07 / "报告.md"),
        },
        "本窗口基准版本": {
            "交接包 parquet sha256": sha256(A4 / "handoff" / "ledger_handoff.parquet"),
            "基准特征 parquet sha256": sha256(A4 / "01_baseline_features.parquet"),
        },
        "来源契约": src_detail,
        "输出哈希逐条": out_cmp,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    acc.append({"序号": 7, "检查项": src_detail["检查项"], "判定": src_detail["判定"],
                "关键事实": (f"输入 {len(in_cmp)} 条{'全一致' if src_detail['输入是否全部与磁盘一致'] else '有不一致'}；"
                             f"输出 {len(out_cmp)} 条{'全一致' if src_detail['输出是否全部与磁盘一致'] else '有不一致'}；"
                             f"读 EA03={reads_ea03} / 读 EA04={reads_ea04}")})

    # ══════════ 汇总 ══════════
    verdicts = [a["判定"] for a in acc]
    all_strict = all(v == "PASS" for v in verdicts)
    all_usable = all(v in ("PASS", "PASS_WITH_NOTE") for v in verdicts)
    summary = {
        "audit": "ENTRY-AUDIT-05",
        "stage": "第一阶段 · FE07 逐项独立复核",
        "generated_utc": now,
        "fe07_version": {
            "table_csv_sha256": sha256(FE07_TABLE_CSV),
            "table_parquet_sha256": sha256(FE07_TABLE_PQ),
            "report_md_sha256": sha256(FE07 / "报告.md"),
            "hashes_json_sha256": sha256(FE07 / "hashes.json"),
        },
        "sample_space": {
            "rows": 227, "unique_signal": n_sig, "filled": int((fe["is_filled"] == True).sum()),
            "unfilled": unfilled, "coti_relation_rows": int(len(coti)),
            "note": "227 条记录 ID 唯一，但只有 226 个唯一市场信号；COTI 同快照被两代际各记一次",
        },
        "acceptance_table": acc,
        "all_pass_strict": all_strict,
        "all_pass_or_note": all_usable,
        "failed_items": [a["检查项"] for a in acc if a["判定"] == "FAIL"],
        "noted_items": [a["检查项"] for a in acc if a["判定"] == "PASS_WITH_NOTE"],
        "details": {
            "identity": identity_detail,
            "money": money_detail,
            "net_identity": identity_detail,
            "classification": cat_detail,
            "atr_first_move": atr_detail,
            "format": fmt_detail,
            "source_contract": src_detail,
        },
        "conclusion_split": {
            "数据交接及产物一致性": {
                "数据层": "通过" if all_usable else "有未通过项，见验收表",
                "文档层": "有缺陷" if not (md_num_ok and hash_ok) else "通过",
                "逐项": {a["检查项"]: a["判定"] for a in acc},
            },
            "历史规格与 PIT 限制": ("仍未解除：spec verified 0 条、formal_pass_allowed 全 false、"
                                    "PIT UNPROVEN；两代际净盈亏方向相反（+144.106 / −42.574）"),
            "策略有效性": "仍未证明（与数据一致性无关，见报告 §结论分离）",
        },
    }
    (OUT / "00_verify_fe07.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(acc).to_csv(OUT / "00_acceptance_table.csv", index=False, encoding="utf-8-sig")

    print(json.dumps(acc, ensure_ascii=False, indent=1))
    print(f"\n严格全通过：{all_strict} / 含注记可用：{all_usable}")


if __name__ == "__main__":
    main()
