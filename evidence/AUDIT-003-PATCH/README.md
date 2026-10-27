# AUDIT-003-PATCH｜针对 AUDIT-003 退回项的定点补修（BLK-02 / BLK-01）

- 日期：2026-10-27（北京时间）
- 基线：`wyckoff-v3.1-main` @ `38eaa83b`（对应审计报告 `22c126be`）
- 状态：**两项退回缺陷已堵住，回归测试 68/68 通过**；**甲乙丙成绩仍不可引用**（见第六节 DEC-01）
- 性质：执行方自报，未经独立验收。本文所有数字均由脚本从产物 JSON 生成，不手抄。

---

## 零、一句话结论

| 退回项 | 原状 | 现状 |
|---|---|---|
| **BLK-02（P0）** 价格区间校验被全链路绕过 | `validate_execution_price_in_bar` 只在单测和 `scan_exit` 里出现，且 `scan_exit` 调用完**只记录不拒单**；**正式撮合 `open_position()` / `close_at_price()` 根本没调**；`plan_entry()` 的高低价是 `Optional=None` + `if bar_low is not None:` 条件分支 ⇒ 漏传即整段跳过 | 三处入口统一走 `require_bar_range()`；`plan_entry` 高低价改为**无默认值必填**（漏传直接 `TypeError`）；6 个真实调用点逐一补齐；**单事件回放 `replay_one` 的退出侧也 fail-closed**；`scan_exit` 不再伪造 `high`；缺参数/越界一律 `MISSING_BAR_RANGE` / `EXEC_PRICE_OUT_OF_RANGE` 拒单，**零写入** |
| **BLK-01（P1）** 退出异常不阻断绩效 | 退出推进捕获异常只 `print`，`research_run` 照常返回，跑分器照常出收益率/胜率 | `run_state` 一等公民：异常/平仓被拒/退出原因异常 ⇒ `run_status=FAILED` + 原因码；`run_status.json` 落盘；`run_abc_pool.ledger_metrics` 与 `backtest_metrics.compute_metrics` 两处入口都**先判状态再算指标**，无效运行 6 个绩效字段全 `None` |

**但**：补修后跑甲乙丙，**ARM A（现行默认 `gap_model=stop_price`）被判 `FAILED`**。这不是门禁误杀，而是暴露了默认跳空模型给出**该 bar 从未到过**的成交价（16 笔持仓，成交价高出当根 bar `high` 最少 3.58%）。按工单约束 2 我**没有改**这个默认值，见第六节 **DEC-01**。

---

## 一、BLK-02 根因（逐环有代码依据）

### 1.1 伪保护：函数存在，但撮合链路不经过它

FIX-002 引入了 `replay_calc.validate_execution_price_in_bar()`。AUDIT-003 的核查结论是：

- 该函数在基线（`38eaa83b`）里只有两处调用：
  1. `plan_entry` 第 218 行——**被 `if bar_low is not None` 包住**，可整段跳过（见 1.2）；
  2. `scan_exit` 第 372 / 388 行——**调用完只把 `price_valid` / `reject_code` 写进返回 dict，不拒单**，
     调用方 `replay_one` 也从不读这两个字段 ⇒ 只起"记录"作用，不构成门禁；
- 真正把成交价写进 `paper_position` / `paper_fill` / `paper_realized` 的
  `paper_engine.open_position()` 与 `paper_engine.close_at_price()` **一次都没调用它**；
- ⇒ 单测绿灯、撮合裸奔。**"有校验函数" ≠ "校验在链路上"，"算出了 valid=False" ≠ "拒了单"。**

### 1.2 `plan_entry()` 的条件分支是第二个穿透点

修复前（`replay_calc.py`）：

```python
def plan_entry(..., bar_low: Optional[float] = None, bar_high: Optional[float] = None, ...):
    ...
    if bar_low is not None and bar_high is not None:      # ← 条件式
        valid_px, dev, rej_px_code = validate_execution_price_in_bar(...)
```

两层问题叠加：

1. **默认值是 `None`** ⇒ 不传参数是**合法调用**，不报错；
2. **校验被 `if` 包住** ⇒ 不传就整段跳过，静默穿透。

而且唯一的生产调用点 `replay_calc.replay_one()` 当时**确实没传**这两个参数 ⇒ 每一笔入场都走"跳过校验"分支。

### 1.3 为什么"单测通过"完全不能证明它有效

单测自己构造 `bar_low/bar_high` 并直接调 `plan_entry`，覆盖的是"函数内部逻辑对不对"；
AUDIT-003 指出的是"**函数在不在生产链路上**"。这是两个不同命题。
本单因此把验证方式改成 **在撮合函数内部打桩**（见第四节 T4/T5：只固定外围依赖，断言"门禁是否真的接进撮合"）。

---

## 二、BLK-01 根因

`accum_early_entry.py` 修复前：

```python
try:
    res = PE.close_at_price(...)
except Exception as e:
    print(f"[exit] error: {e}")     # ← 只打印
    continue                        # ← 继续跑下一个
```

后果链条：

1. 某个持仓平仓失败 ⇒ 仓位仍 `status='open'`，但时间轴已经走过；
2. 后续所有标的继续入场、继续平仓，账本里"该平的没平"；
3. `research_run()` 正常返回，`run_abc_pool` 照常读账本、照常算 `win_rate` / `net_pnl`；
4. ⇒ **带病产出绩效报告，且报告上没有任何"这次不算"的标记**。

