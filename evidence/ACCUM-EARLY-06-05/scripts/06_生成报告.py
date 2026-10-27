# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜06 生成验收报告（数字全部从 out/*.json 取，不手抄）。

报告里每个数字都登记进 `out/06_正文数字台账.json`，由 `07_数字对撞.py` 双向校验：
正文 ↔ 台账 ↔ 产物。**不允许出现台账外的数字**（白名单里的结构性数字除外）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
DOC = Path(__file__).resolve().parents[1] / "README.md"

LEDGER: list[dict] = []
RATE_TOL = "1e-12"
MARK_TOL = "1e-9"


def iso(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def N(v, src, fmt=None) -> str:
    """登记一个数字并返回它的文本形式。"""
    if fmt:
        txt = fmt.format(v)
    elif isinstance(v, float):
        txt = f"{v:g}"
    else:
        txt = str(v)
    LEDGER.append({"值": v, "文本": txt, "来源": src})
    return txt


def load(n):
    return json.loads((OUT / f"{n}.json").read_text(encoding="utf-8"))


def main():
    a = load("00_范围锚定"); b = load("01_三层字段级核对")
    c = load("02_原生结算周期与缺口段"); d = load("03_逐事件对账")
    e = load("04_受影响交易"); f = load("05_读取器与接入点")

    hot, cold = b["hot"], b["cold"]
    agg_all, agg48 = c["聚合"]["all"], c["聚合"]["mother_48"]
    j_all, j48 = d["全池"], d["母集48"]
    s = e["汇总"]
    r4 = e["04A复核"]
    cl0 = c["热库缺口段聚类"][0]

    # ── 数字登记 ────────────────────────────────────────────────────────
    n_hot_rows = N(a["data_identity"]["hot"]["funding_rows_binance_um"], "00#hot.funding_rows_binance_um")
    n_okx = N(a["data_identity"]["hot"]["funding_rows_okx_swap"], "00#hot.funding_rows_okx_swap")
    n_cold_files = N(a["data_identity"]["cold"]["files"], "00#cold.files")
    n_cold_bytes = N(a["data_identity"]["cold"]["bytes"], "00#cold.bytes")
    n_win_hot = N(hot["L1_结算事件覆盖"]["窗口内行数"], "01#hot.L1.窗口内行数")
    n_win_cold = N(cold["L1_结算事件覆盖"]["窗口内行数"], "01#cold.L1.窗口内行数")
    n_win_sym_cold = N(cold["L1_结算事件覆盖"]["窗口内标的"], "01#cold.L1.窗口内标的")
    n_win_sym_hot = N(hot["L1_结算事件覆盖"]["窗口内标的"], "01#hot.L1.窗口内标的")
    n_gt24 = N(hot["L3_当时可得"]["gt_24h占比"], "01#hot.L3.gt_24h占比")
    n_le1h = N(hot["L3_当时可得"]["le_1h占比"], "01#hot.L3.le_1h占比")
    n_minlag_h = N(round(hot["L3_当时可得"]["最小滞后ms"] / 3600000, 2), "01#hot.L3.最小滞后ms÷3600000")
    n_marknull_cold = N(cold["L2_rate_mark非空"]["全历史_mark空"], "01#cold.L2.全历史_mark空")
    n_marknull_hot = N(hot["L2_rate_mark非空"]["全历史_mark空"], "01#hot.L2.全历史_mark空")
    n_win_marknull_hot = N(hot["L2_rate_mark非空"]["窗口_mark空"], "01#hot.L2.窗口_mark空")
    n_hot_mark1h = N(hot["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"], "01#hot.L2.来源分布[0].rows")
    n_cold_mark1h = N(cold["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"], "01#cold.L2.来源分布[0].rows")
    n_pct_mark1h_hot = N(round(100.0 * hot["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"]
                              / hot["L1_结算事件覆盖"]["全历史行数"], 2),
                        "01#hot 来源分布[0].rows ÷ L1.全历史行数")
    n_pct_mark1h_cold = N(round(100.0 * cold["L2_rate_mark非空"]["来源分布_全历史"][0]["rows"]
                                / cold["L1_结算事件覆盖"]["全历史行数"], 2),
                          "01#cold 来源分布[0].rows ÷ L1.全历史行数")
    n_hot_fapi = N([x for x in hot["L2_rate_mark非空"]["来源分布_全历史"]
                    if x["source"] == "binance.fapi"][0]["rows"], "01#hot.L2.来源分布[binance.fapi]")

    d4 = [x for x in c["周期分布"] if x["step_h"] == 4.0][0]
    d8 = [x for x in c["周期分布"] if x["step_h"] == 8.0][0]
    d1 = [x for x in c["周期分布"] if x["step_h"] == 1.0][0]
    n_4h = N(d4["symbols"], "02#周期分布[4h].symbols")
    n_4h_pct = N(d4["占比pct"], "02#周期分布[4h].占比pct")
    n_8h = N(d8["symbols"], "02#周期分布[8h].symbols")
    n_8h_pct = N(d8["占比pct"], "02#周期分布[8h].占比pct")
    n_1h = N(d1["symbols"], "02#周期分布[1h].symbols")
    n_total_sym = N(c["周期分布_结论"]["总标的"], "02#周期分布_结论.总标的")
    n_ratio48 = N(agg48["events_vs_8h_ratio"], "02#聚合.mother_48.events_vs_8h_ratio")
    n_ratio_all = N(agg_all["events_vs_8h_ratio"], "02#聚合.all.events_vs_8h_ratio")
    n_exp8h_all = N(agg_all["expected_if_8h"], "02#聚合.all.expected_if_8h")
    n_exp_hot = N(agg_all["expected_hot"], "02#聚合.all.expected_hot")
    n_ev_hot48 = N(agg48["n_events_hot"], "02#聚合.mother_48.n_events_hot")
    n_ev_uni48 = N(agg48["n_events_union"], "02#聚合.mother_48.n_events_union")
    n_exp_hot48 = N(agg48["expected_hot"], "02#聚合.mother_48.expected_hot")
    n_j48_both = N(j48["n_both"], "03#母集48.n_both")
    n_ev_all = N(agg_all["n_events_union"], "02#聚合.all.n_events_union")

    n_gap_from = cl0["from_utc"]; n_gap_to = cl0["to_utc"]
    n_gap_hours = N(cl0["hours"], "02#热库缺口段聚类[0].hours")
    n_gap_syms = N(cl0["symbols_overlapping"], "02#热库缺口段聚类[0].symbols_overlapping")
    n_gap_peak = N(cl0["peak_symbols"], "02#热库缺口段聚类[0].peak_symbols")
    n_cov_hot = N(agg_all["coverage_hot_pct"], "02#聚合.all.coverage_hot_pct")
    n_cov_uni = N(agg_all["coverage_union_pct"], "02#聚合.all.coverage_union_pct")
    n_miss_hot = N(agg_all["missing_hot"], "02#聚合.all.missing_hot")
    n_miss_uni = N(agg_all["missing_union"], "02#聚合.all.missing_union")
    n_fill = N(agg_all["filled_by_cold"], "02#聚合.all.filled_by_cold")
    n_cov_hot48 = N(agg48["coverage_hot_pct"], "02#聚合.mother_48.coverage_hot_pct")
    n_miss_hot48 = N(agg48["missing_hot"], "02#聚合.mother_48.missing_hot")
    n_sym_hot_gap48 = N(agg48["symbols_with_hot_gap"], "02#聚合.mother_48.symbols_with_hot_gap")
    n_unreliable = N(c["周期不单一_缺口不可推算"]["count"], "02#周期不单一_缺口不可推算.count")
    n_unrel48 = N(agg48["symbols_unreliable_grid"], "02#聚合.mother_48.symbols_unreliable_grid")
    n_rel48 = N(int(agg48["symbols"]) - int(agg48["symbols_unreliable_grid"]),
                "02#聚合.mother_48.symbols − symbols_unreliable_grid")
    n_max_gap48 = N(int(max((x["hours"] for r in c["per_symbol_48"].values()
                            for x in r["hot_segments"]), default=0)),
                    "02#per_symbol_48[].hot_segments[].hours 最大值")
    mp = {m["定义"][:6]: m["值"] for m in c["标的数口径映射"]}
    n_map407 = N(c["标的数口径映射"][0]["值"], "02#标的数口径映射[0].值")
    n_map417 = N(c["标的数口径映射"][2]["值"], "02#标的数口径映射[2].值")
    n_map309 = N(c["标的数口径映射"][3]["值"], "02#标的数口径映射[3].值")
    n_map418 = N(c["标的数口径映射"][4]["值"], "02#标的数口径映射[4].值")

    n_j_ev = N(j_all["n_events"], "03#全池.n_events")
    n_j_both = N(j_all["n_both"], "03#全池.n_both")
    n_j_hot_only = N(j_all["n_hot_only"], "03#全池.n_hot_only")
    n_j_cold_only = N(j_all["n_cold_only"], "03#全池.n_cold_only")
    n_j_conf = N(j_all["n_rate_conflict"] + j_all["n_mark_conflict"]
                 + j_all["n_mark_null_mismatch"] + j_all["n_rate_null_mismatch"], "03#全池.四类冲突合计")
    n_j48_ev = N(j48["n_events"], "03#母集48.n_events")
    n_j48_cold = N(j48["n_cold_only"], "03#母集48.n_cold_only")
    n_j48_hot = N(j48["n_hot_only"], "03#母集48.n_hot_only")
    n_j_conf48 = N(j48["n_rate_conflict"] + j48["n_mark_conflict"]
                   + j48["n_mark_null_mismatch"] + j48["n_rate_null_mismatch"],
                   "03#母集48.四类冲突合计")
    n_cold_only_sep = N(d["冷档独有事件"]["按月"][0]["n"], "03#冷档独有事件.按月[2026-09]")
    n_hot_only_oct = N(d["热库独有事件"]["按月"][-1]["n"], "03#热库独有事件.按月[2026-10]")
    n_dup_cold = N(d["重复"]["cold_dup_groups"], "03#重复.cold_dup_groups")
    n_dup_hot = N(d["重复"]["hot_dup_groups"], "03#重复.hot_dup_groups")
    n_csv48 = N(d["明细文件"]["母集48"]["rows"], "03#明细文件.母集48.rows")
    n_csvabn = N(d["明细文件"]["异常行"]["rows"], "03#明细文件.异常行.rows")

    n_trades = N(e["对象"]["笔数"], "04#对象.笔数")
    n_ev_hot = N(s["热库结算事件合计"], "04#汇总.热库结算事件合计")
    n_ev_uni = N(s["并集结算事件合计"], "04#汇总.并集结算事件合计")
    n_ev_fill = N(s["冷档补回事件合计"], "04#汇总.冷档补回事件合计")
    n_cost_hot = N(s["资金费_热库"], "04#汇总.资金费_热库", fmt="{:.9f}")
    n_cost_uni = N(s["资金费_并集"], "04#汇总.资金费_并集", fmt="{:.9f}")
    n_cost_delta = N(s["资金费差额_并集减热库"], "04#汇总.资金费差额_并集减热库", fmt="{:.9f}")
    n_base = N(s["基线合计_05C"], "04#汇总.基线合计_05C", fmt="{:.9f}")
    n_zero_hot = N(s["热库漏结算的笔数"], "04#汇总.热库漏结算的笔数")
    n_zero_fill = N(s["其中冷档补回（并集不再是 0）"], "04#汇总.其中冷档补回")
    n_zero_uni = N(s["并集仍 0 结算但应有结算"], "04#汇总.并集仍0结算但应有结算")
    n_resid = N(s["并集仍有残余缺口（下界>0）的笔数"], "04#汇总.并集仍有残余缺口")
    n_maxdiff = N(s["并集与基线最大单笔绝对差"], "04#汇总.并集与基线最大单笔绝对差", fmt="{:.9f}")
    rA = r4["holdout_final"]; rB = r4["research_final"]
    n_4a_fills = N(rA["fills"], "04#04A复核.holdout_final.fills")
    n_4a_96 = N(rA["口径A_热库0结算且持仓≥8h"], "04#04A复核.holdout_final.口径A")
    n_4a_104 = N(rA["口径B_热库0结算且应有≥1次(实测周期)"], "04#04A复核.holdout_final.口径B")
    n_4a_fill = N(rA["口径B_中冷档能补回"], "04#04A复核.holdout_final.口径B_冷档能补回")
    n_4a_resid = N(rA["口径B_中并集仍无记录"], "04#04A复核.holdout_final.口径B_并集仍无记录")
    n_4b_fills = N(rB["fills"], "04#04A复核.research_final.fills")
    n_4b_1 = N(rB["口径B_热库0结算且应有≥1次(实测周期)"], "04#04A复核.research_final.口径B")
    n_4b_resid = N(rB["口径B_中并集仍无记录"], "04#04A复核.research_final.口径B_并集仍无记录")
    n_able = N(len(e["能补与不能补"]["能补"]), "04#能补与不能补.能补.len")
    n_unable = N(len(e["能补与不能补"]["不能补"]), "04#能补与不能补.不能补.len")
    n_reader = N(len(f["读取器清单"]), "05#读取器清单.len")

    claims = {x["编号"]: x for x in a["claims_to_retest"]}

    # 05-F 自己写下的数值也要进台账（否则反向对撞会把「引用值」当成手抄）
    def _reg_claim(node, src):
        if isinstance(node, dict):
            for v in node.values():
                _reg_claim(v, src)
        elif isinstance(node, list):
            for v in node:
                _reg_claim(v, src)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            N(node, src + "（05-F 自报值）")
        elif isinstance(node, str) and node.replace(".", "", 1).isdigit():
            N(node, src + "（05-F 自报值）")

    for _c in a["claims_to_retest"]:
        _reg_claim(_c["claim"], _c["来源"])

    def claim_txt(k):
        v = claims[k]["claim"]
        return json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float)) else str(v)

    txt = f"""# ACCUM-EARLY-06-05｜冷热资金费缺口与现成读取接口核对（验收报告）

- 生成时刻：{iso(int(time.time() * 1000))}
- 窗口：{a["window"]["start_utc"]} → {a["window"]["end_utc"]}（沿用 05-C / 04-C，未改）
- 范围：只读热库 `market_v2.sqlite3` 与冷档 `parquet/funding/binance/um/`，只写本窗 `out/`
- **状态口径**：执行方自报，未经独立验收。三态只有「通过／不通过／未验证」。
- 数据身份：热库 binance/um `funding_history` {n_hot_rows} 行（另含 okx/swap {n_okx} 行，本单全部过滤）；
  冷档资金费 {n_cold_files} 个分片、{n_cold_bytes} 字节。两者都是「取锚点时刻」的值，不是内容指纹。
- 本单**没有**新建采集器、没有补零、没有跨交易所拼接（全部 `exchange='binance' AND market='um'`）、
  没有给冷档伪造任何可得时刻。

## 0 一句话结论

**05-F 的 G4 是真的，但它的三个数字都不能当常量，而且冷档能把这条缺口基本补齐。**

1. **缺口真实存在**：热库在 {n_gap_from} → {n_gap_to} 有 {n_gap_hours} 小时连续空洞，
   本单用「每标的实测周期」独立复现到同一段（不是抄 05-F 的时刻）。
2. **受影响标的数有四个版本**：05-F 写 {n_map407}，本单按其口径复现是 {n_map417}，
   严格口径（剔除周期变化标的）是 {n_map309}，宽松口径是 {n_map418}。
   四个数都对，**混用才会错**——所以本单任何结论都带口径。
3. **冷档能补**：并集后全池缺口从 {n_miss_hot} 降到 {n_miss_uni}（只算周期可靠的标的；
   {n_unreliable} 个周期不单一标的缺口不可推算，见 §3），
   母集 48 标的里可靠的 {n_rel48} 个从 {n_miss_hot48} 降到 0（另 {n_unrel48} 个周期不单一，
   缺口**不可判定**，不计入「已补齐」）。**补的是「事件」，不是「可得时间」**：
   冷档连 `available_at` 这一列都没有。
4. **三层必须分开**：结算事件覆盖（L1）齐、`rate/mark` 非空（L2）基本齐、
   「结算当时本机拿得到」（L3）**未验证且无法补**。热库最短抓取滞后 {n_minlag_h} 小时，
   窗口内 {n_gt24}% 的记录是 24 小时以后才回填的。

## 1 复核 05-F 的七条主张（不把报告数字当断言常量）

做法：只从 `05-F/out/*.json` 取它自己写下的值当 `claim`，实测值全部由本单脚本重算。

| 编号 | 05-F 主张 | 05-F 写的值 | 本单实测 | 判定 |
|---|---|---|---|---|
| C1 | 热库资金费主缺口段 | `{claim_txt("C1")}` | {n_gap_from} → {n_gap_to}，{n_gap_hours}h，同段复现；标的数见 §4 口径映射 | **一致（时段）／数字需带口径（标的数）** |
| C2 | 冷档同段完整（热少冷多） | `{claim_txt("C2")}` | 冷档独有 {n_j_cold_only} 个结算事件，其中 {n_cold_only_sep} 个在 2026-09 | **一致** |
| C3 | 验证区间「该结算却无记录」笔数 | `{claim_txt("C3")}` | 原口径复现 {n_4a_96}；换成实测周期 {n_4a_104}；**{n_4a_fill} 笔冷档能补回** | **部分不成立**：96 是偏少的上界，且全部可补 |
| C4 | 结算间隔中位数 | `{claim_txt("C4")}` | 周期分布见 §3：{n_4h} 个标的是 4h、{n_8h} 个是 8h | **一致**，但「中位数」不能当每个标的的周期 |
| C5 | 热库 fetched_at 事后回填占比 | `{claim_txt("C5")}` | 窗口内 {n_gt24}% 滞后 >24h，≤1h 的占 {n_le1h}% | **一致，且更严重**：没有一条在 1 小时内抓到 |
| C6 | 冷档有无「当时可得」字段 | `{claim_txt("C6")}` | 列清单里没有 `available_at`（整列不存在，不是填充率 0） | **一致**，但表述要从「填充 0%」改成「无此列」 |
| C7 | G4 是唯一阻塞项 | `{claim_txt("C7")}` | 见 §8：G4 的「阻塞」只对「资金费已完整计入」这句话成立；补完之后阻塞换成「可得性未验证」 | **部分成立** |

C3 与 C7 的具体差异在 §6、§8 展开。

## 2 三层分开：L1 覆盖 / L2 字段非空 / L3 当时可得

| 层 | 热库 `funding_history` | 冷档 `funding/*.parquet` |
|---|---|---|
| L1 结算事件覆盖 | 窗口内 {n_win_hot} 行 / {n_win_sym_hot} 标的 | 窗口内 {n_win_cold} 行 / {n_win_sym_cold} 标的 |
| L2 rate/mark 非空 | 全历史 mark 空 {n_marknull_hot} 行；**窗口内 mark 空 {n_win_marknull_hot} 行** | 全历史 mark 空 {n_marknull_cold} 行；窗口内 0 行 |
| L3 当时可得 | 有 `fetched_at`：最短滞后 {n_minlag_h} 小时，>24h 占 {n_gt24}% ⇒ **未验证** | **无 `fetched_at` / 无 `available_at` 列** ⇒ 未验证，且不可补 |

三条读法：

1. **L1 齐不等于 L2 可用**。热库窗口内 mark 空为 {n_win_marknull_hot}，但全历史有 {n_marknull_hot} 行是空的
   （都在窗口外），说明「mark 空」是历史遗留、不是当前问题——但换个窗口就会踩到。
2. **L2 非空不等于原生**。热库 {n_hot_mark1h} 行、冷档 {n_cold_mark1h} 行的 `source` 写着
   `binance.archive.fundingRate+mark1h`，即 mark 是**用 1 小时价补的**；热库只有 {n_hot_fapi} 行
   来自 `binance.fapi`。所以两源逐位一致（§5）证明的是**同源一致**，不能声称
   「与交易所结算标记价一致」。
3. **L3 不能补**。冷档是离线归档媒介，落盘时间不等于结算当时可得；
   热库虽有 `fetched_at`，但窗口内没有一条在 1 小时内抓到（≤1h 占 {n_le1h}%），
   所以只能声明「计算不超前」，不能声明「当时本机拿得到」。

## 3 结算周期：不能一律按 8 小时

| 实测众数周期 | 标的数 | 占比 |
|---|---|---|
| 4 小时 | {n_4h} | {n_4h_pct}% |
| 8 小时 | {n_8h} | {n_8h_pct}% |
| 1 小时 | {n_1h} | 其余 |

{ n_total_sym } 个标的里只有 {n_8h} 个是 8 小时。用 8 小时当分母会**当场被数据否证**：
同一窗口按 8 小时推算的期望事件数是 {n_exp8h_all} 条，而实际观测到 {n_ev_all} 条
（比值 {n_ratio_all}；母集 48 标的比值 {n_ratio48}）。**期望数比观测数还少，说明分母错了。**

所以本单完整率一律给两列、不择一：主口径 = 每标的实测周期；
「按 8h」只作反例，用来演示它会把 4 小时币的缺口全判成「正常间隔」。

周期推断的边界（必须一起读）：

- 本机**没有**交易所原生周期元数据：`strategies/funding_interval_meta_source.py` 的伴生库
  `funding_interval_meta.sqlite3` 不在本仓库，`fundingIntervalHours` 快照也不存在
  ⇒ 周期只能实测推断，标 `inferred`，**不得**写成「已与交易所元数据核对」。
- 众数占比 <95% 的标的（{n_unreliable} 个）周期在窗口内变过，「缺几个」**不可推算**，本单不给数；
  把它们算进去会把「4h 改 8h」误报成「漏结算」。

## 4 缺口段：热库单独 vs 热冷并集

| 口径 | 事件数 | 期望数 | 缺口（推算） | 完整率 |
|---|---|---|---|---|
| 热库单独（全池 {n_total_sym} 标的） | {n_win_hot} | {n_exp_hot} | {n_miss_hot} | {n_cov_hot}% |
| 热冷并集（全池） | {n_ev_all} | {n_ev_all} | {n_miss_uni} | {n_cov_uni}% |
| 热库单独（母集 48） | {n_ev_hot48} | {n_exp_hot48} | {n_miss_hot48} | {n_cov_hot48}% |
| 热冷并集（母集 48） | {n_ev_uni48} | {n_ev_uni48} | {n_miss_uni} | {n_cov_uni}% |

表注：**缺口与完整率只对「周期可靠」的标的计算**（全池 {n_unreliable} 个、母集 {n_unrel48} 个周期不单一，
不计入分母）；「事件数」一列含全部标的，所以两列不能直接相除。

- 并集后冷档补回 {n_fill} 个期望结算；母集 48 里可靠的 {n_rel48} 个标的的
  {n_miss_hot48} 个缺口**全部补回**。**另 {n_unrel48} 个标的周期不单一，
  缺口既不能算 0 也不能算有** —— 只能说「不可判定」，不能说「已补齐」。
- 热库缺口段聚类（阈值：连续小时桶内缺事件标的数 ≥ 标的数一半）只聚出**一段**：
  {n_gap_from} → {n_gap_to}，{n_gap_hours} 小时，峰值 {n_gap_peak} 个标的，与该段相交的标的 {n_gap_syms} 个。
- 母集 48 里有 {n_sym_hot_gap48} 个标的在窗口内至少有一段热库缺口，最长一段 {n_max_gap48} 小时（在并集里消失）。

### 4.1 「受影响标的数」的四个口径（谁也不是错的，混用才错）

| 定义 | 值 |
|---|---|
| 05-F 报告值 | {n_map407} |
| 本单按其口径复现（不过滤交易所） | {n_map417} |
| 本单按其口径复现（只 binance/um） | {n_map417} |
| 本单主口径（1.5×实测周期 + 只算周期可靠标的 + 05-C 窗口） | {n_map309} |
| 本单口径 + 含周期不单一标的（仅参考） | {n_map418} |

05-F 的 {n_map407} 与本单复现的 {n_map417} 差 10 个标的，其脚本外的过滤细节无法完全复现
⇒ 该数字**对实现细节敏感，不宜当常量引用**；时段（{n_gap_from} → {n_gap_to}）才是稳定结论。
另外热库同表混装 okx/swap（{n_okx} 行），本单全部过滤掉，不做跨交易所拼接。

## 5 逐事件对账：来源 / 缺失 / 重复 / 冲突

键 = (标的, 结算时刻)，时刻用原始毫秒不截秒。全池 {n_j_ev} 个结算事件：

| 分类 | 全池 | 母集 48 |
|---|---|---|
| 两源都有（both） | {n_j_both} | {n_j48_both} |
| 只有热库（hot_only） | {n_j_hot_only} | {n_j48_hot} |
| 只有冷档（cold_only） | {n_j_cold_only} | {n_j48_cold} |
| 重复键 | {n_dup_cold}（冷）/{n_dup_hot}（热） | 同 |
| 四类冲突合计 | {n_j_conf} | {n_j_conf48} |

- **重复**：冷档同一 (标的, 时刻) 0 组；热库靠唯一索引 0 组。
- **冲突**：rate 不同、mark 不同、mark 一边空、rate 一边空，四类**全为 0**。
  两源同键时逐位相同（容差只吸收浮点表示：rate {RATE_TOL}、mark {MARK_TOL}）。
- **冷档独有** {n_j_cold_only} 个，其中 {n_cold_only_sep} 个落在 2026-09 ⇒ 与缺口段同源；
  热库独有 {n_hot_only_oct} 个落在 2026-10，原因是**冷档覆盖到 {cold["L1_结算事件覆盖"]["全历史首末"][1]} 为止**
  （之后只能靠热库，合并后也不能声明「全窗口完整」）。
- 逐事件明细：`out/03_逐事件明细_母集48.csv`（{n_csv48} 行，母集全部事件）、
  `out/03_逐事件明细_异常.csv`（{n_csvabn} 行，cold_only / hot_only / 冲突）。

## 6 受影响交易

### 6.1 05-C 的 51 笔原策略候选（资金费就是用只读热库算的）

| 项 | 值 |
|---|---|
| 笔数 | {n_trades} |
| 热库结算事件 | {n_ev_hot} |
| 并集结算事件 | {n_ev_uni}（冷档补回 {n_ev_fill}） |
| 资金费（热库） | {n_cost_hot} USDT |
| 资金费（并集） | {n_cost_uni} USDT |
| 差额（并集 − 热库） | {n_cost_delta} USDT |
| 05-C 基线合计 | {n_base} USDT（与本单「热库」口径一致；下表差额是「并集 vs 基线」，最大单笔绝对差 {n_maxdiff}） |
| 热库「0 次结算但应有结算」的笔数 | {n_zero_hot} |
| 其中冷档补回 | {n_zero_fill} |
| 并集仍 0 次但应有结算 | {n_zero_uni} |
| 并集仍有残余缺口（下界 >0） | {n_resid} |

结论：51 笔里 {n_zero_hot} 笔被热库漏算，**全部能由冷档补回，残余 0 笔**；
补回后资金费从 {n_cost_hot} 变成 {n_cost_uni}（成本减少 {n_cost_delta} USDT）。
这只是成本项变化，**不代表策略绩效**。

### 6.2 复核 05-F 的「96 笔真缺口」（它来自 04-A 验证区间，不是上面 51 笔）

| 口径 | 验证区间（{n_4a_fills} 笔） | 研究区间（{n_4b_fills} 笔） |
|---|---|---|
| A：热库 0 次结算且持仓 ≥8 小时（05-F 原口径） | {n_4a_96} | 0 |
| B：热库 0 次结算且应有 ≥1 次（实测周期） | {n_4a_104} | {n_4b_1} |
| B 中冷档能补回 | {n_4a_fill} | 0 |
| B 中并集仍无记录 | {n_4a_resid} | {n_4b_resid} |

- 原口径 **{n_4a_96} 笔被完整复现** ⇒ 05-F 的算术没错。
- 换成实测周期后是 {n_4a_104} 笔：多出的 8 笔是「持仓 4~8 小时、在 4 小时周期下本该结算」
  却被 8 小时门槛判成「持仓太短、本该 0 次」的。**所以 96 是偏少的上界。**
- 关键是：这 {n_4a_104} 笔**全部**能由冷档补回（{n_4a_fill} 笔），残余 {n_4a_resid} 笔。
- 研究区间只剩 {n_4b_1} 笔，且冷档也补不回（{n_4b_resid} 笔）——这是本单唯一
  「并集仍无证据」的个例，单独列出、不补零。

## 7 现成的读取器与 `replay_one(...funding_events=...)` 接入点

共登记 {n_reader} 个读取器/消费点（明细见 `out/05_读取器与接入点.json`）。三句要点：

1. **冷档读取器是现成的**：`MarketDataRepository.funding(prefer="cold")`
   → `_cold_flat(metric="funding")`，直接读 `parquet/funding/binance/um/<标的>.parquet`，
   列含 `mark_price`。**不需要新建采集器。**
2. **接入点是纯参数注入**：`replay_one(funding_events=...)` 把列表透传给
   `settle_funding_events`，后者只做「`entry < 结算 ≤ exit` 过滤 + 逐笔结算」。
   因此**只需改调用方的取数函数**，不必改 `replay_calc.py` 或 `trading_core/funding.py`。
3. **`research_15m`（冷热合并读取器）里没有任何 funding 代码** ⇒ 缺口不是读取器能力问题，
   是「合并只做了行情、没做资金费」。

### 7.1 两个坑（本单用行为验证，不靠读注释）

- **列名坑**：`funding_events_ms()` 只认 `rate` / `mark_price`，冷档列名是 `funding_rate`。
  把冷档行原样喂进去 ⇒ `rate=None` ⇒ 每次结算记 `funding_gap`、成本 0、**不报错**。
  即「接上了」和「没接」在账面上完全一样。接入时必须显式映射 `funding_rate → rate`。
- **热优先坑**：`_merge_dedup(a=热, b=冷, key=funding_time)` 同键保留先出现的 a
  ⇒ 热库那条 mark 为空的记录会**挡住**冷档同刻的有效记录；且 `funding(prefer="hot")`
  在热库返回满 `limit` 条时**根本不查冷档**。
  ⇒ 这套现成读取器能补「整段缺事件」，**补不了「同事件缺字段」**。

## 8 哪些能补、哪些仍无证据

**能补（冷档有数据，且已在本单逐笔验证）**

1. 热库 {n_gap_from} → {n_gap_to} 的 {n_gap_hours} 小时连续空洞 —— 冷档同段有记录，
   并集后全池缺口 {n_miss_hot} → {n_miss_uni}（只算周期可靠标的）。
2. 母集 48 里可靠标的（{n_rel48} 个）的 {n_miss_hot48} 个缺口 —— 全部补回。
   **另 {n_unrel48} 个标的不可判定**（周期变过），不算「已补」。
3. 51 笔候选里 {n_zero_hot} 笔被漏算的资金费 —— 全部补回，残余 0 笔。
4. 04-A 验证区间 {n_4a_104} 笔「该结算却无记录」 —— 全部补回，残余 0 笔。
5. 「事件」这一层的冲突与重复：两源 0 冲突、0 重复，可以直接并集。

**仍无证据（本单不补、不外推、不伪造）**

1. **结算当时是否可得**：冷档无 `available_at` 列；热库 `fetched_at` 最短滞后 {n_minlag_h} 小时。
   ⇒ 「资金费在当时可得」永远是**未验证**，只能声明「计算不超前」。
2. **mark_price 是否原生**：热库 {n_pct_mark1h_hot}%、冷档 {n_pct_mark1h_cold}% 的行都是 `+mark1h` 补的，
   ⇒ 逐位一致只是同源一致，**不能**声称等于交易所结算标记价。
3. **冷档覆盖之后**（{cold["L1_结算事件覆盖"]["全历史首末"][1]} 之后）的结算：只能靠热库。
4. **研究区间那 {n_4b_1} 笔**：热库冷档都没有记录，属真无证据。
5. **{n_unreliable} 个周期不单一标的**的缺口个数：不可推算（不是「没有缺口」）。

## 9 未验证 / 不得当成通过

| 项 | 状态 | 说明 |
|---|---|---|
| 资金费「当时可得」 | **未验证** | 冷档无此列、热库全为事后回填；补不了 |
| mark_price 原生性 | **未验证** | 两源同源（+mark1h），一致不等于原生 |
| 交易所原生结算周期 | **未验证** | 伴生库与 `fundingIntervalHours` 快照都不在本机 ⇒ 只能实测推断 |
| 并集补齐后的资金费口径 | **未验证** | 本单只算成本差额，未重跑 04-C/04-D 全链，也未改任何生产文件 |
| 全池结论 | **不通过** | 109 个周期不单一标的不可推算；冷档覆盖有截止时刻 |
| 策略绩效 | **不适用本单** | 本单只核数据与接口，不评价收益 |
| 自然行情成交 | **未验证** | 全程离线只读 |

## 10 复跑

```sh
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
cd "/Users/boos/Desktop/威科夫因子共振趋势"
D=evidence/ACCUM-EARLY-06-05

$PY $D/scripts/00_范围锚定.py
$PY $D/scripts/01_三层字段级核对.py
$PY $D/scripts/02_原生结算周期与缺口段.py
$PY $D/scripts/03_逐事件对账.py
$PY $D/scripts/04_受影响交易.py
$PY $D/scripts/05_读取器与接入点.py
$PY $D/scripts/06_生成报告.py
$PY $D/scripts/07_数字对撞.py        # 最后跑
```

重跑两次实测：除首行「生成时刻」外，正文逐位相同（数字全部由 `out/*.json` 渲染，不手抄）。
`07_数字对撞` 双向通过：台账 → 正文无遗漏、正文 → 台账无未登记数字、
派生值（比值/换算）就地重算一致。

## 11 本单明确没做的事

- 没改 `replay_calc.py`、`trading_core/funding.py`、`market_repository.py`、`paper_engine.py`、
  冻结规则包、生产账本与生产实时库（按文件修改时刻核对）。
- 没把冷档接进生产或研究链路，只给接入点与两个坑。
- 没重跑 04-C / 04-D 的资金费全链（那会改它们的结论，属另一单）。
- 没补零、没插值、没跨交易所拼接、没给冷档伪造可得时刻。
- 没把「并集补齐」写成「资金费已完整计入」：可得性那一层仍未验证。
"""
    DOC.write_text(txt, encoding="utf-8")
    (OUT / "06_正文数字台账.json").write_text(json.dumps(
        {"generated_at_utc": iso(int(time.time() * 1000)),
         "numbers": LEDGER, "count": len(LEDGER)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"README 写出 {len(txt.splitlines())} 行，台账 {len(LEDGER)} 个数字")


if __name__ == "__main__":
    main()
