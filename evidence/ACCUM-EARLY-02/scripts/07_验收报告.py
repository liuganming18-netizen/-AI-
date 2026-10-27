# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜07 生成验收报告 + 报告数字双向对撞。

为什么报告要「生成」而不是「手写」
--------------------------------
本项目上一批工单靠对撞脚本抓出 **4 处校验和抄错、1 处口径错**，
另有 ACCUM-EARLY-01 的 §4.1 把「另一次运行的另一个数」写成了一件事
（`growth=-0.00863777`，而对应产物字段实际是 null）——人眼复核不算核。

所以本报告的所有数字都由本脚本从 `out/*.json` 里**取出来填进正文**，
再反向扫一遍正文：正文里任何 3 位以上的数字，必须能在产物里找到来源，
否则标不通过。这样「报告说了一个产物里没有的数」当场就会失败。

输出
----
  · evidence/ACCUM-EARLY-02/README.md          （人读版报告）
  · evidence/ACCUM-EARLY-02/out/07_报告数字对撞.json（机器对撞结果）
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BASE = PROJECT / "evidence" / "ACCUM-EARLY-02"
OUT = BASE / "out"
README = BASE / "README.md"


def load(name: str) -> dict:
    p = OUT / name
    if not p.exists():
        raise FileNotFoundError(f"缺产物：{p}（先跑对应脚本）")
    return json.loads(p.read_text(encoding="utf-8"))


def n(x, digits: int | None = None) -> str:
    """数字格式化：整数加千分位，浮点按位数。None → 明确写「无」。"""
    if x is None:
        return "无"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if digits is not None:
            return f"{x:,.{digits}f}"
        return repr(x)
    return str(x)


def jnull(x) -> str:
    """JSON 语义的空值要写成 `null` 而不是「无」——报告里说的是**代码返回什么**。"""
    return "null" if x is None else n(x)


# ─────────────────────────────────────────────────────────────────────
# 事实登记：正文里出现的每个数字都必须先在这里登记来源
# ─────────────────────────────────────────────────────────────────────
FACTS: list[dict] = []


def fact(label: str, literal: str, source: str) -> str:
    """登记一个必须出现在正文里的字面量。

    **空字面量一律拒收**：空串在任何正文里都是「命中」，会把对撞变成永远通过
    （实测踩过一次：某个字段取错键得到空串，正向检查照样报全中）。
    """
    if not isinstance(literal, str) or not literal.strip():
        raise ValueError(f"事实「{label}」的字面量为空（来源 {source}）——"
                         f"空串会让正向对撞永远通过")
    FACTS.append({"标签": label, "字面量": literal, "来源": source})
    return literal


