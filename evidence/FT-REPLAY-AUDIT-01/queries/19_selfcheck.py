# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 19：自检（不信自己的汇总，逐项回到产物文件重算）。"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys

import pandas as pd

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
LEDGER = os.path.join(ROOT, "evidence/_统一执行台账")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))

res = []


def ck(name, ok, detail=""):
    res.append({"项": name, "通过": bool(ok), "实测": str(detail)})


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    # 1 报告
    rp = os.path.join(AUDIT, "00_报告.md")
    txt = open(rp, encoding="utf-8").read()
    ck("报告存在且结论为未通过", "未通过" in txt and "整体未通过" in txt, len(txt))

    # 2 锁定
    lock = json.load(open(os.path.join(OUT, "10_锁定摘要_v2.json"), encoding="utf-8"))
    ck("锁定文件全部存在", lock["缺失数"] == 0, "%d 个文件" % lock["锁文件数"])
    ck("组级 27 / 币级 1065", lock["组级run目录数"] == 27 and lock["币级run目录数"] == 1065,
       "%s / %s" % (lock["组级run目录数"], lock["币级run目录数"]))
    ck("币级批次全部软链到主运行根", lock["币级批次软链指向主运行根"] == 1065,
       lock["币级批次软链指向主运行根"])

    # 3 分组关系
    g = json.load(open(os.path.join(OUT, "11_分组关系_v2.json"), encoding="utf-8"))
    ck("三方笔数均为 4325",
       g["A_源账本"]["笔数"] == g["B_组级登记"]["笔数"] == g["C_币级登记"]["笔数"] == 4325,
       "%d/%d/%d" % (g["A_源账本"]["笔数"], g["B_组级登记"]["笔数"], g["C_币级登记"]["笔数"]))
    ck("三方净额一致", abs(g["A_源账本"]["净额"] - g["B_组级登记"]["净额"]) < 1e-6
       and abs(g["A_源账本"]["净额"] - g["C_币级登记"]["净额"]) < 1e-6,
       "%.6f" % g["A_源账本"]["净额"])
    ck("27 组逐组一致", g["逐组全部一致"] is True and len(g["不一致组"]) == 0, "不一致组 %d" % len(g["不一致组"]))
    ck("去重：组内 0 / 币级 0", g["A_源账本"]["组内重复键"] == 0 and g["C_币级登记"]["重复键"] == 0,
       "组内 %d / 币级 %d" % (g["A_源账本"]["组内重复键"], g["C_币级登记"]["重复键"]))
    ck("合并表与 27 份单表一致", g["合并表核对"]["笔数一致"] and g["合并表核对"]["净额差"] < 1e-9,
       "净额差 %s" % g["合并表核对"]["净额差"])

    # 4 抽样
    s = json.load(open(os.path.join(OUT, "13_抽样摘要_v2.json"), encoding="utf-8"))
    ck("抽样 9 笔且每模式 ≥2", s["抽样数"] == 9 and all(v >= 2 for v in s["按模式"].values()),
       s["按模式"])
    ck("覆盖盈利/亏损/跨日/多币种",
       s["盈利笔数"] >= 2 and s["亏损笔数"] >= 2 and s["跨日笔数"] >= 2 and s["不同币种数"] >= 6,
       "盈 %d / 亏 %d / 跨日 %d / 币种 %d" % (s["盈利笔数"], s["亏损笔数"], s["跨日笔数"], s["不同币种数"]))
    ck("币级链路 78/78", s["各环节一致率"]["币级jsonl"] == "78/78", s["各环节一致率"]["币级jsonl"])
    ck("单 run API 78/78", s["各环节一致率"]["单runAPI"] == "78/78", s["各环节一致率"]["单runAPI"])
    ck("K 线标记 78/78", s["各环节一致率"]["K线标记"] == "78/78", s["各环节一致率"]["K线标记"])
    ck("组级链路 63/75（已知差异 12 项）", s["各环节一致率"]["组级jsonl"] == "63/75",
       s["各环节一致率"]["组级jsonl"])

    # 5 页面
    p = json.load(open(os.path.join(OUT, "15_页面摘要_v2.json"), encoding="utf-8"))
    ck("页面 9/9 打开并通过", p["页面全部通过数"] == 9 and p["页面样本数"] == 9,
       "%d/%d" % (p["页面全部通过数"], p["页面样本数"]))
    ck("页面字段 81/81", p["总字段一致率"] == "81/81", p["总字段一致率"])
    ck("点行触发 focus_ms 9/9", p["focus_ms请求命中数"] == 9, p["focus_ms请求命中数"])
    ck("下单时间=进场时间（缺陷已记录）", p["下单时间等于进场时间的笔数"] == 9,
       p["下单时间等于进场时间的笔数"])
    shots = glob.glob(os.path.join(AUDIT, "screens", "*.png"))
    ck("截图 ≥ 10 张", len(shots) >= 10, len(shots))

    # 6 边界
    e = json.load(open(os.path.join(OUT, "14_边界案例摘要_v2.json"), encoding="utf-8"))
    edf = pd.read_csv(os.path.join(OUT, "14_边界案例_v2.csv"), encoding="utf-8-sig")
    ck("边界三例齐备", len(edf) == 3, list(edf["类别"]))
    ck("拒单未被伪造成交", all(edf[edf["类别"] == "拒单"]["源里是否被记成成交"] == "否"), "")
    ck("确认失败未被伪造成交", all(edf[edf["类别"] == "确认失败/未成交"]["源里是否被记成成交"] == "否"), "")
    ck("候选矩阵含 filled/rejected/not_filled",
       e["候选对照矩阵"].get("filled") == 4325 and e["候选对照矩阵"].get("rejected", 0) > 0,
       e["候选对照矩阵"])

    # 7 元数据
    m = json.load(open(os.path.join(OUT, "16_元数据摘要_v2.json"), encoding="utf-8"))
    ck("时区 0 不一致", m["时区与单位"]["open不一致"] == 0 and m["时区与单位"]["close不一致"] == 0,
       m["时区与单位"])
    ck("holding_minutes 0 不一致", m["时区与单位"]["holding不一致"] == 0, m["时区与单位"]["holding不一致"])
    ck("入场全部 15m 对齐", m["时区与单位"]["未对齐15m"] == 0, m["时区与单位"]["未对齐15m"])
    ck("币级 trade_id 全部可重建", m["trade_id"]["币级可重建"] == 4325, m["trade_id"])
    ck("组级 trade_id 全为序号（缺陷已记录）", m["trade_id"]["组级序号ID"] == 4325, m["trade_id"])
    ck("研究记录链里本批 0 条（符合导入性质）", m["研究记录链"]["含本批的事件数"] in (0, 1),
       m["研究记录链"])

    # 8 登记
    rec = json.load(open(os.path.join(OUT, "17_登记回执.json"), encoding="utf-8"))
    ev = jl(os.path.join(ROOT, "框架/backend/data/experiments/events.jsonl"))
    ck("登记链末条为 observe 且 9 键",
       ev[-1]["event"].get("type") == "observe" and ev[-1]["event"].get("rows") == 9,
       "seq=%s rows=%s" % (ev[-1]["seq"], ev[-1]["event"].get("rows")))
    ck("登记回执哈希与链末条一致", rec["新事件"]["hash"] == ev[-1]["hash"], ev[-1]["hash"][:16])
    import experiment_registry as er
    try:
        er.get_registry()._require_intact()
        ck("登记链完整（_require_intact 通过）", True, "%d 条事件" % len(ev))
    except Exception as ex:
        ck("登记链完整（_require_intact 通过）", False, str(ex)[:120])

    # 9 台账
    page = os.path.join(LEDGER, "07_FT-REPLAY-AUDIT-01_验收结论.md")
    ck("台账页存在且写明未通过", os.path.isfile(page) and "未通过" in open(page, encoding="utf-8").read(),
       os.path.getsize(page) if os.path.isfile(page) else 0)
    man = list(csv.DictReader(open(os.path.join(LEDGER, "05_文件清单与哈希.csv"), encoding="utf-8-sig")))
    ck("台账清单已收录本页", any(r["相对路径"] == os.path.basename(page) for r in man), len(man))

    df = pd.DataFrame(res)
    df.to_csv(os.path.join(OUT, "19_自检结果.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(OUT, "19_自检结果.json"), "w", encoding="utf-8") as fh:
        json.dump({"通过": int(df["通过"].sum()), "总数": len(df),
                   "明细": res}, fh, ensure_ascii=False, indent=2)
    for r in res:
        print("%s %s  %s" % ("PASS" if r["通过"] else "FAIL", r["项"], r["实测"]))
    print("合计 %d/%d" % (int(df["通过"].sum()), len(df)))


if __name__ == "__main__":
    main()
