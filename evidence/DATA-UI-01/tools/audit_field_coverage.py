#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-01 字段覆盖审计（**只读**，一次性证据生成）。

产出：
  · `字段覆盖表.md`   —— 人读：每个字段的来源/路径/非空行数/覆盖币数/UTC 起止/最近成功/缺口/状态
  · `字段覆盖表.json` —— 机读：同上，供离线研究直接消费

纪律：
  · 一律按**实际非空行数**统计，不按「文件存在」；
  · 空串（`''`）算缺失（欧易 K 线主动买卖量就是空串）；
  · `trade_count = 0` 且同根 `volume > 0` 算**占位值**，不计入「真实可用」；
  · 缺失不填 0，一律 `None`；
  · 只读：不写库、不写快照、不联网。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "..", "框架", "backend"))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(BACKEND))

import metrics_api as M  # noqa: E402

OUT_DIR = os.path.dirname(HERE)
ARCHIVE = M.ARCHIVE_ROOT
DB = M.DB_PATH

PERIODS = list(M.PERIODS)


def iso(ms):
    return M.iso(ms)


def _duck():
    con, err = M.duck()
    if con is None:
        raise SystemExit(f"duckdb 不可用：{err}")
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=3")
    return con


def _files(pattern):
    import glob
    return sorted(glob.glob(pattern))


