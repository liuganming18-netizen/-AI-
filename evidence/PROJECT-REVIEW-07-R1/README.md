# PROJECT-REVIEW-07-R1｜证据目录说明

- 性质：**只做证据核查与文档更正**。未改代码、未改规则/参数/配置、未重启进程、未删/移任何文件、未触发下单。
- 上一单 `evidence/PROJECT-REVIEW-07/` 的四件交付物**原样保留**，本单只新增更正说明与支撑证据。
- 判定三态：**通过 / 不通过 / 未验证**。未验证不计入通过；交付方自报不算独立复核。

## 文件索引

| 文件 | 回答什么问题 |
|---|---|
| `00_更正说明.md` | 上一单哪 7 条口径/结论需要更正（含本单第一版自己写错的 1 条），逐条给「原文 → 更正 → 依据」；哪些结论仍然成立；5 条新发现 |
| `01_四项重点对照.md` | 用户真正的四项重点，逐项「完成/部分/未完成/阻塞」+ 页面入口 + 运行 ID + 证据 + 缺口 |
| `02_周期口径更正.md` | 默认 1h + 执行 15m 是否算缺陷；请求/返回/标签三者实测 |
| `03_引擎与证据来源.md` | 项目引擎 / Freqtrade / 模拟盘三套引擎分开统计；fill 与 stop_trace 能力不能外推 |
| `04_策略身份与权威依据.md` | 四份「像规范」的文件逐字段对照；22 组 FT-LOW4H run 实际按什么跑；240h 的真实状态 |
| `05_历史产物分层.md` | **第二版**：物理 4,026 个 run 按「支持范围 / 旧实验 / 归档」分层，四类状态分开计数；第一版错数留档在 §7 |
| `06_下一步清单.md` | 按「信号正确性 → 成交因果性 → 追溯 → 图表」排序的 4 批 19 项可执行清单 |

## 机器可复算结果（`out/`）

| 文件 | 内容 | 脚本 |
|---|---|---|
| `01_产物分层.json` | 5,091 个**逻辑路径**的分层与状态计数（含 1,065 个软链接，**物理 run 是 4,026**） | `probes/01_产物分层.py` |
| `01b_未覆盖run目录.json` | 上一单未覆盖的 1,102 个路径（其中 1,065 个是软链接） | 同上 |
| `02_重开与提示.json` | 435 个 run 的实际 HTTP 状态与提示文案样本（含 375 个数组格式全量） | `probes/02_重开与提示.py` |
| `02b_重开实测.json` | 分层重开实测（A/D 缺 chart/归档全量，B/E/重建各抽 150），**其中 1,016 条因后端重启作废** | `probes/02b_重开实测.py` |
| `02c_重开实测.json` | 对上述 1,016 条作废记录的补测结果（并发 2、重试 3 次、先探健康），**分层行为统计以本文件为准** | `probes/02c_补测.py` |
| `03_引擎与成交事件.json` | 按 engine 的 run 数/成交/订单、`/fills` 实测、模拟盘表计数 | `probes/03_引擎与成交事件.py` |
| `04_身份核对.json` | 四份文件哈希、视图自我声明、12 项逐字段对照、240h 执行路径 | `probes/04_身份核对.py` |
| `05_周期口径.json` | 行情 8 周期 × 2 交易所、回测 6 run × 5 请求、前端取值方式 | `probes/05_周期口径.py` |
| `06_止损路径复核.json` | 37,404 笔成交的 stop_trace 分桶、60 笔合规、未验证样例 | `probes/06_止损路径复核.py` |
| `07_按引擎重算.json` | 按目录全量重算成交笔数与 `stop_trace` 字段有无（补 03 漏掉的 4,325 笔归属） | `probes/07_按引擎重算.py` |
| `08_软链接核对.json` | **物理 run 4,026 + 软链接 1,065 = 逻辑 5,091**；断链 0、唯一目标 1,065 | `probes/08_软链接核对.py` |
| `09_candles_source核对.json` | 抽 40 个 E 类 run：**35 个数据来自盘上却被贴「读时取」** ⇒ 该字段不能当判据 | `probes/09_candles_source核对.py` |

## 复算命令

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY evidence/PROJECT-REVIEW-07-R1/probes/01_产物分层.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/02_重开与提示.py      # 需后端 8787 存活
$PY evidence/PROJECT-REVIEW-07-R1/probes/02b_重开实测.py       # 需后端 8787 存活，约 5 分钟（6 并发，实测会把后端压重启）
$PY evidence/PROJECT-REVIEW-07-R1/probes/02c_补测.py           # 只补 02b 里 http=-1 的记录，并发 2，约 20 分钟
$PY evidence/PROJECT-REVIEW-07-R1/probes/03_引擎与成交事件.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/04_身份核对.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/05_周期口径.py        # 需后端 8787 存活
$PY evidence/PROJECT-REVIEW-07-R1/probes/06_止损路径复核.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/07_按引擎重算.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/08_软链接核对.py
$PY evidence/PROJECT-REVIEW-07-R1/probes/09_candles_source核对.py  # 需后端 8787 存活
```

## 边界声明

- 本单**没有**修 375 个数组格式 chart、**没有**回填 2,677 个无参数 inputs、**没有**改 240h 视图字段、**没有**停任何进程。
- **`02b_重开实测` 的 6 并发把后端压重启了一次**（pid 79140 → 2978，1,010 个请求 `Connection refused`）。
  这 1,016 条已作废、由 `02c_补测` 以 2 并发重测；引用分层数字时以 `02c` 为准。
  该事件本身也是证据：**读侧在多并发下不稳**，属于运维问题，不是 run 的问题。
- 所有数字都绑定核对时刻（见各 `out/*.json` 的时间戳），跨时段引用需重跑。
