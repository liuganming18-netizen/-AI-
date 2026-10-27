# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜09 数字双向对撞（正文 ↔ 产物）。

为什么必须有这一步
------------------
本单的正文里全是数字（成交笔数、R 合计、复现率、案例收益）。这些数字有两条失真的路：
  1. 手抄错（把 −192.78 写成 −129.78）；
  2. 产物重跑后正文没跟着更新（正文留着旧值）。
人眼查不出来，尤其数字多的时候。

做法（两个方向都查）
--------------------
A 产物 → 正文：每个**关键声明**的数值，必须在指定正文里以某种合理格式出现过；
B 正文 → 产物：正文里出现的每个数字，必须能在某个产物里找到同值
  （先剔除时间戳、日期、版本号、文件名等非数值噪声）。

B 方向会报出「正文写了但产物里没有」的数——这正是抄错或过期的信号。

输出：out/09_数字对撞.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BASE = HERE.parents[1]
OUT = BASE / "out"

MD_FILES = ["02_规则接口.md", "03_决策表.md", "04_验证区间方案.md"]
CASE_DIR = BASE / "cases"

# 非数值噪声：ISO 时刻、纯日期、版本串、路径、形如 xxx.json 的文件名
NOISE = [
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"ACCUM-EARLY-[A-Z0-9\-]+"),
    re.compile(r"v\d+"),
    re.compile(r"[\w./\\-]+\.(json|md|py|sqlite3|parquet)"),
    re.compile(r"`[^`]*`"),          # 反引号里的标识符不算
    re.compile(r"\d+\s*ms"),         # 单位换算常数（如 300000ms），不是研究结论
]


