# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 05：段级汇总 + 交给 hharness 的资格结论 + 自检 + 哈希清单。

输入全部是本单 out/ 下已产出的表，不新增读取。
输出：
  out/04_段级资格汇总.csv
  out/05_hharness_资格结论.json
  out/06_自检结果.json
  05_文件清单与哈希.csv（本目录产物）
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
TICKET = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01")
OUT = os.path.join(TICKET, "out")

WARMUP_H = 200 * 4
SEGMENTS = [
    ("S0", "2019-12-31", "2020-08-31", "未接触记录", "数据不合格：全池无 OI"),
    ("S1", "2020-09-01", "2020-12-31", "未接触记录", "数据不合格：仅 BTC 有 OI、无 mark"),
    ("S2", "2021-01-01", "2021-11-30", "已接触", "已扫描；OI 自 2021-12 起才全池可用"),
    ("S3", "2021-12-01", "2021-12-31", "已接触", "已扫描，6 笔唯一交易"),
    ("S4", "2022-01-01", "2026-04-14", "已接触", "主样本母集，253 笔唯一交易"),
    ("S5", "2026-04-15", "2026-10-13", "已接触", "旧 Holdout，已被 FULL_SWEEP 消耗，37 笔 +234.38U"),
    ("S6", "2026-10-14", "2026-10-18", "未接触记录", "样本量不足：约 5 天"),
    ("S7", "2026-10-19", "2099-01-01", "无数据", "尚无数据"),
]


