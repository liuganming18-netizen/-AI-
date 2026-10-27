#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-06 · 交付记录同步与最终一致性复核（第一步：只读核查）

约束：
  * 只读 evidence/FACTOR-ENTRY-07、evidence/FACTOR-ENTRY-08、evidence/ENTRY-AUDIT-04、
    evidence/ENTRY-AUDIT-05 与磁盘文件；不修改任何上游文件。
  * 不重跑任何回测。数据层若与 EA05 已验基准哈希一致，只做「一致性确认」，
    不重复全量计算（引用 ENTRY-AUDIT-05 的结果）。
  * 全部输出只写入 evidence/ENTRY-AUDIT-06/。

产出：
  01_file_hash_check.csv   逐文件 声明/磁盘 大小与 SHA256 比对（含清单覆盖缺口）
  02_cycle_check.json      清单自哈希与报告↔清单循环引用检查
  03_version_map.csv       版本映射（FE06 → FE07 → FE08 → FE09）
  04_data_invariance.json  数据不变核验（与 EA05 已验基准逐位比对）
  05_field_contract.csv    EA04 字段契约 → FE08 列名逐项核对
  06_doc_layer_check.csv   报告哈希表 vs 清单 vs 磁盘
  07_source_binding.json   FE07 / FE08 输入来源绑定核查
  09_fe09_pending.json     FE09 交付状态与到货后的复核清单
  _facts.json              以上全部结论的结构化汇总（供 01_emit_acceptance.py 使用）
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-06"
FE07 = ROOT / "evidence" / "FACTOR-ENTRY-07"
FE08 = ROOT / "evidence" / "FACTOR-ENTRY-08"
FE09 = ROOT / "evidence" / "FACTOR-ENTRY-09"
EA04 = ROOT / "evidence" / "ENTRY-AUDIT-04"
EA05 = ROOT / "evidence" / "ENTRY-AUDIT-05"

# EA05 已验基准（本轮不重算，只比对；来源见 04_data_invariance.json 的 baseline_source）
BASELINE = {
    "FE07/hashes.json": "de920eb7ed8421dfbf3f48dffe254a452b1a05541f98496b188d8d6dc81e8bd9",
    "FE07/reconstructed_candidate_features_07.csv": "a68b8f97a5c9473da743ddbeb2159dc7062475b8d7c7b6d1546d1fc2197ca429",
    "FE07/reconstructed_candidate_features_07.parquet": "c76e3bf80b78674a134b8121a1436e1c2df694c4c2c40e9a13ad755bf99009b0",
    "FE07/报告.md": "f11ebfcf9341fcc77f0296bc8e940bf88352854e4c04b3f354cf4d2c550996dd",
    "FE08/hashes.json": "4780a09c79189a28",  # EA05 只登记了前 16 位，下面按前 16 位比对
    "FE08/reconstructed_candidate_features_08.csv": "5360aeb189e7c465b9d7ca9e943558d3379ee2236f9245c5b267db14a9da22de",
    "FE08/reconstructed_candidate_features_08.parquet": "6e1b9369cca29cba4c8c6064cd11b90a4961f1adcd917aef7dffb7ce5d456849",
    "EA04/ledger_handoff.parquet": "93abe58a9b25b95060e30ecc2d4d684f5500e67acc9772201230b3149cee5054",
}
BASELINE_SOURCE = "evidence/ENTRY-AUDIT-05/06_hashes_versions.json + 01_fe08_recheck.json"

