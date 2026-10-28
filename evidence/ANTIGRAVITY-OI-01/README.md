# ANTIGRAVITY-OI-01｜持仓量时效与阻断核验独立复核报告

- **日期**：2026-10-28（北京时间）
- **工单**：`docs/派工_ANTIGRAVITY-OI-01_持仓量时效与阻断核验_2026-10-28.md`
- **执行角色**：反重力（独立只读复核方）
- **专属证据目录**：`evidence/ANTIGRAVITY-OI-01/`
- **纪律遵循**：100% 只读与隔离复算，零代码修改，零配置修改，不改生产数据，不重启服务。
- **状态结论**：**全项独立复算完成，事实清晰，归因明确，提请主代理独立验收**。

---

## 一、 核心核查结论总览

| 检查项 | 事实依据与复算结论 | 最终判定 |
| :--- | :--- | :---: |
| **1. 真实时间链穿透** | 热库 `oi_history` 累计 747 万行，真实采集器持续运行并记录 `fetched_at`。真实样本中，`snapshot` 延迟仅数秒至数分钟，而 `5m` 历史端点存在客观晚发。 | **证实** |
| **2. 52 分钟延迟复算** | 采用近 24 小时（2026-10-26T17:50Z 至 2026-10-27T17:50Z）、522 标的、149,782 条 `5m` 记录进行全量逐行统计：<br>• **P50 中位数延迟 = 51.83 分钟**（与“52分钟”完全一致）；<br>• **> 15 分钟占比 = 99.30%**（即 99.3% 不满足 15 分钟要求）；<br>• 算法：`delay = (fetched_at - ts) / 1000 / 60`。 | **证实** |
| **3. 门槛与调用链** | 策略 3a 将 OI 作为前置硬门槛（`enable_oi_filter = True`）。读取器 `oi_feed.py:91` 默认固定传入 `period='5m'`，导致 PIT 判定因数据未到而阻断，状态机报 `OI_UNAVAILABLE`，自然候选归零。 | **证实** |
| **4. 保护机制** | `paper_engine.py:1226` 严格锁定未经用户正式批准的动态仓位乘数（强制为 `1.0`），未改变基准风险。 | **证实** |
| **5. 归因结论** | **双重归因**：① 币安官方 `openInterestHist` 历史端点天然延迟 ~52 分钟发布；② 代码层未读取本地已采集的高时效 `snapshot`（中位仅 3.99 分钟，100% $\le 15$ 分钟）。 | **明确** |

---

## 二、 独立复算数据详情（近 24 小时全量样本）

来源文件：[`out/oi_latency_recalc.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/out/oi_latency_recalc.json)

```json
{
  "sample_period_utc": {
    "from_utc": "2026-10-26T17:50:00+00:00",
    "to_utc": "2026-10-27T17:50:00+00:00"
  },
  "oi_5m_openInterestHist": {
    "total_rows": 149782,
    "unique_symbols": 522,
    "min_min": 5.01,
    "p25_min": 47.04,
    "p50_median_min": 51.83,
    "p75_min": 56.64,
    "p90_min": 59.56,
    "p95_min": 60.56,
    "p99_min": 61.89,
    "max_min": 62.97,
    "within_15min_ratio": 0.0070,
    "exceed_15min_percentage_str": "99.30%"
  },
  "oi_snapshot_openInterestHist": {
    "total_rows": 40498,
    "unique_symbols": 522,
    "min_min": 0.01,
    "p50_median_min": 3.99,
    "p90_min": 6.22,
    "max_min": 7.97,
    "within_15min_ratio": 1.0
  }
}
```

---

## 三、 真实样本与对照

来源文件：[`out/oi_recent_samples.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/out/oi_recent_samples.csv)

| 币种 | 类型 (Period) | 事件桶时间 (ts UTC) | 抓取落盘时间 (fetched_at UTC) | 真实到达延迟 | PIT 判定 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDT** | `5m` | 2026-10-27 16:55:00 | 2026-10-27 17:47:04 | **52.07 分钟** | 超时未到 (拒用) |
| **BTCUSDT** | `snapshot` | 2026-10-27 17:45:00 | 2026-10-27 17:48:58 | **3.97 分钟** | 实时可用 |
| **ETHUSDT** | `5m` | 2026-10-27 16:55:00 | 2026-10-27 17:47:06 | **52.10 分钟** | 超时未到 (拒用) |
| **ETHUSDT** | `snapshot` | 2026-10-27 17:45:00 | 2026-10-27 17:49:01 | **4.02 分钟** | 实时可用 |

---

## 四、 给主代理与 Work 的最小建议

1. **不建议在生产直接粗暴关闭 OI 门槛**：主策略 3a 获批定义包含 OI 确认，关闭属于策略变更；
2. **给 Work 的最小代码改进**：在 `oi_feed.py` 中，读取逻辑可增加对本地已存 `snapshot` 的优雅兼容（例如在当前时间近端优先读取 `snapshot`，回测历史读取 `5m`），即可用已有数据彻底解决 15 分钟时效约束，无需等待币安 52 分钟历史桶。

---

## 五、 交付产物清单

| 产物路径 | 类型 | 说明 |
| :--- | :---: | :--- |
| [`README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/README.md) | 报告 | 本终审复核总览报告 |
| [`out/oi_latency_recalc.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/out/oi_latency_recalc.json) | 数据 | 24小时全量 14.9 万条逐行计算分位数与比例统计 |
| [`out/oi_recent_samples.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/out/oi_recent_samples.csv) | 数据 | 主流币逐笔事件时间与落盘时间对比样本明细 |
| [`out/callchain_and_gates.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/out/callchain_and_gates.md) | 专报 | 调用链 Mermaid 流程图与硬门槛/仓位保护分析 |
| [`scripts/verify_oi_latency.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ANTIGRAVITY-OI-01/scripts/verify_oi_latency.py) | 脚本 | 可独立一键复跑的统计计算脚本 |
