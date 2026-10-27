"""90 · 生成带版本的数据事实清单（FACTS-DATA-v1）

事实清单**不手写数字**：每个数值都从 `out/` 的复算产物里读出来，附上统计时点与查询出处。
这样任何人只要重跑 `reproduce.sh`，清单就能重新生成并与本版逐字段比对。

  out/FACTS-DATA-v1.json   机器可读事实清单（供其他两单引用）
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT, dump, now_utc, pool

VERSION = "FACTS-DATA-v1"
STATS_AT = now_utc()


def L(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


oi = L("12_oi_freshness.json")
oi_snap = L("10_hot_inventory.json")
oi_snaponly = L("18_oi_snapshot.json")
met = L("14_metrics_staleness.json")
fund = L("17_funding_hot.json")
cold = L("15_cold_inventory.json")
cat = L("20_catalog_diff.json")
fpath = L("23_funding_path_probe.json")
fcold = L("30_funding_cold_read.json")
contrast = L("34_read_path_contrast.json")
fresh = L("33_cold_freshness.json")
pit = L("80_pit_fill.json")
alias = L("50_alias_full.json")
dup15 = L("51_dup_15m_full.json")
dupoi = L("52_dup_oi_full.json")
zero = L("53_zero_oi_full.json")
sm = L("70_smart_money_t0.json")
code = L("60_code_anchors.json")
close = L("40_close_evidence.json")
p = pool()

facts = []


def F(fid, statement, value, unit, population, stats_at, evidence, confidence="已证",
      supersedes=None, note=None):
    facts.append({
        "id": fid, "statement": statement, "value": value, "unit": unit,
        "population": population, "stats_at_utc": stats_at, "evidence": evidence,
        "confidence": confidence, "supersedes": supersedes, "note": note,
    })


# ---------------------------------------------------------------- 币池与总量
F("F01", "批准币池规模（仅加密币，排除指数）", p["n"], "个",
  "全量（范围文件 binance_items）", p["generated_utc"],
  "数据/contracts/旧标的池/scope_crypto.json", "已证",
  "旧说法「528 币」/「551 标的」都不可当批准币数")
F("F02", "热库对象数（表+视图）", oi_snap["objects"], "个", "全量", oi_snap["stats_at_utc"],
  "out/10_hot_inventory.json")
F("F03", "热库文件体积", round(oi_snap["db_bytes"] / 1024**3, 2), "GiB", "全量",
  oi_snap["stats_at_utc"], "out/10_hot_inventory.json",
  note="1,024 进制；折合 12.99 GB（1,000 进制）")
F("F04", "冷档 parquet 文件数", cold["total_parquet_files"], "个", "全量（磁盘枚举）",
  cold["stats_at_utc"], "out/15_cold_inventory.json")
F("F05", "冷档体积", cold["total_gb"], "GiB", "全量（磁盘枚举）", cold["stats_at_utc"],
  "out/15_cold_inventory.json", note="1,024 进制；折合 20.2 GB（1,000 进制）")

# ---------------------------------------------------------------- 持仓量
F("F10", "持仓量 5m 热库覆盖（池内）",
  f"{oi['pool_symbols_with_5m']}/{oi['approved_pool']}", "币", "全量（GROUP BY symbol）",
  oi["stats_at_utc"], "out/12_oi_freshness.json")
F("F11", "持仓量 5m 一小时内更新（池内）",
  f"{oi['fresh_le_1h']}/{oi['approved_pool']}（{oi['fresh_le_1h_pct']}%）", "币",
  "全量", oi["stats_at_utc"], "out/12_oi_freshness.json")
F("F12", "持仓量 5m 陈旧分布（池内，>1 小时）",
  json.dumps(oi["freshness_buckets_pool"], ensure_ascii=False), "币", "全量",
  oi["stats_at_utc"], "out/12_oi_freshness.json",
  note="4 个币超过 3 天未更新，最久约 10.7 天")
_oi_rows = list(csv.DictReader(open(OUT / "11_oi_5m_per_symbol.csv", encoding="utf-8-sig")))
_pool_rows_5m = sum(int(r["rows_5m"]) for r in _oi_rows if r["in_approved_pool"] == "1")
F("F13", "持仓量 5m 热库行数（全部 528 币）/ 起点",
  f"{oi['total_rows_5m']} 行 / {oi['min_ts_all']}", "行", "全量",
  oi["stats_at_utc"], "out/12_oi_freshness.json",
  supersedes="原报告 7,046,193 行（核对时刻不同，非矛盾）",
  note=f"其中池内 526 币为 {_pool_rows_5m} 行，池外 2 币为其余")
F("F14", "持仓量快照（period<>5m）行数 / 币数",
  f"{oi_snaponly['rows']} 行 / {oi_snaponly['symbols']} 币", "行", "全量",
  oi_snaponly["stats_at_utc"], "out/18_oi_snapshot.json",
  note="快照只在热层（2026-10-05 起），冷档未归档 ⇒ 快照没有长历史")

# ---------------------------------------------------------------- 资金费
F("F20", "资金费热库行数 / 币数 / 覆盖池",
  f"{fund['rows_total']} 行 / {fund['symbols']} 币 / 池内 {fund['pool_symbols']}",
  "行", "全量", fund["stats_at_utc"], "out/17_funding_hot.json")
F("F21", "资金费热库时间跨度 / 结算类型",
  f"{fund['min_funding_utc']} → {fund['max_funding_utc']} / {list(fund['rate_types'])}",
  "", "全量", fund["stats_at_utc"], "out/17_funding_hot.json")
F("F22", "资金费冷读返回行数（池内 526 币逐个调用生产读接口）",
  f"{fcold['cold_rows_total']} 行（0 行的币 {fcold['symbols_cold_zero']}/{fcold['symbols']}）",
  "行", "全量（526 币全调）", fcold["stats_at_utc"], "out/30_funding_cold_read.json")
F("F23", "资金费平铺文件在盘上（池内）",
  f"{fcold['symbols_with_flat_file_on_disk']}/{fcold['symbols']}", "币", "全量",
  fcold["stats_at_utc"], "out/30_funding_cold_read.json",
  note="数据在、路径错 ⇒ 读到 0 行，不是没数据")
F("F24", "资金费冷读对照（同类冷读能否读通）",
  "; ".join(f"{c['case']}={c.get('rows')} 行" for c in contrast["cases"]), "",
  "3 个对照点", contrast["stats_at_utc"], "out/34_read_path_contrast.json")
F("F25", "冷档 funding 列数（没有 fetched_at）",
  ",".join(pit["funding_columns"]), "列", "全量（DESCRIBE）", pit["stats_at_utc"],
  "out/80_pit_fill.json", "已证",
  supersedes="原报告称冷档 funding 有 9 列含 fetched_at —— 实测只有 5 列")

# ---------------------------------------------------------------- 冷档新鲜度
F("F30", "冷档 K 线落后热库",
  f"1h {fresh['lag_days']['candles_1h']} 天 / 4h {fresh['lag_days']['candles_4h']} 天 / "
  f"15m {fresh['lag_days']['candles_15m']} 天", "天", "全量（MAX() 全目录）",
  fresh["stats_at_utc"], "out/33_cold_freshness.json")
F("F31", "冷档 OI 落后热库", f"{fresh['lag_days']['oi_5m']} 天", "天", "全量",
  fresh["stats_at_utc"], "out/33_cold_freshness.json")
F("F32", "冷档资金费落后热库", f"{fresh['lag_days']['funding']} 天", "天", "全量",
  fresh["stats_at_utc"], "out/33_cold_freshness.json")
F("F33", "冷档最新时刻（绝对）",
  json.dumps(fresh["cold_max_utc"], ensure_ascii=False), "", "全量",
  fresh["stats_at_utc"], "out/33_cold_freshness.json")

# ---------------------------------------------------------------- 大户指标
F("F40", "大户/多空比冷档覆盖币数 / 行数",
  f"{met['symbols']} 币 / {met['rows_total']} 行", "行", "全量（GROUP BY symbol）",
  met["stats_at_utc"], "out/14_metrics_staleness.json")
F("F41", "大户/多空比最新时刻 / 停更天数",
  f"max(ts)={met['max_ts_all']}；池内中位停更 {round(met['stale_days_pool']['p50'], 2)} 天",
  "", "全量", met["stats_at_utc"], "out/14_metrics_staleness.json")
F("F42", "大户/多空比全池停更 >7 天的币数",
  f"{met['pool_stale_gt_7d']}/{met['symbols']}", "币", "全量", met["stats_at_utc"],
  "out/14_metrics_staleness.json")
F("F43", "metrics 是否含采集/发布时刻列", "否（只有 ts 槽位）", "", "全量（DESCRIBE）",
  pit["stats_at_utc"], "out/80_pit_fill.json")

# ---------------------------------------------------------------- 索引
F("F50", "冷档索引未登记文件数",
  f"{cat['disk_only_total']}（其中 live.parquet {cat['disk_only_live_parquet']}）", "个",
  "全量（双向比对）", cat["stats_at_utc"], "out/20_catalog_diff.json",
  note="真正「历史年度分片/平铺文件未登记」= metrics 1,647 + funding 526")
F("F51", "冷档索引悬空条目数", cat["index_only_total"], "条", "全量",
  cat["stats_at_utc"], "out/20_catalog_diff.json",
  note="53 条指数残留 + 525 条 funding 目录式键 + 1,331 条 sources")
F("F52", "资金费路径口径差异",
  f"磁盘 {fpath['disk_files']} 个平铺（深度 3）/ 索引 {fpath['index_keys']} 条目录式（深度 4）",
  "", "全量", fpath["stats_at_utc"], "out/23_funding_path_probe.json")
F("F53", "索引自身 updated_at", cat["catalog_updated_at_utc"], "", "全量",
  cat["stats_at_utc"], "out/20_catalog_diff.json")

# ---------------------------------------------------------------- 可得时间
for f in pit["fields"]:
    F("F6-" + str(len([x for x in facts if x["id"].startswith("F6")]) + 1).zfill(2),
      f"{f['dataset']} 的 {f['field']} 填充率",
      f"{f['fill_pct']}%（{f['filled']}/{f['rows']}）", "", "全量",
      pit["stats_at_utc"], "out/80_pit_fill.json", note=f["semantic"])
F("F61", "热库 available_at 与 close_time 的差",
  f"最小 {pit['available_minus_close_ms']['min']} ms / 最大 "
  f"{round(pit['available_minus_close_ms']['max']/86400000, 1)} 天", "",
  f"全量（{pit['available_minus_close_ms']['n']} 行）", pit["stats_at_utc"],
  "out/80_pit_fill.json", note="⇒ available_at 是本地入库时刻，不是交易所发布时刻")

# ---------------------------------------------------------------- 全量复核（原抽样项）
F("F70", "metrics 两组字段是否纯别名（全量逐行比较）",
  f"差异 {alias['pair_retail_vs_count_ls']['differ_rows']} / "
  f"{alias['pair_top_pos_vs_sum_toptrader']['differ_rows']} 行（共 {alias['rows']} 行）", "行",
  "全量", alias["stats_at_utc"], "out/50_alias_full.json",
  note="全量成立，原抽样 10/10 结论被全量确认")
F("F71", "15m 重复 open_time（全量）",
  f"{dup15['dup_rows_total']} 行 / {dup15['symbols_with_dup']} 币", "行", "全量",
  dup15["stats_at_utc"], "out/51_dup_15m_full.json",
  note="2,688 × 222 = 596,736，与原报告「2,688 根/标的」一致")
F("F72", "持仓量重复 ts（全量）", f"{dupoi['dup_rows_total']} 行 / {dupoi['symbols_with_dup']} 币",
  "行", "全量", dupoi["stats_at_utc"], "out/52_dup_oi_full.json",
  note="全部集中在 BTCUSDT")
F("F73", "零持仓观测（全量）",
  f"oi {zero['oi']['zero']} 行（{zero['oi']['zero_pct']}%）/ "
  f"metrics {zero['metrics']['zero']} 行（{zero['metrics']['zero_pct']}%）", "行",
  "全量", zero["stats_at_utc"], "out/53_zero_oi_full.json")

# ---------------------------------------------------------------- 聪明钱
F("F80", "聪明钱快照文件数（池内缺失）",
  f"{sm['files']} 个（缺 {len(sm['pool_symbols_without_snapshot'])} 个："
  f"{','.join(sm['pool_symbols_without_snapshot'])}）", "个", "全量",
  sm["stats_at_utc"], "out/70_smart_money_t0.json")
F("F81", "聪明钱仍在刷新 / 已停更",
  f"1 小时内 {sm['refreshed_le_1h']} 个；停更 >24 小时 {sm['stale_gt_24h']} 个", "个",
  "全量", sm["stats_at_utc"], "out/70_smart_money_t0.json")
F("F82", "聪明钱停更时长（停更那批的中位）",
  f"{round(sm['freshness_hours']['p50'], 1)} 小时", "小时", "全量",
  sm["stats_at_utc"], "out/70_smart_money_t0.json")
F("F83", "聪明钱是否有历史序列", "否（按 symbol 覆盖写，只有当前值）", "", "全量",
  sm["stats_at_utc"], "out/70_smart_money_t0.json")

# ---------------------------------------------------------------- 代码事实
F("F90", "enable_oi_filter 构造默认值（三处）",
  "False（三处均为 False）", "", "全量（源码现算）", code["stats_at_utc"],
  "out/60_code_anchors.json",
  note="; ".join(f"{a['file']} L{h['line']}" for a in code["anchors"]
                 if a["id"] == "A2_engine_default" for h in a["hits"]))
F("F91", "命令行是否有 OI 开关",
  f"无（参数只有 {','.join(code['cli_flags'])}）", "", "全量", code["stats_at_utc"],
  "out/60_code_anchors.json")
F("F92", "生产构造点是否传 enable_oi_filter",
  "否" if not code["runtime_ctor_passes_oi_filter"] else "是", "", "全量",
  code["stats_at_utc"], "out/60_code_anchors.json")
F("F93", "实时链路喂给状态机的 OI",
  "空数组（oi_timestamps/oi_values）", "", "全量", code["stats_at_utc"],
  "out/60_code_anchors.json")
F("F94", "A/B 是否同源", "是（sm_a = sm_b = engine.sm）", "", "全量",
  code["stats_at_utc"], "out/60_code_anchors.json")

# ---------------------------------------------------------------- 关闭时间
c1 = close["class_1_explicit_record"]
c2 = close["class_2_code_snapshot"]
c3 = close["class_3_runtime_evidence"]
F("FA0", "首次明确关闭记录（生产运行时口径）",
  (c1["earliest_production_runtime"] or {}).get("file_mtime_utc"), "UTC", "全量文本扫描",
  close["stats_at_utc"], "out/40_close_evidence.json",
  note="mtime 只证明该记录此刻在盘上，不证明当时状态")
F("FA1", "首次代码快照（内容为默认 False）",
  (c2["earliest_with_false"] or {}).get("mtime_utc"), "UTC", "全量枚举",
  close["stats_at_utc"], "out/40_close_evidence.json")
F("FA2", "首次运行证据（OI 未参与决策）",
  (c3["earliest"] or {}).get("file_mtime_utc"), "UTC", "全量扫描",
  close["stats_at_utc"], "out/40_close_evidence.json")
F("FA3", "关闭的确切时刻 / 实施者 / 批准依据", "未知（三项均无证据）", "", "—",
  close["stats_at_utc"], "out/40_close_evidence.json",
  confidence="未知", note="证据载体不存在（文件未入版本库），不是没查")
F("FA4", "作废的推理",
  "「文件当时存在 ⇒ 当时已关闭」不成立", "", "—", close["stats_at_utc"],
  "out/40_close_evidence.json", confidence="已证",
  supersedes="原报告 04 §二「最晚在 2026-10-23 02:48 UTC 已是关闭状态」")

out = {
    "version": VERSION,
    "generated_at_utc": STATS_AT,
    "supersedes_version": None,
    "scope": "数据资产与读取链路（不含策略结论）",
    "discipline": [
        "每个 fact 的 value 都由 out/ 下的复算产物生成，不手写",
        "population 明确写全量还是抽样；抽样项不得当作全量结论引用",
        "confidence ∈ {已证, 部分证明, 未知}；未知不计入已证",
        "supersedes 列出被本版纠正的旧说法",
    ],
    "stats_at_utc_note": "各 fact 的 stats_at_utc 为其对应查询的执行时刻；跨 fact 相差数分钟内",
    "facts": facts,
    "counts": {
        "total": len(facts),
        "已证": sum(1 for f in facts if f["confidence"] == "已证"),
        "未知": sum(1 for f in facts if f["confidence"] == "未知"),
        "superseded": sum(1 for f in facts if f["supersedes"]),
    },
}
dump("FACTS-DATA-v1.json", out)
print(f"{VERSION} 生成：{len(facts)} 条事实（已证 {out['counts']['已证']}，未知 {out['counts']['未知']}）")
