# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-04 / 02 · 第二阶段：FE07 签收与七项独立复核

工单第二阶段七项：
  1. 检查反重力是否读取了上述准确版本
  2. 核验每条匹配关系，报告未匹配、多重匹配和遗漏数量
  3. 逐笔比较手续费、资金费、毛盈亏和净盈亏，复核净值恒等式
  4. 按 FE06 冻结分类定义独立计算标签（本脚本直接用 01 已冻结的基准）
  5. 验证 ATR 和首触顺序等不依赖费用的字段没有被意外改变
  6. 核对 CSV/Parquet、JSON、Markdown 数字一致；缺值不等于 0
  7. 分别报告「数据一致性通过」和「策略有效性未证明」

FE07 未交付时：输出 PENDING_DELIVERY，并把七项检查的判定规则与已冻结基准写清，
不把工单报完成。FE07 一到，同一条命令即可出结论。

写入边界：仅 evidence/ENTRY-AUDIT-04/；不修改任何反重力文件。
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
OUT = ROOT / "evidence" / "ENTRY-AUDIT-04"
HANDOFF = OUT / "handoff"
FE07 = ROOT / "evidence" / "FACTOR-ENTRY-07"

# 禁用路径与禁用键名：出现即说明读错了版本
FORBIDDEN_PATHS = [
    "FACTOR-ENTRY-03/candidate_features_enriched",
    "total_fees",
    "total_funding",
]
REQUIRED_REFERENCE = "ENTRY-AUDIT-04/handoff/ledger_handoff"

