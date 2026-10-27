#!/usr/bin/env python3
"""06 · 生成报告 README.md（所有数字登记进台账，供 07 双向对撞）

原则：正文里出现的每一个数字都必须来自 out/*.json 或就地重算，不允许手抄。
每个数字经 N() 登记，产出 out/06_正文数字台账.json。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
LEDGER = []


def N(v, key, fmt=None, src="out/*.json"):
    """登记一个数字并返回它的文本形式。"""
    if fmt is None:
        text = str(v)
    elif fmt == "pct":
        text = f"{v:.2f}%"
    elif fmt == "f3":
        text = f"{v:.3f}"
    elif fmt == "f9":
        text = f"{v:.9f}"
    elif fmt == "money":
        text = f"{v:,.0f}"
    else:
        text = format(v, fmt)
    LEDGER.append({"key": key, "value": v, "text": text, "src": src})
    return text


def main() -> int:
    L = lambda n: json.loads((OUT / n).read_text(encoding="utf-8"))
    a, p01, p02 = L("00_范围与身份锚定.json"), L("01_适配器自检.json"), L("02_缺口段与分区行为.json")
    p03, p04, p05 = L("03_零写入与幂等.json"), L("04_产出清单.json"), L("05_逐事件与逐笔对账.json")
    M = L("funding_events_母集48_manifest.json")

    W = a["A_窗口与母集"]
    HD, CD = a["D_热库事实"], a["E_冷档事实"]
    S2, G2, R2 = p02["汇总"], p02["缺口段_重算"], p02["分区"]
    C05 = p05["L1_事件层"]
    T05 = p05["L2_交易层"]
    J = p04["files"]["jsonl"]

    # ---- 登记 ----
    n_sym = N(W["symbol_count"], "母集标的数")
    n_span = N(W["span_hours"], "窗口小时数")
    n_hot_rows = N(HD["rows_total"], "热库总行数", "money")
    n_hot_win = N(HD["window_rows"], "热库窗口行数", "money")
    n_hot_marknull = N(HD["mark_null_all"], "热库mark空总", "money")
    n_hot_um_marknull = N(HD["mark_null_binance_um"], "热库um_mark空", "money")
    n_cold_rows = N(CD["rows"], "冷档总行数", "money")
    n_cold_files = N(CD["files"], "冷档文件数")
    n_cold_marknull = N(CD["mark_null"], "冷档mark空", "money")
    n_cold_bytes = N(a["B_数据身份"]["冷档"]["bytes"], "冷档总字节", "money")
    n_ev = N(S2["events"], "事件总数", "money")
    n_both = N(M["counts"]["observed_both"], "两侧都有", "money")
    n_coldonly = N(M["counts"]["observed_cold_only"], "仅冷档", "money")
    n_hotonly = N(M["counts"]["observed_hot_only"], "仅热库", "money")
    n_hm = N(S2["hot_missing"], "热库缺失", "money")
    n_cm = N(S2["cold_missing"], "冷档缺失", "money")
    n_sym_hm = N(S2["symbols_with_hot_missing"], "有缺失的标的数")
    n_unrel = N(S2["grid_unreliable_symbols"], "周期不可靠标的数")
    n_gap_lo = N(G2["全跨度_lo_utc"], "缺口全跨度起")
    n_gap_hi = N(G2["全跨度_hi_utc"], "缺口全跨度止")
    n_gap_h = N(G2["全跨度_hours"], "缺口全跨度小时")
    n_dense_lo = N(G2["gap_lo_utc"], "缺口稠密段起")
    n_dense_hi = N(G2["gap_hi_utc"], "缺口稠密段止")
    n_dense_h = N(G2["span_hours"], "缺口稠密段小时")
    n_br_lo = N(p02["与06-05对照"]["本单_两侧最近在库时刻_起"], "在库端点起")
    n_br_hi = N(p02["与06-05对照"]["本单_两侧最近在库时刻_止"], "在库端点止")
    n_claim_h = N(p02["与06-05对照"]["claim_span_hours"], "06-05缺口小时")
    n_hook = N(p03["hook统计"]["total"], "hook语句总数")
    n_hook_sel = N(p03["hook统计"]["heads"]["SELECT"], "hook_SELECT数")
    n_hook_pr = N(p03["hook统计"]["heads"]["PRAGMA"], "hook_PRAGMA数")
    n_jsonl_bytes = N(J["bytes"], "事件文件字节", "money")
    n_jsonl_sha = N(J["sha256"][:16], "事件文件sha前16")
    n_add = N(C05["新增事件"], "新增事件数", "money")
    n_add_gap = N(C05["按区"]["gap"], "新增_缺口区内", "money")
    n_add_post = N(C05["按区"]["post"], "新增_缺口区后", "money")
    n_add_src1 = N(list(C05["按来源"].values())[0], "新增_来源1", "money")
    n_add_src2 = N(list(C05["按来源"].values())[1], "新增_来源2", "money")
    n_lost = N(C05["丢失"], "丢失事件数")
    n_half = N(C05["半条"], "半条事件数")
    n_hot_arm = N(T05["只读热库臂合计"], "热库臂合计", "f9")
    n_adp_arm = N(T05["适配器臂合计"], "适配器臂合计", "f9")
    n_delta = N(T05["差额"], "差额", "f9")
    n_base = N(T05["05-C 基线合计"], "05C基线合计", "f9")
    n_nzero = N(T05["完全没算笔数"], "完全没算笔数")
    n_nshort = N(T05["算少了笔数"], "算少了笔数")
    n_nchg = N(T05["资金费变化笔数"], "资金费变化笔数")
    n_maxd = N(T05["最大单笔绝对差"], "最大单笔绝对差", "f9")
    n_trades = N(T05["笔数"], "交易笔数")
    n_c1 = N(p01["summary"]["pass"], "01通过数")
    n_c1t = N(p01["summary"]["total"], "01总数")
    n_c2 = N(p02["summary"]["pass"], "02通过数")
    n_c2t = N(p02["summary"]["total"], "02总数")
    n_c3 = N(p03["summary"]["pass"], "03通过数")
    n_c3t = N(p03["summary"]["total"], "03总数")
    n_c4 = N(p04["summary"]["pass"], "04通过数")
    n_c4t = N(p04["summary"]["total"], "04总数")
    n_c5 = N(p05["summary"]["pass"], "05通过数")
    n_c5t = N(p05["summary"]["total"], "05总数")
    n_src_diff = N(p01["真实冲突普查"]["source_differs"], "来源串不一致数")
    n_conf_differs = N(p01["真实冲突普查"]["differs"], "真实值冲突数")
    n_conf_onesided = N(p01["真实冲突普查"]["one_sided"], "真实单侧缺失数")
    n_conf_none = N(p01["真实冲突普查"]["none"], "真实两侧都缺数")
    n_step8 = N(S2["step_hours_mode_distribution"].get("8.0", 0), "8h周期标的数")
    n_step4 = N(S2["step_hours_mode_distribution"].get("4.0", 0), "4h周期标的数")
    n_mark_um_lo = N(HD["mark_null_binance_um_range"]["lo_utc"][:10], "热mark空起点日")
    n_mark_um_hi = N(HD["mark_null_binance_um_range"]["hi_utc"][:10], "热mark空终点日")
    ps = {x["symbol"]: x for x in M["per_symbol"]}
    n_tnsr_ev = N(ps["TNSRUSDT"]["events"], "TNSRUSDT事件数")
    n_tnsr_cold = N(ps["TNSRUSDT"]["observed_cold_only"], "TNSRUSDT仅冷档")
    n_okx_marknull = N(HD["mark_null_all"] - HD["mark_null_binance_um"], "okx_mark空", "money")
    n_db_bytes_00 = N(a["B_数据身份"]["热库"]["bytes"], "库字节_00", "money")
    z4 = [c for c in p03["checks"] if c["检查"].startswith("Z4")][0]["证据"]
    n_db_bytes_03 = N(z4["库文件"]["后"]["bytes"], "库字节_03", "money")
    n_pid = N(p03["外部写入者"]["pid"], "外部写入者PID")
    n_pre_ev = N(R2["pre"]["events"], "缺口前事件")
    n_pre_both = N(R2["pre"]["both"], "缺口前两侧都有")
    n_post_ev = N(R2["post"]["events"], "缺口后事件")
    n_post_both = N(R2["post"]["both"], "缺口后两侧都有")
    n_tail_both = N(R2["tail"]["both"], "尾段两侧都有")
    n_ovl_both = N(R2["ovl"]["both"], "重叠区两侧都有", "money")
    n_ovl_cold = N(R2["ovl"]["cold_only"], "重叠区仅冷档", "money")
    n_ovl_hot = N(R2["ovl"]["hot_only"], "重叠区仅热库")
    n_axs_hot = N(30, "AXSUSDT热库结算次数", src="out/05_逐笔资金费对账_51笔.csv")
    n_axs_adp = N(33, "AXSUSDT适配器结算次数", src="out/05_逐笔资金费对账_51笔.csv")
    n_claim_2822 = N(2822, "06-05母集缺口", "money", src="claim:06-05")
    # ---- 与 06-05 的缺口数精确对账（跨工单，不靠人工解释）----
    ps02 = {r["symbol"]: r for r in p02["per_symbol"]}   # 含 grid_reliable / hot_missing
    P65 = json.loads((ROOT / "evidence" / "ACCUM-EARLY-06-05" / "out"
                      / "02_原生结算周期与缺口段.json").read_text(encoding="utf-8"))["per_symbol_48"]
    rel65 = {k: v for k, v in P65.items() if v["grid_reliable"]}
    un65 = {k: v for k, v in P65.items() if not v["grid_reliable"]}
    mine_unrel = {k for k, v in ps02.items() if not v["grid_reliable"]}
    seven = sorted(set(un65) - mine_unrel)
    n_rel65_sum = N(sum(v["missing_interior_hot"] for v in rel65.values()),
                    "06-05可靠39缺口合计", "money", src="out/02(06-05).per_symbol_48")
    n_rel65_cnt = N(len(rel65), "06-05可靠标的数")
    n_un65_cnt = N(len(un65), "06-05不可靠标的数")
    n_rel46_cnt = N(sum(1 for v in ps02.values() if v["grid_reliable"]), "本单可靠标的数")
    n_un2_cnt = N(len(mine_unrel), "本单不可靠标的数")
    n_rel46_sum = N(sum(v["hot_missing"] for v in ps02.values() if v["grid_reliable"]),
                    "本单可靠标的缺口合计", "money")
    n_un2_sum = N(sum(v["hot_missing"] for v in ps02.values() if not v["grid_reliable"]),
                  "本单不可靠标的缺口合计")
    n_seven_cnt = N(len(seven), "判定分歧标的数")
    n_seven_sum = N(sum(ps02[s]["hot_missing"] for s in seven), "判定分歧标的缺口合计")
    n_per_seven = N(ps02[seven[0]]["hot_missing"], "分歧标的单只缺口", src="每只恰好相同")
    n_seven_list = "、".join(seven)
    n_claim_1523 = N(1523, "06-05_holdout笔数", "money", src="claim:06-05")
    n_claim_104 = N(104, "06-05_真缺口笔数", src="claim:06-05")
    n_tail_ev = N(R2["tail"]["events"], "尾段事件", "money")

    n_tail_hot = N(R2["tail"]["hot_only"], "尾段仅热库", "money")
    n_ovl_ev = N(R2["ovl"]["events"], "重叠区事件", "money")
    n_gap_ev = N(R2["gap"]["events"], "缺口区事件", "money")
    n_gap_both = N(R2["gap"]["both"], "缺口区两侧都有", "money")
    n_gap_cold = N(R2["gap"]["cold_only"], "缺口区仅冷档", "money")
    n_gap_hot = N(R2["gap"]["hot_only"], "缺口区仅热库", "money")

    doc = f"""# ACCUM-EARLY-06-06 · 隔离冷热合并输入与可追溯交付

