# -*- coding: utf-8 -*-
"""回测**批次**只读通道（`/api/v1/backtests/batches[...]`）。

为什么需要这一层
────────────────
回测的真实产物不是「一个 run」，而是**一次批量运行**：528 个标的同窗口跑一遍，
落成 `回测日志/event_trend_v0_1/runs_sep_all_latest/` 这样一个批次目录。
旧页面只认 `runs/`（单 run 临时目录），于是：

· 批次目录（`runs_aug100` / `runs_sep50` / `runs_sep_all_latest` …）**根本不在列表里**；
· 页面拿「当前 run 的逐笔」与 `public/all_trades.json`（旧批次的 70 行静态快照）并排显示
  ⇒ 批次列表、汇总、逐笔、图表**不是同一批数据**。

本模块只做一件事：**把「批次」当成一等读取对象**，让列表 / 汇总 / 逐笔 / 图表
全部锚定到同一个 `batch_id` + 同一个 `run_id`，并且每个数字都能指回落盘文件。

纪律
────
· **只读**：只打开 run 目录下的 `summary.json` / `inputs.json` / `trades.jsonl` /
  `chart.json` / `run.json`，绝不写入、绝不改账本、绝不触发计算。
· **不合成**：批次净盈亏 = 各 run `summary_variant_a.net_pnl` 直接相加；
  组合回撤/夏普**不在此处计算**（需要组合权益曲线，另走 `/metrics` 与专项审计），
  缺失一律 `None`，不用 0 冒充。
· **可核对**：每个批次都带 `derived_from`（数据来自哪些文件）与
  `checks`（汇总与逐笔行数是否自洽）；不自洽就如实标 `mismatch`。
· 缓存 300s（可用 `?refresh=1` 强制重算）：528 个 run 的 summary 解析要 2–3 秒，
  30s 的 TTL 会让「页面停一会儿再点」每次都重新付一遍这笔钱（实测首屏 18s），
  而批次目录是**已落盘的批量产物**，5 分钟内不变。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCH_ROOT = Path(
    __import__("os").environ.get(
        "EVENT_TREND_BATCH_ROOT",
        str(PROJECT_ROOT / "回测日志" / "event_trend_v0_1"),
    )
)

CACHE_TTL_S = 300.0
_CACHE: Dict[str, Any] = {}
_CACHE_AT: Dict[str, float] = {}
_LOCK = threading.Lock()
_BUILD_LOCKS: Dict[str, threading.Lock] = {}

# 批次目录名：`runs` 本体 + `runs_*` 子批次。只认这个前缀，避免把 reconstructed
# 等人工目录混进来（它们不是一次批量运行）。
BATCH_PREFIX = "runs"


# ══════════════════════════ 基础读取 ══════════════════════════

def _read_json(p: Path) -> Optional[dict]:
    try:
        with p.open("r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _read_jsonl(p: Path) -> List[dict]:
    out: List[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    out.append(v)
    except OSError:
        return []
    return out


def _int_or_none(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _num_or_none(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _batch_dir(batch_id: str) -> Path:
    """批次目录（**只允许** BATCH_ROOT 下一层；拒绝路径穿越）。"""
    if not batch_id or "/" in batch_id or "\\" in batch_id or batch_id in (".", ".."):
        raise KeyError(batch_id)
    if not batch_id.startswith(BATCH_PREFIX):
        raise KeyError(batch_id)
    p = (BATCH_ROOT / batch_id).resolve()
    root = BATCH_ROOT.resolve()
    if root not in p.parents and p != root:
        raise KeyError(batch_id)
    if not p.is_dir():
        raise KeyError(batch_id)
    return p


def batch_ids() -> List[str]:
    if not BATCH_ROOT.is_dir():
        return []
    out = [
        d.name
        for d in BATCH_ROOT.iterdir()
        if d.is_dir() and d.name.startswith(BATCH_PREFIX) and not d.name.startswith(".")
    ]
    # `runs` 是默认运行根（单 run 临时目录，含少量人工/测试目录）；批次目录排在前面
    out.sort(key=lambda n: (n == "runs", n))
    return out


# ══════════════════════════ 单 run 事实 ══════════════════════════

def _run_facts(run_dir: Path, need_window: bool = False) -> Dict[str, Any]:
    """一个 run 目录的只读事实。缺文件 ⇒ 对应字段 `None`，不编造。

    `need_window=False` 时**不读 `inputs.json`**：实测每次 open 约 4ms（沙箱文件系统），
    2130 个 run 各多读一个 10KB 文件就多 10 秒。窗口只在批次级取一次（同批次同窗口），
    单 run 窗口由 `get_run` / `/chart` 提供。

    `run.json` 只在 `summary.json` 缺失时才读其内容（多数 run 两者都有，省一次解析）；
    但**存在性**始终检查（CODE-REVIEW-02：无 run.json ⇒ registered=false，页面显示未登记）。
    """
    summary = _read_json(run_dir / "summary.json")
    run_doc: Dict[str, Any] = {}
    if summary is None:
        run_doc = _read_json(run_dir / "run.json") or {}
    summary = summary or {}
    inputs: Dict[str, Any] = {}
    if need_window:
        inputs = _read_json(run_dir / "inputs.json") or {}

    sa = summary.get("summary_variant_a") or summary.get("summary") or {}
    # FT-REPLAY-01：研究期登记的 run **没有 summary.json**（没跑引擎），数字只在
    # `run.json.metrics` 里（按上游合并成交表逐笔求和）。这里只做**同一份落盘数字**的
    # 读取位置回退，不做任何推算：summary 存在就绝不用 metrics 覆盖。
    if not sa:
        _m = run_doc.get("metrics")
        if isinstance(_m, dict) and _m:
            sa = _m
    net_pnl = _num_or_none(sa.get("net_pnl"))
    # ⚠ 不能写 `sa.get("trade_count") or sa.get("trades_count")`：真实值 **0** 是假值，
    # 会被 `or` 吃掉变成 None ⇒ 516 个「确实 0 笔」的 run 在页面上显示成「笔数 —」，
    # 看起来像字段缺失。必须显式判 None。
    _tc = sa.get("trade_count")
    if _tc is None:
        _tc = sa.get("trades_count")
    trade_count = _int_or_none(_tc)

    ds = (inputs.get("data_summary") or {}) if isinstance(inputs.get("data_summary"), dict) else {}
    tr = ds.get("time_range_1h") or {}
    win_start = _int_or_none(tr.get("start_ms"))
    win_end = _int_or_none(tr.get("end_ms"))
    # 研究期 run 没有 inputs.json ⇒ 窗口在 `run.json.window`（同一份落盘事实）
    if need_window and win_start is None:
        _w = run_doc.get("window")
        if isinstance(_w, dict):
            win_start = _int_or_none(_w.get("win_start_ms"))
            win_end = _int_or_none(_w.get("win_end_ms"))

    trades_path = run_dir / "trades.jsonl"
    has_trades = trades_path.is_file() and trades_path.stat().st_size > 2
    # CODE-REVIEW-02：run.json 存在性单独做事实（历史批量产物没有 ⇒ registered=false，
    # 页面显示「未登记」，不把读时推断的结果冒充成正式运行档案）。
    has_run_json = (run_dir / "run.json").is_file()
    # FT-REPLAY-01：研究期结果必须能一眼看出来。判据只认落盘标记（`summary.spec_status`
    # 或 `run.json.research_period`），不做任何按名字/时间的猜测。
    is_research = (
        summary.get("spec_status") == "research_import"
        or bool(run_doc.get("research_period"))
    )

    # [FIX-002] 账本隔离：将旧版回测、规格未证明或未获正式放行的运行统一标记为历史研究数据
    is_hist_only = (
        is_research
        or run_doc.get("historical_research_only") is True
        or summary.get("historical_research_only") is True
        or sa.get("formal_pass_allowed") is False
        or summary.get("formal_pass_allowed") is False
    )

    return {
        "run_id": run_dir.name,
        "symbol": summary.get("symbol") or inputs.get("symbol") or run_doc.get("symbol"),
        "status": run_doc.get("status") or ("succeeded" if summary else "unknown"),
        "net_pnl": net_pnl,
        "trade_count": trade_count,
        "initial_equity": _num_or_none(sa.get("initial_equity")),
        "final_equity": _num_or_none(sa.get("final_equity")),
        "fee_total": _num_or_none(sa.get("fee_total")),
        "funding_total": _num_or_none(sa.get("funding_total")),
        "formal_pass_allowed": sa.get("formal_pass_allowed"),
        "window_start_ms": win_start,
        "window_end_ms": win_end,
        "has_summary": bool(summary),
        "has_run_json": has_run_json,
        "has_trades": has_trades,
        "has_chart": (run_dir / "chart.json").is_file(),
        "research_period": is_research,
        "historical_research_only": is_hist_only,
        "funnel": summary.get("funnel") if isinstance(summary.get("funnel"), dict) else None,
    }


def _drop(key: str) -> None:
    """强制重算前清缓存（`?refresh=1`）。"""
    with _LOCK:
        _CACHE.pop(key, None)
        _CACHE_AT.pop(key, None)


def _cached(key: str, builder):
    """带 TTL 的缓存 + **同 key 只构建一次**。

    为什么需要「只构建一次」：启动预热线程与首个请求会同时触发 528 个 run 的扫描，
    没有这把锁就是两份工作串着做（实测首屏 24s 而不是 12s）。
    """
    now = time.time()
    with _LOCK:
        if key in _CACHE and now - _CACHE_AT.get(key, 0.0) < CACHE_TTL_S:
            return _CACHE[key]
        lk = _BUILD_LOCKS.setdefault(key, threading.Lock())
    with lk:
        now = time.time()
        with _LOCK:
            if key in _CACHE and now - _CACHE_AT.get(key, 0.0) < CACHE_TTL_S:
                return _CACHE[key]
        val = builder()
        with _LOCK:
            _CACHE[key] = val
            _CACHE_AT[key] = now
        return val


# ══════════════════════════ 批次汇总 ══════════════════════════

def _manifest_facts(batch_id: str, bdir: Path, m: Dict[str, Any],
                    mtime_ms: Optional[int], *, full: bool) -> Dict[str, Any]:
    """清单型批次事实（BACKFILL-01）。

    数字全部来自目录内的 `batch_meta.json` —— 该文件本身由 `runs.jsonl` 逐行现算，
    不含任何估算。这类批次**没有 run 子目录**，所以 `runs_rows` 恒为空；
    逐次明细如实指向 `source.runs_jsonl`，不伪造单 run 档案。
    """
    def _i(k: str) -> Optional[int]:
        v = m.get(k)
        return int(v) if isinstance(v, (int, float)) else None

    def _f(k: str) -> Optional[float]:
        v = m.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    out: Dict[str, Any] = {
        "batch_id": batch_id,
        "path": str(bdir),
        "generated_at_ms": m.get("generated_at_ms") or mtime_ms,
        "title": m.get("title"),
        "runs_total": _i("runs_total"),
        "runs_with_summary": None,
        "runs_missing_summary": None,
        "runs_with_trades": None,
        "runs_win": None,
        "runs_loss": None,
        "trades_total": None,
        "net_pnl_sum": None,
        "net_pnl_runs_counted": None,
        "fee_total_sum": None,
        "funding_total_sum": None,
        "window_start_ms": _i("window_start_ms"),
        "window_end_ms": _i("window_end_ms"),
        "window_source": "batch_meta.json（由 runs.jsonl 的 start/end 现算）",
        "symbols_count": _i("symbols_count"),
        "formal_pass_allowed_any": None,
        "historical_research_only": True,
        "funnel_sum": None,
        "derived_from": m.get("derived_from"),
        "is_default_root": False,
        "manifest_batch": True,
        "groups": _i("groups"),
        "source": m.get("source"),
        "summary_loaded": False,
        "note": m.get("note"),
    }
    if not full:
        return out
    out.update({
        "runs_with_summary": _i("runs_ok"),
        "runs_missing_summary": _i("runs_no_data"),
        "runs_with_trades": _i("runs_with_trades"),
        "runs_win": _i("runs_win"),
        "runs_loss": _i("runs_loss"),
        "trades_total": _i("trades_total"),
        "net_pnl_sum": _f("net_pnl_sum"),
        "net_pnl_runs_counted": _i("runs_ok"),
        "fee_total_sum": _f("fee_total_sum"),
        "funding_total_sum": _f("funding_total_sum"),
        "summary_loaded": True,
        # 没有 run 子目录 ⇒ 不编造 run 清单
        "runs_rows": [],
        "note": ((m.get("note") or "") +
                 "；本批次无逐 run 目录 ⇒ 运行清单为空，逐次明细见 source.runs_jsonl"),
    })
    return out


def _batch_facts(batch_id: str, full: bool = True) -> Dict[str, Any]:
    """批次事实。

    `full=False`（列表用）只做一次 `scandir` 数目录 —— 1726 个 run 各 open 两个 JSON
    在沙箱文件系统上要 12 秒，而列表只需要「有哪些批次、各多少 run」。
    成交/净盈亏等汇总由 `/batches/{id}/summary` 懒算（选中哪个算哪个）。
    """
    bdir = _batch_dir(batch_id)
    run_dirs = sorted([d for d in bdir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    try:
        mtime_ms = int(bdir.stat().st_mtime * 1000)
    except OSError:
        mtime_ms = None

    # ── 清单型批次（BACKFILL-01）──────────────────────────────────────────────
    # 有些批量跑（如全币种压测 19496 次）**刻意不落 run 目录**：两万个目录约 36GB，
    # 磁盘放不下，结果只写 runs.jsonl。这类批次用 `batch_meta.json` 声明真实数字
    # （由 runs.jsonl 现算），本模块只读它、不臆造逐 run 明细。
    manifest = _read_json(bdir / "batch_meta.json")
    if isinstance(manifest, dict) and manifest and not run_dirs:
        return _manifest_facts(batch_id, bdir, manifest, mtime_ms, full=full)

    if not full:
        return {
            "batch_id": batch_id,
            "path": str(bdir),
            "generated_at_ms": mtime_ms,
            "runs_total": len(run_dirs),
            "runs_with_summary": None,
            "runs_missing_summary": None,
            "runs_with_trades": None,
            "runs_win": None,
            "runs_loss": None,
            "trades_total": None,
            "net_pnl_sum": None,
            "net_pnl_runs_counted": None,
            "fee_total_sum": None,
            "funding_total_sum": None,
            "window_start_ms": None,
            "window_end_ms": None,
            "window_source": None,
            "symbols_count": None,
            "formal_pass_allowed_any": None,
            "funnel_sum": None,
            "derived_from": None,
            "is_default_root": batch_id == "runs",
            "summary_loaded": False,
            "note": "轻量列表（只数 run 目录）；成交/净盈亏由 /batches/{id}/summary 懒算",
        }

    # 并发读：528 个 run 的 summary.json 串行解析要 20+ 秒，8 线程降到 2–4 秒
    if len(run_dirs) > 16:
        with ThreadPoolExecutor(max_workers=8) as ex:
            facts = list(ex.map(_run_facts, run_dirs))
    else:
        facts = [_run_facts(d) for d in run_dirs]

    with_summary = [f for f in facts if f["has_summary"]]
    with_trades = [f for f in facts if (f["trade_count"] or 0) > 0]

    # 净盈亏：只累加**有 summary 且有值**的 run；缺失单独计数（不按 0 计）
    nets = [f["net_pnl"] for f in with_summary if f["net_pnl"] is not None]
    fees = [f["fee_total"] for f in with_summary if f["fee_total"] is not None]
    fund = [f["funding_total"] for f in with_summary if f["funding_total"] is not None]

    wins = [f for f in with_summary if (f["net_pnl"] or 0) > 0]
    losses = [f for f in with_summary if (f["net_pnl"] or 0) < 0]

    # 窗口：同批次理应同窗口 ⇒ 只读**一个** run 的 inputs.json（省 10 秒 I/O）。
    # 若首个 run 没有 inputs，再试后面 3 个；都没有 ⇒ None（不猜）。
    win_start = win_end = None
    window_source: Optional[str] = None
    for d in run_dirs[:4]:
        probe = _run_facts(d, need_window=True)
        if probe["window_start_ms"] is not None and probe["window_end_ms"] is not None:
            win_start, win_end = probe["window_start_ms"], probe["window_end_ms"]
            window_source = f"inputs.json(data_summary.time_range_1h) · 取样 run={d.name}"
            break

    funnel_sum: Dict[str, int] = {}
    for f in with_summary:
        for k, v in (f["funnel"] or {}).items():
            if isinstance(v, int):
                funnel_sum[k] = funnel_sum.get(k, 0) + v

    trades_total = sum((f["trade_count"] or 0) for f in with_summary)

    # run 清单**与汇总同一次扫描产出**：早先 `/runs` 与 `/summary` 各扫一遍 528 个目录，
    # 首屏要多等一个 1.5–17 秒；现在共用一份缓存（`runs_rows` 只在服务端用，不回给客户端）。
    runs_rows = [{
        "run_id": f["run_id"],
        "batch_id": batch_id,
        "symbol": f["symbol"],
        "status": f["status"],
        "trades_count": f["trade_count"],
        "net_pnl": f["net_pnl"],
        "net_return_pct": (
            round(f["net_pnl"] / f["initial_equity"] * 100.0, 6)
            if (f["net_pnl"] is not None and f["initial_equity"]) else None
        ),
        "has_trades": f["has_trades"],
        "has_chart": f["has_chart"],
        # CODE-REVIEW-02：未登记标记透传（无 run.json 的历史批量产物）
        "registered": f["has_run_json"],
        # FT-REPLAY-01：研究期重建标记（不是本引擎成绩，不能算进当前策略）
        "research_period": f.get("research_period", False),
        "historical_research_only": f.get("historical_research_only", False),
    } for f in facts]

    return {
        "batch_id": batch_id,
        "path": str(bdir),
        "generated_at_ms": mtime_ms,
        "summary_loaded": True,
        "runs_rows": runs_rows,
        "runs_total": len(facts),
        "runs_with_summary": len(with_summary),
        "runs_missing_summary": len(facts) - len(with_summary),
        "runs_with_trades": len(with_trades),
        "runs_win": len(wins),
        "runs_loss": len(losses),
        "trades_total": trades_total,
        "net_pnl_sum": round(sum(nets), 8) if nets else None,
        "net_pnl_runs_counted": len(nets),
        "fee_total_sum": round(sum(fees), 8) if fees else None,
        "funding_total_sum": round(sum(fund), 8) if fund else None,
        "window_start_ms": win_start,
        "window_end_ms": win_end,
        "window_source": window_source,
        "symbols_count": len({f["symbol"] for f in facts if f["symbol"]}),
        "formal_pass_allowed_any": any(f["formal_pass_allowed"] is True for f in with_summary),
        "historical_research_only": not any(f["formal_pass_allowed"] is True for f in with_summary),
        "funnel_sum": funnel_sum or None,
        "derived_from": "各 run 的 summary.json(summary_variant_a) + inputs.json(data_summary.time_range_1h) 现算",
        "is_default_root": batch_id == "runs",
        "note": (
            manifest.get("note") if (isinstance(manifest, dict) and manifest.get("note")) else (
                "批次净盈亏 = 各 run net_pnl 直接相加（美元）；"
                "组合回撤/夏普不在此处合成（需组合权益曲线，另见单 run /metrics 与专项审计）"
            )
        ),
    }


def list_batches(force: bool = False) -> dict:
    items = _batch_facts_all(force)
    return {
        "items": items,
        "total": len(items),
        "batch_root": str(BATCH_ROOT),
        "batch_root_exists": BATCH_ROOT.is_dir(),
        "derived_from": "批次目录（回测日志/event_trend_v0_1/runs*）目录结构（轻量）",
        "note": ("批次是「一次批量运行」的真实目录；`runs` 为默认运行根（含单 run 临时目录）。"
                 "成交/净盈亏等汇总由 /batches/{id}/summary 懒算，避免列表一次读 1700+ 个 JSON"),
    }


def _batch_facts_all(force: bool = False) -> List[dict]:
    key = "batches:all"
    if force:
        with _LOCK:
            _CACHE.pop(key, None)
    return _cached(key, lambda: _build_all())


def _build_all() -> List[dict]:
    items = [_batch_facts(b, full=False) for b in batch_ids()]
    items.sort(
        key=lambda x: (x.get("window_end_ms") or 0, x.get("generated_at_ms") or 0),
        reverse=True,
    )
    return items


def batch_meta(batch_id: str, force: bool = False) -> dict:
    """单批次**完整**事实（含成交/净盈亏/窗口），按批次缓存 300s（`force` 强制重算）。"""
    full = _batch_full(batch_id, force=force)
    # `runs_rows` 是服务端中间产物（run 清单），不回给客户端
    return {k: v for k, v in full.items() if k != "runs_rows"}


def _batch_full(batch_id: str, force: bool = False) -> dict:
    key = f"batch:{batch_id}:meta"
    if force:
        _drop(key)
    return _cached(key, lambda: _batch_facts(batch_id, full=True))


def warm_all() -> None:
    """预热：批次**清单**（轻量）+ 页面会默认选中的那个批次（run 最多的非默认根）。

    为什么不再逐批预热完整汇总：8 个批次合计 2130 个 run，预热线程会把磁盘和 GIL
    占满 20+ 秒，反而把用户的首个请求堵在后面（实测首屏 19 秒才出图）。
    只热一个批次 ⇒ 首屏等的是 2–3 秒，其它批次按需懒算。
    """
    try:
        items = list_batches(force=True)["items"]
    except Exception:                                     # noqa: BLE001
        return
    real = [b for b in items if not b.get("is_default_root")] or items
    if not real:
        return
    best = max(real, key=lambda b: (b.get("runs_total") or 0))
    try:
        batch_meta(best["batch_id"])
    except Exception:                                     # noqa: BLE001
        pass


def get_batch(batch_id: str) -> Tuple[dict, int]:
    try:
        _batch_dir(batch_id)
    except KeyError:
        return {"batch_id": batch_id, "missing_reason": "未知批次（只允许 runs / runs_* 目录）"}, 404
    try:
        meta = batch_meta(batch_id)
    except Exception as exc:                              # noqa: BLE001
        return {"batch_id": batch_id, "missing_reason": f"批次读取失败：{exc}"}, 500
    return {"batch": meta, "missing_reason": None}, 200


def batch_runs(batch_id: str, force: bool = False) -> Tuple[dict, int]:
    try:
        _batch_dir(batch_id)
    except KeyError:
        return {"batch_id": batch_id, "items": [], "total": 0,
                "missing_reason": "未知批次（只允许 runs / runs_* 目录）"}, 404

    # 与 `/summary` **共用同一份缓存**（同一批目录只扫一次）
    full_doc = _batch_full(batch_id, force=force)
    items = full_doc.get("runs_rows") or []
    body = {
        "batch_id": batch_id,
        "items": items,
        "total": len(items),
        "derived_from": "各 run 目录 summary.json / run.json 现算（窗口见 /batches/{id} 汇总）",
        "note": "同一 batch_id 下每个 run 一个标的；trades_count 取自 summary_variant_a.trade_count",
    }
    # 清单型批次（BACKFILL-01）：本来就没有 run 目录，空清单是事实而不是故障
    if full_doc.get("manifest_batch"):
        body["manifest_batch"] = True
        body["missing_reason"] = (
            "清单型批次：该批量跑**不落 run 目录**（磁盘容量所限），"
            "逐次明细见 batch_meta.json 的 source.runs_jsonl"
        )
        body["source"] = full_doc.get("source")
    return body, 200


# ══════════════════════════ 批次逐笔 ══════════════════════════

def _chart_index(run_dir: Path) -> Tuple[Dict[str, int], Dict[Tuple[str, int], str]]:
    """`chart.json` 的两个索引：

    · `signal_id → 信号时刻(ms)`（交叉核对 signal_id 尾段）
    · `(symbol, entry_t) → trade_id`（`trades.jsonl` 里没有 trade_id，
      图载荷有 —— 这是唯一不需要猜的补法：symbol + 入场毫秒必须同时命中）

    文件不存在或解析失败 ⇒ 两个空字典（调用方据此如实留空，不编造）。
    """
    p = run_dir / "chart.json"
    if not p.is_file():
        return {}, {}
    doc = _read_json(p)
    if not doc:
        return {}, {}
    sig: Dict[str, int] = {}
    for s in doc.get("signals") or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("signal_id")
        t = _int_or_none(s.get("t"))
        if sid and t is not None and t > 0:
            sig[str(sid)] = t
    tid: Dict[Tuple[str, int], str] = {}
    sym = doc.get("symbol")
    for tr in doc.get("trades") or []:
        if not isinstance(tr, dict):
            continue
        entry = tr.get("entry") or {}
        et = _int_or_none(entry.get("t")) if isinstance(entry, dict) else None
        v = tr.get("trade_id")
        s2 = tr.get("symbol") or sym
        if v and et is not None and s2:
            tid[(str(s2), et)] = str(v)
    return sig, tid


def _signal_time_from_id(signal_id: Optional[str]) -> Optional[int]:
    """从候选 ID 的**尾段**取信号时刻。

    真实 ID 形态（`candidates.jsonl` / `trades.jsonl` 一致）：
      `cand_ev_WX_EVENT_TREND_V0_1_binance_<SYMBOL>_<box_start_ms>_<signal_ms>`
    尾段就是信号 bar 的毫秒时刻。这是**结构化字段解析**，不是猜：
    解析值必须与 `chart.signals[].t` 一致才采用（见 `_trade_row`）。
    """
    if not signal_id:
        return None
    tail = str(signal_id).rsplit("_", 1)
    if len(tail) != 2:
        return None
    v = _int_or_none(tail[1])
    # 只接受合理的毫秒时刻（2001-09-09 ~ 2286 年），避免把别的编号误当时间
    if v is None or not (1_000_000_000_000 <= v <= 10_000_000_000_000):
        return None
    return v


def _ledger_trade_index(run_dir: Path) -> Dict[Tuple[int, float], str]:
    """`(平仓时刻, 净盈亏) → trade_id`，取自 `positions.jsonl` 的 `POSITION_CLOSED`。

    为什么需要（BT-UI-02）：`runs/` 批次是项目根的批量脚本跑的，它们的
    `trades.jsonl` 里**根本没有 `trade_id` 字段**（实测 666 笔全为 `None`）；
    旧兜底只从 `chart.json` 的 `trades[]` 换，于是那 469 个没有 `chart.json` 的 run
    永远拿不到交易号，页面上「交易 ID」整列是「—」。

    账本 `positions.jsonl` 的 `POSITION_CLOSED.payload.trade_id` 才是权威来源。
    对齐用**两个字段同时命中**（平仓时刻 + 净盈亏），比只对时刻更不容易串笔；
    同一组键出现两次 ⇒ 视为不唯一，**宁可不给也不猜**。
    """
    seen: Dict[Tuple[int, float], Optional[str]] = {}
    for e in _read_jsonl(run_dir / "positions.jsonl"):
        if not isinstance(e, dict) or e.get("event_type") != "POSITION_CLOSED":
            continue
        payload = e.get("payload") or {}
        tid = payload.get("trade_id")
        t = _int_or_none(e.get("event_time"))
        pnl = _num_or_none(payload.get("net_pnl"))
        if not tid or t is None or pnl is None:
            continue
        k = (t, round(float(pnl), 8))
        seen[k] = None if k in seen else str(tid)      # 第二次出现 ⇒ 标为不唯一
    return {k: v for k, v in seen.items() if v}


def _trade_row(run_dir: Path, batch_id: str, t: dict,
               sig_index: Optional[Dict[str, int]],
               tid_index: Optional[Dict[Tuple[str, int], str]] = None,
               ledger_index: Optional[Dict[Tuple[int, float], str]] = None) -> Dict[str, Any]:
    symbol = t.get("symbol")
    entry_obj = t.get("entry") if isinstance(t.get("entry"), dict) else {}
    exit_obj = t.get("exit") if isinstance(t.get("exit"), dict) else {}
    # 时间字段：官方 runner 写 `entry_time_ms/exit_time_ms`，项目根批量脚本写
    # `entry_time/exit_time`（同样是毫秒）。少认后一组 ⇒ `exit_ms` 恒为 None，
    # 账本对齐（平仓时刻+净盈亏）整条失效、交易号整列拿不到（BT-UI-02 实测 469 个 run）。
    entry_ms = _int_or_none(t.get("entry_time_ms") or entry_obj.get("t")
                            or t.get("entry_time") or t.get("open_timestamp"))
    exit_ms = _int_or_none(t.get("exit_time_ms") or exit_obj.get("t")
                           or t.get("exit_time") or t.get("close_timestamp"))
    stop_trace = t.get("stop_trace") or []
    init_stop = None
    init_stop_reason = None
    for p in stop_trace:
        if not isinstance(p, dict):
            continue
        v = _num_or_none(p.get("stop"))
        if v is None:
            continue
        init_stop = v
        init_stop_reason = p.get("reason")
        break  # stop_trace 首点即初始止损（引擎实况记录，不重建）

    sid = t.get("signal_id")
    sig_from_id = _signal_time_from_id(sid)
    sig_from_chart = sig_index.get(str(sid)) if (sig_index and sid) else None
    if sig_from_chart is not None:
        signal_ms = sig_from_chart
        signal_src = "chart.signals[].t（按 signal_id 命中）"
    elif sig_from_id is not None:
        signal_ms = sig_from_id
        signal_src = "signal_id 尾段（chart.signals 未命中/无图载荷）"
    else:
        signal_ms = None
        signal_src = None

    fees = _num_or_none(t.get("fees"))
    if fees is None:
        ef = _num_or_none(t.get("entry_fee")) or 0.0
        xf = _num_or_none(t.get("exit_fee")) or 0.0
        fees = ef + xf if (t.get("entry_fee") is not None or t.get("exit_fee") is not None) else None

    # trade_id：`trades.jsonl` 本身可能不带 ⇒ 依次退两级，都是**唯一命中才采用**：
    #   ① 账本 `positions.jsonl` 的 `POSITION_CLOSED`（权威，按「平仓时刻 + 净盈亏」对齐）
    #   ② 图载荷 `chart.trades[]`（按 symbol + 入场毫秒对齐）
    # 绝不按行号编 `trd_1` 这种假号（UI-CRASH-01 的教训）。
    tid = t.get("trade_id")
    tid_src = "trades.jsonl" if tid else None
    if not tid and ledger_index and exit_ms is not None:
        _pnl = _num_or_none(t.get("net_pnl"))
        if _pnl is not None:
            hit = ledger_index.get((exit_ms, round(float(_pnl), 8)))
            if hit:
                tid = hit
                tid_src = "positions.jsonl(POSITION_CLOSED，按平仓时刻+净盈亏对齐)"
    if not tid and tid_index and symbol and entry_ms is not None:
        hit = tid_index.get((str(symbol), entry_ms))
        if hit:
            tid = hit
            tid_src = "chart.trades[]（symbol+入场毫秒唯一命中）"

    return {
        "batch_id": batch_id,
        "run_id": run_dir.name,
        "trade_id": tid,
        "trade_id_source": tid_src,
        "symbol": symbol,
        "direction": t.get("direction"),
        "signal_id": sid,
        "signal_time_ms": signal_ms,
        "signal_time_source": signal_src,
        "entry_time_ms": entry_ms,
        "entry_price": _num_or_none(t.get("entry_price") or entry_obj.get("price")),
        "initial_stop": (init_stop if init_stop is not None
                         else _num_or_none(t.get("initial_stop") or t.get("initial_stop_P0")
                                           or t.get("P0") or t.get("p0"))),
        "initial_stop_reason": init_stop_reason or "initial_P0",
        "exit_time_ms": exit_ms,
        "exit_price": _num_or_none(t.get("exit_price") or exit_obj.get("price")),
        "exit_reason": t.get("exit_reason") or exit_obj.get("reason"),
        "qty": _num_or_none(t.get("qty") or entry_obj.get("qty")),
        "gross_pnl": _num_or_none(t.get("gross_pnl")),
        "fees": fees,
        "funding_fee": _num_or_none(t.get("funding_fee")),
        "net_pnl": _num_or_none(t.get("net_pnl")),
        "mae": _num_or_none(t.get("mae")),
        "mfe": _num_or_none(t.get("mfe")),
        "holding_hours": (
            round((exit_ms - entry_ms) / 3_600_000.0, 6)
            if (entry_ms is not None and exit_ms is not None and exit_ms >= entry_ms) else None
        ),
        "stop_trace_points": len([p for p in stop_trace if isinstance(p, dict)]),
    }


def batch_trades(batch_id: str, limit: Optional[int] = None,
                 offset: int = 0, force: bool = False) -> Tuple[dict, int]:
    """批次内**全部真实成交**（逐 run 读 `trades.jsonl`，一行一笔）。"""
    try:
        bdir = _batch_dir(batch_id)
    except KeyError:
        return {"batch_id": batch_id, "items": [], "total": 0,
                "missing_reason": "未知批次（只允许 runs / runs_* 目录）"}, 404

    def build():
        dirs = [x for x in sorted(bdir.iterdir()) if x.is_dir() and not x.name.startswith(".")]

        def rows_for_run(d: Path):
            """单个 run 的逐笔行（IO 密集，交给线程池并发跑）。"""
            raw = _read_jsonl(d / "trades.jsonl")
            if not raw:
                return None
            # 只为**真有成交**的 run 读 chart.json（1MB 级），避免整批 500+ 文件全读
            sig_index, tid_index = _chart_index(d)
            # 账本索引只在 `trades.jsonl` 自己没带 trade_id 时才读（其它批次零额外开销）
            ledger_index = (_ledger_trade_index(d)
                            if any(not t.get("trade_id") for t in raw) else None)
            return [_trade_row(d, batch_id, t, sig_index, tid_index, ledger_index) for t in raw]

        rows: List[dict] = []
        runs_with_trades = 0
        missing_stop = 0
        unlinked_signal = 0
        # 8 线程并发：每个 run 要读 trades.jsonl + chart.json + positions.jsonl 三个文件，
        # 串行 580 个 run 实测 21 秒，并发后降到个位数秒。
        with ThreadPoolExecutor(max_workers=8) as ex:
            for got in ex.map(rows_for_run, dirs):
                if not got:
                    continue
                runs_with_trades += 1
                for row in got:
                    if row["initial_stop"] is None:
                        missing_stop += 1
                    if row["signal_time_ms"] is None:
                        unlinked_signal += 1
                    rows.append(row)
        rows.sort(key=lambda r: (r["entry_time_ms"] or 0, r["run_id"], r["trade_id"] or ""))
        return {
            "rows": rows,
            "runs_with_trades": runs_with_trades,
            "missing_stop": missing_stop,
            "unlinked_signal": unlinked_signal,
        }

    if force:
        _drop(f"batch:{batch_id}:trades")
    built = _cached(f"batch:{batch_id}:trades", build)
    rows = built["rows"]
    total = len(rows)
    off = max(0, int(offset or 0))
    lim = total if limit is None else max(1, int(limit))
    page = rows[off:off + lim]

    return {
        "batch_id": batch_id,
        "items": page,
        "total": total,
        "offset": off,
        "limit": lim,
        "runs_with_trades": built["runs_with_trades"],
        "missing_initial_stop": built["missing_stop"],
        "unlinked_signal_time": built["unlinked_signal"],
        "derived_from": "各 run 的 trades.jsonl（真实账本逐笔）+ chart.json(signals) 交叉核对",
        "note": ("initial_stop 取 trades.jsonl.stop_trace 首点（引擎实况，不重建）；"
                 "signal_time_ms 优先按 signal_id 命中 chart.signals[].t，未命中才用 signal_id 尾段并标注来源"),
    }, 200


def batch_summary(batch_id: str, force: bool = False) -> Tuple[dict, int]:
    """批次汇总 + **自洽性核对**（汇总的 trades_total 必须等于逐笔行数）。"""
    try:
        _batch_dir(batch_id)
    except KeyError:
        return {"batch_id": batch_id, "missing_reason": "未知批次（只允许 runs / runs_* 目录）"}, 404

    try:
        meta = batch_meta(batch_id, force=force)
    except Exception as exc:                              # noqa: BLE001
        return {"batch_id": batch_id, "missing_reason": f"批次读取失败：{exc}"}, 500

    # 清单型批次（BACKFILL-01）：没有 run 目录 ⇒ 没有逐笔可交叉核对。
    # 这里**不能**把 checks 算成 false（那会被读成「汇总与逐笔不符」），
    # 而应标「不适用」并说明数字来源。
    if meta.get("manifest_batch"):
        return {
            "batch_id": batch_id,
            "batch": meta,
            "trades": {
                "count": None, "wins": None, "losses": None, "flats": None,
                "win_rate_pct": None, "net_pnl_sum": None, "fees_sum": None, "funding_sum": None,
                "note": ("清单型批次不提供逐笔接口；成交笔数/净盈亏来自 batch_meta.json"
                         "（由 runs.jsonl 逐行现算），逐笔明细见 source.trades_jsonl"),
            },
            "checks": {
                "trades_total_matches_rows": None,
                "net_pnl_matches_rows": None,
                "summary_trades_total": meta.get("trades_total"),
                "trades_rows": None,
                "net_pnl_from_summary": meta.get("net_pnl_sum"),
                "net_pnl_from_rows": None,
                "note": "不适用：本批次无逐 run 目录，无法用逐笔行数交叉核对；数字来源为 runs.jsonl",
            },
            "derived_from": meta.get("derived_from"),
            "missing_reason": None,
        }, 200

    tr = batch_trades(batch_id, limit=10_000_000, offset=0, force=force)[0]
    rows = tr.get("items") or []
    net_from_rows = sum(r["net_pnl"] for r in rows if r["net_pnl"] is not None)
    wins = sum(1 for r in rows if (r["net_pnl"] or 0) > 0)
    losses = sum(1 for r in rows if (r["net_pnl"] or 0) < 0)
    flats = sum(1 for r in rows if (r["net_pnl"] or 0) == 0)

    checks = {
        "trades_total_matches_rows": (meta["trades_total"] == len(rows)),
        "net_pnl_matches_rows": (
            None if meta["net_pnl_sum"] is None else abs(meta["net_pnl_sum"] - net_from_rows) <= 1e-6
        ),
        "summary_trades_total": meta["trades_total"],
        "trades_rows": len(rows),
        "net_pnl_from_summary": meta["net_pnl_sum"],
        "net_pnl_from_rows": round(net_from_rows, 8),
    }

    return {
        "batch_id": batch_id,
        "batch": meta,
        "trades": {
            "count": len(rows),
            "wins": wins,
            "losses": losses,
            "flats": flats,
            "win_rate_pct": (round(wins / len(rows) * 100.0, 6) if rows else None),
            "net_pnl_sum": round(net_from_rows, 8) if rows else None,
            "fees_sum": (round(sum(r["fees"] for r in rows if r["fees"] is not None), 8)
                         if any(r["fees"] is not None for r in rows) else None),
            "funding_sum": (round(sum(r["funding_fee"] for r in rows if r["funding_fee"] is not None), 8)
                            if any(r["funding_fee"] is not None for r in rows) else None),
        },
        "checks": checks,
        "derived_from": "批次目录各 run 的 summary.json + trades.jsonl 现算",
        "missing_reason": None,
    }, 200


__all__ = [
    "BATCH_ROOT", "batch_ids", "list_batches", "get_batch", "batch_runs",
    "batch_trades", "batch_summary",
]