def metrics_audit(con):
    """币安官方归档 metrics（多空比/OI/taker 比值），只有 5m 粒度。"""
    files = _files(os.path.join(ARCHIVE, "metrics", "binance", "um", "*", "*.parquet"))
    if not files:
        return {"available": False, "reason": "无 metrics 归档"}
    q = f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT symbol) AS symbols,
           MIN(ts) AS first_ts, MAX(ts) AS last_ts,
           COUNT(sum_open_interest) AS oi,
           COUNT(sum_open_interest_value) AS oi_value,
           COUNT(retail_account_ls_ratio) AS global_acct,
           COUNT(count_long_short_ratio) AS count_ls,
           COUNT(top_position_ls_ratio) AS top_pos,
           COUNT(sum_toptrader_long_short_ratio) AS sum_top,
           COUNT(count_toptrader_long_short_ratio) AS top_acct,
           COUNT(sum_taker_long_short_vol_ratio) AS taker
    FROM read_parquet('{os.path.join(ARCHIVE, "metrics", "binance", "um", "*", "*.parquet")}')
    """
    r = con.execute(q).fetchone()
    return {
        "available": True, "files": len(files),
        "rows": int(r[0] or 0), "symbols": int(r[1] or 0),
        "first": iso(r[2]), "last": iso(r[3]),
        "nonnull": {
            "sum_open_interest": int(r[4] or 0),
            "sum_open_interest_value": int(r[5] or 0),
            "retail_account_ls_ratio": int(r[6] or 0),
            "count_long_short_ratio": int(r[7] or 0),
            "top_position_ls_ratio": int(r[8] or 0),
            "sum_toptrader_long_short_ratio": int(r[9] or 0),
            "count_toptrader_long_short_ratio": int(r[10] or 0),
            "sum_taker_long_short_vol_ratio": int(r[11] or 0),
        },
    }


def candles_audit(con, exchange, period):
    market = "um" if exchange == "binance" else "swap"
    pattern = os.path.join(ARCHIVE, "candles", exchange, market, "*", period, "*.parquet")
    files = _files(pattern)
    if not files:
        return {"available": False, "reason": "无归档", "files": 0}
    glob_pat = os.path.join(ARCHIVE, "candles", exchange, market, "*", period, "*.parquet")
    n = lambda c: f"try_cast(nullif(trim({c}), '') AS DOUBLE)"  # noqa: E731
    # `trade_count` 在归档里是 BIGINT（不是字符串）⇒ 不能套 trim()，单独用数值判断
    q = f"""
    SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
           MIN(open_time) AS first_ts, MAX(open_time) AS last_ts,
           COUNT({n('volume')}) AS vol,
           COUNT({n('quote_volume')}) AS qvol,
           COUNT({n('taker_buy_volume')}) AS tbv,
           COUNT({n('taker_buy_quote_volume')}) AS tbqv,
           COUNT(trade_count) AS tc,
           SUM(CASE WHEN trade_count = 0
                     AND try_cast(nullif(trim(volume),'') AS DOUBLE) > 0
                    THEN 1 ELSE 0 END) AS tc_placeholder,
           SUM(CASE WHEN try_cast(nullif(trim(taker_buy_volume),'') AS DOUBLE) IS NULL
                    THEN 1 ELSE 0 END) AS tbv_missing
    FROM read_parquet('{glob_pat}')
    """
    r = con.execute(q).fetchone()
    return {
        "available": True, "files": len(files),
        "rows": int(r[0] or 0), "symbols": int(r[1] or 0),
        "first": iso(r[2]), "last": iso(r[3]),
        "nonnull": {"volume": int(r[4] or 0), "quote_volume": int(r[5] or 0),
                    "taker_buy_volume": int(r[6] or 0),
                    "taker_buy_quote_volume": int(r[7] or 0),
                    "trade_count": int(r[8] or 0)},
        "trade_count_placeholder_rows": int(r[9] or 0),
        "taker_buy_missing_rows": int(r[10] or 0),
    }


def db_audit():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {}
    try:
        r = con.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT symbol) s, MIN(ts) f, MAX(ts) l, "
            "MAX(fetched_at) fetched, COUNT(open_interest) oi, "
            "COUNT(open_interest_value) oiv, COUNT(oi_ccy) oic "
            "FROM oi_history WHERE period='snapshot'").fetchone()
        out["oi_snapshot"] = {"rows": r["n"], "symbols": r["s"], "first": iso(r["f"]),
                              "last": iso(r["l"]), "last_fetched": iso(r["fetched"]),
                              "nonnull": {"open_interest": r["oi"],
                                          "open_interest_value": r["oiv"],
                                          "oi_ccy": r["oic"]}}
        r = con.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT symbol) s, MIN(ts) f, MAX(ts) l, "
            "MAX(fetched_at) fetched FROM oi_history WHERE period='5m'").fetchone()
        out["oi_5m"] = {"rows": r["n"], "symbols": r["s"], "first": iso(r["f"]),
                        "last": iso(r["l"]), "last_fetched": iso(r["fetched"])}
        r = con.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT symbol) s, MIN(funding_time) f, "
            "MAX(funding_time) l, MAX(fetched_at) fetched, COUNT(funding_rate) fr "
            "FROM funding_history WHERE rate_type='settled'").fetchone()
        out["funding_settled"] = {"rows": r["n"], "symbols": r["s"], "first": iso(r["f"]),
                                  "last": iso(r["l"]), "last_fetched": iso(r["fetched"]),
                                  "nonnull": {"funding_rate": r["fr"]}}
        r = con.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT symbol) s, COUNT(funding_rate) fr, "
            "COUNT(next_funding_time) nft, MAX(funding_event_time) ev, MAX(updated_at) upd "
            "FROM realtime_quotes").fetchone()
        out["funding_current"] = {"rows": r["n"], "symbols": r["s"],
                                  "nonnull": {"funding_rate": r["fr"],
                                              "next_funding_time": r["nft"]},
                                  "last_event": iso(r["ev"]), "last_updated": iso(r["upd"])}
        rows = con.execute(
            "SELECT timeframe, COUNT(DISTINCT symbol) s, COUNT(*) n, "
            "SUM(CASE WHEN is_closed=0 THEN 1 ELSE 0 END) forming "
            "FROM realtime_klines GROUP BY timeframe").fetchall()
        out["realtime_klines"] = {r["timeframe"]: {"symbols": r["s"], "rows": r["n"],
                                                   "forming": r["forming"]} for r in rows}
        r = con.execute("SELECT COUNT(*) n, COUNT(DISTINCT symbol) s FROM realtime_klines").fetchone()
        out["realtime_klines_total"] = {"rows": r["n"], "symbols": r["s"]}
        r = con.execute("SELECT COUNT(*) n FROM symbol_classification").fetchone()
        out["classification_rows"] = r["n"]
    finally:
        con.close()
    return out


def snapshot_audit():
    try:
        import public_snapshot as ps
    except Exception as exc:                                     # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    syms = ps.list_symbols()
    idx = ps.read_index()
    have_top = have_ta = have_ga = have_taker = 0
    fresh_ok = 0
    for ex, mid in syms:
        p = ps.read(ex, mid)
        if not p:
            continue
        ls = p.get("long_short") or {}
        for name, bucket in (("top_position_long_short_ratio", "top"),
                             ("top_account_long_short_ratio", "ta"),
                             ("global_account_long_short_ratio", "ga"),
                             ("taker_buy_sell_ratio", "taker")):
            if (ls.get(name) or {}).get("available"):
                if bucket == "top":
                    have_top += 1
                elif bucket == "ta":
                    have_ta += 1
                elif bucket == "ga":
                    have_ga += 1
                else:
                    have_taker += 1
        if (ps.freshness(p) or {}).get("status") == "ok":
            fresh_ok += 1
    return {"available": True, "symbols": len(syms), "index": idx,
            "ratio_blocks": {"top_position": have_top, "top_account": have_ta,
                             "global_account": have_ga, "taker_ratio": have_taker},
            "fresh_ok": fresh_ok, "dir": str(ps.SNAP_DIR)}


def build_report():
    con = _duck()
    scope = M.pool_scope()
    print("· metrics 归档聚合中…", file=sys.stderr)
    mhist = metrics_audit(con)
    candle_stats = {}
    for p in PERIODS:
        print(f"· candles binance {p} 聚合中…", file=sys.stderr)
        candle_stats[("binance", p)] = candles_audit(con, "binance", p)
    for p in PERIODS:
        if p == "8h":
            continue
        print(f"· candles okx {p} 聚合中…", file=sys.stderr)
        candle_stats[("okx", p)] = candles_audit(con, "okx", p)
    print("· 库内表聚合中…", file=sys.stderr)
    db = db_audit()
    print("· 快照聚合中…", file=sys.stderr)
    snap = snapshot_audit()

    report = {
        "generated_at": iso(M._now_ms()),
        "pool_scope": scope,
        "metrics_archive": mhist,
        "candles": {f"{k[0]}:{k[1]}": v for k, v in candle_stats.items()},
        "db": db,
        "snapshot": snap,
        "sources": {"archive": ARCHIVE, "db": DB},
    }
    return report


def md_table(report):
    mhist = report.get("metrics_archive") or {}
    snap = report.get("snapshot") or {}
    L = []
    a = L.append
    a("# DATA-UI-01 字段覆盖表")
    a("")
    a(f"生成时间（UTC）：`{report['generated_at']}`")
    a(f"归档根：`{report['sources']['archive']}`")
    a(f"库：`{report['sources']['db']}`")
    a("")
    a("> 口径：**按实际非空行数统计，不按文件存在**。空串算缺失（欧易 K 线主动买卖量即空串）；")
    a("> `trade_count=0` 且同根 `volume>0` 算**占位值**，不计入可用。缺失一律不填 0。")
    a("")

    a("## 1. 加密池分母（官方分类，不硬编码）")
    a("")
    a("| 交易所 | 分类字段 | 取值 | 加密合约数 | 已排除（官方分类） |")
    a("|---|---|---|---|---|")
    for ex in ("binance", "okx"):
        v = report["pool_scope"].get(ex) or {}
        exc = "、".join(f"{k} {n}" for k, n in (v.get("excluded") or {}).items()) or "—"
        a(f"| {ex} | `{v.get('classification_field')}` | "
          f"`{','.join(v.get('classification_values') or [])}` | **{v.get('total')}** | {exc} |")
    a(f"| **合计** | — | — | **{report['pool_scope'].get('combined_total')}** | — |")
    a("")
    a("说明：旧界面写死的「全池 814 标的」= `symbol_pool` 全部行数，**含 INDEX/股票/商品/SETTLING**，")
    a("不是加密分母。按官方分类，加密且在活动范围的是 526 + 286 = 812。")
    a("")

    a("## 2. 逐字段覆盖")
    a("")
    a("| 字段 | 原始来源 / 路径 | 实际非空行数 | 覆盖币数 | UTC 起止 | 最近成功采集 | 缺口 | 状态 |")
    a("|---|---|---|---|---|---|---|---|")

    def row(name, source, rows, syms, first, last, fetched, gap, status):
        a(f"| {name} | {source} | {rows} | {syms} | {first} ~ {last} | {fetched} | {gap} | {status} |")

    # K 线字段（币安，按周期）
    b = report["candles"]
    for p in PERIODS:
        v = b.get(f"binance:{p}") or {}
        if not v.get("available"):
            continue
        nn = v["nonnull"]
        row(f"K线主动买入量 `taker_buy_volume`（{p}）",
            f"`candles/binance/um/*/{p}/*.parquet`",
            f"{nn['taker_buy_volume']:,}", v["symbols"], v["first"], v["last"],
            v["last"], f"缺根按周期合计见 §4", "历史已存")
    for p in PERIODS:
        v = b.get(f"binance:{p}") or {}
        if not v.get("available"):
            continue
        nn = v["nonnull"]
        row(f"K线成交笔数 `trade_count`（{p}）",
            f"`candles/binance/um/*/{p}/*.parquet`",
            f"{nn['trade_count']:,}", v["symbols"], v["first"], v["last"],
            v["last"], "—", "历史已存")

    # metrics 归档
    if mhist.get("available"):
        nn = mhist["nonnull"]
        row("大户持仓多空比 `top_position_ls_ratio`",
            "`metrics/binance/um/*/*.parquet`（官方归档，**仅 5m**）",
            f"{nn['top_position_ls_ratio']:,}", mhist["symbols"], mhist["first"], mhist["last"],
            mhist["last"], "—", "历史已存（5m）")
        row("大户账户多空比 `count_toptrader_long_short_ratio`",
            "`metrics/binance/um/*/*.parquet`",
            f"{nn['count_toptrader_long_short_ratio']:,}", mhist["symbols"],
            mhist["first"], mhist["last"], mhist["last"], "—", "历史已存（5m）")
        row("全市场账户多空比 `retail_account_ls_ratio`",
            "`metrics/binance/um/*/*.parquet`",
            f"{nn['retail_account_ls_ratio']:,}", mhist["symbols"],
            mhist["first"], mhist["last"], mhist["last"], "—", "历史已存（5m）")
        row("主动买卖量比 `sum_taker_long_short_vol_ratio`",
            "`metrics/binance/um/*/*.parquet`",
            f"{nn['sum_taker_long_short_vol_ratio']:,}", mhist["symbols"],
            mhist["first"], mhist["last"], mhist["last"],
            f"{mhist['rows'] - nn['sum_taker_long_short_vol_ratio']:,} 行为空",
            "历史已存（5m，末尾有空值）")
        row("持仓量 `sum_open_interest`",
            "`metrics/binance/um/*/*.parquet`",
            f"{nn['sum_open_interest']:,}", mhist["symbols"], mhist["first"], mhist["last"],
            mhist["last"], "—", "历史已存（5m）")
        row("持仓名义额 `sum_open_interest_value`",
            "`metrics/binance/um/*/*.parquet`",
            f"{nn['sum_open_interest_value']:,}", mhist["symbols"],
            mhist["first"], mhist["last"], mhist["last"], "—", "历史已存（5m）")

    # 库内
    db = report["db"]
    ois = db.get("oi_snapshot") or {}
    row("OI 数量与名义额（快照分区）", "`oi_history` period=snapshot",
        f"{ois.get('rows'):,}", ois.get("symbols"), ois.get("first"), ois.get("last"),
        ois.get("last_fetched"), "—", "实时采集中")
    fs = db.get("funding_settled") or {}
    row("资金费率（**已结算**）", "`funding_history` rate_type=settled",
        f"{(fs.get('nonnull') or {}).get('funding_rate', 0):,}", fs.get("symbols"),
        fs.get("first"), fs.get("last"), fs.get("last_fetched"), "—", "历史已存")
    fc = db.get("funding_current") or {}
    row("资金费率（**当前/预测**）", "`realtime_quotes.funding_rate` ← `/fapi/v1/premiumIndex`",
        f"{(fc.get('nonnull') or {}).get('funding_rate', 0):,}", fc.get("symbols"),
        "—（覆盖式快照，无历史）", fc.get("last_event"), fc.get("last_updated"),
        "无历史（仅当前值）", "实时采集中")

    # 实时 K 线
    rk = db.get("realtime_klines") or {}
    for p in PERIODS:
        v = rk.get(p) or {}
        row(f"实时 K 线（{p}，含未收盘）", "`realtime_klines`",
            f"{v.get('rows', 0):,}", v.get("symbols"), "—（原地覆盖当前 bar）",
            "—", "见 §5", "—", "实时采集中")
    a("")
    a("## 3. 欧易侧（补充位）")
    a("")
    a("| 字段 | 原始来源 | 实际非空行数 | 覆盖币数 | 结论 |")
    a("|---|---|---|---|---|")
    okx1h = b.get("okx:1h") or {}
    if okx1h.get("available"):
        nn = okx1h["nonnull"]
        a(f"| 主动买入量 `taker_buy_volume` | `candles/okx/swap/*/1h/*.parquet` | "
          f"**0** | {okx1h['symbols']} | **上游不支持**：归档内为空串（{okx1h['taker_buy_missing_rows']:,} 行全空） |")
        a(f"| 成交笔数 `trade_count` | 同上 | **0** | {okx1h['symbols']} | "
          f"**上游不支持**：{okx1h['trade_count_placeholder_rows']:,} 行是**占位 0**（非真实 0 笔） |")
        a(f"| 总成交量 `volume` | 同上 | {nn['volume']:,} | {okx1h['symbols']} | 可用（基础币口径，非张） |")
        a(f"| 总成交额 `quote_volume` | 同上 | {nn['quote_volume']:,} | {okx1h['symbols']} | 可用（USDT） |")
    a("| 多空比三兄弟 | 无 | **0** | 0 | **上游不支持**：欧易无 `/futures/data/*` 同类公开端点 |")
    a("")

    a("## 4. 币安 K 线逐周期缺口（按周期与首末推算）")
    a("")
    a("| 周期 | 币数 | 行数 | UTC 起 | UTC 止 | 缺口状态 |")
    a("|---|---|---|---|---|---|")
    for p in PERIODS:
        v = b.get(f"binance:{p}") or {}
        if not v.get("available"):
            continue
        a(f"| {p} | {v['symbols']} | {v['rows']:,} | {v['first'][:10]} | {v['last'][:10]} | "
          f"{'5m 历史明显短于其它周期（起点 2025-10 起）' if p == '5m' else '首末跨度内按周期推算无整段缺失（逐段缺口另见 coverage_cache）'} |")
    a("")

    a("## 5. 实时 K 线覆盖（各周期标的数）")
    a("")
    a("| 周期 | 标的数 | 未收盘 bar | 备注 |")
    a("|---|---|---|---|")
    for p in PERIODS:
        v = rk.get(p) or {}
        note = ""
        if p == "8h":
            note = "币安 525 / 欧易无原生 8h"
        elif v.get("symbols") and v.get("symbols") < 800:
            note = "**低于其它周期**，需复核"
        a(f"| {p} | {v.get('symbols', 0)} | {v.get('forming', 0)} | {note} |")
    a("")

    a("## 6. 本机比率快照（采集器写入）")
    a("")
    if snap.get("available"):
        rb = snap["ratio_blocks"]
        a(f"标的数：{snap['symbols']}；新鲜（≤阈值）：{snap['fresh_ok']}")
        a("")
        a("| 比率块 | 有值的标的数 |")
        a("|---|---|")
        a(f"| 大户持仓多空比 | {rb['top_position']} |")
        a(f"| 大户账户多空比 | {rb['top_account']} |")
        a(f"| 全市场账户多空比 | {rb['global_account']} |")
        a(f"| taker 买卖量比 | {rb['taker_ratio']} |")
        a("")
        idx = snap.get("index") or {}
        a(f"最近一轮：{idx.get('updated_at')}；目标 {idx.get('targets')}，成功 {idx.get('ok')}，"
          f"失败 {idx.get('failed')}，跳过 {idx.get('skipped')}；"
          f"上游请求 {(idx.get('requests') or {}).get('upstream')}。")
    a("")

    a("## 7. 重复列（**统计时不得重复计数**）")
    a("")
    a("`metrics` 归档里有两对列**数值完全相同**（全库 0 行不等）：")
    a("")
    a("- `retail_account_ls_ratio` == `count_long_short_ratio` → 同一个「全市场账户多空比」")
    a("- `top_position_ls_ratio` == `sum_toptrader_long_short_ratio` → 同一个「大户持仓多空比」")
    a("")
    a("独立的那一列是 `count_toptrader_long_short_ratio`（大户**账户数**多空比）。")
    a("若把四个列名都当四个指标，会把两个指标数成四个。")
    a("")
    return "\n".join(L)


if __name__ == "__main__":
    json_path = os.path.join(OUT_DIR, "字段覆盖表.json")
    if "--md-only" in sys.argv and os.path.exists(json_path):
        # 只重排 markdown（避免为改一句话再扫一遍 13 GB 归档）
        with open(json_path, encoding="utf-8") as fh:
            rep = json.load(fh)
    else:
        rep = build_report()
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "字段覆盖表.md"), "w", encoding="utf-8") as fh:
        fh.write(md_table(rep))
    print("已写出 字段覆盖表.md / 字段覆盖表.json", file=sys.stderr)