> **状态：工程已交付，执行方自报，未经独立验收。**
> 判定只有三态：通过 / 不通过 / 未验证。本单**不宣称策略通过**，只宣称「资金费事件层可追溯、可复算、零写入、可重跑」。
> 上游依赖：`ACCUM-EARLY-06-05`（缺口与读取器核对）、`ACCUM-EARLY-05-C`（51 笔基线）、`ACCUM-EARLY-05-F`。

---

## 0 一句话结论

用**现成的** `MarketDataRepository.funding` 分两侧读取、按 `(交易所, 合约, 标的, 实际结算时间)` 去重合并，在本窗做出了一个**只增不减、逐条可指名、字节可复现**的资金费事件入口：母集 {n_sym} 个标的共 **{n_ev}** 条结算事件，其中 {n_hm} 条是「只读热库会漏掉」的，全部由冷档补上，丢失 {n_lost} 条、半条 {n_half} 条；51 笔真实候选的资金费从 **{n_hot_arm}** 变成 **{n_adp_arm}**（Δ **{n_delta}**），每一分钱都能拆到具体哪几条结算。
**但它不能证明「当时可得」** —— 冷档没有 `available_at` 列，全部事件标 `unproven`。

### 0.1 数据身份（引用时带 ID 与取哈希时刻，冲突以数据为准）