更隐蔽的一条（本单额外发现）：`drive_position_exit` 原来只判 `exit_ts` 是否存在来决定"是否算一次退出"。
如果 `close_at_price` 返回 `ok=False`（拒单），`exit_ts` 仍是调用方传进来的值 ⇒ **拒单被误记成一次成功退出**，
仓位却还开着 ⇒ 既漏记又带病出成绩。这条已在同一处修掉（`close_rejected=True` + `exit_ts=None` + `net_pnl=None`）。

---

## 三、修法

### 3.1 P0：把价格区间校验焊进撮合，且只留一个原因码

#### (1) 新增统一门禁 `require_bar_range()`（`replay_calc.py:150`）

```python
def require_bar_range(bar_low, bar_high, *, where: str = "") -> Optional[dict]:
    """返回 None = 合格，否则返回拒单 dict。"""
```

**为什么必须抽成一个函数**：这条约束要在 `plan_entry` / `open_position` / `close_at_price`
三处执行。如果各写一份，同一类"漏传高低价"会被拆成三种原因码，
按码统计时就查不出"到底有几处漏传"。

判据（fail-closed，任一命中即拒）：

| 情形 | 结果 |
|---|---|
| `bar_low is None` 或 `bar_high is None`（含只传一个） | `MISSING_BAR_RANGE` |
| 无法解析为 float | `MISSING_BAR_RANGE` |
| 非有限数（`inf`/`nan`） | `MISSING_BAR_RANGE` |
| `low <= 0` 或 `high <= 0` | `MISSING_BAR_RANGE` |
| `high < low` | `MISSING_BAR_RANGE` |

#### (2) `plan_entry()` 高低价改为无默认值必填（`replay_calc.py:181-186`）

```python
def plan_entry(*, symbol: str, raw_open: float, prev_quote_volume: Optional[float],
               ...
               bar_low: float,          # ← 原 Optional[float] = None
               bar_high: float,         # ← 原 Optional[float] = None
               tol: float = 0.001) -> dict:
```

去掉默认值后，**漏传在调用处直接 `TypeError`**，不可能再静默穿透；
显式传 `None` 则由 `require_bar_range` 拒单。函数体里的校验也从条件式改为**无条件**：

```python
valid_px, dev, rej_px_code = validate_execution_price_in_bar(
    entry_price, bar_low, bar_high, tol=tol)
```

> 这是本单最重要的一处结构性改动：**用 Python 的关键字参数机制把"漏传"从运行时静默变成编译期/调用期硬错**，
> 而不是靠"记得传"。

#### (3) `paper_engine.open_position()` 在写任何表之前强制校验（`paper_engine.py:1229-1254`）

```python
import replay_calc as _RC
_bar_low  = _f(bar["low"])  if "low"  in bar.keys() else None
_bar_high = _f(bar["high"]) if "high" in bar.keys() else None
_range_gate = _RC.require_bar_range(_bar_low, _bar_high,
                                    where="paper_engine.open_position")
if _range_gate is not None:
    return _reject(arm, symbol, signal_uid, _RC.REJECT_MISSING_BAR_RANGE,
                   detail=_range_gate.get("detail"))
_px_ok, _px_dev, _px_code = _RC.validate_execution_price_in_bar(
    fill, _bar_low, _bar_high)
if not _px_ok:
    return _reject(arm, symbol, signal_uid, _px_code, detail={...})
```

位置很关键：插在 `fill / stop / qty / notional / planned_risk` **算完之后、`INSERT INTO paper_position` 之前**。
所以自动开仓、手工开仓、挂单推进三条路径**都**经过它（它们都走 `open_position`）。

拒单落 `paper_order(status=rejected)` + `paper_blocked`，原因码可对账。

#### (4) `paper_engine.close_at_price()` 高低价必填 + 在开连接之前判（`paper_engine.py:1788-1848`）

签名新增 `bar_low=None, bar_high=None, tol=0.001`；门禁放在 **`con = L.paper_conn(arm)` 之前**：

```python
params = approved_params()
src = bar_source or MARKET_SOURCE
# ── 高低价先判：这是调用链契约，必须在**任何副作用之前** ────
# 放在开连接之前，缺参数时既不建库也不读行情，杜绝「先写半条再拒」。
import replay_calc as _RC
_range_gate = _RC.require_bar_range(bar_low, bar_high,
                                    where="paper_engine.close_at_price")
if _range_gate is not None:
    return {"ok": False, "reason": _RC.REJECT_MISSING_BAR_RANGE,
            "position_id": position_id, "detail": _range_gate.get("detail")}
con = L.paper_conn(arm)
```

算出 `fill` 之后**无条件**校验 `fill`（不是 `raw`）：

```python
_px_ok, _px_dev, _px_code = _RC.validate_execution_price_in_bar(
    fill, bar_low, bar_high, tol=tol)
if not _px_ok:
    return {"ok": False, "reason": _px_code, "position_id": position_id, ...}
```

**为什么校验 `fill` 而不是 `raw`**：越界来源有两类，都要拦——

1. 调用方给了不属于这根 bar 的价格（拿错 bar / 前视 / 编造）；
2. 价格步长过粗把成交价取整到区间外（FIX-002 的 `0.00` 病根）。

只校验 `raw` 会漏掉第 2 类。

#### (5) 新增原因码并登记为"执行有效性"（`策略/WyckoffMaster/trading_core/rejects.py`）

```python
# [AUDIT-003-PATCH] 调用方未传执行 K 线高低价 —— 与「越界」分开记：
# 越界是数据/模型问题，缺高低价是**调用链漏传**问题，两者的整改动作不同。
REJECT_MISSING_BAR_RANGE = "MISSING_BAR_RANGE"

FIX002_VALIDITY_REASONS = frozenset({..., REJECT_MISSING_BAR_RANGE, "missing_bar_range"})
```

