# FACTOR-ENTRY-09 与 FE08 数据一致性确证证明

## 一、确证结论
根据工单 `FACTOR-ENTRY-09` 契约要求：**保留 FE08 原件，以其已核对的数据输出为基准，不改公式、阈值、样本、数据输出或计算逻辑，不重跑回测**。
本报告确证：`evidence/FACTOR-ENTRY-09/` 所绑定的 5 项数据产物与 `evidence/FACTOR-ENTRY-08/` 对应文件达到 **逐文件 100% SHA256 哈希一致与字节级完全相同（0 差异）**。

## 二、逐文件哈希对账清单

| 产物文件名 | FE08 原路径 | 文件大小 (字节) | SHA256 校验和 | 状态 |
| :--- | :--- | :---: | :--- | :---: |
| `reconstructed_candidate_features_08.csv` | `evidence/FACTOR-ENTRY-08/reconstructed_candidate_features_08.csv` | 248,107 | `5360aeb189e7c465b9d7ca9e943558d3379ee2236f9245c5b267db14a9da22de` | **MATCH (一致)** |
| `reconstructed_candidate_features_08.parquet` | `evidence/FACTOR-ENTRY-08/reconstructed_candidate_features_08.parquet` | 143,948 | `6e1b9369cca29cba4c8c6064cd11b90a4961f1adcd917aef7dffb7ce5d456849` | **MATCH (一致)** |
| `source_equivalence_table.csv` | `evidence/FACTOR-ENTRY-08/source_equivalence_table.csv` | 2,027 | `71004f7268e97d3285d980323cd5182770ce64faeff9eb3c69872f564dbb75a0` | **MATCH (一致)** |
| `discrepancy_vs_fe07.csv` | `evidence/FACTOR-ENTRY-08/discrepancy_vs_fe07.csv` | 61,166 | `1396a1391fd8071ca18ef7ea4cf90999775f857a16ef2b65201873042c8edc15` | **MATCH (一致)** |
| `discrepancy_summary.json` | `evidence/FACTOR-ENTRY-08/discrepancy_summary.json` | 3,193 | `c5dfb0f418f0c26235fdba1fce322d895b6ec9da65d942047b7f64910836f2f3` | **MATCH (一致)** |

## 三、关键业务指标守恒检验

- **总行数**：`227` 行（完全守恒）
- **已成交分母**：`205` 笔（完全守恒）
- **未成交样本**：`22` 笔（完全守恒）
- **手续费总额 (`fees`)**：`13.125871` USDT（0 漂移）
- **资金费总额 (`funding_fee`)**：`6.977506` USDT（0 漂移）
- **毛利总额 (`gross_pnl`)**：`121.635348` USDT（0 漂移）
- **校正净盈亏 (`net_pnl`)**：`101.531971` USDT（0 漂移）
- **主体系分类标签计数**：
  - `CHOPPY_OR_OTHER`（杂波）：`94` 笔
  - `EARLY_WEAKNESS`（早期走弱）：`67` 笔
  - `SUSTAINED_TREND`（持续主升）：`36` 笔
  - `MFE_PULLBACK`（浮盈回吐）：`8` 笔
  - `UNFILLED`（未成交）：`22` 笔