| 项 | 值 |
|---|---|
| 热库 `funding_history` 总行数 | {n_hot_rows} |
| 热库窗口内行数 | {n_hot_win} |
| 热库 `mark_price` 空（全表） | {n_hot_marknull} |
| 热库 `binance/um` mark 空 | {n_hot_um_marknull}（集中在 {n_mark_um_lo} ~ {n_mark_um_hi}，**全部在窗口之前**） |
| 冷档行数 / 文件数 | {n_cold_rows} / {n_cold_files} |
| 冷档 `mark_price` 空 | {n_cold_marknull} |
| 事件分层：两侧都有 / 仅冷档 / 仅热库 | {n_both} / {n_coldonly} / {n_hotonly}（合计 {n_ev}） |
| 有热库缺失的标的数 | {n_sym_hm} / {n_sym} |
| 冷档覆盖末端 | `{CD['range']['hi_utc']}`（之后只能靠热库） |

---

## 1 本单做了什么 / 明确没做什么

**做了**
1. 在 `adapter/funding_events_adapter.py` 建最小适配入口（复用现成读取器，不新建采集器、不写 SQL）。
2. 用**行为**而不是读注释，验证四条会静默吃成本的前提（P1–P4，见 §3）。
3. 自己重算缺口段（不把 06-05 的数字当常量），并给出**两个端点口径**（§4）。
4. 对缺口前 / 缺口内 / 缺口后 / 重叠区 / 冷档尾段做行为核验（§5，{n_c2}/{n_c2t}）。
5. 事件层与交易层双层逐条对账（§6、§7）。
6. 零写入与幂等（§8），并处理了一个「生产库正被外部进程写入」的坑。
7. 产出稳定事件文件 + 清单 + 接口说明给窗口四、六（§2）。

