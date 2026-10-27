"""行情尾段写入（架构重构第 3 期）。

从 `adapter.py` 的 `_tail_store()` **原样搬出**，只做两件事：

1. 把「写库」与「HTTP 请求」解耦：web 进程不再持有写连接；
2. 让工作进程可以复用同一段逻辑补写尾段。

为什么必须搬
------------
原实现在 K 线读取路径里直接 INSERT/UPDATE 行情库。也就是说：
**用户打开一张图，可能触发一次数据库写入**。与采集器、历史下载器叠加，
三个写者抢同一个 SQLite 文件 ⇒ 写锁排队、WAL 无法检查点、膨胀到 782MB、
读请求从毫秒掉到秒级。

纪律
----
* 本模块**只接收调用方传入的连接**，不自己决定连哪个库、不自己开事务策略；
* `commit()` 由本函数负责（与原实现一致，调用方不必再提交）；
* 幂等：同内容 sha256 只入一次；已有 `candle_heads` 的 open_time 不再重复入；
* 最近尾段 REST **不抢**已有实时/归档 head 的来源（`DO NOTHING` + 回删）。
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Optional

from data_hub.store import INTERVAL_MS, now_ms


def store_tail(con, exchange: str, symbol: str, tf: str,
               bars: Iterable[dict[str, Any]], src_url: str, *,
               now: Optional[int] = None) -> int:
    """把**已收盘**尾段写入行情库，返回新增根数。

    `bars` 必须已按 `open_time` 升序且只含 `is_closed=True` 的行；
    调用方负责过滤与节流（本函数不做节流，便于 worker 重放）。
    """
    closed = list(bars)
    if not closed:
        return 0
    now = now_ms() if now is None else now
    market = "um" if exchange == "binance" else "swap"

    # REST 尾段只用于补缺；先剔除已有 candle_heads，避免新 REST source 抢走更新的
    # WS/归档 head，也避免重复造 inactive candle_versions。
    have = {r[0] for r in con.execute(
        "SELECT open_time FROM candle_heads WHERE exchange=? AND market=? AND symbol=? "
        "AND timeframe=? AND open_time BETWEEN ? AND ?",
        (exchange, market, symbol, tf, closed[0]["open_time"], closed[-1]["open_time"]))}
    todo = [b for b in closed if b["open_time"] not in have]
    if not todo:
        return 0

    text = "\n".join("|".join(str(b[k]) for k in
                              ("open_time", "open", "high", "low", "close",
                               "volume", "quote_volume")) for b in todo)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    old = con.execute("SELECT id FROM sources WHERE exchange=? AND market=? AND symbol=? "
                      "AND timeframe=? AND sha256=?",
                      (exchange, market, symbol, tf, digest)).fetchone()
    if old:
        return 0

    cur = con.execute(
        "INSERT INTO sources(exchange,market,symbol,timeframe,source_url,file_path,sha256,"
        "imported_at,downloaded_at,first_open,last_close,row_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (exchange, market, symbol, tf, src_url, f"{exchange}:rest:tail",
         digest, now, now, todo[0]["open_time"], todo[-1]["close_time"], len(todo)))
    sid = cur.lastrowid
    added = 0
    for b in todo:
        inserted = con.execute(
            "INSERT OR IGNORE INTO candle_versions(exchange,market,symbol,timeframe,open_time,"
            "close_time,open,high,low,close,volume,quote_volume,trade_count,taker_buy_volume,"
            "taker_buy_quote_volume,source_id,available_at,is_closed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (exchange, market, symbol, tf, b["open_time"], b["close_time"],
             b["open"], b["high"], b["low"], b["close"], b["volume"], b["quote_volume"],
             b["trade_count"], b["taker_buy_volume"], b["taker_buy_quote_volume"], sid,
             b.get("available_at")))
        if not inserted.rowcount:
            continue
        # 最近尾段 REST 不能因同 open_time 重新指向新来源：
        # 已有的实时/归档 head 保持原值与原 source（尤其 available_at 语义不能被抹掉）。
        head = con.execute(
            "INSERT INTO candle_heads(exchange,market,symbol,timeframe,open_time,source_id)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(exchange,market,symbol,timeframe,open_time)"
            " DO NOTHING", (exchange, market, symbol, tf, b["open_time"], sid))
        if not head.rowcount:
            con.execute("DELETE FROM candle_versions WHERE exchange=? AND market=? AND symbol=? "
                        "AND timeframe=? AND open_time=? AND source_id=?",
                        (exchange, market, symbol, tf, b["open_time"], sid))
            continue
        added += 1

    if added:
        cov = con.execute("SELECT rows,first_open,last_close FROM coverage_cache WHERE "
                          "exchange=? AND market=? AND symbol=? AND timeframe=?",
                          (exchange, market, symbol, tf)).fetchone()
        if cov:
            first = min(int(cov["first_open"]) if cov["first_open"] is not None
                        else todo[0]["open_time"], min(b["open_time"] for b in todo))
            last = max(int(cov["last_close"]) if cov["last_close"] is not None
                       else todo[-1]["close_time"], max(b["close_time"] for b in todo))
            rows = int(cov["rows"] or 0) + added
            expected = max(0, (last + 1 - first) // INTERVAL_MS[tf])
            con.execute("UPDATE coverage_cache SET rows=?,first_open=?,last_close=?,expected=?,"
                        "missing=?,source_count=source_count+?,updated_at=? WHERE exchange=? "
                        "AND market=? AND symbol=? AND timeframe=?",
                        (rows, first, last, expected, max(0, expected - rows),
                         int(cur.rowcount), now, exchange, market, symbol, tf))

    if exchange == "okx":
        for f, stored, reason, impact in (
                ("taker_buy_volume", "", "欧易 K 线响应无该字段（币安有）",
                 "B 策略数据不足"),
                ("trade_count", "0", "欧易 K 线响应无 trade_count 字段；本列 NOT NULL，"
                                      "写 0 为占位符，非真值", "成交笔数类指标不可用")):
            con.execute("INSERT OR REPLACE INTO candle_field_notes(exchange,market,symbol,"
                        "timeframe,field,stored_as,reason,impact,source,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (exchange, market, symbol, tf, f, stored, reason, impact,
                         "okx.v5/market-candles", now))

    con.commit()
    return added


__all__ = ["store_tail"]
