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

import glob
import json
import os
import re
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]

import backtest_runner as RUN
import backtest_store as S
import bt_run_log as _LOG
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
        "strategy_id": CURRENT_STRATEGY_ID, "run_id": None, "status": None,
        # 默认**不得**报 ok：未读取到真实质量时一律 unknown（不冒充成功）。
        "quality": {"status": "unknown", "age_seconds": None, "note": None},
        "missing_reason": None, "items": [], "total": None,
        "live_order_enabled": False,
    }
    base.update(kw)
    return base


# ══════════════════════════ 读取 ══════════════════════════

def list_experiments(params: Optional[dict] = None) -> dict:
    """运行记录列表（**分页 + 搜索**，BACKFILL-01 2026-10-21）。

    改动原因：原实现 `S.list_runs(limit=min(limit,1000))` 把上限硬编码成 1000，
    前端又只要 600 条 ⇒ 2127 个 run 目录里有 1500+ 条**永远翻不到**，页面上
    看起来像「只跑了 600 次」。现在：
      · 上限提到 5000（当前 2127 条可全量取）；
      · 新增 `q`（按标的 / run_id 子串，大小写不敏感）与 `status` 过滤；
      · **过滤在截断之前做** —— 否则第 1001 条之后即使匹配也搜不出来；
      · 返回 `total`（命中总数）、`returned`（本次返回条数）、`truncated`（是否被截断）、
        `runs_total_all`（运行根目录总数，含被排除的旧策略/消融）。
    """
    params = params or {}
    try:
        limit = int((params.get("limit") or ["500"])[0])
    except (ValueError, TypeError):
        limit = 500
    limit = max(1, min(limit, 5000))
    q = str((params.get("q") or [""])[0] or "").strip().lower()
    status_f = str((params.get("status") or [""])[0] or "").strip().lower()

    # 全量读取（list_runs 内部先全量扫描再按 limit 截断，这里给足上限）
    runs = S.list_runs(limit=5000)
    runs_total_all = len(runs)
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
        # FT-REPLAY-01：研究期结果（`research_period: true`，由 FT-ENTRY-09 落盘 CSV
        # 重建）**允许进列表**，但必须能一眼看出它不是本引擎跑出来的成绩 ——
        # 所以不隐藏，而是靠 `research_period` / `registration` 标记 + 列表里的
        # 「研究期」徽标区分。旧策略 run（无该标记）仍然排除。
        is_research = bool(r.get("research_period"))
        if sid != CURRENT_STRATEGY_ID and not is_research:
            excluded_legacy.append({
                "run_id": r.get("run_id"), "status": r.get("status"),
                "strategy_id": sid, "created_at": r.get("created_at"),
                "attribution": "legacy_excluded",
                "note": "非当前策略（或身份缺失）⇒ 排除出当前运行列表；旧结果不得当成新策略成绩",
            })
            continue
        if status_f and str(r.get("status") or "").lower() != status_f:
            continue
        if q:
            symbols = " ".join(str(s) for s in ((r.get("spec") or {}).get("symbols") or []))
            hay = f"{rid} {symbols}".lower()
            if q not in hay:
                continue
        current.append(r)

    total_matched = len(current)
    page = current[:limit]
    items = [_summary(r) for r in page]
    return _envelope(items=items, total=total_matched,
                     returned=len(items), truncated=total_matched > len(items),
                     runs_total_all=runs_total_all,
                     query={"q": q or None, "status": status_f or None, "limit": limit},
                     runs_root=str(S.RUNS_ROOT),
                     registry_entry=_registry_entry(),
                     retirement=_retirement_block(),
                     excluded_legacy=excluded_legacy,
                     excluded_legacy_total=len(excluded_legacy),
                     note=f"研究回测实验列表（**仅当前策略** {CURRENT_STRATEGY_ID}；只读；"
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
        return {"strategy_id": CURRENT_STRATEGY_ID, "available": False,
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
        "attribution": (
            "research_period" if doc.get("research_period")
            else ("current_strategy" if sid == CURRENT_STRATEGY_ID else "unknown")),
        "research_period": bool(doc.get("research_period")),
        "research": doc.get("research"),
        "registration": doc.get("registration"),
        "symbols": (doc.get("spec") or {}).get("symbols"),
        "window": doc.get("window"),
        "net_return_pct": m.get("net_return_pct"),
        "trades_count": m.get("trades_count"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "failure_reason": doc.get("failure_reason"),
        # CODE-REVIEW-02：未登记历史运行如实透传（UI 显示「未登记」而不是冒充已完成档案）
        "registered": doc.get("registered"),
        "status_basis": doc.get("status_basis"),
        "record_note": doc.get("record_note"),
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
        registered=doc.get("registered"), status_basis=doc.get("status_basis"),
        record_note=doc.get("record_note"),
        # FT-REPLAY-01：研究期结果的身份必须能一眼看出来 ——
        # 它是上游 freqtrade 回测按落盘 CSV 重建的，**不是本引擎重跑**。
        research_period=bool(doc.get("research_period")),
        research=doc.get("research"),
        registration=doc.get("registration"),
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


@lru_cache(maxsize=4)
def _candidate_matrix_index(run_id: str) -> dict:
    """读批次级的「候选跨模式对照」索引（`_candidate_matrix.json`，只读、进程内缓存）。

    为什么读批次级文件而不是现扫 1065 个目录：一次请求扫 3000+ 个 jsonl 不可接受；
    索引由 `工具_FT_REPLAY_01_生成候选对照.py` 在导入时算一次，这里只查字典。
    文件不存在 ⇒ 返回空字典（界面显示「无对照」，不伪造）。
    """
    try:
        d = S.read_run_dir(run_id)
    except S.RunNotFound:
        return {}
    p = d.parent / "_candidate_matrix.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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
        if init is None and isinstance(r.get("initial_stop"), (int, float)):
            init = float(r["initial_stop"])
        if init is None and isinstance(r.get("initial_stop_P0"), (int, float)):
            init = float(r["initial_stop_P0"])
        r["initial_stop"] = init
        r["initial_stop_source"] = ("trades.jsonl.stop_trace[0]" if st else ("trades.jsonl.initial_stop" if init is not None else None))
        if init is None:
            missing_stop += 1

        t_ms = r.get("signal_time_ms")
        src = "trades.jsonl.signal_time_ms" if t_ms is not None else None
        if t_ms is None:
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

        # 信号、确认、下单、成交、出场物理分别展示。
        # 下单时间：账本里有真实下单时刻（如 freqtrade 的 order_filled_timestamp）就用它；
        # 没有就标「未记录」——**禁止用成交时间替代**（那正是「把信号/下单画成成交」的错）。
        if r.get("order_time_ms") is not None:
            r["order_time"] = None
            r["order_time_source"] = "trades.jsonl.order_time_ms"
        else:
            r["order_time_ms"] = None
            r["order_time"] = "未记录"
            r["order_time_source"] = None

        # 确认 K 线收盘时间：只认账本自带的 confirm_time_ms（研究期 run 由确认表或
        # enter_tag 尾段分派而来，来源写在 confirm_time_basis 里）。C0 无此环节 ⇒ 留空。
        r["confirm_time_source"] = (
            r.get("confirm_time_basis")
            or ("trades.jsonl.confirm_time_ms" if r.get("confirm_time_ms") is not None else None))

        # 原始 P0 / 计划风险 / 仓位金额：有就透传并标来源，缺就 null（不拿兜底值冒充）。
        r["P0_source"] = ("audit_confirmation.orig_P0" if r.get("P0") is not None else None)
        r["planned_risk_source"] = (
            "trades.jsonl.planned_risk_usdt" if r.get("planned_risk_usdt") is not None else None)
        r["stake_amount_source"] = (
            "trades.jsonl.stake_amount" if r.get("stake_amount") is not None else None)

        # 事件 ID 与模式：研究期 run 把它们放在 source.event_id / mode，
        # 拍平到顶层供「同一候选在 C0/C1/C2 中的结果对照」直接按列取用。
        _src = r.get("source") if isinstance(r.get("source"), dict) else {}
        r["event_id"] = _src.get("event_id")
        if not r.get("mode"):
            r["mode"] = None
        # 标明导入生成 ID 性质并保留源记录定位
        if r.get("trade_id"):
            r.setdefault("trade_id_nature", "imported_reconstructed")
    if missing_stop:
        notes.append(f"{missing_stop} 笔没有 stop_trace ⇒ 初始止损留空（不按价格推算）")
    if missing_sig_t:
        notes.append(f"{missing_sig_t} 笔未能定位候选信号时刻 ⇒ signal_time_ms 留空")

    # FT-REPLAY-01：把本 run 涉及的候选在其它模式下的结果一并给出，
    # 供「同一候选在 C0/C1/C2 中的结果对照」。只取本 run 真有的 event_id，
    # 索引里没有的就真的没有对照（不补空对象）。
    matrix_all = _candidate_matrix_index(run_id)
    cand_matrix: Dict[str, Any] = {}
    if matrix_all:
        for r in rows:
            ev = r.get("event_id")
            if ev and ev in matrix_all:
                cand_matrix[str(ev)] = matrix_all[ev]

    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     trade_id_source="ledger:positions.jsonl POSITION_CLOSED.payload.trade_id",
                     unresolved_trade_ids=len(unresolved),
                     missing_initial_stop=missing_stop,
                     missing_signal_time=missing_sig_t,
                     candidate_matrix=cand_matrix or None,
                     candidate_matrix_note=(
                         "同一候选在各参数组/模式下的结果（成交/拒单/未成交）；"
                         "来源 _candidate_matrix.json，由落盘 trades/rejected/not_filled 汇总，"
                         "不进任何账本"),
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


def get_fills(run_id: str) -> Tuple[dict, int]:
    """**全局**逐次成交明细（同一 `run_id` 账本 `fills` 事件；与 orders/trades 穿透）。

    纪律：只读账本 fills.jsonl，缺失表/账本 ⇒ 空列表（不编造）。金额/数量/手续费原样来自真实事件。
    """
    doc, err = _require_done(run_id)
    if err:
        return err
    try:
        facts = _facts(run_id)
    except S.RunNotFound:
        return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                         items=[], total=0, missing_reason="账本缺失（fills 表不可读）"), 200
    rows = facts.all_fills()
    return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                     items=rows, total=len(rows),
                     link_notes=list(facts.notes),
                     note=("逐次成交明细（同一 run_id 账本 fills.jsonl 事件）；"
                           "trade_id 由关联 order_id 反查，支持部分成交展开")), 200


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

    # CODE-REVIEW-02（P1-8）：缺字段**不再填 0.0**。旧实现 `float(x or 0.0)` 会把
    # 「没这个字段」画成「价格 0 / 数量 0 / 盈亏 0 的买卖点」——视觉上就是逐笔乱飞。
    # 缺 ⇒ `None`（JSON null）+ 载荷里已有的 `derived_from` 标注 ⇒ 界面显示「—」。
    def _fnum(v):
        try:
            if v is None:
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    def _tms(v):
        try:
            if v is None:
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    chart_trades = []
    _bad_lines = 0
    trades_file = d / "trades.jsonl"
    if trades_file.exists():
        for tid_idx, line in enumerate(trades_file.read_text(encoding="utf-8").splitlines()):
            if not line.strip(): continue
            try:
                t = json.loads(line)
                _et = _tms(t.get("entry_time") or t.get("entry_time_ms"))
                _xt = _tms(t.get("exit_time") or t.get("exit_time_ms"))
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
                              "price": _fnum(t.get("entry_price")),
                              "qty": _fnum(t.get("qty"))},
                    "exit": {"t": _xt,
                             "price": _fnum(t.get("exit_price")),
                             "reason": str(t.get("exit_reason") or "")},
                    "net_pnl": _fnum(t.get("net_pnl")),
                    "initial_stop": _fnum(t.get("initial_stop")),
                    "stop_trace": [{"t": _tms(x.get("t") or x.get("ts")),
                                    "stop": _fnum(x.get("stop")),
                                    "reason": str(x.get("reason") or "")}
                                   for x in t.get("stop_trace", [])],
                })
            except Exception as exc:                        # noqa: BLE001
                # ERR-01：坏行计数 + 记日志（旧实现 `except: pass` 静默丢行，
                # 丢了多少行永远没人知道）
                _bad_lines += 1
                _LOG.run_log("chart_fallback_bad_line", run_id=run_id,
                             line_no=tid_idx, error=f"{type(exc).__name__}: {exc}")

    chart_equity = []
    _bad_eq_lines = 0
    eq_file = d / "equity_curve.jsonl"
    init_eq = float((doc.get("spec") or {}).get("initial_equity") or 2000.0)
    if eq_file.exists():
        for line in eq_file.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            try:
                e = json.loads(line)
                chart_equity.append({
                    "t": _tms(e.get("timestamp") or e.get("t")),
                    "equity": _fnum(e.get("total_equity") or e.get("equity")),
                    "cash": _fnum(e.get("cash_equity") or e.get("cash")),
                    "unrealized": _fnum(e.get("unrealized_pnl")),
                })
            except Exception as exc:                        # noqa: BLE001
                _bad_eq_lines += 1
                _LOG.run_log("chart_fallback_bad_line", run_id=run_id, kind="equity",
                             error=f"{type(exc).__name__}: {exc}")

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
        stop_by_key: Dict[Tuple[int, int], Tuple[float, str]] = {}
        for r in facts.trade_rows:
            et, xt = r.get("entry_time_ms"), r.get("exit_time_ms")
            st = r.get("initial_stop")
            src = r.get("initial_stop_source") or "trades.jsonl.initial_stop"
            if st is None:
                # 与 ① 的口径一致：初始止损 = `trades.jsonl.stop_trace` 的**首点**（引擎实况记录）
                trace = r.get("stop_trace") or []
                if trace and isinstance(trace[0], dict):
                    st = trace[0].get("stop")
                    src = "trades.jsonl.stop_trace[0]"
            if et is None or xt is None or st is None:
                continue
            try:
                stop_by_key[(int(et), int(xt))] = (float(st), src)
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
            hit = stop_by_key.get(k)
            if hit is not None:
                t["initial_stop"] = hit[0]
                t["initial_stop_source"] = f"ledger:{hit[1]}"

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