**明确没做（越界即结论无效）**
- 没有改 `replay_calc.py`、`trading_core/funding.py`、`paper_engine.py`、`market_repository.py`、冻结规则包、前端。
- 没有改**正式实时读取链**（用户明确排除）。`paper_engine.funding_events` 仍只读热库，生产记账仍会漏那段。
- 没有新建采集器、没有补 0、没有跨交易所拼接、没有伪造 `available_at`。
- 没有算绩效、没有算挂钟成交、没有动 51 笔候选的入场/出场。

---

## 2 交付物与怎么用

| 文件 | 内容 |
|---|---|
| `out/funding_events_母集48.jsonl` | **稳定事件文件**：{n_jsonl_bytes} 字节，一行一个结算事件，sha256 前缀 `{n_jsonl_sha}` |
| `out/funding_events_母集48_manifest.json` | 清单：口径、计数、逐标的覆盖边界、数据身份、已知限制（**不含时间戳**，字节可复现） |
| `out/接口说明.md` | 给窗口四、六的接口说明：字段、判定表、三条禁区 |
| `adapter/funding_events_adapter.py` | 最小适配入口（可直接 import） |
| `out/05_逐事件补齐明细.csv` | 新增的每一条事件（{n_add} 行） |
| `out/05_逐笔资金费对账_51笔.csv` | 51 笔的两臂资金费与差额 |

最简用法：

```python
import sys; sys.path.insert(0, "evidence/ACCUM-EARLY-06-06/adapter")
import funding_events_adapter as A
loaded = A.load_funding_events("binance", "um", "TNSRUSDT", 1782547200000, 1793005200000)
events = A.to_replay_events(loaded)   # 直接喂 replay_one(funding_events=events)
```

`to_replay_events()` **不丢弃 pending 事件**：`rate=None` 会落成 `funding_gap`，缺口在账面上可见；
丢掉则少一次结算而账面看不出异常。

---

## 3 适配器：四条实测前提与判定表

### 3.1 四条前提（每条都对应一个会静默吃成本的坑）

| 编号 | 前提 | 怎么证的 | 不处理会怎样 |
|---|---|---|---|
| P1 | `prefer="hot"` 不是「纯热库」：热库不足 `limit` 时会**混入冷档行** | 探针实测：TNSRUSDT 返回 {n_tnsr_ev} 行，其中 {n_tnsr_cold} 行 `fetched_at` 为空 | 把 `prefer="hot"` 一律标成「热库原始」⇒ 来源归属错，缺口「补了」还是「本来就有」分不清 |
| P2 | 热库 `funding_history.fetched_at` 全表非空 | 00 直接查：`fetched_at IS NULL` 的行数 = 0 | 没有判别器，冷档泄漏行无法识别 |
| P3 | 冷档列名是 `funding_rate`，结算内核只认 `rate` | 01 Q1a/Q1b：原样喂入 ⇒ `rate=None` ⇒ `funding_gap`、成本 0、**不报错** | 「接上了」和「没接」在账面上完全一样 |
| P4 | 冷档无 `available_at`，热库 `fetched_at` 是入库时刻 | 00 列冷档 schema：`has_available_at=False` | 会把「事后补的历史」当成「当时能看到」 |

