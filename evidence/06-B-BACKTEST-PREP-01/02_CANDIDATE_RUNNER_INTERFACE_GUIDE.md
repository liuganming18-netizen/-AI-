# 06-B 回测程序准备｜可调用回测执行入口与调用说明

- **工单编号**：06-B-BACKTEST-PREP-01
- **执行模块**：`框架/backend/candidate_backtest_runner.py`
- **导出模块**：`框架/backend/backtest_runner.py`（可通过 `from backtest_runner import run_candidate_backtest` 直接调用）

---

## 一、接口签名与定义

```python
def run_candidate_backtest(
    candidate: Any,
    spec: Dict[str, Any],
    *,
    data_snapshot: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    pit_mode: str = "approximate",
    holdout_sealed: bool = True,
    holdout_range: Optional[Tuple[int, int]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
```

---

## 二、输入参数说明

| 参数名称 | 类型 | 是否必填 | 默认值 | 详细说明 |
| :--- | :--- | :--- | :--- | :--- |
| `candidate` | `Any` | **必填** | 无 | 冻结候选策略对象或策略标识字符串。可传入实现了 `compute_signals` 或 `run_portfolio_execution` 的策略模块/类，亦可传入既有受控策略标识（如 `"WX_OI_RESONANCE_V1"`）。 |
| `spec` | `dict` | **必填** | 无 | 回测规格字典。必须包含：<br>- `symbols`: 标的列表（必须长度为 1，如 `["BTCUSDT"]`）；<br>- `signal_timeframe`: 信号周期（默认 `"1h"`）；<br>- `exec_timeframe`: 执行撮合周期（默认 `"15m"`）；<br>- `window`: 显式 UTC 毫秒窗口 `{"mode": "explicit", "start_ms": int, "end_ms": int}`；<br>- 可选参数覆盖 `signal_overrides`, `exec_overrides`。 |
| `data_snapshot` | `dict` | 可选 | `None` | **固定内存快照字典**。格式：`{"sig_df": DataFrame, "exec_df": DataFrame, "funding_events": list}`。若提供，回测将 **100% 在内存快照中运行**，完全不触碰底层磁盘数据库，实现零 I/O 漂移与极速隔离。 |
| `snapshot_path` | `str / Path` | 可选 | `None` | **固定磁盘快照目录**。若提供，回测将直接读取该目录下已封存的 `<symbol>_signal_bars.csv`、`<symbol>_exec_bars.csv`、`<symbol>_funding_events.json`。 |
| `output_dir` | `str / Path` | 可选 | `None` | 独立的原子输出根目录。若指定，回测产生的所有结果、账本 SQLite、输入快照、日志全部写入该隔离目录，不污染系统默认 `runs/` 目录。 |
| `pit_mode` | `str` | 可选 | `"approximate"` | 历史可得时间（Point-In-Time）模式：<br>- `"strict"`: 严格因果模式。若 K 线或资金费缺少明确的 `available_at` 证明，数据门禁直接触发硬阻断并置 `status="failed"`；<br>- `"approximate"`: 近似模式。允许在开发验证区间运行，但显式标记 `pit_status="PIT_APPROXIMATE"` 并将数据质量标记为 `degraded`，拒绝冒充可信。 |
| `holdout_sealed` | `bool` | 可选 | `True` | **最终盲测区间物理封存硬闸**。默认保持 `True`。回测窗口一旦触碰盲测区间（默认 2024-10-01 之后），立即抛出 `HoldoutSealedError` 异常阻断，严禁在准备阶段打开盲测。 |
| `holdout_range` | `tuple` | 可选 | `(1727740800000, 1735689600000)` | 盲测区间的起止毫秒时间戳 `(start_ms, end_ms)`。 |
| `run_id` | `str` | 可选 | `None` | 指定原子运行 ID。若未提供，系统根据规格哈希与时间戳自动生成合规 ID（格式：`bt_YYYYMMDDTHHMMSSZ_xxxxxxxx`）。 |

---

## 三、固定快照绑定与身份校验机制（P0-1）

系统杜绝“只读直连持续增长的 SQLite 并冒充固定快照”的伪可信行为：
1. **快照优先级**：`data_snapshot` (内存) > `snapshot_path` (文件快照) > `read_repo` (只读归档抽取)；
2. **外部快照冻结身份强制校验**：若传入 `snapshot_path`，系统在复用前自动读取快照目录下的 `inputs_manifest.json`，并与实际文件 SHA256 逐一强验。若发现任何文件哈希漂移或缺失，直接抛出 `CandidateExecutionError` 拒绝执行，杜绝污染与覆盖。
3. **不可变落盘**：无论从何种来源加载，回测引擎在启动计算前，均在当前运行目录的 `inputs/` 子目录下原子生成一份不可变快照文件：
   - `<symbol>_signal_bars.csv`
   - `<symbol>_exec_bars.csv`
   - `<symbol>_funding_events.json`
4. **数字签名清单**：生成 `inputs_manifest.json`，内含各切片的 SHA256 摘要与记录数，确保输入一旦确定即可跨机器、跨时间 100% 逐位复现，不受任何外部数据库更新影响。

