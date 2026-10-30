# 真实事件源清单

| 事件 | 实际产生/保存来源 | 时间依据 | 价格/坐标依据 | 当前证据与缺口 |
|---|---|---|---|---|
| 箱体识别 | 回测运行目录 `events.jsonl` 中 dossier `box` + `history` 的 `BOX` 状态转换 | `formation_time_ms`；确认 `trigger_bar_close_ms`；可见时间 `available_at_ms`；结束 `ended_at_ms` | `box.high_h` / `box.low_l` | 当前所选 run 30 个。缺 formation、有效时间、可得时间或上下沿的记录不绘制。箱体生效区间从确认时间开始，避免确认前预画。|
| 扫低 | 同一 `events.jsonl` 状态机 dossier：`sweep_time_ms`、`sweep_low_s`，`history` 的 SWEEP 可得时间 | `sweep_time_ms`，另保留 `available_at_ms` | `sweep_low_s` | 当前所选 run 4 个。映射来自持久化事件字段，不从蜡烛推断。|
| 扫高 | 本策略状态机为 long-only，当前源无 sweep-high 事件 | 不存在 | 不存在 | API 明确 `events_unavailable`；UI 显示不可用。|
| 回踩 | dossier `retest_time_ms`、`retest_low_lr`；history RETEST | 原始回踩时间及 `available_at_ms` | `retest_low_lr` | 当前所选 run 2 个。|
| 突破确认 | dossier `breakout_time_ms`、`breakout_close_price`；history CANDIDATE | 突破时间及 CANDIDATE `available_at_ms` | `breakout_close_price` | 当前所选 run 未发现可显示突破记录；不能拿候选创建时间替代。|
| 信号触发 | 回测 `candidates.jsonl` 候选快照；市场源为当前真实 active signal 记录 | 回测 `snapshot_time_ms` 且保留 `available_at_ms`；市场取源记录 `eventTime`，不用 created_at | 回测 `breakout_close`；市场若原信号无 trigger price 则不可用 | 当前所选回测 run 无候选；市场 signals API 返回 500，无法浏览器核验。|
| 信号失效 | 回测 `events.jsonl` 的 INVALIDATED 状态转换 | `trigger_bar_close_ms`；保留 `available_at_ms`；reason 原字段 | 原记录未必有失效价；不补价 | 当前所选 run 15 个。市场生命周期源未给独立发生时间/原因，市场失效事件不可用。|
| 实际入场 | 回测持久化成交 `trades.jsonl` / 链接成功的 LedgerFacts；市场只读 `paper_realized.entry_ts` 和账户/持仓 | 实际成交 `entry_time_ms` / `entry_ts` | 实际成交 `entry_price` | 回测 run 0 成交。市场只读成交端点一次返回 200：4 行中 1 行有效、3 行作废；账户摘要和行情页端点异常，因此无法在市场 K 线显示。|
| 实际平仓 | 回测持久化成交 `trades.jsonl` / 链接成功的 LedgerFacts；市场只读 `paper_realized.exit_ts` | 实际成交 `exit_time_ms` / `exit_ts` | 实际成交 `exit_price`；退出原因 `exit_reason` | 回测 run 0 成交。市场同上；voided 行被排除，计划委托/未成交行不进入事件。|

所有图表映射只读；事件发生时间和首次可得时间分离；不会从 K 线形态推算缺失的状态机事件。