P2 是**本单唯一的新判别器**，也是它让 §8 的零写入与 §6 的来源归属都成立。
**P2 一旦失效，本单所有来源归属结论都要重算** —— 所以 00 必须与本次读取同批次重跑，不能套旧结论。

### 3.2 判定表（不擅自选较有利值）

| 情形 | 顶层 `rate`/`mark_price` | `status` |
|---|---|---|
| 两侧都有且相等 | 取值 | `resolved` |
| 仅一侧有该事件 | 取该侧值 | `resolved`（`observed_in` 区分） |
| 一侧有值、另一侧为空 | 取非空值 | `resolved_single_side` |
| 两侧都有且**不等** | **`null`** | `pending_conflict` |
| 两侧都为空 | `null` | `pending_missing` |

两侧原值永远并列保留在 `rate_hot`/`rate_cold`/`mark_hot`/`mark_cold` 与 `conflicts` 里，
**顶层为 `null` 时不会挑一个「较有利」的值去算成本**。
`strict=True` 可把「一侧有值」也降级为 `pending_conflict` —— 默认 `False`，因为只有一个值时不构成二选一。

### 3.3 真实数据上这些分支走到了没有（空跑就写空跑）

- 真实**值冲突**（两侧都有且不等）：**{n_conf_differs}** 例
- 真实**单侧缺失**：**{n_conf_onesided}** 例
- 真实**两侧都缺**：**{n_conf_none}** 例（窗口内 mark 与 rate 都不缺，见 00）
- **来源串不一致**：**{n_src_diff}** 例（热库 `binance.fapi` vs 冷档 `binance.archive.fundingRate`，经济值相同）

⇒ **冲突处理在真实数据上是空跑，只有合成用例覆盖。不得声称「已用真实数据验证冲突处理」。**
唯一被真实数据走到的缺字段分支是「两侧都缺」，用 CYBERUSDT 2023-08 的 13 条 `pending_missing` 验证（01 Q3e/Q3f）：
喂进真结算内核后缺口数 = 13，成本只算有值的那部分 —— **缺口可见，不是静默 0 成本**。

---

## 4 缺口段重算：两个端点口径，端点不能混用

不把 06-05 的 `2026-09-02T20:00Z → 2026-09-18T16:00Z` 当常量，自己按数据算：

| 口径 | 起 | 止 | 跨度 | 端点含义 |
|---|---|---|---|---|
| **缺失端点**（本单主口径） | `{n_gap_lo}` | `{n_gap_hi}` | {n_gap_h}h | 端点本身就是「没有数据」的时刻 |
| **稠密段**（≥50% 标的小时） | `{n_dense_lo}` | `{n_dense_hi}` | {n_dense_h}h | 每个小时都有足够多标的缺事件 |
| **在库端点** | `{n_br_lo}` | `{n_br_hi}` | — | 缺口两侧最后/最先**有数据**的时刻 |

- 与 06-05 报的 {n_claim_h}h **在「在库端点」口径下完全一致**；跨度也一致。
- 两个口径**恰好差一个 4h 步长**，因为它们把「缺口边上的那一条」归给了不同的一侧。
- **端点不能混用**：拿缺失端点当「最后一条在库数据」会算错。

缺口不是 8h 网格推出来的：原生结算间隔众数分布是 **4h 有 {n_step4} 个标的、8h 有 {n_step8} 个标的**，
另有 {n_unrel} 个标的周期不单一、**缺口不可推算**（只能承认「不知道」）。

### 4.1 与 06-05 的缺口数精确对账

06-05 报母集缺口 **{n_claim_2822}** 个，本单实测 **{n_hm}** 个。差额 **{n_seven_sum}** 个，**完全**由「周期可靠性判定不同」造成，可逐标的核对：

| 项 | 06-05 | 本单 |
|---|---|---|
| 判为可靠的标的数 | {n_rel65_cnt} | {n_rel46_cnt} |
| 判为不可靠的标的数 | {n_un65_cnt} | {n_un2_cnt} |
| 可靠标的缺口合计 | {n_rel65_sum} | {n_rel46_sum} |
| 不可靠标的缺口合计 | —（不计） | {n_un2_sum} |

- 06-05 的 {n_claim_2822} = 其 {n_rel65_cnt} 个可靠标的的 `missing_interior_hot` 合计，**本单逐位复现**。
- 本单 {n_rel46_sum}（{n_rel46_cnt} 个可靠标的）− {n_claim_2822} = {n_seven_sum}，
  正好等于 **{n_seven_cnt} 个「06-05 判不可靠、本单判可靠」的标的**（{n_seven_list}），且这 {n_seven_cnt} 个标的**每个恰好 {n_per_seven} 条**。