# ── FT-REPLAY-01：研究期 run 的 K 线按需读取（只读、不落盘）───────────────────
# 研究期 run（`回测日志/event_trend_v0_1/runs_ft_replay_01/*`）的 `chart.json` **故意
# 不落 K 线**：单 run 窗口中位 336 天、最长 1113 天（≈10.7 万根 15m），1065 个目录全落盘
# 会多出 GB 级冗余，而 K 线本来就在市场库里。故读时按本 run 自己的 symbol + window 取。
#
# 为什么要有上限：`_fetch_candles_all` 单次最多 20000 根、超限直接抛错（拒绝静默截断）。
# 复盘看的是成交附近的走势，硬取 3 年既慢又没必要 ⇒ 超限时**只取末段**，并把
# `candles_truncated=true` 写进载荷，绝不悄悄少给。
FT_REPLAY_CANDLE_CAP = 20000
# 读取范围在「run 窗口 ∪ 全部标记点」之外再前后各留 2 天（192 根 15m），
# 让图上能看到信号前后的走势，而不是从入场那根才开始。
FT_REPLAY_CANDLE_PAD_MS = 2 * 86400 * 1000


@lru_cache(maxsize=48)
def _market_candles_window(symbol: str, ws: int, we: int, exchange: str, market: str,
                           tf: str) -> Tuple[tuple, bool, int]:
    """读 `[ws, we)` 的 K 线，返回 `(rows, truncated, returned_bars)`。

    `rows` 是 `({t,o,h,l,c,v}, …)` 的不可变元组，供 `lru_cache` 共享；
    调用方如需改动要自己 copy。
    """
    repo = RUN._repo()
    page = RUN.CANDLE_PAGE
    pages: List[List[dict]] = []
    total = 0
    end = we
    truncated = False
    while True:
        rows = repo.candles(exchange, market, symbol, tf, start=ws, end=end, limit=page)
        if not rows:
            break
        pages.append(rows)
        total += len(rows)
        first = int(rows[0]["open_time"])
        if len(rows) < page or first <= ws:
            break
        end = first
        if total > FT_REPLAY_CANDLE_CAP:      # 已够截断用的量，不再往前翻
            truncated = True
            break
    merged: Dict[int, dict] = {}
    for rows in reversed(pages):               # 早页在前
        for r in rows:
            merged[int(r["open_time"])] = r
    allrows = [merged[k] for k in sorted(merged)]
    if len(allrows) > FT_REPLAY_CANDLE_CAP:
        allrows = allrows[-FT_REPLAY_CANDLE_CAP:]
        truncated = True
    out = tuple({"t": int(r["open_time"]), "o": float(r["open"]), "h": float(r["high"]),
                 "l": float(r["low"]), "c": float(r["close"]), "v": float(r["volume"])}
                for r in allrows)
    return out, truncated, len(allrows)


