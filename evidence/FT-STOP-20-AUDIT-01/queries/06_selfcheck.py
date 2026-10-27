# -*- coding: utf-8 -*-
"""FT-STOP-20-AUDIT-01 自检（06）

不信自报汇总：把报告里的每一个关键数字重新从原始账本算一遍，逐项断言。
任何一项不成立即 FAIL，不隐藏。
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
            D.setdefault(g, {})[eid(r["enter_tag"])] = (r, float(r["profit_abs"]) / (eq * RISK))
    return D


B = load(FT09 / "trades_Wyckoff_C1_Vs*.csv", lambda p: Path(p).stem.replace("trades_Wyckoff_", ""))
S = load(
    S20 / "trades_Wyckoff_C1_Stop20_*.csv",
    lambda p: "C1_" + Path(p).stem.replace("trades_Wyckoff_C1_Stop20_", ""),
)

results = []


def check(name, cond, detail=""):
    results.append({"项": name, "结果": "PASS" if cond else "FAIL", "说明": detail})


# 1 组数齐全
check("基线 9 组齐全", len(B) == 9, f"实际 {len(B)}")
check("20% 方案 9 组齐全", len(S) == 9, f"实际 {len(S)}")

# 2 笔数
b_n = sum(len(B[g]) for g in B)
s_n = sum(len(S[g]) for g in S)
check("基线 740 笔", b_n == 740, f"实际 {b_n}")
check("20% 方案 631 笔", s_n == 631, f"实际 {s_n}")

# 3 净额
b_net = sum(float(r[0]["profit_abs"]) for g in B for r in B[g].values())
s_net = sum(float(r[0]["profit_abs"]) for g in S for r in S[g].values())
check("基线净额 +327.07", abs(b_net - 327.07) < 0.01, f"实际 {b_net:.4f}")
check("20% 净额 −349.04", abs(s_net + 349.04) < 0.01, f"实际 {s_net:.4f}")

# 4 总 R
b_R = sum(r[1] for g in B for r in B[g].values())
s_R = sum(r[1] for g in S for r in S[g].values())
check("基线总 R +37.97", abs(b_R - 37.97) < 0.05, f"实际 {b_R:.4f}")
check("20% 总 R −34.54", abs(s_R + 34.54) < 0.05, f"实际 {s_R:.4f}")
check("R 变化 −72.51", abs((s_R - b_R) + 72.51) < 0.1, f"实际 {s_R-b_R:.4f}")

# 5 胜率
b_win = sum(1 for g in B for r in B[g].values() if float(r[0]["profit_abs"]) > 0)
s_win = sum(1 for g in S for r in S[g].values() if float(r[0]["profit_abs"]) > 0)
check("基线胜率 23.11%", abs(b_win / b_n * 100 - 23.11) < 0.05, f"实际 {b_win/b_n*100:.4f}")
check("20% 胜率 50.08%", abs(s_win / s_n * 100 - 50.08) < 0.05, f"实际 {s_win/s_n*100:.4f}")

# 6 退出结构
b_reason = Counter(r[0]["exit_reason"] for g in B for r in B[g].values())
s_reason = Counter(r[0]["exit_reason"] for g in S for r in S[g].values())
check("基线止损 544", b_reason["stop_loss"] == 544, f"实际 {b_reason['stop_loss']}")
check("基线跟踪 176", b_reason["trailing_stop_loss"] == 176, f"实际 {b_reason['trailing_stop_loss']}")
check("基线超时 20", b_reason["timeout_240h"] == 20, f"实际 {b_reason['timeout_240h']}")
check("20% 超时 466", s_reason["timeout_240h"] == 466, f"实际 {s_reason['timeout_240h']}")
check("20% 止损 134", s_reason["stop_loss"] == 134, f"实际 {s_reason['stop_loss']}")

# 7 持仓时长（分钟→小时）
b_dur = [float(r[0]["trade_duration"]) / 60 for g in B for r in B[g].values()]
s_dur = [float(r[0]["trade_duration"]) / 60 for g in S for r in S[g].values()]
check("基线持仓均值 44.89h", abs(sum(b_dur) / len(b_dur) - 44.89) < 0.05, f"实际 {sum(b_dur)/len(b_dur):.4f}")
check(
    "20% 持仓均值 204.84h",
    abs(sum(s_dur) / len(s_dur) - 204.84) < 0.05,
    f"实际 {sum(s_dur)/len(s_dur):.4f}",
)

# 8 仅基线/仅20%
only_b = [k for g in B if g in S for k in B[g] if k not in S[g]]
only_s = [k for g in S for k in S[g] if k not in B.get(g, {})]
check("仅基线 109 笔", len(only_b) == 109, f"实际 {len(only_b)}")
check("仅20 方案 0 笔", len(only_s) == 0, f"实际 {len(only_s)}")

# 9 R 分解三行
agg = defaultdict(lambda: [0, 0.0, 0.0])
for g in B:
    if g not in S:
        continue
    for k in B[g]:
        if k in S[g]:
            a = agg[B[g][k][0]["exit_reason"]]
            a[0] += 1
            a[1] += B[g][k][1]
            a[2] += S[g][k][1]
check("止损组 463 笔", agg["stop_loss"][0] == 463, f"实际 {agg['stop_loss'][0]}")
check(
    "止损组 R 变化 +363.33",
    abs((agg["stop_loss"][2] - agg["stop_loss"][1]) - 363.33) < 0.1,
    f"实际 {agg['stop_loss'][2]-agg['stop_loss'][1]:.4f}",
)
check(
    "跟踪组 R 变化 −364.56",
    abs((agg["trailing_stop_loss"][2] - agg["trailing_stop_loss"][1]) + 364.56) < 0.1,
    f"实际 {agg['trailing_stop_loss'][2]-agg['trailing_stop_loss'][1]:.4f}",
)

# 10 逐笔核对结论
aud = json.loads((OUT / "04_逐笔核对.json").read_text(encoding="utf-8"))["汇总"]
check("逐笔核对 631 笔", aud["核对笔数"] == 631, f"实际 {aud['核对笔数']}")
check("止损价 631/631", aud["a_止损价不低于-20%"] == 631, f"实际 {aud['a_止损价不低于-20%']}")
check("2R 合规 631/631", aud["d_2R合规"] == 631, f"实际 {aud['d_2R合规']}")
check("名义不超发 627/631", aud["b_名义不超发"] == 627, f"实际 {aud['b_名义不超发']}")
check("止损被上移 18 笔", aud["止损被上移笔数"] == 18, f"实际 {aud['止损被上移笔数']}")

# 11 机制耦合
coup = json.loads((OUT / "01_扫描结果.json").read_text(encoding="utf-8"))["机制耦合"]
check(
    "20% 方案名义占权益 2.5%",
    abs(coup["20方案_推导名义占权益%"] - 2.5) < 1e-6,
    f"实际 {coup['20方案_推导名义占权益%']}",
)
check(
    "仓位缩放 8.23 倍",
    abs(coup["仓位缩放倍数(现/20)"] - 8.2291) < 0.01,
    f"实际 {coup['仓位缩放倍数(现/20)']}",
)

# 12 报告数字核对项数量
cl = json.loads((OUT / "05_报告数字核对.json").read_text(encoding="utf-8"))
check("报告核对 10 项", len(cl["核对项"]) == 10, f"实际 {len(cl['核对项'])}")

# 13 关键文件存在
for f in ["00_报告.md", "01_扫描结果.json", "02_机制分析.json", "03_对照.json", "04_逐笔核对.json"]:
    p = ROOT / "evidence" / "FT-STOP-20-AUDIT-01" / f if f.endswith(".md") else OUT / f
    check(f"产物存在 {f}", p.exists(), str(p))

npass = sum(1 for r in results if r["结果"] == "PASS")
print(f"{'项':<34}{'结果':<8}{'说明'}")
for r in results:
    print(f"{r['项']:<34}{r['结果']:<8}{r['说明']}")
print(f"\n合计 {npass}/{len(results)} PASS")

(OUT / "06_自检.json").write_text(
    json.dumps({"通过": npass, "总数": len(results), "明细": results}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