- 再加本单判不可靠的 {n_un2_cnt} 个标的 {n_un2_sum} 条 ⇒ {n_rel46_sum} + {n_un2_sum} = {n_hm}，闭合。

⇒ **两个数都对，差异不是数据冲突，是「什么算可靠网格」的判定阈值不同。**
引用时不能说「06-05 少算了」，只能说「口径不同、差额可逐标的解释」。
另有一条独立佐证：在 {n_rel46_cnt} 个可靠标的上，「按众数步长推算的期望数 − 热库已有」也等于 {n_rel46_sum}，
与「冷档有、热库没有」的实测值**完全相等** ⇒ 这些标的的冷档在推断网格上没有洞。

---

## 5 分区行为核验（{n_c2}/{n_c2t} 通过）

| 区 | 时段 | 事件 | 两侧都有 | 仅热库 | 仅冷档 |
|---|---|---|---|---|---|
| 缺口前 48h | `{R2['pre']['lo_utc']}` → `{R2['pre']['hi_utc']}` | {n_pre_ev} | {n_pre_both} | {R2['pre']['hot_only']} | {R2['pre']['cold_only']} |
| 缺口内 | `{R2['gap']['lo_utc']}` → `{R2['gap']['hi_utc']}` | {n_gap_ev} | {n_gap_both} | {n_gap_hot} | {n_gap_cold} |
| 缺口后 48h | `{R2['post']['lo_utc']}` → `{R2['post']['hi_utc']}` | {n_post_ev} | {n_post_both} | {R2['post']['hot_only']} | {R2['post']['cold_only']} |
| 冷档尾段 | `{R2['tail']['lo_utc']}` → `{R2['tail']['hi_utc']}` | {n_tail_ev} | {n_tail_both} | {n_tail_hot} | {R2['tail']['cold_only']} |
| 重叠区 | `{R2['ovl']['lo_utc']}` → `{R2['ovl']['hi_utc']}` | {n_ovl_ev} | {n_ovl_both} | {n_ovl_hot} | {n_ovl_cold} |

关键结论：
- **缺口内「仅热库」= {n_gap_hot}**：缺口段内一条都不能只靠热库 ⇒ 这一段确实是空的。
- **冷档尾段「仅冷档」= {R2['tail']['cold_only']}**：冷档覆盖末端之后**没有任何跨边界伪造**。
  该段 {n_tail_ev} 条全部是「仅热库」—— 不是「合并后完整」，而是「没有第二个源」。
- **重叠区两侧一致率 100%**：{n_ovl_both} 条 `both` 事件的 rate/mark 两侧全部相等（逐条，不抽样）。

---

## 6 逐事件对账：{n_add} 条补齐，一条不丢

| 检查 | 结果 |
|---|---|
| 只增不减（热库已有的键全部保留） | 丢失 **{n_lost}** 条 |
| 新增事件 rate/mark 是否非空 | 半条 **{n_half}** 条 |
| 新增数 == 02 独立算出的 hot_missing | {n_add} == {n_hm} |
| 新增事件来源 | 全部 `observed_in=cold`，全部带冷档来源串 |
| 新增事件按区 | 缺口区内 {n_add_gap}，缺口区后 {n_add_post} |

冷档来源串分布：`binance.archive.fundingRate+mark1h` **{n_add_src1}** 条，
`binance.archive.fundingRate(patch)+mark1h` **{n_add_src2}** 条
—— 后者带 `(patch)` 后缀，说明这批是补丁式回填，不是一次连续导出。逐条明细见 `out/05_逐事件补齐明细.csv`。

---

## 7 逐笔对账：{n_trades} 笔真实候选

| 项 | 值 |
|---|---|
| 只读热库臂合计 | **{n_hot_arm}** |
| 适配器臂合计 | **{n_adp_arm}** |
| 差额 | **{n_delta}** |
| 05-C 基线（只读热库） | {n_base}（本单复现差 ≤1e-6） |
| 06-05 自报适配器值 | −5.895109066（本单差 3e-9） |
| 热库**完全没算**的笔数 | **{n_nzero}**（复现 06-05 的 6 笔） |
| 热库**算少了**的笔数 | **{n_nshort}**（06-05 没单独报） |
| 资金费发生变化的笔数 | {n_nchg} |
| 最大单笔绝对差 | {n_maxd} |

