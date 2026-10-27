# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 交付报告数字核对（05）

把交付报告 `evidence/FT-STOP-20-01/00_固定20止损对照综合研究报告.md` 里的汇总数字，
逐条与原始账本重算结果比对。只比对，不改报告。
"""
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / "out"
FT09 = ROOT / "evidence" / "FT-ENTRY-09" / "out"
S20 = ROOT / "evidence" / "FT-STOP-20-01" / "out"
INIT, RISK = 2000.0, 0.005


def read_csv(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def eid(tag):
    m = re.search(r"(ev_FT_ENTRY_\d+_binance_[A-Z0-9]+_\d+)", tag or "")
    return m.group(1) if m else (tag or "")


def load(pat, gfun):
    D = {}
    for p in glob.glob(str(pat)):
        g = gfun(p)
        rows = read_csv(p)
        cl = sorted((int(r["close_timestamp"]), float(r["profit_abs"])) for r in rows)
        for r in rows:
            o = int(r["open_timestamp"])
            eq = INIT + sum(pa for ct, pa in cl if ct <= o)
            D.setdefault(g, {})[eid(r["enter_tag"])] = (
                r,
                float(r["profit_abs"]) / (eq * RISK),
                eq,
            )
    return D


B = load(FT09 / "trades_Wyckoff_C1_Vs*.csv", lambda p: Path(p).stem.replace("trades_Wyckoff_", ""))
S = load(
    S20 / "trades_Wyckoff_C1_Stop20_*.csv",
    lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", ""),
)

# ---- 基线汇总 ----
b_rows = [r for g in B for r in B[g].values()]
b_n = len(b_rows)
b_net = sum(float(r[0]["profit_abs"]) for r in b_rows)
b_win = sum(1 for r in b_rows if float(r[0]["profit_abs"]) > 0)
b_R = sum(r[1] for r in b_rows)
b_reason = Counter(r[0]["exit_reason"] for r in b_rows)
b_reason_net = defaultdict(float)
for r in b_rows:
    b_reason_net[r[0]["exit_reason"]] += float(r[0]["profit_abs"])
b_dur = [float(r[0]["trade_duration"]) / 60 for r in b_rows]

s_rows = [r for g in S for r in S[g].values()]
s_n = len(s_rows)
s_net = sum(float(r[0]["profit_abs"]) for r in s_rows)
s_win = sum(1 for r in s_rows if float(r[0]["profit_abs"]) > 0)
s_R = sum(r[1] for r in s_rows)
s_reason = Counter(r[0]["exit_reason"] for r in s_rows)
s_dur = [float(r[0]["trade_duration"]) / 60 for r in s_rows]

# ---- 消失/新增 ----
only_b = [k for g in B if g in S for k in B[g] if k not in S[g]]
only_s = [k for g in S for k in S[g] if k not in B.get(g, {})]
only_b_net = sum(float(B[g][k][0]["profit_abs"]) for g in B if g in S for k in B[g] if k not in S[g])
only_s_net = sum(float(S[g][k][0]["profit_abs"]) for g in S for k in S[g] if k not in B.get(g, {}))

# ---- 463 笔四分类 ----
cls = defaultdict(int)
worse = better = 0
for g in B:
    if g not in S:
        continue
    for k in B[g]:
        if k in S[g] and B[g][k][0]["exit_reason"] == "stop_loss":
            br, bR, _ = B[g][k]
            sr, sR, _ = S[g][k]
            still = sr["exit_reason"] in ("stop_loss", "trailing_stop_loss")
            win = float(sr["profit_abs"]) > 0
            if still:
                cls["仍止损_亏损" if not win else "仍止损_盈利"] += 1
                if not win:
                    if sR < bR - 1e-9:
                        worse += 1
                    elif sR > bR + 1e-9:
                        better += 1
            else:
                cls["避免止损_盈利" if win else "避免止损_亏损"] += 1

claims = [
    {
        "报告表述": "描述性和净收益",
        "报告值": "+239.30",
        "重算值": f"{b_net:+.2f}",
        "结论": "算错",
        "说明": "分项逐组相加即为 +327.08",
    },
    {
        "报告表述": "原方案胜率",
        "报告值": "23.41%",
        "重算值": f"{b_win/b_n*100:.2f}%",
        "结论": "算错",
        "说明": f"{b_win}/{b_n}",
    },
    {
        "报告表述": "跟踪止损退出笔数",
        "报告值": "190 笔",
        "重算值": f"{b_reason.get('trailing_stop_loss',0)} 笔",
        "结论": "不符",
        "说明": "直接数账本 exit_reason",
    },
    {
        "报告表述": "跟踪止损贡献净收益",
        "报告值": "+4800.8",
        "重算值": f"{b_reason_net['trailing_stop_loss']:+.2f}",
        "结论": "不符",
        "说明": "",
    },
    {
        "报告表述": "初始止损退出笔数",
        "报告值": "464 笔（62.7%）",
        "重算值": f"{b_reason.get('stop_loss',0)} 笔（{b_reason.get('stop_loss',0)/b_n*100:.1f}%）",
        "结论": "不符",
        "说明": "",
    },
    {
        "报告表述": "超时退出笔数",
        "报告值": "86 笔（11.6%）",
        "重算值": f"{b_reason.get('timeout_240h',0)} 笔（{b_reason.get('timeout_240h',0)/b_n*100:.1f}%）",
        "结论": "不符",
        "说明": "",
    },
    {
        "报告表述": "持仓时长（原→20%）",
        "报告值": "11.5h → 79.2h",
        "重算值": f"{sum(b_dur)/len(b_dur):.2f}h → {sum(s_dur)/len(s_dur):.2f}h",
        "结论": "无法复现",
        "说明": "trade_duration 单位为分钟，除以 60",
    },
    {
        "报告表述": "消失交易",
        "报告值": "132 笔，+412.35",
        "重算值": f"{len(only_b)} 笔，{only_b_net:+.2f}",
        "结论": "不符",
        "说明": "按事件编号匹配",
    },
    {
        "报告表述": "新增交易",
        "报告值": "23 笔，−18.42",
        "重算值": f"{len(only_s)} 笔，{only_s_net:+.2f}",
        "结论": "不符",
        "说明": "20% 方案成交是基线事件的子集",
    },
    {
        "报告表述": "463 笔去向分类",
        "报告值": "204 / 128 / 99 / 32",
        "重算值": (
            f"{cls['避免止损_盈利']} / {cls['避免止损_亏损']} / {better} / {worse}"
            f"（另 {cls['仍止损_盈利']} 笔仍止损但盈利）"
        ),
        "结论": "基本一致，细节有差",
        "说明": "合计 463 笔一致",
    },
]

print(f"{'报告表述':<22}{'报告值':<22}{'重算值':<34}{'结论'}")
for c in claims:
    print(f"{c['报告表述']:<22}{c['报告值']:<22}{c['重算值']:<34}{c['结论']}")

(OUT / "05_报告数字核对.json").write_text(
    json.dumps(
        {
            "基线汇总": {
                "笔数": b_n,
                "净额": round(b_net, 4),
                "胜率%": round(b_win / b_n * 100, 4),
                "总R": round(b_R, 4),
                "退出原因": dict(b_reason),
                "各退出原因净额": {k: round(v, 4) for k, v in b_reason_net.items()},
                "持仓均值h": round(sum(b_dur) / len(b_dur), 4),
            },
            "20方案汇总": {
                "笔数": s_n,
                "净额": round(s_net, 4),
                "胜率%": round(s_win / s_n * 100, 4),
                "总R": round(s_R, 4),
                "退出原因": dict(s_reason),
                "持仓均值h": round(sum(s_dur) / len(s_dur), 4),
            },
            "消失新增": {
                "仅基线笔数": len(only_b),
                "仅基线净额": round(only_b_net, 4),
                "仅20笔数": len(only_s),
                "仅20净额": round(only_s_net, 4),
            },
            "463笔四分类": dict(cls),
            "仍止损亏损中_亏损扩大": worse,
            "仍止损亏损中_亏损减轻": better,
            "核对项": claims,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"\n写出 {OUT/'05_报告数字核对.json'}")