#### (6) 单事件回放的**退出侧**：`scan_exit` 不再伪造 `high`，`replay_one` fail-closed

这一条对应工单的"**检查所有单事件回放**与撮合调用点"。基线里 `scan_exit` 有两处"静默跳过"：

```python
# 基线 replay_calc.py:406-408
if o is None or lo is None:
    continue                                        # ← 缺 low 就整根跳过（可能漏判跳空）
bar_hi = hi if hi is not None else max(o, cur_stop) # ← 缺 high 就**伪造**上界 ⇒ 校验必过
```

修法：

```python
o, lo, hi = _f(b.get("open")), _f(b.get("low")), _f(b.get("high"))
if o is None:
    continue                    # 判跳空只需要 open
bar_hi = hi                     # 原样传 None，交给门禁判 MISSING_BAR_RANGE
...
valid_px, dev, rej_px_code = check_price_in_bar(fill, lo, bar_hi, tol=tol)
```

新增 `check_price_in_bar()`——**先判高低价存在性，再判越界**：

```python
def check_price_in_bar(fill_price, bar_low, bar_high, *, tol: float = 0.001):
    if require_bar_range(bar_low, bar_high, where="check_price_in_bar") is not None:
        return False, 0.0, REJECT_MISSING_BAR_RANGE
    return validate_execution_price_in_bar(fill_price, bar_low, bar_high, tol=tol)
```

为什么不直接用 `validate_execution_price_in_bar`：它把"缺高低价"和"价格越界"都返回
`EXEC_PRICE_OUT_OF_RANGE`，会把两类完全不同的缺陷（调用链漏传 vs 数据/模型问题）统计成一类。

`replay_one()` 在算出 `ex` 后**不再只记录、而是拒掉整条回放**：

```python
if ex.get("hit") and not ex.get("price_valid"):
    return {"status": "invalid_exit_price",
            "reason": ex.get("reject_code") or REJECT_EXEC_PRICE_OUT_OF_RANGE,
            "已实现净额": None, "未实现净额": None, "盈亏": None,
            "跳空敏感性": sens,          # 保留，便于诊断
            "说明": "退出成交价不落在执行 K 线内 ⇒ 本次回放判无效 ..."}
```

`gap_sensitivity()` 的每个模型也补上 `price_valid` / `reject_code`——
**三个模型不再被并列成"三个可选方案"**，物理上不可能成交的那个会被标出来。

### 3.2 P0：全部撮合/回放调用点清单（工单要求"检查所有调用点"）

逐个核对，**6 个真实撮合调用点 + 1 个单事件回放退出点全部补齐**（不是只改被点名的那两个）：

| # | 文件:行 | 调用 | 传入的高低价来源 | 状态 |
|---|---|---|---|---|
| 1 | `replay_calc.py:647` | `plan_entry(...)` | `entry_bar.get("low")` / `.get("high")` | 已补 |
| 2 | `paper_engine.py:1778` `close_position_now` | `close_at_price(...)` | `_f(row["low"])` / `_f(row["high"])`（该 15m 收盘 bar） | 已补 |
| 3 | `paper_engine.py:2084` 回撤清仓 | `close_at_price(...)` | `_f(nxt["low"])` / `_f(nxt["high"])`（下一根开盘 bar） | 已补 |
| 4 | `paper_engine.py:2539` `check_position_exit_on_bar` | `close_at_price(...)` | `low_p` / `float(bar_detail["high"])` | 已补 |
| 5 | `accum_early_entry.py:1860` `drive_position_exit._exit` | `close_at_price(...)` | `float(bar["low"])` / `float(bar["high"])` | 已补 |
| 6 | `live_worker.py:440` `_scan_position_exit` 调用处 | `close_at_price(...)` | `_scan_position_exit` 返回值**末尾追加** `bar_low, bar_high` | 已补 |
| 7 | `replay_calc.py:672` `replay_one` 退出侧 | `scan_exit(...)` → `check_price_in_bar` | 直接用 bar 自身 `low` / `high`（不再伪造） | 已补 |

第 6 处的实现细节：`_scan_position_exit()` 有 **6 个 `return` 分支**（gap-long / gap-short / 720h 到期 /
Donchian 信号退出 / bar 内触 stop-long / bar 内触 stop-short），逐个在元组末尾追加 `lo, hi`；
调用方改成 `raw, bot, bct, why, exit_ts, dctx, b_lo, b_hi = hit`。
**追加在末尾**而不是插在中间，是为了让任何按索引取值的历史代码不被打断（本单已验证：全仓无其他按索引取该元组的位置）。

> 说明：`_scan_position_exit` 返回元组而非 dict 属于既有设计，本单不做重构（工单约束 1）。

### 3.3 P1：运行可信度一等公民化

#### (1) `accum_early_entry.py` 新增运行状态机

```python
RUN_STATUS_OK     = "OK"
RUN_STATUS_FAILED = "FAILED"

INVALID_EXIT_DRIVE_ERROR        = "EXIT_DRIVE_ERROR"
INVALID_EXIT_CLOSE_REJECTED     = "EXIT_CLOSE_REJECTED"
INVALID_TIMELINE_REPLAY_ERROR   = "TIMELINE_REPLAY_ERROR"
INVALID_DRIVE_EXITS_ERROR       = "DRIVE_EXITS_ERROR"

_EXIT_INVALID_REASONS = frozenset({
    "missing_contract_spec", "MISSING_CONTRACT_SPEC",
    "EXEC_PRICE_OUT_OF_RANGE", "MISSING_BAR_RANGE", "INVALID_QTY_PRECISION",
})

def new_run_state() -> dict: ...
def mark_run_invalid(run_state, code, detail=None) -> None: ...
```

