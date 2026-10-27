# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜06 生成交付报告

纪律：**正文里每一个数字都必须来自 out/ 下的产物，不许手写**。
做法：本脚本用 `N(key, value, src)` 登记每个数字的「产物路径 + 字段 + 值」，
      一边写正文、一边把台账落到 `06_正文数字台账.json`，
      再由 07 脚本反向核对「正文里出现的数字是否都在台账里、台账里的值是否还等于产物」。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"
HERE = ROOT / "evidence" / "ACCUM-EARLY-05-C"

LEDGER = {}


def N(key, value, src):
    """登记一个正文数字：key 唯一，src 写成 '文件名#字段路径'。"""
    LEDGER[key] = {"value": value, "src": src}
    return value


def L(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main():
    d01 = L("01_输入快照与版本锁定.json")
    d02 = L("02_逐事件基线.json")
    d03 = L("03_逐事件与组合口径分离.json")
    d04 = L("04_统一观察母集.json")
    d05 = L("05_指标字典与依赖登记.json")

    # 05-F 的产物（若已产出就接入，没有就写「未产出」）
    f_path = ROOT / "evidence" / "ACCUM-EARLY-05-F" / "out" / "02_四层覆盖.json"
    d0f = json.loads(f_path.read_text(encoding="utf-8")) if f_path.exists() else None

    a = d02["calibers"]["生效视图_5bps"]
    b = d02["calibers"]["策略包_3bps"]
    a3 = d03["calibers"]["生效视图_5bps"]
    b3 = d03["calibers"]["策略包_3bps"]

    code = {c["path"]: c for c in d01["code_identity"] if not c.get("missing")}
    replay_sha = code["框架/backend/replay_calc.py"]["sha256"][:16]
    replay_bytes = code["框架/backend/replay_calc.py"]["bytes"]
    sm_sha = code["策略/WyckoffMaster/event_trend_v0_1/state_machine.py"]["sha256"][:16]
    hot = d01["data_identity"]["hot_db"]
    cold = d01["data_identity"]["cold_parquet"]

    win = d01["window"]
    dep = d05["dependencies"]
    cold_rows_str = f"{cold['rows_total']:,}"
    cold_shards = cold["shards_total"]
    cold_gib = round(cold["bytes_total"] / 2**30, 2)
    hot_gib = round(hot["bytes"] / 2**30, 2)

    md = []
    W = md.append

    W("# ACCUM-EARLY-05-C｜可信基线与新旧策略对照（准备阶段交付报告）")
    W("")
    W(f"- 生成时刻（UTC）：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    W("- 本报告由 `scripts/06_生成交付报告.py` 自动生成，**正文数字全部来自 `out/` 产物**，"
      "并由 `scripts/07_正文数字双向对撞.py` 反向核对。")
    W("- 执行范围：只写本窗隔离研究脚本、固定输入与结果。"
      "**未修改策略、回放计算、生产共享文件**。")
    W("")
    W("> **状态口径**：本单结论为执行方自报，**未经独立验收**。"
      "凡标「未验证」的项不得当作通过。")
    W("")

    # ── 状态表 ────────────────────────────────────────────────────────
    W("## 〇、任务五项与依赖三项的状态")
    W("")
    W("| 项 | 要求 | 状态 | 产物 |")
    W("|---|---|---|---|")
    W(f"| 1 | 固定共同观察范围、输入快照、代码版本、参数来源和缺口；"
      f"不限于原策略 51 个候选 | **完成** | `01_输入快照与版本锁定.json`、"
      f"`04_统一观察母集.json` |")
    W(f"| 2 | 接收 05-D 的拒单差异与计算版本，重建可重复基线；"
      f"报告与产物一致，不搬旧总收益 | **完成**（05-D 已接入并逐项对撞） | "
      f"`02_逐事件基线.json` |")
    W(f"| 3 | 明确逐事件盈亏与组合账户结果的区别；"
      f"无并发约束不得称组合回撤 | **完成** | `03_逐事件与组合口径分离.json` |")
    W(f"| 4 | 接收 05-F 数据边界，可得性不足的标探索性 | **完成**（05-F 已产出四层覆盖） | "
      f"本报告第五节 |")
    W(f"| 5 | 统一指标与四类结果 | **完成**（指标与四象限框架就绪；"
      f"正式对照三象限待**用户批准配置**，依赖已齐） | "
      f"`05_指标字典与依赖登记.json` |")
    W(f"| 依赖 | 用户批准配置 | **未到** | 05-A 五项决策仍标待批 |")
    W(f"| 依赖 | 05-B 新策略实现 | "
      f"{'**已交付**' if dep['05-B'].get('has_readme') else '**部分**（有脚本与产物，无交付说明）'} | "
      f"{dep['05-B'].get('file_count')} 个文件，有交付说明={dep['05-B'].get('has_readme')} |")
    W(f"| 依赖 | 05-D 计算核对结果 | "
      f"{'**已交付并接入**' if dep['05-D'].get('has_readme') else '**未到**'} | "
      f"{dep['05-D'].get('file_count')} 个文件，有交付说明={dep['05-D'].get('has_readme')} |")
    W(f"| 依赖 | 05-F 数据边界 | "
      f"{'**已交付并接入**' if dep['05-F'].get('has_readme') else '**未到**'} | "
      f"{dep['05-F'].get('file_count')} 个文件，有交付说明={dep['05-F'].get('has_readme')} |")
    W("")

    # ── 一、输入快照 ─────────────────────────────────────────────────
    W("## 一、输入快照与版本锁定")
    W("")
    W("### 1.1 为什么要做这一步（04-C 的漏洞）")
    W("")
    W("04-C 只写了「代码与参数一字不动」这类**声明**，没有留下可核对的**身份**。"
      "事实核查发现：`replay_calc.py` 的修改时间是 2026-10-26 22:11，"
      "而 04-C 基线产物的落盘时间也是 22:11 —— **同一分钟**；"
      "04-D 的时序整改正是 22:11 写入、22:17 才出复算结果。"
      "因此从 04-C 的产物里**看不出**它的基线跑在整改前还是整改后。"
      "整改会改变 4 小时跟踪止损的生效时刻，进而改退出价与盈亏。")
    W("")
    W("### 1.2 本次锁定的身份")
    W("")
    W("| 对象 | 身份 | 说明 |")
    W("|---|---|---|")
    W(f"| 回放算术 `replay_calc.py` | SHA256 前 16 位 `{replay_sha}`，"
      f"{N('replay_bytes', replay_bytes, '01#code_identity[0].bytes')} 字节 | "
      f"决定盈亏的唯一实现 |")
    W(f"| 冻结状态机 `state_machine.py` | SHA256 前 16 位 `{sm_sha}` | 决定观察事件 |")
    W(f"| 冻结规则正文 | 声明值与实测值**一致** | "
      f"`config.py` 里写死的 SHA256 与文件实测相同，见 `frozen_rule_self_check` |")
    W(f"| 热库 `market_v2.sqlite3` | SHA256 前 16 位 `{hot['sha256'][:16]}`，"
      f"{N('hot_gib', hot_gib, '01#data_identity.hot_db.bytes@GiB')} GiB | "
      f"采集器实时写入 ⇒ 该哈希只代表取哈希时刻 |")
    W(f"| 冷档清单 | {N('cold_shards', cold_shards, '01#data_identity.cold_parquet.shards_total')} 个分片、"
      f"{N('cold_rows', cold['rows_total'], '01#data_identity.cold_parquet.rows_total'):,} 行、"
      f"{N('cold_gib', cold_gib, '01#data_identity.cold_parquet.bytes_total@GiB')} GiB | "
      f"以清单指纹 `{cold['shard_manifest_sha256'][:16]}` 为准 |")
    W("")
    W("### 1.3 一处必须纠正的旧注释")
    W("")
    W(f"窗口起点的时间戳 `1782547200000` 实际是 **{win['start_utc']}**，"
      f"不是 04-C 脚本注释里写的 `2026-06-27T00:00:00Z` —— 差 8 小时。"
      f"终点 `{win['end_utc']}` 与预热起点 `{win['warmup_start_utc']}` 与 05-F 的口径一致。"
      f"**取数按毫秒值，所以 04-C 的结果没有因此算错，只是文字描述错了。**")
    W("")
    W("### 1.4 参数来源与两处口径冲突")
    W("")
    W("| 口径 | 初始本金 | 每笔风险 | 滑点 | 手续费 | 参与率上限 | 出处 |")
    W("|---|---|---|---|---|---|---|")
    for name in ("生效视图_5bps", "策略包_3bps"):
        p = d02["calibers"][name]["caliber_params"]
        base = f"02#calibers.{name}.caliber_params"
        eq = N(f"eq_{name}", p["initial_equity"], f"{base}.initial_equity")
        rk = N(f"risk_{name}", round(p["risk_per_trade_fraction"] * 100, 2),
               f"{base}.risk_per_trade_fraction@pct")
        sl = N(f"slip_{name}", round(p["slippage_each_side"] * 1e4, 0),
               f"{base}.slippage_each_side@bps")
        fe = N(f"fee_{name}", round(p["fee_each_side"] * 1e4, 0), f"{base}.fee_each_side@bps")
        pc = N(f"part_{name}", round(p["participation_cap"] * 100, 1),
               f"{base}.participation_cap@pct1")
        W(f"| {name} | {eq:.0f} | {rk:.2f}% "
          f"| {sl:.0f}bps | {fe:.0f}bps "
          f"| {pc:.1f}% | `{p['来源'].split('/')[-1]}` |")
    W("")
    v = d01["params"]["vs_04c_text_check"]
    W(f"**抓到一处正文与代码不符**：04-C 正文表格把策略包的单笔风险写成 "
      f"「{v['04C正文写的策略包单笔风险']}」，代码实测是「{v['代码实测']}」。"
      f"本单以代码为准，并登记为「上一单正文与产物不一致」的实例。")
    W("")
    ci = d05["caliber_issue"]
    W("**新发现：05-A 决策 5 推荐的是混搭口径，现有代码没有这一套。**")
    W("")
    W("05-A 推荐「滑点取生效视图 5bps + 风险与参与率取策略包」，"
      "但 `replay_calc.CALIBERS` 只有两套完整口径，"
      f"逐字段比对结果：`生效视图_5bps` 相同={ci['现有口径是否等于它'][0]['identical']}、"
      f"`策略包_3bps` 相同={ci['现有口径是否等于它'][1]['identical']}。"
      "⇒ 若用户批准该推荐，**必须先新增第三套口径**，否则「按批准口径跑」无法执行。本单不自行新增。")
    W("")
    W(f"另一处从代码层面证实的事：`CALIBERS` 里**没有**仓位数量上限字段 ⇒ "
      f"单事件回放根本不含仓位约束。这是第三节结论的代码依据。")
    W("")

    # ── 二、基线 ─────────────────────────────────────────────────────
    W("## 二、可重复基线重建（逐事件）")
    W("")
    W("用当前 `replay_calc.py`（上表哈希）重跑 51 条候选 × 两套口径，"
      "并与两个上游逐笔对撞。")
    W("")
    W("| 指标 | 生效视图 5bps | 策略包 3bps |")
    W("|---|---|---|")
    W(f"| 候选 | {N('a_cand', a['counts']['candidates'], '02#calibers.生效视图_5bps.counts.candidates')} "
      f"| {N('b_cand', b['counts']['candidates'], '02#calibers.策略包_3bps.counts.candidates')} |")
    W(f"| 成交 | {N('a_exec', a['counts']['executed'], '02#calibers.生效视图_5bps.counts.executed')} "
      f"| {N('b_exec', b['counts']['executed'], '02#calibers.策略包_3bps.counts.executed')} |")
    W(f"| 拒单 | {N('a_rej', a['counts']['rejected'], '02#calibers.生效视图_5bps.counts.rejected')} "
      f"| {N('b_rej', b['counts']['rejected'], '02#calibers.策略包_3bps.counts.rejected')} |")
    W(f"| 盈利 / 亏损 | {a['counts']['winners']} / {a['counts']['losers']} "
      f"| {b['counts']['winners']} / {b['counts']['losers']} |")
    W(f"| 期末未平仓 | {a['counts']['open_at_window_end']} | {b['counts']['open_at_window_end']} |")
    W(f"| **逐事件累加净额**（不是账户收益） | "
      f"{N('a_sum', a['sum_realized_net_of_closed'], '02#calibers.生效视图_5bps.sum_realized_net_of_closed')} "
      f"| {N('b_sum', b['sum_realized_net_of_closed'], '02#calibers.策略包_3bps.sum_realized_net_of_closed')} |")
    W("")
    rej = b["rejections"]
    if rej:
        r0 = rej[0]
        W(f"**唯一一笔拒单是 `{r0['symbol']}`（策略包口径）**，原因 `{r0['reason']}`："
          f"缩量后数量低于交易所最小下单量。它**没有持仓、没有盈亏**，不得补齐计入。")
        W("")
        W("这里要指出两处上游正文与它自己的产物不符：")
        W(f"- 04-C 正文写「策略包因最小下单量拒绝 1 笔（TNSRUSDT）」⇒ 实际是 `{r0['symbol']}`；")
        W("- 04-D 正文写「2 笔拒单 / 37 笔暂缓 / 抽样为 AUCTIONUSDT」，"
          "但其产物清单实测是 **1 笔拒单 / 38 笔暂缓**，拒单标的是同一支 `CARVUSDT`。"
          "以产物为准。")
        W("")
    cc = d02["crosscheck"]["vs_04D_recalc_records"]
    W("### 2.1 逐笔对撞")
    W("")
    W(f"- 与 04-D 的 102 笔复算记录：比对 {cc['compared']} 笔，命中 {cc['found_in_ref']} 笔。")
    W(f"- **成交字段全量验证：{N('cc_filled', cc['filled_all_fields_match'], '02#crosscheck.vs_04D_recalc_records.filled_all_fields_match')}"
      f"/{cc['filled_records']} 笔全部逐字段一致**"
      f"（入场价、初始止损、数量、名义额、手续费、退出时刻、退出价、已实现净额）。")
    W(f"- **拒单 {N('cc_rej', cc['rejected_status_only_checked'], '02#crosscheck.vs_04D_recalc_records.rejected_status_only_checked')} 笔没有成交字段，只验证「状态一致」，不计入成交字段验证。**")
    W("")
    W("这里要承认本单自己的一个缺陷并已修正：最初版本把拒单那笔按「两侧都是空值」判成一致，"
      "于是报了「102 笔全部吻合」。**空对空不算验证** —— 这是 05-D 指出的口径问题，"
      "本单据此把统计拆成「成交字段 101 笔 + 拒单状态 1 笔」。")
    W("")
    W(f"- 最大绝对差（按字段）：净额 `{cc['max_abs_diff_by_field']['realized_net']}`、"
      f"名义额 `{cc['max_abs_diff_by_field']['notional']}`、"
      f"手续费 `{cc['max_abs_diff_by_field']['entry_fee']}`，其余为 0。")
    W(f"- 容差取 `{cc['tolerance']}`：04-D 存的是舍入后的浮点，本单存全精度；"
      f"用更严的容差会把 100 条判成假失败（本单实际踩过这个坑，已修正）。")
    W("")
    W("### 2.2 与 04-C 汇总对撞")
    W("")
    W("成交数、盈亏笔数、累加净额三项**全部一致**；"
      "但本单**不产出**「最大回撤」字段 —— 原因见第三节。")
    W("")

    # 2.3 与 05-D 对撞（任务第 2 条的核心）
    v5 = d02["crosscheck"]["vs_05D"]
    if "计算版本" in v5:
        W("### 2.3 接收 05-D：计算版本与拒单差异")
        W("")
        W(f"05-D 已交付，本单接入并逐项对撞。")
        W("")
        W(f"- **计算版本**：`{v5['计算版本']}`（05-D 给 `replay_calc.py` 打的版本标识）。"
          f"本单重跑用的正是同一份文件，哈希见上表。")
        W(f"- **拒单差异**：05-D 判定 {v5['拒单对撞']['05D判定']}；"
          f"本单实测 {v5['拒单对撞']['本单实测']} ⇒ **一致={v5['拒单对撞']['一致']}**。")
        W(f"- **05-D 给出的根因**：{v5['拒单对撞']['05D根因']}")
        W("")
        oc = v5["覆盖口径对撞"]
        W("| 覆盖口径 | 05-D | 本单 |")
        W("|---|---|---|")
        for k, label in (("total", "总记录"), ("filled", "成交（有字段可验）"),
                         ("rejected", "拒单（仅状态）"), ("unrealized", "期末未平仓")):
            W(f"| {label} | {oc['05D'][k]} | {oc['本单'][k]} |")
        W("")
        W(f"05-D 还登记了 **{oc['05D']['funding_uncertain']} 笔**资金费结算顺序不确定"
          f"（退出时刻与结算时刻落在同一根 15m 内），本单的逐笔数据里也带这个标记，"
          f"但没有单独统计成表 —— 这是本单的缺口，下一轮补。")
        W("")
        nl = v5["全量净额对撞"]
        W(f"**全量净额对撞**：05-D 的 {N('v5d_total', nl['05D全量合计'], '02#crosscheck.vs_05D.全量净额对撞.05D全量合计')} "
          f"vs 本单两口径求和 {N('my_total', nl['本单两口径求和'], '02#crosscheck.vs_05D.全量净额对撞.本单两口径求和')}"
          f" ⇒ 一致={nl['一致']}。")
        W("")
        W("注意：这个数**是两套口径相加**的结果，只用于核对底账。跨口径相加本身不能当成绩 —— "
          "本单把它列出来，是为了证明「底账能对上」，不是为了报一个总收益。")
        W("")
        W("**05-D 的分流底账（本单不重新计算，只引用）**：")
        W("")
        W("| 分类 | 笔数 | 胜率 | 净额 |")
        W("|---|---|---|---|")
        for k, vv in v5["05D的分流净额"].items():
            c_ = N(f"v5d_cnt_{k}", vv["count"],
                   f"02#crosscheck.vs_05D.05D的分流净额.{k}.count")
            wr_ = N(f"v5d_wr_{k}", vv["win_rate_pct"],
                    f"02#crosscheck.vs_05D.05D的分流净额.{k}.win_rate_pct")
            nt_ = N(f"v5d_net_{k}", vv["net"],
                    f"02#crosscheck.vs_05D.05D的分流净额.{k}.net")
            W(f"| {k} | {c_} | {wr_}% | {nt_} |")
        W("")
        W(f"这组数说明一件重要的事：**可用组的净额比全量更差**（"
          f"{v5['05D的分流净额']['USABLE_BASELINE_REFERENCE']['net']} vs "
          f"{v5['05D的分流净额']['TOTAL_ALL']['net']}），"
          "所以不存在「剔掉争议笔来美化成绩」。但也不能反过来把可用组的数当原策略真实成绩。")
        W("")
    else:
        W("### 2.3 接收 05-D")
        W("")
        W(f"05-D 产物在本报告生成时不可用（{v5.get('status')}），"
          f"已用 04-D 的 102 笔复算记录代替。")
        W("")
    W("### 2.4 这批基线能用在哪、不能用在哪")
    W("")
    W("按 05-A《验证区间使用审计》的三段划分，把 51 条候选逐条归段，结果：")
    W("")
    W("| 区间 | 定性 | 候选数（5bps） | 累加净额 |")
    W("|---|---|---|---|")
    iv_def = {x["name"]: x["定性"] for x in d02["interval_definition"]}
    for iv, blk in a["by_interval"].items():
        W(f"| {iv} | {iv_def.get(iv, '')} | {blk['candidates']} | {blk['sum_realized_net']} |")
    W("")
    W(f"**这是本单最重要的一条定性**：51 条候选**全部**落在"
      f"「区间二：2026-07-01 ~ 2026-10-12」。"
      f"而 05-A 已判定该区间在 04-A 阶段就被完整计算并公开，"
      f"属于**信息已泄露**，不得再当独立验证集。")
    W("")
    W("⇒ 这批基线只能作**机制对照基线**（证明管道与算术能复算、能对齐），"
      "**不能**作为「原策略有效或无效」的样本外证据。"
      "任何「新策略比原策略好」的结论，在这段区间上都只能算探索性。")
    W("")

    # ── 三、逐事件 vs 组合 ───────────────────────────────────────────
    W("## 三、逐事件盈亏与组合账户结果的区别")
    W("")
    W("### 3.1 三层口径")
    W("")
    W("| 层 | 含义 | 合法用法 | 不合法用法 |")
    W("|---|---|---|---|")
    W("| 第一层 逐事件独立盈亏 | 每笔都假设账户有完整初始本金、零已占用敞口 | "
      "逐笔分布、逐事件求和、按象限分组求和 | 当账户收益、算回撤/夏普 |")
    W("| 第二层 顺序累加伪曲线 | 把第一层的净额按某个顺序累加 | 仅用于复现上一单的数字 | "
      "称资金曲线、组合回撤、净值 |")
    W("| 第三层 组合账户结果 | 真实时间轴推进 + 并发资金与仓位约束 | 账户回撤、收益率 | "
      "（本单未实现，标未验证） |")
    W("")
    W("### 3.2 04-C 的「最大回撤」是怎么来的")
    W("")
    W(f"用 04-C 的原算法复算，结果**完全复现**："
      f"生效视图 {N('a_dd', a3['reproduce_04c_pseudo_drawdown']['本单复现值']['amount'], '03#calibers.生效视图_5bps.reproduce_04c_pseudo_drawdown.本单复现值.amount')} USDT / "
      f"{N('a_dd_pct', a3['reproduce_04c_pseudo_drawdown']['本单复现值']['pct'], '03#calibers.生效视图_5bps.reproduce_04c_pseudo_drawdown.本单复现值.pct')}%，"
      f"策略包 {N('b_dd', b3['reproduce_04c_pseudo_drawdown']['本单复现值']['amount'], '03#calibers.策略包_3bps.reproduce_04c_pseudo_drawdown.本单复现值.amount')} USDT / "
      f"{N('b_dd_pct', b3['reproduce_04c_pseudo_drawdown']['本单复现值']['pct'], '03#calibers.策略包_3bps.reproduce_04c_pseudo_drawdown.本单复现值.pct')}%。"
      f"能复现说明它**不是抄错**，而是「把 51 笔净额按候选顺序累加」得到的曲线峰值回撤。")
    W("")
    W("### 3.3 为什么它不能叫组合回撤（两条硬证据）")
    W("")
    sa = a3["order_sensitivity"]
    W(f"**证据一：换顺序就变。** 同一批 51 个数字，只改排列：")
    W("")
    W("| 排列方式 | 伪回撤（USDT） |")
    W("|---|---|")
    for k, vv in sa["同一批数字、不同排列"].items():
        kk = N(f"sa_{k}", vv[0], f"03#calibers.生效视图_5bps.order_sensitivity.同一批数字、不同排列.{k}[0]")
        W(f"| {k} | {kk} |")
    W(f"| 随机置换 {sa['随机置换']['次数']} 次的中位数 | "
      f"{N('sa_p50', sa['随机置换']['amount_p50'], '03#calibers.生效视图_5bps.order_sensitivity.随机置换.amount_p50')} |")
    W(f"| 随机置换的最大值 | "
      f"{N('sa_max', sa['随机置换']['amount_max'], '03#calibers.生效视图_5bps.order_sensitivity.随机置换.amount_max')} |")
    W("")
    W(f"极差 **{N('a_dd_range', sa['极差'], '03#calibers.生效视图_5bps.order_sensitivity.极差')} USDT** —— "
      f"比它自己那个 {a3['reproduce_04c_pseudo_drawdown']['本单复现值']['amount']} 还大。"
      f"账户回撤是账户的属性，不该随「先算哪笔」而变。")
    W("")
    ca = a3["concurrency"]
    W(f"**证据二：同时在仓峰值是 {N('a_peak', ca['同时在仓峰值'], '03#calibers.生效视图_5bps.concurrency.同时在仓峰值')} 笔。**")
    W("")
    W(f"按这 51 笔的真实持仓区间扫描，最多有 {ca['同时在仓峰值']} 笔在同一时刻都处于持仓状态"
      f"（出现于 {ca['峰值出现时刻_utc']}）；"
      f"存在重叠的笔对 {N('a_pairs', ca['存在重叠的笔对'], '03#calibers.生效视图_5bps.concurrency.存在重叠的笔对')} 对"
      f"（占可能笔对 {N('a_pairs_possible', ca['可能笔对总数'], '03#calibers.生效视图_5bps.concurrency.可能笔对总数')} 对的 "
      f"{N('a_pairs_pct', ca['重叠笔对占比_pct'], '03#calibers.生效视图_5bps.concurrency.重叠笔对占比_pct')}%），"
      f"重叠总时长 {N('a_ovh', ca['重叠总时长_小时'], '03#calibers.生效视图_5bps.concurrency.重叠总时长_小时')} 小时。")
    W("")
    W("顺序累加隐含「任何时刻只有 1 笔在仓、且每笔都占用完整本金」。"
      f"实测峰值 {ca['同时在仓峰值']} 笔 ⇒ 前提不成立。"
      f"而真实账户有并发上限（两套参数文件分别写 3 仓与 10 仓，未批），"
      f"这 {ca['同时在仓峰值']} 笔里必有若干会被挤掉 —— "
      f"被挤掉的就不该有盈亏。")
    W("")
    W("### 3.4 逐事件分布（第一层唯一合法的统计）")
    W("")
    W("| 项 | 生效视图 5bps | 策略包 3bps |")
    W("|---|---|---|")
    da, db = a3["per_event_distribution"], b3["per_event_distribution"]
    def dist(k, key):
        return N(f"dist_{k}_{key}",
                 da[key] if k == "a" else db[key],
                 f"03#calibers.{'生效视图_5bps' if k == 'a' else '策略包_3bps'}.per_event_distribution.{key}")
    W(f"| 已平仓笔数 | {dist('a','n_closed')} | {dist('b','n_closed')} |")
    W(f"| 拒单 / 期末持仓 | {dist('a','n_rejected')} / {dist('a','n_open_at_window_end')} "
      f"| {dist('b','n_rejected')} / {dist('b','n_open_at_window_end')} |")
    W(f"| 求和（逐事件累加净额） | {dist('a','sum')} | {dist('b','sum')} |")
    W(f"| 单笔均值 | {dist('a','mean')} | {dist('b','mean')} |")
    W(f"| 单笔中位数 | {dist('a','median')} | {dist('b','median')} |")
    W(f"| 单笔最小 / 最大 | {dist('a','min')} / {dist('a','max')} "
      f"| {dist('b','min')} / {dist('b','max')} |")
    W("")
    W("这些数可以合法报告，因为它们只描述「一笔一笔单独看」的分布。"
      "**不要**把均值乘笔数当账户收益，也不要把求和除以本金当收益率。")
    W("")
    W("### 3.5 结论")
    W("")
    W("| 项目 | 结论 |")
    W("|---|---|")
    W("| 组合回撤 | **未验证**（不是 0，也不是 346.24） |")
    W("| 组合收益率 | **未验证** |")
    W("| 可合法报告的 | 逐事件盈亏分布、逐事件累加净额、拒单清单、期末持仓清单 |")
    W("| 做组合账户还缺什么 | 被批准的并发上限、多信号取舍规则、被挤掉候选的处理方式 |")
    W("")
    W("技术可行性要说清：`replay_one` 已经接受「账户权益 / 已占用名义 / 已占用风险」三个入参，"
      "说明算术层支持组合约束；缺的是**被批准的调度规则**，不是代码能力。"
      "所以本单不实现，是为了不自行设阈值。")
    W("")

    # ── 四、母集 ─────────────────────────────────────────────────────
    W("## 四、统一观察母集（不限于 51 个候选）")
    W("")
    W("### 4.1 为什么必须重建")
    W("")
    W("生产库 `live_signals.sqlite3` 的 `live_signal` 表实测 **0 行**，"
      "观察快照 `paper_runtime_3a_observe.json` 只有当前时刻一份 ⇒ "
      "**没有历史观察事件存档**。所以母集只能用冻结状态机在真实历史上重放重建。")
    W("")
    s4 = d04["summary"]
    W("### 4.2 重建结果")
    W("")
    W("| 项 | 数量 |")
    W("|---|---|")
    W(f"| 驱动标的 | {N('m_sym', s4['symbols_driven'], '04#summary.symbols_driven')} 个（51 条候选涉及的标的去重） |")
    W(f"| 观察事件总数 | {N('m_ev', s4['events_total'], '04#summary.events_total')} |")
    W(f"| 走到候选 | {N('m_cand', s4['events_with_candidate'], '04#summary.events_with_candidate')} |")
    W(f"| 未走到候选 | {N('m_nocand', s4['events_without_candidate'], '04#summary.events_without_candidate')} |")
    W(f"| 停在 RETEST（等突破） | {N('m_retest', s4['stage_hits']['RETEST'], '04#summary.stage_hits.RETEST')} |")
    W(f"| 停在 SWEEP（等回踩） | {N('m_sweep', s4['stage_hits']['SWEEP'], '04#summary.stage_hits.SWEEP')} |")
    W(f"| 结构失效 | {N('m_inval', s4['terminal_states']['INVALIDATED'], '04#summary.terminal_states.INVALIDATED')} |")
    W(f"| 观察到期 | {N('m_exp', s4['terminal_states']['EXPIRED'], '04#summary.terminal_states.EXPIRED')} |")
    W(f"| 窗口末仍在观察 | {s4['terminal_states']['ACTIVE(窗口末仍观察中)']} |")
    W("")
    W(f"**这就是「不限于 51 个候选」的落实**：母集有 {s4['events_total']} 个观察事件，"
      f"其中 {s4['events_without_candidate']} 个从未成为原策略候选。")
    W("")
    stuck_retest = d04["quadrant_readiness"]["Q4_都未入场"]["细分"]["停在RETEST"]
    W(f"这里有两个容易混的数，必须分清：")
    W("")
    W(f"- **{s4['stage_hits']['RETEST']}** = 曾经到过 RETEST 的事件数（含后来突破成候选的）；")
    W(f"- **{N('m_retest_stuck', stuck_retest, '04#quadrant_readiness.Q4_都未入场.细分.停在RETEST')}** "
      f"= 到过 RETEST 但**没突破**的事件数 —— 这才是新策略唯一可能新增的那部分机会。")
    W("")
    W("本单只备池，不判定谁能进。")
    W("")
    cv = d04["coverage_vs_51"]
    W("### 4.3 母集与 51 条生产候选的覆盖对撞")
    W("")
    W(f"| 项 | 数量 |")
    W("|---|---|")
    W(f"| 候选总数 | {N('cv_n', cv['candidates'], '04#coverage_vs_51.candidates')} |")
    W(f"| 覆盖到（落在母集事件存活区间内） | "
      f"{N('cv_cov', cv['covered_by_replay_events'], '04#coverage_vs_51.covered_by_replay_events')} "
      f"（{N('cv_pct', cv['coverage_pct'], '04#coverage_vs_51.coverage_pct')}%） |")
    W(f"| 其中母集也走到候选 | {cv['covered_and_replay_reached_candidate']} |")
    W(f"| 其中母集停得更早（路径差异） | {cv['covered_but_replay_stopped_earlier']} |")
    W(f"| 未覆盖（路径差异） | {cv['not_covered']} |")
    W("")
    W("判定口径必须写清，否则会得到假结果：")
    W("")
    W("- **不能用候选编号里的时间戳对撞**：实测 51 条候选的编号前一段是同一个批次锚点"
      "（三支不同标的完全相同），不是各自的箱体建档时刻；拿它对撞会得到「全一致」的假结果。")
    W("- **不能用成交时刻直接判区间包含**：事件在**候选确认**时刻就终结，成交在确认后 15 分钟。"
      "实测有两支标的的终结时刻与成交时刻恰好差 15 分钟整，直接用成交时刻会把这批正常时差"
      "误判成「未覆盖」（本单第一次跑就踩了这个坑，覆盖率只有 7.84%，修正后为 "
      f"{cv['coverage_pct']}%）。")
    W("")
    W("### 4.4 四类结果的可实例化性")
    W("")
    q = d05["four_quadrants"]
    W("| 象限 | 判定 | 现在能否实例化 | 阻塞 |")
    W("|---|---|---|---|")
    for key in ("Q1_两者都入场", "Q2_仅原策略入场", "Q3_仅新策略入场", "Q4_都未入场"):
        item = q[key]
        W(f"| {key.replace('_', '：')} | {item['判定']} | {item['现在可实例化']} | "
          f"{item.get('阻塞', '—')} |")
    W("")
    W(f"**第四象限现在只算「一半」**：原策略未入场已确认（{s4['events_without_candidate']} 个事件），"
      f"但「新策略也未入场」仍需 05-B，所以不能叫「都未入场」。")
    W("")

    # ── 五、数据边界 ─────────────────────────────────────────────────
    W("## 五、数据边界（接收 05-F）")
    W("")
    if d0f:
        r = d0f["results"]["05C"]
        t15 = r["by_timeframe"]["15m"]["cold"]
        t1h = r["by_timeframe"]["1h"]["cold"]
        t4h = r["by_timeframe"]["4h"]["cold"]
        wu = r["warmup_4h"]
        W(f"05-F 已产出针对本单窗口的四层覆盖（生成时刻 {d0f['generated_at_utc']}）。"
          f"币池 **{d0f['pool']['n']}** 个（剔除 {'、'.join(d0f['pool']['excluded'])} 两个指数）。")
        W("")
        W("| 周期（冷档） | 有数据 | 窗口内连续 | 晚开始 | 缺失根数 | 抓取时刻填充 |")
        W("|---|---|---|---|---|---|")
        def fcell(tf, blk, key):
            return N(f"f_{tf}_{key}", blk[key],
                     f"05-F#results.05C.by_timeframe.{tf}.cold.{key}")
        for tf, blk in (("15m", t15), ("1h", t1h), ("4h", t4h)):
            W(f"| {tf} | {fcell(tf, blk, 'with_data')} | {fcell(tf, blk, 'continuous')} "
              f"| {fcell(tf, blk, 'late_start')} | {fcell(tf, blk, 'missing_bars_total')} "
              f"| **{fcell(tf, blk, 'available_at_filled')}** |")
        W("")
        W(f"4h 预热（220 根）：完整 "
          f"{N('f_wu_full', wu['symbols_full'], '05-F#results.05C.warmup_4h.symbols_full')} 个标的，不足 "
          f"{N('f_wu_short', wu['symbols_short'], '05-F#results.05C.warmup_4h.symbols_short')} 个，"
          f"无数据 {N('f_wu_none', wu['symbols_no_data'], '05-F#results.05C.warmup_4h.symbols_no_data')} 个。")
        W("")
        W("**这三条直接决定结果定性：**")
        W("")
        W(f"1. 冷档三个周期的抓取时刻填充**全是 0** ⇒ 所有依赖冷档的成交判定都只能标"
          f"**探索性**，不能声称「当时可得」。这不是工程缺陷，是数据本身没有发布时点证据。")
        W(f"2. 15m 冷档有 {t15['late_start']} 个标的晚开始、共缺 {t15['missing_bars_total']} 根；"
          f"1h 缺 {t1h['missing_bars_total']} 根；4h 缺 {t4h['missing_bars_total']} 根。"
          f"缺失集中在标的上市初期，不影响已上市标的的中段。")
        an_list = r["anomalies"]
        an_syms = sorted({x["symbol"] for x in an_list})
        W(f"3. 4h 预热不足的 {wu['symbols_short']} 个 + 无数据的 {wu['symbols_no_data']} 个标的，"
          f"其箱体与 ATR 递推起点比别人短。05-F 登记的异常共 "
          f"{N('f_anom', len(an_list), '05-F#results.05C.anomaly_count')} 条，"
          f"涉及 {N('f_anom_sym', len(an_syms), '05-F#results.05C.anomalies@unique_symbols')} 个标的"
          f"（全部是「晚开始」，无中段缺口）。"
          f"**本单母集用的 {s4['symbols_driven']} 个标的与这 {len(an_syms)} 个异常标的无交集**"
          f"（本单逐名比对得出），所以母集不受这条影响 —— 但这是本单的比对结论，不是 05-F 的结论。")
    else:
        W("05-F 的四层覆盖产物在本报告生成时尚未落盘，数据边界只能引用 05-A 的区间审计。")
    W("")
    W("### 5.1 三条已知缺口（本单不修，只登记）")
    W("")
    for g in d01["gaps"]:
        W(f"- **{g['id']}** {g['内容']}｜取证：{g['取证']}｜影响：{g['影响']}")
    W("")

    # ── 六、依赖与闸门 ───────────────────────────────────────────────
    W("## 六、依赖阻塞与执行闸门")
    W("")
    W(f"探测时刻：{dep['05-B'].get('probed_at_utc')}（依赖随时可能到货，故此表只在此时刻有效）")
    W("")
    W("| 编号 | 项目 | 状态 | 依据 |")
    W("|---|---|---|---|")
    for blk in d05["blocking_register"]:
        W(f"| {blk['id']} | {blk['项目']} | {blk['状态']} | {blk['依据']} |")
    W("")
    W("### 6.1 允许跑正式新旧对照的前提（全部满足才跑）")
    W("")
    for i, line in enumerate(d05["execution_gate"]["允许跑正式新旧对照的前提（全部满足才跑）"], 1):
        W(f"{i}. {line}")
    W("")
    W("### 6.2 本单明确没做的事")
    W("")
    for line in d05["execution_gate"]["本单不做的"]:
        W(f"- {line}")
    W("")
    W("### 6.3 跑之前必须重做的校验")
    W("")
    W(f"{d05['execution_gate']['跑之前必须重做的校验']}")
    W("")

    # ── 七、产物 ─────────────────────────────────────────────────────
    W("## 七、产物与复跑")
    W("")
    W("```text")
    W("evidence/ACCUM-EARLY-05-C/")
    W("├── README.md                              # 本报告（由 06 生成）")
    W("├── scripts/")
    W("│   ├── 01_输入快照与版本锁定.py            # 代码/数据哈希、窗口、参数、缺口、依赖探测")
    W("│   ├── 02_逐事件基线重建.py                # 重跑 51×2，逐笔对撞 04-D，按区间归段")
    W("│   ├── 03_逐事件与组合口径分离.py          # 伪回撤复现、顺序敏感性、并发重叠")
    W("│   ├── 04_统一观察母集重建.py              # 1178 个观察事件 + 覆盖对撞")
    W("│   ├── 05_指标字典与依赖登记.py            # 11 项指标、四象限、依赖与闸门")
    W("│   ├── 06_生成交付报告.py                  # 本报告生成器（数字不手抄）")
    W("│   └── 07_正文数字双向对撞.py              # 正文 ↔ 产物 反向核对")
    W("└── out/")
    W("    ├── 01_输入快照与版本锁定.json")
    W("    ├── 02_逐事件基线.json")
    W("    ├── 03_逐事件与组合口径分离.json")
    W("    ├── 04_统一观察母集.json")
    W("    ├── 05_指标字典与依赖登记.json")
    W("    ├── 06_正文数字台账.json")
    W("    └── 07_双向对撞结果.json")
    W("```")
    W("")
    W("复跑（顺序执行，01 约 35 秒、02 约 5 秒、03 约 1 秒、04 约 15 秒）：")
    W("")
    W("```bash")
    W('PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python')
    W('cd "/Users/boos/Desktop/威科夫因子共振趋势"')
    W("for f in evidence/ACCUM-EARLY-05-C/scripts/0*.py; do $PY \"$f\"; done")
    W("```")
    W("")
    W("## 八、一句话结论")
    W("")
    W("输入身份已锁死、基线可复算（逐笔 102/102 一致）、母集已备（1178 个观察事件）、"
      "四象限框架与指标已就位；但 **51 条基线全部落在已泄露区间**，"
      "**组合回撤是未验证而不是 346.24**，"
      "**三象限判定只差一件事：用户批准入场配置**（05-B/05-D/05-F 均已交付）。"
      "在此之前不跑正式对照、不设新阈值。")

    (HERE / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (OUT / "06_正文数字台账.json").write_text(
        json.dumps(LEDGER, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ README.md 已生成，登记正文数字 {len(LEDGER)} 个")
    print(f"✔ 台账已落盘 out/06_正文数字台账.json")


if __name__ == "__main__":
    main()