MONEY_COLS = ["gross_pnl", "fees", "funding_fee", "net_pnl"]
ATR_FIRST_MOVE_COLS = ["signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h",
                       "mfe_r_4h", "mae_r_4h", "first_move_4h", "mfe_r", "mfe_atr_full"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_baseline() -> pd.DataFrame:
    p = OUT / "01_baseline_features.parquet"
    if not p.exists():
        raise SystemExit("缺 01_baseline_features.parquet，请先跑 01_independent_classification.py")
    return pd.read_parquet(p)


def discover_fe07() -> dict:
    """自适应探测 FE07 产物：不预设文件名。"""
    found = {"exists": FE07.exists(), "tables": [], "json": [], "markdown": [], "scripts": []}
    if not FE07.exists():
        return found
    for p in sorted(FE07.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        s = p.suffix.lower()
        if s in (".csv", ".parquet"):
            found["tables"].append(p)
        elif s == ".json":
            found["json"].append(p)
        elif s in (".md", ".markdown"):
            found["markdown"].append(p)
        elif s in (".py", ".sh"):
            found["scripts"].append(p)
    return found


def pick_feature_table(paths: list[Path], baseline_ids: set) -> tuple[Path | None, pd.DataFrame | None, str]:
    """挑出「候选级特征表」：含 candidate_id 且能覆盖交接包 227 个 ID。"""
    best, best_df, best_note = None, None, "未找到候选级特征表"
    for p in paths:
        try:
            df = pd.read_parquet(p) if p.suffix.lower() == ".parquet" else pd.read_csv(p)
        except Exception as exc:  # 损坏文件不静默跳过
            best_note = f"{p.name} 读取失败：{type(exc).__name__}"
            continue
        if "candidate_id" not in df.columns:
            continue
        hit = len(set(df["candidate_id"].astype(str)) & baseline_ids)
        if hit >= 200 and (best is None or hit > best_df["candidate_id"].nunique()):
            best, best_df = p, df
            best_note = f"命中 {hit}/227 个候选 ID"
    return best, best_df, best_note


def check_1_version(fe07: dict) -> dict:
    """① 是否读取了准确版本"""
    refs, forbidden = [], []
    for p in fe07["scripts"] + fe07["markdown"] + fe07["json"]:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if REQUIRED_REFERENCE in txt:
            refs.append(p.name)
        for f in FORBIDDEN_PATHS:
            if f in txt:
                forbidden.append({"file": p.name, "hit": f})
    return {
        "检查项": "读取的是否为 ENTRY-AUDIT-04 交接包",
        "要求": f"产物或脚本里应出现 {REQUIRED_REFERENCE}",
        "引用交接包的文件": refs,
        "命中禁用路径或键名的文件": forbidden,
        "判定": ("PASS" if refs and not forbidden else
                 ("FAIL" if forbidden else "UNPROVEN")),
        "说明": ("出现 total_fees / total_funding / FACTOR-ENTRY-03 特征表，说明读的是"
                 "「163 条费用为 0」的旧口径" if forbidden else
                 ("未出现禁用路径" if refs else "没有任何文件引用交接包，无法证明读了准确版本")),
    }


def check_2_match(fe07_df: pd.DataFrame | None, baseline: pd.DataFrame) -> dict:
    """② 匹配关系：未匹配 / 多重匹配 / 遗漏"""
    if fe07_df is None:
        return {"检查项": "匹配关系核验", "判定": "PENDING", "说明": "未找到候选级特征表"}
    b_ids = set(baseline["candidate_id"].astype(str))
    f_ids = fe07_df["candidate_id"].astype(str)
    f_set = set(f_ids)
    dup = f_ids[f_ids.duplicated(keep=False)]
    dup_detail = sorted(set(dup.tolist()))
    unmatched = sorted(f_set - b_ids)
    missing = sorted(b_ids - f_set)
    return {
        "检查项": "匹配关系核验",
        "交接包候选数": len(b_ids),
        "FE07 行数": int(len(fe07_df)),
        "FE07 唯一候选数": len(f_set),
        "多重匹配（同 ID 出现多次）": len(dup_detail),
        "多重匹配明细": dup_detail[:20],
        "未匹配（FE07 有、交接包无）": len(unmatched),
        "未匹配明细": unmatched[:20],
        "遗漏（交接包有、FE07 无）": len(missing),
        "遗漏明细": missing[:20],
        "判定": ("PASS" if not unmatched and not missing and not dup_detail else "FAIL"),
    }


def check_3_money(fe07_df: pd.DataFrame | None, baseline: pd.DataFrame) -> dict:
    """③ 逐笔金额与净值恒等式"""
    if fe07_df is None:
        return {"检查项": "费用与净盈亏逐笔对账", "判定": "PENDING", "说明": "未找到候选级特征表"}
    b = baseline.set_index("candidate_id")
    f = fe07_df.copy()
    f["candidate_id"] = f["candidate_id"].astype(str)
    f = f.set_index("candidate_id")
    common = [c for c in MONEY_COLS if c in f.columns]
    if not common:
        return {"检查项": "费用与净盈亏逐笔对账", "判定": "FAIL",
                "说明": f"FE07 表里没有金额列，只有 {list(f.columns)[:12]}"}
    rows, per_col = [], {}
    for c in common:
        a = pd.to_numeric(f[c], errors="coerce")
        bb = pd.to_numeric(b[c].reindex(a.index), errors="coerce")
        diff = (a - bb).abs()
        per_col[c] = {
            "比较条数": int(diff.notna().sum()),
            "一致(<=1e-9)": int((diff <= 1e-9).sum()),
            "不一致": int((diff > 1e-9).sum()),
            "最大绝对差": (None if diff.notna().sum() == 0 else float(np.nanmax(diff.to_numpy()))),
        }
        bad = diff[diff > 1e-9]
        for cid in bad.index[:20]:
            rows.append({"candidate_id": cid, "字段": c, "FE07": float(a.loc[cid]),
                         "交接包": float(bb.loc[cid]), "差": float(bad.loc[cid])})
    # 恒等式：用 FE07 自己的四个数复算
    identity = {"可复算条数": 0, "成立": 0, "违反": 0}
    if all(c in f.columns for c in MONEY_COLS):
        g = pd.to_numeric(f["gross_pnl"], errors="coerce")
        fe = pd.to_numeric(f["fees"], errors="coerce")
        fu = pd.to_numeric(f["funding_fee"], errors="coerce")
        ne = pd.to_numeric(f["net_pnl"], errors="coerce")
        r = (ne - (g - fe - fu)).abs()
        ok = r.notna()
        identity = {"可复算条数": int(ok.sum()), "成立": int((r[ok] <= 1e-9).sum()),
                    "违反": int((r[ok] > 1e-9).sum()),
                    "最大残差": (None if not ok.any() else float(np.nanmax(r[ok].to_numpy())))}
    all_ok = all(v["不一致"] == 0 for v in per_col.values()) and identity.get("违反", 0) == 0
    return {
        "检查项": "费用与净盈亏逐笔对账",
        "公式": "net_pnl = gross_pnl - fees - funding_fee；fees = entry_fee + exit_fee",
        "逐列结果": per_col,
        "差异明细（前 20 条/列）": rows,
        "FE07 自身净值恒等式": identity,
        "判定": "PASS" if all_ok else "FAIL",
    }


def check_4_classification(fe07_df: pd.DataFrame | None, baseline: pd.DataFrame) -> dict:
    """④ 标签与已冻结基准比对"""
    if fe07_df is None:
        return {"检查项": "独立分类比对", "判定": "PENDING", "说明": "未找到候选级特征表"}
    pairs = [("category_standard_mixed", "cat_standard_mixed_indep", "标准混合"),
             ("category_pure_atr_4h", "cat_pure_atr_4h_indep", "纯ATR-4h"),
             ("category_pure_atr_full", "cat_pure_atr_full_indep", "纯ATR-全周期")]
    res = []
    for fc, bc, name in pairs:
        if fc not in fe07_df.columns:
            res.append({"体系": name, "状态": "FE07 无此列"})
            continue
        m = baseline[["candidate_id", bc]].merge(
            fe07_df[["candidate_id", fc]], on="candidate_id", how="inner")
        diff = m[m[bc].astype(str) != m[fc].astype(str)]
        res.append({
            "体系": name, "比较条数": int(len(m)),
            "一致": int(len(m) - len(diff)), "不一致": int(len(diff)),
            "不一致明细": diff.head(20).to_dict("records"),
            "判定": "PASS" if len(diff) == 0 else "FAIL",
        })
    # 工单点名：net_pnl<=0 参与的浮盈回吐
    pullback_note = None
    if "category_standard_mixed" in fe07_df.columns:
        pb = fe07_df[fe07_df["category_standard_mixed"].astype(str) == "MFE_PULLBACK"]
        if "net_pnl" in pb.columns and len(pb):
            viol = pb[pd.to_numeric(pb["net_pnl"], errors="coerce") > 0]
            pullback_note = {
                "FE07 浮盈回吐条数": int(len(pb)),
                "基准浮盈回吐条数": int((baseline["cat_standard_mixed_indep"] == "MFE_PULLBACK").sum()),
                "被标为回吐但 net_pnl>0 的条数": int(len(viol)),
                "明细": viol.head(10)[["candidate_id", "net_pnl"]].to_dict("records"),
                "判定": "PASS" if len(viol) == 0 else "FAIL",
            }
    return {"检查项": "独立分类比对", "逐体系": res,
            "浮盈回吐专项（net_pnl<=0）": pullback_note,
            "判定": ("PASS" if all(r.get("判定") == "PASS" for r in res if "判定" in r)
                     and (pullback_note is None or pullback_note["判定"] == "PASS") else "FAIL")}


def check_5_invariance(fe07_df: pd.DataFrame | None, baseline: pd.DataFrame) -> dict:
    """⑤ ATR 与首触顺序等不依赖费用的字段是否被改动"""
    if fe07_df is None:
        return {"检查项": "不依赖费用字段的不变性", "判定": "PENDING", "说明": "未找到候选级特征表"}
    cols = [c for c in ATR_FIRST_MOVE_COLS if c in fe07_df.columns and c in baseline.columns]
    if not cols:
        return {"检查项": "不依赖费用字段的不变性", "判定": "FAIL",
                "说明": "FE07 表里没有任何可比的 ATR / 首触字段"}
    m = baseline[["candidate_id"] + cols].merge(fe07_df[["candidate_id"] + cols],
                                                on="candidate_id", how="inner",
                                                suffixes=("_基准", "_FE07"))
    out = {}
    for c in cols:
        a, b = m[f"{c}_基准"], m[f"{c}_FE07"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            nan_mismatch = int((a.isna() != b.isna()).sum())
            d = (pd.to_numeric(a, errors="coerce") - pd.to_numeric(b, errors="coerce")).abs()
            out[c] = {"数值型": True, "NaN 不一致": nan_mismatch,
                      "不一致(>1e-12)": int((d > 1e-12).sum()),
                      "最大差": None if d.notna().sum() == 0 else float(np.nanmax(d.to_numpy()))}
        else:
            out[c] = {"数值型": False,
                      "字符串不一致": int((a.astype(str) != b.astype(str)).sum())}
    ok = all((v.get("不一致(>1e-12)", 0) == 0 and v.get("NaN 不一致", 0) == 0
              and v.get("字符串不一致", 0) == 0) for v in out.values())
    return {"检查项": "不依赖费用字段的不变性", "比较字段": cols, "逐字段": out,
            "判定": "PASS" if ok else "FAIL",
            "说明": "ATR / 首触顺序与费用无关，被改动即说明重建过程有副作用"}


def check_6_formats(fe07: dict, fe07_df: pd.DataFrame | None, baseline: pd.DataFrame) -> dict:
    """⑥ 四格式数字一致 + 缺值不等于 0"""
    if not fe07["exists"]:
        return {"检查项": "多格式数字一致", "判定": "PENDING", "说明": "FE07 未交付"}
    res = {"检查项": "多格式数字一致", "文件清单": {
        "表": [p.name for p in fe07["tables"]],
        "JSON": [p.name for p in fe07["json"]],
        "Markdown": [p.name for p in fe07["markdown"]],
    }}
    # 同一目录下 CSV 与 Parquet 若并存，逐值比
    pairs = []
    for p in fe07["tables"]:
        twin = p.with_suffix(".parquet" if p.suffix.lower() == ".csv" else ".csv")
        if twin.exists():
            pairs.append((p, twin))
    pair_res = []
    for a, b in pairs:
        try:
            da = pd.read_csv(a) if a.suffix.lower() == ".csv" else pd.read_parquet(a)
            db = pd.read_parquet(b) if b.suffix.lower() == ".parquet" else pd.read_csv(b)
        except Exception as exc:
            pair_res.append({"pair": f"{a.name} / {b.name}", "error": f"{type(exc).__name__}"})
            continue
        common = [c for c in da.columns if c in db.columns and pd.api.types.is_numeric_dtype(da[c])]
        bad = {}
        for c in common:
            x = pd.to_numeric(da[c], errors="coerce")
            y = pd.to_numeric(db[c], errors="coerce")
            d = (x - y).abs()
            if d.notna().any() and float(np.nanmax(d.to_numpy())) > 1e-9:
                bad[c] = float(np.nanmax(d.to_numpy()))
        pair_res.append({"pair": f"{a.name} / {b.name}", "比较列数": len(common),
                         "不一致列": bad, "判定": "PASS" if not bad else "FAIL"})
    res["CSV/Parquet 配对"] = pair_res

    # 缺值不等于 0：已成交行的金额列若出现 0，而基准同一行非 0，就是「把缺值当 0」
    zero_check = None
    if fe07_df is not None:
        f = fe07_df.copy()
        f["candidate_id"] = f["candidate_id"].astype(str)
        b = baseline.set_index("candidate_id")
        issues = []
        for c in MONEY_COLS:
            if c not in f.columns:
                continue
            s = pd.to_numeric(f[c], errors="coerce")
            zero = f.loc[s == 0, "candidate_id"]
            for cid in zero.tolist()[:50]:
                if cid in b.index and abs(float(b.loc[cid, c] or 0.0)) > 1e-12:
                    issues.append({"candidate_id": cid, "字段": c,
                                   "FE07": 0.0, "基准": float(b.loc[cid, c])})
        zero_check = {"把非零写成 0 的条数": len(issues), "明细": issues[:20],
                      "判定": "PASS" if not issues else "FAIL"}
    res["缺值是否被当成 0"] = zero_check
    ok = (all(p.get("判定") == "PASS" for p in pair_res if "判定" in p)
          and (zero_check is None or zero_check["判定"] == "PASS"))
    res["判定"] = "PASS" if ok else "FAIL"
    return res


def main() -> None:
    baseline = load_baseline()
    fe07 = discover_fe07()

    result = {
        "audit": "ENTRY-AUDIT-04",
        "stage": "第二阶段 · FE07 独立一致性验收",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "fe07_dir": str(FE07),
        "fe07_exists": fe07["exists"],
        "handoff_primary_sha256": sha256(HANDOFF / "ledger_handoff.parquet"),
    }

    if not fe07["exists"]:
        result.update({
            "status": "PENDING_DELIVERY",
            "conclusion": ("FE07 尚未交付（evidence/ 下只有 FACTOR-ENTRY-01~06）⇒ 第二阶段无法执行。"
                           "本工单只完成第一阶段（冻结可交接账本），不得报整体完成。"),
            "ready_checks": [
                {"序号": 1, "检查项": "读取的是否为 ENTRY-AUDIT-04 交接包",
                 "判定规则": f"FE07 产物或脚本里应出现 {REQUIRED_REFERENCE}；"
                             f"出现 {FORBIDDEN_PATHS} 任一即判 FAIL"},
                {"序号": 2, "检查项": "匹配关系核验",
                 "判定规则": "candidate_id 与交接包 227 个 ID 比；未匹配 / 遗漏 / 多重匹配任一非 0 即 FAIL"},
                {"序号": 3, "检查项": "费用与净盈亏逐笔对账",
                 "判定规则": "gross_pnl / fees / funding_fee / net_pnl 四列逐笔比，容差 1e-9；"
                             "并用 FE07 自己的四个数复算 net_pnl = gross_pnl - fees - funding_fee"},
                {"序号": 4, "检查项": "独立分类比对",
                 "判定规则": "三套标签与本基准 01_baseline_features 逐条比；"
                             "并专项查「被标为浮盈回吐但 net_pnl>0」的条数"},
                {"序号": 5, "检查项": "不依赖费用字段的不变性",
                 "判定规则": "signal_atr_snapshot / box_atr_formation / fwd_ret_4h / mfe_r_4h / "
                             "mae_r_4h / first_move_4h / mfe_r / mfe_atr_full 逐字段比，容差 1e-12，NaN 对 NaN"},
                {"序号": 6, "检查项": "多格式数字一致 + 缺值不等于 0",
                 "判定规则": "同目录 CSV/Parquet 逐值比；已成交行的金额列若为 0 而基准非 0 即判 FAIL"},
                {"序号": 7, "检查项": "两条结论分开报",
                 "判定规则": "「数据一致性通过」只说明账对得上；「策略有效性未证明」必须单独陈述，不得混为一谈"},
            ],
            "baseline_ready": {
                "file": "evidence/ENTRY-AUDIT-04/01_baseline_features.parquet",
                "rows": int(len(baseline)),
                "filled": int((baseline["is_filled"] == True).sum()),
                "labels_frozen": {
                    "标准混合": {k: int(v) for k, v in baseline["cat_standard_mixed_indep"].value_counts().items()},
                    "纯ATR-4h": {k: int(v) for k, v in baseline["cat_pure_atr_4h_indep"].value_counts().items()},
                    "纯ATR-全周期": {k: int(v) for k, v in baseline["cat_pure_atr_full_indep"].value_counts().items()},
                },
                "note": "基准已冻结；FE07 一到，直接跑本脚本即可出七项结论",
            },
        })
        (OUT / "02_fe07_recheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print("FE07 未交付 ⇒ 第二阶段状态 PENDING_DELIVERY")
        print("  七项检查的判定规则与冻结基准已写入 02_fe07_recheck.json")
        return

    # ── FE07 已交付：跑七项 ──
    table, df, note = pick_feature_table(fe07["tables"], set(baseline["candidate_id"].astype(str)))
    result["fe07_feature_table"] = {"path": (str(table) if table else None),
                                    "sha256": (sha256(table) if table else None),
                                    "note": note}
    result["checks"] = {
        "1_版本核对": check_1_version(fe07),
        "2_匹配关系": check_2_match(df, baseline),
        "3_金额对账": check_3_money(df, baseline),
        "4_独立分类": check_4_classification(df, baseline),
        "5_字段不变性": check_5_invariance(df, baseline),
        "6_多格式一致": check_6_formats(fe07, df, baseline),
    }
    verdicts = [v.get("判定") for v in result["checks"].values()]
    result["data_consistency"] = {
        "判定": "PASS" if all(v == "PASS" for v in verdicts) else "FAIL",
        "口径": "只回答「账对不对得上」，不回答「策略有没有效」",
    }
    result["strategy_validity"] = {
        "判定": "未证明",
        "原因": ("历史合约规格全池无 verified、PIT 未证、两代际净盈亏方向相反；"
                 "即使数据一致性 PASS，也不构成策略有效性的证据"),
    }
    (OUT / "02_fe07_recheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print(json.dumps({k: v.get("判定") for k, v in result["checks"].items()},
                     ensure_ascii=False, indent=2))
    print("数据一致性：", result["data_consistency"]["判定"])
    print("策略有效性：", result["strategy_validity"]["判定"])


if __name__ == "__main__":
    main()
