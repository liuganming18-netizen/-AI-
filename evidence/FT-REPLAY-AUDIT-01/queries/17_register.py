# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 17：把本次验收抽样登记进系统现有研究记录入口。

入口：`框架/backend/experiment_registry.py::ExperimentRegistry.observe`（EV_OBSERVE，
append-only + 哈希链）。不新建系统、不写数据库、不改其它入口。

登记语义：这 9 笔已被逐笔核对过 ⇒ 按「已观察」登记，此后不得当作未见样本。
键 = `<币级run_id>|<入场毫秒>`，与逐笔表主键同构。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))

SAMPLE_SET_ID = "FT-REPLAY-AUDIT-01/9笔验收抽样"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    import experiment_registry as er

    reg = er.get_registry()
    before = reg._require_intact()
    before_head = {"count": len(before), "last_hash": before[-1]["hash"] if before else er.GENESIS}

    payload = json.load(open(os.path.join(OUT, "13_页面期望值_v2.json"), encoding="utf-8"))
    keys = ["%s|%d" % (p["币级run_id"], p["期望"]["入场时间"]) for p in payload]
    assert len(keys) == len(set(keys)) == 9, "键必须唯一且为 9 条"

    det = os.path.join(OUT, "13_逐笔核对明细_v2.csv")
    page = os.path.join(OUT, "15_页面逐字段核对_v2.csv")
    note = ("FT-REPLAY-AUDIT-01 独立验收：C0/C1/C2 各 3 笔共 9 笔已逐笔核对"
            "（源账本→币级登记→单 run API→页面→K 线标记）。结论：币级链路 78/78 字段一致、"
            "页面 9/9 打开且数值一致；但页面「下单时间」列 9/9 等于「进场时间」、"
            "组级登记 P0 缺失且信号时间取错字段、专用 /rejected 接口读不出拒单。"
            "整体未通过，需修正后复验。证据：evidence/FT-REPLAY-AUDIT-01/00_报告.md（明细 %s / %s）。"
            % (sha256(det)[:16], sha256(page)[:16]))

    st = reg.observe(SAMPLE_SET_ID, keys, rows=len(keys),
                     by="hharness/FT-REPLAY-AUDIT-01",
                     purpose="系统复盘独立验收：抽样逐笔已核对，此后不得当未见样本",
                     note=note)

    after = reg._require_intact()
    new_event = after[-1]["event"] if after else {}
    receipt = {
        "工单": "FT-REPLAY-AUDIT-01",
        "登记时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "入口": "框架/backend/experiment_registry.py::ExperimentRegistry.observe（EV_OBSERVE）",
        "存储": "框架/backend/data/experiments/events.jsonl（append-only + 哈希链）",
        "sample_set_id": SAMPLE_SET_ID,
        "登记键数": len(keys),
        "键样例": keys[:3],
        "链状态_登记前": before_head,
        "链状态_登记后": {"count": len(after), "last_hash": after[-1]["hash"] if after else er.GENESIS},
        "新事件": {"seq": after[-1]["seq"] if after else None,
                   "type": new_event.get("type"),
                   "at": new_event.get("at"),
                   "by": new_event.get("by"),
                   "rows": new_event.get("rows"),
                   "purpose": new_event.get("purpose"),
                   "hash": after[-1]["hash"] if after else None},
        "链完整性复验": "通过（_require_intact 未抛 RegistryTampered）",
        "证据文件": {"out/13_逐笔核对明细_v2.csv": sha256(det),
                     "out/15_页面逐字段核对_v2.csv": sha256(page)},
        "登记的语义": "「已观察」= 这 9 笔此后不得被当作未见样本；不改变任何运行结果。",
        "未做的事": ["未修改历史事件", "未新建表或系统", "未跑策略、未改账本"],
    }
    with open(os.path.join(OUT, "17_登记回执.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
