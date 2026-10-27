# ENTRY-VOLUME-02｜缩量过滤定向验收

本目录对 ENTRY-VOLUME-01 做了定向验收：从 206 行候选逐笔母表独立重建 201 个事件，复算低量过滤数量、贡献、逐一移除与分期，并检查冻结规则及上游哈希。

## 验收结论

- 206 候选 → 201 事件；按 `(symbol, entry_time_utc)` 聚类，每组保留数值最小的 `group`；5 个被剔除候选全部未命中缩量。
- 206 与 201 两口径均命中同一批 14 笔低量下跌事件。
- 事件级少亏 +27.446471 U、错失盈利影响 −77.977014 U、过滤净差额 −50.530543 U；14 次逐一移除均未转正。
- 分期边界未变，代码生成 JSON 与独立逐笔重算相符。VOLUME-BE-02 报告正文的分期金额与两个时期的去重数有误，ENTRY-VOLUME-01 报告中的额外 `close < entry_price` 条件也不在真实入场因子实现中；详情见 `报告.md`。
- 27 项上游版本清单哈希通过；本轮监测 19 个输入文件前后哈希稳定。
- 只说明当前历史样本可复算，不代表样本外有效。

## 重要文件

- `报告.md`：主报告与文档差异溯源。
- `00_逐项验收表.md`：工单要求逐项验收结果。
- `03_实际命令.md`、`reproduce.sh`：实际命令与复现入口。
- `verify_recalc.py`：从完整逐笔母表独立重算；汇总 JSON 仅用于算后对照。
- `code/02_verify_source_manifests.py`：复核两份既有 SHA-256 清单。
- `code/03_write_hash_manifest.py`：生成本轮交付物与输入指纹。
- `out/candidate_event_mapping.csv`：206 行完整候选—事件映射。
- `out/duplicate_pairs_recomputed.csv`：5 组重复项完整对照。
- `out/low_vol_trade_recalculation.csv`：14 笔贡献明细。
- `out/leave_one_out_recalculation.csv`：14 次逐一移除结果。
- `out/subperiod_recalculation.csv`：候选全体、去重事件、14 笔命中子集三种分期表。
- `out/version_differences.csv`：ENTRY-VOLUME-01、VOLUME-BE-02 正文/JSON、早期 VOLUME-BE-01 逐期差异并列表。
- `out/independent_recalculation.json`：机器可读计算、核验断言、版本差异。
- `out/upstream_manifest_check.json`、`out/source_stability.json`：上游哈希与稳定性证明。
- `out/versions.json`、`out/00_SHA256.txt`：本轮文件哈希表。
