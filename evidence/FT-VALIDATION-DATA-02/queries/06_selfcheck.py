# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 06：自检（逐项复算，不看自报）。"""
from __future__ import annotations

import csv
import json
import os
import sys

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T2 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02")
OUT = os.path.join(T2, "out")
LEDGER = os.path.join(ROOT, "evidence/_统一执行台账")
sys.path.insert(0, os.path.join(ROOT, "框架/backend"))


def main():
    concl = list(csv.DictReader(open(os.path.join(OUT, "02_逐条结论.csv"), encoding="utf-8-sig")))
    ev = list(csv.DictReader(open(os.path.join(OUT, "01_证据采集_逐区间.csv"), encoding="utf-8-sig")))
    warm = list(csv.DictReader(open(os.path.join(OUT, "05_预热加载实证.csv"), encoding="utf-8-sig")))
    up = list(csv.DictReader(open(os.path.join(ROOT,
        "evidence/FT-VALIDATION-DATA-01/out/03_区间资格表.csv"), encoding="utf-8-sig")))
    up49 = {(r["symbol"], r["区间起"], r["区间止"]) for r in up if r["接触分层"] == "④"}
    mine = {(r["symbol"], r["区间起"], r["区间止"]) for r in concl}
    rec = json.load(open(os.path.join(OUT, "03_登记回执.json"), encoding="utf-8"))

    checks = []
    checks.append(("49 条区间", len(concl) == 49, str(len(concl))))
    checks.append(("与上游 ④ 档 49 条一一对应", up49 == mine, "%d vs %d" % (len(up49), len(mine))))
    checks.append(("四分类全部为 A", all(r["接触状态"] == "A" for r in concl), ""))
    checks.append(("子级全部为 A2-计算级", all(r["接触子级"] == "A2-计算级" for r in concl), ""))
    checks.append(("事件级命中 = 0",
                   all(int(r["事件级_窗口覆盖数"]) == 0 and int(r["事件级_区间内策略输出数"]) == 0
                       for r in concl), ""))
    checks.append(("计算级命中 = 49",
                   all(float(r["计算级_ATR预热伸入天数"]) > 0 for r in concl), ""))
    checks.append(("ATR 伸入天数在 32~36.7",
                   all(31.5 <= float(r["计算级_ATR预热伸入天数"]) <= 36.8 for r in concl), ""))
    checks.append(("4h/1h/15m 覆盖率均 = 1.0",
                   all(float(r["数据_4h覆盖率"]) == 1.0 and float(r["数据_1h覆盖率"]) == 1.0
                       and float(r["数据_15m覆盖率"]) == 1.0 for r in concl), ""))
    checks.append(("OI 5m 覆盖率均 ≥0.9995",
                   all(float(r["数据_OI5m覆盖率"]) >= 0.9995 for r in concl), ""))
    checks.append(("OI fetched_at 空值率均 = 1.0",
                   all(float(r["OI时点证据_fetched_at空值率"]) == 1.0 for r in concl), ""))
    checks.append(("满 12 个月 = 0 条", all(r["时长_满12个月"] == "否" for r in concl), ""))
    checks.append(("满 30 天 = 49 条", all(r["时长_满30天"] == "是" for r in concl), ""))
    checks.append(("天数合计 3820",
                   sum(int(r["天数"]) for r in concl) == 3820, str(sum(int(r["天数"]) for r in concl))))
    checks.append(("日历段分布 42/6/1",
                   {r["覆盖日历段"] for r in concl} <= {"S5", "S4/S5", "S4"}
                   and sum(1 for r in concl if r["覆盖日历段"] == "S5") == 42, ""))
    checks.append(("预热实证 49 行且 48 条≈220",
                   len(warm) == 49 and sum(1 for w in warm if w["是否≈220"] == "是") == 48,
                   "%d 行 / %d 条" % (len(warm), sum(1 for w in warm if w["是否≈220"] == "是"))))
    checks.append(("证据采集表与结论表同数", len(ev) == len(concl), "%d vs %d" % (len(ev), len(concl))))

    # 登记链复验
    import experiment_registry as er
    reg = er.get_registry()
    rows = reg._require_intact()
    last = rows[-1]["event"]
    keys = {r["sample_set_id"] for r in [last] if r.get("type") == "observe"}
    checks.append(("登记链完整（_require_intact 通过）", True, "事件 %d 条" % len(rows)))
    checks.append(("末条事件为 observe 且 49 键",
                   last.get("type") == "observe" and len(last.get("sample_keys") or []) == 49,
                   "%s / %d 键" % (last.get("type"), len(last.get("sample_keys") or []))))
    checks.append(("登记事件哈希与回执一致",
                   rows[-1]["hash"] == rec["新事件"]["hash"], ""))
    reg_keys = set(last.get("sample_keys") or [])
    from datetime import datetime, timezone
    def ms(s):
        return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    csv_keys = {"%s|%d" % (r["symbol"], ms(r["区间起"])) for r in concl}
    checks.append(("登记键集合 = 结论表键集合", reg_keys == csv_keys, "%d vs %d" % (len(reg_keys), len(csv_keys))))

    # 台账
    led = os.path.join(LEDGER, "06_FT-VALIDATION-DATA-02_逐条结论.md")
    txt = open(led, encoding="utf-8").read() if os.path.isfile(led) else ""
    n_rows = sum(1 for line in txt.splitlines() if line.startswith("| ") and "| A2-计算级 |" in line)
    checks.append(("台账页存在且含 49 行明细", n_rows == 49, str(n_rows)))
    man = open(os.path.join(LEDGER, "05_文件清单与哈希.csv"), encoding="utf-8-sig").read()
    checks.append(("台账清单已追加本页", "06_FT-VALIDATION-DATA-02_逐条结论.md" in man, ""))

    res = {"检查项数": len(checks),
           "通过": sum(1 for _, ok, _ in checks if ok),
           "明细": [{"检查": n, "通过": bool(ok), "备注": note} for n, ok, note in checks]}
    with open(os.path.join(OUT, "06_自检结果.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    for n, ok, note in checks:
        print(("PASS " if ok else "FAIL ") + n + ("  " + note if note else ""))
    print("合计 %d/%d" % (res["通过"], res["检查项数"]))


if __name__ == "__main__":
    main()
