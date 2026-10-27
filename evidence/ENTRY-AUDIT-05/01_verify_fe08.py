# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-05 / 01 · 第二阶段：接收 FE08 并做版本与差异核验

工单第二阶段：
  反重力将在 evidence/FACTOR-ENTRY-08/ 补齐 EA04 交接包来源。
  核对其是否实际读取指定冻结包，并比较 FE08 与 FE07 输出。
  若数值不变，沿用已完成的独立计算，只补必要的版本与差异核验；
  不得为收尾重复全池回测。
  若 FE08 未到，先报告第一阶段结果，明确第二阶段待交付，不能整单报完成。

本脚本不重跑任何回测，只做读取与比对。
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
FE08 = ROOT / "evidence" / "FACTOR-ENTRY-08"
A4 = ROOT / "evidence" / "ENTRY-AUDIT-04"

EA04_PRIMARY = "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet"
EA04_CSV = "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.csv"
EA04_META = "evidence/ENTRY-AUDIT-04/handoff/handoff_meta.json"

MONEY_PAIRS = [("gross_pnl", "gross_pnl"), ("fee_paid", "fees"),
               ("funding_cost", "funding_fee"), ("net_pnl", "net_pnl")]
CAT_COLS = ["category_standard_mixed", "category_pure_atr_4h", "category_pure_atr_full"]
ATR_COLS = ["signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h",
            "mfe_r_4h", "mae_r_4h", "mfe_r", "mfe_atr_full", "first_move_4h"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover() -> dict:
    d = {"exists": FE08.exists(), "tables": [], "json": [], "markdown": [], "scripts": []}
    if not d["exists"]:
        return d
    for p in sorted(FE08.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or ".pytest_cache" in p.parts:
            continue
        s = p.suffix.lower()
        if s in (".csv", ".parquet"):
            d["tables"].append(p)
        elif s == ".json":
            d["json"].append(p)
        elif s in (".md", ".markdown"):
            d["markdown"].append(p)
        elif s in (".py", ".sh"):
            d["scripts"].append(p)
    return d


def pick_table(paths: list[Path]) -> tuple[Path | None, pd.DataFrame | None, str]:
    """挑候选级特征表。

    ⚠ 不能只按行数挑：FE08 目录里还有 discrepancy_vs_fe07.csv 也是 227 行，
    但它是差异表不是特征表。判据加成：文件名含 features、列数多。
    """
    cands = []
    note = "未找到候选级特征表"
    for p in paths:
        try:
            df = pd.read_parquet(p) if p.suffix.lower() == ".parquet" else pd.read_csv(p)
        except Exception as exc:
            note = f"{p.name} 读取失败：{type(exc).__name__}"
            continue
        if "candidate_id" not in df.columns or len(df) < 200:
            continue
        score = (1000 if "features" in p.name.lower() else 0) + len(df.columns)
        cands.append((score, p, df))
    if not cands:
        return None, None, note
    cands.sort(key=lambda x: -x[0])
    score, best, df = cands[0]
    return best, df, f"{best.name}（{len(df)} 行 × {len(df.columns)} 列）"


def num_cmp(a: pd.Series, b: pd.Series, tol: float = 1e-9) -> dict:
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    na, nb = a.isna(), b.isna()
    both = ~na & ~nb
    d = (a[both] - b[both]).abs()
    return {
        "比较条数": int(len(a)),
        "NaN 对 NaN": int((na & nb).sum()),
        "一边空一边有值": int((na ^ nb).sum()),
        "数值不一致(>tol)": int((d > tol).sum()),
        "最大绝对差": (None if len(d) == 0 else float(np.nanmax(d.to_numpy()))),
    }


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fe08 = discover()
    fe07 = pd.read_csv(FE07 / "reconstructed_candidate_features_07.csv")

    result = {
        "audit": "ENTRY-AUDIT-05",
        "stage": "第二阶段 · 接收 FE08 并做版本与差异核验",
        "generated_utc": now,
        "fe08_dir": str(FE08),
        "fe08_exists": fe08["exists"],
        "ea04_handoff": {
            "primary": EA04_PRIMARY,
            "primary_sha256": sha256(ROOT / EA04_PRIMARY),
            "csv": EA04_CSV,
            "csv_sha256": sha256(ROOT / EA04_CSV),
            "meta": EA04_META,
            "meta_sha256": sha256(ROOT / EA04_META),
        },
    }

    if not fe08["exists"]:
        result.update({
            "status": "PENDING_DELIVERY",
            "conclusion": ("FE08 尚未交付（evidence/ 下没有 FACTOR-ENTRY-08）⇒ 第二阶段无法执行。"
                           "本工单只完成第一阶段（FE07 七项复核），不得报整体完成。"),
            "ready_checks": [
                {"序号": 1, "检查项": "FE08 是否实际读取 EA04 交接包",
                 "判定规则": ("FE08 的输入清单/脚本里必须出现 "
                              f"{EA04_PRIMARY}（或其 csv/meta），且登记的 sha256 必须与磁盘逐位相同；"
                              "若仍只列 ENTRY-AUDIT-03 的文件 ⇒ 判「未按指定冻结包接入」")},
                {"序号": 2, "检查项": "FE08 与 FE07 的四项金额是否相同",
                 "判定规则": "gross_pnl / fee_paid / funding_cost / net_pnl 逐笔比，容差 1e-9；相同则沿用第一阶段已完成的独立计算"},
                {"序号": 3, "检查项": "FE08 与 FE07 的三套分类是否相同",
                 "判定规则": "category_standard_mixed / pure_atr_4h / pure_atr_full 逐条比；任一不同则须重算并说明"},
                {"序号": 4, "检查项": "FE08 与 FE07 的 ATR/首触是否相同",
                 "判定规则": "8 个不依赖费用字段逐字段比，容差 1e-12；NO_DATA 条数须仍等于未成交数 22"},
                {"序号": 5, "检查项": "FE08 自身多格式一致性",
                 "判定规则": "CSV/Parquet 逐值比（空值按位置比，不算内容差异）；报告.md 的数字与哈希表须与数据一致"},
            ],
            "baseline_ready": {
                "FE07 表 sha256": sha256(FE07 / "reconstructed_candidate_features_07.csv"),
                "FE07 行数": int(len(fe07)),
                "FE07 三套分类分布": {c: {k: int(v) for k, v in fe07[c].value_counts().items()}
                                      for c in CAT_COLS},
                "note": "基准已冻结；FE08 一到直接跑本脚本即可出结论，无需重跑回测",
            },
        })
        (OUT / "01_fe08_recheck.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("FE08 未交付 ⇒ 第二阶段状态 PENDING_DELIVERY")
        print("  五项核验规则与 FE07 基准已写入 01_fe08_recheck.json")
        return

    # ══════════ FE08 已交付 ══════════
    table, df8, note = pick_table(fe08["tables"])
    result["fe08_table"] = {"path": (str(table) if table else None),
                            "sha256": (sha256(table) if table else None), "note": note}

    # ── ① 是否读了 EA04 交接包 ──
    # 主判据：00_manifest.json 明确声明读的是哪个文件、哈希多少，且与磁盘逐位相同
    # 辅判据：脚本里是否真的打开该文件
    manifest = {}
    for p in fe08["json"]:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        hp = (obj or {}).get("handoff_package")
        if isinstance(hp, dict):
            manifest = {"file": p.name, **hp}
            break

    declared = dict((manifest.get("all_handoff_hashes") or {}))
    if manifest.get("read_file") and manifest.get("read_file_sha256"):
        declared[Path(manifest["read_file"]).name] = manifest["read_file_sha256"]

    decl_check = []
    for name, h in declared.items():
        p = ROOT / "evidence" / "ENTRY-AUDIT-04" / "handoff" / name
        cur = sha256(p) if p.exists() else None
        decl_check.append({"文件": f"ENTRY-AUDIT-04/handoff/{name}",
                           "FE08 声明 sha": h[:16],
                           "磁盘实际 sha": (cur or "")[:16],
                           "一致": bool(cur and cur == h)})

    # 脚本里是否真的打开交接包
    script_reads = []
    for p in fe08["scripts"]:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "ledger_handoff" in txt and ("read_parquet" in txt or "read_csv" in txt):
            script_reads.append(p.name)

    # 旧口径：只在脚本里查「是否把 total_fees/total_funding 当费用来源赋值」
    # 报告与说明里提到这两个词是在讲历史错误，不算命中
    old_key_hits = []
    for p in fe08["scripts"]:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in [r'\.get\(\s*["\']total_fees', r'\.get\(\s*["\']total_funding',
                    r'\[\s*["\']total_fees\s*["\']\s*\]']:
            if re.search(pat, txt):
                old_key_hits.append({"file": p.name, "pattern": pat})

    # 是否还在读 EA03 身份表当主来源（FE08 允许引用 EA03 做对照，但不能当主输入）
    reads_ea03_as_input = False
    if manifest:
        reads_ea03_as_input = "ENTRY-AUDIT-03" in json.dumps(manifest.get("all_handoff_hashes") or {})

    reads_ea04 = bool(declared) and any(
        x["文件"].endswith(Path(manifest.get("read_file") or "x").name) for x in decl_check)
    all_hash_ok = bool(decl_check) and all(x["一致"] for x in decl_check)

    result["check_1_source"] = {
        "检查项": "FE08 是否实际读取 EA04 交接包",
        "清单文件": manifest.get("file"),
        "清单声明读取的文件": manifest.get("read_file"),
        "清单声明的交接版本": manifest.get("version"),
        "登记哈希逐条核对": decl_check,
        "登记哈希是否全部与磁盘一致": all_hash_ok,
        "脚本里实际打开交接包的文件": script_reads,
        "命中旧键名（仅脚本内赋值才算）": old_key_hits,
        "是否仍把 EA03 当主输入": reads_ea03_as_input,
        "判定": ("PASS" if all_hash_ok and script_reads and not old_key_hits else "FAIL"),
        "说明": ("判 PASS 需同时满足：①清单声明读 EA04 交接包 ②登记的每个哈希都与磁盘逐位相同 "
                 "③脚本里确实打开该文件 ④脚本里没有再把 total_fees/total_funding 当费用来源"),
    }

    # ── ② 与 FE07 的四项金额 ──
    money = {}
    if df8 is not None:
        m = fe07[["candidate_id"] + [c for c, _ in MONEY_PAIRS if c in fe07.columns]].merge(
            df8[["candidate_id"] + [c for c, _ in MONEY_PAIRS if c in df8.columns]],
            on="candidate_id", how="outer", suffixes=("_07", "_08"), indicator=True)
        money["身份对齐"] = m["_merge"].value_counts().to_dict()
        per = {}
        for c, _ in MONEY_PAIRS:
            if f"{c}_07" in m.columns and f"{c}_08" in m.columns:
                per[c] = num_cmp(m[f"{c}_07"], m[f"{c}_08"])
        money["逐列"] = per
        money["判定"] = ("PASS" if per and all(v["数值不一致(>tol)"] == 0 for v in per.values()) else "FAIL")

        # FE08 与 FE07 一致，不等于与交接包口径一致。
        # 未成交行必须单独对交接包比一次，否则「两边都写 0」会被误判成「已修正」。
        try:
            # 交接包的列名是 fees / funding_fee，FE07/FE08 是 fee_paid / funding_cost
            ho = pd.read_parquet(ROOT / EA04_PRIMARY).rename(
                columns={h: f for f, h in MONEY_PAIRS})
            mm = ho[["candidate_id"] + [c for c, _ in MONEY_PAIRS if c in ho.columns]].merge(
                df8[["candidate_id"] + [c for c, _ in MONEY_PAIRS if c in df8.columns]],
                on="candidate_id", how="inner", suffixes=("_ho", "_08"))
            if "is_filled" in df8.columns:
                mm = mm.merge(df8[["candidate_id", "is_filled"]], on="candidate_id", how="left")
                unf = mm[mm["is_filled"] != True]
            else:
                unf = mm.iloc[0:0]
            money["未成交行 vs 交接包"] = {
                "条数": int(len(unf)),
                **{f"{c}": {
                    "FE08 取值": sorted(set(unf[f"{c}_08"].dropna().round(9).tolist()))[:3],
                    "交接包空值条数": int(unf[f"{c}_ho"].isna().sum()),
                    "FE08 空值条数": int(unf[f"{c}_08"].isna().sum()),
                } for c, _ in MONEY_PAIRS if f"{c}_08" in unf.columns},
                "说明": ("FE07 与 FE08 在这 22 行都写 0，而交接包是 null；"
                         "「两边一致」只证明两代之间没变，不证明符合缺值政策"),
            }
        except Exception as e:  # 交接包读不到不影响主判定，但要留痕
            money["未成交行 vs 交接包"] = {"错误": repr(e)}
    result["check_2_money"] = {"检查项": "FE08 与 FE07 四项金额", **money}

    # ── ③ 三套分类 ──
    cat = {}
    if df8 is not None:
        for c in CAT_COLS:
            if c in df8.columns and c in fe07.columns:
                mm = fe07[["candidate_id", c]].merge(df8[["candidate_id", c]], on="candidate_id",
                                                     suffixes=("_07", "_08"))
                diff = mm[mm[f"{c}_07"].astype(str) != mm[f"{c}_08"].astype(str)]
                cat[c] = {"比较条数": int(len(mm)), "不一致": int(len(diff)),
                          "不一致明细": diff.head(10).to_dict("records"),
                          "判定": "PASS" if len(diff) == 0 else "FAIL"}
    result["check_3_classification"] = {
        "检查项": "FE08 与 FE07 三套分类",
        "逐体系": cat,
        "判定": ("PASS" if cat and all(v["判定"] == "PASS" for v in cat.values()) else "FAIL"),
    }

    # ── ④ ATR / 首触 ──
    atr = {}
    if df8 is not None:
        for c in ATR_COLS:
            if c in df8.columns and c in fe07.columns:
                if pd.api.types.is_numeric_dtype(fe07[c]) and not pd.api.types.is_bool_dtype(fe07[c]):
                    atr[c] = num_cmp(fe07[c], df8[c], tol=1e-12)
                else:
                    na7, na8 = fe07[c].isna(), df8[c].isna()
                    s7 = fe07[c].where(~na7, "<NA>").astype(str)
                    s8 = df8[c].where(~na8, "<NA>").astype(str)
                    atr[c] = {"非空内容不同行数": int((s7 != s8).sum()),
                              "空值位置相同": bool((na7 == na8).all())}
        no_data8 = int((df8.get("first_move_4h", pd.Series(dtype=object)).astype(str) == "NO_DATA").sum())
        unfilled8 = int((df8.get("is_filled", pd.Series(dtype=bool)) != True).sum())
        atr["_NO_DATA_条数"] = no_data8
        atr["_未成交条数"] = unfilled8
        atr["_NO_DATA等于未成交"] = no_data8 == unfilled8
    result["check_4_atr"] = {
        "检查项": "FE08 与 FE07 的 ATR/首触",
        "逐字段": atr,
        "判定": ("PASS" if all((v.get("数值不一致(>tol)", 0) == 0 and v.get("一边空一边有值", 0) == 0
                                and v.get("非空内容不同行数", 0) == 0)
                               for k, v in atr.items() if not k.startswith("_"))
                 and atr.get("_NO_DATA等于未成交") is True else "FAIL"),
    }

    # ── ⑤ FE08 自身多格式 ──
    fmt = {}
    for p in fe08["tables"]:
        twin = p.with_suffix(".parquet" if p.suffix.lower() == ".csv" else ".csv")
        if not twin.exists():
            continue
        try:
            da = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_parquet(p)
            db = pd.read_parquet(twin) if twin.suffix.lower() == ".parquet" else pd.read_csv(twin)
        except Exception as exc:
            fmt[f"{p.name}/{twin.name}"] = {"error": type(exc).__name__}
            continue
        bad = {}
        for c in [x for x in da.columns if x in db.columns]:
            if pd.api.types.is_numeric_dtype(da[c]) and not pd.api.types.is_bool_dtype(da[c]):
                a = pd.to_numeric(da[c], errors="coerce")
                b = pd.to_numeric(db[c], errors="coerce")
                d = (a - b).abs()
                if d.notna().any() and float(np.nanmax(d.to_numpy())) > 1e-9:
                    bad[c] = float(np.nanmax(d.to_numpy()))
            else:
                na, nb = da[c].isna(), db[c].isna()
                if not bool((na == nb).all()):
                    bad[c] = "空值位置不同"
                elif int((da[c].where(~na, "<NA>").astype(str)
                          != db[c].where(~nb, "<NA>").astype(str)).sum()):
                    bad[c] = "非空内容不同"
        fmt[f"{p.name}/{twin.name}"] = {"不一致列": bad, "判定": "PASS" if not bad else "FAIL"}
    result["check_5_format"] = {"检查项": "FE08 自身多格式一致性", "配对": fmt,
                                "判定": ("PASS" if fmt and all(v.get("判定") == "PASS" for v in fmt.values())
                                         else "FAIL")}

    # ── ⑥ FE08 文档层：报告.md 的数字与哈希表 ──
    # FE07 就是栽在这里（报告数字与自己的数据不符、哈希表全是旧值），FE08 必须同样查
    docs = {}
    hj8 = {}
    for p in fe08["json"]:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(obj, dict) and all(isinstance(v, dict) and "sha256" in v for v in obj.values()):
            hj8 = obj
            docs["哈希清单文件"] = p.name
            break
    # 哈希清单 vs 磁盘
    hj_cmp = []
    for name, meta in hj8.items():
        p = FE08 / name
        cur = sha256(p) if p.exists() else None
        hj_cmp.append({"文件": name, "清单 sha": meta["sha256"][:16],
                       "磁盘 sha": (cur or "")[:16], "一致": bool(cur and cur == meta["sha256"])})
    docs["哈希清单与磁盘"] = {
        "条数": len(hj_cmp),
        "全部一致": bool(hj_cmp) and all(x["一致"] for x in hj_cmp),
        "不一致": [x["文件"] for x in hj_cmp if not x["一致"]],
    }
    # 报告.md 里的哈希表 vs 清单 vs 磁盘
    md8 = ""
    for p in fe08["markdown"]:
        md8 += p.read_text(encoding="utf-8", errors="ignore")
    md_hash_rows = []
    for m_ in re.finditer(r"\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*([\d,]+)\s*字节\s*\|\s*`([0-9a-f]{64})`\s*\|", md8):
        name, size, h = m_[1], int(m_[2].replace(",", "")), m_[3]
        p = FE08 / name
        actual = hj8.get(name)
        disk = sha256(p) if p.exists() else None
        md_hash_rows.append({
            "文件": name,
            "报告字节": size,
            "清单字节": (actual or {}).get("size"),
            "磁盘字节": (p.stat().st_size if p.exists() else None),
            "报告与清单 sha 一致": bool(actual and actual["sha256"] == h),
            "清单与磁盘 sha 一致": bool(disk and actual and disk == actual["sha256"]),
        })
    docs["报告.md 哈希表"] = {
        "条数": len(md_hash_rows),
        "报告与清单一致条数": sum(1 for x in md_hash_rows if x["报告与清单 sha 一致"]),
        "清单与磁盘一致条数": sum(1 for x in md_hash_rows if x["清单与磁盘 sha 一致"]),
        "明细": md_hash_rows,
    }
    # 报告.md 的关键金额 vs 数据实际
    key_money = {}
    if df8 is not None and "is_filled" in df8.columns:
        f8 = df8[df8["is_filled"] == True]
        for col, label in [("gross_pnl", "毛利"), ("fee_paid", "手续费"),
                           ("funding_cost", "资金费"), ("net_pnl", "净盈亏")]:
            if col in f8.columns:
                val = float(pd.to_numeric(f8[col], errors="coerce").sum())
                s6 = f"{val:.6f}"
                key_money[label] = {"数据实际": round(val, 6), "六位小数串": s6,
                                    "报告中是否出现": s6 in md8}
    docs["报告.md 金额 vs 数据"] = key_money
    # 报告.md 的分类分布 vs 数据实际
    cat_doc = {}
    for c in CAT_COLS:
        if df8 is not None and c in df8.columns:
            dist = {k: int(v) for k, v in df8[c].value_counts().items()}
            cat_doc[c] = {"数据实际": dist,
                          "报告中是否出现各计数": all(str(v) in md8 for v in dist.values())}
    docs["报告.md 分类分布 vs 数据"] = cat_doc
    md_ok = (all(v["报告中是否出现"] for v in key_money.values())
             and all(v["报告中是否出现各计数"] for v in cat_doc.values()))
    hash_ok8 = (len(md_hash_rows) > 0
                and all(x["报告与清单 sha 一致"] for x in md_hash_rows))
    docs["判定"] = ("PASS" if md_ok and hash_ok8 and docs["哈希清单与磁盘"]["全部一致"]
                    else ("PASS_WITH_NOTE" if md_ok and docs["哈希清单与磁盘"]["全部一致"] else "FAIL"))
    docs["说明"] = ("文档层与数据层分开看：哈希清单与磁盘一致、报告金额与分类分布与数据一致，"
                    "才算文档层通过；报告里的哈希表若与清单不符，说明是旧版或占位值")
    result["check_6_docs"] = {"检查项": "FE08 文档层一致性（报告数字与哈希表）", **docs}

    verdicts = {k: v["判定"] for k, v in result.items() if isinstance(v, dict) and "判定" in v}
    result["acceptance"] = verdicts
    result["all_pass"] = all(v == "PASS" for v in verdicts.values())
    result["reuse_decision"] = ("数值未变 ⇒ 沿用第一阶段已完成的独立计算，不重跑回测"
                                if result["check_2_money"].get("判定") == "PASS"
                                and result["check_3_classification"].get("判定") == "PASS"
                                else "数值有变 ⇒ 需按第一阶段方法重算并给出差异表")

    (OUT / "01_fe08_recheck.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdicts, ensure_ascii=False, indent=1))
    print("结论：", result["reuse_decision"])


if __name__ == "__main__":
    main()
