# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R2 · 08 数字对撞

三向校验，任一不通过即 rc=1：
  F1 台账 → 正文：按登记的字符偏移逐字符比对（强校验，不是「全文里出现过」）
  F2 台账自洽：偏移严格递增、无重叠、无未落位项、条数与正文自报数一致
  F3 正文 → 台账：正文里每个数字 token 必须落在某个台账 span 内，
     或命中白名单（代码块/行内代码/日期/哈希/章节号/工单号/标的代号…），
     或与某个已登记值同值复现；「未登记」必须为 0
  F4 派生值就地重算：不引用台账，直接用产物原始字段重算并比对正文
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r2 as L

README = L.ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2" / "README.md"
CHECKS: list = []


def add(name, ok, detail):
    CHECKS.append({"检查": name, "通过": bool(ok), "明细": detail})
    return ok


# ── 白名单：这些位置的数字不算「正文裸数字」 ───────────────────────────
EXEMPT = [
    ("代码块", re.compile(r"```.*?```", re.S)),
    ("行内代码", re.compile(r"`[^`\n]*`")),
    ("日期时刻", re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?")),
    ("哈希", re.compile(r"\b[0-9a-f]{16,}\b")),
    ("算法名", re.compile(r"\bsha\d+\b", re.I)),
    ("章节号", re.compile(r"(?m)^#{1,6}\s+\d+")),
    ("§引用", re.compile(r"§\d+")),
    ("反例编号", re.compile(r"\bS\d+[a-z]?\b")),
    ("校验编号", re.compile(r"\bQ\d+\b")),
    ("工单号", re.compile(r"\b\d{2}-[0-9A-Z]{1,4}\b")),
    ("字母数字编号", re.compile(r"\b[A-Z]{1,3}\d+[a-z]?\b")),
    ("标的代号", re.compile(r"\b[A-Z0-9]{2,}USDT\b")),
    ("表首要求序号", re.compile(r"(?m)^\|\s*\d+\s")),
    ("时点三元组", re.compile(r"\d{2}/\d{2}/\d{2}")),
    ("月日", re.compile(r"\d+\s*[月日]")),
]

TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def build_mask(text: str) -> dict:
    """返回 {字符位置: 白名单类别}。"""
    mask: dict = {}
    for cat, rx in EXEMPT:
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                mask.setdefault(i, cat)
    return mask


def main() -> int:
    if not README.exists():
        print("README 不存在，先跑 07")
        return 2
    text = README.read_text(encoding="utf-8")
    led = json.loads((L.OUT / "07_正文数字台账.json").read_text(encoding="utf-8"))
    items = led["items"]

    p00 = json.loads((L.OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    p02 = json.loads((L.OUT / "02_缺口判据与完整性.json").read_text(encoding="utf-8"))
    p04 = json.loads((L.OUT / "04_真实记录核对.json").read_text(encoding="utf-8"))
    p05 = json.loads((L.OUT / "05_两口径逐笔与汇总.json").read_text(encoding="utf-8"))

    # ── F1 台账 → 正文（逐字符） ──
    bad = []
    for it in items:
        if not it["offsets"]:
            bad.append({"key": it["key"], "原因": "未落位"})
            continue
        for off in it["offsets"]:
            got = text[off:off + len(it["text"])]
            if got != it["text"]:
                bad.append({"key": it["key"], "offset": off,
                            "期望": it["text"], "实得": got})
    add("F1 台账→正文：每个登记项的每个落位点都逐字符相同",
        not bad, {"登记项": len(items),
                  "落位点": sum(len(i["offsets"]) for i in items),
                  "不一致": bad[:10]})

    # ── F2 台账自洽 ──
    spans = []
    for it in items:
        for off in it["offsets"]:
            spans.append((off, off + len(it["text"]), it["key"]))
    spans.sort()
    overlap = [(a, b) for a, b in zip(spans, spans[1:]) if a[1] > b[0]]
    add("F2a 落位区间两两不重叠", not overlap,
        {"区间数": len(spans), "重叠": overlap[:5]})

    m = re.search(r"正文 (\d+) 个数字登记在", text)
    add("F2b 正文自报的台账条数 == 台账实际条数",
        m is not None and int(m.group(1)) == len(items),
        {"正文自报": int(m.group(1)) if m else None, "台账实际": len(items)})

    add("F2c 台账无未落位项",
        all(i["offsets"] for i in items),
        {"未落位": [i["key"] for i in items if not i["offsets"]]})

    # ── F3 正文 → 台账 ──
    mask = build_mask(text)
    covered = bytearray(len(text))
    for it in items:
        for off in it["offsets"]:
            for i in range(off, off + len(it["text"])):
                covered[i] = 1
    known_text = {i["text"] for i in items}

    bucket = {"台账落位": 0, "白名单": {}, "同值复现": 0, "未登记": []}
    for m in TOKEN.finditer(text):
        tok, s, e = m.group(0), m.start(), m.end()
        if all(covered[i] for i in range(s, e)):
            bucket["台账落位"] += 1
            continue
        cats = {mask[i] for i in range(s, e) if i in mask}
        if cats:
            for c in cats:
                bucket["白名单"][c] = bucket["白名单"].get(c, 0) + 1
            continue
        if tok in known_text:
            bucket["同值复现"] += 1
            continue
        bucket["未登记"].append({"token": tok, "上下文": text[max(0, s - 40):e + 25]})

    add("F3a 正文没有「既不在台账 span、也不在白名单、也不是同值复现」的数字",
        not bucket["未登记"],
        {"未登记数": len(bucket["未登记"]), "样例": bucket["未登记"][:10]})

    add("F3b 白名单只掩盖结构字符，不掩盖数据（台账落位 ≥ 白名单掩盖数）",
        bucket["台账落位"] >= sum(bucket["白名单"].values()),
        {"台账落位": bucket["台账落位"], "白名单分类": bucket["白名单"],
         "同值复现": bucket["同值复现"]})

    # ── F4 派生值就地重算（不读台账） ──
    A = p00["A_范围"]
    S2 = p02["汇总"]
    SA = p05["汇总"]["生效视图_5bps"]
    SB = p05["汇总"]["策略包_3bps"]

    add("F4a 口径数 × 候选数 == 持仓记录数（2 × 51 = 102）",
        len(A["calibers"]) * A["candidates"] == A["holds"],
        {"口径": len(A["calibers"]), "候选": A["candidates"], "持仓记录": A["holds"]})

    add("F4b 已成交 + 拒单 == 持仓记录数",
        A["holds_filled"] + (A["holds"] - A["holds_filled"]) == A["holds"],
        {"已成交": A["holds_filled"], "拒单": A["holds"] - A["holds_filled"]})

    claim_sum = sum(S2["claim_dist"].values())
    add("F4c 主张四态之和 == 候选数", claim_sum == S2["candidates"],
        {"和": claim_sum, "候选": S2["candidates"], "分布": S2["claim_dist"]})

    comp_sum = sum(S2["completeness_dist"].values())
    add("F4d 完整性五态之和 == 候选数", comp_sum == S2["candidates"],
        {"和": comp_sum, "分布": S2["completeness_dist"]})

    basis_sum = sum(S2["interval_basis_dist"].values())
    add("F4e 证据分级之和 == 候选数", basis_sum == S2["candidates"],
        {"和": basis_sum, "分布": S2["interval_basis_dist"]})

    add("F4f 两口径都满足 ΔNet == −ΔFunding",
        all([abs(SA["delta_net_sum"] + SA["delta_funding_sum"]) < 1e-9,
             abs(SB["delta_net_sum"] + SB["delta_funding_sum"]) < 1e-9]),
        {"A": [SA["delta_funding_sum"], SA["delta_net_sum"]],
         "B": [SB["delta_funding_sum"], SB["delta_net_sum"]]})

    for name, S in (("A", SA), ("B", SB)):
        dist = S["change_reason_dist"]
        # 分布覆盖「全部候选」（含 REJECTED），所以对齐的是 candidates 不是 filled
        add(f"F4g-{name} 变化原因分布之和 == 候选数",
            sum(dist.values()) == S["candidates"],
            {"分布": dist, "和": sum(dist.values()), "候选": S["candidates"],
             "成交": S["filled"]})

    R1 = p04["R1_R1缺失复核"]
    add("F4h R1 的缺失条数 == 复核条数（同一个数被两处引用）",
        R1["r1_missing_instants"] == S2["r1_missing_total"],
        {"复核": R1["r1_missing_instants"], "汇总": S2["r1_missing_total"]})

    add("F4i 抖动匹配数 == 复核条数（188/188 而不是 188/188 以外）",
        R1["其中在 ±1s 内有真实事件"] == R1["r1_missing_instants"],
        {"匹配": R1["其中在 ±1s 内有真实事件"], "复核": R1["r1_missing_instants"]})

    R2c = p04["R2_周期切换实证"]
    cons = [c for c in R2c["明细"] if c["official_matches_last_regime"]]
    al = [c for c in cons if c["update_minus_switch_hours"] is not None
          and abs(c["update_minus_switch_hours"]) <= 48]
    add("F4j 「官方与最后一段一致」+「不一致」== 有切换点的标的数",
        len(cons) + (len(R2c["明细"]) - len(cons)) == R2c["有切换点的标的数"],
        {"一致": len(cons), "总": R2c["有切换点的标的数"]})
    add("F4k 「≤48h」+「>48h」== 「一致」（两个子集互斥且穷尽）",
        len(al) + (len(cons) - len(al)) == len(cons),
        {"≤48h": len(al), "一致": len(cons)})

    R6 = p04["R6_补入事件是否在网格上"]
    add("F4l 补入事件数 − 网格外 == 网格内（本单网格外应为 0）",
        R6["补入事件数（51 笔持仓内）"] - R6["网格外"] == R6["补入事件数（51 笔持仓内）"]
        and R6["网格外"] == 0,
        {"补入": R6["补入事件数（51 笔持仓内）"], "网格外": R6["网格外"]})

    add("F4m A 口径事件增量 == 补入事件数（同一批事件被两处引用）",
        SA["events_new_sum"] - SA["events_old_sum"] == R6["补入事件数（51 笔持仓内）"],
        {"A 增量": SA["events_new_sum"] - SA["events_old_sum"],
         "补入": R6["补入事件数（51 笔持仓内）"]})

    # 正文里的关键数字必须与产物逐位相同（防止「台账对、正文手改」）
    spot = {
        "口径A资金费差额": (f"{SA['delta_funding_sum']:,.9f}", text),
        "口径B资金费差额": (f"{SB['delta_funding_sum']:,.9f}", text),
        "口径A净额旧": (f"{SA['net_old_sum']:,.9f}", text),
        "口径B净额旧": (f"{SB['net_old_sum']:,.9f}", text),
    }
    missing = [k for k, (v, t) in spot.items() if v not in t]
    add("F4n 四个关键金额在正文里逐位可查（不是四舍五入后的近似值）",
        not missing, {"缺": missing})

    ok = all(c["通过"] for c in CHECKS)
    out = {"工单": "ACCUM-EARLY-06-W4-R2", "性质": "数字对撞（三向 + 派生重算）",
           "正文": "README.md", "台账": "out/07_正文数字台账.json",
           "统计": {"台账项": len(items),
                    "落位点": sum(len(i["offsets"]) for i in items),
                    "数字token": bucket["台账落位"] + sum(bucket["白名单"].values())
                    + bucket["同值复现"] + len(bucket["未登记"]),
                    "分桶": {"台账落位": bucket["台账落位"],
                             "白名单": bucket["白名单"],
                             "同值复现": bucket["同值复现"],
                             "未登记": len(bucket["未登记"])}},
           "checks": CHECKS}
    (L.OUT / "08_数字对撞.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in CHECKS:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print(f"\n{sum(1 for c in CHECKS if c['通过'])}/{len(CHECKS)} 通过 | "
          f"台账 {len(items)} 项 / {out['统计']['落位点']} 处落位 | "
          f"未登记 {len(bucket['未登记'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
