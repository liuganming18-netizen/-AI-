#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06-02 计数对账：把「49 / 48 / 13 / 21 / 43 / 5」逐个数从源里算出来。

不手抄任何计数：
  · 49 / 13 / 21 ← 直接 import 生产模块读 REQUIRED_PATHS / PENDING_TO_CONFIG
  · 48           ← 读空白表单，数 null 叶子
  · 43（8/9/23/3）← 解析 04-A/02_规则接口.md 的 6.一～6.四 四张表
  · 49（4/30/6/9）← 解析 05-A-R1/03_完整方案参数附件表.md 的 49 行状态列
  · 5            ← 解析 05-A-R1/02_简明决策表.md 的「决策 N」行

只读。输出 out/00_计数对账.json。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "out" / "00_计数对账.json"

sys.path.insert(0, str(ROOT / "框架/backend"))
import accum_early_entry as A  # noqa: E402


def leaves(d, pre=""):
    out = []
    for k, v in d.items():
        p = f"{pre}.{k}" if pre else k
        if isinstance(v, dict):
            out += leaves(v, p)
        else:
            out.append((p, v))
    return out


def parse_04a():
    md = (ROOT / "evidence/ACCUM-EARLY-04-A/02_规则接口.md").read_text(encoding="utf-8")
    lines = md.splitlines()
    sections = {"6.一": [], "6.二": [], "6.三": [], "6.四": []}
    cur = None
    for ln in lines:
        m = re.match(r"^### (6\.[一二三四])", ln)
        if m:
            cur = m.group(1)
            continue
        if cur and ln.startswith("| `"):
            sections[cur].append(ln)
        if cur and ln.startswith("## "):
            cur = None
    return {k: len(v) for k, v in sections.items()}


def parse_05a_r1():
    md = (ROOT / "evidence/ACCUM-EARLY-05-A-R1/03_完整方案参数附件表.md").read_text(encoding="utf-8")
    rows = []
    for ln in md.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|(.*)$", ln)
        if m:
            rows.append({"no": int(m.group(1)), "path": m.group(2), "rest": m.group(3)})
    buckets = {"用户已定": [], "建议未批准": [], "待确认": [], "建议沿用": [], "格式/身份": [], "未分类": []}
    detail = []
    for r in rows:
        cell = r["rest"].split("|")[1] if r["rest"].count("|") >= 1 else r["rest"]
        status = "未分类"
        if "用户已定" in cell:
            status = "用户已定"
        elif "建议未批准" in cell:
            status = "建议未批准"
        elif "待确认" in cell:
            status = "待确认"
        elif "建议沿用" in cell:
            status = "建议沿用"
        elif "格式字段" in cell or "身份字段" in cell:
            status = "格式/身份"
        buckets[status].append(r["path"])
        detail.append({"no": r["no"], "path": r["path"], "status": status,
                       "cell": cell.strip()})
    # 总览（文件开头四条）自报的计数
    overview = {}
    for label, key in (("用户已定", "用户已定"), ("建议未批准", "建议未批准"),
                       ("待确认", "待确认"), ("格式", "格式/身份")):
        m = re.search(r"^- \*\*" + label + r"[^：]*\*\*：(\d+) 项", md, flags=re.M)
        overview[key] = int(m.group(1)) if m else None
    rows_count = {k: len(v) for k, v in buckets.items()}
    mismatch = {k: {"overview": overview.get(k), "rows": rows_count.get(k)}
                for k in overview if overview.get(k) != rows_count.get(k)}
    return {"rows": len(rows), "buckets": {k: {"n": len(v), "paths": v} for k, v in buckets.items()},
            "rows_count": rows_count, "overview": overview, "mismatch": mismatch,
            "detail": detail}


def parse_5decisions():
    md = (ROOT / "evidence/ACCUM-EARLY-05-A-R1/02_简明决策表.md").read_text(encoding="utf-8")
    ids = re.findall(r"^\|\s*\*\*决策\s*(\d)\*\*", md, flags=re.M)
    return {"count": len(ids), "ids": ids}