三条标记路径：

| 触发 | 原因码 |
|---|---|
| `_advance_open_positions` / `_replay_timeline` / `drive_all_exits` 捕获异常 | `EXIT_DRIVE_ERROR` / `TIMELINE_REPLAY_ERROR` / `DRIVE_EXITS_ERROR` |
| `drive_position_exit` 里 `close_at_price` 返回 `ok=False` | `EXIT_CLOSE_REJECTED` |
| 退出原因码落在 `_EXIT_INVALID_REASONS`（越界/缺规格/精度非法） | 原码透传 |

`research_run()` 返回顶层新增 `run_status` / `invalid_reasons` / `run_problems` / `run_invalid_note`；
`timeline` 的 stats 也带 `run_status`（避免下游只读 stats 时看不到）。

#### (2) `drive_position_exit`：平仓被拒不再当退出

```python
res = PE.close_at_price(..., bar_low=float(bar["low"]), bar_high=float(bar["high"]), ...)
if not res.get("ok"):
    row["reason"] = res.get("reason") or "exit_close_rejected"
    row["close_rejected"] = True
    row["exit_ts"]  = None      # ← 关键：不再假装平掉了
    row["net_pnl"]  = None
```

#### (3) 两个绩效入口都先判状态

**跑分器** `evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py`：

- `WORKER_SRC` 透传 `run_status` / `invalid_reasons` / `run_problems`；
- `_block_metrics(out, run_invalid)` 抹掉一切绩效结论（`n_trades`/`win_rate`/`net_pnl`/`profit_factor`/`max_drawdown`/`annualized_return` 全 `None`），**保留账本事实**（`n_positions_rows`/`realized`/`exit_reasons`）；
- `main()` 里 `status = "INVALID_RUN_FAILED"`，优先级**高于** drift 与 unapproved；
- 落 `run_dir/run_status.json`。

**生产指标入口** `框架/backend/backtest_metrics.py`：

```python
def load_run_status(run_dir) -> Optional[dict]:      # 读 run_status.json
_INVALID_STATUSES = frozenset({"FAILED", "INVALID", "ERROR", "ABORTED"})

def compute_metrics(run_dir, run_id, doc=None):
    _st = load_run_status(run_dir)
    if _st is not None and _st["status"] in _INVALID_STATUSES:
        return {..., "invalid": True, "blocked_by": "run_status_gate",
                "net_return_pct": None, "win_rate_pct": None, ...}, notes
```

**无 `run_status.json` 不阻断**（兼容历史 run，不追溯否定已归档结果）。
这条是本单的刻意选择：不阻断历史、只阻断"新跑出来且明确标了无效"的。

### 3.4 一处不得不改的测试适配

`策略/WyckoffMaster/event_trend_v0_1/tests/test_fix002_precision_and_gates.py` 两个原有用例补了
`bar_low=99.5, bar_high=100.5`。原因：`plan_entry` 的高低价已改必填，不补就 `TypeError`。
**这两个用例的断言内容一字未改**（仍断言越界被拒 / 合法通过），只是补齐调用参数。

---

## 四、测试结果

### 4.1 本单新增回归测试：68 项断言全通过

脚本：`scripts/test_patch_price_gate_and_run_status.py`（~660 行，自带 `Sandbox` 隔离
`MARKET_DB` / `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_GATE_FILE`）

原始输出：`out/02_test_output.txt`（`通过 68 / 失败 0 / 异常 0`，exit 0）

| 组 | 覆盖 | 断言数 | 负向对照 |
|---|---|---|---|
| T1 | `close_at_price` 缺 `bar_low/bar_high` ⇒ `MISSING_BAR_RANGE` 且**零写入**、仓位仍 `open` | 7 | 含"只传 low"、"`high<low`"两个非法形态 |
| T2 | `close_at_price` 成交价越界 ⇒ `EXEC_PRICE_OUT_OF_RANGE`、仓位仍 `open` | 5 | T3 |
| T3 | **负向对照**：同持仓 + 合法区间 ⇒ 正常平仓成功 | 3 | 证明 T2 不是"一律拒绝" |
| T4 | `open_position` 成交价越界 ⇒ 拒单（门禁真接在撮合里） | 4 | T4.4 合法区间 ⇒ `filled` |
| T5 | `open_position` 成交 K 线缺 `high` ⇒ `MISSING_BAR_RANGE` | 3 | — |
| T6 | `plan_entry` 漏传 ⇒ `TypeError`；显式 `None` ⇒ `MISSING_BAR_RANGE` | 3 | T6.3 合法区间 ⇒ `ok` |
| T7 | `replay_one` 真的把 `entry_bar` 高低价传下去了（**入场侧**） | 3 | 含"缺 low/high"分支 |
| T12 | `replay_one` **退出侧**越界 ⇒ `invalid_exit_price`，`已实现/未实现净额` 全 `None` | 5 | T12.5 高覆盖止损价 ⇒ 正常出数 |
| T13 | `scan_exit` 缺 `high` ⇒ `MISSING_BAR_RANGE`（不再伪造上界）；`gap_sensitivity` 标出 `price_valid` | 8 | T13.5 有 high 且合法 ⇒ `True` |
| T8 | 注入 `drive_position_exit` 抛异常 ⇒ `run_status=FAILED` + `EXIT_DRIVE_ERROR` | 4 | T9 |
| T9 | **负向对照**：退出正常 ⇒ `run_status=OK`、`invalid_reasons=[]` | 2 | 证明 T8 不是恒失败 |
| T10 | `ledger_metrics(run_invalid=...)` ⇒ 绩效全 `None` + `invalid=True` + 原因码 | 10 | 同函数 `run_invalid=None` ⇒ 正常出数 |
| T11 | `compute_metrics` 读到 `run_status.json=FAILED` ⇒ 6 个指标全阻断 | 11 | T11.1 无文件不阻断 / T11.6 标记 OK 不阻断 |