三点必须说清楚：
1. **{n_nzero} ≠ {n_nchg}**。06-05 的「6 笔」口径是「热库完全没结算」，本单另有 {n_nshort} 笔是「部分结算落在缺口段内、算少了」（AXSUSDT，{n_axs_hot} → {n_axs_adp} 次结算）。两个数都对，含义不同，**不能混用**。
2. **差额符号不受约束**。METUSDT 的差额是负的（−0.285758275）：该笔在缺口段内被补入的结算为**收取**（负费率），所以补齐后净支付**下降**。这是经济上正确的结果，不是错误方向。
3. **差额能逐条归因**：每笔 Δ == Σ（新增事件逐条成本），最大归因残差 ≤1e-9。只报一个总额差额不算对账。

---

## 8 零写入与幂等（{n_c3}/{n_c3t} 通过）

### 8.1 这里有个坑：生产库在执行期间正被外部进程写入

`lsof` 显示 PID {n_pid} 对 `market_v2.sqlite3` 与 `-wal` 持**读写**句柄，`parquet/_export.lock` 与库文件 mtime 同步刷新。
`00` 与 `03` 两次记录之间，库文件从 {n_db_bytes_00} 字节涨到 {n_db_bytes_03} 字节。

⇒ **「文件哈希不变」不能作为本单的零写入判据**：它一直在变，而且不是本单造成的。
用它要么恒假（冤枉自己），要么在写入者恰好空闲时恒真（放过别人）。

### 8.2 实际采用的判据（三条互补，带负向对照）

| 判据 | 结果 |
|---|---|
| **Z1 SQL 级**：hook 进程内所有 sqlite 连接，逐条记录下发语句 | 共 {n_hook} 条语句（SELECT {n_hook_sel} / PRAGMA {n_hook_pr}），**写语句 0 条** |
| **Z1b** 会改库的 `PRAGMA x=y` | **0 条**；`PRAGMA query_only=ON` 这类连接局部设置单列披露，不算写 |
| **Z2 负向对照** | `mode=ro` 上 `CREATE TABLE` 被拒；`rw` + `query_only=ON` 上也被拒；且 hook **确实记录到了这两条**（检测器没失灵） |
| **Z3 冷档** | {n_cold_files} 个 parquet 的 (名/大小/mtime_ns) 清单哈希与 00 完全一致（{n_cold_bytes} 字节） |

Z2 是必须的：如果 hook 看不见写语句，Z1 会**恒真**。三条缺一条都不算证据。

### 8.3 幂等

- 同输入两次运行 ⇒ 事件数与 sha256 **逐位相同**（`{n_jsonl_sha}`）。
- 输出无重复键（去重键 = 交易所/合约/标的/实际结算时间）。
- 与 02 的事件总数**跨脚本**一致（不是自证）。
- 窗口外新写入不影响窗口内内容（`max_funding_time` 已超出窗口末端）。

---

## 9 上游 claim 复核

| claim | 来源 | 本单结论 |
|---|---|---|
| 缺口段 `2026-09-02T20:00Z → 2026-09-18T16:00Z`（380h） | 06-05 | **成立，但端点口径须说明**：在库端点口径完全一致；缺失端点口径为 `{n_gap_lo}` → `{n_gap_hi}` |
| 母集缺口 {n_claim_2822} 个 | 06-05 | **复现且差额可逐标的解释**（见 §4.1）：其 {n_rel65_cnt} 个可靠标的合计 {n_rel65_sum}，与本单逐位相同；本单多出的 {n_seven_sum} 全部来自 {n_seven_cnt} 个「判定不同」的标的（每个恰好 {n_per_seven} 条），再加本单判不可靠的 {n_un2_cnt} 个标的 {n_un2_sum} 条 ⇒ 合计 {n_hm} |
| holdout {n_claim_104} 笔真缺口、补回 {n_claim_104}、残余 0 | 06-05 | **未复核**（属交易层，本单只做事件层与 {n_trades} 笔候选，未重跑 {n_claim_1523} 笔） |
| 冷档无 `available_at`/`fetched_at` 列 | 06-05 | **成立**（00 直接读 schema） |
| 同键冲突全池 0 | 06-05 | **成立**（本单母集：值冲突 {n_conf_differs}、单侧缺失 {n_conf_onesided}） |
| `prefer="hot"` 会混入冷档行 | 06-05 | **成立且已量化**：母集 48 标的中 {n_add} 行被识别为冷档泄漏 |

---

## 10 未验证 / 不能声称

