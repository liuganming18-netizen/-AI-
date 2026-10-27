# -*- coding: utf-8 -*-
"""研究回测 HTTP 适配（`/api/v1/backtests` 扩展）。

契约（`bt_api_v1`，稳定）
────────────────────────
`POST /api/v1/backtests`                 创建研究回测 → `{run_id, status:"queued"}` 202
`GET  /api/v1/backtests`                 实验列表（**不带 mode**；带 mode 走旧通道）
`GET  /api/v1/backtests/{id}`            状态 + 配置（**不触发计算**）
`GET  /api/v1/backtests/{id}/metrics`    指标（从账本算）
`GET  /api/v1/backtests/{id}/equity`     权益曲线
`GET  /api/v1/backtests/{id}/trades`     交易列表
`GET  /api/v1/backtests/{id}/trades/{tid}` 单笔（同一 trade_id 联结图表与表格）
`GET  /api/v1/backtests/{id}/orders`     **全局**订单/成交明细
`GET  /api/v1/backtests/{id}/rejected`   **拒单明细**
`GET  /api/v1/backtests/{id}/chart`      图表载荷（真实 K 线/信号/开平仓/保护止损轨迹）
`GET  /api/v1/backtests/{id}/quality`    数据质量与对账

纪律
────
· **GET 一律不触发计算**（只读 `run.json` / `ledger.sqlite3`）。
· 仅 `WX_OI_RESONANCE_V1`；参数白名单；周期/交易所/市场白名单（当前仅 1h 信号 + 15m 执行）。
· 创建走**原子独占目录**，重名拒绝；任务在**后台线程**执行，状态持久化。
· **运行列表只含当前策略**；非当前策略 / 身份缺失的 run 排除并单列 `excluded_legacy`。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import backtest_runner as RUN
import backtest_store as S
import backtest_chart as CH
import event_trend_bridge as BR
import ledger_links as LL
import backtest_metrics as MET
import backtest_batches as BATCH

CONTRACT_VERSION = "bt_api_v1"

# ── 任务队列开关（架构重构第 2 期）────────────────────────────────────────────
# 默认：回测投递到持久任务表，由 `workers/backtest_worker.py` 在独立进程执行。
# 回滚：置 `INPROCESS_JOBS=1`，恢复「HTTP 进程内起 daemon 线程」的旧行为。
JOBS_ENABLED = os.environ.get("INPROCESS_JOBS", "0") != "1"
_JOBS = None
_JOBS_LOCK = threading.Lock()


def _jobs():
    """惰性构造任务库连接；失败时返回 None（调用方回退到线程模式并记原因）。"""
    global _JOBS
    if _JOBS is None:
        with _JOBS_LOCK:
            if _JOBS is None:
                from infrastructure.jobs.store import JobsStore
                _JOBS = JobsStore()
    return _JOBS


_THREADS: Dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()


def _facts(run_id: str) -> LL.LedgerFacts:
    """本 run 的账本事实（真实标识关联；一次读盘多处复用）。

    这些 run 的账本落成了 **JSONL**（无 `ledger.sqlite3`），且 `trades.jsonl` /
    `orders.jsonl` 本身不带 `trade_id` —— 因此**必须**走本模块按真实标识关联，
    绝不能让 `read_ledger_rows` 的 `f"trd_{len(rows)+1}"` 兜底生效（那是按行号猜配）。
    """
    return LL.load_ledger_facts(S.read_run_dir(run_id), run_id)



def _envelope(**kw) -> dict:
    base = {
        "mode": "research_backtest", "contract_version": CONTRACT_VERSION,
        "strategy_id": BR.STRATEGY_ID, "run_id": None, "status": None,
        # 默认**不得**报 ok：未读取到真实质量时一律 unknown（不冒充成功）。
        "quality": {"status": "unknown", "age_seconds": None, "note": None},
        "missing_reason": None, "items": [], "total": None,
        "live_order_enabled": False,
    }
    base.update(kw)
    return base


# ══════════════════════════ 读取 ══════════════════════════

def list_experiments(params: Optional[dict] = None) -> dict:
    params = params or {}
    try:
        limit = int((params.get("limit") or ["500"])[0])
    except (ValueError, TypeError):
        limit = 500
    runs = S.list_runs(limit=max(1, min(limit, 1000)))
    # ── WORK-04 新旧归属：运行列表**只含新策略** `WX_OI_RESONANCE_V1` ─────────────
    # 运行根（`回测日志/oi_resonance_v1/runs`）本应只含新策略 run；此处仍按 strategy_id
    # 显式核对，把非新策略 / 身份缺失的 run **排除出当前列表**并单独登记（不冒充新结果）。
    current: List[dict] = []
    excluded_legacy: List[dict] = []
    for r in runs:
        rid = str(r.get("run_id") or "")
        # 内部消融变体仅用于对账，不作为主回测记录干扰前端列表
        if re.search(r"_(A|B|C)_[0-9]+$", rid):
            continue
        sid = (r.get("spec") or {}).get("strategy_id") or r.get("strategy_id")
        if sid == BR.STRATEGY_ID:
            current.append(r)
        else:
            excluded_legacy.append({
                "run_id": r.get("run_id"), "status": r.get("status"),
                "strategy_id": sid, "created_at": r.get("created_at"),
                "attribution": "legacy_excluded",
                "note": "非当前策略（或身份缺失）⇒ 排除出当前运行列表；旧结果不得当成新策略成绩",
            })
    items = [_summary(r) for r in current]
    return _envelope(items=items, total=len(items),
                     runs_root=str(S.RUNS_ROOT),
                     registry_entry=_registry_entry(),
                     retirement=_retirement_block(),
                     excluded_legacy=excluded_legacy,
                     excluded_legacy_total=len(excluded_legacy),
                     note=f"研究回测实验列表（**仅当前策略** {BR.STRATEGY_ID}；只读；"
                          "创建走 POST /api/v1/backtests）。旧策略 run 已排除并单列 excluded_legacy。")


def _retirement_block() -> dict:
    """旧策略退役汇总（懒加载；失败如实报缺）。"""
    try:
        from strategies import registry as _reg
        return _reg.retirement_summary(None)
    except Exception as exc:                              # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _registry_entry() -> dict:
    """注册表「单一当前回测策略」元数据（懒加载、失败如实报缺）。"""
    try:
        from strategies import registry as _reg
        return _reg.describe_current_backtest(None)
    except Exception as exc:                              # noqa: BLE001
        return {"strategy_id": BR.STRATEGY_ID, "available": False,
                "import_error": f"{type(exc).__name__}: {exc}"}


def _summary(doc: dict) -> dict:
    m = doc.get("metrics") or {}
    sid = (doc.get("spec") or {}).get("strategy_id") or doc.get("strategy_id")
    return {
        "run_id": doc.get("run_id"), "status": doc.get("status"),
        "created_at": doc.get("created_at"), "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "strategy_id": sid,
        "strategy_key": BR.STRATEGY_KEY,
        "attribution": ("current_strategy" if sid == BR.STRATEGY_ID else "unknown"),
        "symbols": (doc.get("spec") or {}).get("symbols"),
        "window": doc.get("window"),
        "net_return_pct": m.get("net_return_pct"),
        "trades_count": m.get("trades_count"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "failure_reason": doc.get("failure_reason"),
    }


def get_run(run_id: str) -> Tuple[dict, int]:
    """状态 + 配置（**只读，不触发计算**）。"""
    try:
        doc = S.read_run(run_id)
    except S.RunNotFound:
        return _envelope(missing_reason=f"未知 run_id：{run_id}"), 404
    try:
        counts = S.ledger_counts(run_id) if S.ledger_path(run_id).exists() else {}
    except Exception as exc:                              # noqa: BLE001
        counts = {"error": f"{type(exc).__name__}: {exc}"}
    return _envelope(
        run_id=run_id, status=doc.get("status"),
        quality=_quality_of(doc),
        config={"spec": doc.get("spec"), "config": doc.get("config"),
                "actual_config": (doc.get("data_quality") or {}).get("actual_config")},
        hashes=doc.get("hashes"), window=doc.get("window"),
        engine=doc.get("engine"), ledger_counts=counts,
        chart_file=doc.get("chart_file"), snapshot_files=doc.get("snapshot_files"),
        created_at=doc.get("created_at"), started_at=doc.get("started_at"),
        finished_at=doc.get("finished_at"), failure_reason=doc.get("failure_reason"),
        note="GET 只读，不触发任何计算"), 200


def _quality_of(doc: dict) -> dict:
    """运行状态 → 质量块。**未成功/缺质量一律不报 ok**（失败/中断/进行中如实反映）。"""
    st = doc.get("status")
    dq = doc.get("data_quality") or {}
    if st == S.STATUS_SUCCEEDED:
        return {"status": dq.get("status") or "unknown", "age_seconds": None,
                "note": dq.get("reasons") or None}
    if st in (S.STATUS_QUEUED, S.STATUS_RUNNING):
        return {"status": "pending", "age_seconds": None,
                "note": "运行尚未完成；GET 只读、不触发计算"}
    return {"status": "unavailable", "age_seconds": None,
            "note": f"运行未成功（{st}）：{doc.get('failure_reason')}"}


def _require_done(run_id: str) -> Tuple[Optional[dict], Optional[Tuple[dict, int]]]:
    try:
        doc = S.read_run(run_id)
    except S.RunNotFound:
        return None, (_envelope(missing_reason=f"未知 run_id：{run_id}",
                                quality={"status": "unavailable", "age_seconds": None,
                                         "note": "未知 run_id"}), 404)
    if doc.get("status") not in (S.STATUS_SUCCEEDED,):
        return None, (_envelope(run_id=run_id, status=doc.get("status"),
                                quality=_quality_of(doc),
                                missing_reason=(
                                    "结果尚未就绪（GET 不触发计算）"
                                    if doc.get("status") in (S.STATUS_QUEUED, S.STATUS_RUNNING)
                                    else f"运行未成功：{doc.get('failure_reason')}"),
                                failure_reason=doc.get("failure_reason")), 200)
    return doc, None


def get_metrics(run_id: str) -> Tuple[dict, int]:
    doc, err = _require_done(run_id)
    if err:
        return err
    # 指标**由本 run 账本现算**（不是读 summary.json 里可能不存在的字段）：
    #   · 胜负 = 扣费后 net_pnl（>0 盈 / <0 亏 / =0 平）
    #   · 最大回撤 = equity_curve.total_equity 逐点峰值回撤（含未实现浮盈浮亏）
    #   · profit_factor（聚合口径）与 avg_win_loss_ratio（均值口径）**分开、不混用**
    metrics, notes = MET.compute_metrics(S.read_run_dir(run_id), run_id, doc)
    stored = doc.get("metrics") or {}
    bench: Optional[dict] = None
    monthly: Optional[dict] = None
    try:
        chart = _backfill_chart(run_id, S.read_chart(run_id))
        bench = MET.compute_benchmark(chart)
        if bench is None:
            notes.append("chart.json 无基准序列 ⇒ benchmark_buy_hold 返回缺失（不伪造）")
    except S.RunNotFound:
        notes.append("chart.json 缺失 ⇒ benchmark_buy_hold 返回缺失（不伪造）")
    try:
        monthly = MET.compute_monthly_returns(
            S.read_ledger_rows(run_id, "equity", order_by="ts_ms ASC"),
            metrics.get("initial_equity"))
        if monthly is None:
            notes.append("权益曲线不足 2 点 ⇒ monthly_returns 返回缺失（不伪造）")
    except S.RunNotFound:
        notes.append("equity 表不可读 ⇒ monthly_returns 返回缺失（不伪造）")
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     metrics=metrics,
                     metrics_from_ledger=True,
                     metrics_stored_summary=stored,
                     metrics_notes=notes,
                     benchmark_buy_hold=bench,
                     monthly_returns=monthly,
                     formal_m4_gate=doc.get("formal_m4_gate"),
                     m4_disclaimer=doc.get("m4_disclaimer"),
                     note=("metrics 由本 run 账本现算（净值口径）；metrics_stored_summary 为"
                           "落盘 summary 原值，仅供对照，不作为展示来源")), 200


def get_quality(run_id: str) -> Tuple[dict, int]:
    try:
        doc = S.read_run(run_id)
    except S.RunNotFound:
        return _envelope(missing_reason=f"未知 run_id：{run_id}",
                         quality={"status": "unavailable", "age_seconds": None,
                                  "note": "未知 run_id"}), 404
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     data_quality=doc.get("data_quality"),
                     hashes=doc.get("hashes"), window=doc.get("window"),
                     ledger_counts=(doc.get("data_quality") or {}).get("ledger_counts")), 200


def get_equity(run_id: str) -> Tuple[dict, int]:
    doc, err = _require_done(run_id)
    if err:
        return err
    rows = S.read_ledger_rows(run_id, "equity", order_by="ts_ms ASC")
    bench_series: List[dict] = []
    bench_meta: Optional[dict] = None
    try:
        chart = S.read_chart(run_id)
        chart = _backfill_chart(run_id, chart)   # 修掉批量脚本写死的 benchmark.return_pct = 0.0
        bench_series = (chart.get("benchmark") or {}).get("equity_series") or []
        # 基准**成本口径如实标注**：批量脚本落盘的基准序列是 `initial_equity × close_i / open_0`，
        # 即**纯价格**口径（不含手续费/滑点/资金费）⇒ 只给 price_only_return_pct，
        # net_return_pct 留空，**不把纯价格收益冒充「净收益」**与策略净收益并列比较。
        bench_meta = MET.compute_benchmark(chart)
        if bench_meta is None:
            bench_meta = {"note": "本 run 的 chart.json 未含基准序列 ⇒ 无基准（不伪造）"}
    except S.RunNotFound:
        bench_series, bench_meta = [], {"note": "chart.json 缺失 ⇒ 无基准序列（不伪造）"}
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     benchmark=bench_meta, benchmark_equity=bench_series,
                     note=("策略权益曲线取自账本 equity 表（含未实现浮盈浮亏）；"
                           "benchmark_equity 为本 run 落盘的买入持有基准逐点净值，"
                           "其成本口径见 benchmark.cost_model（纯价格口径时不给 net_return_pct）")), 200


@lru_cache(maxsize=64)
def _signal_index(run_id: str) -> Dict[str, int]:
    """`chart.json` 的 signals：signal_id → 信号时刻(ms)。

    只读、带缓存（图载荷 1MB 级，逐笔接口不该每次重解）。文件缺失/解析失败 ⇒ `{}`
    （调用方据此退到 signal_id 尾段并标注来源，不编造时刻）。
    """
    try:
        d = S.read_run_dir(run_id)
        p = d / "chart.json"
        if not p.is_file():
            return {}
        with p.open("r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:                                     # noqa: BLE001
        return {}
    out: Dict[str, int] = {}
    for s in (doc or {}).get("signals") or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("signal_id")
        t = s.get("t")
        if sid and isinstance(t, (int, float)) and t > 0:
            out[str(sid)] = int(t)
    return out


def get_trades(run_id: str) -> Tuple[dict, int]:
    doc, err = _require_done(run_id)
    if err:
        return err
    facts = _facts(run_id)
    rows = facts.trade_rows
    unresolved = [r for r in rows if not r.get("trade_id")]
    notes = list(facts.notes)
    if unresolved:
        notes.append(
            f"{len(unresolved)} 笔交易的 trade_id 在账本里无法唯一确定 ⇒ 如实留空（不按行号猜配）")

    # ── 逐笔复核所需的两项派生字段（2026-10-18 BT-UI-01）────────────────────
    # ① initial_stop：trades.jsonl 的 stop_trace 首点（引擎实况记录），不是重算值。
    # ② signal_time_ms：候选信号时刻。优先按 signal_id 命中 chart.signals[].t；
    #    未命中才用 signal_id 尾段（结构化字段）并标注来源 —— 两者都缺就 None。
    sig_index = _signal_index(run_id)
    missing_stop = 0
    missing_sig_t = 0
    for r in rows:
        st = r.get("stop_trace") or []
        init = None
        for p in st:
            if isinstance(p, dict) and isinstance(p.get("stop"), (int, float)):
                init = float(p["stop"])
                break
        r["initial_stop"] = init
        r["initial_stop_source"] = ("trades.jsonl.stop_trace[0]" if init is not None else None)
        if init is None:
            missing_stop += 1

        sid = r.get("signal_id")
        t_ms = sig_index.get(str(sid)) if (sig_index and sid) else None
        src = "chart.signals[].t（按 signal_id 命中）" if t_ms is not None else None
        if t_ms is None and sid:
            tail = str(sid).rsplit("_", 1)
            if len(tail) == 2:
                try:
                    v = int(tail[1])
                except ValueError:
                    v = 0
                if 1_000_000_000_000 <= v <= 10_000_000_000_000:
                    t_ms = v
                    src = "signal_id 尾段（chart.signals 未命中/无图载荷）"
        r["signal_time_ms"] = t_ms
        r["signal_time_source"] = src
        if t_ms is None:
            missing_sig_t += 1
    if missing_stop:
        notes.append(f"{missing_stop} 笔没有 stop_trace ⇒ 初始止损留空（不按价格推算）")
    if missing_sig_t:
        notes.append(f"{missing_sig_t} 笔未能定位候选信号时刻 ⇒ signal_time_ms 留空")

    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     trade_id_source="ledger:positions.jsonl POSITION_CLOSED.payload.trade_id",
                     unresolved_trade_ids=len(unresolved),
                     missing_initial_stop=missing_stop,
                     missing_signal_time=missing_sig_t,
                     link_notes=notes,
                     note=("trade_id / 关联订单 / 资金费归属全部取自本 run 账本的真实标识；"
                           "stop_trace 已统一为 `t`（毫秒），无后续止损调整时即为单点；"
                           "initial_stop 取 stop_trace 首点；signal_time_ms 优先来自 chart.signals")), 200


def get_orders(run_id: str) -> Tuple[dict, int]:
    """**全局**订单/成交明细（同一 `run_id` 账本 `orders` 表；与逐笔同源）。

    纪律：只读账本，缺失表/账本 ⇒ 空列表（不编造）。金额/数量原样来自账本。
    `trade_id` 用**真实 `order_id` 反查**归属（`orders.jsonl` 本身没有该字段），
    关联不上 ⇒ `trade_id=None` + `trade_link="unlinked"`，**绝不按顺序套用第 N 笔**。
    """
    doc, err = _require_done(run_id)
    if err:
        return err
    try:
        facts = _facts(run_id)
    except S.RunNotFound:
        return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                         items=[], total=0, missing_reason="账本缺失（orders 表不可读）"), 200
    rows = facts.all_orders()
    unlinked = sum(1 for r in rows if not r.get("trade_id"))
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     unlinked_orders=unlinked,
                     link_notes=list(facts.notes),
                     note=("全局订单/成交明细（同一 run_id 账本 orders 表）；"
                           "trade_id 由真实 order_id 反查，关联不上则如实留空")), 200


def get_rejected(run_id: str) -> Tuple[dict, int]:
    """**拒单明细**（同一 `run_id` 账本 `rejected` 表：时点 / 标的 / 相位 / 原因）。

    纪律：只读账本；缺表 ⇒ 空列表并标注 `missing_reason`，不把「无记录」包装成「无拒单」。
    """
    doc, err = _require_done(run_id)
    if err:
        return err
    try:
        rows = S.read_ledger_rows(run_id, "rejected", order_by="ts_ms ASC")
    except S.RunNotFound:
        return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                         items=[], total=0, missing_reason="账本缺失（rejected 表不可读）"), 200
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     note="拒单明细（账本 rejected 表：风险/仓位/资金/信号门禁等拒绝原因）"), 200


def get_trade(run_id: str, trade_id: str) -> Tuple[dict, int]:
    """单笔明细：**同一真实 `trade_id`** 联结逐笔 / 开平仓订单 / 资金费 / 原始候选信号。

    关联只用真实标识：订单走 `trades.order_id` / `exit_order_id` 直连 `orders.jsonl`；
    资金费走时间窗**唯一**归属；候选信号走 `trades.signal_id == candidates.candidate_id`。
    任一处不唯一 ⇒ 该处为空并附 `missing_reason`，**不按行号/顺序补**。
    """
    doc, err = _require_done(run_id)
    if err:
        return err
    facts = _facts(run_id)
    t = facts.trade_by_id(trade_id)
    if not t:
        known = [r.get("trade_id") for r in facts.trade_rows if r.get("trade_id")]
        return _envelope(
            run_id=run_id,
            missing_reason=(f"未知 trade_id：{trade_id}"
                            + ("" if known else "；本 run 账本里没有可唯一确定的 trade_id")),
            known_trade_ids=known), 404
    orders = facts.orders_of(trade_id)
    funding = facts.funding_of(trade_id)
    sig_id = t.get("signal_id")
    sig = facts.signal_by_id.get(str(sig_id)) if sig_id else None
    cross = facts.funding_cross_check(trade_id)
    missing: List[str] = []
    if t.get("linked_order_count") != 2:
        missing.append("开仓/平仓订单未能各关联到 1 条 ⇒ 订单明细不完整（不补）")
    if not sig:
        missing.append("原始候选信号未在 candidates.jsonl 中按 signal_id 命中 ⇒ 返回缺失")
    if funding and not cross.get("match"):
        missing.append("资金费事件归属与账本 funding_fee 交叉校验不一致 ⇒ 见 funding_cross_check")
    if not funding:
        missing.append("本笔无归属资金费事件（账本中 0 条，或时间窗归属不唯一）")
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     trade=t, orders=orders, funding=funding,
                     original_signal=sig,
                     signal_id=sig_id,
                     funding_cross_check=cross,
                     linked_order_count=t.get("linked_order_count"),
                     trade_id_source=t.get("trade_id_source"),
                     missing=list(missing),
                     link_notes=list(facts.notes),
                     items=[t], total=1), 200


def _run_own_window(run_id: str, d) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """该 run **自己的**窗口（毫秒），以及来源说明。

    为什么需要（BT-UI-01）：旧 `_generate_fallback_chart` 把窗口写死成
    `2024-01-01 ~ 2024-06-30`。实测 4 个缺 `chart.json` 的 run 真实窗口分别是
    2024-09→2025-09、2021-06→2022-06、2022-10→2023-10、2023-01→2024-01 ——
    写死的窗口与真实窗口**完全不相交**，图上那根 K 线根本不是这笔交易发生的那一段，
    用户看到的就是「图表和逐笔对不上」。

    顺序：`inputs.json(data_summary.time_range_1h)` → 账本 `equity_curve.jsonl` 首尾时刻。
    两处都取不到 ⇒ 返回 `(None, None, None)`，调用方**不画图**（宁缺不造）。
    """
    try:
        inp = json.loads((d / "inputs.json").read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        inp = {}
    tr = ((inp.get("data_summary") or {}).get("time_range_1h") or {}) if isinstance(inp, dict) else {}
    ws, we = tr.get("start_ms"), tr.get("end_ms")
    if isinstance(ws, int) and isinstance(we, int) and ws > 0 and we > ws:
        return ws, we, "inputs.json(data_summary.time_range_1h)"
    ts: List[int] = []
    try:
        with (d / "equity_curve.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                t = e.get("timestamp") or e.get("t") or e.get("ts_ms")
                if isinstance(t, int) and t > 0:
                    ts.append(t)
    except OSError:
        ts = []
    if ts:
        return min(ts), max(ts), "equity_curve.jsonl(首/末时刻)"
    return None, None, None


def _generate_fallback_chart(run_id: str, doc: dict) -> dict:
    """`chart.json` 缺失时，用**该 run 自己的**标的与窗口重建图表载荷。

    纪律（BT-UI-01）：
      · 窗口 / 标的只能来自本 run 的落盘文件（`inputs.json` / `summary.json`）；
        取不到就抛 `RunNotFound`，由 `get_chart` 如实报「缺 K 线」，**不猜、不写死**；
      · **不落盘**：读接口不得改写 run 目录（旧实现会 `S.write_chart` 把重建结果写回去，
        等于用一份错窗口的图覆盖证据目录）。
    """
    from WyckoffMaster.event_trend_v0_1 import EventTrendRunner
    d = S.read_run_dir(run_id)

    # 标的一律取 summary.json（该 run 真实跑过的标的），不拿 spec 默认值顶替
    symbol = None
    try:
        sm = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        symbol = sm.get("symbol")
    except Exception:                                      # noqa: BLE001
        symbol = None
    symbol = symbol or ((doc.get("spec") or {}).get("symbols") or [None])[0]
    if not symbol:
        raise S.RunNotFound(f"{run_id}: 无法确定标的（summary.json 无 symbol）")

    ws, we, win_src = _run_own_window(run_id, d)
    if ws is None or we is None:
        raise S.RunNotFound(f"{run_id}: 无法确定本 run 的真实窗口 ⇒ 不重建图表")

    start_date = datetime.fromtimestamp(ws / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(we / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    runner = EventTrendRunner()
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events = runner.load_symbol_data(
        symbol=symbol, start_date=start_date, end_date=end_date
    )
    if len(df_15m) == 0:
        raise S.RunNotFound(f"{run_id}: 本 run 窗口 {start_date}~{end_date} 无 K 线数据 ⇒ 不重建")
    candles = [
        {"t": int(r.open_time), "o": float(r.open), "h": float(r.high),
         "l": float(r.low), "c": float(r.close), "v": float(r.volume)}
        for r in df_15m.itertuples(index=False)
    ]
    # 真实 trade_id 一律取账本（positions.jsonl 的 POSITION_CLOSED）——**不按行号猜配**。
    try:
        _f = _facts(run_id)
    except Exception:                                      # noqa: BLE001
        _f = None
    chart_trades = []
    trades_file = d / "trades.jsonl"
    if trades_file.exists():
        for tid_idx, line in enumerate(trades_file.read_text(encoding="utf-8").splitlines()):
            if not line.strip(): continue
            try:
                t = json.loads(line)
                _et = int(t.get("entry_time") or t.get("entry_time_ms") or 0)
                _xt = int(t.get("exit_time") or t.get("exit_time_ms") or 0)
                _tid = None
                if _f is not None:
                    _row = next((r for r in _f.trade_rows
                                 if r.get("entry_time_ms") == _et and r.get("exit_time_ms") == _xt),
                                None)
                    _tid = (_row or {}).get("trade_id")
                chart_trades.append({
                    "trade_id": _tid,
                    "symbol": symbol,
                    "direction": "long",
                    "signal_type": 1,
                    "entry": {"t": _et,
                              "price": float(t.get("entry_price") or 0.0),
                              "qty": float(t.get("qty") or 0.0)},
                    "exit": {"t": _xt,
                             "price": float(t.get("exit_price") or 0.0),
                             "reason": str(t.get("exit_reason") or "")},
                    "net_pnl": float(t.get("net_pnl") or 0.0),
                    "initial_stop": float(t.get("initial_stop") or 0.0),
                    "stop_trace": [{"t": int(x.get("t", x.get("ts", 0))),
                                    "stop": float(x.get("stop", 0)),
                                    "reason": str(x.get("reason", ""))}
                                   for x in t.get("stop_trace", [])],
                })
            except Exception: pass

    chart_equity = []
    eq_file = d / "equity_curve.jsonl"
    init_eq = float((doc.get("spec") or {}).get("initial_equity") or 2000.0)
    if eq_file.exists():
        for line in eq_file.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try:
                e = json.loads(line)
                chart_equity.append({
                    "t": int(e.get("timestamp") or e.get("t") or 0),
                    "equity": float(e.get("total_equity") or e.get("equity") or init_eq),
                    "cash": float(e.get("cash_equity") or e.get("cash") or init_eq),
                    "unrealized": float(e.get("unrealized_pnl") or 0.0),
                })
            except Exception: pass

    open_0 = float(df_15m['open'].iloc[0]) if len(df_15m) else 1.0
    bench_series = [
        {"t": int(r.open_time), "equity": float(init_eq * (float(r.close) / open_0))}
        for r in df_15m.iloc[::4].itertuples(index=False)
    ]

    chart = {
        "symbol": symbol,
        "exec_timeframe": "15m",
        "window": {
            # 窗口取**该 run 自己**的（`_run_own_window`），不用 df_1h 的首尾冒充
            "start_ms": int(ws),
            "end_ms": int(we),
            "source": win_src,
        },
        "candles": candles,
        "signals": [],
        "trades": chart_trades,
        "equity_curve": chart_equity,
        "funding_events": [],
        "benchmark": {"symbol": symbol, "return_pct": 0.0, "equity_series": bench_series},
        # 如实标注：这份载荷是**读时重建**的（原 run 没落 chart.json），
        # 且窗口/标的取自本 run 自己的落盘文件；不写回 run 目录。
        "derived_from": (f"chart.json 缺失 ⇒ 读时重建：标的取 summary.json，窗口取 {win_src}；"
                         "K 线为该窗口真实行情，未写回 run 目录"),
    }
    return chart


def _read_jsonl(p) -> list:
    """逐行 JSON 读取；单行坏掉只跳过该行，不因一行毁掉整个载荷。"""
    import json as _j
    out = []
    try:
        if not p.exists():
            return out
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_j.loads(line))
                except Exception:                          # noqa: BLE001
                    continue
    except Exception:                                      # noqa: BLE001
        return []
    return out


def _candle_tuple(x):
    """K 线两种落盘格式归一：数组 `[t,o,h,l,c,v]` 或对象 `{t,o,h,l,c,v}`。"""
    if isinstance(x, (list, tuple)) and len(x) >= 5:
        return x
    if isinstance(x, dict):
        return [x.get("t"), x.get("o"), x.get("h"), x.get("l"), x.get("c"), x.get("v")]
    return None


def _backfill_chart(run_id: str, chart: dict) -> dict:
    """把 chart.json 里**被批量脚本写死为空**的字段，用本 run 自己的真实落盘文件补齐。

    背景（2026-10-17 查实）：项目根目录的 7 个 `run_batch_*.py` / `run_*_batch_test.py`
    自己拼 chart.json，把 `signals` 写成 `[]`、`funding_events` 写成 `[]`、
    `benchmark.return_pct` 写成 `0.0`、`trade_id` 直通 `null`（实测 344 个 run 无一例外）。
    而这些字段的真实数据其实都在**同一目录**的 `candidates.jsonl` / `funding.jsonl` /
    `trades.jsonl` / `equity_curve.jsonl` 里。

    本函数**只做补全，不做任何推算**：
    - 每个值都取自本 run 自己的落盘文件；
    - 取不到就保持原样（宁可空着，不造数）；
    - `candles` / `equity_curve` 一律不动；
    - 只覆盖「现状无效」的字段（空数组 / `t<=0` / `trade_id` 为空 / `return_pct` 为 0）。

    返回同一个 dict（就地补全）。
    """
    if not isinstance(chart, dict):
        return chart
    try:
        d = S.read_run_dir(run_id)
    except Exception:                                      # noqa: BLE001
        return chart

    # ① 信号（箱体突破候选）—— 源：candidates.jsonl
    cur_sig = chart.get("signals") or []
    valid_sig = [s for s in cur_sig
                 if isinstance(s, dict) and isinstance(s.get("t"), (int, float)) and s.get("t") > 0]
    if not valid_sig:
        sig = []
        for c in _read_jsonl(d / "candidates.jsonl"):
            t_ms = c.get("available_at_ms") or c.get("snapshot_time_ms") or c.get("execution_earliest_ms")
            try:
                t_ms = int(t_ms)
            except (TypeError, ValueError):
                continue
            if t_ms <= 0:
                continue
            atr = c.get("box_atr_a")
            oi_g = c.get("oi_growth_6h")
            sig.append({
                "t": t_ms,
                "kind": ("box_breakout" if c.get("breakout_close") is not None else "candidate"),
                "direction": "long",
                "signal_id": c.get("candidate_id"),
                "stop_price": (float(c["initial_stop_price"])
                               if c.get("initial_stop_price") is not None else None),
                "atr": float(atr) if atr is not None else None,
                "oi_score": float(oi_g) if oi_g is not None else None,
                "oi_available": oi_g is not None,
                "oi_quality": ("real_5m" if oi_g is not None else None),
            })
        if sig:
            chart["signals"] = sig

    # ② 逐笔 trade_id —— 源：**账本真实标识**（positions.jsonl 的
    #    POSITION_CLOSED.payload.trade_id，按 (symbol, exit_time) 对齐）。
    #    ⚠ 这里**绝不**再用 `f"trd_{i+1}"`：那是按行号猜配，会让「图上选中的那一笔」
    #    与「账本里真正的那一笔」失去可核对性（`trd_1` 与真实 `trd_<16hex>` 对不上）。
    try:
        facts = _facts(run_id)
    except Exception:                                      # noqa: BLE001
        facts = None
    if facts is not None:
        by_key: Dict[Tuple[int, int], str] = {}
        for r in facts.trade_rows:
            et, xt, tid = r.get("entry_time_ms"), r.get("exit_time_ms"), r.get("trade_id")
            if et is None or xt is None or not tid:
                continue
            by_key[(int(et), int(xt))] = tid
        for t in (chart.get("trades") or []):
            if not isinstance(t, dict) or t.get("trade_id"):
                continue
            try:
                k = (int((t.get("entry") or {}).get("t") or 0),
                     int((t.get("exit") or {}).get("t") or 0))
            except (TypeError, ValueError):
                continue
            tid = by_key.get(k)
            if tid:
                t["trade_id"] = tid

    # ②b 止损轨迹字段契约统一：官方 runner 落 `t`，批量脚本落 `ts`。
    #     这里**只补 `t`、不改原值**（补不出 `t` 的点保持原样），保证前端按官方契约能画。
    #     注意：账本只有 1 个 `init` 点时，本函数**不合成任何后续轨迹点**。
    for t in (chart.get("trades") or []):
        if not isinstance(t, dict):
            continue
        for p in (t.get("stop_trace") or []):
            if not isinstance(p, dict):
                continue
            if not isinstance(p.get("t"), (int, float)) and isinstance(p.get("ts"), (int, float)):
                p["t"] = int(p["ts"])
    if chart.get("stop_trace_source") is None and (chart.get("trades") or []):
        chart["stop_trace_source"] = (
            "ledger:trades.jsonl.stop_trace（官方 `t` 毫秒；无后续调整时只有 1 个 init 点，不合成）")

    # ②c 成交点的 `initial_stop`：批量脚本写 `0.0`、官方 runner 干脆不写这个键。
    #     账本里这笔的初始止损是确定的（`trades.jsonl.initial_stop`，或 `stop_trace` 的 init 点），
    #     按 `(entry_t, exit_t)` 对齐补上；**不按行号猜配**，补不到就保持原样。
    if facts is not None:
        stop_by_key: Dict[Tuple[int, int], float] = {}
        for r in facts.trade_rows:
            et, xt = r.get("entry_time_ms"), r.get("exit_time_ms")
            st = r.get("initial_stop")
            if st is None:
                # 与 ① 的口径一致：初始止损 = `trades.jsonl.stop_trace` 的**首点**（引擎实况记录）
                trace = r.get("stop_trace") or []
                if trace and isinstance(trace[0], dict):
                    st = trace[0].get("stop")
            if et is None or xt is None or st is None:
                continue
            try:
                stop_by_key[(int(et), int(xt))] = float(st)
            except (TypeError, ValueError):
                continue
        for t in (chart.get("trades") or []):
            if not isinstance(t, dict):
                continue
            if t.get("initial_stop"):
                continue                       # 已有真实值，不覆盖
            try:
                k = (int((t.get("entry") or {}).get("t") or 0),
                     int((t.get("exit") or {}).get("t") or 0))
            except (TypeError, ValueError):
                continue
            st = stop_by_key.get(k)
            if st is not None:
                t["initial_stop"] = st
                t.setdefault("initial_stop_source", "ledger:trades.jsonl.stop_trace[0]")

    # ③ 买入持有基准收益率 —— 源：本 run 自己的基准净值序列（缺则退回 K 线首开→末收）
    bench = chart.get("benchmark")
    if isinstance(bench, dict) and not bench.get("return_pct"):
        series = bench.get("equity_series") or []
        eq0 = None
        for e in (chart.get("equity_curve") or []):
            if isinstance(e, dict) and e.get("equity") is not None:
                eq0 = e.get("equity")
                break
        try:
            if series and eq0:
                last_eq = float(series[-1].get("equity"))
                base = float(eq0)
                if base:
                    bench["return_pct"] = (last_eq - base) / base * 100.0
            else:
                cc = [_candle_tuple(x) for x in (chart.get("candles") or [])]
                cc = [x for x in cc if x and x[1] is not None and x[4] is not None]
                if cc:
                    o0, cl = float(cc[0][1]), float(cc[-1][4])
                    if o0:
                        bench["return_pct"] = (cl - o0) / o0 * 100.0
        except (TypeError, ValueError, IndexError):
            pass

    # ④ 资金费事件 —— 源：funding.jsonl（本 run 只落了 delta；rate/mark 未落盘 ⇒ 保持 None）
    #    `trade_id`：funding 事件**本身不带 trade 标识** ⇒ 走时间窗**唯一**归属；
    #    归属不唯一/在持仓之外 ⇒ None（`trade_link` 写明原因），**不按顺序套用第 N 笔**。
    if not (chart.get("funding_events") or []):
        fe = []
        if facts is not None:
            for f in facts.funding_rows:
                t_ms = f.get("funding_time_ms")
                if not isinstance(t_ms, (int, float)) or t_ms <= 0:
                    continue
                fe.append({"t": int(t_ms), "rate": f.get("rate"), "mark": f.get("mark"),
                           "amount": f.get("amount"), "trade_id": f.get("trade_id"),
                           "trade_link": f.get("trade_link")})
        if fe:
            chart["funding_events"] = fe

    return chart


# 盘上 chart.json 的窗口与本 run 自己的窗口比对容差。
# 取 1 天：run 落盘时窗口两端可能因「最后一根 K 线未收盘」差一根，属正常；
# 超过 1 天就不是对齐误差，而是图表画在了另一段时间上。
_CHART_WINDOW_TOLERANCE_MS = 86_400_000


def _chart_window_reject_reason(run_id: str, chart: dict) -> Optional[str]:
    """盘上 `chart.json` 的窗口与本 run **自己的**窗口不符 ⇒ 返回拒收原因。

    为什么需要（BT-UI-02）：`回测日志/event_trend_v0_1/runs/` 下实测有 83 个 run 的
    `chart.json` 窗口被写成固定的 `2024-01-01 ~ 2024-06-30`，而它们真实窗口在
    2021–2023 —— 这些 run 的 89 笔成交里有 85 笔落在图外。这是**旧读接口把重建结果
    写回 run 目录**留下的错图（BT-UI-01 已切断写回，但盘上那批文件还在）。

    「有图」不等于「图对」：只要窗口对不上就拒收，改由 `_generate_fallback_chart`
    按本 run 自己的窗口重画，并在 `derived_from` 里写明拒收原因。
    **只读不写**，run 目录一个字节不改。
    """
    w = chart.get("window") or {}
    cws, cwe = w.get("start_ms"), w.get("end_ms")
    if not isinstance(cws, int) or not isinstance(cwe, int):
        return None                       # 窗口字段本身缺失，不据此拒收（交给 _backfill_chart）
    try:
        d = S.read_run_dir(run_id)
    except S.RunNotFound:
        return None
    ws, we, src = _run_own_window(run_id, d)
    if ws is None or we is None:
        return None                       # 本 run 自己的窗口都取不到 ⇒ 无从比对
    if abs(cws - ws) > _CHART_WINDOW_TOLERANCE_MS or abs(cwe - we) > _CHART_WINDOW_TOLERANCE_MS:
        def _d(ms: int) -> str:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return (f"盘上 chart.json 的窗口 {_d(cws)}→{_d(cwe)}（UTC）与本 run 自己的窗口 "
                f"{_d(ws)}→{_d(we)}（UTC，来源 {src}）不符 ⇒ 拒收该图表，改按本 run 窗口重建")
    return None


@lru_cache(maxsize=8)
def _fallback_chart_cached(run_id: str) -> dict:
    """读时重建图表的**内存缓存**（不落盘）。

    为什么需要：一个 1 年窗口的 15m 载荷是 3.5 万根 K 线、约 6MB，现算要 4.5 秒；
    同一 run 反复看（切 tab、刷新）没必要每次重算。缓存只在内存里，run 目录一个字节不改。
    取不到就抛异常（`lru_cache` 不缓存异常）⇒ 上层如实报「缺 K 线」。
    """
    return _generate_fallback_chart(run_id, S.read_run(run_id))


def get_chart(run_id: str) -> Tuple[dict, int]:
    doc, err = _require_done(run_id)
    if err:
        return err
    chart = None
    rejected: Optional[str] = None
    try:
        chart = S.read_chart(run_id)
        # 有图 ≠ 图对：窗口与本 run 不符的盘上图表一律拒收（BT-UI-02）
        rejected = _chart_window_reject_reason(run_id, chart)
        if rejected:
            chart = None
    except S.RunNotFound:
        chart = None
    if chart is None:
        try:
            chart = _fallback_chart_cached(run_id)
            if rejected:
                # 赋值而非追加：`_fallback_chart_cached` 返回的是缓存里的同一个 dict，
                # 追加会在第二次访问时重复堆叠同一句话。
                chart["derived_from"] = (
                    f"{rejected}；K 线为本 run 窗口内真实行情（标的取 summary.json，"
                    f"窗口取 inputs.json），未写回 run 目录")
        except Exception as exc:                           # noqa: BLE001
            why = rejected or "本 run 无 chart.json"
            return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                             missing_reason=(f"{why}，且无法按本 run 自己的窗口重建"
                                             f"（{exc}）⇒ 缺 K 线，不伪造")), 200
    # 读取侧补全：修掉批量脚本写死的空字段（只取自本 run 自己的落盘文件，不推算）
    chart = _backfill_chart(run_id, chart)
    # OI 持仓量：批量脚本落盘的 `chart.json` 不带（5m 序列体积大）⇒ 读时按**本 run 自己的**
    # 窗口现取真实归档（`repo.oi(period="5m")`，与策略喂数同一方法）。取不到就空 + 原因，不造点。
    try:
        _spec = doc.get("spec") or {}
        chart = CH.attach_oi(chart,
                             exchange=str(_spec.get("exchange") or "binance"),
                             market=str(_spec.get("market") or "um"))
    except Exception as exc:                               # noqa: BLE001
        chart["oi"] = []
        chart["oi_meta"] = {"error": f"{type(exc).__name__}: {exc}"}
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     chart=chart), 200


# ══════════════════════════ 创建 ══════════════════════════

def _spec_hash(clean_spec: dict) -> str:
    import hashlib
    import json as _j
    return hashlib.sha256(
        _j.dumps(clean_spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _run_in_thread(run_id: str) -> None:
    try:
        RUN.execute_run(run_id)
    finally:
        with _THREADS_LOCK:
            _THREADS.pop(run_id, None)


def _runnable_gate() -> Optional[str]:
    """**策略可运行性硬闸**（WORK-04）：返回拒绝原因，或 `None`（放行）。

    唯一可运行策略是 `WX_OI_RESONANCE_V1`；旧 3a–3d / A / B 已退役。
    注册表不可用时不静默放行 —— 返回原因并拒绝创建。
    """
    try:
        from strategies import registry as _reg
        return _reg.guard_runnable(BR.STRATEGY_ID)
    except Exception as exc:                              # noqa: BLE001
        return f"策略注册表不可用：{type(exc).__name__}: {exc}"


def _freeze_design(experiment_id: Optional[str]) -> Optional[str]:
    """在**创建时**冻结设计身份。返回错误说明，或 `None`（已冻结 / 无需冻结）。

    「已冻结」不是错误：同一设计身份重复提交会复用已有实验，第二次进来
    应当安静跳过，而不是把重复提交变成失败。

    为什么要单独一个函数：冻结是「跑之前把设计定死」，它属于**创建**的职责，
    不属于执行。放在这里还有一个好处——`backtest_runner.py` 是冻结区文件，
    这样改就不必碰它。
    """
    if not experiment_id:
        return None
    try:
        import experiment_registry as EXP
        reg = EXP.get_registry()
        exp = reg.state()["experiments"].get(experiment_id)
        if exp is None:
            return f"实验未登记：{experiment_id}"
        if exp.get("frozen_at"):
            return None
        reg.freeze(experiment_id, by="api:create_run",
                   note="研究运行创建时冻结（设计先定、再跑）")
        return None
    except Exception as exc:                              # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def create_run(payload: Optional[dict]) -> Tuple[dict, int]:
    """创建研究回测（后台执行）。返回 202 + run_id。"""
    if not BR.AVAILABLE:
        return _envelope(missing_reason=f"候选策略包不可用：{BR.IMPORT_ERROR}"), 503
    # ── WORK-04：可运行性硬闸（旧策略 / 未知键一律拒绝，不回退任何旧执行通道）──
    gate_err = _runnable_gate()
    if gate_err:
        return _envelope(missing_reason=f"策略不可运行：{gate_err}"), 409
    clean, errs = BR.validate_run_spec(payload)
    if errs:
        return _envelope(missing_reason="运行规格校验失败", errors=errs), 400

    # ── FRAME-FIT-01：实验登记 / 冻结 / 分组使用规则闸门 ────────────────
    # `validate_run_spec` 只保留白名单字段，`experiment` 块会被丢掉 ⇒ 从原始
    # payload 取回并显式并入 clean，供 `execute_run` 复核（不靠全局变量）。
    exp_block = (payload or {}).get("experiment") if isinstance(payload, dict) else None
    if exp_block is not None and not isinstance(exp_block, dict):
        return _envelope(missing_reason="experiment 必须是 JSON 对象"), 400
    clean["experiment"] = exp_block or {}
    try:
        import experiment_registry as EXP
        exp_gate = EXP.guard_create(clean)
    except Exception as exc:                              # noqa: BLE001
        # 登记库不可用 ⇒ 不静默放行（否则「记录缺失」被当成通过）
        return _envelope(
            missing_reason=f"实验登记库不可用，拒绝创建：{type(exc).__name__}: {exc}",
            stage="experiment_registry"), 503
    if not exp_gate.get("ok"):
        return _envelope(
            missing_reason="实验资格校验未通过（FRAME-FIT-01）",
            stage="experiment_gate",
            experiment=exp_gate,
            errors=exp_gate.get("blockers") or []), 409
    clean["experiment"]["experiment_id"] = exp_gate.get("experiment_id")

    # ── 留出段守卫（执行侧第一道问句）────────────────────────────────────
    # 未定义留出段时 `status=no_holdout` ⇒ 一律放行，不打断任何在跑的批量脚本。
    # 一旦有人写了 `data/experiments/holdout.json`，窗口碰到留出段的运行会被拒绝，
    # 除非请求里显式带 `experiment.acknowledge_holdout_overlap = true`（明确认账）。
    try:
        from infrastructure.guard import holdout as _HOLD
        hold_check = _HOLD.check(
            symbols=clean.get("symbols") or [],
            window_start=clean.get("window_start"),
            window_end=clean.get("window_end"))
    except Exception as exc:                              # noqa: BLE001
        # 守卫自身坏了不能静默放行 —— 否则「配置写坏」就是一条绕过通道。
        return _envelope(
            missing_reason=f"留出段守卫不可用，拒绝创建：{type(exc).__name__}: {exc}",
            stage="holdout_guard"), 503
    ack = bool((exp_block or {}).get("acknowledge_holdout_overlap"))
    if hold_check.get("status") == _HOLD.STATUS_OVERLAP and not ack:
        return _envelope(
            missing_reason="本次运行窗口与最终留出段重叠，已拒绝（防止「看过未来数据」）",
            stage="holdout_guard",
            holdout=hold_check,
            errors=[hold_check.get("note") or "",
                    "确需在这段上跑，请在 experiment 块里显式写 acknowledge_holdout_overlap=true，"
                    "该运行会被登记为「已污染留出」"]), 409
    if hold_check.get("status") == _HOLD.STATUS_INVALID:
        return _envelope(
            missing_reason="留出段或运行窗口配置无法解析，拒绝创建（配置坏了不等于放行）",
            stage="holdout_guard", holdout=hold_check), 409
    clean["experiment"]["holdout"] = hold_check

    # ── 冻结设计身份（研究模式也必须冻结）────────────────────────────────
    # 为什么必须在这里做：`backtest_runner.execute_run` 的执行闸门要求
    # 「实验未冻结 ⇒ 不得产出结果」，而 `guard_create` 的研究模式只**登记**
    # 不冻结 ⇒ 每条研究回测都以「实验未冻结」失败，`record_execution` 也据此
    # 拒绝留痕。实测后果：2,125 个回测产物目录、登记库 0 条业务记录。
    # 冻结的语义是「跑之前先把设计定死」，创建时做正合适：设计身份由
    # 代码+参数+样本+分组+指标决定，任一项变了就是另一条实验，不会互相污染。
    # 冻结失败**不阻断创建**（只把原因透出）—— 保持旧 API 契约，
    # 让执行闸门按原样如实报错，而不是把 202 变成 409。
    freeze_error = _freeze_design(exp_gate.get("experiment_id"))
    if freeze_error:
        clean["experiment"]["freeze_error"] = freeze_error
        exp_gate["freeze_error"] = freeze_error

    sh = _spec_hash(clean)
    run_id = S.new_run_id(sh)
    # 落盘请求覆盖后实际生效的参数
    cfg_actual = {
        "params": clean.get("params", {}),
        "actual_config": {
            "initial_equity": float(clean.get("initial_equity", 2000.0)),
            "risk_budget_per_trade": float(clean.get("params", {}).get("risk_budget_per_trade", 0.005)),
            "taker_fee_bps": float(clean.get("params", {}).get("taker_fee_bps", 5.0)),
            "slippage_bps": float(clean.get("params", {}).get("slippage_bps", 3.0)),
        }
    }
    doc = {
        "run_id": run_id, "strategy_id": BR.STRATEGY_ID, "status": S.STATUS_QUEUED,
        "spec": clean, "spec_sha256": sh,
        "config": cfg_actual,
        "engine": BR.version_block(),
        "contract_version": CONTRACT_VERSION,
        "requested_at": S.utc_now_iso(),
        "live_order_enabled": False,
        "note": "WX_EVENT_TREND_V0_1 真实数据低频事件回测；2000U 本金",
    }
    try:
        S.create_run(run_id, doc)
    except S.RunExists as exc:
        return _envelope(missing_reason=str(exc)), 409

    # ── 投递方式：持久任务表（默认）或进程内线程（回滚路径）──────────────────
    if JOBS_ENABLED:
        try:
            job_id, created = _jobs().enqueue(
                "backtest", {"run_id": run_id},
                idempotency_key=f"backtest:{run_id}")
        except Exception as exc:                          # noqa: BLE001
            # 任务投不进去就不能留一个永远 queued 的 run —— 如实标失败。
            S.set_status(run_id, S.STATUS_FAILED,
                         failure_reason=f"任务队列不可用：{type(exc).__name__}: {exc}",
                         gate="jobs")
            return _envelope(
                run_id=run_id, status=S.STATUS_FAILED,
                missing_reason="任务队列不可用，回测未执行；可用 INPROCESS_JOBS=1 回退",
                error={"code": "JOB_QUEUE_UNAVAILABLE", "retryable": True}), 503
        return _envelope(run_id=run_id, status=S.STATUS_QUEUED, items=[_summary(doc)],
                         total=1,
                         experiment=exp_gate,
                         holdout=hold_check,
                         formal_eligible=False,
                         note=("已受理并写入任务队列，由独立工作进程执行；"
                               "用 GET /api/v1/backtests/{id} 轮询状态"
                               "（研究模式：本 run 不具正式实验资格，见 experiment.formal_blockers）")), 202

    th = threading.Thread(target=_run_in_thread, args=(run_id,),
                          name=f"bt-{run_id}", daemon=True)
    with _THREADS_LOCK:
        _THREADS[run_id] = th
    th.start()
    return _envelope(run_id=run_id, status=S.STATUS_QUEUED, items=[_summary(doc)],
                     total=1,
                     experiment=exp_gate,
                     holdout=hold_check,
                     formal_eligible=False,
                     note="已受理，后台执行；用 GET /api/v1/backtests/{id} 轮询状态"
                          "（研究模式：本 run 不具正式实验资格，见 experiment.formal_blockers）"), 202


# ══════════════════════════ 路由 ══════════════════════════

_SUBRES = ("metrics", "equity", "trades", "chart", "quality")


def route(seg: List[str], params: Optional[dict] = None) -> Tuple[dict, int]:
    """`GET /api/v1/backtests[...]`（`seg` = path 去掉 `api/v1/backtests` 前缀后的段）。

    批次通道（2026-10-18 BT-UI-01）：
      `GET /batches`                       批次列表（runs / runs_* 目录）
      `GET /batches/{id}`                  单批次元数据
      `GET /batches/{id}/summary`          批次汇总 + 与逐笔行数的自洽核对
      `GET /batches/{id}/trades`           批次内全部真实成交（可 limit/offset）
      `GET /batches/{id}/runs`             批次内 run 清单
    批次段必须先于 run_id 判定 —— 否则 `batches` 会被当成一个 run_id。
    """
    if seg and seg[0] == "batches":
        params = params or {}
        force = str((params.get("refresh") or [""])[0]).lower() in ("1", "true", "yes")
        if len(seg) == 1:
            return BATCH.list_batches(force=force), 200
        bid = seg[1]
        if len(seg) == 2:
            return BATCH.get_batch(bid)
        sub = seg[2]
        if sub == "summary":
            return BATCH.batch_summary(bid, force=force)
        if sub == "runs":
            return BATCH.batch_runs(bid, force=force)
        if sub == "trades":
            lim_raw = (params.get("limit") or [None])[0]
            off_raw = (params.get("offset") or ["0"])[0]
            try:
                lim = int(lim_raw) if lim_raw is not None else None
            except (TypeError, ValueError):
                lim = None
            try:
                off = int(off_raw)
            except (TypeError, ValueError):
                off = 0
            return BATCH.batch_trades(bid, limit=lim, offset=off, force=force)
        return _envelope(missing_reason=f"未知批次子资源：{'/'.join(seg)}"), 404

    if not seg:
        return list_experiments(params), 200
    rid = seg[0]
    if len(seg) == 1:
        return get_run(rid)
    sub = seg[1]
    if sub == "metrics":
        return get_metrics(rid)
    if sub == "equity":
        return get_equity(rid)
    if sub == "chart":
        return get_chart(rid)
    if sub == "quality":
        return get_quality(rid)
    if sub == "orders":
        return get_orders(rid)
    if sub == "rejected":
        return get_rejected(rid)
    if sub == "trades":
        if len(seg) == 2:
            return get_trades(rid)
        if len(seg) == 3:
            return get_trade(rid, seg[2])
    return _envelope(missing_reason=f"未知子资源：{'/'.join(seg)}"), 404


def route_post(path: str, payload: Optional[dict]) -> Optional[Tuple[dict, int]]:
    """`POST` 分派：命中返回 `(body, status)`，未命中返回 `None`（交给其它写入口）。"""
    if path == "/api/v1/backtests":
        return create_run(payload)
    return None


def _warm_batches() -> None:
    """后台预热：默认运行列表 + 页面默认选中的那个批次。

    首次扫 1198 + 528 个目录约 5–10 秒（沙箱文件系统每次 open ~4ms），
    放在启动后台线程里做 —— 用户打开回测页时缓存已就绪（TTL 300s / 10s，之后按需重算）。
    失败不影响服务：预热只是缓存，读不到就等请求时现算。
    """
    try:
        S.list_runs(1000)
    except Exception:                                     # noqa: BLE001
        pass
    try:
        BATCH.warm_all()
    except Exception:                                     # noqa: BLE001
        pass


def startup() -> List[str]:
    """服务启动钩子。

    * 队列模式（默认）：**不再**把 queued/running 一律标 interrupted ——
      任务在表里，worker 会继续执行；由 worker 启动时按「有无活跃任务」对账。
    * 线程模式（`INPROCESS_JOBS=1`）：沿用旧行为，重启即标 interrupted。
    """
    touched: List[str] = []
    if not JOBS_ENABLED:
        touched = S.mark_orphans_interrupted()
    threading.Thread(target=_warm_batches, name="warm-batches", daemon=True).start()
    return touched


__all__ = [
    "CONTRACT_VERSION", "list_experiments", "get_run", "get_metrics", "get_equity",
    "get_trades", "get_trade", "get_orders", "get_rejected", "get_chart", "get_quality",
    "create_run", "route", "route_post", "startup",
]