分组小计：**P0（T1–T7、T12、T13）= 41 项**、**P1（T8–T11）= 27 项**，合计 68。

**T13 顺带给出了 DEC-01 的定量证据**（单笔 1 单位、止损 95、跳空 bar `[89, 90.5]`、开盘 90）：

| gap_model | 成交价 | `price_valid` | 净额 |
|---|---|---|---|
| `stop_price`（现行默认） | 95.0 | **False** | −5.10 |
| `midpoint` | 92.5 | **False** | −7.60 |
| `bar_open` | 90.0 | True | −10.10 |

⇒ 默认口径把这一笔的亏损**少算了约 50%**。这是"默认模型偏乐观"的最小可复现证据。

**关键设计**：T4/T5 用 `_open_position_with_bar(*, bar, fill_price, uid, price_step=0.01)`，
只固定外围依赖、**显式指定 `fill_price`**，复刻 AUDIT-003 引用的真实场景
（raw `0.021` 被粗 tick 取整成 `0.03`，而当根 bar 最高只有 `0.02127`）。
不另造第二套取整口径 —— 复算取整已由 FIX-002 的 `test_plan_entry_with_out_of_bar` 覆盖。

### 4.2 回归矩阵（共线性）

原始输出：`out/03_regression_matrix.txt`

| # | 命令 | 结果 |
|---|---|---|
| 1 | `test_fix001_timeline.py --integration` | **17 / 17 通过** |
| 2 | `pytest 框架/backend/tests/test_accum_early_entry.py test_business.py` | **78 passed / 23 skipped** |
| 3 | `pytest 策略/WyckoffMaster/event_trend_v0_1/tests` | **4 failed / 122 passed**（4 项为已证基线失败） |
| 4 | `pytest .../test_fix002_precision_and_gates.py` | **11 passed** |
| 5 | `pytest .../test_spec_historical_applicability.py` | **35 passed** |
| 6 | `pytest 框架/backend/tests/test_paper_live_01.py` | **1 failed / 4 passed**（基线既有失败） |

> 该矩阵在 `replay_calc.py` 每一轮改动后都复跑过（含最后一次加 `n_bars_no_low` 计数），**数字不变**。

**基线失败的判定手法**（不是"我觉得无关"）：
备份本单 8 个改动文件 → `git checkout --` 回退到 `38eaa83b` → 同一命令复跑 → 得到**相同**结果
→ 按 sha256 逐字节还原（8/8 OK）。

- [3] 的 4 项典型报错是 `assert None == 10`（`max_holding_days` 配置视图口径），与本单无关；
- [6] 的 `test_03` 成因：该测试的退出判定只按 `low <= stop` 触发，而 `gap_model=stop_price`
  在开盘跳空时给出的成交价落在当根 bar 之外 ⇒ 门禁判 `EXEC_PRICE_OUT_OF_RANGE` ⇒ `exits_triggered=0`。
  **这不是门禁误杀，是第六节 DEC-01 同一个未批准默认口径的另一种表现。**

### 4.3 解释器差异（踩坑记录，复现时必读）

| 用途 | 解释器 | 原因 |
|---|---|---|
| 独立脚本 | `/usr/bin/python3` | 有 numpy 2.0.2 / pandas 2.3.3 |
| pytest | `~/.workbuddy-ai/binaries/python/envs/default/bin/python` | pytest 9.1.1 + numpy 2.5.4 |

用托管 `python3.13.12` 跑独立脚本会失败：它**没有 numpy**，`close_at_price` 内部
`from 策略.WyckoffMaster.trading_core.fill import price_breakdown` 抛 `ModuleNotFoundError`
⇒ 会把 T2/T3/T10 误判成失败（我第一次跑就踩了这个坑）。

---

## 五、补修后甲乙丙重跑对账（两臂）

产物：`out/`（ARM A）、`out_baropen/`（ARM B）；汇总 `out/05_两臂对账.md`；被拒明细 `out/04_reject_detail.txt`

### 5.1 两臂的定位（先读这段，否则会误读成绩）

| 臂 | `exit.gap_model` | 定位 | 成绩可否引用 |
|---|---|---|---|
| **A** | `stop_price` | **现行默认**，`fixture_rules_06w2.json` 原样，**未获批准** | **否**（run 被判 `FAILED`） |
| **B** | `bar_open` | 敏感性对照，仅换这一个字段，**同样未获批准** | **否**（只用于证明门禁不阻断合法链路） |

两臂的**唯一**差异是 `exit.gap_model` 一个字段（`config/fixture_rules_06w2_gap_bar_open.json`，
与 `fixture_rules_06w2.json` 逐字节相同，仅该字段 + `_patch_note`）。
两臂 `code_drift` 均为 `[]`。

### 5.2 主对账表

