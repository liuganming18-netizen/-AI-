# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 / 07：把本次对照审计样本登记进系统现有研究记录入口。

入口：`框架/backend/experiment_registry.py::ExperimentRegistry.observe`（EV_OBSERVE，
append-only + 哈希链）。不新建系统、不改其它入口。

登记语义：原 9 组 C1 基线 740 笔 + 20% 方案 631 笔，共 1371 条「组×事件」逐笔对照样本
已被完整核对过 ⇒ 按「已观察」登记，此后不得当作未见样本。
键 = `<组>|<事件编号>`，与逐笔对照表主键同构。
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-STOP-20-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))

SAMPLE_SET_ID = "FT-STOP-20-AUDIT-01/20%止损对照-9组C1逐笔配对"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def eid(tag):
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def keys_of(pattern, gfun, plan):
    ks = []
    for p in sorted(glob.glob(pattern)):
        g = gfun(p)
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                ks.append(f"{plan}|{g}|{eid(r['enter_tag'])}")
    return ks


def main():
    import experiment_registry as er

    reg = er.get_registry()
    before = reg._require_intact()
    before_head = {
        "count": len(before),
        "last_hash": before[-1]["hash"] if before else er.GENESIS,
    }

    kb = keys_of(
        os.path.join(ROOT, "evidence/FT-ENTRY-09/out/trades_Wyckoff_C1_Vs*.csv"),
        lambda p: Path(p).stem.replace("trades_Wyckoff_", ""),
        "基线",
    )
    ks = keys_of(
        os.path.join(ROOT, "evidence/FT-STOP-20-01/out/trades_Wyckoff_C1_Stop20_*.csv"),
        lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", ""),
        "20方案",
    )
    keys = kb + ks
    assert len(kb) == 740, len(kb)
    assert len(ks) == 631, len(ks)
    assert len(keys) == len(set(keys)) == 1371, (len(keys), len(set(keys)))

    cmp_csv = os.path.join(OUT, "03_逐笔对照.csv")
    aud_csv = os.path.join(OUT, "04_逐笔核对.csv")
    note = (
        "FT-STOP-20-AUDIT-01 独立审核：原 9 组 C1（740 笔）与 20% 固定止损方案（631 笔）"
        "按事件编号逐笔配对核对。开跑前核对通过（20% 止损+0.5% 风险预算、2R=+40% 联动、"
        "其余信号/数据/费用/退出/账户约束未变）。逐笔核对 631 笔：止损价 631/631、"
        "2R 631/631、名义不超发 627/631（4 笔 BTC 因最小合约单位被迫超发）。"
        "结论：总 R 由 +37.97 降至 −34.54（−72.51），胜率 23.11%→50.08%，"
        "平均持仓 44.89h→204.84h；亏损端 +363.33R 与盈利端 −364.56R 几乎完全对冲，"
        "因 2R 门槛抬到 +40% 使跟踪止损由 176 笔降至 28 笔。"
        "判定为「提高胜率、牺牲盈利、延长持仓」，未改善整体风险收益。"
        "另发现系统登记 signal_time_ms 取错字段（偏差 17~134 小时，上一轮已指出、本轮重犯）"
        "与 Vs04_Z02 确认表重复 173 行。证据：evidence/FT-STOP-20-AUDIT-01/00_报告.md"
        "（明细 " + sha256(cmp_csv)[:16] + " / " + sha256(aud_csv)[:16] + "）。"
    )

    reg.observe(
        SAMPLE_SET_ID,
        keys,
        rows=len(keys),
        by="hharness/FT-STOP-20-AUDIT-01",
        purpose="止损与仓位效果独立审核：9 组 C1 与 20% 方案逐笔配对已核对，此后不得当未见样本",
        note=note,
    )

    after = reg._require_intact()
    new_event = after[-1]["event"] if after else {}
    receipt = {
        "工单": "FT-STOP-20-AUDIT-01",
        "登记时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "入口": "框架/backend/experiment_registry.py::ExperimentRegistry.observe（EV_OBSERVE）",
        "存储": "框架/backend/data/experiments/events.jsonl（append-only + 哈希链）",
        "sample_set_id": SAMPLE_SET_ID,
        "登记键数": len(keys),
        "基线键数": len(kb),
        "20方案键数": len(ks),
        "键样例": keys[:3],
        "链状态_登记前": before_head,
        "链状态_登记后": {
            "count": len(after),
            "last_hash": after[-1]["hash"] if after else er.GENESIS,
        },
        "新事件": {
            "seq": after[-1]["seq"] if after else None,
            "type": new_event.get("type"),
            "at": new_event.get("at"),
            "by": new_event.get("by"),
            "rows": new_event.get("rows"),
            "purpose": new_event.get("purpose"),
            "hash": after[-1]["hash"] if after else None,
        },
        "链完整性复验": "通过（_require_intact 未抛 RegistryTampered）",
        "证据文件": {"out/03_逐笔对照.csv": sha256(cmp_csv), "out/04_逐笔核对.csv": sha256(aud_csv)},
        "登记的语义": "「已观察」= 这 1371 条对照样本此后不得被当作未见样本；不改变任何运行结果。",
        "未做的事": ["未修改历史事件", "未新建表或系统", "未跑策略、未改账本、未改策略代码"],
    }
    with open(os.path.join(OUT, "07_登记回执.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
