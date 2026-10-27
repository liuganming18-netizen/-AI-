# -*- coding: utf-8 -*-
"""FT-VALIDATION-DATA-01 / 03：币种 × 时间区间 的资格判定。

只用索引与元数据；不跑策略、不算收益、不读封存行情数值。

判定链
------
1) 数据可用起点 = max(4h 首根 + 200 根×4h 预热, 1h 首根, 15m 首根, OI 首)
   200 根 4h = 800 小时（config.py: atr_warmup_bars=200）
2) 数据可用终点 = min(4h 末根收盘, 1h 末根, 15m 末根, OI 末)
3) 接触区间 = 逐币运行记录 ∪ 研究母集/白名单区间（02 产出）
4) 段级接触 = 该区间是否落在已被整体扫描/研究的日历段（S2/S3/S4/S5）
   —— 段级接触优先于「该币无运行记录」：整体被扫过，就不能说该段没被看过
5) 未接触候选区间 = 数据可用区间 − 逐币接触区间
6) 分类（优先级从高到低）：
   ⑤ 数据不足/不满足策略条件 → ① 有运行记录 → ③ 明确封存未研究 →
   ② 无运行记录、无段级接触、但有完整性清单 → ④ 其余（接触历史不清楚）

输出：out/03_区间资格表.csv、out/03_资格结论.json
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(ROOT, "evidence/FT-VALIDATION-DATA-01/out")
V31 = os.path.join(ROOT, "数据/研究输入/WyckoffMaster_v31")

WARMUP_H = 200 * 4
MIN_DAYS = 30
FROZEN_MIN_DAYS = 365
MARK_FROM = dt.date(2022, 1, 1)   # G3：真实结算 mark 最早 2022-01-01（OOS-READY-01 探针）

# 既有证据（OOS-READY-01/out/02_验证数据资格表.csv、FT-RESEARCH-01/out/02_接触台账_逐段_v1.csv）
SEGMENTS = [
    ("S0", dt.date(2019, 12, 31), dt.date(2020, 8, 31), "未接触记录", "数据不合格：全池无 OI"),
    ("S1", dt.date(2020, 9, 1), dt.date(2020, 12, 31), "未接触记录", "数据不合格：仅 BTC 有 OI、无 mark"),
    ("S2", dt.date(2021, 1, 1), dt.date(2021, 11, 30), "已接触", "已扫描；OI 自 2021-12 起才全池可用"),
    ("S3", dt.date(2021, 12, 1), dt.date(2021, 12, 31), "已接触", "已扫描，6 笔唯一交易"),
    ("S4", dt.date(2022, 1, 1), dt.date(2026, 4, 14), "已接触", "主样本母集，253 笔唯一交易"),
    ("S5", dt.date(2026, 4, 15), dt.date(2026, 10, 13), "已接触", "旧 Holdout，已被 FULL_SWEEP 消耗，37 笔 +234.38U"),
    ("S6", dt.date(2026, 10, 14), dt.date(2026, 10, 18), "未接触记录", "样本量不足：约 5 天"),
    ("S7", dt.date(2026, 10, 19), dt.date(2099, 1, 1), "无数据", "尚无数据"),
]


def pdt(s: str):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def day(x):
    return x.date() if isinstance(x, dt.datetime) else x


def seg_of(s, e):
    """区间覆盖到的日历段（可能多段）。"""
    hits = []
    for code, a, b, contact, note in SEGMENTS:
        if s <= b and e >= a:
            hits.append((code, contact, note))
    return hits


def load_v31_symbols():
    p = os.path.join(V31, "逐币覆盖_v31.csv")
    is_syms, val_syms = set(), set()
    if os.path.isfile(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                sym = os.path.basename(r["path"]).replace(".parquet", "")
                if r["path"].startswith("is/"):
                    is_syms.add(sym)
                elif r["path"].startswith("val/"):
                    val_syms.add(sym)
    return is_syms, val_syms


def main():
    cov = list(csv.DictReader(open(os.path.join(OUT_DIR, "01_数据覆盖_逐币.csv"), encoding="utf-8-sig")))
    contact = list(csv.DictReader(open(os.path.join(OUT_DIR, "02_接触区间_逐币.csv"), encoding="utf-8-sig")))
    research = list(csv.DictReader(open(os.path.join(OUT_DIR, "02_研究接触_区间.csv"), encoding="utf-8-sig")))
    is_syms, val_syms = load_v31_symbols()

    cby = defaultdict(list)
    for r in contact:
        cby[r["symbol"]].append({
            "s": day(pdt(r["区间起"])), "e": day(pdt(r["区间止"])),
            "runs": int(r["合并运行数"]), "src": r["来源类型"],
            "cand": int(float(r["区间内候选"] or 0)), "tr": int(float(r["区间内成交"] or 0)),
        })
    for r in research:
        cby[r["symbol"]].append({
            "s": day(pdt(r["start"])), "e": day(pdt(r["end"])),
            "runs": 0, "src": "研究母集", "cand": 0, "tr": 0,
        })

    rows = []
    for c in cov:
        sym = c["symbol"]
        f4, l4 = pdt(c["4h_首根"]), pdt(c["4h_末根收盘"])
        f1, l1 = pdt(c["1h_首根"]), pdt(c["1h_末根"])
        f15, l15 = pdt(c["15m_首根"]), pdt(c["15m_末根"])
        foi, loi = pdt(c["OI_首"]), pdt(c["OI_末"])
        ffu = pdt(c["funding_首"])
        if not f4 or not l4:
            continue
        warm_done = f4 + dt.timedelta(hours=WARMUP_H)
        starts = [warm_done] + [x for x in (f1, f15, foi) if x]
        ends = [x for x in (l4, l1, l15, loi) if x]
        usable_s = day(max(starts))
        usable_e = day(min(ends))
        # OI 覆盖按日期比较（OI 末常比 4h 末根早几十分钟，不构成缺口）
        # OI / funding 的覆盖按「区间两端」判，不按全symbol判（不同区间起点差异很大）

        cons = sorted(cby.get(sym, []), key=lambda x: x["s"])
        merged = []
        for x in cons:
            if merged and x["s"] <= merged[-1]["e"] + dt.timedelta(days=1):
                merged[-1]["e"] = max(merged[-1]["e"], x["e"])
                merged[-1]["runs"] += x["runs"]
                merged[-1]["src"] = "|".join(sorted(set(merged[-1]["src"].split("|")) | set(x["src"].split("|"))))
                merged[-1]["cand"] += x["cand"]
                merged[-1]["tr"] += x["tr"]
            else:
                merged.append(dict(x))

        def emit(s, e, kind, src="", runs=0, cand=0, tr=0):
            segs = seg_of(s, e)
            oi_ok = bool(foi and loi and day(foi) <= s and day(loi) >= e)
            fund_ok = bool(ffu and day(ffu) <= s)
            rows.append({
                "symbol": sym, "区间起": s.isoformat(), "区间止": e.isoformat(),
                "天数": (e - s).days + 1, "区间类型": kind,
                "覆盖日历段": "/".join(x[0] for x in segs),
                "段级接触": "是" if any(x[1] == "已接触" for x in segs) else "否",
                "接触分层": "", "判定": "",
                "接触来源": src, "合并运行数": runs, "区间内候选": cand, "区间内成交": tr,
                "数据_4h首": c["4h_首根"], "数据_4h末": c["4h_末根收盘"],
                "预热完成点": warm_done.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "OI首": c["OI_首"], "OI末": c["OI_末"], "OI覆盖": "是" if oi_ok else "否",
                "funding首": c["funding_首"], "funding覆盖起点": "是" if fund_ok else "否",
                "真实mark可用": "是" if e >= MARK_FROM else "否",
                "满足200根预热": "是" if s >= day(warm_done) else "否",
                "满足30天": "是" if (e - s).days + 1 >= MIN_DAYS else "否",
                "满足12个月": "是" if (e - s).days + 1 >= FROZEN_MIN_DAYS else "否",
                "v31完整性清单": ("is+val" if sym in is_syms and sym in val_syms
                                 else "is" if sym in is_syms else "val" if sym in val_syms else "无"),
            })

        if usable_e < usable_s:
            # 预热完成点晚于数据终点：整段数据都不够预热，按实际数据区间记一行
            emit(day(f4), usable_e, "无可用区间")
            continue

        cur = usable_s
        for m in merged:
            s, e = max(m["s"], usable_s), min(m["e"], usable_e)
            if e < s:
                continue
            if s > cur:
                emit(cur, s - dt.timedelta(days=1), "未接触候选")
            emit(s, e, "已接触", m["src"], m["runs"], m["cand"], m["tr"])
            cur = max(cur, e + dt.timedelta(days=1))
        if cur <= usable_e:
            emit(cur, usable_e, "未接触候选")

    for r in rows:
        days = r["天数"]
        if r["区间类型"] == "已接触":
            r["接触分层"] = "①"
            r["判定"] = "已用于回测/观察/调参：%d 个运行窗口，候选 %d、成交 %d" % (
                r["合并运行数"], r["区间内候选"], r["区间内成交"])
            continue
        if r["区间类型"] == "无可用区间":
            r["接触分层"] = "⑤"
            r["判定"] = "数据不足：4h 预热完成点晚于数据终点"
            continue
        # 未接触候选
        if days < MIN_DAYS:
            r["接触分层"] = "⑤"
            r["判定"] = "数据不足：仅 %d 天，低于 30 天最小可评估长度" % days
        elif r["真实mark可用"] != "是":
            r["接触分层"] = "⑤"
            r["判定"] = "不满足策略条件：区间止于 2022-01-01 前，无真实结算 mark，资金费无法按真实口径计算"
        elif r["OI覆盖"] != "是":
            r["接触分层"] = "⑤"
            r["判定"] = "不满足策略条件：OI 5m 未覆盖区间两端（OI 门槛必然 oi_unavailable）"
        elif r["满足200根预热"] != "是":
            r["接触分层"] = "⑤"
            r["判定"] = "不满足策略条件：4h 不足 200 根预热"
        elif r["段级接触"] == "是":
            r["接触分层"] = "④"
            r["判定"] = ("接触历史不清楚：该币无运行记录，但区间落在已整体扫描/研究的日历段（%s），"
                         "按“无证据不默认未污染”不得视为干净" % r["覆盖日历段"])
        elif r["v31完整性清单"] != "无":
            r["接触分层"] = "②"
            r["判定"] = "仅被 v3.1 manifest 做过完整性检查（行数/首末/重复键/哈希），无运行记录、无段级接触"
        else:
            r["接触分层"] = "④"
            r["判定"] = "接触历史不清楚：无运行记录、无完整性清单，也无访问日志"

    p = os.path.join(OUT_DIR, "03_区间资格表.csv")
    cols = list(rows[0].keys())
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["symbol"], x["区间起"])):
            w.writerow(r)

    cnt = Counter(r["接触分层"] for r in rows)
    cand = [r for r in rows if r["区间类型"] == "未接触候选"]
    # 数据合格 = 四项硬条件全过（与分层无关，先判数据、再判接触）
    data_ok = [r for r in cand if r["满足200根预热"] == "是" and r["满足30天"] == "是"
               and r["OI覆盖"] == "是" and r["真实mark可用"] == "是"]
    usable = [r for r in cand if r["接触分层"] in ("②", "③")]
    long_ok = [r for r in usable if r["满足12个月"] == "是"]
    summary = {
        "工单": "FT-VALIDATION-DATA-01",
        "生成时间": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "币数": len({r["symbol"] for r in rows}),
        "区间行数": len(rows),
        "分层计数": dict(sorted(cnt.items())),
        "未接触候选区间数": len(cand),
        "未接触候选_数据合格数": len(data_ok),
        "未接触候选_数据合格且满12个月数": len([r for r in data_ok if r["满足12个月"] == "是"]),
        "数据合格候选中_仅完整性检查": len([r for r in data_ok if r["接触分层"] == "②"]),
        "数据合格候选中_明确封存未研究": len([r for r in data_ok if r["接触分层"] == "③"]),
        "数据合格候选中_段级接触": len([r for r in data_ok if r["接触分层"] == "④"]),
        "可自动启用的封存区间数": cnt.get("③", 0),
        "合格独立验证区间数": len(long_ok),
        "合格独立验证区间_说明": (
            "0。数据合格的未接触候选区间 %d 个，全部落在已整体扫描/研究的日历段（④：S4/S5），"
            "没有一个是“明确封存且未研究”（③），也没有一个满 12 个月；"
            "且 OI 无发布时点证据，任何区间都不能宣称 PIT 隔离。" % len(data_ok)),
        "数据条件": {
            "预热": "200 根 4h = 800 小时（config.py atr_warmup_bars=200；runner.py 只回取 220 根，数据不足时静默降级）",
            "OI": "5m 持仓量，须覆盖区间两端；全库 OI 的 fetched_at 为空 ⇒ 无发布时点证据",
            "funding": "真实结算事件 + mark（mark 最早 2022-01-01）",
            "最小长度": "%d 天" % MIN_DAYS,
            "冻结规则最低样本": "12 个月 + 100 笔已结束交易 + 10 个实际交易币种",
        },
        "候选区间明细": [
            {k: r[k] for k in ("symbol", "区间起", "区间止", "天数", "接触分层", "覆盖日历段", "判定")}
            for r in sorted(cand, key=lambda x: (-x["天数"], x["symbol"]))
        ],
    }
    with open(os.path.join(OUT_DIR, "03_资格结论.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in summary.items() if k != "候选区间明细"}, ensure_ascii=False, indent=2))
    print("写出 %s（%d 行）" % (p, len(rows)))


if __name__ == "__main__":
    main()