| 项 | ARM A `stop_price` | ARM B `bar_open` |
|---|---|---|
| 标的数 | 40 | 40 |
| 候选 4a / 4b / 4c | **105 / 87 / 97** | **105 / 87 / 97** |
| 开仓 4a / 4b / 4c | 65 / 82 / 88 | 100 / 24 / 26 |
| `n_exits` | 251 | 150 |
| `run_status` | **FAILED** | OK |
| `invalid_reasons` | `['EXIT_CLOSE_REJECTED']` | `[]` |
| `run_problems` | 20 | 0 |

**候选数两臂完全一致（105/87/97），且与 AUDIT-003 审计运行一致** ⇒ 本单没有改动信号识别链路，
工单约束 1 达成。（40 个 symbol 取自 `ACCUM-EARLY-07-W5/out/回测_甲乙丙对比.json` 的原运行名单，
不是"池子前 40 个"；名单见 `out/00_symbols.txt`。）

### 5.3 绩效指标：ARM A 全阻断，ARM B 出数

| scheme | 臂 | n_trades | win_rate | net_pnl | status | blocked_by |
|---|---|---|---|---|---|---|
| 4a | A | `None` | `None` | `None` | FAILED | `run_status_gate` |
| 4b | A | `None` | `None` | `None` | FAILED | `run_status_gate` |
| 4c | A | `None` | `None` | `None` | FAILED | `run_status_gate` |
| 4a | B | 99 | 8.08% | −486.81 | — | — |
| 4b | B | 24 | 8.33% | −143.27 | — | — |
| 4c | B | 26 | 11.54% | −173.61 | — | — |

**`None` 不是"算出来是 0"，是主动不产出。** 这是 BLK-01 的验收点。

### 5.4 账本事实仍保留（阻断指标 ≠ 抹掉账本）

| scheme | 臂 | positions | realized | n_open_at_end | exit_reasons |
|---|---|---|---|---|---|
| 4a | A | 65 | 55 | 10 | `initial_stop_loss:26 / trailing_stop_loss:29` |
| 4b | A | 82 | 80 | 2 | `initial_stop_loss:36 / trailing_stop_loss:44` |
| 4c | A | 88 | 83 | 5 | `initial_stop_loss:40 / trailing_stop_loss:42 / trailing_stop_gap:1` |
| 4a | B | 100 | 99 | 1 | `initial_stop_loss:46 / trailing_stop_loss:41 / trailing_stop_gap:12` |
| 4b | B | 24 | 24 | 0 | `initial_stop_loss:10 / trailing_stop_loss:13 / trailing_stop_gap:1` |
| 4c | B | 26 | 26 | 0 | `initial_stop_loss:11 / trailing_stop_loss:14 / trailing_stop_gap:1` |

阻断的是"绩效结论"，不是把成交记录删掉 —— 审计仍可复算。

### 5.5 ARM A 被拒平仓明细（16 笔持仓 / 20 条记录）

全量见 `out/04_reject_detail.txt`。摘录：

| scheme | symbol | pos | stop_used | fill（取整后） | bar_low | bar_high | 超出 high |
|---|---|---|---|---|---|---|---|
| 4a | GTCUSDT | 7 | 0.07561 | 0.07557 | 0.0706 | 0.072 | +4.96% |
| 4a | HYPEUSDT | 52 | 71.013 | 70.977 | 59.156 | 59.417 | **+19.45%** |
| 4a | VTHOUSDT | 53 | 0.0003515 | 0.0003513 | 0.0003095 | 0.0003097 | +13.43% |
| 4a | ZORAUSDT | 54 | 0.006323 | 0.006319 | 0.004862 | 0.004984 | **+26.79%** |
| 4b | GTCUSDT | 6 | 0.07561 | 0.07557 | 0.0722 | 0.07277 | +3.85% |
| 4c | FLUXUSDT | 30 | 0.0405 | 0.04047 | 0.03896 | 0.03907 | +3.58% |

统计判据（脚本断言，非目测）：

- 20/20 条 `reason == EXEC_PRICE_OUT_OF_RANGE`
- 20/20 条 `gap == True`
- 20/20 条 `exit_class == trailing_stop_gap`
- 16/16 笔 `fill_price > bar_high`（严格大于）
- 16/16 笔 `fill_price > bar_high * 1.001`（连 0.1% 容差上限都过不去）
- 超出 high 最小 **3.583%**、最大 **26.786%**

⇒ **该根 K 线从未到过这个价格。** 旧实现照样按该价平仓并写进 `paper_realized` ⇒ 系统性偏乐观。

### 5.6 ARM B 的读法（只作对照，不是成绩）

- `run_status=OK`、`run_problems=0` ⇒ 门禁在合法区间下**不阻断任何正常流程**，
  证明 ARM A 的 `FAILED` 不是"门禁恒拒"。
- 4b/4c 出现 `account_drawdown_locked` **62 / 70** 笔 —— 换 `bar_open` 后这两个口径很早就把账户打到回撤锁定。
  这与本单改动无关（ARM A 同口径没有该锁定），是 `gap_model` 更换的连锁反应。
- 胜率 8.08% / 8.33% / 11.54%、全组净亏 ⇒ **这个策略在这个窗口/这组参数下不成立**，
  但该结论依赖未批准的 `gap_model` 与未裁决的经济口径，**不得作为策略成绩引用**。

---

## 六、DEC-01：必须由用户裁决（阻塞正式成绩）

### 6.1 问题

`exit.gap_model` 决定"开盘跳空穿越止损时按什么价成交"。三个候选：

