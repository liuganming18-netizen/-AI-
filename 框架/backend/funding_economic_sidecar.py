# -*- coding: utf-8 -*-
"""历史资金费事后经济估值 Sidecar 模块 (Funding Economic Sidecar).

本模块为 PR2 独立隔离环境下的专用旁路经济估值组件，严格遵循以下契约：
1. 只读读取历史持仓、成交、手续费与资金费事件，绝不写入生产数据库或账本；
2. 严格区分 PIT 决策净盈亏 (pnl_pit) 与事后经济估值净盈亏 (net_pnl_economic)；
3. 严格分级资金费证据等级 (VERIFIED / ESTIMATED / BOUNDARY_UNRESOLVED / UNKNOWN / INVALID)；
4. 绝不将事后资金费反向输入策略信号、入场、出场或止损决策；
5. 绝不将未知资金费静默记为零；
6. 边界接触事件 (funding_time == exit_ts) 默认作为 PENDING 未决代管，不进入确定性结算；
7. 使用 stable dedup key 严格去重，确保防二次入账与计算绝对幂等。
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import sqlite3
from typing import Dict, List, Optional, Set, Tuple, Any


class EvidenceGrade(str, Enum):
    """资金费证据等级枚举."""
    VERIFIED = "VERIFIED"  # 原生撮合标记价格与确凿结算证据
    ESTIMATED = "ESTIMATED"  # 费率确凿，但标记价格采用 1h K 线收盘价等代理
    BOUNDARY_UNRESOLVED = "BOUNDARY_UNRESOLVED"  # 结算时刻与平仓时刻重合，时序先后无法自证
    UNKNOWN = "UNKNOWN"  # 未知/缺失资金费，严禁静默计为 0
    INVALID = "INVALID"  # 越界事件或字段非法


class BoundaryHandlingMode(str, Enum):
    """边界接触事件处理模式."""
    PENDING = "PENDING"  # 默认：列入未决代管，不计入确定性结算金额
    INCLUDED = "INCLUDED"  # 包含边界事件（敏感性区间）
    EXCLUDED = "EXCLUDED"  # 排除边界事件（敏感性区间）


@dataclass
class FundingEventRecord:
    """资金费事件标准记录."""
    account_id: str
    position_id: int
    symbol: str
    funding_time: int
    rate: float
    mark: float
    amount: float
    grade: EvidenceGrade
    source: str
    fetched_at: Optional[int] = None
    recorded_at: Optional[int] = None
    price_source: str = "binance.archive.fundingRate+mark1h"
    dedup_key: str = ""

    def __post_init__(self):
        if not self.dedup_key:
            self.dedup_key = f"{self.account_id}:{self.position_id}:{self.funding_time}"


@dataclass
class PositionValuationResult:
    """单笔持仓事后经济估值结果."""
    scheme: str
    position_id: int
    symbol: str
    entry_ts: int
    exit_ts: Optional[int]
    status: str
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    pnl_pit: float
    funding_estimated: float
    funding_verified: float
    boundary_unresolved_amount: float
    net_pnl_economic: float
    unresolved_escrow: float
    events_count: int
    events: List[FundingEventRecord] = field(default_factory=list)
    has_unknown_funding: bool = False
    unknown_details: Optional[str] = None
    sensitivity: Dict[str, float] = field(default_factory=dict)


@dataclass
class LedgerValuationSummary:
    """账本估值汇总结果."""
    scheme: str
    total_positions: int
    closed_positions: int
    open_positions: int
    sum_gross_pnl: float
    sum_entry_fee: float
    sum_exit_fee: float
    sum_pnl_pit: float
    sum_funding_estimated: float
    sum_funding_verified: float
    sum_boundary_unresolved: float
    sum_net_pnl_economic: float
    sum_unresolved_escrow: float
    events_by_grade: Dict[str, int]
    amounts_by_grade: Dict[str, float]
    sensitivity: Dict[str, float]
    positions: List[PositionValuationResult] = field(default_factory=list)


def classify_funding_event(
    account_id: str,
    position_id: int,
    symbol: str,
    funding_time: int,
    rate: Optional[float],
    mark: Optional[float],
    amount: Optional[float],
    entry_ts: int,
    exit_ts: Optional[int],
    source: str = "archive",
    fetched_at: Optional[int] = None,
    recorded_at: Optional[int] = None,
    native_mark: bool = False
) -> FundingEventRecord:
    """对单条资金费事件执行严格分级与校验."""
    if rate is None or mark is None or amount is None:
        return FundingEventRecord(
            account_id=account_id, position_id=position_id, symbol=symbol,
            funding_time=funding_time, rate=rate or 0.0, mark=mark or 0.0, amount=amount or 0.0,
            grade=EvidenceGrade.UNKNOWN, source=source, fetched_at=fetched_at, recorded_at=recorded_at,
            price_source="missing"
        )

    # 存续期合法性检查: (entry_ts, exit_ts]
    # 若 funding_time < entry_ts，显然在持仓建立前，非法
    if funding_time < entry_ts:
        return FundingEventRecord(
            account_id=account_id, position_id=position_id, symbol=symbol,
            funding_time=funding_time, rate=rate, mark=mark, amount=amount,
            grade=EvidenceGrade.INVALID, source=source, fetched_at=fetched_at, recorded_at=recorded_at,
            price_source="out_of_bounds_before_entry"
        )

    if exit_ts is not None and funding_time > exit_ts:
        return FundingEventRecord(
            account_id=account_id, position_id=position_id, symbol=symbol,
            funding_time=funding_time, rate=rate, mark=mark, amount=amount,
            grade=EvidenceGrade.INVALID, source=source, fetched_at=fetched_at, recorded_at=recorded_at,
            price_source="out_of_bounds_after_exit"
        )

    # 边界重合检查: funding_time == exit_ts 或 funding_time == entry_ts
    if (exit_ts is not None and funding_time == exit_ts) or funding_time == entry_ts:
        return FundingEventRecord(
            account_id=account_id, position_id=position_id, symbol=symbol,
            funding_time=funding_time, rate=rate, mark=mark, amount=amount,
            grade=EvidenceGrade.BOUNDARY_UNRESOLVED, source=source,
            fetched_at=fetched_at, recorded_at=recorded_at,
            price_source="touching_boundary_unresolved"
        )

    # 原生标记价格 vs 1h K线代理
    if native_mark:
        grade = EvidenceGrade.VERIFIED
        price_src = "binance.um.native_mark_price"
    else:
        grade = EvidenceGrade.ESTIMATED
        price_src = "binance.archive.fundingRate+mark1h"

    return FundingEventRecord(
        account_id=account_id, position_id=position_id, symbol=symbol,
        funding_time=funding_time, rate=rate, mark=mark, amount=amount,
        grade=grade, source=source, fetched_at=fetched_at, recorded_at=recorded_at,
        price_source=price_src
    )


def calculate_position_valuation(
    scheme: str,
    position_id: int,
    symbol: str,
    entry_ts: int,
    exit_ts: Optional[int],
    status: str,
    gross_pnl: float,
    entry_fee: float,
    exit_fee: float,
    raw_events: List[Dict[str, Any]],
    boundary_mode: BoundaryHandlingMode = BoundaryHandlingMode.PENDING,
    has_unknown_gap: bool = False,
    unknown_details: Optional[str] = None
) -> PositionValuationResult:
    """计算单笔持仓的经济估值，严格分离 PIT 决策与经济估值."""
    pnl_pit = gross_pnl - entry_fee - exit_fee

    # 去重处理，避免同一资金费事件重复计算
    seen_keys: Set[str] = set()
    classified_events: List[FundingEventRecord] = []

    sum_estimated = 0.0
    sum_verified = 0.0
    sum_boundary = 0.0

    for ev in raw_events:
        account_id = ev.get("account_id", f"paper_{scheme}")
        ft = ev["funding_time"]
        dedup_key = f"{account_id}:{position_id}:{ft}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        rec = classify_funding_event(
            account_id=account_id,
            position_id=position_id,
            symbol=symbol,
            funding_time=ft,
            rate=ev.get("rate"),
            mark=ev.get("mark"),
            amount=ev.get("amount"),
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            source=ev.get("source", "archive"),
            fetched_at=ev.get("fetched_at"),
            recorded_at=ev.get("recorded_at"),
            native_mark=ev.get("native_mark", False)
        )
        classified_events.append(rec)

        if rec.grade == EvidenceGrade.ESTIMATED:
            sum_estimated += rec.amount
        elif rec.grade == EvidenceGrade.VERIFIED:
            sum_verified += rec.amount
        elif rec.grade == EvidenceGrade.BOUNDARY_UNRESOLVED:
            sum_boundary += rec.amount
        elif rec.grade == EvidenceGrade.UNKNOWN:
            has_unknown_gap = True

    # 确定性结算金额与未决代管处理
    # 注意资金费口径: amount 正数为支出 (扣除现金), 负数为收入 (增加现金)
    # net_pnl = gross_pnl - entry_fee - exit_fee - funding_paid = pnl_pit - funding_paid
    if boundary_mode == BoundaryHandlingMode.PENDING:
        # PENDING 模式: 边界未决金额不进入确定性结算
        net_econ = pnl_pit - (sum_estimated + sum_verified)
        escrow = sum_boundary
    elif boundary_mode == BoundaryHandlingMode.INCLUDED:
        net_econ = pnl_pit - (sum_estimated + sum_verified + sum_boundary)
        escrow = 0.0
    else:  # EXCLUDED
        net_econ = pnl_pit - (sum_estimated + sum_verified)
        escrow = 0.0

    # 敏感性区间分析
    pnl_incl = pnl_pit - (sum_estimated + sum_verified + sum_boundary)
    pnl_excl = pnl_pit - (sum_estimated + sum_verified)
    sensitivity = {
        "if_included": pnl_incl,
        "if_excluded": pnl_excl,
        "if_pending_settled": pnl_excl,
        "unresolved_escrow": sum_boundary
    }

    return PositionValuationResult(
        scheme=scheme,
        position_id=position_id,
        symbol=symbol,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        status=status,
        gross_pnl=gross_pnl,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        pnl_pit=pnl_pit,
        funding_estimated=sum_estimated,
        funding_verified=sum_verified,
        boundary_unresolved_amount=sum_boundary,
        net_pnl_economic=net_econ,
        unresolved_escrow=escrow,
        events_count=len(classified_events),
        events=classified_events,
        has_unknown_funding=has_unknown_gap,
        unknown_details=unknown_details,
        sensitivity=sensitivity
    )


def evaluate_ledger_from_db(
    sqlite_path: str,
    scheme: str,
    external_funding_events: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    boundary_mode: BoundaryHandlingMode = BoundaryHandlingMode.PENDING
) -> LedgerValuationSummary:
    """从只读 SQLite 数据库读取已平仓持仓并进行经济估值核算."""
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 读取所有持仓
    positions_raw = {r["position_id"]: dict(r) for r in conn.execute("SELECT * FROM paper_position").fetchall()}
    
    # 读取已平仓记录 (paper_realized)
    realized_raw = [dict(r) for r in conn.execute("SELECT * FROM paper_realized ORDER BY position_id").fetchall()]

    # 读取数据库内自带的 funding_events
    db_funding: Dict[int, List[Dict[str, Any]]] = {}
    try:
        for r in conn.execute("SELECT * FROM paper_funding ORDER BY funding_time").fetchall():
            pid = r["position_id"]
            db_funding.setdefault(pid, []).append(dict(r))
    except sqlite3.OperationalError:
        pass

    conn.close()

    total_positions = len(positions_raw)
    closed_positions = len(realized_raw)
    open_positions = total_positions - closed_positions

    results: List[PositionValuationResult] = []
    events_grade_counts: Dict[str, int] = {g.value: 0 for g in EvidenceGrade}
    events_grade_amounts: Dict[str, float] = {g.value: 0.0 for g in EvidenceGrade}

    sum_gross = 0.0
    sum_entry_fee = 0.0
    sum_exit_fee = 0.0
    sum_pit = 0.0
    sum_estimated = 0.0
    sum_verified = 0.0
    sum_boundary = 0.0
    sum_net_econ = 0.0
    sum_escrow = 0.0

    for r in realized_raw:
        pid = r["position_id"]
        pos = positions_raw.get(pid, {})
        symbol = r["symbol"]
        entry_ts = pos.get("entry_ts") or r.get("entry_time") or 0
        exit_ts = pos.get("exit_ts") or r.get("exit_time")
        status = pos.get("status", "closed")
        gross = r["gross_pnl"]
        efee = r["entry_fee"]
        xfee = r["exit_fee"]

        # 获取资金费事件：优先使用外部提供的事件（如 07 补充或冷热合并事件），若无则取 db 内部
        events: List[Dict[str, Any]] = []
        if external_funding_events and pid in external_funding_events:
            events = external_funding_events[pid]
        elif pid in db_funding:
            events = db_funding[pid]

        res = calculate_position_valuation(
            scheme=scheme,
            position_id=pid,
            symbol=symbol,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            status=status,
            gross_pnl=gross,
            entry_fee=efee,
            exit_fee=xfee,
            raw_events=events,
            boundary_mode=boundary_mode
        )
        results.append(res)

        sum_gross += res.gross_pnl
        sum_entry_fee += res.entry_fee
        sum_exit_fee += res.exit_fee
        sum_pit += res.pnl_pit
        sum_estimated += res.funding_estimated
        sum_verified += res.funding_verified
        sum_boundary += res.boundary_unresolved_amount
        sum_net_econ += res.net_pnl_economic
        sum_escrow += res.unresolved_escrow

        for ev in res.events:
            events_grade_counts[ev.grade.value] += 1
            events_grade_amounts[ev.grade.value] += ev.amount

    sensitivity = {
        "if_included": sum_pit - (sum_estimated + sum_verified + sum_boundary),
        "if_excluded": sum_pit - (sum_estimated + sum_verified),
        "if_pending_settled": sum_pit - (sum_estimated + sum_verified),
        "unresolved_escrow": sum_boundary
    }

    return LedgerValuationSummary(
        scheme=scheme,
        total_positions=total_positions,
        closed_positions=closed_positions,
        open_positions=open_positions,
        sum_gross_pnl=sum_gross,
        sum_entry_fee=sum_entry_fee,
        sum_exit_fee=sum_exit_fee,
        sum_pnl_pit=sum_pit,
        sum_funding_estimated=sum_estimated,
        sum_funding_verified=sum_verified,
        sum_boundary_unresolved=sum_boundary,
        sum_net_pnl_economic=sum_net_econ,
        sum_unresolved_escrow=sum_escrow,
        events_by_grade=events_grade_counts,
        amounts_by_grade=events_grade_amounts,
        sensitivity=sensitivity,
        positions=results
    )


def verify_idempotency_run(
    sqlite_path: str,
    scheme: str,
    external_funding_events: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    rounds: int = 3
) -> Tuple[bool, str]:
    """对同一 SQLite 数据库与输入执行多次独立估值，断言结果绝对幂等."""
    summaries = []
    for _ in range(rounds):
        s = evaluate_ledger_from_db(
            sqlite_path=sqlite_path,
            scheme=scheme,
            external_funding_events=external_funding_events,
            boundary_mode=BoundaryHandlingMode.PENDING
        )
        summaries.append(s)

    base = summaries[0]
    for idx, other in enumerate(summaries[1:], start=2):
        if base.closed_positions != other.closed_positions:
            return False, f"Round {idx} closed_positions mismatch: {base.closed_positions} != {other.closed_positions}"
        if abs(base.sum_net_pnl_economic - other.sum_net_pnl_economic) > 1e-9:
            return False, f"Round {idx} sum_net_pnl_economic drift: {base.sum_net_pnl_economic} != {other.sum_net_pnl_economic}"
        if abs(base.sum_unresolved_escrow - other.sum_unresolved_escrow) > 1e-9:
            return False, f"Round {idx} sum_unresolved_escrow drift: {base.sum_unresolved_escrow} != {other.sum_unresolved_escrow}"
        if base.events_by_grade != other.events_by_grade:
            return False, f"Round {idx} events_by_grade mismatch"

    return True, f"Idempotency verified across {rounds} passes: delta=0.000000"
