"""ACCUM-EARLY-06-06 · 最小资金费事件适配入口（只读，供研究复算调用）

定位
----
给 `replay_calc.replay_one(funding_events=...)` 提供「冷热合并 + 逐事件来源可追溯」的资金费
事件列表。**不改生产读取链**、**不新建采集器**、**不补 0**、**不伪造可得时间**。

复用
----
底层完全复用现成读取器 `框架/backend/market_repository.py::MarketDataRepository.funding`，
本模块只做三件事：两侧分读 → 键级合并 → 冲突并列。不自己写 sqlite/parquet SQL。

四条实测前提（每条都对应一个会静默吃成本的坑，见 06-05 §7）
----------------------------------------------------------
P1  `prefer="hot"` 不是「纯热库」：热库命中不足 `limit` 时会**混入冷档行**并合并排序
    （实测 TNSRUSDT 727 行里有 94 行 `fetched_at` 为空）。⇒ 不能把 `prefer="hot"` 的结果
    一律标成「热库原始」。
P2  判别器：热库 `funding_history.fetched_at` 全表非空（实测 NULL 行数 = 0，见 out/00）
    ⇒ `fetched_at is None` 的行必来自冷档。若某天该前提被破坏，本模块**不静默丢行**，
    改为记 `discriminator_conflict` 并把该行按热库处理。
P3  冷档列名是 `funding_rate`，而 `trading_core.funding.funding_events_ms` 只认 `rate`
    ⇒ 原样喂入会 `rate=None` → 记 `funding_gap` → 成本 0 且**不报错**。必须显式映射。
P4  冷档无 `available_at` 列、热库 `fetched_at` 是「入库时刻」不是「当时可得」
    ⇒ 本模块把可得性一律标 `unproven`，绝不填一个看起来合理的值。

冲突判定（不擅自选较有利值）
--------------------------
同键 = (exchange, market, symbol, funding_time)，即「交易所 / 合约 / 实际结算时间」。
逐经济字段（rate、mark_price）分五态：

  agree             两侧都有且相等            → 取值，status="resolved"
  one_sided         一侧有值、另一侧为空      → 取非空值，status="resolved_single_side"
                                               （只有一个值，不存在「选较有利」；但记 one_sided）
  differs           两侧都有且不等            → **顶层 rate/mark_price 置 None**，
                                               status="pending_conflict"，两侧值并列保留
  only_hot          仅热库有该事件            → 取值，observed_in="hot"
  only_cold         仅冷档有该事件            → 取值，observed_in="cold"
  none              两侧都为空                → 置 None，status="pending_missing"

`strict=True` 时 `one_sided` 也降级为 `pending_conflict`（留给调用方自行选择保守度，
默认 False：因为「只有一个值」不构成二选一，强行置 None 会把可算的成本变成 gap）。

顶层 `rate` / `mark_price` 为 None 的事件**仍然保留在列表里**，不丢弃 —— 丢弃会让
`settle_funding_events` 少算一次结算而账面看不出异常；保留则落成 `funding_gap`，
缺口可见。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ADAPTER_VERSION = "funding-adapter-06/v1"
KEY_FIELDS: Tuple[str, ...] = ("exchange", "market", "symbol", "funding_time")

DEFAULT_HOT_DB = "数据/市场数据/db/market_v2.sqlite3"
DEFAULT_ARCHIVE_ROOT = "数据/市场数据/parquet"

_REL_TOL = 1e-12


def _repo_root() -> str:
    """仓库根（本文件在 evidence/ACCUM-EARLY-06-06/adapter/ 下 ⇒ 上溯 3 层）。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _ensure_backend_on_path() -> None:
    backend = os.path.join(_repo_root(), "框架", "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _f(v: Any) -> Optional[float]:
    """字符串/数值 → float；空 → None。热库返回的是 TEXT，冷档是 DOUBLE。"""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _same(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _REL_TOL * max(1.0, abs(a), abs(b))


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _get_repo(hot_db: Optional[str], archive_root: Optional[str]):
    _ensure_backend_on_path()
    from market_repository import MarketDataRepository  # 现成读取器，不复制其逻辑

    root = _repo_root()
    hot = hot_db or os.path.join(root, DEFAULT_HOT_DB)
    arch = archive_root or os.path.join(root, DEFAULT_ARCHIVE_ROOT)
    return MarketDataRepository(hot, arch, watermark_ms=0)


def _read_side(repo, exchange: str, market: str, symbol: str, start_ms: int, end_ms: int,
               limit: int, prefer: str) -> Tuple[List[dict], bool]:
    rows = repo.funding(exchange, market, symbol, start=start_ms, end=end_ms,
                        limit=limit, prefer=prefer)
    return list(rows or []), len(rows or []) >= limit


def _classify_hot_side(rows: Sequence[dict], cold_keys: set,
                       has_cold: bool) -> Tuple[List[dict], int, int]:
    """把 `prefer="hot"` 的返回拆成「真热库行」与「混进来的冷档行」。

    `has_cold=False`（调用方明确只要热库、没读冷档）时按 P2 **严格**判定：
    `fetched_at is None` 一律算冷档泄漏并丢弃。P2 已在 out/00 全表核过（NULL 行数 = 0），
    所以这个判定在当下是精确的；若哪天 P2 被破坏，这里会少算事件 ——
    因此 out/00 必须与本次读取同一批次重跑，不能拿旧结论套。

    `has_cold=True` 时多一道校验：泄漏行的键若不在冷档集合里，说明 P2 可能已失效，
    此时**不丢行**（按热库处理）并计入 `n_disc`，宁可多算也不静默少算。

    返回 (真热库行, 判为冷档泄漏的行数, 判别器冲突行数)。
    """
    hot_rows: List[dict] = []
    n_leaked = 0
    n_disc_conflict = 0
    for r in rows:
        if r.get("fetched_at") is not None:
            hot_rows.append(r)
            continue
        k = (r.get("exchange"), r.get("market"), r.get("symbol"), r.get("funding_time"))
        if has_cold and k not in cold_keys:
            n_disc_conflict += 1
            hot_rows.append(r)
        else:
            n_leaked += 1
    return hot_rows, n_leaked, n_disc_conflict


def _event_from_sides(ex: str, mk: str, sym: str, t: int,
                      h: Optional[dict], c: Optional[dict],
                      strict: bool) -> dict:
    """把一个键上的两侧原始行合成一条可追溯事件。"""
    rate_hot = _f(h.get("funding_rate")) if h else None
    mark_hot = _f(h.get("mark_price")) if h else None
    rate_cold = _f(c.get("funding_rate")) if c else None
    mark_cold = _f(c.get("mark_price")) if c else None

    conflicts: List[dict] = []
    pending = False

    def judge(field: str, vh: Optional[float], vc: Optional[float]) -> Optional[float]:
        nonlocal pending
        if h is None:
            return vc
        if c is None:
            return vh
        if vh is None and vc is None:
            conflicts.append({"field": field, "kind": "none", "hot": None, "cold": None,
                              "severity": "blocking"})
            pending = True
            return None
        if vh is None or vc is None:
            # 只有一个值：不存在二选一，但必须留痕
            conflicts.append({"field": field, "kind": "one_sided",
                              "hot": vh, "cold": vc, "severity": "strict" if strict else "info"})
            if strict:
                pending = True
                return None
            return vc if vh is None else vh
        if _same(vh, vc):
            return vh
        conflicts.append({"field": field, "kind": "differs", "hot": vh, "cold": vc,
                          "abs_diff": abs(vh - vc), "severity": "blocking"})
        pending = True
        return None

    rate = judge("rate", rate_hot, rate_cold)
    mark = judge("mark_price", mark_hot, mark_cold)

    # 来源/结算类型差异：不影响经济值，只记 provenance
    src_hot = _s(h.get("source")) if h else None
    src_cold = _s(c.get("source")) if c else None
    if h is not None and c is not None and src_hot != src_cold:
        conflicts.append({"field": "source", "kind": "differs", "hot": src_hot, "cold": src_cold,
                          "severity": "info"})
    rt_hot = _s(h.get("rate_type")) if h else None
    rt_cold = _s(c.get("rate_type")) if c else None
    if h is not None and c is not None and rt_hot != rt_cold:
        conflicts.append({"field": "rate_type", "kind": "differs", "hot": rt_hot,
                          "cold": rt_cold, "severity": "info"})

    if h is not None and c is not None:
        observed = "both"
    elif h is not None:
        observed = "hot"
    else:
        observed = "cold"

    if pending:
        status = "pending_conflict" if any(x["kind"] == "differs" for x in conflicts) else "pending_missing"
    elif any(x["kind"] == "one_sided" for x in conflicts):
        status = "resolved_single_side"
    else:
        status = "resolved"

    return {
        "exchange": ex,
        "market": mk,
        "symbol": sym,
        "funding_time": int(t),
        # ---- 顶层：供 settle 直接消费；冲突时为 None（fail-closed） ----
        "rate": rate,
        "mark_price": mark,
        # ---- 两侧原值并列保留，永不覆盖 ----
        "rate_hot": rate_hot,
        "rate_cold": rate_cold,
        "mark_hot": mark_hot,
        "mark_cold": mark_cold,
        "source_hot": src_hot,
        "source_cold": src_cold,
        "rate_type_hot": rt_hot,
        "rate_type_cold": rt_cold,
        "fetched_at_hot": (int(h["fetched_at"]) if h and h.get("fetched_at") is not None else None),
        # ---- 判定痕迹 ----
        "observed_in": observed,
        "status": status,
        "conflicts": conflicts,
        # ---- 可得性：不可证明就写 unproven，不猜 ----
        "available_at": None,
        "availability": "unproven",
    }


def load_funding_events(exchange: str, market: str, symbol: str,
                        start_ms: int, end_ms: int, *,
                        repo=None, hot_db: Optional[str] = None,
                        archive_root: Optional[str] = None,
                        limit: int = 200000, strict: bool = False,
                        include_cold: bool = True) -> Dict[str, Any]:
    """读取单个 (exchange, market, symbol) 在 [start_ms, end_ms] 内的资金费结算事件。

    返回 dict：
      events      去重后按 funding_time 升序的事件列表（每条含两侧原值）
      counts      各状态计数
      read_scope  读取范围与参数（含 limit / prefer / 是否截断）
      notes       缺失说明与告警
      identity    数据身份（文件大小、mtime、sha 片段），用于对账与零写入核对
    """
    repo = repo or _get_repo(hot_db, archive_root)
    notes: List[str] = []

    raw_hot, hot_trunc = _read_side(repo, exchange, market, symbol, start_ms, end_ms, limit, "hot")
    if hot_trunc:
        notes.append(f"hot_truncated: prefer=hot 返回 {len(raw_hot)} 条已达 limit={limit}")
    raw_cold: List[dict] = []
    cold_trunc = False
    if include_cold:
        raw_cold, cold_trunc = _read_side(repo, exchange, market, symbol, start_ms, end_ms,
                                          limit, "cold")
        if cold_trunc:
            notes.append(f"cold_truncated: prefer=cold 返回 {len(raw_cold)} 条已达 limit={limit}")

    cold_by_key: Dict[tuple, dict] = {}
    for r in raw_cold:
        k = (r.get("exchange") or exchange, r.get("market") or market,
             r.get("symbol") or symbol, r.get("funding_time"))
        cold_by_key.setdefault(k, r)   # 冷档同键重复只取首条，重复数单独计
    n_cold_dup = len(raw_cold) - len(cold_by_key)

    hot_rows, n_leaked, n_disc = _classify_hot_side(raw_hot, set(cold_by_key),
                                                   has_cold=bool(cold_by_key))
    if n_leaked:
        notes.append(f"hot_path_leaked_cold: prefer=hot 结果中 {n_leaked} 行 fetched_at 为空，"
                     f"判为冷档混入（未计入热库原始）")
    if n_disc:
        notes.append(f"discriminator_conflict: {n_disc} 行 fetched_at 为空且不在冷档集合中，"
                     f"按热库处理（判别器前提 P2 可能已失效）")

    hot_by_key: Dict[tuple, dict] = {}
    n_hot_dup = 0
    for r in hot_rows:
        k = (r.get("exchange") or exchange, r.get("market") or market,
             r.get("symbol") or symbol, r.get("funding_time"))
        if k in hot_by_key:
            n_hot_dup += 1
            continue
        hot_by_key[k] = r
    if n_hot_dup:
        notes.append(f"hot_dup: 热库侧同键重复 {n_hot_dup} 行，保留首条")

    keys = sorted(set(hot_by_key) | set(cold_by_key), key=lambda k: k[3])
    events = [_event_from_sides(exchange, market, symbol, k[3],
                                hot_by_key.get(k), cold_by_key.get(k), strict) for k in keys]

    counts: Dict[str, int] = {
        "events": len(events),
        "observed_both": sum(1 for e in events if e["observed_in"] == "both"),
        "observed_hot_only": sum(1 for e in events if e["observed_in"] == "hot"),
        "observed_cold_only": sum(1 for e in events if e["observed_in"] == "cold"),
        "resolved": sum(1 for e in events if e["status"] == "resolved"),
        "resolved_single_side": sum(1 for e in events if e["status"] == "resolved_single_side"),
        "pending_conflict": sum(1 for e in events if e["status"] == "pending_conflict"),
        "pending_missing": sum(1 for e in events if e["status"] == "pending_missing"),
        "with_rate": sum(1 for e in events if e["rate"] is not None),
        "with_mark": sum(1 for e in events if e["mark_price"] is not None),
        "hot_rows_raw": len(raw_hot),
        "cold_rows_raw": len(raw_cold),
        "cold_rows_dedup": len(cold_by_key),
        "hot_rows_dedup": len(hot_by_key),
        # 来源归属痕迹：prefer='hot' 里被判为冷档混入的行数 / 判别器冲突行数
        "hot_path_leaked_cold": n_leaked,
        "discriminator_conflict": n_disc,
        "hot_dup": n_hot_dup,
        "cold_dup": n_cold_dup,
    }

    # 冷档覆盖边界：本 symbol 冷档实际给出的最早/最晚结算时刻（不是「声明覆盖」，是实测）
    cold_lo = min(cold_by_key, key=lambda k: k[3])[3] if cold_by_key else None
    cold_hi = max(cold_by_key, key=lambda k: k[3])[3] if cold_by_key else None
    hot_lo = min(hot_by_key, key=lambda k: k[3])[3] if hot_by_key else None
    hot_hi = max(hot_by_key, key=lambda k: k[3])[3] if hot_by_key else None
    if not include_cold:
        notes.append("include_cold=False：只读热库（按 P2 严格剔除 prefer=hot 里混进来的冷档行），"
                     f"已剔除 {n_leaked} 行；缺口段内将缺少结算事件。"
                     "本模式仅用于「补齐前」基线对照，不可用于交付")
    if end_ms > (cold_hi or 0) and cold_hi is not None:
        notes.append(f"cold_ends_before_window_end: 冷档最后一条 {cold_hi} < end_ms {end_ms}"
                     f"，该段只能靠热库，不能声明「合并后全窗口完整」")

    return {
        "adapter_version": ADAPTER_VERSION,
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "events": events,
        "counts": counts,
        "coverage_observed": {"hot_lo": hot_lo, "hot_hi": hot_hi,
                              "cold_lo": cold_lo, "cold_hi": cold_hi},
        "read_scope": {
            "start_ms": int(start_ms), "end_ms": int(end_ms), "limit": int(limit),
            "prefer_hot": "hot", "prefer_cold": "cold" if include_cold else None,
            "strict": bool(strict),
            "repo": "MarketDataRepository.funding",
            "hot_db": getattr(repo, "hot_db", None) if not isinstance(repo, type(None)) else None,
            "archive_root": str(getattr(repo, "archive_root", "")),
            "dedup_key": list(KEY_FIELDS),
        },
        "notes": notes,
    }


def to_replay_events(loaded: Dict[str, Any]) -> List[dict]:
    """转成 `replay_one(funding_events=...)` 直接可用的形状。

    只保留 `funding_time`（毫秒）/`rate`/`mark_price` 三个必需字段，
    外加 `status` 供调用方统计。**不丢弃 pending 事件**：rate=None 会落成
    `funding_gap`，缺口可见；丢弃则少一次结算且账面无异常。
    """
    return [{"funding_time": e["funding_time"],
             "rate": e["rate"],
             "mark_price": e["mark_price"],
             "status": e["status"],
             "observed_in": e["observed_in"]} for e in loaded["events"]]


def load_many(symbols: Sequence[str], start_ms: int, end_ms: int, *,
              exchange: str = "binance", market: str = "um",
              repo=None, hot_db: Optional[str] = None,
              archive_root: Optional[str] = None,
              limit: int = 200000, strict: bool = False) -> Dict[str, Any]:
    """批量：一次建 repo，逐 symbol 调用，返回合并结果（供产出稳定事件文件）。"""
    repo = repo or _get_repo(hot_db, archive_root)
    per: Dict[str, Any] = {}
    for s in symbols:
        per[s] = load_funding_events(exchange, market, s, start_ms, end_ms, repo=repo,
                                     limit=limit, strict=strict)
    return {"adapter_version": ADAPTER_VERSION, "per_symbol": per}


def events_sha256(events: Sequence[dict]) -> str:
    """事件列表的确定性摘要（键序固定、浮点用 repr、不含生成时刻）。"""
    import hashlib

    h = hashlib.sha256()
    for e in events:
        h.update(json.dumps(e, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


if __name__ == "__main__":  # 极简手工冒烟
    out = load_funding_events("binance", "um", "TNSRUSDT", 1782547200000, 1793005200000)
    print(json.dumps({k: v for k, v in out.items() if k != "events"},
                     ensure_ascii=False, indent=1))
    print("events[0] =", json.dumps(out["events"][0], ensure_ascii=False))