| 值 | 语义 | 问题 |
|---|---|---|
| `stop_price` | 直接按止损价成交 | **该 bar 可能从未到过这个价**（本单实测 16 笔，最少高出 3.58%）⇒ 系统性偏乐观。**这是现行默认值，未获批准** |
| `bar_open` | 按跳空后第一根 bar 的开盘价成交 | 物理上可成交，但跳空越大亏得越多（更保守）。**未获批准** |
| `midpoint` | 按 `(bar_open + stop)/2` 成交 | 介于两者之间。**未获批准** |

### 6.2 本单的处理（按工单约束 2）

工单明确"保持甲乙丙策略参数及 approved=false 状态不变" ⇒ **我不改默认值**，
只把越界成交显式拒单、把 run 标为 `FAILED`。

### 6.3 需要用户回答的问题

> **DEC-01**：`exit.gap_model` 取哪一个？取定后需重新出正式成绩。
>
> 附我的判断（仅供参考，不代替裁决）：`bar_open` 是三个里唯一"该价格一定在 bar 上存在"的模型；
> `stop_price` 已被本单证伪（16/16 笔成交价不在 bar 内）；`midpoint` 同样可能不在 bar 内
> （若跳空幅度小于半个 bar 振幅，`(open+stop)/2` 可能落在 `[low, high]` 外）。

---

## 七、剩余风险与未验证项

1. **`test_paper_live_01::test_03` 仍未通过**。已证为基线既有失败，但**它测的正是"隔离开仓→退出闭环"**，
   现在因为 `gap_model=stop_price` 被门禁拦掉。⇒ **DEC-01 裁决后必须重跑该测试**，
   若届时仍失败，则不能再用"基线失败"解释。
2. **门禁覆盖 5 条路径**：`plan_entry` / `open_position` / `close_at_price`（撮合侧）+
   `replay_one` 入场 / `replay_one` 退出（单事件回放侧）。
   若**另有代码直接 `INSERT INTO paper_realized`**（绕过 `close_at_price`），门禁管不到。
   本单用 `grep` 核过 `框架/backend` 下无此路径，但**未做全仓静态证明**（未上 AST/数据流分析）。
3. **`scan_exit` 在"开盘没穿止损 + 缺 `low`"时仍无法判定盘中是否触及**。
   这是数据缺失导致的**不可判定**，不是漏检：本单把它计数上报为 `n_bars_no_low`，
   并在 `no_exit_in_window` 的 note 里写明"盘中触及可能被漏判"。
   **没有把它伪造成"未触及"，也没有伪造成"已触及"** —— 但这条路径的结论确实不完整，
   复核时应看 `n_bars_no_low` 是否为 0。
   （注意：本单两臂回测走的是 `paper_engine` 撮合链，`_worker.py` 里 `replay_calc` 出现 0 次
   ⇒ `scan_exit` **未参与**两臂重跑，该字段在两臂产物中不存在，不能拿来当"两臂为 0"的证据。）
4. **`_scan_position_exit` 的元组式返回值**：本单用"末尾追加"降低风险，
   但这是既有设计的脆弱点，未来若有人插在中间会静默错位。**未重构**（工单约束 1）。
5. **`run_status.json` 只在 `run_abc_pool.py` 落盘**。若用其他入口跑回测，
   `compute_metrics` 读不到文件 ⇒ 不阻断。这是"兼容历史 run"的代价，**是已知缺口，不是疏漏**。
6. **ARM B 的成绩不可引用**；两臂都建立在**未裁决的经济口径**上
   （滑点 3bps/5bps、本金 2000/5000、参与率 0.1%/1.0% 两套未择一）。
7. **本单未触及**：`06-W1-R1` 的 21 项待决参数、资金费缺口（`2026-09-02T20:00Z→09-18T16:00Z`，380h）、
   `funding_events` 仍只读热库。这些与 BLK-01/BLK-02 无关，不在本单范围。

---

## 八、产物清单与复现命令

```
evidence/AUDIT-003-PATCH/
├── README.md                                   ← 本文件
├── config/
│   └── fixture_rules_06w2_gap_bar_open.json    ← ARM B 用（仅 gap_model 不同 + _patch_note）
├── scripts/
│   └── test_patch_price_gate_and_run_status.py ← 68 项回归测试
├── out/                                        ← ARM A（stop_price，现行默认）
│   ├── 00_symbols.txt                          ← 审计原运行的 40 个 symbol
│   ├── 01_code_diff.patch                      ← 代码 diff（git apply --check --reverse 通过）
│   ├── 02_test_output.txt                      ← 68/68 通过原始输出
│   ├── 03_regression_matrix.txt                ← 6 组回归矩阵 + 基线失败判定记录
│   ├── 04_reject_detail.txt                    ← ARM A 16 笔被拒平仓明细
│   ├── 05_两臂对账.md                          ← 两臂对账（本 README 第五节的数据源）
│   ├── 回测_甲乙丙对比.{json,csv,md}
│   └── 运行身份_a3patch_stop40.json
├── out_baropen/                                ← ARM B（bar_open，敏感性对照）
└── runs/
    ├── a3patch_40sym/        ← smoke
    ├── a3patch_stop40/       ← ARM A 账本 + run_status.json（status=FAILED）
    └── a3patch_baropen40/    ← ARM B 账本 + run_status.json（status=OK）
```

### 版本库口径（重要）

`*.sqlite3` 被根 `.gitignore:38` 忽略 ⇒ **`runs/*/paper/*.sqlite3` 与 `observe.sqlite3` 不进版本库**
（与 FIX-001 的处理一致）。纳入版本库的是 37 个文件、约 1.0 MB：
`README.md` / `config/` / `scripts/` / `out*/` 全部产物 / `runs/*/{job.json,gate.json,run_status.json,raw.json,ledger_identity.json,_worker.py}`。

