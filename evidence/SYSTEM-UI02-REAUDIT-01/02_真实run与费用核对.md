# 02｜真实 run 与费用一致性核对

> 对应工单第 4 条前半：「核查真实 run 是否存在、收益与费用是否一致」
>
> 复算脚本：`queries/02_真实run对账.py`　原始结果：`out/02_真实run对账.json`
> 方法：不看报告怎么写，直接把交付自带的 `04_回测run详情与summary逐字段对账表.csv` 与磁盘上的 run 目录做机械对账。

---

## 一、结论先行（一句话）

**run 是真的，数字是真的，但「对账表」是假的。**

- 真实 run 存在，产物完整（manifest 16/16 一致），8 个数值字段与 `summary.json` 分毫不差；
- 但 CSV 04 声明的 14 条字段路径里有 **12 条不成立**（`summary.json` 里根本没有 `trades` 这一层）；
- 声称的「产物哈希 `6ff401541802…`」在 run 目录 **19 个文件里零命中**；
- `win_rate = 0.0` 是**把 null 写成 0**。

---

## 二、run 存在性与产物完整性：通过

| 项目 | 结果 |
|---|---|
| run 目录 | `回测日志/event_trend_v0_1/runs_sep50/run_WX_EVENT_TREND_V0_1_BRETTUSDT_1792253027` |
| 存在 | **是** |
| manifest 声明文件数 | 16 |
| bytes + SHA-256 逐文件一致 | **16 / 16** |
| 不一致 | **0** |

⇒ **UI-02 换用真实 run 这一动作成立，且产物未被篡改。** 这是相对 UI-01（虚构 `run_sep50_001`）的实质改进，应记功。

`summary.json` 实际 SHA-256 = `1b053393bb59cca90b06bde51c91be18c34966b04593bbbeaece2af37c12ef05`，与 `manifest.json → files["summary.json"].sha256` **完全一致**。

---

## 三、数值一致性：8 个字段全部对得上

`summary.json` 的真实结构里**没有 `trades` 层**，顶层键是：

```
formal_pass_allowed, funnel, funnel_variant_b, identity, instrument_spec, precision,
reconcile, run_id, spec_status, summary_variant_a, summary_variant_b,
summary_variant_c, symbol, trading_summary, variant
```

数值实际落在 `trading_summary`。逐字段比对：

| 字段 | CSV 04 声称落盘值 | `summary.trading_summary` 真实值 | 一致？ |
|---|---|---|---|
| `initial_equity` | 2000.0 | 2000.0 | **一致** |
| `final_equity` | 1989.9209599312276 | 1989.9209599312276 | **一致** |
| `net_pnl` | −10.079040068772143 | −10.079040068772143 | **一致** |
| `trade_count` | 1 | 1 | **一致** |
| `order_count` | 2 | 2 | **一致** |
| `fee_total` | 0.038118035 | 0.038118035 | **一致** |
| `funding_total` | 0.10349203377214099 | 0.10349203377214099 | **一致** |
| `slippage_total` | 0.02316931335413816 | 0.02316931335413816 | **一致** |
| `formal_pass_allowed` | False | False | **一致** |

**收益与费用本身经得起复算。** 净亏损 −10.08 美元 = 毛亏 −9.93743 + 手续费 0.038118 + 资金费 0.103492 + 滑点 0.023169 + 取整 0.000299，`reconcile` 段里 `cash_identity_ok: true`、`equity_identity_ok: true`、`slippage_split_ok: true`。

`formal_pass_allowed: false` **如实记录未通过**，这一条也记功——没有夸大。

---

## 四、字段路径：12 / 14 条不成立

问题在于 CSV 04 的「服务端文件真实字段」这一列。它写的是：

```
summary.json / trades.initial_equity
summary.json / trades.net_pnl
...
```

但 `summary.json` **没有 `trades` 键**（`"trades" in summary == False`）。一条按这个路径去取值的程序会直接 `KeyError`。

| 对账字段 | CSV 声明路径 | 路径是否存在 |
|---|---|---|
| Run ID | `manifest.json / run_id` | **否**（manifest 顶层只有 `commit_mode` / `files` / `files_count` / `run_dir`，**无 `run_id`**） |
| 策略内核 | `run.json / strategy_id` | 是 |
| 产物哈希 | `manifest.json files.summary.sha256` | **否**（正确写法是 `files["summary.json"].sha256`，且值也对不上，见下节） |
| 初始资金 | `summary.json / trades.initial_equity` | **否**（实为 `trading_summary.initial_equity`） |
| 期末资金 | `summary.json / trades.final_equity` | **否** |
| 净盈亏 | `summary.json / trades.net_pnl` | **否** |
| 交易笔数 | `summary.json / trades.trade_count` | **否** |
| 订单数量 | `summary.json / trades.order_count` | **否** |
| 总手续费 | `summary.json / trades.fee_total` | **否** |
| 总资金费 | `summary.json / trades.funding_total` | **否** |
| 纯滑点损耗 | `summary.json / trades.slippage_total` | **否** |
| 胜率指标 | `summary.json (样本不足30笔)` | **否**（不可解析路径） |
| 夏普与回撤 | `summary.json (样本过小打限制)` | **否**（不可解析路径） |
| 质量门禁 | `summary.json / formal_pass_allowed` | 是 |

**统计：14 行中路径成立 2 行、不成立 12 行。**