def pdt(s):
    if not s:
        return None
    s = s.strip().rstrip("Z")
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def D(x):
    return x.date() if isinstance(x, dt.datetime) else x


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    cov = list(csv.DictReader(open(os.path.join(OUT, "01_数据覆盖_逐币.csv"), encoding="utf-8-sig")))
    contact = list(csv.DictReader(open(os.path.join(OUT, "02_接触区间_逐币.csv"), encoding="utf-8-sig")))
    qual = list(csv.DictReader(open(os.path.join(OUT, "03_区间资格表.csv"), encoding="utf-8-sig")))
    runs = list(csv.DictReader(open(os.path.join(OUT, "02_接触运行索引.csv"), encoding="utf-8-sig")))

    # 逐币接触区间
    cby = defaultdict(list)
    for r in contact:
        cby[r["symbol"]].append((D(pdt(r["区间起"])), D(pdt(r["区间止"]))))

    # 逐币运行窗口（用于段级是否被该币跑过）
    runby = defaultdict(list)
    for r in runs:
        runby[r["symbol"]].append((D(pdt(r["start"])), D(pdt(r["end"]))))

    seg_rows = []
    for code, a, b, seg_contact, note in SEGMENTS:
        s, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        n_4h = n_oi = n_warm = n_run = 0
        for c in cov:
            f4, l4 = pdt(c["4h_首根"]), pdt(c["4h_末根收盘"])
            foi, loi = pdt(c["OI_首"]), pdt(c["OI_末"])
            if not f4 or not l4:
                continue
            if not (D(f4) <= s and D(l4) >= e):
                continue
            n_4h += 1
            if not (foi and loi and D(foi) <= s and D(loi) >= e):
                continue
            n_oi += 1
            if s < D(f4 + dt.timedelta(hours=WARMUP_H)):
                continue
            n_warm += 1
            sym = c["symbol"]
            if any(x <= e and y >= s for x, y in runby.get(sym, [])):
                n_run += 1
        seg_rows.append({
            "段": code, "起": a, "止": b,
            "日历天数": (e - s).days + 1,
            "段级接触": seg_contact, "段级说明": note,
            "4h覆盖币数": n_4h, "OI覆盖币数": n_oi, "预热达标币数": n_warm,
            "其中该段有运行记录币数": n_run,
            "数据合格且无运行记录币数": n_warm - n_run,
            "判定": ("不可用：数据不合格" if n_warm == 0 else
                     ("不可用：数据合格币全部已有运行记录" if n_warm - n_run == 0 else
                      "数据合格但无该币运行记录；仍受段级接触约束")),
        })
    p4 = os.path.join(OUT, "04_段级资格汇总.csv")
    with open(p4, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(seg_rows[0].keys()))
        w.writeheader()
        w.writerows(seg_rows)

    # 段级结论
    cnt = Counter(r["接触分层"] for r in qual)
    cand = [r for r in qual if r["区间类型"] == "未接触候选"]
    data_ok = [r for r in cand if r["满足200根预热"] == "是" and r["满足30天"] == "是"
               and r["OI覆盖"] == "是" and r["真实mark可用"] == "是"]
    usable = [r for r in cand if r["接触分层"] in ("②", "③")]
    long_ok = [r for r in usable if r["满足12个月"] == "是"]
    gaps = [r for r in cand if r["接触分层"] == "④"]
    max_gap = max((int(r["天数"]) for r in gaps), default=0)

    verdict = {
        "工单": "FT-VALIDATION-DATA-01",
        "生成时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "给谁": "hharness（独立验证编排）",
        "一句话结论": ("可用作独立验证的区间数 = 0。全库 528 个标的中，凡数据满足"
                       "「4h 200 根预热 + 1h/15m + OI 5m 覆盖」的区间，全部已有运行记录（①）；"
                       "没有任何一段是「明确封存且尚未用于研究」（③）；"
                       "无运行记录的残余区间要么落在已整体扫描的日历段（④），要么数据不足（⑤）。"),
        "合格独立验证区间数": len(long_ok),
        "分层计数": dict(sorted(cnt.items())),
        "分母": {
            "标的数": len(cov),
            "区间行数": len(qual),
            "已接触区间数": cnt.get("①", 0),
            "未接触候选区间数": len(cand),
            "未接触候选中数据合格数": len(data_ok),
            "唯一运行窗口数": len(runs),
        },
        "为什么是 0": [
            "① 已接触 %d 段（占区间表 %.0f%%）：FULL_SWEEP 的 200 个 365 天窗口并集 = 2021-01-01~2026-10-13 连续无洞，"
            "叠加 BACKTEST-LOSS、保本/缩量/联合母集、70 白名单 9 格研究，528 个标的全部至少有一个运行窗口；"
            "其中数据条件全过且满 12 个月的有 %d 段。" % (
                cnt.get("①", 0), 100.0 * cnt.get("①", 0) / len(qual),
                len([r for r in qual if r["接触分层"] == "①" and r["OI覆盖"] == "是"
                     and r["真实mark可用"] == "是" and r["满足12个月"] == "是"])),
            "② 仅完整性检查 0 段：数据合格的未接触区间 %d 个，全部落在已整体扫描的日历段（S4/S5），"
        "因此归入 ④ 而不是 ② —— 它们不是「只做过完整性检查」，而是「同一段行情已被研究过」。" % len(data_ok),
            "③ 明确封存未研究 0 段：旧 Holdout（2026-04-15~2026-10-15）已被 FULL_SWEEP 消耗（37 笔唯一交易、+234.38U），"
            "v3.1 的 val 目录止于 2026-04-15 不含留出期，因此不存在封存段。" ,
            "④ 接触历史不清楚 %d 段：该币无运行记录，但区间落在 S4/S5 已扫描日历段内，最长 %d 天，"
            "按「无证据不默认未污染」不得视为干净。" % (len(gaps), max_gap),
            "⑤ 数据不足 %d 段：54 段长度只有 1~28 天；1 段止于 2022 年前、无真实结算 mark（BTCUSDT 2020-09-01~2020-12-31）；"
        "1 段 4h 不足 200 根预热（CTUSDT 2026-10-01 才上市）。" % cnt.get("⑤", 0),
        ],
        "数据条件核对": {
            "200根4h预热": "config.py: atr_warmup_bars=200；800 小时。runner.py:297-300 只回取 220 根，"
                          "数据不足时静默降级为「有多少用多少」⇒ 预热不足不会报错，只会静默失真。",
            "预热合规性": "21,549 个运行窗口中 4,607 个（21.4%）起点早于该币预热完成点，涉及 467 币；"
                        "这些窗口的 ATR 预热不足，结果不应与达标窗口等量齐观。",
            "OI": "5m 持仓量齐全（528/528 币有文件），但全库 128,566,270 行 OI 的 fetched_at 全为空 ⇒ 无发布时点证据。",
            "OI_PIT结论": "只能声明计算上不超前（桶起点+5m≤决策时刻），不能声明已证明无未来函数；OI 因子正式 PIT 资格 = 0。",
            "funding": "真实结算事件存在；mark 最早 2022-01-01（G3），2021 段资金费无法按真实口径计算。",
        },
        "不能据此做的事": [
            "不能把 ④ 档区间当样本外——它们与已研究期同段。",
            "不能把旧 Holdout 改名复用。",
            "不能宣称策略有效或无效——本单只回答「有没有干净数据」。",
        ],
        "建议的固定起点前向方案（未启动）": {
            "性质": "只提方案，不采集、不模拟、不交易。",
            "固定起点": "2026-11-01T00:00:00Z（本单之后第一个整月起点，避免用本单已看过的 10 月残段）",
            "预热": "起点前需 200 根 4h（≈33.3 天）已闭合，即数据自 2026-09-28 起连续",
            "币池": "按冻结规则 §6 原文：每日 UTC 00:00 取此前完整 30 天平均日成交额前 50，上市满 90 天，排除指数合约",
            "最小样本": "12 个月 + 100 笔已结束交易 + 10 个实际交易币种",
            "判定": "一次性评估，事前写死失败条件（净收益≤0 / PF<1.20 / 回撤>10% / 重采样下界≤0 / 压力成本≤0）",
            "起算前置": "G1（OI 发布时点）、G2（历史合约规格 0 verified）、G4（既有运行非冻结币池）未闭环前不得起算",
            "预计等待": "按既有密度 62 笔/年，达到 100 笔约 19~24 个月",
        },
        "证据路径": {
            "逐币数据覆盖": "out/01_数据覆盖_逐币.csv",
            "运行索引": "out/02_接触运行索引.csv",
            "逐币接触区间": "out/02_接触区间_逐币.csv",
            "研究母集接触": "out/02_研究接触_区间.csv",
            "区间资格表": "out/03_区间资格表.csv",
            "段级汇总": "out/04_段级资格汇总.csv",
            "OI发布时点": "out/04_OI发布时点探针.json",
        },
    }
    p5 = os.path.join(OUT, "05_hharness_资格结论.json")
    with open(p5, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, ensure_ascii=False, indent=2)

    # 自检
    checks = []
    checks.append(("区间表行数 = ①+④+⑤ 之和",
                   len(qual) == sum(cnt.values()), "%d = %s" % (len(qual), dict(cnt))))
    checks.append(("① 段全部有运行数>0", all(int(r["合并运行数"]) > 0 for r in qual if r["接触分层"] == "①"), ""))
    checks.append(("未接触候选 全部无运行数", all(int(r["合并运行数"]) == 0 for r in cand), ""))
    checks.append(("③ 档为 0", cnt.get("③", 0) == 0, str(cnt.get("③", 0))))
    checks.append(("合格独立验证区间 = 0", len(long_ok) == 0, str(len(long_ok))))
    checks.append(("④ 档全部数据合格（OI 覆盖=是 且 天数>=30）",
                   all(r["OI覆盖"] == "是" and int(r["天数"]) >= 30 for r in gaps), ""))
    checks.append(("⑤ 档全部不满足数据条件",
                   all((int(r["天数"]) < 30 or r["OI覆盖"] != "是" or r["满足200根预热"] != "是"
                        or r["真实mark可用"] != "是")
                       for r in cand if r["接触分层"] == "⑤"), ""))
    checks.append(("④ 档数量 = 数据合格未接触区间数", len(gaps) == len(data_ok), "%d vs %d" % (len(gaps), len(data_ok))))
    checks.append(("段级汇总 S5 预热达标币数>0 且无运行记录币数=0",
                   any(r["段"] == "S5" and int(r["预热达标币数"]) > 0 and int(r["数据合格且无运行记录币数"]) == 0
                       for r in seg_rows), ""))
    checks.append(("标的数 = 528", len(cov) == 528, str(len(cov))))
    checks.append(("OI 探针：全库空值占比 = 1.0",
                   json.load(open(os.path.join(OUT, "04_OI发布时点探针.json"), encoding="utf-8"))["空值占比"] == 1.0, ""))
    res = {
        "检查项数": len(checks),
        "通过": sum(1 for _, ok, _ in checks if ok),
        "明细": [{"检查": n, "通过": bool(ok), "备注": note} for n, ok, note in checks],
    }
    with open(os.path.join(OUT, "06_自检结果.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    for n, ok, note in checks:
        print(("PASS " if ok else "FAIL ") + n + ("  " + note if note else ""))

    # 哈希清单
    rows = []
    for dirpath, _, files in os.walk(TICKET):
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, TICKET)
            if rel in ("05_文件清单与哈希.csv",):
                continue
            rows.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p)})
    with open(os.path.join(TICKET, "05_文件清单与哈希.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "bytes", "sha256"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["path"]))
    print("产物 %d 个，已写哈希清单" % len(rows))


if __name__ == "__main__":
    main()
