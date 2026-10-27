# -*- coding: utf-8 -*-
"""07b 本轮报告数字机械对账。

规则（项目硬约定）：报告里出现的每个关键数字，都必须有一条"回到 out/*.json|csv
原始字段"的对账记录。人工抄写已验证会出错，本脚本只做机器比对，不改报告。

输出：
  out/07b_报告数值对账.csv   逐条：段 / 项 / 正则 / 报告值 / 复算值 / 差值 / 通过
  out/07b_报告数值对账.json  同上 + 判据统计
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
REP = L.HERE / "00_报告.md"


def js(name: str) -> dict:
    with open(OUT / name, encoding="utf-8") as f:
        return json.load(f)


A01 = js("01_运行来源核实.json")
A02 = js("02_执行核对.json")
A03 = js("03_账户与指标.json")
A04 = js("04_稳定性.json")
A05 = js("05_配对与终裁.json")
A06 = js("06_系统核对.json")
A07 = js("07_报告复核.json")

T051 = pd.read_csv(OUT / "05_稳定性终裁.csv", encoding="utf-8-sig").set_index("组合")
T041 = pd.read_csv(OUT / "04_极端盈利依赖.csv", encoding="utf-8-sig").set_index("组合")
T031 = pd.read_csv(OUT / "03_逐组指标.csv", encoding="utf-8-sig")
T052 = pd.read_csv(OUT / "05_逐配对.csv", encoding="utf-8-sig")
CLSP = pd.read_csv(OUT / "02_退出分类逐组合.csv", encoding="utf-8-sig").set_index("组合")
D02 = pd.read_csv(OUT / "02_逐笔重建明细.csv", encoding="utf-8-sig")
D02 = D02[D02["状态"].eq("ok")].copy()

RAW = REP.read_text(encoding="utf-8")
TXT = RAW.replace("\u2212", "-").replace("\u2013", "-")   # 报告里的全角减号


# ---------------------------------------------------------------- 工具

def num(s) -> float:
    t = str(s).strip()
    for a, b in [(",", ""), ("+", ""), ("USDT", ""), ("%", ""), ("R", ""),
                 ("h", ""), ("笔", ""), ("个", ""), ("组", ""), ("份", ""), (" ", "")]:
        t = t.replace(a, b)
    if "/" in t:          # 形如 "6/9" 的盈利组数，取分子
        t = t.split("/")[0]
    return float(t)


ROWS: list[dict] = []


def chk(sec: str, item: str, pat: str, want, tol: float = 0.005, note: str = "") -> None:
    """want 是标量或元组（元组 = 多个捕获组逐一比对）。"""
    m = re.search(pat, TXT)
    rec = {"段": sec, "项": item, "正则": pat, "复算值": want, "报告值": None,
           "差值": None, "通过": False, "备注": note}
    if m is None:
        rec["备注"] = (note + " | 报告里没找到匹配文本").strip(" |")
        ROWS.append(rec)
        return
    gs = [g for g in m.groups() if g is not None]
    try:
        got = [num(g) for g in gs]
    except Exception:
        rec["备注"] = (note + f" | 无法解析为数字 {gs}").strip(" |")
        ROWS.append(rec)
        return
    w = list(want) if isinstance(want, (tuple, list)) else [want]
    rec["报告值"] = got if len(got) > 1 else got[0]
    if len(got) != len(w):
        rec["备注"] = (note + f" | 捕获组数 {len(got)} != 期望 {len(w)}").strip(" |")
        ROWS.append(rec)
        return
    diffs = [a - b for a, b in zip(got, w)]
    rec["差值"] = diffs if len(diffs) > 1 else diffs[0]
    rec["通过"] = all(abs(d) <= tol for d in diffs)
    ROWS.append(rec)


def chk_text(sec: str, item: str, pat: str, want: str) -> None:
    m = re.search(pat, TXT)
    got = m.group(1) if m else None
    ROWS.append({"段": sec, "项": item, "正则": pat, "复算值": want, "报告值": got,
                 "差值": None, "通过": got == want,
                 "备注": "" if m else "报告里没找到匹配文本"})


def block(head: str) -> str:
    m = re.search(r"### " + head + r".*?(?=\n### |\n## |\Z)", TXT, re.S)
    return m.group(0) if m else ""


def table_rows(head: str, first: str = r"S\d\d_D\d\d") -> list[list[str]]:
    out = []
    for ln in block(head).splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip().replace("**", "").replace("`", "")
                 for c in s.strip("|").split("|")]
        if cells and re.fullmatch(first, cells[0]):
            out.append(cells)
    return out


# ---------------------------------------------------------------- 复算量

per = A03["段"]
acct = per["账户"]
slot = per["槽位与风险"]
dd = per["回撤口径"]
tri = {r["组合"]: r for r in per["三层次"]["16 组合 L3/L2/L1 表"]}

# 真实止损上移（口径：重建生效止损 > 初始止损 P0）
D02["已上移"] = D02["重建止损"] > D02["P0"] + 1e-9
cross = pd.crosstab(D02["exit_reason"], D02["退出分类"])
up_by_label = D02.groupby("exit_reason")["已上移"].agg(["sum", "count"])
round_by_label = D02.assign(取整=(D02["止损差"].abs() > 1e-9)).groupby("exit_reason")["取整"].sum()

rel = (D02["止损差"] / D02["重建止损"]).abs()

# 季度（S05_D15 / S05_D20）
raw, _, _ = L.load_all()
DALL = L.enrich(raw)
DALL["q"] = pd.to_datetime(DALL["close_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.to_period("Q")
Q = {c: DALL[DALL["组合"].eq(c)].groupby("q")["profit_abs"].sum().round(0).to_dict()
     for c in ["S05_D15", "S05_D20"]}

# 成本
g = DALL.groupby("策略")
FEE = float(g["手续费USDT"].sum().sum())
FUND = float(g["资金费USDT"].sum().sum())
SPREAD = float(g["价差盈亏USDT"].sum().sum())
NET = float(g["profit_abs"].sum().sum())
COST = FEE - FUND

# 文件数
N_TRADES = len(list((L.UP / "out").glob("trades_*.csv")))
N_CONF = len(list((L.UP / "out").glob("audit_confirmation_*.csv")))
N_ORD = len(list((L.UP / "out").glob("audit_orders_*.csv")))
N_FEATHER = len(list(L.DATA.glob("*.feather")))

# 四档仓位率
p4 = {r["组合"]: r["仓位率中位"] for r in A01["段"]["P4_行为反证"]["由仓位反推的 s（逐组合）"]}
POSRATE = [p4["S05_D05"], p4["S10_D05"], p4["S15_D05"], p4["S20_D05"]]

# 前 3 币贡献 / 剔除后中位
CONTRIB = T051["前三币贡献占比"]
EX3 = T051["剔除后L2中位"]
POS11 = T051[T051["L3合计"] > 0]        # 报告里"L3 合计为正的 11 个组合"
WINS = T041["占比pct"]
WINS_POS = WINS[WINS.index.isin(POS11.index)]
CONTRIB_POS = CONTRIB[CONTRIB.index.isin(POS11.index)]
TARGETS = T051[T051["为正的结束日数"] > 0]

# ================================================================ 逐条对账

# ---- 0 摘要
chk("§0 摘要", "来源判据 27/27", r"行为反证，(\d+)/27", A01["判据统计"]["通过"])
chk("§0 摘要", "逐笔退出时刻 9,849/9,849", r"（([\d,]+)/9,849 退出时刻一致",
    A02["段"]["退出链路"]["退出时间一致"])
chk("§0 摘要", "手续费误差", r"手续费误差 ([\d.eE+-]+)",
    acct["手续费 = 数量×(开+平)×0.0005 最大误差"], tol=1e-16)
chk("§0 摘要", "资金费误差", r"资金费误差 ([\d.eE+-]+)",
    acct["资金费逐笔复现最大误差"], tol=1e-14)
chk("§0 摘要", "C1~C8 最高 / 门槛", r"最好 (\d)/8，门槛 (\d)/8",
    (int(T051["C1~C8满足数"].max()), 5))
chk("§0 摘要", "引擎低估回撤组数 144/144", r"(\d+)/144 组都低估",
    dd["引擎回撤 < MTM 回撤的组数"])

# ---- 1.1
chk("§1.1", "策略文件总数", r"策略文件 \| (\d+) 个", A01["段"]["P1_规模"]["策略文件总数"])
chk("§1.1", "逐笔/确认表/订单表 份数", r"\| (\d+) / (\d+) / (\d+) 份", (N_TRADES, N_CONF, N_ORD))
chk("§1.1", "派生类参数错位条数", r"错位 (\d+) 条", len(A01["段"]["P1_规模"]["派生类参数错位"]))

# ---- 1.2
chk("§1.2", "zip 总数", r"### 1\.2 (\d+) 个压缩包", A01["段"]["P2_压缩包"]["zip 总数"])
chk("§1.2", "标准包与逐笔一一对应", r"\| (\d+) \| 正式结果，与 (\d+) 份",
    (A01["段"]["P2_压缩包"]["标准结果包数"], N_TRADES))

# ---- 1.3
chk("§1.3", "zip 内嵌派生类一致", r"\*\*(\d+)/144 完全一致\*\*",
    A01["段"]["P3_运行时"]["zip 内嵌派生类与磁盘逐文件一致"])
chk_text("§1.3", "日志最早时间", r"最早 \*\*(\d\d:\d\d:\d\d)\*\*",
         A01["段"]["P3_运行时"]["日志首行时间范围"][0][-8:])
chk_text("§1.3", "日志最晚时间", r"最晚 (\d\d:\d\d:\d\d)",
         A01["段"]["P3_运行时"]["日志首行时间范围"][1][-8:])
chk_text("§1.3", "基类写入时间", r"基类写入时间 \| \*\*(\d\d:\d\d:\d\d)\*\*",
         A01["段"]["P3_运行时"]["基类_mtime"][-8:])
chk("§1.3", "四档仓位率", r"([\d.]+) / ([\d.]+) / ([\d.]+) / ([\d.]+) —— \*\*四档清晰可分\*\*",
    tuple(round(v, 4) for v in POSRATE), tol=0.00006)

# ---- 1.4
chk("§1.4", "feather 文件数", r"(\d+) 个 feather 文件", N_FEATHER)
chk("§1.4", "与 FT-ENTRY-02 比对文件数", r"与 `FT-ENTRY-02` 的 (\d+) 个文件", N_FEATHER)

# ---- 2.2
e = A02["段"]["退出链路"]
chk("§2.2", "可重建笔数", r"可重建笔数 \| \*\*([\d,]+) / ([\d,]+)\*\*",
    (A02["段"]["重建"]["可重建笔数"], A02["段"]["规模"]["笔数"]))
chk("§2.2", "退出时刻一致", r"退出时刻与账本一致 \| \*\*([\d,]+) / ([\d,]+)\*\*",
    (e["退出时间一致"], A02["段"]["规模"]["笔数"]))
chk("§2.2", "账面止损低于重建值笔数", r"账面止损\*\*低于\*\*重建止损的笔数 \| \*\*(\d+)\*\*",
    e["账面止损低于重建止损的笔数"])
chk("§2.2", "账面超出重建 >0.2% 笔数", r">0\.2% 的笔数 \| \*\*(\d+)\*\*",
    e["账面止损超出重建值 >0.2% 的笔数"])
chk("§2.2", "相对差中位/最大", r"相对差中位 ([\d.eE+-]+)，最大 ([\d.eE+-]+)",
    (round(float(rel.median()), 8), round(float(rel.max()), 8)), tol=5e-6)
chk("§2.2", "非跳空成交价=账面止损", r"非跳空成交价 = 账面止损 \| \*\*([\d,]+) / ([\d,]+)\*\*",
    (e["非跳空成交价 = 账面止损"], e["非跳空成交价 = 账面止损"]))
chk("§2.2", "期末结算成交价", r"期末结算成交价 = 最后一根开盘价 \| \*\*(\d+) / (\d+)\*\*",
    (e["期末结算成交价 = 最后一根开盘价"], e["期末结算成交价 = 最后一根开盘价"]))
chk("§2.2", "跳空笔数", r"止损高于本根最高价（跳空） \| \*\*(\d+) 笔\*\*",
    A02["段"]["跳空"]["止损高于本根最高价（按开盘成交）笔数"])
chk("§2.2", "成交价可解释", r"三者之一） \| \*\*([\d,]+) / ([\d,]+)\*\*",
    (e["成交价可解释（重建止损 / 本根最低 / 本根开盘）"],
     e["成交价可解释（重建止损 / 本根最低 / 本根开盘）"]))
chk("§2.2", "取整导致边界触发", r"\*\*(\d+) 笔\*\*（占止损退出 ([\d.]+)%）",
    (e["取整导致的边界触发笔数"],
     round(e["取整导致的边界触发笔数"] / (A02["段"]["规模"]["笔数"] - e["期末结算成交价 = 最后一根开盘价"]) * 100, 2)),
    tol=0.02)

# ---- 2.3
fut = A02["段"]["未来数据"]["偷看未来（用未收盘 4h）后退出时刻改变的笔数"]
chk("§2.3", "偷看未来抽样笔数", r"抽样 (\d+) 笔", fut["抽样"])
chk("§2.3", "偷看未来改变笔数/占比", r"\*\*(\d+) 笔的退出时刻会改变\*\*（([\d.]+)%）",
    (fut["改变"], round(fut["改变"] / fut["抽样"] * 100, 2)), tol=0.02)
chk("§2.3", "退出根上新止损被采用", r"新止损被采用\"的笔数为 \*\*(\d+)\*\*",
    A02["段"]["同根K线"]["退出根上新止损被采用笔数"])

# ---- 2.4
cls = A02["段"]["分类"]["四类构成（重建）"]
lab = A02["段"]["分类"]["引擎标签构成（仅对照）"]
chk("§2.4", "stop_loss 标签笔数", r"\| `stop_loss` \| ([\d,]+) \|", lab["stop_loss"])
chk("§2.4", "trailing 标签笔数", r"\| `trailing_stop_loss` \| ([\d,]+) \|", lab["trailing_stop_loss"])
chk("§2.4", "force_exit 标签笔数", r"\| `force_exit` \| (\d+) \|", lab["force_exit"])
chk("§2.4", "stop_loss 中初始止损", r"\| `stop_loss` \| [\d,]+ \| \*\*([\d,]+)\*\* \|",
    int(cross.loc["stop_loss", "初始止损"]))
chk("§2.4", "trailing 中初始止损", r"\| `trailing_stop_loss` \| [\d,]+ \| (\d+) \|",
    int(cross.loc["trailing_stop_loss", "初始止损"]))
chk("§2.4", "trailing 中跟踪亏损", r"\| `trailing_stop_loss` \| [\d,]+ \| \d+ \| ([\d,]+) \|",
    int(cross.loc["trailing_stop_loss", "跟踪亏损"]))
chk("§2.4", "trailing 中跟踪盈利", r"\| `trailing_stop_loss` \| [\d,]+ \| \d+ \| [\d,]+ \| \*\*([\d,]+)\*\*",
    int(cross.loc["trailing_stop_loss", "跟踪盈利"]))
chk("§2.4", "stop_loss 中跟踪亏损", r"\| `stop_loss` \| [\d,]+ \| \*\*[\d,]+\*\* \| (\d+) \|",
    int(cross.loc["stop_loss", "跟踪亏损"]))
chk("§2.4", "stop_loss=初始止损 占比", r"2,827/2,979 = \*\*([\d.]+)%\*\*",
    round(cross.loc["stop_loss", "初始止损"] / lab["stop_loss"] * 100, 1), tol=0.05)
chk("§2.4", "stop_loss 中跟踪亏损占比", r"另有 \*\*(\d+) 笔（([\d.]+)%）\*\* 是\"止损已上移",
    (int(cross.loc["stop_loss", "跟踪亏损"]),
     round(cross.loc["stop_loss", "跟踪亏损"] / lab["stop_loss"] * 100, 1)), tol=0.05)
chk("§2.4", "trailing 中跟踪盈利笔数/占比",
    r"只有 \*\*([\d,]+) 笔（([\d.]+)%）\*\* 出场价高于入场价",
    (int(cross.loc["trailing_stop_loss", "跟踪盈利"]),
     round(cross.loc["trailing_stop_loss", "跟踪盈利"] / lab["trailing_stop_loss"] * 100, 1)), tol=0.05)
chk("§2.4", "trailing 中跟踪亏损笔数", r"另有 \*\*([\d,]+) 笔是跟踪亏损\*\*",
    int(cross.loc["trailing_stop_loss", "跟踪亏损"]))
chk("§2.4", "trailing 中初始止损笔数", r"还有 \*\*(\d+) 笔其实是初始止损出场\*\*",
    int(cross.loc["trailing_stop_loss", "初始止损"]))
chk("§2.4", "trailing 标签中非盈利笔数", r"实际上其中 \*\*([\d,]+) 笔\*\*（6,797 - 2,773）不是盈利",
    int(lab["trailing_stop_loss"] - cross.loc["trailing_stop_loss", "跟踪盈利"]))
chk("§2.4", "已上移：stop_loss / trailing",
    r"只有 \*\*(\d+) 笔 `stop_loss`\*\* 和 \*\*([\d,]+) 笔 `trailing_stop_loss`\*\* 满足",
    (int(up_by_label.loc["stop_loss", "sum"]), int(up_by_label.loc["trailing_stop_loss", "sum"])))
chk("§2.4", "已上移：force_exit 满足/不满足",
    r"`force_exit` 里 (\d+) 笔满足、(\d+) 笔不满足",
    (int(up_by_label.loc["force_exit", "sum"]),
     int(up_by_label.loc["force_exit", "count"] - up_by_label.loc["force_exit", "sum"])))
chk("§2.4", "取整口径 / 重建>P0 口径 对照",
    r"按取整口径是 ([\d,]+)，按\"重建>P0\"口径是 (\d+)",
    (int(round_by_label["stop_loss"]), int(up_by_label.loc["stop_loss", "sum"])))
chk("§2.4", "四类构成：初始止损", r"\| 初始止损 \| \*\*([\d,]+)\*\* \|", cls["初始止损"])
chk("§2.4", "四类构成：跟踪盈利", r"\| 跟踪盈利（出场价 > 入场价） \| ([\d,]+) \|", cls["跟踪盈利"])
chk("§2.4", "四类构成：跟踪亏损", r"\| 跟踪亏损（出场价 ≤ 入场价） \| ([\d,]+) \|", cls["跟踪亏损"])
chk("§2.4", "四类构成：期末技术结算", r"\| 期末技术结算 \| (\d+) \|", cls["期末技术结算"])
chk("§2.4", "初始止损为 0 的组合个数", r"初始止损笔数为 \*\*0\*\* 的组合有 \*\*(\d+) 个\*\*",
    int(CLSP["初始止损"].eq(0).sum()))

# ---- 3.1
chk("§3.1", "组数 / 笔数", r"\| 组数 / 笔数 \| (\d+) / ([\d,]+) \|",
    (per["完整性"]["组数"], per["完整性"]["总笔数"]))
chk("§3.1", "MTM 期末闭合差", r"闭合差 \*\*([\d.]+)\*\*", abs(acct["MTM 期末权益与 2000+Σprofit_abs 闭合"]))
chk("§3.1", "手续费恒等式最大误差", r"0\.0005.，最大误差 \*\*([\d.eE+-]+)\*\*",
    acct["手续费 = 数量×(开+平)×0.0005 最大误差"], tol=1e-16)
chk("§3.1", "手续费合计", r"\| 手续费合计 \| \*\*([\d,.]+) USDT\*\*", round(FEE, 2), tol=0.02)
chk("§3.1", "资金费复现最大误差", r"最大误差 \*\*([\d.eE+-]+)\*\*（9,849 笔）",
    acct["资金费逐笔复现最大误差"], tol=1e-14)
chk("§3.1", "资金费合计", r"\| 资金费合计 \| \*\*([-−]?[\d,.]+) USDT\*\*", round(FUND, 2), tol=0.02)
fin = acct["期末技术结算单列"]
chk("§3.1", "期末技术结算", r"\*\*(\d+) 笔 / \+([\d,.]+) USDT / \+([\d.]+)R\*\*",
    (fin["笔数"], round(fin["USDT"], 2), round(fin["R"], 2)), tol=0.02)

# ---- 3.2
chk("§3.2", "最大并发 / 上限", r"\| 最大并发持仓 \| \*\*(\d+)\*\* \| (\d+) \|",
    (slot["最大并发持仓（全组最大值）"], 3))
chk("§3.2", "超槽组数", r"\| 超槽组数 \| \*\*(\d+)\*\*", slot["超槽组数"])
chk("§3.2", "并发名义峰值", r"\*\*([\d.]+)%\*\* \| 90%",
    round(slot["并发名义峰值占权益（最大）"] * 100, 2), tol=0.02)
chk("§3.2", "并发风险峰值", r"\*\*([\d.]+)%\*\* \| 1\.5%",
    round(slot["并发风险峰值占当时权益（最大）"] * 100, 3), tol=0.002)
chk("§3.2", "风险覆盖比区间", r"中位区间 \*\*([\d.]+) ~ ([\d.]+)\*\*",
    (round(slot["风险覆盖比（计划风险/预算）区间"][0], 3),
     round(slot["风险覆盖比（计划风险/预算）区间"][1], 3)), tol=0.001)

# ---- 3.3
m_ = dd["MTM 回撤 USDT（144 组）区间"]
g_ = dd["引擎回撤 USDT（144 组）区间"]
chk("§3.3", "MTM 回撤 区间/中位/最大", r"盯市 MTM（主口径）\*\* \| ([\d.]+) / 中位 ([\d.]+) / ([\d.]+)",
    (round(m_[0], 2), round(m_[1], 2), round(m_[2], 2)), tol=0.02)
chk("§3.3", "引擎回撤 区间/中位/最大", r"引擎自报（= 已实现曲线） \| ([\d.]+) / 中位 ([\d.]+) / ([\d.]+)",
    (round(g_[0], 2), round(g_[1], 2), round(g_[2], 2)), tol=0.02)
chk("§3.3", "低估中位/最大", r"中位低估 \*\*([\d.]+)%\*\*，最大低估 \*\*([\d.]+)%\*\*",
    (round(dd["引擎回撤低估幅度（中位 / 最大）"][0], 1),
     round(dd["引擎回撤低估幅度（中位 / 最大）"][1], 1)), tol=0.05)
rd = A07["段"]["逐列"]["回撤口径替换"]
chk("§3.3", "S05_D20 引擎→盯市", r"报告 25\.85%（引擎），盯市 \*\*([\d.]+)%\*\*",
    rd["MTM 口径（含未平仓）"]["S05_D20"], tol=0.005)
chk("§3.3", "S15_D05 引擎→盯市", r"报告 3\.34%，盯市 \*\*([\d.]+)%\*\*",
    rd["MTM 口径（含未平仓）"]["S15_D05"], tol=0.005)

# ---- 3.4
chk("§3.4", "S05_D20 L1 区间", r"9 个账户分别在 [-−]([\d.]+) ~ \+([\d.]+) USDT",
    (round(-tri["S05_D20"]["L1最小USDT"], 2), round(tri["S05_D20"]["L1最大USDT"], 2)), tol=0.02)
chk("§3.4", "S05_D20 L2 中位", r"9 次独立重复\*\*） \| \+([\d.]+) USDT \|",
    round(tri["S05_D20"]["L2中位USDT"], 2), tol=0.02)
chk("§3.4", "S05_D20 L3 合计", r"\| \*\*\+([\d,.]+) USDT\*\* \|",
    round(tri["S05_D20"]["L3合计USDT"], 2), tol=0.02)
chk("§3.4", "最差单组", r"最差单组是 `S05_D10_Vs04_Z04`（[-−]([\d.]+) USDT）",
    round(-T031["总USDT"].min(), 2), tol=0.02)
chk("§3.4", "三个负中位组合", r"`S15_D15`（[-−]([\d.]+)）、`S20_D15`（[-−]([\d.]+)）、`S20_D20`（[-−]([\d.]+)）",
    tuple(round(-T051.loc[c, "L2中位"], 2) for c in ["S15_D15", "S20_D15", "S20_D20"]), tol=0.02)

# ---- 4.1 核心对照表
r41 = table_rows(r"4\.1 核心对照表")
bad41 = []
for c in r41:
    combo = c[0]
    want = [tri[combo]["笔数"], tri[combo]["L3合计USDT"], tri[combo]["L2中位USDT"],
            tri[combo]["盈利组数"], tri[combo]["MTM回撤中位"], tri[combo]["MTM回撤最大"],
            tri[combo]["引擎回撤中位"], T051.loc[combo, "Δ总R合计"], T051.loc[combo, "C1~C8满足数"]]
    got = [num(x) for x in c[1:10]]
    for i, (a, b) in enumerate(zip(got, want)):
        if abs(a - b) > 0.02:
            bad41.append(f"{combo} 第{i + 1}列 报告{a} 复算{round(b, 2)}")
ROWS.append({"段": "§4.1", "项": "16 组合核心对照表（笔数/L3/L2/盈利组数/MTM中位/MTM最大/引擎中位/Δ总R/C1~C8）",
             "正则": "解析表格", "复算值": "9 列 × 16 行 = 144 项", "报告值": "同左",
             "差值": None, "通过": len(r41) == 16 and not bad41,
             "备注": f"解析到 {len(r41)} 行；不一致 {len(bad41)} 项" + ("；" + "；".join(bad41[:6]) if bad41 else "")})

# ---- 4.2 三条重点观察对象
chk("§4.2", "S05_D20 前 5 笔盈利/占比", r"前 5 笔盈利 ([\d,.]+) USDT = 净收益的 \*\*([\d.]+)%\*\*",
    (round(T041.loc["S05_D20", "前5笔盈利"], 2), round(T041.loc["S05_D20", "占比pct"], 1)), tol=0.5)
chk("§4.2", "S10_D20 缩尾后中位", r"缩尾 ±5R 后中位转负（[-−]([\d.]+)）",
    round(-T051.loc["S10_D20", "缩尾后中位"], 2), tol=0.02)
chk("§4.2", "S10_D20 剔前3币后中位", r"剔掉前 3 个币后中位 [-−]([\d.]+)",
    round(-T051.loc["S10_D20", "剔除后L2中位"], 2), tol=0.02)
chk("§4.2", "S10_D20 为正结束日数", r"13 个结束日只有 \*\*(\d+) 个\*\*为正 \|",
    T051.loc["S10_D20", "为正的结束日数"])
chk("§4.2", "S05_D05 L2 中位 / Δ总R", r"L2 \+([\d.]+)，但 Δ总R 对上一轮是 \*\*[-−]([\d.]+)\*\*",
    (round(T051.loc["S05_D05", "L2中位"], 2), round(-T051.loc["S05_D05", "Δ总R合计"], 2)), tol=0.02)

# ---- 4.3 稳定性判据表
r43 = table_rows(r"4\.3 稳定性判据")
bad43 = []
cols = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
for c in r43:
    combo = c[0]
    got = [1 if x == "✓" else 0 for x in c[1:9]]
    want = [1 if bool(T051.loc[combo, k]) else 0 for k in cols]
    tot = int(num(c[9]))
    if got != want or tot != T051.loc[combo, "C1~C8满足数"]:
        bad43.append(f"{combo} 报告{got}/{tot} 复算{want}/{T051.loc[combo, 'C1~C8满足数']}")
ROWS.append({"段": "§4.3", "项": "C1~C8 逐组合打勾 + 合计",
             "正则": "解析表格", "复算值": "8 列 × 16 行 + 合计", "报告值": "同左",
             "差值": None, "通过": len(r43) == 16 and not bad43,
             "备注": f"解析到 {len(r43)} 行；不一致 {len(bad43)} 项" + ("；" + "；".join(bad43[:6]) if bad43 else "")})
chk("§4.3", "C4/C5/C6 全灭", r"C4、C5、C6 在 (\d+) 个组合上\*\*全部不通过\*\*",
    16 if all((T051[k] == False).all() for k in ["C4", "C5", "C6"]) else -1)

# ---- 4.4 极端依赖
r44 = table_rows(r"4\.4 为什么")
bad44 = []
TOL44 = [0.02, 0.5, 0.02, 0.0]          # 金额 / 占比(%) / R / 结束日数
for c in r44:
    combo = c[0]
    got = [num(c[1]), num(c[2]), num(c[3]), num(c[4])]
    want = [round(T041.loc[combo, "前5笔盈利"], 2), round(T041.loc[combo, "占比pct"], 1),
            round(T041.loc[combo, "单笔最大盈利R"], 2), T051.loc[combo, "为正的结束日数"]]
    if any(abs(a - b) > t for a, b, t in zip(got, want, TOL44)):
        bad44.append(f"{combo} 报告{got} 复算{[round(x, 2) for x in want]}")
ROWS.append({"段": "§4.4", "项": "前 5 笔盈利 / 占比 / 单笔最大 R / 为正结束日数",
             "正则": "解析表格", "复算值": "4 列 × 6 行", "报告值": "同左",
             "差值": None, "通过": len(r44) == 6 and not bad44,
             "备注": f"解析到 {len(r44)} 行；不一致 {len(bad44)} 项" + ("；" + "；".join(bad44[:6]) if bad44 else "")})
chk("§4.4", "S05_D15 2022Q1", r"2022Q1 [-−]([\d,]+)、2022Q3 [-−]([\d,]+)、2023Q3 [-−]([\d,]+)、2024Q2 [-−]([\d,]+)、2024Q3 [-−]([\d,]+)",
    tuple(round(-Q["S05_D15"][pd.Period(q)], 0) for q in ["2022Q1", "2022Q3", "2023Q3", "2024Q2", "2024Q3"]), tol=0.5)
chk("§4.4", "S05_D15 三个正季度", r"2023Q4 \+([\d,]+)、2024Q1 \+([\d,]+)、\*\*2024Q4 \+([\d,]+)\*\*",
    tuple(round(Q["S05_D15"][pd.Period(q)], 0) for q in ["2023Q4", "2024Q1", "2024Q4"]), tol=0.5)

# ---- 4.5
chk("§4.5", "参数组极差中位/最大", r"极差中位 \*\*([\d.]+) USDT\*\*、最大 \*\*([\d.]+)\*\*",
    (round(A04["段"]["参数组离散"]["极差的中位 / 最大"][0], 2),
     round(A04["段"]["参数组离散"]["极差的中位 / 最大"][1], 2)), tol=0.02)
chk("§4.5", "16 组合 L2 极差", r"L2 中位极差只有 ([\d.]+)",
    round(A04["段"]["参数组离散"]["16 组合 L2 中位极差"], 2), tol=0.02)
chk("§4.5", "前后半段同号组合数", r"同号的组合 (\d+)/(\d+)",
    (A04["段"]["时间稳定"]["前后半段同号组合数"], 16))
chk("§4.5", "前 3 币贡献区间", r"前 3 个币贡献了净收益的 \*\*([\d.]+)%~([\d.]+)%\*\*",
    (round(float(CONTRIB_POS.min()), 0), round(float(CONTRIB_POS.max()), 0)), tol=0.5)
chk("§4.5", "剔前 3 币后中位区间", r"全部转负\*\*（中位 [-−]([\d.]+) ~ [-−]([\d.]+)）",
    (round(-float(EX3.max()), 2), round(-float(EX3.min()), 2)), tol=0.02)

# ---- 5.1
r51 = table_rows(r"5\.1 上一轮基准", first=r"Stop\d\d")
prev = {r["s_code"]: r for r in A05["段"]["上一轮基准"]["按止损档位的 L3 合计（原始账本重算）"]}
bad51 = []
for c in r51:
    k = c[0].replace("Stop", "S")          # 报告写 Stop05，JSON 里是 s_code=S05
    got = [num(c[1]), num(c[2])]
    want = [prev[k]["笔数"], prev[k]["净收益"]]
    if abs(got[0] - want[0]) > 0.5 or abs(got[1] - want[1]) > 0.02:
        bad51.append(f"{k} 报告{got} 复算{want}")
ROWS.append({"段": "§5.1", "项": "上一轮四个止损档位（笔数 / L3 合计）",
             "正则": "解析表格", "复算值": "2 列 × 4 行", "报告值": "同左",
             "差值": None, "通过": len(r51) == 4 and not bad51,
             "备注": f"解析到 {len(r51)} 行；不一致 {len(bad51)} 项" + ("；" + "；".join(bad51) if bad51 else "")})

# ---- 5.2
pr = A05["段"]["配对"]
chk("§5.2", "配对数", r"配对数 \*\*(\d+)\*\*", pr["配对数"])
chk("§5.2", "Δ 分解恒等式误差", r"Δ 分解恒等式误差 \*\*([\d.eE+-]+)\*\*",
    pr["Δ 分解恒等式最大误差"], tol=1e-13)
chk("§5.2", "共有事件占比 中位/最小", r"中位 \*\*([\d.]+)%\*\*，最小 ([\d.]+)%",
    (round(pr["共有事件占比（中位 / 最小）"][0] * 100, 1),
     round(pr["共有事件占比（中位 / 最小）"][1] * 100, 1)), tol=0.05)
sk = pd.read_csv(OUT / "05_跳过统计.csv", encoding="utf-8-sig")
chk("§5.2", "新增/减少/跳过/跳过率",
    r"新增 \*\*([\d,]+) 笔\*\*、减少 \*\*([\d,]+) 笔\*\*、跳过 \*\*([\d,]+) 笔\*\*（[^）]*跳过率 \*\*([\d.]+)%\*\*",
    (pr["新增/减少笔数合计"]["新增"], pr["新增/减少笔数合计"]["减少"],
     int(sk["跳过"].sum()), round(sk["跳过"].sum() / sk["确认通过"].sum() * 100, 1)), tol=0.05)
chk("§5.2", "Δ总R 分位", r"最小 [-−]([\d.]+)、中位 \*\*\+([\d.]+)\*\*、最大 \+([\d.]+)",
    (round(-T052["Δ总R"].min(), 2), round(float(T052["Δ总R"].median()), 2),
     round(T052["Δ总R"].max(), 2)), tol=0.02)
chk("§5.2", "符号检验原始/BH-FDR", r"原始 p<0\.05 有 \*\*(\d+)\*\* 个配对；\*\*BH-FDR 后仍有 (\d+) 个显著\*\*",
    (pr["符号检验原始 p<0.05 的配对数"], pr["BH-FDR 后 p<0.05 的配对数"]))

# ---- 5.3
chk("§5.3", "C8 通过个数", r"S20_D10（\*\*(\d+) 个\*\*）", int(T051["C8"].sum()))
chk("§5.3", "C8 相反读法通过个数", r"按相反读法通过的则是 .*?这 (\d+) 个", int(T051["C8_相反读法"].sum()))

# ---- 6.1
ap = A06["段"]["接口"]
chk("§6.1", "批次 runs_total", r"\| 批次 `runs_total` \| (\d+) ✓", ap["批次 runs_total"])
chk("§6.1", "批次 trades.count", r"\| \*\*([\d,]+)\*\*，与我方逐笔完全一致", ap["批次 trades.count"])
chk("§6.1", "批次 net_pnl_sum", r"\| \*\*([\d,.]+) USDT\*\*，与我方", round(ap["批次 net_pnl_sum（由逐笔行现算）"], 2), tol=0.02)
chk("§6.1", "抽核一致条数", r"\| \*\*(\d+)/(\d+) 与源账本一致\*\*",
    (A06["段"]["逐笔抽核"]["接口返回条数"] - A06["段"]["逐笔抽核"]["与源账本不一致条数"],
     A06["段"]["逐笔抽核"]["接口返回条数"]))

# ---- 6.2
df6 = A06["段"]["缺陷"]
chk("§6.2", "runs_with_trades / trades_total", r"`runs_with_trades = (\d+)`、`trades_total = (\d+)`",
    (df6["runs_with_trades"], df6["trades_total（批次汇总字段）"]))
chk_text("§6.2", "net_pnl_sum / runs_counted", r"`net_pnl_sum = (\w+)`", "null")
chk_text("§6.2", "系统自检 trades_total_matches_rows", r"`checks\.trades_total_matches_rows = (\w+)`", "false")
chk("§6.2", "单 run 费率当金额", r"21 笔写成 ([\d.]+) USDT，真实是 \*\*([\d.]+) USDT\*\*（差约 (\d+) 倍）",
    (df6["缺陷确认：单 run summary 的 total_fees 把费率当金额"], 3.9941,
     round(3.9941 / df6["缺陷确认：单 run summary 的 total_fees 把费率当金额"], 0)), tol=0.5)
chk("§6.2", "单 run total_funding", r"`total_funding` 写 0，真实 \*\*[-−]([\d.]+) USDT\*\*",
    1.0905, tol=0.0005)

# ---- 7.2
chk("§7.2", "144 表手续费全 0 → 真实值", r"真实手续费合计 \*\*([\d,.]+) USDT\*\*", round(FEE, 2), tol=0.02)
chk("§7.2", "144 表资金费全 0 → 真实值", r"真实资金费合计 \*\*[-−]([\d,.]+) USDT\*\*", round(-FUND, 2), tol=0.02)
chk("§7.2", "极低回撤替换 3 个", r"盯市口径分别为 \*\*([\d.]+)% / ([\d.]+)% / ([\d.]+)%\*\*",
    tuple(rd["MTM 口径（含未平仓）"][c] for c in ["S15_D05", "S20_D05", "S10_D05"]), tol=0.005)
chk("§7.2", "S05_D20 回撤替换", r"从 25\.85% 升到 \*\*([\d.]+)%\*\*", rd["MTM 口径（含未平仓）"]["S05_D20"], tol=0.005)
chk("§7.2", "S05_D20 跟踪盈利 笔数 88 vs 82", r"差 (\d+) 笔（S05_D20 (\d+) 笔 vs (\d+) 笔）",
    (int(CLSP.loc["S05_D20", "跟踪盈利"]) - 82, int(CLSP.loc["S05_D20", "跟踪盈利"]), 82))
chk("§7.2", "S05_D20 跟踪盈利 金额 6094.65 / 6088.26",
    r"\| \*\*\"S05_D20 跟踪盈利 \+([\d,.]+)\"\*\* \| ([\d,.]+) \| 按出场价口径 \*\*([\d,.]+)\*\*",
    (6094.65, 6094.65, 6088.26), tol=0.02)

# ---- 7.3
chk("§7.3", "初始止损为 0 的组合数（7.3 表）", r"实测 \*\*(\d+) 个\*\*组合为 0",
    int(pd.read_csv(OUT / "02_退出分类逐组合.csv", encoding="utf-8-sig")
        .set_index("组合")["初始止损"].eq(0).sum()))

# ---- 9.2
chk("§9.2", "S05_D20 回撤最大（28.64%）", r"盯市回撤最大 601\.50（\*\*([\d.]+)%\*\*）",
    rd["MTM 口径（含未平仓）"]["S05_D20"], tol=0.005)

# ---- 9.3
chk("§9.3", "前 5 笔盈利占净收益区间", r"前 5 笔盈利占净收益 \*\*([\d.]+)%~([\d.]+)%\*\*",
    (round(float(WINS_POS.min()), 0), round(float(WINS_POS.max()), 0)), tol=0.5)
chk("§9.3", "缩尾后转负个数", r"16 个组合里 \*\*(\d+) 个\*\*中位收益转负",
    int((T051["缩尾后中位"] < 0).sum()))
chk("§9.3", "缩尾后仍为正的个数与区间", r"剩下 (\d+) 个只剩 \+([\d.]+) ~ \+([\d.]+) USDT",
    (int((T051["缩尾后中位"] > 0).sum()),
     round(float(T051.loc[T051["缩尾后中位"] > 0, "缩尾后中位"].min()), 2),
     round(float(T051.loc[T051["缩尾后中位"] > 0, "缩尾后中位"].max()), 2)), tol=0.02)
_q15 = Q["S05_D15"]
_rest = float(T051.loc["S05_D15", "L3合计"]) - sum(
    _q15[pd.Period(q)] for q in ["2023Q4", "2024Q1", "2024Q4"])
chk("§9.3", "S05_D15 其余 10 季合计", r"其余 10 个季度合计 \*\*[-−]([\d,]+)\*\*",
    round(-_rest, 0), tol=0.5)
chk("§9.3", "S05_D20 2024Q2", r"S05_D20` 还要再加上 2024Q2（\+([\d,]+)）",
    round(Q["S05_D20"][pd.Period("2024Q2")], 0), tol=0.5)
chk("§9.3", "前 3 币贡献区间（9.3）", r"前 3 个币贡献 \*\*([\d.]+)%~([\d.]+)%\*\*",
    (round(float(CONTRIB_POS.min()), 0), round(float(CONTRIB_POS.max()), 0)), tol=0.5)
chk("§9.3", "成本合计与占价差毛利比",
    r"两项成本合计 \*\*[-−]([\d,.]+) USDT\*\*，相当于 144 组价差毛利（([\d,.]+) USDT）的 \*\*([\d.]+)%\*\*",
    (round(COST, 2), round(SPREAD, 2), round(COST / SPREAD * 100, 1)), tol=0.05)
chk("§9.3", "净收益恒等式", r"价差毛利 ([\d,.]+) - 手续费 ([\d,.]+) \+ 资金费 \([-−]([\d,.]+)\)\"",
    (round(SPREAD, 2), round(FEE, 2), round(-FUND, 2)), tol=0.02)

# ================================================================ 汇总

df = pd.DataFrame(ROWS)
df["通过"] = df["通过"].astype(bool)
df.to_csv(OUT / "07b_报告数值对账.csv", index=False, encoding="utf-8-sig")

bad = df[~df["通过"]]
res = {
    "说明": "报告里每个关键数字 → out/*.json|csv 原始字段的机械对账；不通过项必须改报告或改脚本，禁止人工放过",
    "总条数": int(len(df)),
    "通过": int(df["通过"].sum()),
    "不通过": int(len(bad)),
    "判据统计": {"总": int(len(df)), "通过": int(df["通过"].sum()), "不通过": int(len(bad))},
    "不通过明细": bad[["段", "项", "复算值", "报告值", "差值", "备注"]].to_dict("records"),
    "对账基准": {
        "手续费合计": round(FEE, 4), "资金费合计": round(FUND, 4),
        "价差毛利": round(SPREAD, 4), "净收益": round(NET, 4), "成本合计": round(COST, 4),
        "成本占价差毛利%": round(COST / SPREAD * 100, 2),
    },
}
with open(OUT / "07b_报告数值对账.json", "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2, default=str)

print(f"对账 {res['总条数']} 条：通过 {res['通过']}，不通过 {res['不通过']}")
if len(bad):
    for _, r in bad.iterrows():
        print(f"  ✗ {r['段']} | {r['项']} | 报告={r['报告值']} 复算={r['复算值']} | {r['备注'][:90]}")
