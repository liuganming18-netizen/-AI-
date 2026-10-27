# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 03：把 49 条结论登记进系统现有研究记录入口。

入口：`框架/backend/experiment_registry.py` 的 `observe()`（append-only + 哈希链，
事件类型 EV_OBSERVE =「这批样本已经被看过，此后不得再当未见留出」）。
不新建任何系统、不写数据库表、不改其它入口。

登记语义：
  · 49 条区间均属 A2-计算级接触（数据被后续研究窗口作为 4h 预热读入），
    因此按「已观察」登记是保守且正确的方向：它们不能再被当作未见留出。
  · 每条键 = `<symbol>|<区间起 epoch ms>`，与 `_contamination_blockers` 的解析口径一致。

输出：out/03_登记回执.json（含登记前后链状态、事件哈希、证据指纹）
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import sys

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T2 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02")
OUT = os.path.join(T2, "out")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))

SAMPLE_SET_ID = "FT-VALIDATION-DATA-02/49条④区间-计算级接触"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ms(s):
    s = str(s).strip().rstrip("Z")
    if len(s) == 10:
        return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def main():
    import experiment_registry as er

    reg = er.get_registry()
    before = reg._require_intact()
    before_head = {"count": len(before), "last_hash": before[-1]["hash"] if before else er.GENESIS}

    rows = list(csv.DictReader(open(os.path.join(OUT, "02_逐条结论.csv"), encoding="utf-8-sig")))
    keys = ["%s|%d" % (r["symbol"], ms(r["区间起"])) for r in rows]
    assert len(keys) == len(set(keys)) == 49, "键必须唯一且为 49 条"

    ev_path = os.path.join(OUT, "02_逐条结论.csv")
    note = ("FT-VALIDATION-DATA-02 逐条核查结论：49 条全部为 A2-计算级接触（无事件级："
            "无窗口覆盖、无候选/成交落在区间内；但区间末段 32~36.7 天被 1~2 个后续研究窗口"
            "作为 4h 预热读入，参与 ATR(14) 递推）。证据：evidence/FT-VALIDATION-DATA-02/"
            "out/02_逐条结论.csv（sha256 %s）、out/01_证据明细.json、00_报告.md。" % sha256(ev_path)[:32])

    st = reg.observe(SAMPLE_SET_ID, keys, rows=len(keys),
                     by="Work/FT-VALIDATION-DATA-02",
                     purpose="数据接触审计：计算级（4h ATR 预热），非事件级",
                     note=note)

    after = reg._require_intact()
    new_event = after[-1]["event"] if after else {}
    receipt = {
        "工单": "FT-VALIDATION-DATA-02",
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
        "证据文件": {"out/02_逐条结论.csv": sha256(ev_path)},
        "登记的语义": "「已观察」= 该批样本此后不得被任何实验当作未见留出；不改变任何运行结果。",
        "未做的事": ["未修改任何历史事件", "未新建表或系统", "未跑策略、未算收益"],
    }
    with open(os.path.join(OUT, "03_登记回执.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
