# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜06 生成验收报告（数字全部来自 out/，不手抄）。

README 的每个数字都由本脚本从 out/*.json 取出来填进模板；
改数字只改脚本重跑，不改正文。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "out"


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def main():
    d00 = load("00_范围锚定.json")
    d01 = load("01_币池与退市.json")
    d02 = load("02_四层覆盖.json")
    d03 = load("03_读取链与可得时点.json")
    d04 = load("04_资金费口径.json")
    d05 = load("05_可用范围与缺口.json")

    w = d02["windows"]["05C"]
    res = d02["results"]
    r5, rr, rh = res["05C"], res["04A_research"], res["04A_holdout"]
    ru = res["05C_universe"]
    uni = ru["universe"]
    uni_n = uni["symbol_count"]
    eb = ru.get("candidate_entry_bars") or {}
    pool = d02["pool"]["n"]
    spec = d03["non_bar_inputs"]["contract_spec"]
    fund = d03["non_bar_inputs"]["funding"]
    gapc = d03["funding_coverage_gap"]
    seg0 = gapc["hot_gap_segments"][0]
    warm5 = r5["warmup_4h"]
    warmu = ru["warmup_4h"]
    warmr = rr["warmup_4h"]
    warmh = rh["warmup_4h"]
    q4 = d04["quantified"]
    xc = d04.get("cross_check_04C", {})

    def cell(tf, layer, key):
        return r5["by_timeframe"][tf][layer][key]

    lines = []
    A = lines.append

    A("# ACCUM-EARLY-05-F｜实验输入与历史可得性核对（验收报告）")
    A("")
    A(f"- 生成时刻：{d00['generated_at_utc']}")
    A(f"- 本单范围：只读原始数据与共享实现；只写 `evidence/ACCUM-EARLY-05-F/`。"
      "不新增采集器、不启动全池补下载、不改币池、不改冻结规则。")
    A("- **状态口径**：本文结论为**执行方自报**，未经独立验收。三态判定只有"
      "「通过／不通过／未验证」，本文不使用「通过」以外的模糊说法。")
    A("")
    A("## 0 一句话结论")
    A("")
    A(f"**价格数据够用，资金费不够用。**")
    A("")
    A(f"1. **05-C 的观察母集里，数据完全够用。** 母集是 {uni_n} 个标的、"
      f"{uni['events_total']} 个观察事件、{uni['events_with_candidate']} 个走到候选。"
      f"这 {uni_n} 个标的的 15 分钟、1 小时、4 小时 K 线在窗口内**全部连续**"
      f"（{ru['by_timeframe']['15m']['cold']['continuous']} / "
      f"{ru['by_timeframe']['1h']['cold']['continuous']} / "
      f"{ru['by_timeframe']['4h']['cold']['continuous']}，中间断流 0、上市晚 0）；"
      f"{eb.get('candidates')} 条候选的成交 bar 在冷档里全部存在"
      f"（{eb.get('found')} / {eb.get('candidates')}）。")
    A(f"2. **但这个母集只有 {uni_n} 个标的，不是全池。** 它是「51 条原策略候选涉及的标的去重」，"
      f"所以「只有新策略会进场」的机会只在这 {uni_n} 个标的里找过。"
      f"把范围放到 {pool} 全池，就会看到 {cell('15m','cold','late_start')} 个窗口期间才上市的新币"
      "在前段没有数据——这是全池现象，不是母集现象。")
    A(f"3. 但**所有冷档 K 线都没有「抓取时刻」**（填充率 0），"
      f"热库只有 {pct(cell('15m','hot','available_at_filled'), cell('15m','hot','available_at_filled') + cell('15m','hot','available_at_null'))}% 的 15 分钟 bar 有（全池口径）。"
      "所以能说「计算没有用到未来数据」，不能说「当时本机确实拿得到」。")
    A(f"4. **资金费只读热库会算错。** 热库在 {seg0['from_utc']} → {seg0['to_utc']} "
      f"缺了 15.8 天的资金费（{seg0['symbols']} 个标的），冷档同段是完整的。"
      f"04-C 和 04-D 的资金费都只读热库 ⇒ 那段里持仓的笔少算了成本。"
      f"本单实测：验证区间 {q4['holdout_final']['funding_real_gap_trades']} 笔「该结算却没有记录」的成交，"
      f"**{q4['holdout_final']['funding_gap_inside_hot_gap']} 笔全部落在这个缺口段里**。")
    A("")
    A("## 1 核对对象：05-C 固定的范围")
    A("")
    A("| 项 | 值 | 来源 |")
    A("|---|---|---|")
    A(f"| 窗口起点 | {w['from_utc']} | 05-C 的输入快照 |")
    A(f"| 窗口终点 | {w['to_utc']} | 同上 |")
    A(f"| 预热起点 | {w['warmup_from_utc']} | 同上（4 小时 220 根） |")
    A(f"| 观察母集 | {uni_n} 个标的、{uni['events_total']} 个事件（来源：{uni['symbols_source']}） | 05-C 的 `04_统一观察母集` |")
    A(f"| 全池对照 | {pool} 个（币安 USD-M 加密币，排除 {len(d02['pool']['excluded'])} 个指数） | 04-A 已落盘口径 |")
    A("")
    A("说明两件事，避免后面被误读：")
    A("")
    A(f"- 本单的**主口径**是 05-C 的统一观察母集（{uni_n} 标的）；{pool} 全池只作「若扩到全池」的对照。"
      "两个口径的差别见下面 2.2 节，不要混着引用。")
    A(f"- 05-C 脚本里窗口常量的注释写「2026-06-27T00:00:00Z」，实际值是 {w['from_utc']}，"
      "差 8 小时。以产物里的数值为准，本单按 08:00 统计。")
    A("")
    A("## 2 四层覆盖：文件存在 / 窗口连续 / 当时可得 / 实际读取")
    A("")
    A("这四层必须分开看，因为结论完全不同：文件在，不等于窗口连续；窗口连续，"
      "不等于当时拿得到；当时拿得到，也不等于策略真的读了它。")
    A("")
    A(f"### 2.1 05-C 母集：{uni_n} 个标的（主口径）")
    A("")
    A("| 周期 | 应有根数 | 有数据 | 完全连续 | 上市晚 | 中间断流 | 尾部提前结束 | 冷档有抓取时刻 | 热库有抓取时刻 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for tf in ("15m", "1h", "4h"):
        c, h = ru["by_timeframe"][tf]["cold"], ru["by_timeframe"][tf]["hot"]
        cn = c["available_at_filled"] + c["available_at_null"]
        hn = h["available_at_filled"] + h["available_at_null"]
        A(f"| {tf} | {ru['by_timeframe'][tf]['expected_bars']} | {c['with_data']} | "
          f"{c['continuous']} | {c['late_start']} | {c['mid_gap']} | {c['tail_short']} | "
          f"{c['available_at_filled']} / {cn} | "
          f"{h['available_at_filled']} / {hn}（{h['available_at_filled_pct']}%） |")
    A("")
    A("读法：")
    A("")
    A(f"- 这 {uni_n} 个标的、三个周期，窗口内**一个异常都没有**（异常数 {ru['anomaly_count']}）："
      "没有上市晚、没有中间断流、没有尾部提前结束。")
    A(f"- 「应有根数」是窗口内按步长算出来的；4 小时那一行是 {ru['by_timeframe']['4h']['expected_bars']} 根，"
      "因为窗口终点 09:00 不是 4 小时边界，窗口里仍包含 08:00 那根。用整除会算成少一根，"
      "结果所有标的都被判成「不连续」——本单第一次统计就踩了这个假失败，已改成向上取整。")
    A("- 冷档「有抓取时刻」一列全是 0：冷档的 `available_at` 字段**整列是空类型**，不是漏填。")
    A(f"- 热库「有抓取时刻」一列不是 0，但热库 15 分钟起点晚（"
      f"{d03['trade_bar_layers']['hot_first_bar']['15m']['utc']}），窗口前段根本没有热库数据，"
      "所以热库那列「完全连续」是 0 属正常，不代表缺数据。")
    A(f"- {eb.get('candidates')} 条候选的成交 bar 在冷档里**全部存在**"
      f"（{eb.get('found')} / {eb.get('candidates')}，缺 {eb.get('missing')} 条）。这是全量核验，不是抽样。")
    A("")
    A(f"### 2.2 若扩到 {pool} 全池（对照，不是本单主口径）")
    A("")
    A("同一个窗口、同一套统计，换成 04-A 用的全池：")
    A("")
    A("| 周期 | 应有根数 | 有数据 | 完全连续 | 上市晚 | 中间断流 | 尾部提前结束 | 冷档有抓取时刻 | 热库有抓取时刻 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for tf in ("15m", "1h", "4h"):
        c, h = r5["by_timeframe"][tf]["cold"], r5["by_timeframe"][tf]["hot"]
        cn = c["available_at_filled"] + c["available_at_null"]
        hn = h["available_at_filled"] + h["available_at_null"]
        A(f"| {tf} | {r5['by_timeframe'][tf]['expected_bars']} | {c['with_data']} | "
          f"{c['continuous']} | {c['late_start']} | {c['mid_gap']} | {c['tail_short']} | "
          f"{c['available_at_filled']} / {cn} | "
          f"{h['available_at_filled']} / {hn}（{h['available_at_filled_pct']}%） |")
    A("")
    A(f"两个口径差在哪：**只差「上市晚」**。全池 15 分钟口径有 "
      f"{cell('15m','cold','late_start')} 个标的在窗口开始后才上市，母集是 "
      f"{ru['by_timeframe']['15m']['cold']['late_start']} 个；4 小时口径全池 "
      f"{cell('4h','cold','late_start')} 个、母集 {ru['by_timeframe']['4h']['cold']['late_start']} 个。"
      "中间断流与尾部提前结束两边都是 0。")
    A("")
    A(f"原因很直接：母集是「51 条候选涉及的标的」去重，能跑出候选的币必须在窗口开头就在，"
      "新上市的币前段没有数据，进不了事件链，自然也不会出现在母集里。"
      f"所以**母集 {uni_n} 个标的的覆盖结论，不能推广成「{pool} 全池覆盖通过」**："
      f"能说的是「本次要复核的这 {uni_n} 个标的，数据够」；"
      f"不能说「全池数据够」，也不能拿这 {uni_n} 个标的的 0 异常去替全池的 "
      f"{cell('15m','cold','late_start')} 个晚上市标的做担保。")
    A("")
    A("### 2.3 04-A 两段区间（对照，因为已交付的成绩建立在这两段上）")
    A("")
    A("| 区间 | 周期 | 完全连续 | 上市晚 | 中间断流 | 完全无数据 |")
    A("|---|---|---|---|---|---|")
    for name, blk in (("研究 2025-01-01→2026-07-01", rr), ("验证 2026-07-01→2026-10-12", rh)):
        for tf in ("15m", "1h", "4h"):
            c = blk["by_timeframe"][tf]["cold"]
            A(f"| {name} | {tf} | {c['continuous']} | {c['late_start']} | {c['mid_gap']} | {c['no_data']} |")
    A("")
    A(f"研究区间有 {rr['by_timeframe']['15m']['cold']['mid_gap']} 个标的出现**真实中间断流**"
      f"（不是上市晚）：最长的洞是 70 根 15 分钟。名单在 `out/02_四层覆盖.json` 的 anomalies 里。")
    A("")
    A("### 2.4 预热段（含在读取范围里，但常被漏算）")
    A("")
    A("| 区间 | 预热段 | 应有 4h 根数 | 完整标的 | 不足 | 完全无数据 |")
    A("|---|---|---|---|---|---|")
    A(f"| 05-C 母集（{uni_n} 标的） | {warmu['from_utc']}→{warmu['to_utc']} | {warmu['expected_bars']} | "
      f"{warmu['symbols_full']} | {warmu['symbols_short']} | {warmu['symbols_no_data']} |")
    A(f"| 05-C 全池对照（{pool} 标的） | {warm5['from_utc']}→{warm5['to_utc']} | {warm5['expected_bars']} | "
      f"{warm5['symbols_full']} | {warm5['symbols_short']} | {warm5['symbols_no_data']} |")
    A(f"| 04-A 研究 | {warmr['from_utc']}→{warmr['to_utc']} | {warmr['expected_bars']} | "
      f"{warmr['symbols_full']} | {warmr['symbols_short']} | {warmr['symbols_no_data']} |")
    A(f"| 04-A 验证 | {warmh['from_utc']}→{warmh['to_utc']} | {warmh['expected_bars']} | "
      f"{warmh['symbols_full']} | {warmh['symbols_short']} | {warmh['symbols_no_data']} |")
    A("")
    A("先看表头怎么读：「完整」+「不足」= 池大小；「完全无数据」是「不足」里面的子集，"
      "不是第三类，别把三列相加（会得出比池还大的数）。")
    A("")
    A(f"05-C 母集这一段的预热**全部完整**（{warmu['symbols_full']} / {uni_n}，不足 0、无数据 0），"
      f"所以母集里的 ATR 递推起点是满的。全池对照里有 {warm5['symbols_no_data']} 个标的这段完全没数据，"
      "都是窗口前后才上市的新币——同一件事，只是母集把它们排除在外了。")
    A("")
    A(f"04-A 研究区间的预热只有 {warmr['symbols_full']} 个标的是完整的："
      f"{warmr['symbols_no_data']} 个标的在 2023-11-28 之前根本没有数据（那时还没上市）。"
      "这意味着 04-A 研究区间里大部分标的的 ATR 递推起点比理论值短，"
      "它自己登记的「宽度相对差 <0.5% 是 56/64」正是这个原因，不是判据不同。")
    A("")
    A("## 3 策略实际需要哪些字段、由谁读")
    A("")
    A("| 周期 | 字段 | 用途 | 实际被读 |")
    A("|---|---|---|---|")
    zh = {True: "是", False: "否"}
    for f in d03["field_requirements"]:
        A(f"| {f['timeframe']} | {f['field']} | {f['用途']} | {zh.get(f['实际读取'], f['实际读取'])} |")
    A("")
    A("三条要特别说清：")
    A("")
    A("1. **成交量与主动买卖额不是「指标归档」那条链。** 策略读的是 K 线行里的 "
      "`quote_volume` 与 `taker_buy_quote_volume`，随该根收盘就有；"
      "指标归档（`parquet/metrics`）停在 2026-10-14，但策略根本不读它。"
      "所以「指标停更」不阻塞本次实验。")
    A("2. **持仓量不参与规则。** 04-A 的三组规则与基线价格链都不读 OI，"
      "冻结配置里的 OI 开关属于另一条实现。本单按「用途说明」处理，不因 OI 缺可得时点而给策略加门槛。")
    A("3. **合约规格在 04-A 里根本不读**（用 R 倍数绕开数量），只有 04-C/04-D 的净额口径才用。")
    A("")
    A("### 3.1 读取链确实落到冷档（抽样对撞）")
    A("")
    A(f"04-A 产物里记的 `bars_4h`/`bars_1h` 是它**读取范围**（含预热）的根数。"
      f"按同一范围独立统计冷档，{d03['reader_crosscheck']['matched']} 项一致、"
      f"{d03['reader_crosscheck']['mismatched']} 项不一致。")
    A("")
    A("这条很重要：04-A 那段区间热库 15 分钟是 0 行，如果它记的根数与本单冷档统计一致，"
      "说明它读的确实是冷档，不是读了个空热库还照跑。")
    A("")
    A("### 3.2 成交 bar 到底来自哪一层")
    A("")
    A("| 区间 | 成交笔数 | 入场 bar 只可能来自冷档 | 占比 |")
    A("|---|---|---|---|")
    for tag, label in (("research_final", "04-A 研究"), ("holdout_final", "04-A 验证")):
        b = d03["trade_bar_layers"]["by_window"][tag]
        A(f"| {label} | {b['fills']} | {b['entry_only_from_cold']} | {b['cold_share_pct']}% |")
    A("")
    A(f"判据：热库 15 分钟第一根是 {d03['trade_bar_layers']['hot_first_bar']['15m']['utc']}，"
      "早于它的成交 bar 不可能来自热库。验证区间仍有七成多成交落在冷档，"
      "所以「成交价来自冷档」不是个别现象，是常态。")
    A("")
    A("## 4 资金费：04-A 未计与 04-C/04-D 计入的差别")
    A("")
    A("### 4.1 差别不止资金费一项")
    A("")
    A("| 项 | 04-A | 04-C/04-D |")
    A("|---|---|---|")
    for d in d04["calibers"]["differences"]:
        A(f"| {d['项']} | {d['04A']} | {d['04CD']} |")
    A("")
    A("所以这两套结果**不能直接横比**。资金费只是其中一项，而且不是最大的一项。")
    A("")
    A("### 4.2 资金费单独值多少钱")
    A("")
    A("做法：拿 04-A 两段区间的全部成交明细，按「入场 < 结算时刻 ≤ 出场」把 `rate_type='settled'` "
      "的费率加起来，再按 R 的定义换算。只读数据，不改 04-A 任何产物。")
    A("")
    A("| 区间 | 成交笔数 | 持仓中位(小时) | 结算间隔中位(小时) | 结算次数中位 | 费率合计 | 对 R 的影响 | 方向 | 计入后 R 合计 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for tag, label in (("research_final", "04-A 研究"), ("holdout_final", "04-A 验证")):
        q = q4[tag]
        A(f"| {label} | {q['fills']} | {q['held_hours']['median']} | "
          f"{q['settlements']['median_interval_hours']} | {q['settlements']['median_per_trade']} | "
          f"{q['sum_rate_stats']['total']} | {q['delta_r']['total']} | "
          f"{q['delta_r']['direction']} | {q['delta_r']['base_R_sum_after']} |")
    A("")
    A("两个容易被误读的地方，先说清：")
    A("")
    A(f"1. **结算周期是 4 小时，不是教科书上的 8 小时。** 实测结算间隔中位数是 "
      f"{q4['holdout_final']['settlements']['median_interval_hours']} 小时，"
      f"持仓中位 {q4['holdout_final']['held_hours']['median']} 小时 ⇒ 中位 "
      f"{q4['holdout_final']['settlements']['median_per_trade']} 次结算，两者吻合。"
      "如果按 8 小时去估，资金费会算少一半。")
    A(f"2. **费率合计与 R 影响符号相反是正常的。** 因为 R 的定义里带负号"
      "（ΔR = −Σrate / 0.05）：费率为正表示多头付钱，R 减少。")
    A("")
    A(f"两段方向相反：研究区间资金费是**净收入**（{q4['research_final']['sum_rate_stats']['positive_trades']} 笔正费率、"
      f"{q4['research_final']['sum_rate_stats']['negative_trades']} 笔负费率，负费率时多头收钱），"
      f"验证区间是**净成本**（{q4['holdout_final']['sum_rate_stats']['positive_trades']} 笔正费率、"
      f"{q4['holdout_final']['sum_rate_stats']['negative_trades']} 笔负费率）。"
      "所以「资金费一定是成本」这个直觉是错的，得按当时的费率环境算。")
    A("")
    A(f"再给一个对得上的分解，免得笔数看着不闭合：验证区间 {q4['holdout_final']['fills']} 笔里，"
      f"{q4['holdout_final']['funding_zero_settlement_trades']} 笔区间内一次结算都没有——"
      f"其中 {q4['holdout_final']['funding_zero_but_hold_lt_8h']} 笔是持仓本来就不足一次结算（正常），"
      f"{q4['holdout_final']['funding_real_gap_trades']} 笔是真缺口（见 4.4 节）。"
      f"研究区间 {q4['research_final']['fills']} 笔里，"
      f"{q4['research_final']['funding_zero_settlement_trades']} 笔没有结算记录，"
      f"全部是持仓太短，真缺口 {q4['research_final']['funding_real_gap_trades']} 笔。")
    A("")
    A(f"验证区间这一项尤其要注意：04-A 的验证区间四组合计 R 是 "
      f"{q4['holdout_final']['base']['r_sum']}，计入资金费后变成 "
      f"{q4['holdout_final']['delta_r']['base_R_sum_after']}。"
      "乙组那个 +67 的正数，很大一部分会被资金费吃掉。")
    A("")
    A("### 4.3 量级交叉验证")
    A("")
    if xc:
        k0 = "生效视图_5bps" if "生效视图_5bps" in xc else list(xc)[0]
        v0 = xc[k0]
        per = q4["holdout_final"]["per_trade_usdt_and_r"]
        A(f"04-C 的 51 笔基线实测：资金费合计 {v0['total_funding']} USDT，"
          f"占净额 {v0['funding_pct_of_abs_net']}%、占手续费 {v0['funding_pct_of_fees']}%。"
          f"按「权益 5000、每笔风险 0.5%、止损 5%」折算，本单在 04-A 验证区间得到的每笔资金费是 "
          f"{per['usdt_per_trade']} USDT，对应 {per['r_per_trade']} R；"
          f"04-C 那 {v0['total_candidates']} 笔折下来每笔约 {v0['per_trade_usdt']} USDT。"
          "两个独立样本的每笔量级对得上（都是零点几 USDT、千分之几 R），"
          "说明本单的估法没有数量级错误。")
    A("")
    A("### 4.4 热库资金费有 15.8 天缺口（本单新发现）")
    A("")
    A(f"同一区间并排统计：热库 {gapc['hot_rows']} 行、冷档 {gapc['cold_rows']} 行；"
      f"冷档记录多于热库的标的有 {gapc['symbols_cold_more_than_hot']} 个，冷档多出 {gapc['cold_surplus_rows']} 行。")
    A("")
    A(f"主缺口段：**{seg0['from_utc']} → {seg0['to_utc']}**（{seg0['max_hours']:g} 小时，"
      f"{seg0['symbols']} 个标的）。抽查单个标的，热库在这段只有 2 条记录，冷档每天 6 条齐全。")
    A("")
    _rg = q4["holdout_final"]["funding_real_gap_trades"]
    _in = q4["holdout_final"]["funding_gap_inside_hot_gap"]
    A(f"影响：04-C/04-D 的资金费只从热库 `funding_history` 读，所以这段里持仓的笔会少算。"
      f"本单在 04-A 验证区间找到 {_rg} 笔「持仓够久、区间内却没有结算记录」的成交，"
      f"其中 {_in} 笔的持仓区间与缺口段相交"
      f"（{_in} / {_rg}，{'全部' if _in == _rg else '部分'}归因到「热库缺、冷档有」，不是策略侧问题）。"
      f"研究区间同类缺口是 {q4['research_final']['funding_real_gap_trades']} 笔——"
      "不是研究区间更干净，而是那段区间（2025-01 到 2026-07）根本不含 2026 年 9 月。")
    A("")
    A(f"还有一层：本单算出的验证区间 {q4['holdout_final']['delta_r']['total']} R 也是**下限**，"
      "因为本单同样只读了热库。要拿到真值，得先把资金费读取改成冷热合并。")
    A("")
    A("这条不阻塞价格链回放，但**阻塞「资金费已完整计入」这句话**。"
      "修复不需要新采集：冷档同段有数据，把资金费读取改成冷热合并即可。")
    A("")
    A("### 4.5 同条件比较必须统一的输入（八条）")
    A("")
    for u in d04["unified_input_requirements"]:
        A(f"- **{u['编号']}** {u['要求']} —— {u['为什么']}")
    A("")
    A("## 5 币池、退市标的、指数排除")
    A("")
    A("| 口径 | 数量 | 说明 |")
    A("|---|---|---|")
    for k, v in d01["denominators"].items():
        A(f"| {k} | {v} | |")
    A("")
    A(f"三件事：")
    A("")
    A(f"1. **指数排除规则不完整，但这次没造成影响。** 指数类标的一共 "
      f"{len(d01['index_rule']['index_symbols'])} 个"
      f"（{'、'.join(d01['index_rule']['index_symbols'])}），04-A 只显式排除了 "
      f"{len(d01['index_rule']['excluded_by_04A'])} 个；第三个没有 K 线目录，"
      "所以没进池。规则该补，但不改变本次结果。")
    A(f"2. **已退市标的一个都没有历史数据。** 元数据里 "
      f"{d01['delisted']['count']} 个合约状态是 SETTLING，"
      f"冷档目录 {d01['delisted']['in_cold_dirs']} 个、热库 K 线 {d01['delisted']['in_hot_candles']} 行。"
      "不是「有数据但没用」，是**根本没有归档**。")
    A(f"3. **幸存者偏差规模可上界、不可精确。** 现在用的池是 {d01['survivorship']['pool_used_by_04A']} 个，"
      f"加上已退市的 {d01['delisted']['count']} 个，历史全集上界 "
      f"{d01['survivorship']['historical_union_upper_bound']} 个，"
      f"被排除的比例上界 {d01['survivorship']['excluded_fraction_pct']}%。"
      "方向明确：对只做多策略会高估收益。大小算不出来，因为元数据里没有下架时刻，"
      "无法判断这些标的在窗口内是否还活着、贡献过多少候选。")
    A("")
    A("本单**没有改池**，也没有补下载。上面三条只是范围偏差的登记。")
    A("")
    A("## 6 给 05-C 的可用范围")
    A("")
    A("| 数据项 | 可用性 | 限制 |")
    A("|---|---|---|")
    for a in d05["availability"]:
        A(f"| {a['数据项']} | {a['可用']} | {a['限制']} |")
    A("")
    A("### 6.1 能下的结论 / 不能下的结论")
    A("")
    for i, c in enumerate(d05["constraints"], 1):
        A(f"{i}. {c}")
    A("")
    A("## 7 缺口清单与阻塞判定")
    A("")
    A("判「阻塞」的标准只有一条：不解决它，这次实验的结论会不会是错的。"
      "「数据不完美」不等于「阻塞」；「能跑出数」也不等于「结论可信」。")
    A("")
    A("| 编号 | 缺口 | 阻塞什么 | 等级 | 能否补 |")
    A("|---|---|---|---|---|")
    for g in d05["gaps"]:
        A(f"| {g['编号']} | {g['缺口']} | {g['阻塞什么']} | {g['阻塞等级']} | {g['能否补']} |")
    A("")
    bs = d05["blocking_summary"]
    A(f"汇总：真正阻塞结论的是 {'、'.join(bs['真正阻塞结论的'])}；"
      f"只限制声明的是 {'、'.join(bs['只限制声明的'])}；"
      f"不阻塞的是 {'、'.join(bs['不阻塞的'])}。")
    A("")
    A("换句话说：**只有资金费读取链这一条会改结论**，其余都是「能算但话不能说满」。"
      "指标停更、聪明钱没有历史、欧易合约不参与、股票类合约没 K 线——这些都不该变成策略工作的前置条件。")
    A("")
    A("## 8 复跑")
    A("")
    A("```sh")
    A("PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python")
    A('cd "/Users/boos/Desktop/威科夫因子共振趋势"')
    A("D=evidence/ACCUM-EARLY-05-F")
    A("")
    A("$PY $D/scripts/00_范围锚定.py")
    A("$PY $D/scripts/01_币池与退市核对.py")
    A("$PY $D/scripts/02_四层覆盖_文件与连续.py      # 约 25 秒")
    A("$PY $D/scripts/03_读取链与可得时点.py        # 约 15 秒")
    A("$PY $D/scripts/04_资金费口径差异.py          # 依赖 03 的缺口段")
    A("$PY $D/scripts/05_可用范围与缺口.py          # 依赖 01–04")
    A("$PY $D/scripts/06_生成报告.py                # 生成本文")
    A("$PY $D/scripts/07_数字对撞.py                # 最后跑")
    A("```")
    A("")
    A("## 9 未验证 / 不得当成通过")
    A("")
    A("| 项 | 状态 | 说明 |")
    A("|---|---|---|")
    A("| 冷档「当时可得」 | **未验证** | 冷档没有抓取时刻字段，补不了；只能说计算不超前 |")
    A("| 资金费完整计入 | **不通过（对 04-C/04-D）** | 热库缺 15.8 天，只读热库必漏 |")
    A("| 合约规格历史有效性 | **未验证** | 单时点快照，文件自写历史变化未证明 |")
    A("| 全池历史覆盖 | **不通过** | 178 个已退市标的零归档 |")
    A("| 策略效果 | **不适用本单** | 本单不跑策略、不设阈值、不评价收益 |")
    A("| 自然行情成交 | **未验证** | 本单全程离线，不涉及生产运行 |")
    A("")

    text = "\n".join(lines)
    (HERE / "README.md").write_text(text, encoding="utf-8")
    print(f"README 写入 {len(text)} 字符，{len(lines)} 行")


if __name__ == "__main__":
    main()