---

## 四、同运行编号防篡改、写前拒绝与受控中断恢复（P0-5）

1. **同 run_id 规范化比对防漂移**：若显式传入同一 `run_id`，系统对新旧配置执行严格规范化（补齐缺省值、新增字段、exchange/market、timeframe、warmup_bars、pit_mode 等）并进行全字段比对。若配置发生任何漂移，直接抛出 `CandidateExecutionError` 拒绝。
2. **写前拒绝与防覆盖铁律**：
   - 即使传入带有自身有效 manifest 的不同内容外部快照，只要指纹与旧 run 已绑定的输入身份不符，绝对禁止覆盖旧 run；
   - 所有冲突均在 `set_status(RUNNING)` 之前抛错拒绝；
   - 拒绝后旧 run 的成功状态、快照文件及账本保持 100% 原始不变。
3. **复用凭据强验**：冻结复用时，若快照或 manifest 损坏/缺失，直接抛错拒绝，**严禁只警告后继续**。
4. **受控中断恢复**：若运行因中途崩溃（如进程被终止、状态停留在 `RUNNING` 且账本残留部分脏行），再次使用相同身份调用时，系统在写账前原子清理残留行，确保恢复后账本与不中断基准 100% 一致，无重复、漏记，状态正常成功结束。
5. **真实实验门禁复用与防绕过（P0-6）**：
   - 检查实验注册表中的存在性、冻结状态（`frozen_at`）及批准开发范围（`sample_scope`）；
   - 不存在的非空实验 ID 直接抛错拒绝；
   - 正式候选必须绑定已冻结实验，不得靠省略 `is_formal` 或传入 `holdout_sealed=False` 绕过；
   - 机制测试走明确的 `is_mechanism_test=True` 隔离测试通道，盲测保护依然硬阻断。

---

## 四、输出规范与对账结构

调用返回标准回测文档字典，包含以下关键键：

```json
{
  "run_id": "bt_20261110T142828Z_6904feee",
  "status": "succeeded",
  "window": {
    "symbol": "BTCUSDT",
    "win_start_ms": 1704067200000,
    "win_end_ms": 1704672000000,
    "window_days": 7.0
  },
  "metrics": {
    "trades_count": 0,
    "net_pnl": 0.0,
    "net_return_pct": 0.0,
    "total_fees": 0.0,
    "total_funding_fee_confirmed": 0.0,
    "total_funding_fee_hypothetical": 0.0,
    "funding_ok": true
  },
  "ledger_recomputed": {
    "available": true,
    "trades_count": 0,
    "net_pnl": 0.0,
    "cash_identity_ok": true,
    "equity_identity_ok": true
  },
  "data_quality": {
    "status": "ok",
    "pit_status": "PIT_PROVEN",
    "reasons": []
  },
  "snapshot_manifest": {
    "signal_bars_sha256": "...",
    "exec_bars_sha256": "...",
    "funding_events_sha256": "..."
  },
  "holdout_sealed": true,
  "duration_seconds": 0.035
}
```

---

## 五、隔离位置与落盘产物

回测运行完全与实盘/模拟盘隔离，产物仅写入对应运行目录（`runs/<run_id>/`）：

| 产物文件 | 格式 | 用途说明 |
| :--- | :--- | :--- |
| `run.json` | JSON | 完整运行元数据、指标、数据质量门禁与状态 |
| `summary.json` | JSON | 供历史视图与外部 UI 统一读取的摘要 |
| `inputs_manifest.json` | JSON | 输入数据快照数字签名清单与哈希校验值 |
| `inputs/` | 目录 | 本次回测实际使用的不可变输入切片（CSV / JSON） |
| `trades.jsonl` | JSONL | 逐笔交易明细记录（开平仓时间、价格、盈亏、费用） |
| `orders.jsonl` | JSONL | 逐笔订单意图与成交明细 |
| `positions.jsonl` | JSONL | 持仓生命周期与 95% 止损上移轨迹记录 |
| `funding.jsonl` | JSONL | 资金费事件结算记录（已确认与假设桶明细） |
| `equity_curve.jsonl` | JSONL | 15m 逐点权益曲线（现金权益与总权益） |
| `ledger.sqlite3` | SQLite | 独立账本数据库（符合 `LEDGER_SCHEMA`，支持 SQL 幂等对账） |

---

## 六、失败行为与日志记录

1. **盲测越界失败**：抛出 `HoldoutSealedError`，不写运行目录，直接硬拦截；
2. **严格 PIT 门禁阻断**：置 `status="failed"`，写入 `failure_reason="严格 PIT 门禁阻断..."`，写入 `logs/backtest_runs.log`；
3. **对账不一致失败**：若引擎输出指标与独立账本重算指标不符（误差超出容差），立即置 `status="failed"`，记录对账差异项；
4. **日志追踪**：每次运行的启动、成功、失败均写入 `框架/backend/logs/backtest_runs.log`，带有 UTC 时间戳、标的、运行编号与耗时。