这条的意义：**如果这张表是脚本生成的，第一行就会崩。** 它能存在，说明它是人手写的——而手写时把 `trading_summary` 记成了 `trades`。

---

## 五、产物哈希：`6ff401541802…` 在 run 目录零命中

CSV 04 第 3 行声称：

> 产物哈希 `spec_sha256` = `6ff401541802c1e37e39bab4a820a13ac753ff108be8464cf94e25d085e2e2e1`
> 「物理落盘文件 SHA256 哈希完全匹配」→「完全一致」

**实际复算：**

| 检查 | 结果 |
|---|---|
| 逐文件算 SHA-256（19 个文件） | **无一个等于该值** |
| 在全部 `.json` / `.jsonl` 正文里搜该哈希前 16 位 | **零命中** |
| `manifest.json → files["summary.json"].sha256` | `1b053393bb59cca9…`（**不是** `6ff401541802…`） |

⇒ **这个哈希不属于本 run 的任何文件，也不在任何文件里被记录过。** 它是一条无出处的字符串，却被标成「完全一致」。

---

## 六、`win_rate = 0.0` 是把 null 写成 0

| 来源 | `win_rate` 相关字段 | 值 |
|---|---|---|
| `summary.json` | 有无 `win_rate` 键 | **无** |
| `summary.trading_summary` | 有无 `win_rate` 键 | **无** |
| `run.json → metrics.win_rate_pct` | 有 | **`null`** |
| `run.json → metrics.wins` / `losses` | 有 | **`null`** |
| CSV 04 声称 | 「服务端真实文件落盘值」 | **`0.0`** |

`run.json.metrics.basis` 自己写得很清楚：

> 「胜负/回撤/PF/盈亏比该文件未统计 ⇒ **null**，由 GET /metrics 从账本现算」

**文件里没有统计，就应该是 null。写成 `0.0` 等于宣布「0% 胜率」是一个已观测事实，而它并不是。** 这与项目既定纪律「缺结果用 `None` 不充 0」直接冲突。

同一行的「夏普与回撤」写 `None (不合成虚假夏普)` 反而是对的——**同一张表里，一行守纪律、一行破纪律**，更说明它是人手拼的，不是同一套逻辑产出的。

---

## 七、为什么能断定这张表不是脚本生成的

三条独立证据（详见 `04_证据表来源.md`）：

1. **路径不存在**：`trades.*` 会让脚本崩，它没崩 ⇒ 不是脚本取的。
2. **shell 展开残骸**：CSV 04 里 3 处 `zsh.0381` / `zsh.1035` / `zsh.0232`——原本是 `$0.0381` / `$0.1035` / `$0.0232`，`$0` 被 shell 当成「当前 shell 名」展开了。同理 `,000.00`（`$2,000.00`）、`,989.92`、`-.08 (-0.50%)`。
3. **机器侧对账数组为空**：`audit_ui_verification.json` 里 `backtest_run_parity = {}`，**0 行**机器对账记录。

---

## 八、信号 / 持仓 / 止损轨迹的「真实 run」侧核对

工单第 4 条后半要求核对「信号、持仓、止损轨迹的正常／空态／失败状态」。这一部分与 run 无关，属页面状态覆盖，结论在 `01_截图与操作覆盖复算.md` 与 `03_隔离失效与生产污染.md`：

| 模块 | 状态 | 判定 | 关键证据 |
|---|---|---|---|
| 信号 | 正常 | 未验证 | 隔离库确实有 3 条预置信号且清理生效，但页面是否渲染出 3 张卡片无 DOM 记录 |
| 信号 | 空态 | **通过** | `live_signal` 由 3 → 0，页面空态文案存在于构建产物 |
| 信号 | 失败 | **不通过** | 与空态**同字节**（像素差异 0.000%） |
| 持仓 | 正常 | **不通过** | 页面显示「0 仓在持 / 3 笔已平」= 生产账本，隔离库的 1 条持仓从未渲染 |
| 持仓 | 空态 | **不通过** | 与正常态同源同画面（差异 0.081%），不是状态迁移 |
| 持仓 | 失败 | **通过** | 500 注入 + 5.5s 等待，像素差异 23.52%，JS 含「持仓接口读取异常」 |
| 止损轨迹 | 正常 | **不通过** | 与失败态、View06 三张同字节；生产 `paper_stop_history` 0 行，无轨迹可显示 |
| 止损轨迹 | 空态 | **不通过** | 脚本从未产出空态（`state_coverage.stop_trail` 只有 normal/failed） |
| 止损轨迹 | 失败 | **不通过** | 注入后无等待，与正常态同字节 |

---

## 九、本项结论

| 子项 | 判定 | 依据 |
|---|---|---|
| 真实 run 存在 | **通过** | 目录真实存在于 `runs_sep50/` |
| 产物完整性（未被篡改） | **通过** | manifest 16/16 bytes+SHA 一致 |
| 收益与费用数值一致 | **通过** | 8 个数值字段与 `trading_summary` 完全一致 |
| `formal_pass_allowed` 如实记录 | **通过** | `false` 三处一致 |
| 对账表字段路径成立 | **不通过** | 14 行中 12 行路径不存在 |
| 声称的产物哈希真实 | **不通过** | `6ff401541802…` 零命中 |
| `win_rate` 有出处 | **不通过** | 文件为 `null`，表里写 `0.0` |
| 对账表由脚本生成 | **不通过** | 路径会崩 + shell 残骸 + 机器侧 0 行 |
