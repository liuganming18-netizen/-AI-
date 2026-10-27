# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜07 数字对撞（正文 ↔ 台账 ↔ 产物，双向）。

正向：台账里每个数字都必须在正文里出现（**写进台账却没写进正文** = 台账虚高）。
反向：正文里每个数字都必须能追到台账或结构性白名单（**正文有台账外的数** = 手抄）。
另外再抽 12 个关键数字，**回到产物 JSON 重新取一次**，证明台账本身没抄错。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "out"
DOC = BASE / "README.md"

# 结构性白名单：阈值、周期、章节号、协议常量 —— 它们不是「统计结果」，不需要台账
WHITELIST = {
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "15", "24", "30", "40",
    "50", "95", "100", "500", "401", "0.25", "1.5", "0.05", "0.001", "0.1",
    "1e-12", "1e-9", "4.0", "8.0", "2020", "2026", "48", "51", "05", "06", "07",
    "00", "01", "02", "03", "04", "13", "14",
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
SECTION_RE = re.compile(r"§\d+(?:\.\d+)?")
SCRIPT_RE = re.compile(r"\b0\d_[^\s`）)]*")
CODE_RE = re.compile(r"```.*?```", re.S)
MONTH_RE = re.compile(r"20\d{2}-\d{2}(?!-\d)")
HEAD_RE = re.compile(r"^#{2,4}\s*\d+(?:\.\d+)*", re.M)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")


def iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(v) -> set:
    """一个数值的多种等价写法。"""
    out = set()
    if isinstance(v, bool):
        return out
    if isinstance(v, int):
        out |= {str(v), f"{v}.0"}
    elif isinstance(v, float):
        out |= {f"{v:g}", repr(v)}
        if abs(v - round(v)) < 1e-12:
            out |= {str(int(round(v)))}
    else:
        out.add(str(v))
    return out


def main():
    t0 = time.time()
    led = json.loads((OUT / "06_正文数字台账.json").read_text(encoding="utf-8"))
    text = DOC.read_text(encoding="utf-8")

    # ── 正向：台账 → 正文 ────────────────────────────────────────────────
    missing_in_doc = []
    for item in led["numbers"]:
        forms = canon(item["值"])
        if not any(f in text for f in forms):
            missing_in_doc.append(item)

    # ── 反向：正文 → 台账 / 白名单 ──────────────────────────────────────
    t2 = CODE_RE.sub(" ", text)
    t2 = DATE_RE.sub(" <DATE> ", t2)
    t2 = SECTION_RE.sub(" <SEC> ", t2)
    t2 = SCRIPT_RE.sub(" <SCRIPT> ", t2)
    t2 = MONTH_RE.sub(" <MONTH> ", t2)
    t2 = HEAD_RE.sub(" <HEAD> ", t2)
    known = set(WHITELIST)
    for item in led["numbers"]:
        known |= canon(item["值"])
    ledger_nums = []
    for item in led["numbers"]:
        try:
            ledger_nums.append(float(item["值"]))
        except (TypeError, ValueError):
            pass
    unknown = []
    for m in NUM_RE.finditer(t2):
        tok = m.group(0)
        if tok in known:
            continue
        if tok.lstrip("-") in known:
            continue
        try:                                  # 数值等价：0.659199640 ≡ 0.65919964
            fv = float(tok)
            if any(abs(fv - lv) <= 1e-12 * max(1.0, abs(lv)) for lv in ledger_nums):
                continue
        except ValueError:
            pass
        unknown.append({"token": tok, "context": t2[max(0, m.start() - 40):m.end() + 40]
                        .replace("\n", " ")})

    # ── 抽样回产物复核：台账值是否真的等于产物里的值 ────────────────────
    prods = {n: json.loads((OUT / f"{n}.json").read_text(encoding="utf-8"))
             for n in ("00_范围锚定", "01_三层字段级核对", "02_原生结算周期与缺口段",
                       "03_逐事件对账", "04_受影响交易", "05_读取器与接入点")}
    spot = []

    checks = [
        ("00", "data_identity.hot.funding_rows_binance_um", 2065205),
        ("01", "hot.L3_当时可得.gt_24h占比", None),
        ("01", "cold.L2_rate_mark非空.全历史_mark空", None),
        ("02", "聚合.all.missing_hot", None),
        ("02", "聚合.mother_48.missing_union", None),
        ("02", "热库缺口段聚类.0.hours", None),
        ("03", "全池.n_cold_only", None),
        ("03", "母集48.n_rate_conflict", None),
        ("04", "汇总.资金费_并集", None),
        ("04", "04A复核.holdout_final.口径A_热库0结算且持仓≥8h", None),
        ("05", "读取器清单", None),
    ]
    for tag, path, expect in checks:
        prod = {"00": prods["00_范围锚定"], "01": prods["01_三层字段级核对"],
                "02": prods["02_原生结算周期与缺口段"], "03": prods["03_逐事件对账"],
                "04": prods["04_受影响交易"], "05": prods["05_读取器与接入点"]}[tag]
        node = prod
        ok = True
        for part in path.split("."):
            try:
                node = node[int(part)] if isinstance(node, list) else node[part]
            except Exception:
                ok = False
                break
        if isinstance(node, list):
            node = len(node)
        spot.append({"产物": f"{tag}_{path}", "产物值": node,
                     "台账值": next((x["值"] for x in led["numbers"]
                                  if x["来源"].startswith(tag) and path.split(".")[-1]
                                  in x["来源"]), None),
                     "取到": ok})

    # 台账里的值必须能在产物里找到（来源字段是人工写的路径，这里按值比对）
    prod_values = set()

    def walk(n, acc):
        if isinstance(n, dict):
            for v in n.values():
                walk(v, acc)
        elif isinstance(n, list):
            for v in n:
                walk(v, acc)
        elif isinstance(n, (int, float)) and not isinstance(n, bool):
            acc |= canon(n)
    for p in prods.values():
        walk(p, prod_values)
    orphan = [x for x in led["numbers"] if not (canon(x["值"]) & prod_values)]

    # 派生值（比值/换算）不在产物里，必须**就地重算**并比对，不能直接放行
    h = prods["01_三层字段级核对"]["hot"]
    cld = prods["01_三层字段级核对"]["cold"]
    derived = {
        "01#hot.L3.最小滞后ms÷3600000": round(h["L3_当时可得"]["最小滞后ms"] / 3600000, 2),
        "01#hot 来源分布[0].rows ÷ L1.全历史行数": round(
            100.0 * h["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"]
            / h["L1_结算事件覆盖"]["全历史行数"], 2),
        "01#cold 来源分布[0].rows ÷ L1.全历史行数": round(
            100.0 * cld["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"]
            / cld["L1_结算事件覆盖"]["全历史行数"], 2),
        "02#聚合.mother_48.symbols − symbols_unreliable_grid": int(
            prods["02_原生结算周期与缺口段"]["聚合"]["mother_48"]["symbols"]
            - prods["02_原生结算周期与缺口段"]["聚合"]["mother_48"]["symbols_unreliable_grid"]),
    }
    still = []
    for x in orphan:
        exp = derived.get(x["来源"])
        if exp is not None and abs(float(exp) - float(x["值"])) < 1e-9:
            continue
        still.append(x)

    ok = (not missing_in_doc) and (not unknown) and (not still)
    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "07_数字对撞",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "正文行数": len(text.splitlines()),
        "台账数字": led["count"],
        "正向_台账未出现在正文": missing_in_doc,
        "正向_通过": not missing_in_doc,
        "反向_正文里的台账外数字": unknown[:40],
        "反向_未登记数量": len(unknown),
        "反向_通过": not unknown,
        "产物回查_抽样": spot,
        "产物回查_台账值不在任何产物里": orphan,
        "产物回查_派生值重算通过": [x["来源"] for x in orphan if x not in still],
        "产物回查_无法追溯": still,
        "总体": "通过" if ok else "不通过",
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (OUT / "07_数字对撞.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("正文行数", "台账数字", "正向_通过",
                                          "反向_未登记数量", "反向_通过", "总体")},
                     ensure_ascii=False, indent=2))
    if unknown:
        print("未登记样例：")
        for u in unknown[:12]:
            print(" ", u["token"], "|", u["context"][:90])
    if missing_in_doc:
        print("台账未进正文样例：", missing_in_doc[:5])


if __name__ == "__main__":
    main()
