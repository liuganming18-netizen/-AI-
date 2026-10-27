# 当前统一版本清单（FRAME-CLOSE-01）

- 采集时点：2026-10-19 09:44:16 CST
- Git HEAD：`2a13325f189e13de7ed06a836ba3d9db4ee63ce3`（分支 `wyckoff-v3.1-main`，dirty=True）
- 工作区：已跟踪已修改 214 个，未跟踪 172 个
- 本清单收录：145 个文件（关键 8 个 + 代码面 137 个）

> 说明：Git HEAD 无法代表本工程真实状态（大量文件未纳入版本控制），
> 故本清单以**文件内容 SHA-256** 为准，Git 状态仅作辅助标注。

## 一、8 个关键文件

| 文件 | 大小 | 修改时间 | SHA-256（前 16 位） | Git 状态 |
| :--- | ---: | :--- | :--- | :--- |
| `evidence/FLOW-OI-01/generate_flow_oi_01.py` | 44495 | 2026-10-19 02:15:36 | `cf0de2f4adbd512c` | tracked-clean |
| `evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py` | 8878 | 2026-10-19 01:59:03 | `78d9a280d718ebb4` | tracked-clean |
| `框架/backend/backtest_api.py` | 61513 | 2026-10-19 02:56:18 | `4c6a536dcc89d1ac` | M |
| `框架/backend/backtest_runner.py` | 76699 | 2026-10-19 02:45:44 | `27ada5b927a6f09e` | M |
| `框架/backend/experiment_registry.py` | 64875 | 2026-10-19 02:45:15 | `89d10a8a1320bea2` | ?? |
| `框架/backend/tests/test_frame_fit_02.py` | 26139 | 2026-10-19 03:03:56 | `5f9b5990171e5054` | ?? |
| `策略/WyckoffMaster/flow_oi/pit_features.py` | 31829 | 2026-10-19 02:00:16 | `96c257fad0bb146a` | tracked-clean |
| `策略/WyckoffMaster/trading_core/pit_guard.py` | 21253 | 2026-10-18 21:54:53 | `79f60d11adc1aa63` | ?? |

## 二、代码面全量清单

完整逐文件哈希见 `versions.csv`。以下按目录汇总。

| 目录 | 文件数 |
| :--- | ---: |
| `evidence/FLOW-OI-01/` | 2 |
| `evidence/FRAME-PIT-02/` | 2 |
| `框架/backend/` | 86 |
| `策略/WyckoffMaster/` | 47 |