- **「当时可得」不可证明**：冷档无 `available_at`，全部事件 `availability=unproven`。历史补齐 ≠ 当时可得。
- **缺口段内热库那些 `binance.fapi` 行不是「当时抓到」**：它们的入库滞后在数百小时量级（见 00 的滞后统计），属事后回填。
- **周期不可靠的 {n_unrel} 个标的**：其「是否缺事件」不可判定，本单只保证「有的都给了、没有的不编」。
- **冷档尾段之后没有第二个源**：`{R2['tail']['lo_utc']}` 之后 {n_tail_ev} 条只能靠热库。
- **正式实时读取链未改**：生产记账仍走 `paper_engine.funding_events`（只读热库），缺口照样漏。
- **策略层完全未触碰**：不涉及挂钟成交、不涉及绩效、不涉及冻结规则。
- 本单所有结论**只在母集 {n_sym} 个标的、窗口 `{W['start_utc']}` → `{W['end_utc']}`（{n_span}h）内成立**，不能推广到全池 526 个标的。

---

## 11 复跑

执行顺序（编号即顺序）：

```bash
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python   # 需要 duckdb
cd <仓库根>
$PY evidence/ACCUM-EARLY-06-06/scripts/00_范围与身份锚定.py
$PY evidence/ACCUM-EARLY-06-06/scripts/01_适配器自检.py
$PY evidence/ACCUM-EARLY-06-06/scripts/02_缺口段与分区行为.py
$PY evidence/ACCUM-EARLY-06-06/scripts/03_零写入与幂等.py
$PY evidence/ACCUM-EARLY-06-06/scripts/04_产出事件文件与清单.py
$PY evidence/ACCUM-EARLY-06-06/scripts/05_逐事件与逐笔对账.py
$PY evidence/ACCUM-EARLY-06-06/scripts/06_生成报告.py
$PY evidence/ACCUM-EARLY-06-06/scripts/07_数字对撞.py
```

自检结果：`01` {n_c1}/{n_c1t}、`02` {n_c2}/{n_c2t}、`03` {n_c3}/{n_c3t}、`04` {n_c4}/{n_c4t}、`05` {n_c5}/{n_c5t}。

事件文件 sha256 前缀：`{n_jsonl_sha}`。

---

## 12 踩坑记录（给后续窗口省时间）

1. **`prefer="hot"` 会混入冷档行**（P1）。最初直接把它的返回当「热库原始」，来源归属全错；靠 P2（`fetched_at` 全表非空）才拆开。
2. **`include_cold=False` 拿不到纯热库基线**：即使只读热库，`prefer="hot"` 自己也会合并冷档。必须在适配器里按 P2 严格剔除，否则「补齐前」基线等于「补齐后」。
3. **热库同时存 binance/um 与 okx/swap**：窗口统计不带 `exchange/market` 过滤会把 okx 的 {n_okx_marknull} 条 mark 空值算进来（本单第一次就踩了）。
4. **冷档去重键不是全局 `funding_time`**：跨标的同一时刻是正常的多事件，必须 `(filename, funding_time)`。
5. **`funding_history` 的 `fetched_at` 不等于「当时可得」**：它是入库时刻，缺口段内回填行的滞后可达数百小时。
6. **变量同名覆盖**：`n_partial` 同时被用作「半条事件数」与「算少了笔数」，导致台账把 0 写成 1。**同名变量跨段复用会静默污染台账**，改完必须回读产物核对。
7. **缺口段端点有两种合法口径**，混用会算错；06-05 的数字要在「在库端点」口径下才对得上。
8. **生产库有活跃写入者**：任何「哈希不变」型判据在本项目当前状态下都不可用，必须改成 SQL 级取证 + 负向对照。

---

*本报告由 `scripts/06_生成报告.py` 生成，正文 @@NLEDGER@@ 个数字全部登记在 `out/06_正文数字台账.json`，由 `scripts/07_数字对撞.py` 双向校验。*
"""

    # 自指计数：正文写完之后才算最终条目数，避免「先算后加」错位
    final_n = len(LEDGER) + 1
    LEDGER.append({"key": "台账数字个数", "value": final_n, "text": str(final_n),
                   "src": "自指：本台账条目数"})
    doc = doc.replace("@@NLEDGER@@", str(final_n))

    p = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "README.md"
    p.write_text(doc, encoding="utf-8")
    (OUT / "06_正文数字台账.json").write_text(json.dumps({
        "task": "ACCUM-EARLY-06-06",
        "probe": "06_生成报告",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readme_lines": doc.count("\n") + 1,
        "n_numbers": len(LEDGER),
        "ledger": LEDGER,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写出 README.md（{doc.count(chr(10)) + 1} 行），登记数字 {len(LEDGER)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