def _fill_candles_from_market(run_id: str, doc: dict, chart: dict,
                              focus_ms: Optional[int] = None) -> dict:
    """`chart.json` 里 `candles` 为空时，按本 run 自己的标的与窗口补真实 K 线。

    纪律：
      · 标的取 `chart.symbol`（其次 `summary.json`、`spec.symbols`），窗口取 `chart.window`；
        取不到就留空 + 写明原因，**不猜窗口、不拿别的 run 的图顶替**；
      · **不写回 run 目录**（`chart` 是内存副本，盘上文件一个字节不改）；
      · 截断必须显式：`candles_truncated=true` + `candles_note` 说明只给了末段。

    为什么读 K 线的范围要**大于** `chart.window`：run 的窗口是「首笔入场 ~ 末笔出场」，
    而信号与确认都发生在入场**之前**（C1/C2 实测：信号在入场前 45 分钟以上）。
    只按窗口取 K 线，信号点和确认点就落在图外、根本画不出来。所以读取范围按
    「所有标记点 + 前后各留 2 天」算，`chart.window` 本身不动（它要与 run 窗口逐值对齐，
    否则会被 `_chart_window_reject_reason` 判为错图拒收）。
    """
    win = chart.get("window") or {}
    ws, we = win.get("start_ms"), win.get("end_ms")
    symbol = chart.get("symbol")
    if not symbol:
        try:
            symbol = (json.loads((S.read_run_dir(run_id) / "summary.json")
                                 .read_text(encoding="utf-8")).get("symbol"))
        except Exception:                                  # noqa: BLE001
            symbol = None
    symbol = symbol or (((doc.get("spec") or {}).get("symbols") or [None])[0])
    if not symbol or not isinstance(ws, int) or not isinstance(we, int):
        chart = dict(chart)
        chart["candles_note"] = "本 run 无可用标的或窗口 ⇒ K 线留空（不伪造）"
        return chart
    spec = doc.get("spec") or {}
    ex = str(spec.get("exchange") or "binance")
    mk = str(spec.get("market") or "um")
    tf = str(chart.get("exec_timeframe") or "15m")

    # 标记点跨度（信号 / 确认 / 入场 / 出场）—— 这些点必须都在图内
    times: List[int] = []
    for s in chart.get("signals") or []:
        if isinstance(s.get("t"), int):
            times.append(int(s["t"]))
    for c in chart.get("confirms") or []:
        if isinstance(c.get("t"), int):
            times.append(int(c["t"]))
    for t in chart.get("trades") or []:
        for k in ("entry", "exit"):
            v = (t.get(k) or {}).get("t")
            if isinstance(v, int):
                times.append(int(v))
    pad = FT_REPLAY_CANDLE_PAD_MS
    read_ws = min([ws] + times) - pad
    read_we = max([we] + times) + pad
    # 按笔聚焦：一个 run 是该「参数组 × 币种」的全部成交，成交可能分散在几年里
    # （实测标记跨度中位 3.2 万根 15m、最长 10.7 万根）。一次画全必然要么截断、
    # 要么稀疏到看不清某笔的四个点。带 `focus_ms` 时改取「该时刻前后各半」，
    # 让用户点哪一笔就看清哪一笔 —— 这是复盘的最小必要能力，不改数据结构。
    focused = False
    if isinstance(focus_ms, int) and focus_ms > 0:
        half = FT_REPLAY_CANDLE_CAP // 2
        read_ws = max(read_ws, focus_ms - half * 900000)
        read_we = min(read_we, focus_ms + half * 900000)
        focused = True
    # 同样裁到「本 run 实际消费的数据区间」：留白段可能已经落在冻结数据之后
    _dr_a, _dr_b = _run_data_range(run_id)
    clipped = False
    if isinstance(_dr_a, int) and isinstance(_dr_b, int):
        _nw, _ne = max(int(read_ws), _dr_a), min(int(read_we), _dr_b)
        clipped = (_nw != int(read_ws) or _ne != int(read_we))
        read_ws, read_we = _nw, _ne

    try:
        rows, truncated, n = _market_candles_window(symbol, int(read_ws), int(read_we), ex, mk, tf)
    except Exception as exc:                               # noqa: BLE001
        chart = dict(chart)
        chart["candles_note"] = (f"读市场库失败（{type(exc).__name__}: {exc}）"
                                 f"⇒ K 线留空，不伪造")
        return chart
    chart = dict(chart)
    if not rows:
        chart["candles_note"] = (f"市场库无 {ex}/{mk}/{symbol}/{tf} 在 "
                                 f"{read_ws}~{read_we} 的 K 线 ⇒ 留空（不伪造）")
        return chart
    chart["candles"] = [dict(r) for r in rows]
    chart["candles_truncated"] = truncated
    chart["candles_focused_ms"] = int(focus_ms) if focused else None
    chart["candles_clipped_to_run_data"] = bool(clipped)
    chart["candles_source"] = (
        f"读时从市场库读取 {ex}/{mk}/{symbol}/{tf}（读取范围 {read_ws}~{read_we}，UTC 毫秒；"
        f"{'按笔聚焦于 ' + str(focus_ms) + ' 前后各半' if focused else '本 run 窗口 ∪ 全部标记点'} "
        f"± {pad // 3600000} 小时）；未写回 run 目录")
    if truncated:
        first_t = rows[0]["t"]
        chart["candles_note"] = (
            f"本 run 窗口内 K 线超过单次上限 {FT_REPLAY_CANDLE_CAP} 根 ⇒ "
            f"K 线只返回末 {n} 根（起于 "
            f"{datetime.fromtimestamp(first_t / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}Z）；"
            f"信号/确认/成交标记保留全窗口，落在该时刻之前的标记不在本图上")
        chart["candles_window_shown"] = {"start_ms": rows[0]["t"], "end_ms": rows[-1]["t"]}
    return chart


