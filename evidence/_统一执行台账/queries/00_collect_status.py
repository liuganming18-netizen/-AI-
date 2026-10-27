#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一执行台账：从既有工单目录的报告原文中摘录"已写明的结论状态"。

只读，不做重新判定；状态分类只依据原文措辞，且**默认不可跳过复核**。
规则：只有原文明确写"独立验收通过/复核通过"且全文无待验收、无失败字样，才标可跳过。

输出：evidence/_统一执行台账/out/01_状态摘录.csv、02_待决策与未解决摘录.csv
"""
import csv
import glob
import os
import re

BASE = "/Users/boos/Desktop/威科夫因子共振趋势/evidence"
OUT = os.path.join(BASE, "_统一执行台账", "out")

STATUS_KW = re.compile(
    r"(PASS|FAIL|通过|不通过|未通过|待审|待复核|待验收|待决策|未解决|已验收|独立验收|"
    r"整改|结论|状态[:：]|资格|不可复现|NOT_TESTED|UNPROVEN|FORBIDDEN|停工)", re.I)
DECISION_KW = re.compile(
    r"(待决策|待用户|未解决|待核|待确认|待审|待验收|阻塞|前置缺口|不可复现|NOT_TESTED|UNPROVEN)", re.I)

PENDING_ACCEPT = re.compile(r"(待\s*Codex|等待\s*Codex|待独立验收|待验收|待复核|待审)", re.I)
FAILED = re.compile(r"(FAIL|失败|不通过|未通过|退回)", re.I)
ACCEPTED = re.compile(r"(独立验收通过|复核结论[:：]?\s*通过|验收结论[:：]?\s*通过|验收通过[。\n]|全部通过[。\n])", re.I)


def head_lines(path, n=80):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                out.append(line.rstrip("\n"))
    except Exception as e:
        out.append(f"[读取失败] {e}")
    return out


def pick(rel_dir):
    md = sorted(glob.glob(os.path.join(BASE, rel_dir, "*.md")))
    if not md:
        # 退一层找子目录里的报告
        md = sorted(glob.glob(os.path.join(BASE, rel_dir, "*", "*.md")))
    if not md:
        return []
    prio = []
    for p in md:
        b = os.path.basename(p)
        score = 0
        if "报告" in b:
            score += 3
        if "结论" in b or "收口" in b:
            score += 3
        if "验收" in b:
            score += 2
        if b.startswith("README"):
            score += 1
        prio.append((score, p))
    prio.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in prio[:3]]


def classify(joined):
    """只依据原文措辞分类；拿不准一律归'未明确'。"""
    if PENDING_ACCEPT.search(joined):
        return "待独立验收", "否"
    if FAILED.search(joined):
        return "存在失败/退回项", "否"
    if ACCEPTED.search(joined) and not PENDING_ACCEPT.search(joined):
        return "原文写明验收通过", "是"
    if re.search(r"(自检|自查).{0,6}(通过|全通过)", joined):
        return "仅自检通过", "否"
    if re.search(r"(PASS|通过)", joined):
        return "含通过字样但未写明独立验收", "否"
    return "未明确", "否"


def main():
    os.makedirs(OUT, exist_ok=True)
    dirs = sorted(d for d in os.listdir(BASE)
                  if os.path.isdir(os.path.join(BASE, d)) and not d.startswith("_"))
    status_rows, decision_rows = [], []
    for d in dirs:
        files = pick(d)
        if not files:
            status_rows.append({"工单目录": d, "状态分类": "无报告文件",
                                "结论状态（报告原文摘录）": "顶层与一层子目录均无 .md",
                                "来源文件": "", "是否可跳过复核": "否"})
            continue
        hits = []
        for p in files:
            for ln in head_lines(p):
                s = ln.strip()
                if not s or len(s) < 6:
                    continue
                if STATUS_KW.search(s):
                    hits.append((os.path.relpath(p, BASE), s[:200]))
                if DECISION_KW.search(s):
                    decision_rows.append({"工单目录": d, "来源文件": os.path.relpath(p, BASE),
                                          "摘录": s[:200]})
        joined = " ".join(t for _, t in hits)
        cls, skip = classify(joined)
        brief = " ⏎ ".join(f"[{os.path.basename(f)}] {t}" for f, t in hits[:3]) if hits else "未在前 80 行找到状态声明"
        status_rows.append({"工单目录": d, "状态分类": cls, "结论状态（报告原文摘录）": brief,
                            "来源文件": os.path.relpath(files[0], BASE), "是否可跳过复核": skip})

    with open(os.path.join(OUT, "01_状态摘录.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["工单目录", "状态分类", "结论状态（报告原文摘录）", "来源文件", "是否可跳过复核"])
        w.writeheader()
        for r in status_rows:
            w.writerow(r)

    seen, uniq, per_dir = set(), [], {}
    for r in decision_rows:
        key = (r["工单目录"], r["摘录"])
        if key in seen:
            continue
        seen.add(key)
        per_dir[r["工单目录"]] = per_dir.get(r["工单目录"], 0) + 1
        if per_dir[r["工单目录"]] <= 4:
            uniq.append(r)
    with open(os.path.join(OUT, "02_待决策与未解决摘录.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["工单目录", "来源文件", "摘录"])
        w.writeheader()
        for r in uniq:
            w.writerow(r)

    from collections import Counter
    c = Counter(r["状态分类"] for r in status_rows)
    print("目录", len(dirs), "个；状态分类：", dict(c))
    print("可跳过复核：", [r["工单目录"] for r in status_rows if r["是否可跳过复核"] == "是"] or "无")
    print("待决策/未解决摘录", len(uniq), "条")


if __name__ == "__main__":
    main()