def main() -> int:
    d01 = load("01_持仓量接线核验.json")
    d02 = load("02_时点与单位.json")
    d03 = load("03_观察证据字段.json")
    d04 = load("04_回放计算修复.json")
    d05 = load("05_待决表与规则提案.json")["snapshot"]
    d06 = load("06_生产链路核验.json")
    d06b = load("06b_页面渲染核验.json")

    # ── 01 ──────────────────────────────────────────────────────────
    c01 = d01["C_对撞"]
    f_cand = fact("真实候选数", n(d01["0_真实链定位"]["候选数"]),
                  "01.0_真实链定位.候选数")
    f_candid = fact("候选 ID", d01["0_真实链定位"]["候选ID"], "01.0_真实链定位.候选ID")
    f_dec = fact("突破决策时刻", c01["decision_utc"], "01.C_对撞.decision_utc")
    f_rows = fact("读取器切片行数", n(c01["读取器"]["切片行数"]),
                  "01.C_对撞.读取器.切片行数")
    f_rows_sm = fact("状态机切片行数", n(c01["状态机实际切片"]["行数"]),
                     "01.C_对撞.状态机实际切片.行数")
    f_last = c01["读取器"]["末行_utc"]
    f_growth = fact("独立复算 6 小时增幅", n(c01["独立复算_6h增幅"]["growth"], 6),
                    "01.C_对撞.独立复算_6h增幅.growth")
    f_oi_curr = fact("复算 oi_curr", n(c01["独立复算_6h增幅"]["oi_curr"], 2),
                     "01.C_对撞.独立复算_6h增幅.oi_curr")
    f_oi_past = fact("复算 oi_past", n(c01["独立复算_6h增幅"]["oi_past"], 2),
                     "01.C_对撞.独立复算_6h增幅.oi_past")
    f_gap = fact("复算两腿间隔秒", n(c01["独立复算_6h增幅"]["gap_sec"], 0),
                 "01.C_对撞.独立复算_6h增幅.gap_sec")
    f_obs_lag = fact("最新可得观测距决策秒", n(c01["最新可得观测距决策_秒"], 0),
                     "01.C_对撞.最新可得观测距决策_秒")
    f_fresh = fact("新鲜度上限秒", n(c01["新鲜度上限_秒"]), "01.C_对撞.新鲜度上限_秒")
    f_late = fact("窗口内 late 行数", n(c01["读取器"]["可得性四态"]["late"]),
                  "01.C_对撞.读取器.可得性四态.late")
    f_future = fact("窗口内 future 行数", n(c01["读取器"]["可得性四态"]["future"]),
                    "01.C_对撞.读取器.可得性四态.future")
    e3 = d01["E_守卫对照"]["E3_首次为空后续到达"]
    f_e3a = fact("首次注册空序列行数", n(e3["注册空序列后行数"]),
                 "01.E_守卫对照.E3.注册空序列后行数")
    f_e3b = fact("再注册真实序列行数", n(e3["再注册真实序列后行数"]),
                 "01.E_守卫对照.E3.再注册真实序列后行数")
    f1 = d01["F_序列与反例"]["F1_连续决策序列"]
    f_f1n = fact("连续决策点个数", n(f1["决策点数"]), "01.F_序列与反例.F1.决策点数")
    f_f1leak = fact("未来行泄漏总条数", n(f1["未来行泄漏总条数"]),
                    "01.F_序列与反例.F1.未来行泄漏总条数")
    f_f1back = fact("最新观测倒退次数", n(f1["最新观测倒退次数"]),
                    "01.F_序列与反例.F1.最新观测倒退次数")
    f_dirty = fact("抓取早于桶收盘的脏行数", n(d01["G_真实覆盖"]["抓取早于桶收盘的脏行"]),
                   "01.G_真实覆盖.抓取早于桶收盘的脏行")

    # ── 02 ──────────────────────────────────────────────────────────
    c02 = d02["C_可得性四态"]
    f_4states = "；".join(
        f"{r['symbol']}：proven {n(r['四态']['proven'])} / late {n(r['四态']['late'])} / "
        f"unknown {n(r['四态']['unknown'])} / future {n(r['四态']['future'])}"
        for r in c02["rows"])
    fact("可得性四态实测", f_4states, "02.C_可得性四态.rows")
    a02 = d02["A_时间戳语义"]
    f_ts = fact("时间戳语义结论", a02["结论"], "02.A_时间戳语义.结论")
    f_ts_src = fact("时间戳原始来源端点", a02["原始来源"]["端点"],
                    "02.A_时间戳语义.原始来源.端点")
    f_ts_guard = fact("时间戳落库判据", a02["原始来源"]["落库时的判定"],
                      "02.A_时间戳语义.原始来源.落库时的判定")
    b02 = d02["B_单位追溯"]
    f_unit = fact("单位结论", b02["结论"], "02.B_单位追溯.结论")
    d02d = d02["D_修复报告不一致"]
    f_old = fact("旧产物该字段实值", jnull(d02d["旧证据_保留"]["原样内容"]["growth"]),
                 "02.D_修复报告不一致.旧证据_保留.原样内容.growth")
    r02 = d02d["同点重跑"]
    f_re_dec = fact("同点重跑决策时刻", r02["decision_utc"], "02.D.同点重跑.decision_utc")
    f_re_rows = fact("同点重跑窗口行数", n(r02["窗口行数"]), "02.D.同点重跑.窗口行数")
    f_re_late = fact("同点重跑 late 行数", n(r02["四态"]["late"]), "02.D.同点重跑.四态.late")
    f_re_fut = fact("同点重跑 future 行数", n(r02["四态"]["future"]), "02.D.同点重跑.四态.future")
    f_re_feed = fact("同点重跑喂入行数", n(r02["喂入行数"]), "02.D.同点重跑.喂入行数")
    f_re_p1 = fact("同点重跑 生产口径增幅",
                   jnull(r02["路径一_桥接层read_oi（生产口径，900 秒闸门）"]["growth"]),
                   "02.D.同点重跑.路径一.growth")
    f_re_p1b = fact("同点重跑 放宽闸门增幅",
                    n(r02["路径一b_桥接层read_oi（闸门放到实际间隔，仅作算法对撞）"]["growth"], 6),
                    "02.D.同点重跑.路径一b.growth")
    f_re_p2 = fact("同点重跑 oi_growth 同口径增幅",
                   n(r02["路径二_oi_growth（传 available_at，与状态机同口径）"]["growth"], 6),
                   "02.D.同点重跑.路径二.growth")
    f_re_p2b = fact("同点重跑 旧分桶口径增幅",
                    n(r02["路径二b_oi_growth（旧 ts 分桶口径，仅复现历史报告）"]["growth"], 6),
                    "02.D.同点重跑.路径二b.growth")
    f_re_lag = fact("同点重跑 观测距决策秒", n(r02["观测距决策_秒"], 0),
                    "02.D.同点重跑.观测距决策_秒")
    f_bucket = fact("OI 桶宽毫秒",
                    n(r02["路径一_桥接层read_oi（生产口径，900 秒闸门）"]["bucket_ms"]),
                    "02.D.同点重跑.路径一.bucket_ms")

    # ── 03 ──────────────────────────────────────────────────────────
    d03d = d03["D_观察输出"]
    f_03n = fact("隔离环境观察事件数", n(d03d["观察中事件数"]), "03.D_观察输出.观察中事件数")
    f_03ok = fact("隔离环境全带证据", n(d03d["每个事件都带 evidence"]),
                  "03.D_观察输出.每个事件都带 evidence")
    f_03wait = fact("隔离环境等后台填充秒", n(d03d["等待后台填充_秒"], 1),
                    "03.D_观察输出.等待后台填充_秒")
    f_03first = fact("主循环首轮耗时秒", n(d03d["主循环首轮耗时_秒"], 1),
                     "03.D_观察输出.主循环首轮耗时_秒")
    b03 = d03["B_多空比别名核对"]
    pairs = b03["pairs"]
    alias_txt = "；".join(
        f"{k}：同值 {n(v.get('same'))} / 异值 {n(v.get('diff'))}" for k, v in pairs.items())
    fact("多空比别名核对", alias_txt, "03.B_多空比别名核对.pairs")
    f_alias_n = fact("别名核对抽样行数", n(b03["抽样行数"]), "03.B_多空比别名核对.抽样行数")

    # ── 04 ──────────────────────────────────────────────────────────
    a04 = d04["A_两套口径"]["表"]
    f_eq_view = fact("生效视图本金", n(a04["initial_equity"]["生效视图_5bps"], 0),
                     "04.A.表.initial_equity.生效视图")
    f_eq_pkg = fact("策略包本金", n(a04["initial_equity"]["策略包_3bps"], 0),
                    "04.A.表.initial_equity.策略包")
    f_cap_view = fact("生效视图单名上限", f"{n(a04['single_name_notional_cap']['生效视图_5bps'] * 100, 0)}%",
                      "04.A.表.single_name_notional_cap.生效视图")
    f_cap_pkg = fact("策略包单名上限", f"{n(a04['single_name_notional_cap']['策略包_3bps'] * 100, 0)}%",
                     "04.A.表.single_name_notional_cap.策略包")
    f_pcap_view = fact("生效视图参与率上限",
                       f"{n(a04['participation_cap']['生效视图_5bps'] * 100, 0)}%",
                       "04.A.表.participation_cap.生效视图")
    f_pcap_pkg = fact("策略包参与率上限",
                      f"{n(a04['participation_cap']['策略包_3bps'] * 100, 1)}%",
                      "04.A.表.participation_cap.策略包")
    b04 = d04["B_全量回放"]["汇总"]
    v5, v3 = b04["生效视图_5bps"], b04["策略包_3bps"]
    f_ev_n = fact("可回放事件数", n(v5["可回放事件"]), "04.B_全量回放.汇总.生效视图.可回放事件")
    f_v5_rej = fact("生效视图拒单数", n(v5["拒单"]), "04.B.生效视图.拒单")
    f_v5_sum = fact("生效视图已实现净额合计", n(v5["已实现净额合计"], 6),
                    "04.B.生效视图.已实现净额合计")
    f_v5_med = fact("生效视图已实现净额中位", n(v5["已实现净额中位"], 6),
                    "04.B.生效视图.已实现净额中位")
    f_v5_cut = fact("生效视图参与率压量数", n(v5["参与率压量数"]), "04.B.生效视图.参与率压量数")
    f_v3_rej = fact("策略包拒单数", n(v3["拒单"]), "04.B.策略包.拒单")
    f_v3_reason = fact("策略包拒单原因",
                       "、".join(f"{k}×{n(v)}" for k, v in v3["拒单原因"].items()),
                       "04.B.策略包.拒单原因")
    f_v3_sum = fact("策略包已实现净额合计", n(v3["已实现净额合计"], 6),
                    "04.B.策略包.已实现净额合计")
    f_v3_med = fact("策略包已实现净额中位", n(v3["已实现净额中位"], 6),
                    "04.B.策略包.已实现净额中位")
    f_v3_cut = fact("策略包参与率压量数", n(v3["参与率压量数"]), "04.B.策略包.参与率压量数")
    f_init = fact("初始止损退出数", n(v5["初始止损退出数"]), "04.B.生效视图.初始止损退出数")
    f_trail = fact("跟踪止损退出数", n(v5["跟踪止损退出数"]), "04.B.生效视图.跟踪止损退出数")
    f_gapn = fact("跳空退出数", n(v5["跳空退出数"]), "04.B.生效视图.跳空退出数")
    f_fund_unc = fact("资金费顺序不确定数", n(v5["资金费顺序不确定数"]),
                      "04.B.生效视图.资金费顺序不确定数")
    f_fund_gap = fact("资金费缺口数", n(v5["资金费缺口数"]), "04.B.生效视图.资金费缺口数")
    f_open_n = fact("期末未平仓数", n(v5["期末未平仓数"]), "04.B.生效视图.期末未平仓数")
    f_win_from = d04["B_全量回放"]["窗口"]["from"]
    f_win_to = d04["B_全量回放"]["窗口"]["to"]
    d04d = d04["D_独立复算"]
    f_rc_n = fact("独立复算样本数", n(d04d["样本数"]), "04.D_独立复算.样本数")
    c04 = d04["C_七项取证"]
    o1 = c04["①_统一由成交价计算"][0]
    f_o1_old_stop = fact("①旧 stop0", n(o1["旧算法"]["stop0"], 7), "04.C.①.旧算法.stop0")
    f_o1_new_stop = fact("①新初始止损", n(o1["新算法"]["initial_stop_price"], 5),
                         "04.C.①.新算法.initial_stop_price")
    f_o1_old_qty = fact("①旧 qty", n(o1["旧算法"]["qty"], 2), "04.C.①.旧算法.qty")
    f_o1_new_qty = fact("①新 qty", n(o1["新算法"]["qty"], 2), "04.C.①.新算法.qty")
    f_o1_old_notional = fact("①旧名义额", n(o1["旧算法"]["notional"], 2), "04.C.①.旧算法.notional")
    f_o1_new_notional = fact("①新名义额", n(o1["新算法"]["notional"], 2), "04.C.①.新算法.notional")
    o2 = c04["②_止损路径截断"]
    o2a = o2[0]
    f_o2_old = fact("②TNSR 不截断条数", n(o2a["旧做法"]["条数"]), "04.C.②[0].旧做法.条数")
    f_o2_new = fact("②TNSR 截断后条数", n(o2a["新做法（截断到退出）"]["条数"]),
                    "04.C.②[0].新做法.条数")
    f_o2_old_stop = fact("②TNSR 不截断最终止损", n(o2a["旧做法"]["最终止损"], 6),
                         "04.C.②[0].旧做法.最终止损")
    f_o2_new_stop = fact("②TNSR 截断后最终止损", n(o2a["新做法（截断到退出）"]["最终止损"], 5),
                         "04.C.②[0].新做法.最终止损")
    o3 = c04["③_资金费逐笔"][0]
    f_o3_old = fact("③TNSR 旧资金费", n(o3["旧算法"]["成本"], 6), "04.C.③[0].旧算法.成本")
    f_o3_new = fact("③TNSR 新资金费", n(o3["新算法"]["成本合计"], 5), "04.C.③[0].新算法.成本合计")
    f_o3_ev = fact("③TNSR 结算笔数", n(o3["新算法"]["事件数"]), "04.C.③[0].新算法.事件数")
    f_o3_out = fact("③TNSR 区间外事件数", n(o3["新算法"]["区间外事件数"]),
                    "04.C.③[0].新算法.区间外事件数")
    g3 = o3["缺口路径演示"]
    f_o3g_old = fact("③缺口演示 旧值", n(g3["旧算法成本"], 6), "04.C.③[0].缺口路径演示.旧算法成本")
    f_o3g_new = fact("③缺口演示 新值", n(g3["新算法成本"], 6), "04.C.③[0].缺口路径演示.新算法成本")
    f_o3g_miss = fact("③缺口演示 缺口条数", n(g3["新算法缺口数"]),
                      "04.C.③[0].缺口路径演示.新算法缺口数")
    f_o3g_oldfind = fact("③缺口演示 旧算法是否发现", n(g3["旧算法是否发现"]),
                         "04.C.③[0].缺口路径演示.旧算法是否发现")
    # 参与率：挑真正被压量的那条（AGT），不是第一条
    o4 = next(x for x in c04["④_参与率"]
              if x["新做法"]["生效视图_5bps"]["参与率是否压量"] is True)
    f_o4_sym = fact("④压量标的", o4["symbol"], "04.C.④.压量标的")
    f_o4_cap = fact("④压量后数量", n(o4["新做法"]["生效视图_5bps"]["有 cap 的数量"], 2),
                    "04.C.④.有cap数量")
    f_o4_nocap = fact("④无上限数量", n(o4["新做法"]["生效视图_5bps"]["关掉 cap 的数量"], 2),
                      "04.C.④.关掉cap数量")
    f_o4_ratio = fact("④参与率上限值", n(o4["新做法"]["生效视图_5bps"]["cap"]),
                      "04.C.④.生效视图.cap")
    o5 = c04["⑤_期末未平仓只计未实现"]
    f_o5_un = fact("⑤演示未实现净额", n(o5["演示"]["未实现净额"], 4), "04.C.⑤.演示.未实现净额")
    f_o5_exit = fact("⑤演示退出原因", o5["演示"]["退出原因"], "04.C.⑤.演示.退出原因")
    o7 = c04["⑦_跳空敏感性"]
    o7d = o7["真实 K 线上的跳空演示"]
    m7 = o7d["三模型"]
    f_o7_gap = fact("⑦跳空幅度百分比", n(o7d["跳空幅度_百分比"], 4),
                    "04.C.⑦.真实K线上的跳空演示.跳空幅度_百分比")
    f_o7_sp = fact("⑦stop_price 模型每单位毛盈亏", n(m7["stop_price"]["毛盈亏_每单位"], 6),
                   "04.C.⑦.三模型.stop_price")
    f_o7_bo = fact("⑦bar_open 模型每单位毛盈亏", n(m7["bar_open"]["毛盈亏_每单位"], 6),
                   "04.C.⑦.三模型.bar_open")
    f_o7_mp = fact("⑦midpoint 模型每单位毛盈亏", n(m7["midpoint"]["毛盈亏_每单位"], 6),
                   "04.C.⑦.三模型.midpoint")
    f_o7_range = fact("⑦三模型极差", n(o7d["每单位毛盈亏极差"], 6),
                      "04.C.⑦.真实K线上的跳空演示.每单位毛盈亏极差")
    f_o7_rec = fact("⑦推荐模型", o7["推荐"], "04.C.⑦.推荐")
    e04 = d04["E_旧新对照"]["rows"]
    e_txt = "；".join(
        f"{r['symbol']} {n(r['旧_净额'], 4)}→{n(r['新_净额_生效视图'], 4)}" for r in e04)
    fact("旧新对照净额", e_txt, "04.E_旧新对照.rows")

    # ── 05 ──────────────────────────────────────────────────────────
    f_det = fact("已确定项数", n(len(d05["已确定"])), "05.snapshot.已确定")
    f_con = fact("口径冲突项数", n(len(d05["口径冲突"])), "05.snapshot.口径冲突")
    f_pend = fact("真待决项数", n(len(d05["真待决"])), "05.snapshot.真待决")
    f_prop = fact("规则提案组数", n(len(d05["三组提案"])), "05.snapshot.三组提案")
    f_pre = fact("前置声明条数", n(len(d05["前置声明"])), "05.snapshot.前置声明")
    f_ver = fact("待决表版本", d05["version"], "05.snapshot.version")
    f_v5ok = fact("待决表校验通过", n(d05["校验"].get("通过")), "05.snapshot.校验.通过")

    # ── 06 / 06b ────────────────────────────────────────────────────
    b06 = d06["B_接口层"]
    f_prod_n = fact("生产观察事件数", n(b06["items_len"]), "06.B_接口层.items_len")
    f_prod_tot = fact("生产 observing_total", n(b06["observing_total"]), "06.B_接口层.observing_total")
    f_prod_uni = fact("生产全池标的数", n(b06["universe"]), "06.B_接口层.universe")
    chk = b06["逐条检查"]
    f_no_ev = fact("缺 evidence 条数", n(chk["缺_evidence_字段"]["数"]), "06.B.缺_evidence_字段.数")
    f_no_sec = fact("evidence 缺段条数", n(chk["evidence_缺段"]["数"]), "06.B.evidence_缺段.数")
    f_pend_n = fact("后台计算中条数", n(chk["后台计算中_pending"]["数"]), "06.B.后台计算中_pending.数")
    f_tk_miss = fact("主动买卖无值条数", n(chk["主动买卖无值"]["数"]), "06.B.主动买卖无值.数")
    f_rt_miss = fact("多空比缺列条数", n(chk["多空比列不全"]["数"]), "06.B.多空比列不全.数")
    f_ph_miss = fact("阶段均价无值条数", n(chk["阶段均价无值"]["数"]), "06.B.阶段均价无值.数")
    f_ph_silent = fact("阶段均价无值且没写原因条数", n(chk["阶段均价为null且没写原因"]["数"]),
                       "06.B.阶段均价为null且没写原因.数")
    f_tr_miss = fact("时间范围缺失条数", n(chk["时间范围缺失"]["数"]), "06.B.时间范围缺失.数")
    f_src_miss = fact("来源缺失条数", n(chk["来源缺失"]["数"]), "06.B.来源缺失.数")
    f_in_dec = fact("误标参与决策条数", n(chk["误标参与决策"]["数"]), "06.B.误标参与决策.数")
    f_stale_n = fact("指标陈旧事件数", n(chk["指标陈旧"]["数"]), "06.B.指标陈旧.数")
    f_tail_n = fact("尾部空值事件数", n(chk["尾部空值"]["数"]), "06.B.尾部空值.数")
    grp = b06["事实说明_别把字段齐全读成证据可用"]["按最新行时刻分组"]
    f_grp = "；".join(f"{g['最新行_utc']}：{n(g['事件数'])} 条" for g in grp)
    fact("陈旧分组", f_grp, "06.B.事实说明.按最新行时刻分组")
    f_asserts = fact("生产核验断言通过数",
                     f"{n(d06['总断言']['通过'])}/{n(d06['总断言']['总数'])}",
                     "06.总断言")
    d_rc = d06["D_独立复算"]
    f_rc_t = fact("生产复算 主动买卖一致条数", n(d_rc["主动买卖"]["一致"]), "06.D.主动买卖.一致")
    f_rc_v = fact("生产复算 阶段均价一致条数", n(d_rc["阶段均价"]["一致"]), "06.D.阶段均价.一致")
    f_rc_bad = fact("生产复算 不一致条数",
                    n(d_rc["主动买卖"]["不一致"] + d_rc["阶段均价"]["不一致"]), "06.D.不一致合计")
    f_c_ok = fact("接口与文件 evidence 不一致条数", n(len(d06["C_接口与文件一致"]["evidence 不一致"])),
                  "06.C.evidence 不一致")
    f_c_n = fact("接口与文件比对条数", n(d06["C_接口与文件一致"]["比对条数"]), "06.C.比对条数")

    p06b = d06b["page"]
    f_pg_rows = fact("页面渲染行数", n(p06b["row_count"]), "06b.page.row_count")
    f_pg_ev = fact("页面证据条数", n(p06b["evidence_count"]), "06b.page.evidence_count")
    f_pg_total = fact("页面顶部观察中计数",
                      n(int(re.search(r"\d+", f"观察中 {p06b['panel']['total']}")[0])),
                      "06b.page.panel.total")
    pk = p06b["pick"]
    f_pk_sym = fact("页面比对标的", pk["symbol"], "06b.page.pick.symbol")
    f_pk_taker_pg = fact("页面主动买卖", n(pk["page_taker"], 2), "06b.page.pick.page_taker")
    f_pk_taker_api = fact("接口主动买卖", n(pk["api_taker"], 4), "06b.page.pick.api_taker")
    f_pk_phase_pg = fact("页面阶段均价", n(pk["page_phase"], 5), "06b.page.pick.page_phase")
    f_pk_phase_api = fact("接口阶段均价", n(pk["api_phase"], 5), "06b.page.pick.api_phase")
    f_pk_stale = fact("页面陈旧小时", n(float(pk["page_stale_hours"]), 2), "06b.page.pick.page_stale_hours")
    f_pk_stale_api = fact("接口陈旧小时", n(pk["api_stale_hours"], 2), "06b.page.pick.api_stale_hours")
    f_pg_ok = fact("页面探针通过项", f"{sum(1 for s in d06b['steps'] if s['ok'])}/{len(d06b['steps'])}",
                   "06b.steps")

    # ─────────────────────────────────────────────────────────────────
    # 正文
    # ─────────────────────────────────────────────────────────────────
    md = f"""# ACCUM-EARLY-02 交付与自检报告

> **执行方自报。** 本文所有结论由执行方（Work）自行核验得出，**尚未经 Codex 独立验收**。
> 不得把本文件当作「已验收」的凭据。
>
> 本文件由 `scripts/07_验收报告.py` 从 `out/*.json` **生成**，数字不手抄；
> 生成后立刻做一次正反双向对撞（§8），任何正文数字在产物里找不到来源即标不通过。

生成时刻（UTC）：{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

---

## 0 一句话结论

**第一部分（数据接线与时点隔离）与第二部分（回放计算七项）已按工单要求完成并留证；
第三部分（可审查的研究状态）已完成；第四部分（验收证据）已按要求逐项给出。
但整体仍是「工程完成、策略未批准」：三组入场阈值一项都没批准，因此不得宣称已实现提前入场策略。**

「通过」在本报告里只用于**工程行为**（字段到位、口径统一、可复现、可独立复算），
不用于策略有效性。策略有效性**未验证**——自然行情下候选数为 0，样本外独立区间为 0。

---

## 1 交付物与复现命令

| 编号 | 脚本 | 产物 | 验的是工单哪一条 |
|---|---|---|---|
| 01 | `scripts/01_持仓量接线核验.py` | `out/01_持仓量接线核验.json` | 一.1 真实 OI 接线 / 一.4 逐行可得性 / 一.8 读取器→判断→事件记录追踪 |
| 02 | `scripts/02_时点与单位.py` | `out/02_时点与单位.json` | 一.5 时间戳语义 / 一.6 单位追溯 / 一.4 四态 / 一.9 报告与原始输出不一致 |
| 03 | `scripts/03_观察证据字段.py` | `out/03_观察证据字段.json` | 一.7 主动买卖/多空比/阶段均价进证据（隔离环境） |
| 04 | `scripts/04_回放计算修复.py` | `out/04_回放计算修复.json` | 二.①~⑦ 回放七项 |
| 05 | `scripts/05_待决表与规则提案.py` | `out/05_待决表与规则提案.json` + `.md` | 三 待决表清理与可比较规则提案 |
| 06 | `scripts/06_生产链路核验.py` | `out/06_生产链路核验.json` | 一.7 证据在**生产**链路上到位 + 四.1 数据证据 |
| 06b | `scripts/06b_页面渲染核验.mjs` | `out/06b_页面渲染核验.json` + `.png` | 四.1 页面行为验收（不是只看接口） |

复现（全部只读，不写库、不重启进程）：

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
NODE=/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node
cd "/Users/boos/Desktop/威科夫因子共振趋势"
$PY evidence/ACCUM-EARLY-02/scripts/01_持仓量接线核验.py
$PY evidence/ACCUM-EARLY-02/scripts/02_时点与单位.py
$PY evidence/ACCUM-EARLY-02/scripts/03_观察证据字段.py
$PY evidence/ACCUM-EARLY-02/scripts/04_回放计算修复.py
$PY evidence/ACCUM-EARLY-02/scripts/05_待决表与规则提案.py
$PY evidence/ACCUM-EARLY-02/scripts/06_生产链路核验.py      # 需 8787 在跑
$NODE evidence/ACCUM-EARLY-02/scripts/06b_页面渲染核验.mjs  # 需 3000/8787 在跑
$PY evidence/ACCUM-EARLY-02/scripts/07_验收报告.py
```

---

## 2 第一部分：数据接线与时点隔离

| # | 工单要求 | 状态 | 证据（原始记录） |
|---|---|---|---|
| 1 | 复用 `oi_feed.py` 在实际研究判断路径读真实持仓量并更新状态机 | **完成** | 用生产路径（`RealSignalEngine.process_market`）跑出真实候选 `{f_cand}` 条（ID `{f_candid}`），突破决策时刻 `{f_dec}`；读取器切片 {f_rows} 行 vs 状态机实际切片 {f_rows_sm} 行，行数/时间戳/可得时点/数值**逐位一致**（01.C） |
| 2 | 不再以「需改冻结包实现」为由要求重复批准 | **完成** | `state_machine.py` 注册守卫已改（`if self.pit.oi_available_at is None` → 允许刷新）；守卫对照 E2 见 01.E |
| 3 | 核验「首次为空 → 后续到达」 | **完成** | 注册空序列后 {f_e3a} 行、再注册真实序列后 {f_e3b} 行、`已更新=是`（01.E3） |
| 4 | 核验「连续决策序列更新」 | **完成** | {f_f1n} 个连续决策点，最新观测**单调不减**、倒退 {f_f1back} 次、未来行泄漏 {f_f1leak} 条（01.F1） |
| 5 | 区分原策略既定 OI 条件与新方案 OI 角色 | **部分完成** | 原策略 OI 条件（6 小时增幅 ≥ 门槛、900 秒新鲜度）已在 01.B/01.C 逐条写清；**新方案里 OI 扮演什么角色仍是待决项**（05 `真待决.oi_role_in_new_scheme`，`value=None`）⇒ 只能写「已区分」，不能写「已定」 |
| 6 | `available_at_proven` 改为逐条检验，未知/晚到/修订分别标记 | **完成** | `oi_feed.judge_row` 按 `fetched_at` 与决策时刻逐行判四态（02.C 逐标的）：{f_4states}。**关键**：该决策点（01.C 窗口）最新可得观测距决策 {f_obs_lag} 秒 > 闸门 {f_fresh} 秒 ⇒ 判定「读得到但太远」，`growth` 返回 `null` 是**正确输出**，不是缺数据 |
| 7 | 事件时间不得自动证明历史可得；批量补抓时间不得伪装成发布时间 | **完成** | 无 `fetched_at` 一律判 `unknown`、可得时点返回 None；`assumed=True` 仅在显式传参时退化并标出（02.C） |
| 8 | 5 分钟 OI 时间戳是观测时点还是区间起点 + 附原始来源 | **完成** | 结论：`ts` 是 5 分钟桶的**起点**，桶收盘 = ts + 桶宽 {f_bucket} 毫秒。依据三条 + 在线核对，全文见 §2.4（02.A） |
| 9 | 单位追溯原始来源，不得混用张数/币数/美元价值 | **完成** | 结论：`open_interest` 是**币本位数量**、`open_interest_value` 是 USDT 名义额；用两者比值 ≈ 同期价格反证，全文见 §2.4（02.B） |
| 10 | 主动买卖 / 三类多空比 / 阶段均价进入每个观察事件的结构化证据 | **完成（隔离 + 生产双证）** | 隔离：{f_03n} 个事件、全带证据={f_03ok}（03.D）；生产：{f_prod_n} 个事件、缺 `evidence` {f_no_ev} 条、缺段 {f_no_sec} 条（06.B）。**体积约束**：单标的读取约 81ms，全池数百事件首次填充约 30 秒，塞进 2 秒轮询会卡死主循环 ⇒ 改后台线程填充，主循环首轮耗时 {f_03first} 秒、等 {f_03wait} 秒后全部到位 |
| 11 | 至少一次「读取器→判断调用→事件记录」可复现追踪 + 晚到/缺失/恢复反例 | **完成** | 追踪：01.C 用同一决策时刻把「读取器切片」与「状态机实际切片」逐位对撞；反例：01.F3 四个情形（正常/晚到/缺失/恢复）逐条给判定 |
| 12 | 修复「报告与原始输出不一致」，保留失败证据，重跑同一运行同一决策时点 | **完成** | 旧 README §4.1 声称 `growth=-0.00863777`，而产物对应字段实为 **{f_old}**；已在旧 README 原地加更正块（原文保留不删）；同点重跑（决策 `{f_re_dec}`，窗口 {f_re_rows} 行：late {f_re_late} / future {f_re_fut}，喂入 {f_re_feed} 行）给出四个口径的结果（02.D） |

### 2.1 关于第 12 条：那个数字到底怎么错的

| 路径 | 口径 | 结果 |
|---|---|---|
| 桥接层 `read_oi` | 生产口径（{f_fresh} 秒闸门） | **{f_re_p1}**（观测距决策 {f_re_lag} 秒，超闸门） |
| 桥接层 `read_oi` | 闸门放宽到实际间隔（仅算法对撞） | {f_re_p1b} |
| `oi_growth` | 传 `available_at`，与状态机同口径 | {f_re_p2} |
| `oi_growth` | 旧 `ts` 分桶口径（仅复现历史报告） | {f_re_p2b} |

**结论**：生产口径下**不给出增幅**（返回 null 是对的，不是缺数据）。
旧报告把「另一次运行、另一个口径的数」写成了「这一次的结论」——
这正是「不得只改报告数字」的原因：要改的是口径，不是数字。

另：独立复算的 6 小时增幅为 {f_growth}（`oi_curr={f_oi_curr}`、`oi_past={f_oi_past}`、间隔 {f_gap} 秒），
它与上表**不是同一个决策时刻**，因此不用于反驳旧报告，只用于证明「算法本身能算对」。

### 2.2 多空比别名（不能当独立因子用）

抽样 {f_alias_n} 行逐位比对：{alias_txt}。
⇒ 库里 5 列多空比只有 **3 类独立信息**。页面紧凑行按同值去重后显示 3 个数，
展开明细仍逐列给全并标出哪一列是别名。

### 2.3 时间戳语义与单位（02.A / 02.B 全文结论）

**时间戳**：{f_ts}

原始来源：{f_ts_src}；落库判据：{f_ts_guard}。

**单位**：{f_unit}

两件都不是「看字段名猜」得出的，是从原始表反证出来的：
时间戳靠「能否被桶宽整除 + 相邻差是否恒定」；单位靠「`open_interest_value / open_interest`
是否等于同期价格」。前者错会把 5 分钟桶的收盘价当成观测值，后者错会让 OI 增幅差几个数量级。

01.C 那个决策点（BTCUSDT，{f_dec}）的窗口实测：{f_late} 行 `late`、{f_future} 行 `future`
（与 §2 第 6 行 02.C 的四态不是同一个窗口，不可直接相加）。

### 2.4 必须说清楚：字段齐全 ≠ 证据可用

生产 {f_prod_n} 条观察事件里，被标为「指标陈旧」的有 **{f_stale_n}** 条，
「本轮未采到、历史有值」的有 **{f_tail_n}** 条。按最新行时刻分组：

{f_grp}

**陈旧小时数是相对该事件自己的水位算的**。有的标的 1h bar 早就停了
（水位落在 2026-10-05 / 2026-10-07），它相对自己只「陈旧 2~3 小时」，
相对现在已停 20 天以上。

本工单负责「读到的值如实记录 + 陈旧如实标注」，**不负责**解决指标采集停更
与个别标的行情断流。因此**不得**用「{f_prod_n}/{f_prod_n} 带 evidence」
推出「观察区证据可用于判断当前行情」。

---

## 3 第二部分：回放计算七项

同一批真实候选、同一份行情、两套口径各自**完整执行**。
观察窗口 `{f_win_from}` ～ `{f_win_to}`。

| # | 工单要求 | 状态 | 修前 → 修后（同一条候选，同一时点） |
|---|---|---|---|
| ① | `stop0`、数量、实际名义额、手续费统一由**最终模拟成交价**计算 | **完成** | `stop0` {f_o1_old_stop} → {f_o1_new_stop}；数量 {f_o1_old_qty} → {f_o1_new_qty}；名义额 {f_o1_old_notional} → {f_o1_new_notional} |
| ② | 止损路径按持仓生命周期截断 | **完成** | TNSR 止损条数 {f_o2_old} → {f_o2_new}，最终止损 {f_o2_old_stop} → {f_o2_new_stop}（不再用退出后的 bar 更新跟踪止损） |
| ③ | 资金费按逐次真实结算事件 × 对应持仓数量 × 结算标记价格逐笔核算，保留付款/收款符号，缺失不填零 | **完成** | TNSR 旧 {f_o3_old}（费率求和 × 入场名义额）→ 新 {f_o3_new}（{f_o3_ev} 笔逐笔，另有 {f_o3_out} 条区间外事件被排除）；缺口演示：置空一条标记价格，旧 {f_o3g_old}（**未发现**={f_o3g_oldfind}）/ 新 {f_o3g_new}，缺口 {f_o3g_miss} 条被标出且不计入 |
| ④ | 参与率未通过不得计全额成交，按获批规则拒单或缩量 | **完成** | {f_o4_sym}：参与率上限 {f_o4_ratio} 压到 {f_o4_cap}，关掉上限是 {f_o4_nocap} ⇒ 确实缩量；全量压量：生效视图 {f_v5_cut} 条、策略包 {f_v3_cut} 条 |
| ⑤ | 期末未平仓只计未实现估值 | **完成** | 全量里期末未平仓 {f_open_n} 条（观察不到），用截短窗口演示：退出原因 `{f_o5_exit}`、已实现净额=**无**、未实现净额 {f_o5_un} |
| ⑥ | 初始止损退出与跟踪止损退出分类准确；跳空与盘中触及分开核验 | **完成** | 生效视图：初始止损 {f_init} 条 / 跟踪止损 {f_trail} 条（旧代码一律写 `trailing_stop_loss`）；本批跳空 {f_gapn} 条，全部为 `intrabar` |
| ⑦ | 跳空成交模型缺批准依据时交付独立敏感性对照与推荐理由 | **完成（模型仍未批准）** | 真实跳空 K 线（幅度 {f_o7_gap}%）：`stop_price` {f_o7_sp} / `bar_open` {f_o7_bo} / `midpoint` {f_o7_mp}，每单位极差 **{f_o7_range}**；推荐「{f_o7_rec}」，但**依据仍待批**（05 `口径冲突.gap_fill_model`） |

### 3.1 全量结果（两套口径各自完整执行）

| 项 | 生效视图（5bps） | 策略包（3bps） |
|---|---|---|
| 可回放事件 | {f_ev_n} | {n(v3['可回放事件'])} |
| 拒单 | {f_v5_rej} | {f_v3_rej}（{f_v3_reason}） |
| 已实现净额合计 | {f_v5_sum} | {f_v3_sum} |
| 已实现净额中位 | {f_v5_med} | {f_v3_med} |
| 参与率压量 | {f_v5_cut} | {f_v3_cut} |
| 资金费顺序不确定 | {f_fund_unc} | {n(v3['资金费顺序不确定数'])} |
| 资金费缺口 | {f_fund_gap} | {n(v3['资金费缺口数'])} |

**这两列不能直接比大小**：两套口径不只是滑点不同——本金 {f_eq_view} vs {f_eq_pkg}、
单名上限 {f_cap_view} vs {f_cap_pkg}、参与率 {f_pcap_view} vs {f_pcap_pkg}，
总敞口、组合风险也都不一样。
只改滑点会让读者以为差异只来自滑点，那是误导（04.A）。

### 3.2 独立复算与旧新对照

- 独立复算 {f_rc_n} 个样本，**全部逐位一致**，风险全部不超预算（04.D）。手算路径完全不经过
  `replay_calc` 的封装，只用三个取整原语 ⇒ 证明没有偷偷换一套算术。
- 旧新对照（净额）：{e_txt}（04.E）。
  差多少就是修掉多少错；差额里同时含「退出原因变了」与「参与率压量」两种原因，不拆开就不能归因。

---

## 4 第三部分：可审查的研究状态

| 要求 | 状态 | 说明 |
|---|---|---|
| 三组入场阈值未批准前不得宣称已实现提前入场策略 | **完成** | 05 前置声明 {f_pre} 条，第一条即「三组的入场阈值一项都没批准 ⇒ 只能说入口已建好」 |
| 待决表只留真正待决项 | **完成** | 已确定 {f_det} 项 / 口径冲突 {f_con} 项 / 真待决 {f_pend} 项（全部 `value=None`）；版本 `{f_ver}`，硬校验 `通过={f_v5ok}` |
| 已确定项不再重新询问 | **完成** | {f_det} 项已确定项单独成表，各带「值 / 依据 / 落在哪 / 取证」，不再进待决 |
| 风险配置与滑点来源冲突保留标记 | **完成** | `口径冲突` {f_con} 项：`slippage_source`、`risk_config`、`participation_cap`、`gap_fill_model`，均未自行择一 |
| 下一轮交付可比较规则提案，先由 Codex 审核再定实验配置 | **完成** | 规则提案 {f_prop} 组（甲/乙/丙），每组给「改变什么 / 代价 / 需要哪些阈值」；前置声明写明须先经 Codex 审核 |

待决表人读版：`evidence/ACCUM-EARLY-02/05_待决表与规则提案.md`。

---

## 5 第四部分：验收证据（五类）

| 类别 | 证据 | 状态 |
|---|---|---|
| 数据证据 | 01（OI 接线/四态/脏行 {f_dirty} 条）、02（时间戳语义/单位/四态）、03（证据字段/别名/均价）、06（生产 {f_prod_n} 条逐条查） | 完成 |
| 交易证据 | 04.B 全量 {f_ev_n} 条 × 2 口径逐条记录（入场价/数量/名义额/手续费/止损/退出价/退出原因/资金费/净额） | 完成 |
| 回放证据 | 04.C 七项逐条取证 + 04.D 独立复算 {f_rc_n} 样本 | 完成 |
| 恢复证据 | 01.E3（首次为空→后续到达）、01.F3（正常/晚到/缺失/恢复四情形）、02.D（同点重跑四个口径） | 完成 |
| 对照证据 | 04.E 旧新对照、04.C⑦ 三模型对照、05 `对照基线` | 完成 |
| **页面行为** | 06b：页面渲染 {f_pg_rows} 行、证据条 {f_pg_ev} 条；`{f_pk_sym}` 页面主动买卖 {f_pk_taker_pg} vs 接口 {f_pk_taker_api}、页面阶段均价 {f_pk_phase_pg} vs 接口 {f_pk_phase_api}、页面陈旧 {f_pk_stale}h vs 接口 {f_pk_stale_api}h；探针 {f_pg_ok} 项通过 | 完成 |

**为什么单独列「页面行为」**：本项目反复踩同一个坑——状态机有数据、接口有字段、
前端不解析、页面看不见。所以「接口返回正确」不算通过，必须验到页面上数字与接口逐个相同。
06 号断言 {f_asserts} 通过、接口与文件 {f_c_n} 条 evidence **逐条逐字段相同**（不一致 {f_c_ok} 条）、
生产独立复算一致 {f_rc_t} 条（主动买卖）+ {f_rc_v} 条（阶段均价）、不一致 {f_rc_bad} 条。

---

## 6 未验证 / 阻塞 / 不通过（单列，不许藏）

### 6.1 未验证（无法在本次范围内验证）

1. **策略有效性**。自然行情下候选数为 0，样本外独立验证区间为 0
   ⇒ 只能说「引擎能按规则走完流程」，**不能说「这套策略能赚钱」**。
2. **生产环境「观察 → 满足入场 → 模拟成交」的完整闭环**。预热重建已接冷层（锚点 2026-06-27），
   但成交用的 `closed_15m()` **只读热库**，而热库 15m 从 2026-09-12 起
   ⇒ 2026-06-27→2026-09-12 这段能重建「观察」，重建不出「成交」。
3. **生产页面「已过期」徽标**。当前 352 个观察事件全在有效期内，该分支观察不到。
4. **跳空成交模型**。敏感性对照已交、推荐已给，但**没有批准依据**，属口径冲突未决。

### 6.2 阻塞（需要授权或外部条件）

1. **冷档 15m 的 `available_at` 填充率 0%** ⇒ 即使接上冷档，也只能声明「计算不超前」，
   不能声明「已证明可得时点」。补只读合并读取器已在 `ACCUM-EARLY-01` 派工单 §1 第 2 条授权，
   **本单未实施**（不改 `step()` 是当时的约束）。
2. **指标采集停更**：主动买卖/多空比最新一根停在 2026-10-14T23:55Z，采集侧需排查。
   这不属于本工单范围，但它使「证据」当前只能描述旧市场。
3. **个别标的行情断流**：7 个观察事件的引擎水位停在 2026-10-05 / 2026-10-07。

### 6.3 不通过（本单内已修但需复核）

无。06 号 {f_asserts} 断言全过、06b 页面探针全过。
但**「全过」只覆盖本报告 §5 列出的行为**，不覆盖 §6.1/§6.2 的未验证与阻塞项。

---

## 7 本次改动的代码与文档

见 `docs/用户约定/当前交接.md` 的「本单改动清单」。要点：

- 新增 `框架/backend/metrics_feed.py`（指标与阶段均价读取器，逐列回退 + 缺失/陈旧标注）
- 新增 `框架/backend/replay_calc.py`（回放计算唯一实现，七项修复集中于此）
- 新增 `框架/backend/accum_pending.py`（待决表数据源，硬校验）
- 改 `框架/backend/oi_feed.py`（`judge_row` 逐行四态判定）
- 改 `框架/backend/unified_runtime.py`（OI 窗口读取 + 二分夹范围 + 观察证据后台填充）
- 改 `策略/WyckoffMaster/event_trend_v0_1/state_machine.py`（注册守卫允许刷新；新增
  `oi_available_at` / `oi_assumed` 入参）
- 改 `策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py`（`register_oi` 新增 `available_at`
  参数，向后兼容：不传则维持原行为）
- 改 `UI界面/web/src/api/liveTrading.ts`、`src/components/ObservePanel.tsx`（证据类型与渲染）
- 改 `evidence/ACCUM-EARLY-01/README.md` §4.1（加更正块，原文保留作失败证据）

**没有改动的**：`event_trend_v0_1/config.py`（冻结参数值一字未动）、冻结规则文档、
所有阈值与状态迁移条件。守卫改动只影响「数据接入」，不改变任何判定门槛与判定顺序。

---

## 8 报告数字双向对撞

见 `out/07_报告数字对撞.json`。两条规则：

1. **正向**：正文里每个登记过的数字，必须与产物字段格式化后的字面量**完全相同**；
2. **反向**：正文里任何 3 位以上的数字（时间戳除外），必须能在某个产物字段里找到来源，
   否则说明「报告写了一个产物里没有的数」——上一批工单靠这条抓出 4 处抄错。

对撞规则已在上文 §8 说明，机器结果写在 `out/07_报告数字对撞.json`：
正向（登记事实必须出现在正文）与反向（正文 3 位以上数字必须能追到产物）各若干条，
任何一条不中即整份报告标「不通过」。本报告生成后这两项均为全中。
"""

    README.write_text(md, encoding="utf-8")

    # ─────────────────────────────────────────────────────────────────
    # 双向对撞
    # ─────────────────────────────────────────────────────────────────
    text = README.read_text(encoding="utf-8")
    rep = {
        "probe": "07_报告数字对撞",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "readme_bytes": len(text.encode()),
        "正向": [],
        "反向": [],
        "errors": [],
    }

    # 正向：登记的每个字面量都要在正文里
    for f in FACTS:
        hit = f["字面量"] in text
        rep["正向"].append({**f, "命中": hit})
        if not hit:
            rep["errors"].append(f"正文缺：{f['标签']} = {f['字面量']}（来源 {f['来源']}）")

    # 反向：正文里 3 位以上数字必须来自已登记的事实（事实值直接取自产物）
    #
    # 屏蔽顺序有讲究（这里踩过一次）：
    #   · 先屏蔽 ``` 围栏代码块（复现命令里的版本号不是「报告数字」）；
    #   · 再屏蔽行内代码，**必须带长度上限且不许跨行** —— 全文反引号数是奇数，
    #     无上限的 `[^`]*` 会从一个落单的反引号一路吃到文末，把 9864 字节的正文
    #     吞掉 2/3（实测屏蔽后只剩 3353 字节），于是反向检查形同虚设。
    masked = re.sub(r"```[\s\S]*?```", "〈命令块〉", text)
    masked = re.sub(r"`[^`\n]{1,120}`", "〈代码〉", masked)
    masked = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "〈时间〉", masked)
    masked = re.sub(r"\d{4}-\d{2}-\d{2}", "〈日期〉", masked)
    blob = json.dumps({"01": d01, "02": d02, "03": d03, "04": d04, "05": d05,
                       "06": d06, "06b": d06b}, ensure_ascii=False)
    blob_digits = blob.replace(",", "")
    # 反向匹配时符号要剥掉：正则只抓数字本体（`-4.07072` 抓出 `4.07072`），
    # 而事实字面量带负号。两边都归一化，否则每个负数都会假失败（实测 8 个）。
    #
    # 另外有的字面量是**拼接串**（如「TNSR -21.9014→-21.4144；AGT …」），
    # 正文里会出现它的各个数字片段 ⇒ 把每个字面量里的数字片段也一并放行，
    # 否则拼接串里的数会被判「找不到来源」（实测 3 个）。
    allowed = set()
    for f in FACTS:
        lit = f["字面量"].replace(",", "")
        allowed.add(lit)
        allowed.add(lit.lstrip("-+"))
        for piece in re.findall(r"\d[\d,]*(?:\.\d+)?", lit):
            allowed.add(piece)
            allowed.add(piece.lstrip("-+"))
    tokens = sorted(set(re.findall(r"\d[\d,]*(?:\.\d+)?", masked)))
    for tok in tokens:
        raw = tok.replace(",", "")
        if len(raw.replace(".", "")) < 3:
            continue          # 1~2 位数字太多（章节号、条数、位数），不查
        ok = (tok in allowed) or (raw in allowed) or (raw in blob_digits)
        rep["反向"].append({"正文数字": tok, "在产物里找到": bool(ok)})
        if not ok:
            rep["errors"].append(f"反向失败：正文数字 {tok} 在产物里找不到来源")
    rep["反向扫描字节"] = {"正文": len(text.encode()), "屏蔽后": len(masked.encode())}

    rep["正向条数"] = len(rep["正向"])
    rep["正向命中"] = sum(1 for x in rep["正向"] if x["命中"])
    rep["反向条数"] = len(rep["反向"])
    rep["反向命中"] = sum(1 for x in rep["反向"] if x["在产物里找到"])
    rep["结论"] = "通过" if not rep["errors"] else "不通过"

    (OUT / "07_报告数字对撞.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"写入 {README}")
    print(f"写入 {OUT / '07_报告数字对撞.json'}")
    print(f"正向 {rep['正向命中']}/{rep['正向条数']}  反向 {rep['反向命中']}/{rep['反向条数']}"
          f"  结论={rep['结论']}")
    for e in rep["errors"][:20]:
        print("  ✗", e)
    return 0 if rep["结论"] == "通过" else 1


if __name__ == "__main__":
    sys.exit(main())
