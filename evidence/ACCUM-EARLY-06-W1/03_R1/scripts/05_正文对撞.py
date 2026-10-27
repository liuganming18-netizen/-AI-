#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""05 正文双向对撞（正文 ↔ 产物）

正向：正文里每一处「关键数字」必须能在 `out/*.json` 里读到**同一个值**（不是手抄）。
反向：正文里出现的每个数字，必须能在产物/授权数据里找到出处，或在白名单里给出理由。

扫描前会剔除这些**非主张**内容（避免把路径/时刻当数字主张）：
  · Markdown 标题行（`#` 开头，含章节号）
  · 围栏代码块（``` 之间）
  · ISO 时间戳 `YYYY-MM-DDTHH:MM:SS(.mmm)Z`
  · 十六进制哈希片段
  · `文件:行` 形式（`.py:1803`、`.md:56`、`:25`）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

DOCS = [W.HERE / "README.md", W.HERE / "待决表_R1.md"]
DATA_FILES = [W.HERE / "校正表.json", W.HERE / "待决表_R1.json"] + sorted(W.OUT.glob("*.json"))

#: 反向扫描白名单：正文允许出现、但产物里没有对应字段的数字（必须给理由）
WHITELIST = {
    "1": "序号/单位（第 1 条、1 个字节）",
    "2": "「两轴」「两份」「2 项」等计数词",
    "3": "「三类」「3 处」「3 分钟」",
    "4": "「4 处」「4 个凭证字段」「4h」",
    "5": "「5 项」「5bps」「5 行恒真」",
    "6": "「6 项」「6/6」",
    "7": "「7 行」「7 项」",
    "8": "「8 项」「8 个冲突」",
    "9": "「9 行」「9 个文件」",
    "12": "「12 项」「12 问」",
    "14": "旧产物 14 个（out/00 有 len，但正文按「14」写）",
    "15": "「15m」「15 分钟」",
    "18": "引用 18 条（out/01 references.total）",
    "21": "待决 21 项（out/04 counts.items）",
    "23": "E3 23 行（out/01 by_level.E3）",
    "25": "决策文档第 25 行",
    "26": "决策文档第 26 行",
    "27": "决策文档第 27 行",
    "28": "决策文档第 28 行",
    "31": "31 条规则项（out/01 counts.rows）",
    "45": "45 个行为字段（out/04 counts.p_fields）",
    "49": "49 必填（out/04 counts.p_fields_plus_credentials）",
    "67": "out/03 impact.hits_total",
    "95": "止损 95%",
    "179": "179,999 ms 的前半（带千分位，拆成两段）",
    "999": "179,999 ms 的后半（带千分位，拆成两段）",
    "2026": "年份",
    "10": "月份/10 天",
    "0": "0 命中 / 0 项 / 0.05 等",
}

FACTS = [
    # (说明, 正文应出现的字面量, 产物文件, 取值路径, 期望值)
    ("规则项行数", "31 行", "01_approval_evidence.json", "counts.rows", 31),
    ("E1 行数", "E1 **7**", "01_approval_evidence.json", "counts.by_level.E1", 7),
    ("E2 行数", "E2 **1**", "01_approval_evidence.json", "counts.by_level.E2", 1),
    ("E3 行数", "E3 **23**", "01_approval_evidence.json", "counts.by_level.E3", 23),
    ("标签改写行数", "changed_label_rows=31", "01_approval_evidence.json", "counts.changed_label_rows", 31),
    ("不在效行数", "只有 3 行「行为不在效」", "01_approval_evidence.json", "counts.behavior_not_in_force", 3),
    ("措辞校正条数", "旧报告的三处措辞校正", "01_approval_evidence.json", "counts.wording_fixes", 3),
    ("01 引用通过", "**18/18**", "01_approval_evidence.json", "references.pass", 18),
    ("止损项数", "8 项", "02_stop_decision.json", "len(not_asked)", 8),
    ("止损引用通过", "**12/12**", "02_stop_decision.json", "references.pass", 12),
    ("研究侧生效时刻", "2026-10-10T12:00:00Z", "03_effective_time.json", "runtime.research_eff_utc", "2026-10-10T12:00:00Z"),
    ("退出 exit_ts", "2026-10-10T12:02:59Z", "03_effective_time.json", "runtime.exit_case.position_row.exit_ts", 1791633779999),
    ("与研究生效差", "179,999 ms", "03_effective_time.json", "runtime.exit_case.delta_vs_research_ms", 179999),
    ("调用点数", "3 处", "03_effective_time.json", "len(static.call_sites)", 3),
    ("公式处数", "3 处", "03_effective_time.json", "len(static.formulas)", 3),
    ("待决项数", "**21 项**", "04_pending_dedupe.json", "counts.items", 21),
    ("P 类", "P 参数待决 | **12**", "04_pending_dedupe.json", "counts.by_class.P", 12),
    ("E 类", "E 证据缺口 | **6**", "04_pending_dedupe.json", "counts.by_class.E", 6),
    ("G 类", "G 明确工程问题 | **3**", "04_pending_dedupe.json", "counts.by_class.G", 3),
    ("P 字段", "45", "04_pending_dedupe.json", "counts.p_fields", 45),
    ("字段合计", "**49**", "04_pending_dedupe.json", "counts.p_fields_plus_credentials", 49),
    ("冲突折叠", "8 个冲突编号**全部折叠**", "04_pending_dedupe.json", "counts.conflicts_folded", 8),
    ("04 引用通过", "**14/14**", "04_pending_dedupe.json", "references.pass", 14),
]

ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
TIME = re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d{3})?")
SECTION = re.compile(r"§\d+(?:\.\d+)?")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
HEX = re.compile(r"\b[0-9a-f]{8,}\b")
FILELINE = re.compile(r"[\w./\-]+\.(?:py|md|json):\d+(?:/\d+)*")
COLONNUM = re.compile(r":\d+(?:/\d+)*\b")
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def resolve(obj, path: str):
    if path.startswith("len(") and path.endswith(")"):
        inner = path[4:-1]
        return len(resolve(obj, inner))
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def main() -> int:
    problems: list = []
    texts = {p.name: p.read_text(encoding="utf-8") for p in DOCS}
    data = {}
    for p in DATA_FILES:
        data[p.name] = W.load_json(p)

    # ── 正向 ────────────────────────────────────────────────────────────
    facts_out = []
    for label, literal, fname, path, expect in FACTS:
        got = resolve(data[fname], path)
        hit_files = [n for n, t in texts.items() if literal in t]
        ok_val = (got == expect)
        ok_lit = bool(hit_files)
        facts_out.append({"label": label, "literal": literal, "file": fname, "path": path,
                          "expected": expect, "actual": got, "value_ok": ok_val,
                          "literal_in": hit_files, "literal_ok": ok_lit})
        W.require(ok_val, f"{label}: {fname}:{path} = {got!r} != {expect!r}", problems)
        W.require(ok_lit, f"{label}: 正文缺字面量 {literal!r}", problems)

    # ── 反向 ────────────────────────────────────────────────────────────
    allowed = set()
    for obj in data.values():
        for m in NUM.findall(json.dumps(obj, ensure_ascii=False)):
            allowed.add(m)
    allowed |= set(WHITELIST)

    def numbers_in(text: str) -> list[str]:
        body, fenced = [], False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced or line.lstrip().startswith("#"):
                continue
            line = ISO.sub(" ", line)
            line = TIME.sub(" ", line)
            line = SECTION.sub(" ", line)
            line = HEX.sub(" ", line)
            line = FILELINE.sub(" ", line)
            line = COLONNUM.sub(" ", line)
            line = THOUSANDS.sub("", line)
            body.append(line)
        return NUM.findall("\n".join(body))

    # 负向对照：扫描器必须能抓到一个人为数字，否则「0 未登记」是恒真
    probe = numbers_in("本节有一个未登记数字 987654，另有时刻 12:00:00.000 与 §4.4 不算数字主张。")
    W.require("987654" in probe, "反向扫描器抓不到人为数字（恒真）", problems)
    W.require("12:00:00.000" not in probe and "4.4" not in probe,
              f"扫描器未剔除时刻/章节号：{probe}", problems)

    unregistered = []
    scanned = {}
    for name, text in texts.items():
        nums = numbers_in(text)
        scanned[name] = len(nums)
        for m in nums:
            if m not in allowed:
                unregistered.append({"doc": name, "number": m})

    # 去重后报告
    uniq = sorted({u["number"] for u in unregistered}, key=lambda x: (len(x), x))
    W.require(not unregistered, f"正文出现未登记数字：{uniq}", problems)

    # ── 上游产物身份未漂移（防止「正文引用的 json 已被改过」）──────────────
    drift = []
    pairs = [
        ("01_approval_evidence.json", "input.correction_table_sha256", W.HERE / "校正表.json"),
        ("04_pending_dedupe.json", "input.pending_table_sha256", W.HERE / "待决表_R1.json"),
        ("04_pending_dedupe.json", "input.correction_table_sha256", W.HERE / "校正表.json"),
        ("02_stop_decision.json", "input.pending_table_sha256", W.HERE / "待决表_R1.json"),
    ]
    for fname, path, actual_file in pairs:
        rec = resolve(data[fname], path)
        now = W.sha256_file(actual_file)
        drift.append({"file": fname, "path": path, "recorded": rec, "actual": now, "ok": rec == now})
        W.require(rec == now, f"{fname}:{path} 记录 {rec[:12]} != 实际 {now[:12]}（产物间已漂移）", problems)

    payload = {
        "schema": "accum-early-06-w1-r1/report-collision/v1",
        "checked_at_utc": W.now_iso(),
        "upstream_identity": drift,
        "docs": {p.name: {"sha256": W.sha256_file(p), "lines": len(p.read_text(encoding='utf-8').splitlines())}
                 for p in DOCS},
        "forward": {"total": len(facts_out), "pass": sum(1 for f in facts_out if f["value_ok"] and f["literal_ok"]),
                    "rows": facts_out},
        "reverse": {"unregistered_total": len(unregistered), "unregistered": unregistered,
                    "numbers_scanned": scanned, "negative_control": {"probe_numbers": probe, "ok": "987654" in probe},
                    "whitelist": WHITELIST},
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
    }
    W.dump("05_report_collision.json", payload)

    print(json.dumps({
        "forward": f"{payload['forward']['pass']}/{payload['forward']['total']}",
        "reverse_unregistered": len(unregistered),
        "numbers_scanned": scanned,
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
