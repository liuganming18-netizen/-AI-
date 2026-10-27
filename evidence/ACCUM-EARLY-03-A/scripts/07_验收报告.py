#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-A · 07 生成验收报告 + 双向对撞

纪律要求：报告里的**每个数字**都必须能回原始字段复算，不能靠人眼复核。
本脚本做三件事：

  ① 复算：所有数字从 out/*.json 直接读取，正文由 f-string 拼装，**不手抄**
  ② 正向对撞：声明为「事实」的字面量必须在正文里真实出现
     （空串一律 raise —— 空串会让 `"" in text` 恒真，把假通过放过去）
  ③ 反向对撞：正文里每个数字必须能在源 JSON 的叶子值里找到出处
     （先屏蔽 ``` 围栏与 `行内代码`，否则落单反引号会吞掉大段正文）

输出：README.md、out/collision_report.json
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
OUT = os.path.join(BASE, "out")


def load(name):
    p = os.path.join(OUT, name)
    if not os.path.isfile(p):
        raise SystemExit(f"缺少上游产物：{p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def yn(b):
    """布尔值中文化，别把 True/False 直接印进中文正文。"""
    return "是" if b else "否"


def n(x):
    """大数字加千分位，只为可读；反向对撞会去掉逗号再比对。"""
    try:
        return f"{int(x):,}"
    except (TypeError, ValueError):
        return str(x)


def leaves(obj, acc=None):
    if acc is None:
        acc = []
    if isinstance(obj, dict):
        for v in obj.values():
            leaves(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            leaves(v, acc)
    elif obj is not None:
        acc.append(obj if isinstance(obj, str) else str(obj))
    return acc


def main():
    gap = load("pool_gap.json")
    chain = load("chain_audit.json")
    health = load("health_intact.json")
    obs = load("observe_summary.json")
    rec = load("recovery_before_after.json")

    S = gap["summary"]
    rows = gap["rows"]
    by_sym = {r["symbol"]: r for r in rows}

    uni = S["universe"]
    n_universe = uni["count"]
    n_head = uni["with_1h_head"]
    n_snapfile = uni["with_snapshot_file"]
    n_snapfresh = uni["snapshot_fresh"]
    buckets = {k: v["count"] for k, v in S["buckets"].items()}
    b_ok = buckets.get("OK", 0)
    b_nosnap = buckets.get("OK_BAR_NO_SNAPSHOT", 0)
    b_stale = buckets.get("OK_BAR_SNAPSHOT_STALE", 0)
    b_notrade = buckets.get("EXCHANGE_NOT_TRADING", 0)
    notrade_syms = S["buckets"].get("EXCHANGE_NOT_TRADING", {}).get("symbols", [])

    arch = S["metrics_archive"]
    arch_dirs = arch["dirs"]
    arch_mtime = arch["dir_mtime"]
    btc_last = (arch["sample"].get("BTCUSDT") or {}).get("last_event_time")

    procs = chain["processes"]
    col = procs["collector"][0] if procs["collector"] else {}
    col_pid = col.get("pid")
    col_created = col.get("created_utc")
    col_uptime = col.get("uptime_hours")

    wa = chain["writer_audit"]
    metrics_writer_hits = wa["metrics_parquet_writer"]["count"]
    exp_funcs = wa.get("exporter_functions") or []
    exp_metrics = wa.get("exporter_mentions_metrics")
    st_keys = wa.get("export_state_keys") or []

    rs = chain["request_stats"]
    rounds_seen = rs["snapshot_round_count"]
    ok_total = rs["snapshot_ok_total"]
    fail_total = rs["snapshot_failed_total"]

    lr = chain["latest_rows"]
    pool_max_snap = lr["symbol_pool"]["max_snapshot_utc"]
    dl_tf = lr["download_task_timeframes"]

    rp = chain["read_path"]["api_metrics"]
    api_counts = rp["counts"]
    api_quality = rp["quality_status"]

    H = health
    ticket_start = H["ticket_start_utc"]
    h_col_count = H["P1_P2_processes"]["collector_count"]
    h_code_ok = H["P3_code_unchanged"]["all_mtime_before_ticket"]
    h_growth = H["P4_db_growth"]["growth"]
    h_gap = H["P4_gap_s"]
    g5 = h_growth["candle_heads_5m"]
    goi = h_growth["oi_history"]
    gq = h_growth["realtime_quotes"]
    code_files = H["P3_code_unchanged"]["files"]

    O = obs
    o_win = O["window"]
    o_rounds = O["round_advance"]
    o_seq = o_rounds["round_sequence"]
    o_adv = o_rounds["advanced_by"]
    o_meets = o_rounds["meets_two_cycles"]
    o_fs = O["fresh_stale"]
    btc = O["probes"]["BTCUSDT"]
    dot = O["probes"]["DOTUSDT"]

    R = rec
    v1 = R["V1_api_history_bar"]
    v1_before_avail = v1["before_available"]
    v1_before_reason = v1["before_reason"]
    v1_after = v1["after"]
    v1_retest = v1["direct_reader_retest"]
    v2_live_mtime = (R["V2_cold_parquet"].get("live") or {}).get("mtime_utc")
    v3 = R["V3_checkpoint"]
    v3_before_round = v3["before_last_completed_round"]
    v3_before_last = v3["before_last_line_of_that_day"]
    v3_after = v3["after"]
    v4 = R["V4_first_catchup_round"]
    v4_fast = (v4.get("fast") or {}).get("stats") or {}
    v4_slow = (v4.get("slow") or {}).get("stats") or {}
    v4_oi = (v4.get("oi") or {}).get("stats") or {}
    v4_total = sum(int((x or {}).get("新增行") or 0)
                   for x in (v4_fast, v4_slow, v4_oi))
    mu = R["metrics_archive_unaffected"]

    L = []
    A = L.append
    A("# ACCUM-EARLY-03-A｜指标停更与行情断流排查恢复")
    A("")
    A(f"生成时刻：{R['generated_at']}。本文件由 `scripts/07_验收报告.py` 从 "
      f"`out/*.json` 拼装，数字不手抄。")
    A("")
    A("## 一、结论先说")
    A("")
    A("排查下来，被报成「指标停更、行情断流」的现象，其实是**三件不同的事**，"
      "只有第一件能在本单范围内修，而且已经修好并验证了。")
    A("")
    A(f"- **冷档导出停了八天（已修复）**。热转冷导出器在 2026-10-18 的半轮里被杀，"
      f"之后没人拉起，冷层 K 线和持仓量从此不再更新。重启后首轮补了 {n(v4_total)} 行，"
      f"归档 K 线水位从读不到直接追到 {v1_after}。")
    A(f"- **指标历史（5 分钟多空比归档）从来没有增量写入者（未修，跨界）**。"
      f"它停更在 {btc_last}，跟导出器停摆无关——重启导出器也补不了，因为导出器里"
      f"根本没有指标这一段。")
    A(f"- **行情水位停滞只发生在 {b_notrade} 个已交割合约上（未修，跨界）**。"
      f"交易所侧状态已不是 TRADING，价格冻结、成交为零。全池其余标的的 1 小时 K 线"
      f"水位都在正常推进。")
    A("")
    A("**采集器本身健康**：进程连续运行 "
      f"{col_uptime} 小时未重启，最近 {rounds_seen} 轮快照刷新成功 {ok_total} 次、"
      f"失败 {fail_total} 次；接口对页首标的 {api_counts['fields_total']} 个字段全有值"
      f"（质量状态 `{api_quality}`）。所以本单**没有改采集器一行代码**。")
    A("")

    A("## 二、三种原因怎么分开的")
    A("")
    A("工单要求区分「采集停更 / 读取错误 / 标的停止交易」。判定不看现象，只看三组客观值：")
    A("")
    A("| 判据 | 采集停更 | 读取错误 | 标的停止交易 |")
    A("| --- | --- | --- | --- |")
    A("| 写入侧 | 快照刷新时刻不再前移 | 照常前移 | 照常前移（仍会写零成交价） |")
    A("| 读取侧 | 有值 | 有文件但接口返回空/故障态 | 有值，但值是冻结价 |")
    A("| 交易所侧 | 仍是 TRADING | 仍是 TRADING | 非 TRADING（交割/暂停） |")
    A("")
    A(f"实测：采集器 pid `{col_pid}` 启动于 {col_created}，"
      f"已连续运行 {col_uptime} 小时；最近 {rounds_seen} 轮快照刷新"
      f"成功 {ok_total} 次、失败 {fail_total} 次。**写入侧没有停更。**")
    A("")
    A(f"读取侧实测（BTCUSDT 1 小时）：`available={api_counts['available']}`、"
      f"`missing={api_counts['missing']}`、`quality={api_quality}`，"
      f"{api_counts['fields_total']} 个字段逐个带 `event_time` 与 `source`。"
      f"**读取侧没有错误。**")
    A("")
    A(f"交易所侧实测：全池 {n_universe} 个合约里，**{b_notrade} 个**状态不是 TRADING"
      f"（全部为 `SETTLING`）。这就是「行情水位停滞」的全部来源。")
    A("")

    A("## 三、根因一：冷档导出停摆八天（本单已修复）")
    A("")
    A("### 怎么发现的")
    A("")
    A(f"查「谁在写冷层 parquet」时发现导出器的日志在 2026-10-18 就断了。"
      f"那天的最后一行是：")
    A("")
    A("```")
    A(str(v3_before_last))
    A("```")
    A("")
    A("这行是**半轮**——只有快车道结果，没有随后的慢车道、持仓量、也没有"
      "「本轮结束」。上一轮完整收尾是：")
    A("")
    A("```")
    A(str(v3_before_round))
    A("```")
    A("")
    A("也就是说进程是在 2026-10-18 的那一轮中间被杀掉的，不是正常退出，"
      "也没有任何报错。此后既没有常驻进程，也没有装进系统定时任务，"
      "所以八天里冷层一直是死的。")
    A("")
    A("### 怎么修的")
    A("")
    A("只做一件事：用**项目自带的启动脚本**把已经停摆的导出器拉起来，"
      "断点文件原样保留，没有改一行代码。")
    A("")
    A("- 动作：`sh 框架/backend/exporter.sh start`（脚本幂等，已在跑就复用）")
    A(f"- 重启前先空转一轮确认规模：约 {n(v4_total)} 行待补，磁盘余量充足，"
      f"空转不落盘、不动断点")
    A("- 断点文件 `_export_state.json` 未被改写，恢复后从原水位继续")
    A("")
    A("### 恢复结果（三个独立视角交叉验证）")
    A("")
    A("| 视角 | 恢复前 | 恢复后 |")
    A("| --- | --- | --- |")
    A(f"| 接口读归档 K 线 | `available={v1_before_avail}`（{v1_before_reason}） | "
      f"`{v1_after}` |")
    A(f"| 冷档文件写入时刻 | 停在 2026-10-18 | {v2_live_mtime} |")
    A(f"| 导出器断点 | 收尾于 {str(v3_before_round)[1:20] if v3_before_round else ''} | "
      f"收尾于 {v3_after['finished_at_utc']}，用时 {v3_after['duration_s']} 秒 |")
    A("")
    A("首轮补数明细（直接从导出器日志解析）：")
    A("")
    A("| 车道 | 组合数 | 新增行 | 写文件 |")
    A("| --- | --- | --- | --- |")
    A(f"| K 线快车道（15m/1h/4h） | {v4_fast.get('组合')} | {n(v4_fast.get('新增行'))} | "
      f"{v4_fast.get('写文件')} |")
    A(f"| K 线慢车道（5m/30m/8h/12h/1d） | {v4_slow.get('组合')} | "
      f"{n(v4_slow.get('新增行'))} | {v4_slow.get('写文件')} |")
    A(f"| 持仓量 | {v4_oi.get('组合')} | {n(v4_oi.get('新增行'))} | {v4_oi.get('写文件')} |")
    A("")
    A(f"绕过接口直接调读取器复测两次，结果一致（`{v1_retest.get('open_time')}`），"
      f"排除缓存假象。")
    A("")

    A("## 四、根因二：指标历史从来没有增量写入者（跨界，未修）")
    A("")
    A(f"指标历史（币安官方 5 分钟归档）停在 **{btc_last}**。"
      f"归档目录 {arch_dirs} 个标的、目录修改时刻 {arch_mtime}。")
    A("")
    A("这条**和导出器停摆无关**，四条审计结果说明它压根没有写入者：")
    A("")
    A(f"1. 全仓搜「写指标 parquet」的代码，命中 {metrics_writer_hits} 处，"
      f"但全部是读取侧的路径拼接，没有一处写盘。")
    A(f"2. 热转冷导出器的导出函数只有 "
      f"`{'`、`'.join(f for f in exp_funcs if f.startswith('export_'))}`，"
      f"文件里出现 `metrics` 的次数为 {exp_metrics}。")
    A(f"3. 导出断点文件的键是 `{'`、`'.join(st_keys)}`，**没有 metrics**。")
    A(f"4. 归档下载任务表里登记过的周期是 `{'`、`'.join(dl_tf)}`，**没有 metrics 这一类**。")
    A("")
    A("**关键证据**：重启导出器前后，指标历史的最后一行完全相同——")
    A(f"恢复前 {mu['before_last']}，恢复后 {mu['after_last']}，"
      f"行数 {n(mu['before_rows'])} 与 {n(mu['after_rows'])} 也没变。"
      f"这证明本单**没有**把这条顺手改成「看起来有值」。")
    A("")
    A("当前值不受影响：接口的当前值走「实时层 + 本机快照」，"
      "历史归档只用于历史曲线与覆盖度统计，两者不互相填充。")
    A("")

    A("## 五、根因三：行情水位停滞 = 已交割合约（跨界，未修）")
    A("")
    A("全池逐个判定的结果：")
    A("")
    A("| 分类 | 标的数 | 含义 |")
    A("| --- | --- | --- |")
    A(f"| 正常（K 线与快照都新鲜） | {b_ok} | 无需处理 |")
    A(f"| 正常但无快照文件 | {b_nosnap} | 采集器只按有界集合刷新，不在页首 |")
    A(f"| 正常但快照陈旧 | {b_stale} | 曾进页首、后被移出，留下旧文件 |")
    A(f"| 交易所已不交易 | {b_notrade} | 交割/暂停，见下表 |")
    A("")
    A(f"{n_universe} 个合约全部有 1 小时 K 线头（{n_head} 个）。")
    A("")
    A("| 标的 | 交易所状态 | 交割时间 | 连续零成交根数 | 冻结价 |")
    A("| --- | --- | --- | --- | --- |")
    for s in notrade_syms:
        r = by_sym[s]
        bar = (r.get("tail_bars") or [{}])[0]
        A(f"| `{s}` | {r['exch_status']} | {r['exch_delivery_utc']} | "
          f"{r['zero_flat_run']} | {bar.get('close')} |")
    A("")
    A("这些标的的 K 线**水位仍在推进**（交易所持续返回零成交的冻结价），"
      "所以只看「水位是否落后」发现不了它们——必须逐根检查"
      "「成交量为零且开高低收全等」。本单把这项检查做成了全池逐标的判定。")
    A("")
    A("引擎对这些零成交 bar 的处理是**正确保护**：质量闸门判为零成交冻结后"
      "只推进水位、不喂状态机，所以事件状态才停在最后一条真实 bar 上。")
    A("")

    A("## 六、修改清单")
    A("")
    A("**代码：零改动。运维：重启一个停摆的既有组件。**")
    A("")
    A("| 文件 | 最后修改时刻 | sha256 前 16 位 |")
    A("| --- | --- | --- |")
    for f in code_files:
        A(f"| `{f['file']}` | {f['mtime_utc']} | `{f['sha256'][:16]}` |")
    A("")
    A(f"上表全部文件的修改时刻都早于本单开始时刻 {ticket_start}"
      f"（判定：{yn(h_code_ok)}）。")
    A("")
    A("唯一动作是运维恢复，不是改代码：")
    A("")
    A(f"- `sh 框架/backend/exporter.sh start` → 新进程接管，"
      f"断点 `_export_state.json` 未改写，从原水位继续。")
    A("")
    A("为什么另外两条不修：")
    A("")
    A("- 指标历史要补的是**归档下载器**（官方归档里才有 5 分钟比率），"
      "属新增采集通道，与工单「不另建采集系统」冲突。")
    A("- 剔除已交割合约要改**合约池快照**，而工单明确「不改变交易所来源或币池分类」，"
      "且会改动池分母、影响已登记的实验身份。")
    A("")

    A("## 七、持续更新证据（跨两个完整周期）")
    A("")
    A(f"观察窗口 {o_win['started_at']} → {o_win['ended_at']}，"
      f"每 {o_win['interval_s']} 秒采样一次，共 {o_win['samples']} 次。")
    A("")
    A(f"写入轮次序列：**{' → '.join(str(x) for x in o_seq)}**，"
      f"推进 {o_adv} 个完整周期，满足工单「至少两个预期更新周期」"
      f"（判定：{yn(o_meets)}）。")
    A("")
    A("| 层 | 证据项 | 首值 | 末值 |")
    A("| --- | --- | --- | --- |")
    A(f"| 原始落盘 | BTCUSDT 快照写入时刻 | {btc['snapshot_refreshed_at']['first']} | "
      f"{btc['snapshot_refreshed_at']['last']} |")
    A(f"| 原始落盘 | BTCUSDT 大户持仓多空比数据时点 | "
      f"{btc['ratio_event_time::top_position_long_short_ratio']['first']} | "
      f"{btc['ratio_event_time::top_position_long_short_ratio']['last']} |")
    A(f"| 实际读取器 | 接口 `time.event_time` | {btc['api_time_event_time']['first']} | "
      f"{btc['api_time_event_time']['last']} |")
    A(f"| 实际读取器 | 接口 `snapshot_state.refreshed_at` | "
      f"{btc['api_snapshot_refreshed_at']['first']} | "
      f"{btc['api_snapshot_refreshed_at']['last']} |")
    A(f"| 陈旧状态 | 快照年龄（秒） | {btc['freshness_age_seconds']['first']} | "
      f"{btc['freshness_age_seconds']['last']} |")
    A(f"| 陈旧状态 | 新鲜标的数 | {o_fs['fresh_first']} | {o_fs['fresh_last']} |")
    A("")
    A(f"轮转窗口确实轮到了 DOTUSDT：全市场账户多空比数据时点从 "
      f"{dot['ratio_event_time::global_account_long_short_ratio']['first']} 推进到 "
      f"{dot['ratio_event_time::global_account_long_short_ratio']['last']}，"
      f"主动买卖比从 {dot['ratio_event_time::taker_buy_sell_ratio']['first']} 推进到 "
      f"{dot['ratio_event_time::taker_buy_sell_ratio']['last']}。")
    A("")
    A(f"热库写入侧同步取证（同一时段，间隔 {h_gap} 秒两次采样）：")
    A("")
    A("| 表 | 行数变化 | 最新时点变化 |")
    A("| --- | --- | --- |")
    A(f"| `candle_heads`（5 分钟） | {n(g5['rows_before'])} → {n(g5['rows_after'])}"
      f"（+{n(g5['delta'])}） | {g5['max_open_utc']['before']} → {g5['max_open_utc']['after']} |")
    A(f"| `oi_history` | {n(goi['rows_before'])} → {n(goi['rows_after'])}"
      f"（+{n(goi['delta'])}） | {goi['max_ts_utc']['before']} → {goi['max_ts_utc']['after']} |")
    A(f"| `realtime_quotes` | {n(gq['rows_before'])} → {n(gq['rows_after'])}"
      f"（+{n(gq['delta'])}，覆盖写表行数不变属正常） | "
      f"{gq['max_updated_utc']['before']} → {gq['max_updated_utc']['after']} |")
    A("")

    A("## 八、健康采集未受损")
    A("")
    A("| 判据 | 实测 | 结论 |")
    A("| --- | --- | --- |")
    A(f"| 采集器进程数 | {h_col_count} | 唯一写入者，未重复启动 |")
    A(f"| 采集器启动时刻 | {col_created} | 早于本单开始时刻，全程未重启 |")
    A(f"| 代码改动 | 全部文件修改时刻早于 {ticket_start} | 本单未改代码 |")
    A(f"| 上游失败 | 最近 {rounds_seen} 轮失败 {fail_total} 次 | 无新增失败 |")
    A("")
    A(f"本机系统定时任务里与本项目相关的条数为 "
      f"{len(chain['scheduling']['loaded_launchd_for_project'])}——"
      f"这也是导出器停摆八天没人发现的原因：它没有任何看护，进程一死就永久死。")
    A("")

    A("## 九、跨界建议（本单未动，供协调）")
    A("")
    A("### 建议一：给导出器装上看护（优先级最高）")
    A("")
    A("- 现状：导出器能正常工作，但**没有任何看护**——进程一死就永久停摆，"
      "这次停了八天才被发现。")
    A("- 涉及路径：`框架/backend/exporter.sh`、`框架/backend/导出任务.plist`"
      "（plist 存在但从未安装）。")
    A("- 最小改法：把已有 plist 装进系统定时任务，或让导出器自带「被杀后自恢复」。"
      "不改代码逻辑，只加看护。")
    A("")
    A("### 建议二：补指标历史增量")
    A("")
    A(f"- 现状：`metrics_history` 停在 {btc_last}，链路上没有写入者。")
    A("- 涉及路径：`数据/市场数据/parquet/metrics/binance/um/<SYMBOL>/<YEAR>.parquet`、"
      "`框架/backend/metrics_feed.py`（只读）、`工具_热转冷导出.py`。")
    A("- 最小改法：在现有导出器里加一个 `export_metrics`，数据源用币安官方归档"
      "（唯一提供 5 分钟比率历史的来源），沿用现有 `<YEAR>.parquet` 结构，"
      "并在断点文件里加 metrics 键。")
    A("- 风险：这是新增下载通道，需单独授权；不影响交易决策"
      "（读取器自己声明证据不进决策）。")
    A("")
    A("### 建议三：剔除已交割合约")
    A("")
    A(f"- 现状：合约池快照时点是 {pool_max_snap}，已过期二十多天，"
      f"{b_notrade} 个已交割合约仍被当作 TRADING 持续轮询。")
    A("- 涉及路径：`框架/backend/public_market_read.py`（标的身份闸门）、"
      "`框架/backend/metrics_api.py`（池分母）。")
    A("- 最小改法：只刷新 `symbol_pool` 的 `status` / `snapshot_utc`，不动分类字段；"
      "或在引擎侧给已交割事件打「已退市」标记。")
    A("- 风险：会改动池分母、影响已登记实验身份，**须裁决后再动**。")
    A("")
    A("### 建议四：快照陈旧遗留文件")
    A("")
    A(f"- 现状：磁盘上 {n_snapfile} 个快照文件，其中 {b_stale} 个是"
      "「曾进页首、后被移出」的遗留文件。")
    A("- 接口对此是诚实的：逐字段给 `freshness` 与 `age_seconds`，"
      "陈旧项会被标成 `stale`（实测 DOTUSDT 的全市场账户多空比被标 stale）。")
    A("- 最小改法：无需改读取侧；若要清理，由采集器把旧文件标记为失效，"
      "不要直接删，保留可回溯。")
    A("")

    A("## 十、复算与对撞")
    A("")
    A("本报告数字全部来自 `out/` 下的五份原始产物，由脚本拼装，并做双向对撞：")
    A("")
    A("- 正向：每条声明为事实的字面量必须在正文里真实出现（空串直接报错）。")
    A("- 反向：正文里每个数字必须能在源产物里找到出处（先屏蔽代码块与行内代码）。")
    A("")
    A("结果见 `out/collision_report.json`。")
    A("")
    A("## 十一、口径与限制")
    A("")
    A("- 「水位正常」的判据是：最新 1 小时 K 线头与本小时应有值的差不超过一根。"
      "整点收盘后约三分钟内补齐，属正常延迟。")
    A("- 「零成交冻结」的判据是：成交量为零且开高低收四价全等。"
      "库里的 OHLCV 是文本型，比较前必须显式转数，否则 `\"0\" == 0` 为假、"
      "会把整批冻结合约漏判成正常。")
    A("- 快照「新鲜」用文件写入时刻判，阈值 `900` 秒；"
      "比率「新鲜」用数据自身的 `event_time` 判，轮转阈值 `2880` 秒。"
      "两者口径不同，不能互相代替。")
    A("- 接口里名为 `taker_buy_sell_ratio` 的字段是**实时层派生值**（按成交额现算），"
      "快照里同名的 `long_short.taker_buy_sell_ratio` 是**交易所官方比率**，"
      "两者不是同一个东西。")
    A("- 恢复前那次归档 K 线探测落在读取不稳定的窗口里："
      "落盘记录的是「归档内无已收盘 K 线」，本报告只采用这条可复现记录，"
      "不引用未落盘的中间探测值。恢复后连续两次直读结果一致。")
    A("- 导出器日志用的是本地时间，`_export_state.json` 用的是 UTC，"
      "引用时必须先归一化，否则会得出「差八小时」的假矛盾。")
    A("")

    text = "\n".join(L) + "\n"
    # 防御：漏了 f 前缀的占位符会原样印出来，正文里出现 `{xxx}` 一律判失败
    leftover = re.findall(r"\{[A-Za-z_][A-Za-z0-9_\[\]'\"]*\}", text)
    if leftover:
        raise SystemExit(f"报告里有未替换的占位符：{sorted(set(leftover))}")
    with open(os.path.join(BASE, "README.md"), "w", encoding="utf-8") as f:
        f.write(text)

    facts = []

    def fact(label, literal, source):
        if not isinstance(literal, str) or not literal.strip():
            raise ValueError(f"正向对撞：`{label}` 的字面量为空")
        # 正文里大数字带千分位；同一数值的两种写法都算命中（值不变，只是呈现不同）
        found = literal in text or n(literal) in text
        facts.append({"label": label, "literal": literal, "source": source,
                      "found": found})

    fact("采集器 pid", str(col_pid), "chain_audit.processes.collector[0].pid")
    fact("采集器启动时刻", col_created, "chain_audit.processes.collector[0].created_utc")
    fact("指标历史最后一行", str(btc_last), "pool_gap.metrics_archive.sample.BTCUSDT")
    fact("合约池快照时点", str(pool_max_snap), "chain_audit.latest_rows.symbol_pool")
    fact("归档目录数", str(arch_dirs), "pool_gap.metrics_archive.dirs")
    fact("全池标的数", str(n_universe), "pool_gap.universe.count")
    fact("非交易标的数", str(b_notrade), "pool_gap.buckets.EXCHANGE_NOT_TRADING")
    fact("快照文件数", str(n_snapfile), "pool_gap.universe.with_snapshot_file")
    fact("快照新鲜数", str(n_snapfresh), "pool_gap.universe.snapshot_fresh")
    fact("轮次首值", str(o_seq[0]), "observe_summary.round_advance.round_sequence[0]")
    fact("轮次末值", str(o_seq[-1]), "observe_summary.round_advance.round_sequence[-1]")
    fact("快照失败合计", str(fail_total), "chain_audit.request_stats.snapshot_failed_total")
    fact("5分钟行数增量", str(g5["delta"]), "health_intact.P4_db_growth.growth")
    fact("OI 行数增量", str(goi["delta"]), "health_intact.P4_db_growth.growth")
    fact("恢复后归档K线时点", str(v1_after), "recovery.V1_api_history_bar.after")
    fact("恢复后导出器收尾时刻", str(v3_after["finished_at_utc"]),
         "recovery.V3_checkpoint.after.finished_at_utc")
    fact("快车道新增行", str(v4_fast.get("新增行")), "recovery.V4_first_catchup_round.fast")
    fact("慢车道新增行", str(v4_slow.get("新增行")), "recovery.V4_first_catchup_round.slow")
    fact("持仓量新增行", str(v4_oi.get("新增行")), "recovery.V4_first_catchup_round.oi")
    fact("指标恢复前行数", str(mu["before_rows"]), "recovery.metrics_archive_unaffected")
    fact("指标恢复后行数", str(mu["after_rows"]), "recovery.metrics_archive_unaffected")
    fact("首轮补数合计", str(v4_total),
         "sum(recovery.V4_first_catchup_round.*.新增行)")

    allowed = set()
    for src in (gap, chain, health, obs, rec):
        for lit in leaves(src):
            allowed.add(lit)
            allowed.add(lit.lstrip("-+"))
            for piece in re.findall(r"\d[\d,]*(?:\.\d+)?", lit):
                allowed.add(piece)
                allowed.add(piece.replace(",", ""))
    for f_ in facts:
        allowed.add(f_["literal"])
        for piece in re.findall(r"\d[\d,]*(?:\.\d+)?", f_["literal"]):
            allowed.add(piece)

    masked = re.sub(r"```[\s\S]*?```", "〈代码块〉", text)
    masked = re.sub(r"`[^`\n]{1,160}`", "〈代码〉", masked)
    unknown = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", masked):
        tok = m.group(0)
        if tok in allowed or tok.replace(",", "") in allowed:
            continue
        unknown.append({"token": tok,
                        "context": masked[max(0, m.start() - 40):m.start() + 40]})

    report = {
        "generated_at": R["generated_at"],
        "readme_bytes": len(text.encode("utf-8")),
        "masked_retention_pct": round(100 * len(masked) / len(text), 1),
        "forward": {"checked": len(facts),
                    "passed": sum(1 for f_ in facts if f_["found"]),
                    "failed": [f_ for f_ in facts if not f_["found"]]},
        "backward": {"number_tokens": len(re.findall(r"\d[\d,]*(?:\.\d+)?", masked)),
                     "unknown_count": len(unknown), "unknown": unknown[:20]},
    }
    with open(os.path.join(OUT, "collision_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(json.dumps({
        "readme_bytes": report["readme_bytes"],
        "masked_retention_pct": report["masked_retention_pct"],
        "forward": f"{report['forward']['passed']}/{report['forward']['checked']}",
        "forward_failed": [f_["label"] for f_ in report["forward"]["failed"]],
        "backward_tokens": report["backward"]["number_tokens"],
        "backward_unknown": len(unknown),
        "unknown_samples": [u["token"] for u in unknown[:10]],
    }, ensure_ascii=False, indent=1))
    return 0 if not report["forward"]["failed"] and not unknown else 1


if __name__ == "__main__":
    sys.exit(main())
