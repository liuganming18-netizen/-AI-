# ENTRY-AUDIT-03 · 冻结基线清单

- 冻结时刻（UTC）：2026-10-18 08:58:44
- 文件总数：4139
- 体量：6708.4 MB

## 分类计数

| 类别 | 数量 |
| :--- | ---: |
| 上游研究产物 | 17 |
| 策略代码 | 18 |
| 合约规格证据 | 16 |
| 15m K 线 | 848 |
| 1h K 线 | 826 |
| 4h K 线 | 826 |
| 持仓量 OI | 794 |
| 资金费 | 152 |
| 指标 metrics | 642 |

覆盖 152 个标的（= 冻结研究范围内出现的全部标的）。

## 合约规格证据（决定「规格资格」）

- exchangeInfo 快照：2 份 → exchangeInfo_futures_2026-10-05T015512Z.json, exchangeInfo_futures_2026-10-05T045328Z.json
- 规格变更证据 `spec_changes_*.json`：**0 份**

> 零份变更证据 ⇒ 没有任何窗口能证明「规格在该窗口内未变更」⇒ 全池不可能出现 `verified`。

## 关键代码版本

| 文件 | sha256 |
| :--- | :--- |
| `策略/WyckoffMaster/event_trend_v0_1/config.py` | `29a42d98f7b7c5302b7adc0cef20b4ea…` |
| `策略/WyckoffMaster/event_trend_v0_1/contracts.py` | `6895e0949f9b683cd3280e299dfc40f9…` |
| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | `21c875a849331ce04c57c85efcacbad9…` |
| `策略/WyckoffMaster/event_trend_v0_1/features.py` | `4f49073c91ef68398ea8617350880265…` |
| `策略/WyckoffMaster/event_trend_v0_1/runner.py` | `d9e881589b6fdcd65bb326191dcb56db…` |
| `策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py` | `fc956b5502c5ff977402d34a0678955a…` |
| `策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py` | `97b9842de9a2e7c843416aa919841e81…` |
| `策略/WyckoffMaster/trading_core/__init__.py` | `ebe97733854b93248d36e8c58e8e4ff2…` |
| `策略/WyckoffMaster/trading_core/engine.py` | `8cf36bc5e09f6e1c68f30e96a0f42b39…` |
| `策略/WyckoffMaster/trading_core/policy.py` | `6303d06ae6fea38be8c0a3fe5304742a…` |
| `策略/WyckoffMaster/trading_core/fill.py` | `f050652c0d83b41a8a60f4d088c7864d…` |
| `策略/WyckoffMaster/trading_core/risk.py` | `ad8e14d56e8e6cbf4b8e664bdface179…` |
| `策略/WyckoffMaster/trading_core/rejects.py` | `a8cae2c214a06cf7f723892fc6e178ac…` |
| `策略/WyckoffMaster/trading_core/ledger.py` | `c813e5842e8d3d6b1f6f00419099a46f…` |
| `策略/WyckoffMaster/trading_core/position.py` | `5df732b2eb0bb5d1aba25388f704dc57…` |
| `策略/WyckoffMaster/trading_core/funding.py` | `436ebba29be84993eba02150595c121b…` |
| `策略/WyckoffMaster/trading_core/signal.py` | `572b226808d48c335256a5507182a0f8…` |
| `策略/WyckoffMaster/trading_core/pit.py` | `952c31aa847aac4c073d93dfd96ac430…` |

## 配置指纹

- `EventTrendConfig` 序列化 sha256：`0faa5fc046f1841d3900508ddcfbbd6717dff8b0196a3dc17ae406efb455d7b0`
- 引擎契约版本：`trading_engine_v1.0.0`

完整参数见 `00_config_fingerprint.json`（含预热/持仓/资金初始化/符号约定的逐条事实声明）。

## 两个代际（禁止拼接）

- 连续回放 `cand_ev_*`：185 行（成交 163 + 未成交 22），133 个标的，窗口 2021-12-01 ~ 2026-10-18
- 窗口回放 `old_cand_*`：42 行，35 个标的，35 个不同组、43 个 (组,标的) 组合，每组 365 天

> 完整 64 位哈希见 `00_freeze_manifest.json`。
