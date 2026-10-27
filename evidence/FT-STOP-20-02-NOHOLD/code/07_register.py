# -*- coding: utf-8 -*-
"""FT-STOP-20-02-NOHOLD / 07：把本轮 18 组无超时回测的逐笔样本登记进系统现有研究记录入口。

入口：`框架/backend/experiment_registry.py::ExperimentRegistry.observe`（EV_OBSERVE，
append-only + 哈希链）。不新建系统、不改其它入口。

登记语义：A 组（20% 止损 + 无超时）451 笔、B 组（ATR 止损 + 无超时）740 笔，
共 1191 条「组×事件」逐笔样本已被完整核对过 ⇒ 按「已观察」登记，此后不得当作未见样本。
C0/C1 的 1371 条上一轮已登记（seq=11），本轮不重复登记。
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
AUD = os.path.join(ROOT, "evidence/FT-STOP-20-02-NOHOLD")
OUT = os.path.join(AUD, "out")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))

SAMPLE_SET_ID = "FT-STOP-20-02-NOHOLD/去掉240h超时-18组逐笔"


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
    before_head = {"count": len(before), "last_hash": before[-1]["hash"] if before else er.GENESIS}

    ka = keys_of(os.path.join(OUT, "trades_Wyckoff_C1_Stop20NoHold_*.csv"),
                 lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20NoHold_", ""),
                 "A20止损无超时")
    kb = keys_of(os.path.join(OUT, "trades_Wyckoff_C1_NoHold_*.csv"),
                 lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_NoHold_", ""),
                 "BATR止损无超时")
    keys = ka + kb
    assert len(ka) == 451, len(ka)
    assert len(kb) == 740, len(kb)
    assert len(keys) == len(set(keys)) == 1191, (len(keys), len(set(keys)))

    rep = os.path.join(AUD, "00_报告.md")
    sc = os.path.join(OUT, "05_自检结果.json")
    note = (
        "FT-STOP-20-02-NOHOLD 重测：按用户指令去掉 240 小时持仓上限，从两套基类各派生一套"
        "无超时策略（唯一改动=custom_exit 删掉 timeout_240h，源码差异 14 行，信号/数据/费用/"
        "仓位/2R/跟踪/账户约束全未动），9 组网格 × 2 套止损共 18 次回测，窗口 20211201-20241231。"
        "结果：A 组（20% 止损+无超时）451 笔/胜率 29.71%/净 −322.03/总 R −29.41/PF 0.894/"
        "最大回撤 61.12%/持仓中位 469.2h；B 组（ATR 止损+无超时）740 笔/胜率 23.11%/净 +361.54/"
        "总 R +41.44/PF 1.066/最大回撤 34.93%。"
        "对照：C1（20% 有超时）−34.54R、C0（基线有超时）+37.97R。"
        "结论：去掉超时救不了 20% 方案——总 R 仅 +5.13R 改善，但最大回撤从 36.28% 翻倍到 61.12%、"
        "平均每笔 R 由 −0.0547 恶化到 −0.0652；R 桥分解显示 +5.13R 中只有 +7.07R 与「不止损」有关，"
        "其余来自槽位锁死导致的「少做 39 笔亏单 +21.00、多做 8 笔赚单 +15.57、丢掉 149 笔赚单 −37.98」。"
        "基线去掉超时反而是纯改善（+37.97R→+41.44R，回撤下降，740 笔事件一笔不差）⇒ "
        "病根是 20% 止损本身（仓位压缩 8.2 倍 + 2R 门槛抬到 +40%），不是超时规则。"
        "证据：evidence/FT-STOP-20-02-NOHOLD/00_报告.md（" + sha256(rep)[:16] + "）。"
    )

    reg.observe(
        SAMPLE_SET_ID,
        keys,
        rows=len(keys),
        by="hharness/FT-STOP-20-02-NOHOLD",
        purpose="去掉 240h 持仓上限重测：A/B 两组 18 次回测逐笔已核对，此后不得当未见样本",
        note=note,
    )

    after = reg._require_intact()
    new_event = after[-1]["event"] if after else {}
    receipt = {
        "工单": "FT-STOP-20-02-NOHOLD",
        "登记时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "入口": "框架/backend/experiment_registry.py::ExperimentRegistry.observe（EV_OBSERVE）",
        "存储": "框架/backend/data/experiments/events.jsonl（append-only + 哈希链）",
        "sample_set_id": SAMPLE_SET_ID,
        "登记键数": len(keys),
        "A组键数": len(ka),
        "B组键数": len(kb),
        "键样例": keys[:3],
        "链状态_登记前": before_head,
        "链状态_登记后": {"count": len(after),
                    "last_hash": after[-1]["hash"] if after else er.GENESIS},
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
        "证据文件": {"00_报告.md": sha256(rep), "out/05_自检结果.json": sha256(sc)},
        "登记的语义": "「已观察」= 这 1191 条样本此后不得被当作未见样本；不改变任何运行结果。",
        "未做的事": ["未修改历史事件", "未新建表或系统", "未改既有策略文件", "未改账本", "未改闸门"],
    }
    with open(os.path.join(OUT, "07_登记回执.json"), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