@lru_cache(maxsize=8)
def _fallback_chart_cached(run_id: str) -> dict:
    """读时重建图表的**内存缓存**（不落盘）。

    为什么需要：一个 1 年窗口的 15m 载荷是 3.5 万根 K 线、约 6MB，现算要 4.5 秒；
    同一 run 反复看（切 tab、刷新）没必要每次重算。缓存只在内存里，run 目录一个字节不改。
    取不到就抛异常（`lru_cache` 不缓存异常）⇒ 上层如实报「缺 K 线」。
    """
    return _generate_fallback_chart(run_id, S.read_run(run_id))


# ══════════════ K 线补齐后的元数据收口（WORK-DYNAMIC-BT-02 / BT-CHART-FIX-05）══════════════

_CHART_TF_ALLOWED = ("1d", "12h", "8h", "4h", "1h", "30m", "15m")


def cm_utc(ms: Optional[int]) -> str:
    """UTC 毫秒 → `YYYY-MM-DD HH:MMZ`（仅用于文案，不参与任何计算）。"""
    if not isinstance(ms, int):
        return "n/a"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


@lru_cache(maxsize=64)
def _run_data_range(run_id: str) -> Tuple[Optional[int], Optional[int]]:
    """本 run **实际消费的数据区间**（左闭右开，UTC 毫秒），取不到返回 (None, None)。

    为什么必须裁剪：实时市场库比冻结的 feather 新。按「末笔成交 + 2 天留白」取数时，
    末尾那一段会取到**回测当时还不存在**的行情 —— 实测 BEAMX（末笔 2025-01-01）在
    1h 图上多出 24 根、4h 多 6 根、15m 多 96 根，全是 2025-01-01 的实时数据。
    复盘图里出现回测数据之外的价格，等于让人看着未来价格评价当时的决策。
    """
    try:
        d = json.loads((S.read_run_dir(run_id) / "inputs.json").read_text(encoding="utf-8"))
        tr = ((d.get("data_summary") or {}).get("time_range_1h") or {})
        a, b = tr.get("start_ms"), tr.get("end_ms")
        if isinstance(a, int) and isinstance(b, int) and b > a:
            return a, b
    except Exception:                                          # noqa: BLE001
        pass
    return None, None


def _finalize_chart_meta(chart: dict, doc: dict, *, req_sym: str, req_tf: str) -> dict:
    """把「candles 到底有没有」与「元数据怎么说」对齐（只改返回载荷，不落盘）。

    为什么必须有这一步：补 K 线有三条分支（replay 包 / parquet / 市场库），它们只写
    `candles`；而 `chart.json` 里上一轮留下的 `chart_partial` / `what_works` /
    `chart_blocked_reason` 仍写着「candles 为空（结构性限制）」⇒ 同一份响应自相矛盾
    （实测：1334 根真实 K 线，配「K 线为空」的说明）。审核与前端都无法据此判断真假。

    同时纠正一处语义污染：补 K 线的分支把 `exec_timeframe` 改成了**当前显示周期**，
    而它本义是**策略执行周期**（本单为 15m）。这里恢复执行周期语义，另给
    `display_timeframe` 表示本次实际返回的周期。

    纪律：只改内存副本；不写回 run 目录；不猜数据；有就写有、没有就写清原因。
    """
    chart = dict(chart)
    spec = doc.get("spec") or {}
    exec_tf = str(spec.get("exec_timeframe") or "15m")
    sig_tf = str(spec.get("signal_timeframe") or "1h")
    # 补 K 线的分支会把 `exec_timeframe` 改写成「本次实际返回的周期」。必须在恢复
    # 执行周期语义**之前**把它取出来，否则下面 display_timeframe 只能拿到 15m
    # ⇒ 实测请求 1h/4h 时 display_timeframe 全报 15m（图是 1h、标签是 15m）。
    shown_tf = str(chart.get("exec_timeframe") or "").strip().lower()
    chart["exec_timeframe"] = exec_tf          # 恢复真实执行周期语义（补 K 线的分支会改写它）
    chart["signal_timeframe"] = sig_tf
    bars = chart.get("candles") or []
    n = len(bars)
    short = (req_sym or "").replace("USDT", "").upper()
    syms = chart.get("symbols") or []
    in_run = bool(short) and short in [str(x).replace("USDT", "").upper() for x in syms]
    if req_sym:
        chart["requested_symbol"] = short
        chart["symbol_in_run"] = in_run

    if n:
        first_t, last_t = int(bars[0]["t"]), int(bars[-1]["t"])
        shown = chart.get("candles_window_shown") or {"start_ms": first_t, "end_ms": last_t}
        chart["candles_window_shown"] = shown
        chart["candles_bars"] = n
        chart["candles_truncated"] = bool(chart.get("candles_truncated") or False)
        if not chart.get("candles_source"):
            chart["candles_source"] = (
                f"读时按本 run 自己的标的与窗口取真实 K 线（{chart.get('symbol')}/{req_tf}），"
                f"未写回 run 目录")
        chart["display_timeframe"] = shown_tf if shown_tf in _CHART_TF_ALLOWED else req_tf
        # 与 run 窗口的关系必须显式：组合 run 的 window 是「首笔入场~末笔出场」，
        # 而单标的 K 线只覆盖该标的自己的成交区间 ⇒ 两者本来就不该相等。
        win = chart.get("window") or {}
        ws, we = win.get("start_ms"), win.get("end_ms")
        matches = bool(isinstance(ws, int) and isinstance(we, int)
                       and abs(first_t - ws) <= 3_600_000 and abs(last_t - we) <= 3_600_000)
        chart["candles_window_matches_run_window"] = matches
        chart["chart_partial"] = False
        chart["chart_blocked_reason"] = None
        note = ""
        if req_sym and not in_run:
            note = (f"注意：{short} 不在本 run 的成交标的内（本 run 只成交过 "
                    f"{len(syms)} 个标的）⇒ 图上没有该 run 的成交/信号标记，"
                    f"K 线仅是该标的的真实行情。")
        elif not matches:
            note = ("图轴请以 candles_window_shown 为准：单标的 K 线只覆盖该标的在本 run 的"
                    "成交区间，与 run 级 window（首笔入场~末笔出场）不相等属正常。")
        if chart.get("candles_scope") == "trade_windows_union":
            note = (f"⚠ K 线是「各笔成交回看窗口的并集」，含 {chart.get('candles_gaps')} 处断档，"
                    f"不是连续行情，不可用于形态/趋势判断（连续行情取不到才会退回这种）。"
                    + (" " + note if note else ""))
        chart["what_works"] = (
            f"K 线可用：{n} 根 {req_tf}（{chart.get('symbol')}，"
            f"{datetime.fromtimestamp(first_t / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}Z"
            f" ~ {datetime.fromtimestamp(last_t / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}Z"
            f"{'，已截断' if chart['candles_truncated'] else ''}）；"
            f"同标的的逐笔/信号/止损轨迹/切档事件与账户权益曲线同响应返回。"
            f"执行周期 {exec_tf}、信号周期 {sig_tf}；本次显示 {chart['display_timeframe']}。"
            + (" " + note if note else ""))
    else:
        chart["chart_partial"] = True
        chart["candles_bars"] = 0
        chart["display_timeframe"] = None
        # 原因必须指向**本次请求**，不能留上一轮「组合契约装不下」的旧文案：
        # 现在契约已经能按 symbol 出图，再说「装不下」就是错的。
        if req_sym and short not in [str(x).replace("USDT", "").upper() for x in syms] and syms:
            chart["chart_blocked_reason"] = (
                f"请求的标的 {short} 不在本 run 的成交标的内（本 run 成交过 {len(syms)} 个标的），"
                f"且市场库也无该标的 {req_tf} K 线 ⇒ 本次无图。可选标的见 symbols。")
        else:
            chart["chart_blocked_reason"] = (
                f"本次请求（symbol={req_sym or '未指定'}，timeframe={req_tf}）"
                f"未取到 K 线；不伪造、不用别的 run 的图顶替。")
        chart["what_works"] = (
            "K 线为空（原因见 chart_blocked_reason / candles_note）；"
            "账户权益曲线、逐笔、信号仍可用。")
    return chart


