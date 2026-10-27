# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-02 / 04：把逐条结论写进既有跨工单台账（人读入口）。

台账目录：evidence/_统一执行台账/（既有，不新建系统）
  · 新增 06_FT-VALIDATION-DATA-02_逐条结论.md
  · 在该目录 05_文件清单与哈希.csv 追加本文件一行（append-only）

输出：台账文件 + 哈希追加。
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
T2 = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-02")
OUT = os.path.join(T2, "out")
LEDGER = os.path.join(ROOT, "evidence/_统一执行台账")
TARGET = os.path.join(LEDGER, "06_FT-VALIDATION-DATA-02_逐条结论.md")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    rows = list(csv.DictReader(open(os.path.join(OUT, "02_逐条结论.csv"), encoding="utf-8-sig")))
    receipt = json.load(open(os.path.join(OUT, "03_登记回执.json"), encoding="utf-8"))
    from collections import Counter
    cnt = Counter(r["接触状态"] for r in rows)
    sub = Counter(r["接触子级"] for r in rows)

    lines = []
    A = lines.append
    A("# FT-VALIDATION-DATA-02｜49 条「接触历史不清楚」区间逐条结论")
    A("")
    A("- 登记时间：%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("- 性质：只读核查；不跑策略、不算收益、不打开封存行情")
    A("- 交付目录：`evidence/FT-VALIDATION-DATA-02/`")
    A("- 研究记录入口：`框架/backend/data/experiments/events.jsonl` 第 %s 条事件（EV_OBSERVE，哈希 `%s`）"
      % (receipt["新事件"]["seq"], receipt["新事件"]["hash"][:16] + "…"))
    A("")
    A("## 结论")
    A("")
    A("| 四分类 | 条数 | 说明 |")
    A("|---|---:|---|")
    A("| A 有证据表明曾用于策略研究 | **%d** | 全部为 A2-计算级：无窗口覆盖、无候选/成交，但末段 4h 被后续研究窗口当 ATR 预热读入 |" % cnt.get("A", 0))
    A("| B 仅检查过数据完整性 | %d | — |" % cnt.get("B", 0))
    A("| C 有依据支持未用于研究 | %d | — |" % cnt.get("C", 0))
    A("| D 仍无法确认 | %d | — |" % cnt.get("D", 0))
    A("")
    A("事件级（区间内产出候选/成交、或被窗口逐根处理）：**0 条**。")
    A("计算级（区间数据进入后续窗口 ATR 递推）：**%d 条**，伸入 %d~%d 天。"
      % (sum(1 for r in rows if float(r["计算级_ATR预热伸入天数"]) > 0),
         int(min(float(r["计算级_ATR预热伸入天数"]) for r in rows)),
         int(max(float(r["计算级_ATR预热伸入天数"]) for r in rows))))
    A("记录级（入库/覆盖检查/清单）：%d 条；数据本身 4h/1h/15m 覆盖率 100%%、OI 5m 覆盖 ≥99.96%%。"
      % sum(1 for r in rows if int(r["记录级_入库源文件数"]) > 0))
    A("")
    A("> 关键：**不能**判「仅检查过数据完整性」——数据确实进了研究计算（ATR 预热）；")
    A("> 也**不能**判「未用于研究」——区间末段波动率决定了后续窗口的箱体归一化与止损距离。")
    A("")
    A("## 逐条")
    A("")
    A("| # | 标的 | 区间 | 天数 | 状态 | ATR 预热伸入 | 后续窗口 | OI 5m 覆盖 | 满 12 个月 |")
    A("|---:|---|---|---:|---|---:|---:|---:|---|")
    for i, r in enumerate(rows, 1):
        A("| %d | %s | %s ~ %s | %s | %s | %s 天 | %s | %s | %s |" % (
            i, r["symbol"], r["区间起"], r["区间止"], r["天数"], r["接触子级"],
            r["计算级_ATR预热伸入天数"], r["计算级_后续窗口数"],
            r["数据_OI5m覆盖率"], r["时长_满12个月"]))
    A("")
    A("## 证据链接")
    A("")
    A("| 内容 | 路径 |")
    A("|---|---|")
    A("| 主报告 | `evidence/FT-VALIDATION-DATA-02/00_报告.md` |")
    A("| 逐条结论表 | `evidence/FT-VALIDATION-DATA-02/out/02_逐条结论.csv` |")
    A("| 逐条证据明细 | `evidence/FT-VALIDATION-DATA-02/out/01_证据明细.json` |")
    A("| 证据采集表 | `evidence/FT-VALIDATION-DATA-02/out/01_证据采集_逐区间.csv` |")
    A("| 登记回执 | `evidence/FT-VALIDATION-DATA-02/out/03_登记回执.json` |")
    A("| 上游区间表 | `evidence/FT-VALIDATION-DATA-01/out/03_区间资格表.csv` |")
    A("| 运行索引 | `evidence/FT-VALIDATION-DATA-01/out/02_接触运行索引.csv` |")
    A("")
    A("## 对上游结论的影响")
    A("")
    A("- `FT-VALIDATION-DATA-01` 的「④ 接触历史不清楚 49 段」应收窄为 **A2-计算级接触 49 段**：")
    A("  不是「不清楚」，而是「确实以计算级方式进过研究」。")
    A("- 「合格独立验证区间 = 0」不变；这 49 段仍然不能当样本外，理由更硬（ATR 泄漏），而不是更软。")
    A("- 任何把这 49 段当「未见过」的用法都已被登记阻断（EV_OBSERVE）。")

    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("写出 %s（%d 行）" % (TARGET, len(lines)))

    # 追加哈希到既有清单（append-only）
    man = os.path.join(LEDGER, "05_文件清单与哈希.csv")
    with open(man, encoding="utf-8-sig") as fh:
        existing = fh.read()
    rel = "06_FT-VALIDATION-DATA-02_逐条结论.md"
    if rel not in existing:
        with open(man, "a", encoding="utf-8") as fh:
            fh.write("%s,%d,%s\n" % (rel, os.path.getsize(TARGET), sha256(TARGET)))
        print("已追加清单行：%s" % rel)
    else:
        print("清单已含该文件，跳过追加")


if __name__ == "__main__":
    main()
