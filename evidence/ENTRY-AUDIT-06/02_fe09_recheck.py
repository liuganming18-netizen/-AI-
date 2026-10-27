#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-06 · FE09 到货复核器（幂等：未交付只报「待交付」，交付完整则执行六项）

六项（对应工单第二节）：
  1 报告中的路径、文件大小、哈希与磁盘逐项一致
  2 EA04 输入绑定、字段契约、生成脚本、交付物清单完整性
  3 manifest 无自哈希、报告与 manifest 无互相引用哈希（单向 DAG）
  4 输出哈希与已验 FE08 基准逐位相同（相同则沿用 EA05 数据层检查，不重算）
  5 报告金额、分类、记录数量与已验数据一致
  6 结论不得自动放行

另加稳定性保护：开始与结束各取一次清单哈希，若中途变化则判 UNSTABLE（说明对方仍在写盘）。
产出：11_fe09_recheck.json
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
FE09 = ROOT / "evidence" / "FACTOR-ENTRY-09"
FE08 = ROOT / "evidence" / "FACTOR-ENTRY-08"
EA04 = ROOT / "evidence" / "ENTRY-AUDIT-04"

BASELINE = {
    "features_csv": "5360aeb189e7c465b9d7ca9e943558d3379ee2236f9245c5b267db14a9da22de",
    "features_parquet": "6e1b9369cca29cba4c8c6064cd11b90a4961f1adcd917aef7dffb7ce5d456849",
    "handoff_parquet": "93abe58a9b25b95060e30ecc2d4d684f5500e67acc9772201230b3149cee5054",
}
REPORT_ROW = re.compile(
    r"^\|\s*\[`?(?P<name>[^\]`]+)`?\]\([^)]*\)\s*\|\s*(?P<size>[\d,]+)\s*字节\s*\|\s*`?(?P<sha>[0-9a-f]{64})`?"
)
INPUT_ROW = re.compile(
    r"^\|\s*`(?P<name>[^`]+)`\s*\|\s*`(?P<path>evidence/[^`]+)`\s*\|\s*(?P<size>[\d,]+)\s*字节\s*\|\s*`(?P<sha>[0-9a-f]{64})`"
)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_cache(name: str) -> bool:
    return name.endswith(".pyc") or name.startswith("__pycache__/") or "/__pycache__/" in name


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    files = [p for p in FE09.rglob("*") if p.is_file()] if FE09.exists() else []
    names = {str(p.relative_to(FE09)) for p in files}

    def pick(pred):
        return next((p for p in files if pred(p)), None)

    csv_f = pick(lambda p: p.suffix == ".csv" and "candidate_features" in p.name)
    parq_f = pick(lambda p: p.suffix == ".parquet" and "candidate_features" in p.name)
    man_f = pick(lambda p: p.name == "00_manifest.json")
    hash_f = pick(lambda p: p.name == "hashes.json")
    rep_f = pick(lambda p: p.name == "报告.md")

    missing = [label for label, v in
               (("特征底表 CSV", csv_f), ("特征底表 Parquet", parq_f), ("manifest", man_f),
                ("hashes.json", hash_f), ("报告.md", rep_f)) if v is None]
    if missing:
        res = {
            "audit": "ENTRY-AUDIT-06", "stage": "FE09 到货复核", "checked_utc": now,
            "fe09_dir": str(FE09.relative_to(ROOT)), "目录存在": FE09.exists(),
            "文件数": len(files), "已有文件": sorted(names), "缺失": missing,
            "状态": "PENDING_DELIVERY", "判定": "PENDING_DELIVERY",
            "说明": "FE09 未构成交付：缺上述文件。不得据已有脚本推断产物，也不得整单报完成",
        }
        (OUT / "11_fe09_recheck.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print("FE09:", res["判定"], "缺失:", missing)
        return

    watch = {"hashes.json": sha(hash_f), "00_manifest.json": sha(man_f), "报告.md": sha(rep_f)}
    hmap = json.loads(hash_f.read_text(encoding="utf-8"))
    man = json.loads(man_f.read_text(encoding="utf-8"))
    text = rep_f.read_text(encoding="utf-8")
    table = pd.read_csv(csv_f)

    # ---- 1 报告逐项 vs 清单 vs 磁盘
    report_rows = []
    for line in text.splitlines():
        m = REPORT_ROW.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        fp = FE09 / name
        report_rows.append(dict(
            文件=name,
            报告字节=int(m.group("size").replace(",", "")),
            清单字节=hmap.get(name, {}).get("size"),
            磁盘字节=fp.stat().st_size if fp.exists() else None,
            报告sha=m.group("sha"),
            清单sha=hmap.get(name, {}).get("sha256"),
            磁盘sha=sha(fp) if fp.exists() else None,
        ))
    for r in report_rows:
        r["报告与清单一致"] = r["报告sha"] == r["清单sha"]
        r["清单与磁盘一致"] = r["清单sha"] == r["磁盘sha"]
        r["字节一致"] = r["报告字节"] == r["磁盘字节"]
    check1 = bool(report_rows) and all(
        r["报告与清单一致"] and r["清单与磁盘一致"] and r["字节一致"] for r in report_rows)

    # ---- 1b 报告中的 EA04 输入表（7 项：路径 / 大小 / 哈希）
    input_rows = []
    for line in text.splitlines():
        m = INPUT_ROW.match(line.strip())
        if not m:
            continue
        fp = ROOT / m.group("path")
        a = sha(fp) if fp.exists() else None
        size = int(m.group("size").replace(",", ""))
        input_rows.append(dict(
            文件=m.group("name"), 报告路径=m.group("path"),
            报告字节=size, 磁盘字节=fp.stat().st_size if fp.exists() else None,
            报告sha=m.group("sha"), 磁盘sha=a,
            路径存在=fp.exists(), 一致=bool(fp.exists() and a == m.group("sha")
                                          and size == fp.stat().st_size),
        ))
    check1b = len(input_rows) == 7 and all(r["一致"] for r in input_rows)

    # ---- 2 清单覆盖 / manifest 交付物 / EA04 绑定 / 脚本
    declared = set(hmap)
    unreg = sorted(names - declared)
    # hashes.json 是清单本身（不自哈希），缓存文件不算交付物
    unreg_real = [n for n in unreg if n != "hashes.json" and not is_cache(n)]
    h_bad = sorted(k for k in declared & names if hmap[k]["sha256"] != sha(FE09 / k))
    h_missing = sorted(declared - names)

    man_items = man.get("deliverables", [])
    man_bad = []
    for it in man_items:
        fp = FE09 / it["name"]
        if not fp.exists():
            man_bad.append({"文件": it["name"], "原因": "磁盘缺失"})
        elif sha(fp) != it["sha256"] or fp.stat().st_size != it["bytes"]:
            man_bad.append({"文件": it["name"], "原因": "大小或哈希不符",
                            "清单": [it["bytes"], it["sha256"][:12]],
                            "磁盘": [fp.stat().st_size, sha(fp)[:12]]})

    hp = man.get("handoff_package", {})
    handoff_cmp = []
    for name, h in hp.get("all_handoff_hashes", {}).items():
        fp = EA04 / "handoff" / name
        a = sha(fp) if fp.exists() else None
        handoff_cmp.append(dict(文件=name, 声明=h, 磁盘=a, 一致=(h == a)))

    py_files = [p for p in files if p.suffix == ".py"]
    # 只有「从记录里按键取值」才算旧口径；变量名叫 total_fees 但值来自 fee_paid 列不算
    old_pat = re.compile(r"""(\.get\(\s*["']total_(fees|funding)["']|\[\s*["']total_(fees|funding)["']\s*\])""")
    binding = {
        "声明交接版本": hp.get("version"),
        "声明读取文件": hp.get("read_file"),
        "登记交接哈希条数": len(handoff_cmp),
        "交接哈希全部一致": bool(handoff_cmp) and all(c["一致"] for c in handoff_cmp),
        "脚本实际打开交接包": any("ledger_handoff" in p.read_text(encoding="utf-8", errors="ignore")
                                   for p in py_files),
        "脚本仍用旧费用键名": sorted({m.group(0) for p in py_files
                                     for m in old_pat.finditer(
                                         p.read_text(encoding="utf-8", errors="ignore"))}),
        "字段契约文档": [p.name for p in files if "contract" in p.name.lower()],
    }
    check2 = (not unreg_real and not h_missing and not h_bad and not man_bad
              and binding["交接哈希全部一致"] and binding["脚本实际打开交接包"]
              and not binding["脚本仍用旧费用键名"])

    # ---- 3 自哈希 / 循环
    own_hash = sha(hash_f)
    man_hashes_report = any(it["name"] == "报告.md" for it in man_items)
    report_has_manifest = "00_manifest.json" in text and man.get("manifest_version", "@@") in text
    cycle = {
        "hashes.json 是否登记自身": "hashes.json" in declared,
        "hashes.json 内容是否含自身哈希": own_hash in hash_f.read_text(encoding="utf-8"),
        "manifest 是否登记自身": any(it["name"] == "00_manifest.json" for it in man_items),
        "manifest 登记了报告.md": man_hashes_report,
        "报告是否引用 manifest 的哈希或版本": bool(report_has_manifest),
        "报告是否含自身哈希": sha(rep_f) in text,
        "单向 DAG": "数据/脚本/测试 → 报告.md → manifest（hashes.json 汇总，不登记自身）",
    }
    cycle["存在循环"] = bool(
        cycle["hashes.json 是否登记自身"] or cycle["hashes.json 内容是否含自身哈希"]
        or cycle["manifest 是否登记自身"] or cycle["报告是否引用 manifest 的哈希或版本"]
        or cycle["报告是否含自身哈希"])
    check3 = not cycle["存在循环"]

    # ---- 4 与 FE08 基准
    f9sha, f9parq = sha(csv_f), sha(parq_f)
    same_csv = f9sha == BASELINE["features_csv"]
    same_parq = f9parq == BASELINE["features_parquet"]
    diff = {}
    if not (same_csv and same_parq):
        f8 = pd.read_csv(FE08 / "reconstructed_candidate_features_08.csv")
        common = [c for c in table.columns if c in f8.columns]
        cols = []
        for c in common:
            a, b = table[c], f8[c]
            if len(a) != len(b):
                cols.append({"列": c, "原因": "行数不同"}); continue
            if pd.api.types.is_bool_dtype(a) or pd.api.types.is_bool_dtype(b):
                a, b = a.astype("float64"), b.astype("float64")
            if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
                both = a.notna() & b.notna()
                if int((both & ((a - b).abs() > 1e-12)).sum()) or int((a.notna() ^ b.notna()).sum()):
                    cols.append({"列": c, "原因": "数值不同"})
            elif int((a.astype("string").fillna("∅") != b.astype("string").fillna("∅")).sum()):
                cols.append({"列": c, "原因": "字符不同"})
        diff = {"差异列": cols,
                "新增列": [c for c in table.columns if c not in f8.columns],
                "缺失列": [c for c in f8.columns if c not in table.columns]}
    check4 = same_csv and same_parq

    # ---- 5 报告数字与数据
    money_cols = {"gross_pnl": "gross_pnl", "fees": "fee_paid",
                  "funding_fee": "funding_cost", "net_pnl": "net_pnl"}
    nums = {k: round(float(table[v].sum()), 6) for k, v in money_cols.items() if v in table.columns}
    nums_ok = all(str(v) in text for v in nums.values())
    cats = {}
    for c in ("category_standard_mixed", "category_pure_atr_4h", "category_pure_atr_full"):
        if c in table.columns:
            cats[c] = {k: int(v) for k, v in table[c].value_counts().items()}
    counts = {
        "行数": int(len(table)),
        "成交": int((table["exec_status"] == "FILLED").sum()) if "exec_status" in table else None,
        "未成交": int(table["exec_status"].astype(str).str.startswith("UNFILLED").sum())
        if "exec_status" in table else None,
        "报告写明行数": "227" in text,
        "报告写明成交数": "205" in text,
        "报告写明未成交数": "22" in text,
    }
    cats_ok = all(str(n) in text for dist in cats.values() for n in dist.values())
    check5 = bool(nums_ok and cats_ok and counts["报告写明行数"]
                  and counts["报告写明成交数"] and counts["报告写明未成交数"])

    # ---- 稳定性
    watch2 = {"hashes.json": sha(hash_f), "00_manifest.json": sha(man_f), "报告.md": sha(rep_f)}
    stable = watch == watch2

    checks = {
        "1a_交付物逐项一致": check1,
        "1b_EA04输入表逐项一致": check1b,
        "2_清单与交付物完整": check2,
        "3_无自哈希无循环": check3,
        "4_与FE08基准一致": check4,
        "5_报告数字与数据一致": check5,
    }
    verdict = "PASS" if all(checks.values()) and stable else ("UNSTABLE" if not stable else "FAIL")
    res = {
        "audit": "ENTRY-AUDIT-06",
        "stage": "FE09 到货复核",
        "checked_utc": now,
        "状态": "DELIVERED",
        "fe09_dir": str(FE09.relative_to(ROOT)),
        "文件数": len(files),
        "报告逐项": report_rows,
        "报告中EA04输入表": input_rows,
        "清单核对": {"清单条数": len(declared), "磁盘文件数": len(names),
                     "未登记": unreg, "未登记实质文件": unreg_real,
                     "清单登记但缺失": h_missing, "哈希不符": h_bad},
        "manifest交付物": {"条数": len(man_items), "不符": man_bad},
        "输入绑定与脚本": binding,
        "交接哈希逐条": handoff_cmp,
        "循环引用": cycle,
        "输出哈希": {"FE09_CSV": f9sha, "FE08_CSV基准": BASELINE["features_csv"], "CSV一致": same_csv,
                     "FE09_Parquet": f9parq, "FE08_Parquet基准": BASELINE["features_parquet"],
                     "Parquet一致": same_parq},
        "变动范围": diff,
        "报告数字": {"金额": nums, "金额全部出现在报告": nums_ok, "分类分布": cats, "计数": counts},
        "稳定性": {"开始": watch, "结束": watch2, "稳定": stable},
        "逐项判定": checks,
        "复用决定": ("输出与 FE08 基准逐位相同 ⇒ 沿用 ENTRY-AUDIT-05 数据层检查并引用其版本，不重算"
                     if check4 else "输出已变 ⇒ 列变动范围、只补必要复核，不得自动放行"),
        "放行结论": ("文档与清单层通过；数据层沿用已验结论；但策略有效性与 PIT/规格限制不变，"
                     "本项不构成任何策略放行" if verdict == "PASS" else "不放行"),
        "判定": verdict,
    }
    (OUT / "11_fe09_recheck.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("FE09:", verdict, json.dumps(checks, ensure_ascii=False), "stable:", stable)
    if verdict != "PASS":
        print(json.dumps({k: v for k, v in res.items() if k in
                          ("清单核对", "manifest交付物", "循环引用", "变动范围")},
                         ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