# ══════════ 上下文行情：把图的时间窗扩到 run 消费区间之外（BT-KLINE-CONTEXT-01）══════════
#
# 用户原话：「只有 7 根 K 线」。查证：`FT-ENTRY-09` 那批 run 的窗口只有 0.25~27 小时
# （`run.json.window`），而策略前置条件是 **7 天箱体 + 24h 冷却** ⇒ 窗口内根本看不到
# 生成信号的箱体，复盘页在这种 run 上等于没用。
#
# 所以这里提供**可选**的上下文补数：`?context_days=N`（默认 0 = 完全保持原口径）。
# 纪律（这是与「不把回测没消费过的行情画进复盘图」那条规则的和解方式）：
#   · **只加不改**：run 区间内的 K 线原样保留（它们必须还能回到回测实际消费的数据）；
#   · 新增的两段**单独计数**（`candles_context_before/after`）并给出 `candles_run_range`，
#     前端据此把「非本 run 消费区间」标出来，不能混为一谈；
#   · 上下文只来自市场库真实行情；取不到就**不加**，不拿别的 run / 别的周期顶替；
#   · 总量受 `FT_REPLAY_CANDLE_CAP` 约束，超了**先砍上下文**，绝不砍 run 区间内的；
#   · 只改内存副本，不写回 run 目录。

