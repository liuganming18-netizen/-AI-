# -*- coding: utf-8 -*-
"""资金费周期元数据 · 只读因果查询 API（合成测试夹具 / SYNTHETIC TEST FIXTURE）。

【注意 / NOTICE】
本模块是为资金费区间消费回归测试（test_funding_interval_consumer 等）构建的
隔离合成测试夹具实现，提供严格因果语义查询接口。
严禁伪造生产数据，本夹具仅供离线测试环境验证使用。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

EXCHANGE = "binance"
MARKET = "um"
UNVERIFIABLE_NO_INTERVAL_META = "UNVERIFIABLE_NO_INTERVAL_META"
REASON_NO_RECORD = "no_record"
REASON_STALE = "stale"
RATIFIED_RULE = "HUMAN_RATIFIED_SETTLEMENT_CAUSAL_V1"
RATIFIED_STATUS = "RATIFIED_BY_HUMAN_2026_10_13"


def connect(db: str | Path | sqlite3.Connection, readonly: bool = True) -> sqlite3.Connection:
    """以只读模式连接 SQLite 数据库（mode=ro + PRAGMA query_only=1）。"""
    if isinstance(db, sqlite3.Connection):
        return db
    p = Path(db).resolve()
    if not p.exists():
        raise FileNotFoundError(f"数据库文件不存在：{p}")
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=1")
    else:
        con = sqlite3.connect(str(p))
    return con


def _get_connection(con: Optional[sqlite3.Connection], db: Any) -> tuple[sqlite3.Connection, bool]:
    """获取连接对象及是否需要在函数结束时关闭连接的标识。"""
    if con is not None:
        return con, False
    if db is not None:
        return connect(db, readonly=True), True
    raise ValueError("必须提供 con 或 db 参数")


def has_symbol(symbol: str, *, con: Optional[sqlite3.Connection] = None, db: Any = None) -> bool:
    """检查伴生库中是否存在该标的的周期记录。"""
    c, need_close = _get_connection(con, db)
    try:
        row = c.execute(
            "SELECT 1 FROM funding_interval_meta WHERE exchange=? AND market=? AND symbol=? LIMIT 1",
            (EXCHANGE, MARKET, symbol),
        ).fetchone()
        return row is not None
    finally:
        if need_close:
            c.close()


def coverage(symbol: str, *, basis: str = "settlement", con: Optional[sqlite3.Connection] = None, db: Any = None) -> Dict[str, Any]:
    """查询标的的有效周期元数据覆盖范围。"""
    if basis not in ("settlement", "archive_publish"):
        raise ValueError(f"无效口径 basis: {basis}")
    avail_col = "avail_settlement_ms" if basis == "settlement" else "avail_archive_publish_ms"
    c, need_close = _get_connection(con, db)
    try:
        row = c.execute(
            f"SELECT MIN({avail_col}), MAX({avail_col}), COUNT(*) "
            f"FROM funding_interval_meta WHERE exchange=? AND market=? AND symbol=?",
            (EXCHANGE, MARKET, symbol),
        ).fetchone()
        if not row or row[2] == 0:
            return {"first_available_ms": None, "last_available_ms": None, "n": 0}
        return {"first_available_ms": int(row[0]), "last_available_ms": int(row[1]), "n": int(row[2])}
    finally:
        if need_close:
            c.close()


def interval_hours_at(
    symbol: str,
    T: int,
    *,
    basis: str = "settlement",
    con: Optional[sqlite3.Connection] = None,
    db: Any = None,
) -> Optional[Dict[str, Any]]:
    """因果查询：返回在 T 时刻已知且生效的最新资金费周期记录。

    严格因果原则：
    - available_at_ms <= T
    - 找不到记录时返回 None，绝不默认兜底 8h！
    """
    if basis not in ("settlement", "archive_publish"):
        raise ValueError(f"无效口径 basis: {basis}")
    avail_col = "avail_settlement_ms" if basis == "settlement" else "avail_archive_publish_ms"
    c, need_close = _get_connection(con, db)
    try:
        query = (
            f"SELECT m.calc_time_ms, m.funding_interval_hours, m.last_funding_rate, "
            f"       m.{avail_col} AS available_at_ms, s.zip_name AS source_file, "
            f"       m.exchange, m.market "
            f"FROM funding_interval_meta m "
            f"JOIN source_files s ON s.source_id = m.source_id "
            f"WHERE m.exchange=? AND m.market=? AND m.symbol=? AND m.{avail_col} <= ? "
            f"ORDER BY m.calc_time_ms DESC LIMIT 1"
        )
        row = c.execute(query, (EXCHANGE, MARKET, symbol, int(T))).fetchone()
        if not row:
            return None
        return {
            "exchange": row[5],
            "market": row[6],
            "symbol": symbol,
            "calc_time_ms": int(row[0]),
            "funding_interval_hours": int(row[1]),
            "last_funding_rate": str(row[2]),
            "available_at_ms": int(row[3]),
            "source_file": str(row[4]),
            "ratified_rule": RATIFIED_RULE,
        }
    finally:
        if need_close:
            c.close()


def settlement_at(
    symbol: str,
    T: int,
    *,
    epsilon_ms: int = 0,
    con: Optional[sqlite3.Connection] = None,
    db: Any = None,
) -> Optional[Dict[str, Any]]:
    """精确结算点查询：若与精确结算时刻误差 <= epsilon_ms 则返回记录，否则返回 None。"""
    if epsilon_ms < 0:
        raise ValueError("epsilon_ms 必须为非负整数")
    c, need_close = _get_connection(con, db)
    try:
        query = (
            "SELECT m.calc_time_ms, m.funding_interval_hours, m.last_funding_rate, "
            "       m.avail_settlement_ms, s.zip_name, m.exchange, m.market "
            "FROM funding_interval_meta m "
            "JOIN source_files s ON s.source_id = m.source_id "
            "WHERE m.exchange=? AND m.market=? AND m.symbol=? "
            "  AND abs(m.calc_time_ms - ?) <= ? "
            "ORDER BY abs(m.calc_time_ms - ?) ASC LIMIT 1"
        )
        row = c.execute(query, (EXCHANGE, MARKET, symbol, int(T), int(epsilon_ms), int(T))).fetchone()
        if not row:
            return None
        return {
            "exchange": row[5],
            "market": row[6],
            "symbol": symbol,
            "calc_time_ms": int(row[0]),
            "funding_interval_hours": int(row[1]),
            "last_funding_rate": str(row[2]),
            "available_at_ms": int(row[3]),
            "source_file": str(row[4]),
            "ratified_rule": RATIFIED_RULE,
        }
    finally:
        if need_close:
            c.close()


def resolve_settlement(
    symbol: str,
    T: int,
    *,
    epsilon_ms: int = 0,
    con: Optional[sqlite3.Connection] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """结算点解析诊断包装器。"""
    rec = settlement_at(symbol, T, epsilon_ms=epsilon_ms, con=con, db=db)
    if rec is not None:
        return {"status": "OK", "reason": None, "record": rec}
    return {
        "status": UNVERIFIABLE_NO_INTERVAL_META,
        "reason": "not_a_settlement_point_within_epsilon",
        "record": None,
    }


def resolve_interval(
    symbol: str,
    T: int,
    *,
    max_staleness_ms: Optional[int] = None,
    con: Optional[sqlite3.Connection] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """周期元数据判定诊断包装器。"""
    rec = interval_hours_at(symbol, T, basis="settlement", con=con, db=db)
    if rec is None:
        return {
            "status": UNVERIFIABLE_NO_INTERVAL_META,
            "marker": UNVERIFIABLE_NO_INTERVAL_META,
            "reason": REASON_NO_RECORD,
            "record": None,
        }
    if max_staleness_ms is not None and (int(T) - rec["calc_time_ms"] > int(max_staleness_ms)):
        return {
            "status": UNVERIFIABLE_NO_INTERVAL_META,
            "marker": UNVERIFIABLE_NO_INTERVAL_META,
            "reason": REASON_STALE,
            "record": None,
        }
    return {"status": "OK", "marker": None, "record": rec}