⇒ 复核者若要看账本级证据，需按第八节命令**本地重跑**（产物可逐位复现，因为 `job.json` 固定了
config / symbols / 窗口 / max_boxes / walk_days）。`raw.json` 保留了每个持仓的完整明细
（含 `detail.close_result.bar_low/bar_high`），**第五节 5.5 的被拒明细全部可从 `raw.json` 复算**，不依赖 sqlite。

### 复现命令

```bash
cd <项目根>

# 1) 本单回归测试（必须用 /usr/bin/python3，见 4.3）
/usr/bin/python3 evidence/AUDIT-003-PATCH/scripts/test_patch_price_gate_and_run_status.py

# 2) FIX-001 共线性（不得回归）
/usr/bin/python3 evidence/FIX-001/scripts/test_fix001_timeline.py --integration

# 3) 后端单测（用带 pytest 的解释器）
PY=~/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY -m pytest 框架/backend/tests/test_accum_early_entry.py 框架/backend/tests/test_business.py -q

# 4) 两臂重跑
#    ARM A = 现行默认 config（fixture_rules_06w2.json，gap_model=stop_price）
#    ARM B = 只把 gap_model 换成 bar_open 的变体
SYMS="$(cat evidence/AUDIT-003-PATCH/out/00_symbols.txt)"     # 单行逗号分隔的 40 个 symbol
PY2=/usr/bin/python3

$PY2 evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py \
     --allow-fixture --symbols "$SYMS" \
     --out-dir  evidence/AUDIT-003-PATCH/out \
     --runs-dir evidence/AUDIT-003-PATCH/runs \
     --run-id a3patch_stop40

$PY2 evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py \
     --allow-fixture --symbols "$SYMS" \
     --config evidence/AUDIT-003-PATCH/config/fixture_rules_06w2_gap_bar_open.json \
     --out-dir  evidence/AUDIT-003-PATCH/out_baropen \
     --runs-dir evidence/AUDIT-003-PATCH/runs \
     --run-id a3patch_baropen40
```

窗口与池子（两臂相同，取自 `out/回测_甲乙丙对比.json → identity`）：
`start=2026-06-27T08:00:00Z`、`end=2026-10-26T09:00:00Z`（121.042 天）、
`pool_source=explicit_symbols`、`n=40`、`max_boxes=40`、`walk_days=30`。
两臂 config 均 `kind=fixture`、`approved=false`。

---

## 九、代码改动统计

`out/01_code_diff.patch`：**1058 行 diff，8 个文件，+504 / −48**

| 文件 | 增/删 | 内容 |
|---|---|---|
| `框架/backend/replay_calc.py` | +130 / −18 | `require_bar_range()` / `check_price_in_bar()`、`plan_entry` 必填化、`replay_one` 补参 + 退出侧 fail-closed、`scan_exit` 停止伪造 high + `n_bars_no_low` 计数、`gap_sensitivity` 带 `price_valid`、`__all__` |
| `框架/backend/accum_early_entry.py` | +123 / −13 | 运行状态机、`drive_position_exit` 拒单不当退出、3 处异常标记、`research_run` 透传 |
| `evidence/ACCUM-EARLY-07-W5/scripts/run_abc_pool.py` | +74 / −5 | `_block_metrics()`、`ledger_metrics(run_invalid=)`、`run_status.json` 落盘、`--out-dir/--runs-dir` |
| `框架/backend/paper_engine.py` | +72 / −3 | `open_position` 门禁、`close_at_price` 必填+前置门禁+`fill` 校验、3 处内部调用补参 |
| `框架/backend/backtest_metrics.py` | +71 / −0 | `load_run_status()`、`compute_metrics()` 入口阻断 |
| `框架/backend/live_worker.py` | +21 / −9 | `_scan_position_exit` 6 个 return 补 `lo/hi`、调用处解包传参 |
| `策略/WyckoffMaster/event_trend_v0_1/tests/test_fix002_precision_and_gates.py` | +7 / −0 | 两个用例补 `bar_low/bar_high`（断言未改） |
| `策略/WyckoffMaster/trading_core/rejects.py` | +6 / −0 | `REJECT_MISSING_BAR_RANGE` + 登记有效性集合 |

**未改动**（工单约束 1/2 的验证点）：

- `scan_symbol` / 箱体构建 / 状态机 / 候选判定 / 失效判定 —— 零改动（候选数 105/87/97 两臂一致即佐证）
- `_timeline_events` / `_advance_open_positions` / `_replay_timeline` 的**推进与释放槽位逻辑** —— 只加 `run_state` 形参和异常标记，排序/三步顺序零改动
- 甲乙丙三套策略参数、`approved=false` 状态、`fixture_rules_06w2.json` —— 零改动
- `EventTrendConfig` 冻结参数 —— 零改动

---

## 十、判定

| 项 | 判定 |
|---|---|
| BLK-02（P0）价格区间校验接入真实撮合 | **通过**（T1–T7、T12、T13 共 41 项断言 + 7 个调用点逐一核对） |
| BLK-01（P1）退出异常阻断绩效汇总 | **通过**（T8–T11 共 27 项断言，两处绩效入口 + 三处负向对照） |
| 统一时间轴与仓位释放逻辑未被破坏 | **通过**（FIX-001 集成 17/17） |
| 甲乙丙参数与 `approved=false` 未变 | **通过**（候选数两臂一致、config 逐字节核对） |
| 甲乙丙成绩可引用 | **不通过**（`DEC-01` 未裁决；ARM A 已被判无效） |
| 整体 | **补修通过，成绩待裁决** |