def load_json(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def leaves(obj, path="$"):
    """遍历 JSON，产出 (路径, 数值)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{path}[{i}]")
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        yield path, obj


def fmt_variants(v) -> set:
    """一个数值在正文里可能出现的写法。"""
    out = set()
    if isinstance(v, int):
        out.add(str(v))
        out.add(f"{v:,}")
    else:
        for nd in (0, 1, 2, 3, 4, 6):
            out.add(f"{v:.{nd}f}")
        out.add(f"{v:,.0f}")
        out.add(f"{v:,.2f}")
        out.add(f"{v:.2%}")
        out.add(f"{v:.1%}")
        if abs(v) >= 1:
            out.add(f"{v:.3f}")
    return {x for x in out if x not in ("", "-0", "-0.0", "-0.00")}


def build_value_set():
    """所有产物的数值 → 可匹配写法集合（附带来源，便于定位）。"""
    idx = {}
    files = sorted(OUT.glob("*.json"))
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            continue
        for path, v in leaves(d):
            for s in fmt_variants(v):
                idx.setdefault(s, []).append(f"{f.name}:{path}")
    return idx, [f.name for f in files]


NUM_RE = re.compile(r"(?<![\d.])(-?\d[\d,]*(?:\.\d+)?)")


MINUS = {"\u2212": "-", "\u2013": "-", "\uff0d": "-", "\u2014": "-"}


def norm(text: str) -> str:
    """正文里为了排版常用 U+2212「−」写负数，产物里是 ASCII「-」。
    不归一化就会出现「明明写了却匹配不上」的假失败，或反过来「用别的写法蒙过去」的假通过。
    """
    for k, v in MINUS.items():
        text = text.replace(k, v)
    return text


def numbers_in(text: str):
    """正文里出现的数字（已剔除噪声）。"""
    text = norm(text)
    for pat in NOISE:
        text = pat.sub(" ", text)
    out = []
    for m in NUM_RE.finditer(text):
        s = m.group(1)
        out.append((s, text[max(0, m.start() - 40):m.end() + 20].replace("\n", " ")))
    return out


def main() -> int:
    idx, files = build_value_set()
    rep = {"probe": "09_数字对撞", "generated_at_utc": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "产物文件数": len(files), "可匹配数值写法数": len(idx),
        "A_产物到正文": [], "B_正文到产物": {}, "结论": {}}

    # ── A：关键声明必须出现在正文 ──
    res = load_json("01_共同观察范围_research_final.json")
    hol = load_json("01_共同观察范围_holdout_final.json")
    c_res = load_json("07_仓位约束对照_research_final.json")
    c_hol = load_json("07_仓位约束对照_holdout_final.json")
    cmp4 = load_json("04_三组对照.json")
    chk2 = load_json("02_逐位核对.json")

    claims = []

    def add(label, value, md_targets, src):
        if value is None:
            return
        claims.append({"声明": label, "值": value, "正文": md_targets, "来源": src})

    for key, label in (("baseline", "基线"), ("4a", "甲"), ("4b", "乙"), ("4c", "丙")):
        if res:
            t = res["aggregate"]["schemes"][key]["trade_summary"]
            add(f"研究区间 {label} 成交", t["成交"],
                ["03_决策表.md", "04_验证区间方案.md"] if key != "baseline"
                else ["04_验证区间方案.md"],
                f"01_..._research_final.json: schemes.{key}.trade_summary.成交")
            add(f"研究区间 {label} R合计", t["R合计"],
                ["03_决策表.md", "04_验证区间方案.md"],
                f"01_..._research_final.json: schemes.{key}.trade_summary.R合计")
        if hol:
            t = hol["aggregate"]["schemes"][key]["trade_summary"]
            add(f"验证区间 {label} 成交", t["成交"], ["04_验证区间方案.md"],
                f"01_..._holdout_final.json: schemes.{key}.trade_summary.成交")
            add(f"验证区间 {label} R合计", t["R合计"], ["04_验证区间方案.md"],
                f"01_..._holdout_final.json: schemes.{key}.trade_summary.R合计")
        if c_res:
            t = c_res["schemes"][key]["3仓约束下可执行"]
            add(f"研究区间 3仓 {label} R合计", t["R合计"], ["04_验证区间方案.md"],
                f"07_..._research_final.json: schemes.{key}.3仓约束下可执行.R合计")
        if c_hol:
            t = c_hol["schemes"][key]["3仓约束下可执行"]
            add(f"验证区间 3仓 {label} R合计", t["R合计"], ["04_验证区间方案.md"],
                f"07_..._holdout_final.json: schemes.{key}.3仓约束下可执行.R合计")

    if cmp4:
        w = cmp4["windows"].get("research_final") or cmp4["windows"].get("research") or {}
        if w:
            add("原策略候选数", w["baseline"]["candidates"], ["03_决策表.md", "04_验证区间方案.md"],
                "04_三组对照.json: baseline.candidates")
            add("三组都没覆盖的候选", w["baseline"]["三组都没覆盖的候选"],
                ["03_决策表.md", "04_验证区间方案.md"],
                "04_三组对照.json: baseline.三组都没覆盖的候选")
            for key, label in (("4a", "甲"), ("4b", "乙"), ("4c", "丙")):
                s = w["schemes"][key]
                add(f"{label} 重叠笔 R合计", s["重叠笔_结果"]["R合计"],
                    ["03_决策表.md", "04_验证区间方案.md"],
                    f"04_三组对照.json: schemes.{key}.重叠笔_结果.R合计")
                add(f"{label} 新增机会 R合计", s["新增机会_结果"]["R合计"],
                    ["03_决策表.md", "04_验证区间方案.md"],
                    f"04_三组对照.json: schemes.{key}.新增机会_结果.R合计")

    if chk2:
        add("51 候选复现率", chk2["D_51候选"]["复现率"], ["03_决策表.md"],
            "02_逐位核对.json: D_51候选.复现率")
        add("入场模型四项全过", chk2["E_成交模型"]["四项全过"], ["03_决策表.md"],
            "02_逐位核对.json: E_成交模型.四项全过")

    texts = {}
    for f in MD_FILES + ["README.md"]:
        p = BASE / f
        texts[f] = norm(p.read_text(encoding="utf-8")) if p.exists() else ""
    for p in sorted(CASE_DIR.glob("*.md")):
        texts[f"cases/{p.name}"] = p.read_text(encoding="utf-8")

    for c in claims:
        c["正文"] = list(c["正文"]) + ["README.md"]   # 验收报告也要核，但不参与 B 方向
        variants = fmt_variants(c["值"])
        hit_in = [t for t in c["正文"] if any(v in texts.get(t, "") for v in variants)]
        missing = [t for t in c["正文"] if t not in hit_in]
        rep["A_产物到正文"].append({
            "声明": c["声明"], "值": c["值"], "来源": c["来源"],
            "应出现在": c["正文"], "实际出现在": hit_in, "缺失于": missing,
            "结果": "通过" if not missing else "**未找到**",
        })

    # ── B：正文里每个数字都要能在产物里找到 ──
    for f, text in texts.items():
        miss = []
        for s, ctx in numbers_in(text):
            if s in idx:
                continue
            # 允许千分位/末尾零的等价写法
            try:
                v = float(s.replace(",", ""))
            except ValueError:
                miss.append({"数": s, "上下文": ctx})
                continue
            if any(x in idx for x in fmt_variants(v)):
                continue
            miss.append({"数": s, "上下文": ctx})
        rep["B_正文到产物"][f] = {
            "数字个数": len(numbers_in(text)),
            "找不到来源": len(miss),
            "明细": miss[:40],
        }

    a_fail = [x for x in rep["A_产物到正文"] if x["结果"] != "通过"]
    b_fail = {f: v["找不到来源"] for f, v in rep["B_正文到产物"].items() if v["找不到来源"]}
    rep["结论"] = {
        "A 声明总数": len(rep["A_产物到正文"]),
        "A 未找到": len(a_fail),
        "B 有未匹配数字的文件": b_fail,
        "判定": ("通过" if not a_fail and not b_fail else "不通过（见明细）"),
    }

    (OUT / "09_数字对撞.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(f'A 声明 {len(rep["A_产物到正文"])} 条，未找到 {len(a_fail)} 条')
    for x in a_fail:
        print("  缺:", x["声明"], "=", x["值"], "应出现在", x["应出现在"])
    print("B 逐文件未匹配：")
    for f, v in rep["B_正文到产物"].items():
        print(f'  {f:28s} 数字 {v["数字个数"]:4d}  未匹配 {v["找不到来源"]:3d}')
    print("判定:", rep["结论"]["判定"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