_CONTEXT_DAYS_MAX = 90
_TF_STEP_MS = {
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _parse_context_days(params: dict) -> int:
    """`?context_days=N`（天，0~90）；缺省/非法 ⇒ 0（不改变既有行为）。"""
    raw = params.get("context_days")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    try:
        n = int(raw) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        n = 0
    return max(0, min(n, _CONTEXT_DAYS_MAX))


def _infer_tf_from_bars(bars: List[dict]) -> Optional[str]:
    """从相邻 `t` 的中位间隔反推周期；只认白名单三档，认不出就 `None`（不猜）。"""
    ts = [int(b["t"]) for b in bars[:60] if isinstance(b.get("t"), int)]
    if len(ts) < 2:
        return None
    diffs = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    step = diffs[len(diffs) // 2]
    best = min(_TF_STEP_MS, key=lambda k: abs(_TF_STEP_MS[k] - step))
    return best if abs(_TF_STEP_MS[best] - step) <= _TF_STEP_MS[best] * 0.25 else None


def _apply_candle_context(run_id: str, doc: dict, chart: dict, context_days: int) -> dict:
    """在 run 消费区间**之外**前后各补 `context_days` 天真实行情。"""
    chart = dict(chart)
    bars = [dict(b) for b in (chart.get("candles") or [])]
    if not bars:
        chart["candles_context_note"] = "本 run 无 K 线 ⇒ 不补上下文（不伪造）"
        return chart

    tf = str(chart.get("display_timeframe") or "").strip().lower()
    if tf not in _CHART_TF_ALLOWED:
        tf = _infer_tf_from_bars(bars) or ""
    if not tf:
        chart["candles_context_note"] = f"无法确定 K 线周期（白名单 {','.join(_CHART_TF_ALLOWED)} 之外）⇒ 不补上下文"
        return chart

    symbol = str(chart.get("symbol") or "").strip()
    if not symbol:
        chart["candles_context_note"] = "载荷无标的 ⇒ 不补上下文"
        return chart

    budget = FT_REPLAY_CANDLE_CAP - len(bars)
    if budget <= 0:
        chart["candles_context_note"] = (
            f"run 区间本身已 {len(bars)} 根、达到单次上限 {FT_REPLAY_CANDLE_CAP} ⇒ 不补上下文")
        return chart

    first_bar_t = int(bars[0]["t"])
    last_bar_t = int(bars[-1]["t"])
    spec = doc.get("spec") or {}
    ex = str(spec.get("exchange") or "binance")
    mk = str(spec.get("market") or "um")
    pad = context_days * 86_400_000
    try:
        # 左闭右开 [first_bar_t - pad, first_bar_t)，末根与 bars[0] 无缝贴合
        before, _bt, _ = _market_candles_window(symbol, first_bar_t - pad, first_bar_t, ex, mk, tf)
        # 右侧从 last_bar_t 开始查询，再按 run_ts 剔除重复的末根，首根与 bars[-1] 无缝贴合
        after, _at, _ = _market_candles_window(symbol, last_bar_t, last_bar_t + pad + 86_400_000, ex, mk, tf)
    except Exception as exc:                                   # noqa: BLE001
        chart["candles_context_note"] = (
            f"上下文行情读取失败（{type(exc).__name__}: {exc}）⇒ 只保留 run 区间")
        return chart

    run_ts = {int(x["t"]) for x in bars}
    pre = [dict(r) for r in before if int(r["t"]) not in run_ts]
    post = [dict(r) for r in after if int(r["t"]) not in run_ts]
    orig_pre_len = len(pre)
    orig_post_len = len(post)
    is_budget_clipped = False
    # 超预算时**从离 run 最远的开始砍**，run 区间一根不动
    while len(pre) + len(post) > budget:
        is_budget_clipped = True
        if len(pre) >= len(post):
            pre = pre[1:]
        else:
            post = post[:-1]

    merged = pre + bars + post
    merged.sort(key=lambda x: int(x["t"]))
    first_t, last_t = int(merged[0]["t"]), int(merged[-1]["t"])

    chart["candles"] = merged
    chart["candles_bars"] = len(merged)
    chart["candles_context_days"] = context_days
    chart["candles_context_before"] = len(pre)
    chart["candles_context_after"] = len(post)
    chart["candles_context_budget_clipped"] = is_budget_clipped
    chart["candles_context_budget_cap"] = FT_REPLAY_CANDLE_CAP
    chart["candles_context_budget_remaining"] = max(0, budget - (len(pre) + len(post)))
    chart["candles_context_tf"] = tf
    chart["candles_run_range"] = {"start_ms": first_bar_t, "end_ms": last_bar_t}
    chart["candles_window_shown"] = {"start_ms": first_t, "end_ms": last_t}
    chart["candles_window_matches_run_window"] = False
    chart["candles_source"] = (
        f"{str(chart.get('candles_source') or '').rstrip('；; ')}；"
        f"另补**上下文行情** ±{context_days} 天（{tf}，前 {len(pre)} 根 / 后 {len(post)} 根），"
        f"全部为市场库真实行情、**非本 run 消费区间**；"
        f"run 区间 {cm_utc(first_bar_t)} ~ {cm_utc(last_bar_t)} 内的 {len(bars)} 根原样保留、未替换")
    if not pre and not post:
        chart["candles_context_note"] = (
            f"市场库在 ±{context_days} 天内无 {symbol} {tf} 行情 ⇒ 只显示 run 区间（{len(bars)} 根）")
    if isinstance(chart.get("what_works"), str):
        chart["what_works"] = (
            f"{chart['what_works']} 另补上下文 ±{context_days} 天 {tf} 真实行情"
            f"（前 {len(pre)} 根 / 后 {len(post)} 根，非本 run 消费区间）；"
            f"当前合计 {len(merged)} 根。")
    return chart


def get_chart(run_id: str, params: Optional[dict] = None) -> Tuple[dict, int]:
    doc, err = _require_done(run_id)
    if err:
        return err
    chart = None
    rejected: Optional[str] = None
    params = params or {}
    req_sym = str((params.get("symbol") or [""])[0] if isinstance(params.get("symbol"), list) else (params.get("symbol") or "")).strip().upper()

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

    # 多币组合标的识别与动态 K 线切换支持（支持 WORK-DYNAMIC-BT-01 与多币组合复盘）
    req_tf = str((params.get("timeframe") or ["1h"])[0] if isinstance(params.get("timeframe"), list) else (params.get("timeframe") or "1h")).strip().lower()
    # 周期白名单：非法值**拒绝**而不是静默回退成 1h —— 否则响应会一边给 1h 的 K 线、
    # 一边把 display 标成用户要的周期，等于用错的图冒充对的图。
    if req_tf not in _CHART_TF_ALLOWED:
        return _envelope(run_id=run_id, status=doc.get("status"), quality=_quality_of(doc),
                         missing_reason=(f"timeframe 只支持 {'/'.join(_CHART_TF_ALLOWED)}，"
                                         f"收到 {req_tf!r} ⇒ 拒绝（不静默回退、不拿别的周期冒充）")), 400
    # 按笔聚焦（可选）：成交可能分散在几年里，聚焦到某一笔才看得清四个点
    focus_ms = params.get("focus_ms")
    if isinstance(focus_ms, list):
        focus_ms = focus_ms[0] if focus_ms else None
    try:
        focus_ms = int(focus_ms) if focus_ms not in (None, "") else None
    except (TypeError, ValueError):
        focus_ms = None
    is_portfolio = chart and (str(chart.get("symbol", "")).startswith("PORTFOLIO") or "PORTFOLIO" in str((doc.get("spec") or {}).get("symbols", [])))

    if chart and (is_portfolio or req_sym):
        try:
            d = S.read_run_dir(run_id)
            all_trades = []
            tf_file = d / "trades.jsonl"
            if tf_file.exists():
                all_trades = [json.loads(line) for line in tf_file.read_text(encoding="utf-8").splitlines() if line.strip()]

            # 汇总该组合的所有标的清单
            symbols_in_run = list(dict.fromkeys(t.get("symbol") for t in all_trades if t.get("symbol")))
            if symbols_in_run:
                chart["symbols"] = symbols_in_run

            # 确定当前展示的标的
            active_sym = req_sym
            if not active_sym and symbols_in_run:
                active_sym = symbols_in_run[0]

            if active_sym:
                sym_clean = active_sym.replace("USDT", "").upper()
                sym_full = f"{sym_clean}USDT"

                # 优先从 WORK-DYNAMIC-BT-01 replay 包中提取该标的高保真真实 K 线 (含 1h/4h/15m + 止损切档事件)
                m_dyn = re.search(r"run_FT_LOW4H_DYN01_(Wyckoff_.*)", run_id)
                replay_loaded = False
                if m_dyn:
                    strat_name = m_dyn.group(1)
                    rp_path = PROJECT_ROOT / "evidence" / "WORK-DYNAMIC-BT-01" / "replay" / f"{strat_name}.jsonl.gz"
                    if rp_path.exists():
                        try:
                            import gzip
                            with gzip.open(rp_path, "rt", encoding="utf-8") as f_rp:
                                rp_trades = [json.loads(line) for line in f_rp]

                            matched_rp_trades = [t for t in rp_trades if t.get("symbol", "").upper() == sym_clean or t.get("symbol", "").upper() == active_sym]
                            if matched_rp_trades:
                                all_bars = []
                                # 只取**请求的那个周期**。原来写成 `tfs.get(req_tf) or tfs.get("1h")`，
                                # 一旦某笔缺 4h 就会把 1h 的 K 线混进来，再被标成 4h 返回 ——
                                # 用错周期的图冒充对的图。宁可少画也不混周期，缺的记进 candles_skipped_tf。
                                skipped_tf = []
                                for mt in matched_rp_trades:
                                    tfs_dict = mt.get("timeframes") or {}
                                    bars = tfs_dict.get(req_tf) or []
                                    if not bars:
                                        skipped_tf.append(mt.get("trade_id"))
                                        continue
                                    all_bars.extend(bars)

                                by_t = {c["t"]: c for c in all_bars if "t" in c}
                                rp_bars = [by_t[k] for k in sorted(by_t)]

                                # ── 主图 K 线：优先「连续行情」，replay 只作兜底 ──
                                # replay 包里的 K 线是「每笔成交各自回看窗口」的并集，成交跨年份时
                                # 会留下成万个空档（实测 AVAX 1h：3 笔成交跨 2021-12/2023-05/2023-09，
                                # 空档 12,261 根 ≈ 1.4 年）。把它当主图就是断轴图 ⇒ 按该标的自己的
                                # 成交区间，从市场库取连续 K 线；取不到才退回并集，并显式标出断档数。
                                _spec = doc.get("spec") or {}
                                _ex = str(_spec.get("exchange") or "binance")
                                _mk = str(_spec.get("market") or "um")
                                _times: List[int] = []
                                for mt in matched_rp_trades:
                                    for _k in ("entry", "exit"):
                                        _v = (mt.get(_k) or {}).get("t")
                                        if isinstance(_v, int):
                                            _times.append(int(_v))
                                _pad = FT_REPLAY_CANDLE_PAD_MS
                                _read_ws = (min(_times) - _pad) if _times else None
                                _read_we = (max(_times) + _pad) if _times else None
                                if _times and focus_ms:
                                    _half = FT_REPLAY_CANDLE_CAP // 2 * 900000
                                    _read_ws = max(_read_ws, focus_ms - _half)
                                    _read_we = min(_read_we, focus_ms + _half)
                                # 裁到「本 run 实际消费的数据区间」：实时库比冻结数据新，
                                # 不裁就会把回测数据之外的行情画进复盘图（BEAMX 实测多 24 根）。
                                _dr_a, _dr_b = _run_data_range(run_id)
                                _clipped = False
                                if (_read_ws is not None and _read_we is not None
                                        and isinstance(_dr_a, int) and isinstance(_dr_b, int)):
                                    _nw, _ne = max(int(_read_ws), _dr_a), min(int(_read_we), _dr_b)
                                    if _nw != int(_read_ws) or _ne != int(_read_we):
                                        _clipped = True
                                    _read_ws, _read_we = _nw, _ne
                                cont: tuple = ()
                                cont_trunc = False
                                cont_err = None
                                if _read_ws is not None:
                                    try:
                                        cont, cont_trunc, _ = _market_candles_window(
                                            sym_full, int(_read_ws), int(_read_we), _ex, _mk, req_tf)
                                    except Exception as exc:          # noqa: BLE001
                                        cont_err = f"{type(exc).__name__}: {exc}"

                                if cont:
                                    chart["candles"] = [dict(r) for r in cont]
                                    chart["candles_scope"] = "market_continuous"
                                    chart["candles_truncated"] = bool(cont_trunc)
                                    chart["candles_clipped_to_run_data"] = bool(_clipped)
                                    chart["candles_source"] = (
                                        f"读时从市场库读取 {_ex}/{_mk}/{sym_full}/{req_tf} 连续行情"
                                        f"（{cm_utc(_read_ws)} ~ {cm_utc(_read_we)}，UTC"
                                        f"{'，已裁到本 run 实际消费的数据区间' if _clipped else ''}）；"
                                        f"replay 包只提供该标的逐笔成交/信号/止损轨迹/切档事件；"
                                        f"未写回 run 目录")
                                    chart["candles_window_shown"] = {
                                        "start_ms": int(cont[0]["t"]), "end_ms": int(cont[-1]["t"])}
                                    if focus_ms:
                                        chart["candles_focused_ms"] = int(focus_ms)
                                elif rp_bars:
                                    _step = {
                                        "1d": 86_400_000,
                                        "12h": 43_200_000,
                                        "8h": 28_800_000,
                                        "4h": 14_400_000,
                                        "1h": 3_600_000,
                                        "30m": 1_800_000,
                                        "15m": 900_000,
                                    }.get(req_tf, 900_000)
                                    _ts = [int(c["t"]) for c in rp_bars]
                                    _gaps = sum(1 for i in range(len(_ts) - 1)
                                                if _ts[i + 1] - _ts[i] > _step * 1.5)
                                    chart["candles"] = rp_bars
                                    chart["candles_scope"] = "trade_windows_union"
                                    chart["candles_gaps"] = _gaps
                                    chart["candles_truncated"] = False
                                    chart["candles_source"] = (
                                        f"市场库取不到 {sym_full}/{req_tf} 连续行情"
                                        f"（{cont_err or '无数据'}）⇒ 退回 replay 包的"
                                        f"「各笔成交回看窗口并集」：{len(rp_bars)} 根，"
                                        f"含 {_gaps} 处断档，**不是连续行情**，不可当主图做形态判断")
                                    chart["candles_window_shown"] = {
                                        "start_ms": _ts[0], "end_ms": _ts[-1]}

                                if chart.get("candles"):
                                    chart["symbol"] = sym_full
                                    chart["exec_timeframe"] = req_tf
                                    chart["display_timeframe"] = req_tf
                                    chart["candles_timeframe"] = req_tf
                                    chart["missing_reason"] = None
                                    chart["candles_skipped_tf"] = skipped_tf

                                    # 提取该标的交易与信号
                                    chart_trades = []
                                    chart_signals = []
                                    for mt in matched_rp_trades:
                                        t_item = {
                                            "trade_id": f"trd_{strat_name}_{mt['trade_id']:04d}",
                                            "symbol": sym_full,
                                            "pair": mt.get("pair"),
                                            "direction": "long",
                                            "signal_type": 1,
                                            "entry": mt.get("entry"),
                                            "exit": mt.get("exit"),
                                            "stop_trace": mt.get("stop_trace"),
                                            "stage_events": mt.get("stage_events"),
                                            "net_pnl": (mt.get("pnl") or {}).get("profit_abs"),
                                            "exit_reason": (mt.get("exit") or {}).get("reason"),
                                        }
                                        chart_trades.append(t_item)
                                        sg = mt.get("signal")
                                        if sg:
                                            chart_signals.append({
                                                "signal_id": mt.get("candidate_id"),
                                                "trade_id": t_item["trade_id"],
                                                "t": sg.get("t"),
                                                "price": (mt.get("entry") or {}).get("price"),
                                                "confirm_time": sg.get("confirm_t"),
                                                "entry_time": (mt.get("entry") or {}).get("t"),
                                                "decision": sg.get("t"),
                                                "P0": (mt.get("entry") or {}).get("price"),
                                                "direction": "long",
                                                "kind": "candidate_from_fill",
                                            })
                                    chart["trades"] = chart_trades
                                    chart["signals"] = chart_signals
                                    replay_loaded = True
                        except Exception as exc:
                            replay_loaded = False

                # 若非 DYN01 或 replay 未加载，从 parquet / 市场库提取
                if not replay_loaded:
                    pq_files = sorted(glob.glob(f"{PROJECT_ROOT}/数据/市场数据/parquet/candles/binance/um/{sym_full}/{req_tf}/*.parquet"))
                    # 不再「缺该周期就拿 15m 顶」：那会把 15m 的 K 线标成 1h/4h 返回。
                    if not pq_files:
                        chart["candles_source"] = (
                            f"parquet 无 {sym_full}/{req_tf}（也不拿 15m 冒充 {req_tf}）"
                            f"⇒ 本标的本次无图")
                    if pq_files:
                        import pandas as pd
                        dfs = [pd.read_parquet(f) for f in pq_files]
                        df_raw = pd.concat(dfs, ignore_index=True)
                        df_raw = df_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
                        for c in ("open", "high", "low", "close", "volume"):
                            if c in df_raw.columns:
                                df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")
                        df_raw["open_time"] = pd.to_numeric(df_raw["open_time"], errors="coerce")
                        ws = (chart.get("window") or {}).get("start_ms")
                        we = (chart.get("window") or {}).get("end_ms")
                        if not (isinstance(ws, int) and isinstance(we, int)):
                            # 原来这里 `or 1638316800000 / or 1735689600000` 会悄悄用
                            # 2022-01-01~2025-01-01 兜底 ⇒ 图与 run 窗口无关也算「有图」。
                            raise ValueError("本 run 无 window（start_ms/end_ms）⇒ 不猜区间，本次不出图")
                        df_win = df_raw.loc[(df_raw["open_time"] >= ws) & (df_raw["open_time"] <= we)]
                        chart["candles"] = [
                            {"t": int(r.open_time), "o": float(r.open), "h": float(r.high),
                             "l": float(r.low), "c": float(r.close), "v": float(r.volume)}
                            for r in df_win.itertuples(index=False)
                        ]
                        chart["symbol"] = sym_full
                        chart["exec_timeframe"] = req_tf
                        sym_trades = [t for t in all_trades if t.get("symbol") in (active_sym, sym_clean, sym_full)]
                        chart["trades"] = sym_trades
                        chart["signals"] = [
                            {"signal_id": t.get("signal_id"), "trade_id": t.get("trade_id"),
                             "t": t.get("signal_time_ms"), "price": t.get("entry_price"),
                             "confirm_time": t.get("confirm_time_ms"), "p0": t.get("initial_stop")}
                            for t in sym_trades if t.get("signal_time_ms")
                        ]
        except Exception as exc:                           # noqa: BLE001
            # 不吞异常：多币 K 线切换失败时要把原因留在载荷里，否则前端只看到「图没变」，
            # 无法区分「该标的本来没数据」与「后端取数出错」。
            chart = dict(chart) if chart else {}
            chart["candles_error"] = f"{type(exc).__name__}: {exc}"

    # FT-REPLAY-01：研究期 run 的 chart.json 故意不落 K 线 ⇒ 读时按本 run 自己的
    # 标的与窗口从市场库取（只补 candles，不动 signals/confirms/trades，不写回目录）。
    # `focus_ms`（可选）按笔聚焦：一个 run 装的是该「参数组 × 币种」的全部成交，
    # 成交可能分散在几年里 ⇒ 不聚焦时只给末段，聚焦时给该笔前后各半。
    if chart is not None and not (chart.get("candles") or []):
        try:
            _focus = params.get("focus_ms")
            if isinstance(_focus, list):
                _focus = _focus[0] if _focus else None
            try:
                _focus = int(_focus) if _focus not in (None, "") else None
            except (TypeError, ValueError):
                _focus = None
            chart = _fill_candles_from_market(run_id, doc, chart, focus_ms=_focus)
        except Exception as exc:                           # noqa: BLE001
            chart["candles_note"] = (f"补 K 线失败（{type(exc).__name__}: {exc}）"
                                     f"⇒ 留空，不伪造")
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
    # 元数据收口：K 线有了就别说「K 线为空」（WORK-DYNAMIC-BT-02）
    chart = _finalize_chart_meta(chart, doc, req_sym=req_sym, req_tf=req_tf)
    # 只读透传状态机档案和成交事实；不从 candles 推断事件。事件按当前图表时间窗裁剪。
    try:
        event_symbol = str(chart.get("symbol") or req_sym or "")
        chart_events = CH.events_from_run_dir(S.read_run_dir(run_id), event_symbol, run_id)
        window = chart.get("window") or {}
        lo, hi = window.get("start_ms"), window.get("end_ms")
        chart_events.extend(CH.trade_events_from_rows(
            chart.get("trades") or [], event_symbol, req_tf, run_id))
        chart["events"] = [e for e in chart_events
            if (not isinstance(lo, (int, float)) or e["event_time"] >= lo)
            and (not isinstance(hi, (int, float)) or e["event_time"] <= hi)]
        chart["events_status"] = "available" if chart["events"] else "empty_or_unrecorded"
        chart["events_unavailable"] = [{"event_type": "sweep_high",
            "reason": "当前 WX_EVENT_TREND 状态机是 long-only，仅产出 sweep_low；无真实扫高事件源"}]
    except Exception as exc:  # noqa: BLE001
        chart["events"] = []
        chart["events_status"] = "unavailable"
        chart["events_error"] = f"{type(exc).__name__}: {exc}"
    # 上下文行情（可选，默认 0 = 保持原口径）：把时间窗扩到 run 消费区间之外
    _ctx_days = _parse_context_days(params)
    if _ctx_days:
        try:
            chart = _apply_candle_context(run_id, doc, chart, _ctx_days)
        except Exception as exc:                           # noqa: BLE001
            chart = dict(chart)
            chart["candles_context_note"] = (
                f"上下文补齐失败（{type(exc).__name__}: {exc}）⇒ 只保留 run 区间")
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


CURRENT_STRATEGY_ID = "WX_EVENT_TREND_V0_1"


def _runnable_gate() -> Optional[str]:
    """**策略可运行性硬闸**（WORK-04）：返回拒绝原因，或 `None`（放行）。

    唯一可运行策略是 `WX_OI_RESONANCE_V1`；旧 3a–3d / A / B 已退役。
    注册表不可用时不静默放行 —— 返回原因并拒绝创建。
    """
    try:
        from strategies import registry as _reg
        return _reg.guard_runnable(CURRENT_STRATEGY_ID)
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
    # 复用现有窗口/标的/周期校验；当前执行规则由已批准视图冻结。
    incoming = dict(payload or {})
    if incoming.get("strategy_id", CURRENT_STRATEGY_ID) != CURRENT_STRATEGY_ID:
        return _envelope(missing_reason="仅接受当前事件策略"), 400
    if incoming.get("signal_timeframe", "1h") != "1h" or incoming.get("exec_timeframe", "15m") != "15m":
        return _envelope(missing_reason="仅验证 1h 信号与 15m 执行，禁止静默改周期"), 400
    if incoming.get("params"):
        return _envelope(missing_reason="收尾基线使用已批准 FT-LOW4H 配置，暂不接受参数覆盖"), 400
    incoming["strategy_id"] = BR.STRATEGY_ID
    incoming.pop("experiment", None)
    clean, errs = BR.validate_run_spec(incoming)
    from oi_resonance_bridge import validate_window
    clean["window"], window_errors = validate_window(incoming)
    errs.extend(window_errors)
    if len(clean.get("symbols") or []) != 1:
        errs.append("当前基线仅支持单标的，禁止静默丢弃其它标的")
    clean["strategy_id"] = CURRENT_STRATEGY_ID
    clean["window_start"] = incoming.get("window_start")
    clean["window_end"] = incoming.get("window_end")
    if (clean.get("window") or {}).get("mode") != "explicit":
        errs.append("FT-LOW4H 回测必须指定起止窗口")
    import paper_engine as _PE
    import experiment_registry as _EXP
    frozen = _PE.approved_params()
    if not frozen.get("_config_valid"):
        errs.append("已批准 FT-LOW4H 配置不可读")
    clean["initial_equity"] = frozen.get("initial_equity", 5000.0)
    # 契约只从 `experiment_registry.default_execution_contract()` 取一份：
    # 登记侧（current_design_parts）用的是同一个函数，两侧身份才能对上。
    clean["execution_contract"] = _EXP.default_execution_contract() or {}
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
    cfg_actual = {"params": {}, "actual_config": {
        "initial_equity": clean["initial_equity"], **clean["execution_contract"]}}
    doc = {
        "run_id": run_id, "strategy_id": CURRENT_STRATEGY_ID, "status": S.STATUS_QUEUED,
        "spec": clean, "spec_sha256": sh,
        "config": cfg_actual,
        "engine": BR.version_block(),
        "contract_version": CONTRACT_VERSION,
        "requested_at": S.utc_now_iso(),
        "live_order_enabled": False,
        "note": "FT-LOW4H 冻结配置；B 变体关闭 OI 门槛；仅研究模拟",
    }
    try:
        S.create_run(run_id, doc)
    except S.RunExists as exc:
        return _envelope(missing_reason=str(exc)), 409

    # 受理说明：**按实验真实模式**写，不把正式实验标成「研究模式」。
    #   `mode=research_backtest` 指的是「本接口只跑回测、不下实盘单」，
    #   与「实验是否有正式资格」是两件事，不能混成一句话。
    _is_formal_exp = str(exp_gate.get("mode") or "").lower() == "formal"
    _deliver_ok = bool(exp_gate.get("delivery_eligible")) if _is_formal_exp else False
    _accept_note = (
        "（正式实验：已通过启动闸门；结果交付资格见 experiment.delivery_eligible / "
        "delivery_blockers，未交付前不得宣称正式结论）" if _is_formal_exp
        else "（研究模式：本 run 不具正式实验资格，见 experiment.formal_blockers）")

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
                         formal_eligible=_deliver_ok,
                         note=("已受理并写入任务队列，由独立工作进程执行；"
                               "用 GET /api/v1/backtests/{id} 轮询状态" + _accept_note)), 202

    th = threading.Thread(target=_run_in_thread, args=(run_id,),
                          name=f"bt-{run_id}", daemon=True)
    with _THREADS_LOCK:
        _THREADS[run_id] = th
    th.start()
    return _envelope(run_id=run_id, status=S.STATUS_QUEUED, items=[_summary(doc)],
                     total=1,
                     experiment=exp_gate,
                     holdout=hold_check,
                     formal_eligible=_deliver_ok,
                     note="已受理，后台执行；用 GET /api/v1/backtests/{id} 轮询状态" + _accept_note), 202


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
        return get_chart(rid, params=params)
    if sub == "quality":
        return get_quality(rid)
    if sub == "orders":
        return get_orders(rid)
    if sub == "fills":
        return get_fills(rid)
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