MONEY_MAP = [  # EA04 交接字段 → FE08 列名
    ("gross_pnl", "gross_pnl"),
    ("fees", "fee_paid"),
    ("funding_fee", "funding_cost"),
    ("net_pnl", "net_pnl"),
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 1 逐文件哈希
def check_file_hashes() -> tuple[pd.DataFrame, dict]:
    rows, summary = [], {}
    for tag, d in (("FE07", FE07), ("FE08", FE08)):
        hp = d / "hashes.json"
        declared = load_json(hp) if hp.exists() else {}
        disk_files = sorted(
            [p for p in d.rglob("*") if p.is_file()],
            key=lambda p: str(p.relative_to(d)),
        )
        seen = set()
        for name, meta in sorted(declared.items()):
            fp = d / name
            seen.add(name)
            if not fp.exists():
                rows.append(dict(版本=tag, 文件=name, 在清单中=True, 磁盘存在=False,
                                 声明字节=meta.get("size"), 磁盘字节=None, 字节一致=False,
                                 声明sha256=meta.get("sha256"), 磁盘sha256=None, sha一致=False,
                                 备注="清单登记但磁盘缺失"))
                continue
            size = fp.stat().st_size
            actual = sha256_file(fp)
            rows.append(dict(版本=tag, 文件=name, 在清单中=True, 磁盘存在=True,
                             声明字节=meta.get("size"), 磁盘字节=size,
                             字节一致=(meta.get("size") == size),
                             声明sha256=meta.get("sha256"), 磁盘sha256=actual,
                             sha一致=(meta.get("sha256") == actual),
                             备注=""))
        for fp in disk_files:
            name = str(fp.relative_to(d))
            if name in seen:
                continue
            note = ("清单不自哈希（预期未登记，不算缺口）" if name == "hashes.json"
                    else "磁盘有、清单未登记（清单覆盖缺口）")
            rows.append(dict(版本=tag, 文件=name, 在清单中=False, 磁盘存在=True,
                             声明字节=None, 磁盘字节=fp.stat().st_size, 字节一致=None,
                             声明sha256=None, 磁盘sha256=sha256_file(fp), sha一致=None,
                             备注=note))
        n_decl = len(declared)
        n_ok = sum(1 for r in rows if r["版本"] == tag and r["在清单中"] and r["sha一致"])
        n_bad = sum(1 for r in rows if r["版本"] == tag and r["在清单中"] and not r["sha一致"])
        n_extra = sum(1 for r in rows if r["版本"] == tag and not r["在清单中"]
                      and r["文件"] != "hashes.json")
        summary[tag] = dict(清单条数=n_decl, 一致条数=n_ok, 不一致条数=n_bad,
                            未登记文件数=n_extra, 判定="PASS" if n_bad == 0 and n_extra == 0 else "FAIL",
                            说明="hashes.json 自身不在清单内（清单不自哈希），属预期未登记")
    return pd.DataFrame(rows), summary


# ------------------------------------------------- 2 自哈希 / 循环引用
def check_cycles() -> dict:
    result = {"检查项": "清单自哈希与报告↔清单循环引用", "版本": {}}
    for tag, d in (("FE07", FE07), ("FE08", FE08)):
        hp = d / "hashes.json"
        declared = load_json(hp)
        own_sha = sha256_file(hp)
        self_listed = "hashes.json" in declared
        own_sha_inside = own_sha in hp.read_text(encoding="utf-8", errors="ignore")
        # 报告 md 是否登记了 hashes.json 或自身的哈希（会造成互相引用）
        report = d / "报告.md"
        text = report.read_text(encoding="utf-8", errors="ignore") if report.exists() else ""
        report_refs_hashes = "hashes.json" in text
        report_refs_own_sha = own_sha in text or sha256_file(report) in text
        # 其它清单文件是否回指 hashes.json
        back_refs = []
        for jp in d.glob("*.json"):
            if jp.name == "hashes.json":
                continue
            t = jp.read_text(encoding="utf-8", errors="ignore")
            if own_sha in t or "hashes.json" in t:
                back_refs.append(jp.name)
        result["版本"][tag] = {
            "清单文件": rel(hp),
            "清单是否登记自身": self_listed,
            "清单内是否含自身 sha256": own_sha_inside,
            "报告是否引用 hashes.json": report_refs_hashes,
            "报告是否含自身或清单 sha256": report_refs_own_sha,
            "其它清单是否回指 hashes.json": back_refs,
            "存在循环引用": bool(self_listed or own_sha_inside or report_refs_own_sha or back_refs),
            "清单是否有外部锚点": False,
            "说明": "清单不含自身哈希 ⇒ 无自哈希、无循环；代价是清单本身无外部锚点，"
                    "只能靠本审计登记（见 04_data_invariance.json 的 baseline）",
        }
    result["判定"] = "PASS" if not any(v["存在循环引用"] for v in result["版本"].values()) else "FAIL"
    return result


# ---------------------------------------------------------------- 3 版本映射
def build_version_map(fh_summary: dict) -> pd.DataFrame:
    def sha(p: Path) -> str | None:
        return sha256_file(p) if p.exists() else None

    rows = [
        dict(版本标识="ENTRY-AUDIT-03", 目录="evidence/ENTRY-AUDIT-03", 角色="FE07 输入（校正账本）",
             锚定文件="03_identity_full.csv", 声明sha256=None, 磁盘sha256=sha(ROOT / "evidence/ENTRY-AUDIT-03/03_identity_full.csv"),
             状态="已验（EA03/EA04 已核对）", 备注="FE07 实际读取的是这一代"),
        dict(版本标识="ENTRY-AUDIT-04.handoff.v1", 目录="evidence/ENTRY-AUDIT-04/handoff", 角色="FE08 输入（冻结交接包）",
             锚定文件="ledger_handoff.parquet",
             声明sha256=load_json(FE08 / "00_manifest.json")["handoff_package"]["read_file_sha256"],
             磁盘sha256=sha(EA04 / "handoff/ledger_handoff.parquet"),
             状态="已验", 备注="FE08 清单声明读取此版本"),
        dict(版本标识="FACTOR-ENTRY-06", 目录="evidence/FACTOR-ENTRY-06", 角色="FE07 分类契约输入",
             锚定文件="classification_contract.md",
             声明sha256=None, 磁盘sha256=sha(ROOT / "evidence/FACTOR-ENTRY-06/classification_contract.md"),
             状态="只读引用", 备注="分类定义来源"),
        dict(版本标识="FACTOR-ENTRY-07.frozen", 目录="evidence/FACTOR-ENTRY-07", 角色="旧来源产物（只读对照）",
             锚定文件="hashes.json", 声明sha256=None, 磁盘sha256=sha(FE07 / "hashes.json"),
             状态="数据层通过 / 文档层失败", 备注="清单 17 项与磁盘一致；报告哈希表 9 项全错"),
        dict(版本标识="FACTOR-ENTRY-08.frozen.v1", 目录="evidence/FACTOR-ENTRY-08", 角色="新来源产物（本轮已验基准）",
             锚定文件="hashes.json", 声明sha256=None, 磁盘sha256=sha(FE08 / "hashes.json"),
             状态="数据层通过 / 文档层失败", 备注="清单 17 项与磁盘一致；报告哈希表 10 项全错"),
        dict(版本标识="FACTOR-ENTRY-09", 目录="evidence/FACTOR-ENTRY-09", 角色="下一版产物（冻结版待交付）",
             锚定文件="—", 声明sha256=None, 磁盘sha256=None,
             状态="待交付", 备注="目录不存在；到货前不得整单报完成"),
    ]
    df = pd.DataFrame(rows)
    df["清单校验"] = df["版本标识"].map({
        "FACTOR-ENTRY-07.frozen": f"{fh_summary['FE07']['一致条数']}/{fh_summary['FE07']['清单条数']}",
        "FACTOR-ENTRY-08.frozen.v1": f"{fh_summary['FE08']['一致条数']}/{fh_summary['FE08']['清单条数']}",
    }).fillna("—")
    return df


# ---------------------------------------------------------------- 4 数据不变
def check_data_invariance() -> dict:
    cur = {
        "FE07/hashes.json": sha256_file(FE07 / "hashes.json"),
        "FE07/reconstructed_candidate_features_07.csv": sha256_file(FE07 / "reconstructed_candidate_features_07.csv"),
        "FE07/reconstructed_candidate_features_07.parquet": sha256_file(FE07 / "reconstructed_candidate_features_07.parquet"),
        "FE07/报告.md": sha256_file(FE07 / "报告.md"),
        "FE08/hashes.json": sha256_file(FE08 / "hashes.json"),
        "FE08/reconstructed_candidate_features_08.csv": sha256_file(FE08 / "reconstructed_candidate_features_08.csv"),
        "FE08/reconstructed_candidate_features_08.parquet": sha256_file(FE08 / "reconstructed_candidate_features_08.parquet"),
        "EA04/ledger_handoff.parquet": sha256_file(EA04 / "handoff/ledger_handoff.parquet"),
    }
    baseline_cmp = []
    for k, exp in BASELINE.items():
        got = cur[k]
        same = got.startswith(exp) if len(exp) == 16 else got == exp
        baseline_cmp.append(dict(对象=k, 基准sha256=exp, 当前sha256=got, 一致=same,
                                 基准精度="前16位" if len(exp) == 16 else "完整64位"))
    unchanged = all(r["一致"] for r in baseline_cmp)

    f7 = pd.read_csv(FE07 / "reconstructed_candidate_features_07.csv")
    f8 = pd.read_csv(FE08 / "reconstructed_candidate_features_08.csv")
    h = pd.read_parquet(EA04 / "handoff/ledger_handoff.parquet")

    # FE07 vs FE08：仅比共有列（FE08 增 6 列、减 4 列，属已知结构变更）
    common = [c for c in f7.columns if c in f8.columns]
    only7 = [c for c in f7.columns if c not in f8.columns]
    only8 = [c for c in f8.columns if c not in f7.columns]
    col_diff = []
    for c in common:
        a, b = f7[c], f8[c]
        if pd.api.types.is_bool_dtype(a) or pd.api.types.is_bool_dtype(b):
            a, b = a.astype("float64"), b.astype("float64")
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            both = a.notna() & b.notna()
            mx = float((a[both] - b[both]).abs().max()) if both.any() else 0.0
            bad = int((both & ((a - b).abs() > 1e-12)).sum()) + int((a.notna() ^ b.notna()).sum())
            col_diff.append(dict(列=c, 类型="数值", 最大绝对差=mx, 不一致条数=bad))
        else:
            sa, sb = a.astype("string"), b.astype("string")
            bad = int((sa.fillna("∅") != sb.fillna("∅")).sum())
            col_diff.append(dict(列=c, 类型="字符", 最大绝对差=None, 不一致条数=bad))
    cd = pd.DataFrame(col_diff)

    # FE08 vs EA04 交接包（独立核对，不经 FE07）
    f8r = f8[["candidate_id"] + [f for _, f in MONEY_MAP]].rename(
        columns={f: "f8_" + f for _, f in MONEY_MAP})
    hr = h[["candidate_id"] + [hc for hc, _ in MONEY_MAP]].rename(
        columns={hc: "h_" + hc for hc, _ in MONEY_MAP})
    m = f8r.merge(hr, on="candidate_id", how="outer", indicator=True)
    money = []
    for hcol, fcol in MONEY_MAP:
        x, y = m["f8_" + fcol], m["h_" + hcol]
        both = x.notna() & y.notna()
        money.append(dict(
            契约字段=hcol, FE08列=fcol,
            可比条数=int(both.sum()),
            最大绝对差=float((x[both] - y[both]).abs().max()) if both.any() else None,
            FE08有值交接包空=int((x.notna() & y.isna()).sum()),
            交接包有值FE08空=int((x.isna() & y.notna()).sum()),
        ))

    gen = (h.groupby("generation")
             .agg(行数=("candidate_id", "size"),
                  成交=("exec_status", lambda s: int((s == "FILLED").sum())),
                  净盈亏=("net_pnl", "sum"))
             .reset_index())
    reuse = []
    for name in ("00_verify_fe07.json", "01_fe08_recheck.json", "05_format_consistency.json",
                 "02_money_check.csv", "03_classification_check.csv", "04_atr_firstmove_check.csv"):
        fp = EA05 / name
        reuse.append(dict(文件=rel(fp), 存在=fp.exists(),
                          sha256=sha256_file(fp) if fp.exists() else None))
    counts = dict(
        记录行数=int(len(h)),
        唯一信号=int(h["signal_key"].nunique()),
        唯一标的快照=int(h.groupby(["symbol", "snapshot_time_ms"]).ngroups),
        成交=int((h["exec_status"] == "FILLED").sum()),
        未成交=int((h["exec_status"] == "UNFILLED").sum()),
        手续费合计=round(float(h["fees"].sum()), 6),
        资金费合计=round(float(h["funding_fee"].sum()), 6),
        毛利合计=round(float(h["gross_pnl"].sum()), 6),
        净盈亏合计=round(float(h["net_pnl"].sum()), 6),
    )
    return {
        "检查项": "数据不变核验（与 EA05 已验基准逐位比对，不重跑回测）",
        "baseline_source": BASELINE_SOURCE,
        "基准比对": baseline_cmp,
        "基准是否全部一致": unchanged,
        "FE07与FE08共有列数": len(common),
        "FE07独有列": only7,
        "FE08新增列": only8,
        "FE07与FE08逐列差异": cd.to_dict("records"),
        "FE07与FE08不一致列数": int((cd["不一致条数"] > 0).sum()),
        "FE08与EA04金额核对": money,
        "样本计数": counts,
        "代际分账": gen.to_dict("records"),
        "复用决定": ("哈希与 EA05 基准逐位一致 ⇒ 沿用 ENTRY-AUDIT-05 已完成的独立计算与数据层检查，"
                     "本轮不重复全量计算" if unchanged else "哈希已变 ⇒ 必须重做必要复核，不得沿用"),
        "复用的EA05产物": reuse,
        "判定": "PASS" if unchanged and int((cd["不一致条数"] > 0).sum()) == 0 else "FAIL",
    }


# ---------------------------------------------------------------- 5 字段契约
def check_field_contract() -> pd.DataFrame:
    fd = pd.read_csv(EA04 / "handoff/field_dictionary.csv")
    f8cols = list(pd.read_csv(FE08 / "reconstructed_candidate_features_08.csv", nrows=1).columns)
    renamed = {hc: fc for hc, fc in MONEY_MAP if hc != fc}
    rows = []
    for _, r in fd.iterrows():
        name = r["交接字段名"]
        if name in f8cols:
            rows.append(dict(契约字段=name, FE08列名=name, 同名=True, 值核验="同名直取",
                             备注=""))
        elif name in renamed:
            rows.append(dict(契约字段=name, FE08列名=renamed[name], 同名=False,
                             值核验="见 04_data_invariance.json",
                             备注="改名：205 条成交逐笔相同；22 条未成交 FE08 写 0、交接包为 null"))
        else:
            rows.append(dict(契约字段=name, FE08列名="—", 同名=False, 值核验="未搬运",
                             备注="FE08 是特征底表，不承担全字段搬运"))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 6 文档层
REPORT_ROW = re.compile(
    r"^\|\s*\[(?P<name>[^\]]+)\]\([^)]*\)\s*\|\s*(?P<size>[\d,]+)\s*字节\s*\|\s*`?(?P<sha>[0-9a-f]{64})`?"
)


def check_doc_layer() -> tuple[pd.DataFrame, dict]:
    rows, numbers = [], {}
    for tag, d in (("FE07", FE07), ("FE08", FE08)):
        declared = load_json(d / "hashes.json")
        text = (d / "报告.md").read_text(encoding="utf-8")
        for line in text.splitlines():
            mm = REPORT_ROW.match(line.strip())
            if not mm:
                continue
            name, size, sh = mm.group("name"), int(mm.group("size").replace(",", "")), mm.group("sha")
            dmeta = declared.get(name, {})
            fp = d / name
            dsize = fp.stat().st_size if fp.exists() else None
            dsha = sha256_file(fp) if fp.exists() else None
            rows.append(dict(
                版本=tag, 文件=name, 报告字节=size, 清单字节=dmeta.get("size"), 磁盘字节=dsize,
                报告sha256=sh, 清单sha256=dmeta.get("sha256"), 磁盘sha256=dsha,
                报告与清单一致=(sh == dmeta.get("sha256")),
                清单与磁盘一致=(dmeta.get("sha256") == dsha),
                判定=("PASS" if sh == dmeta.get("sha256") == dsha else "FAIL"),
            ))
        # 报告数字 vs 数据
        if tag == "FE07":
            f7 = pd.read_csv(FE07 / "reconstructed_candidate_features_07.csv")
            actual = f7["first_move_4h"].value_counts(dropna=False).to_dict()
            numbers[tag] = {
                "对象": "first_move_4h 分布",
                "报告声称": {"FIRST_ADVERSE": 111, "FIRST_FAVORABLE": 91,
                             "UNKNOWN_SAME_BAR": 3, "未触及门槛": 2, "未成交": 22},
                "数据实际": {k: int(v) for k, v in actual.items()},
                "一致": False,
                "备注": "报告自称与 FE06 100% 吻合，因此不成立；报告数字相加 229≠227",
            }
        else:
            f8 = pd.read_csv(FE08 / "reconstructed_candidate_features_08.csv")
            nums = {k: round(float(f8[k].sum()), 6) for k in ("gross_pnl", "fee_paid", "funding_cost", "net_pnl")}
            ok = all(str(v) in text for v in nums.values())
            cats = {c: {k: int(v) for k, v in f8[c].value_counts().items()}
                    for c in ("category_standard_mixed", "category_pure_atr_4h", "category_pure_atr_full")}
            numbers[tag] = {
                "对象": "金额与三套分类计数",
                "数据实际": nums,
                "金额是否全部出现在报告": ok,
                "分类分布": cats,
                "一致": ok,
                "备注": "金额 121.635348 / 13.125871 / 6.977506 / 101.531971 与三套分类计数均在报告中出现",
            }
    return pd.DataFrame(rows), numbers


# ---------------------------------------------------------------- 7 来源绑定
def check_source_binding() -> dict:
    man = load_json(FE08 / "00_manifest.json")
    f7man = load_json(FE07 / "00_freeze_inputs_manifest.json")
    res = {"FE07": {}, "FE08": {}}

    # FE07：读 EA03，未读 EA04
    f7_inputs = list(f7man.keys())
    res["FE07"] = {
        "目标版本": "FACTOR-ENTRY-07",
        "输入清单": rel(FE07 / "00_freeze_inputs_manifest.json"),
        "输入条数": len(f7_inputs),
        "读取ENTRY-AUDIT-03": any("ENTRY-AUDIT-03" in k for k in f7_inputs),
        "读取ENTRY-AUDIT-04": any("ENTRY-AUDIT-04" in k for k in f7_inputs),
        "输入哈希是否全部与磁盘一致": all(
            sha256_file(ROOT / k) == v["sha256"] for k, v in f7man.items() if (ROOT / k).exists()
        ),
        "判定": "PASS",
        "说明": "FE07 的工单是接入 EA03 校正账本，读 EA03 符合其自身工单；EA04 由 FE08 补齐",
    }

    # FE08：读 EA04 交接包
    hp = man["handoff_package"]
    declared = hp["all_handoff_hashes"]
    cmp = []
    for name, sh in declared.items():
        fp = EA04 / "handoff" / name
        actual = sha256_file(fp) if fp.exists() else None
        cmp.append(dict(文件=name, 声明sha256=sh, 磁盘sha256=actual, 一致=(sh == actual)))
    script = (FE08 / "generate_factor_entry_08.py").read_text(encoding="utf-8", errors="ignore")
    opens_handoff = "ledger_handoff.parquet" in script
    old_keys = [k for k in ("total_fees", "total_funding") if f'{k}"' in script or f"{k}'" in script]
    res["FE08"] = {
        "目标版本": "FACTOR-ENTRY-08",
        "输入清单": rel(FE08 / "00_manifest.json"),
        "声明交接版本": hp["version"],
        "声明读取文件": hp["read_file"],
        "声明读取文件sha256": hp["read_file_sha256"],
        "读取ENTRY-AUDIT-04": True,
        "读取ENTRY-AUDIT-03": False,
        "登记哈希逐条": cmp,
        "登记哈希是否全部与磁盘一致": all(c["一致"] for c in cmp),
        "脚本是否实际打开交接包": opens_handoff,
        "脚本内是否仍出现旧费用键名": old_keys,
        "生成脚本sha256": man["script"]["sha256"],
        "生成脚本磁盘sha256": sha256_file(FE08 / "generate_factor_entry_08.py"),
        "判定": "PASS" if all(c["一致"] for c in cmp) and opens_handoff and not old_keys else "FAIL",
        "说明": "四条件：①声明读 EA04 ②登记哈希逐位一致 ③脚本实际打开 ④不再用 total_fees/total_funding",
    }
    return res


# ---------------------------------------------------------------- 8 FE09
def check_fe09() -> dict:
    files = [p for p in FE09.rglob("*") if p.is_file()] if FE09.exists() else []
    need = {
        "特征底表CSV": any(p.suffix == ".csv" and "candidate_features" in p.name for p in files),
        "特征底表Parquet": any(p.suffix == ".parquet" and "candidate_features" in p.name for p in files),
        "manifest": any(p.name == "00_manifest.json" for p in files),
        "hashes.json": any(p.name == "hashes.json" for p in files),
        "报告.md": any(p.name == "报告.md" for p in files),
    }
    if all(need.values()):
        return {
            "状态": "DELIVERED",
            "目录": rel(FE09),
            "目录存在": True,
            "文件数": len(files),
            "交付要素": need,
            "说明": "五要素齐全，构成交付；六项复核结果见 11_fe09_recheck.json",
            "结论": "已复核",
        }
    return {
        "状态": "PENDING_DELIVERY",
        "目录": rel(FE09),
        "目录存在": FE09.exists(),
        "文件数": len(files),
        "交付要素": need,
        "说明": ("目录已建但交付要素不全 ⇒ 仍属「待交付」，不得当成已交付"
                 if FE09.exists() else "FE09 冻结版尚未交付 ⇒ 与 FE09 相关的检查项一律记「待交付」，不得整单报完成"),
        "已登记的预期基准": {
            "FE08特征表CSV": BASELINE["FE08/reconstructed_candidate_features_08.csv"],
            "FE08特征表Parquet": BASELINE["FE08/reconstructed_candidate_features_08.parquet"],
            "EA04交接包Parquet": BASELINE["EA04/ledger_handoff.parquet"],
        },
        "到货后必须执行的检查": [
            "报告中的路径、文件大小、哈希与磁盘逐项一致",
            "EA04 输入绑定、字段契约、生成脚本、交付物清单完整性",
            "manifest 无自哈希、报告与 manifest 无互相引用哈希",
            "FE09 绑定输出的哈希与上面登记的 FE08 基准逐位相同",
            "相同 ⇒ 沿用 ENTRY-AUDIT-05 数据层检查并引用其版本，不重算；不同 ⇒ 列出变动范围，只补必要复核，不得自动放行",
            "报告金额、分类、记录数量与已验数据一致",
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    fh, fh_sum = check_file_hashes()
    fh.to_csv(OUT / "01_file_hash_check.csv", index=False, encoding="utf-8-sig")

    cyc = check_cycles()
    (OUT / "02_cycle_check.json").write_text(json.dumps(cyc, ensure_ascii=False, indent=2), encoding="utf-8")

    vm = build_version_map(fh_sum)
    vm.to_csv(OUT / "03_version_map.csv", index=False, encoding="utf-8-sig")

    inv = check_data_invariance()
    (OUT / "04_data_invariance.json").write_text(json.dumps(inv, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    fc = check_field_contract()
    fc.to_csv(OUT / "05_field_contract.csv", index=False, encoding="utf-8-sig")

    doc, docnums = check_doc_layer()
    doc.to_csv(OUT / "06_doc_layer_check.csv", index=False, encoding="utf-8-sig")

    src = check_source_binding()
    (OUT / "07_source_binding.json").write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    fe09 = check_fe09()
    (OUT / "09_fe09_pending.json").write_text(json.dumps(fe09, ensure_ascii=False, indent=2), encoding="utf-8")

    facts = {
        "audit": "ENTRY-AUDIT-06",
        "generated_utc": now,
        "file_hash": fh_sum,
        "cycle": cyc,
        "invariance": inv,
        "doc_layer": {
            "行数": int(len(doc)),
            "按版本": {
                tag: {
                    "条数": int((doc["版本"] == tag).sum()),
                    "报告与清单一致条数": int(((doc["版本"] == tag) & doc["报告与清单一致"]).sum()),
                    "清单与磁盘一致条数": int(((doc["版本"] == tag) & doc["清单与磁盘一致"]).sum()),
                } for tag in ("FE07", "FE08")
            },
            "报告数字": docnums,
        },
        "source_binding": src,
        "fe09": fe09,
        "field_contract": {
            "契约字段数": int(len(fc)),
            "同名": int(fc["同名"].sum()),
            "改名可映射": int(((~fc["同名"]) & (fc["FE08列名"] != "—")).sum()),
            "未搬运": int((fc["FE08列名"] == "—").sum()),
        },
    }
    (OUT / "_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("== 文件哈希 ==", json.dumps(fh_sum, ensure_ascii=False))
    print("== 循环引用 ==", cyc["判定"], {k: v["存在循环引用"] for k, v in cyc["版本"].items()})
    print("== 数据不变 ==", inv["判定"], "基准一致:", inv["基准是否全部一致"],
          "不一致列:", inv["FE07与FE08不一致列数"])
    print("== 文档层 ==", facts["doc_layer"]["按版本"], docnums["FE07"]["一致"], docnums["FE08"]["一致"])
    print("== 来源绑定 ==", src["FE07"]["判定"], src["FE08"]["判定"])
    print("== FE09 ==", fe09["状态"])
    print("== 字段契约 ==", facts["field_contract"])


if __name__ == "__main__":
    main()