def main() -> int:
    required = list(A.REQUIRED_PATHS)
    pending_map = {k: list(v) for k, v in A.PENDING_TO_CONFIG.items()}
    pending_fields = sorted({f for v in pending_map.values() for f in v})
    conflicts = list(A.CONFLICT_FIELDS)
    meta = ["schema_version", "kind", "approved", "approval_ref"]

    other = [p for p in required
             if p not in meta and p not in pending_fields and p not in conflicts]

    blank = json.loads((ROOT / "evidence/ACCUM-EARLY-05-B/config/批准配置_空白表单_05.json")
                       .read_text(encoding="utf-8"))
    blank_leaves = leaves(blank)
    blank_null = [p for p, v in blank_leaves if v is None]
    blank_prefilled = [{"path": p, "value": v} for p, v in blank_leaves if v is not None]

    fixture = json.loads((ROOT / "evidence/ACCUM-EARLY-05-B/config/fixture_rules_05.json")
                         .read_text(encoding="utf-8"))
    fixture_leaves = leaves(fixture)
    notes = [p for p, _ in fixture_leaves if p.startswith("_fixture_notes")]

    payload = {
        "schema": "accum-early-06-w1/decision-inventory/v1",
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "required_paths": {
            "total": len(required),
            "meta": meta,
            "pending_mapped": pending_fields,
            "conflict_fields": conflicts,
            "conflict_only": [c for c in conflicts if c not in pending_fields],
            "other_behavior": other,
            "counts": {
                "total": len(required),
                "meta": len(meta),
                "pending_mapped": len(pending_fields),
                "conflict_only": len([c for c in conflicts if c not in pending_fields]),
                "other_behavior": len(other),
            },
            "decomposition_ok": (len(meta) + len(pending_fields)
                                 + len([c for c in conflicts if c not in pending_fields])
                                 + len(other)) == len(required),
        },
        "pending_decisions": {
            "count": len(pending_map),
            "fields": len(pending_fields),
            "map": pending_map,
        },
        "blank_form": {
            "leaves": len(blank_leaves),
            "null": len(blank_null),
            "prefilled": blank_prefilled,
        },
        "fixture": {"leaves": len(fixture_leaves), "notes": len(notes),
                    "rule_fields": len(fixture_leaves) - len(notes)},
        "doc_04a": parse_04a(),
        "doc_05a_r1": parse_05a_r1(),
        "doc_5decisions": parse_5decisions(),
    }

    # 未批准但没进「13 项待决」入口的字段（按 05-A-R1 逐行状态口径）
    d5 = payload["doc_05a_r1"]
    unapproved_status = ("建议未批准", "待确认", "建议沿用")
    unapproved = [r["path"] for r in d5["detail"] if r["status"] in unapproved_status]
    mapped = set(pending_fields) | set(conflicts)
    unmapped = [p for p in unapproved if p not in mapped]
    payload["coverage"] = {
        "unapproved_by_rows": len(unapproved),
        "mapped_to_13": len([p for p in unapproved if p in mapped]),
        "unmapped": unmapped,
        "unmapped_n": len(unmapped),
        "funding_fields_in_49": [p for p in required if "fund" in p.lower()],
        "funding_field_count": len([p for p in required if "fund" in p.lower()]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    d = payload["required_paths"]["counts"]
    print(json.dumps({
        "required": d,
        "decomposition_ok": payload["required_paths"]["decomposition_ok"],
        "pending": payload["pending_decisions"],
        "blank": payload["blank_form"],
        "fixture": payload["fixture"],
        "doc_04a": payload["doc_04a"],
        "doc_05a_r1_rows": payload["doc_05a_r1"]["rows"],
        "doc_05a_r1_buckets": {k: v["n"] for k, v in payload["doc_05a_r1"]["buckets"].items()},
        "decisions": payload["doc_5decisions"],
    }, ensure_ascii=False, indent=1))
    return 0 if payload["required_paths"]["decomposition_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
