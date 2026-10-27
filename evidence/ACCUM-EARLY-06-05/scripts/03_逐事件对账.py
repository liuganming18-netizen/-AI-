# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜03 逐事件对账：来源 / 缺失 / 重复 / 冲突。

一张表回答四件事（键 = 标的 + 结算时刻）
----------------------------------------
· **来源**：`both`（两源都有）/ `hot_only` / `cold_only`；
· **缺失**：并集里该有而没有的期望结算 —— 用 02 号脚本的「可靠网格」结论，
  不可推算的标的单列，不硬凑数；
· **重复**：同一 (标的, 时刻) 出现多次（冷档实测 0，热库靠唯一索引 0）；
· **冲突**：两源同键但数值不同 —— 分四类：rate 不同 / mark 不同 / mark 一边空 /
  rate 一边空。**「空 vs 有值」也算冲突**：`settle_funding` 缺 mark 会记 funding_gap
  且不计成本，等于把这条结算当成免费。

为什么必须查冲突
----------------
冷档 mark_price 的 `source` 有 2,089,604 行写着 `+mark1h`（用 1 小时价补的），
热库 94.1% 的行也是这个来源 ⇒ 两源「逐位一致」只证明**同源一致**，
不能证明「与交易所结算标记价一致」。这一点在报告里必须写清。

只读；不补零；不跨交易所（只 binance/um）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"

RATE_TOL = 1e-12
MARK_TOL = 1e-9


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    import duckdb
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    a = int(anchor["window"]["start_ms"]); b = int(anchor["window"]["end_ms"])
    syms48 = anchor["universe"]["symbols"]
    s48 = "[" + ",".join("'" + s + "'" for s in syms48) + "]"

    files = sorted(str(p) for p in COLD_DIR.glob("*.parquet"))
    lit = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    d = duckdb.connect()
    d.execute(f"ATTACH '{HOT}' AS hot (READ_ONLY)")
    d.execute(f"""
        CREATE TABLE j AS
        WITH c AS (
          SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) AS sym, funding_time,
                 funding_rate AS c_rate, mark_price AS c_mark, source AS c_source
          FROM read_parquet({lit}, filename=true)
          WHERE funding_time > {a} AND funding_time <= {b}),
        h AS (
          SELECT symbol AS sym, funding_time, CAST(funding_rate AS DOUBLE) AS h_rate,
                 CAST(mark_price AS DOUBLE) AS h_mark, source AS h_source, fetched_at
          FROM hot.funding_history
          WHERE exchange='binance' AND market='um'
            AND rate_type='settled' AND funding_time > {a} AND funding_time <= {b})
        SELECT COALESCE(c.sym,h.sym) AS sym,
               COALESCE(c.funding_time,h.funding_time) AS funding_time,
               c.c_rate, c.c_mark, c.c_source, h.h_rate, h.h_mark, h.h_source, h.fetched_at,
               CASE WHEN c.sym IS NOT NULL AND h.sym IS NOT NULL THEN 'both'
                    WHEN h.sym IS NOT NULL THEN 'hot_only'
                    ELSE 'cold_only' END AS src,
               (c.sym IS NOT NULL AND h.sym IS NOT NULL
                AND ABS(c.c_rate - h.h_rate) > {RATE_TOL}) AS rate_conflict,
               (c.sym IS NOT NULL AND h.sym IS NOT NULL
                AND c.c_mark IS NOT NULL AND h.h_mark IS NOT NULL
                AND ABS(c.c_mark - h.h_mark) > {MARK_TOL}) AS mark_conflict,
               (c.sym IS NOT NULL AND h.sym IS NOT NULL
                AND ((c.c_mark IS NULL) <> (h.h_mark IS NULL))) AS mark_null_mismatch,
               (c.sym IS NOT NULL AND h.sym IS NOT NULL
                AND ((c.c_rate IS NULL) <> (h.h_rate IS NULL))) AS rate_null_mismatch
        FROM c FULL OUTER JOIN h ON c.sym = h.sym AND c.funding_time = h.funding_time
    """)

    def agg(where: str) -> dict:
        r = d.execute(f"""
            SELECT COUNT(*) n,
              SUM(CASE WHEN src='both' THEN 1 ELSE 0 END) n_both,
              SUM(CASE WHEN src='hot_only' THEN 1 ELSE 0 END) n_hot_only,
              SUM(CASE WHEN src='cold_only' THEN 1 ELSE 0 END) n_cold_only,
              SUM(CASE WHEN rate_conflict THEN 1 ELSE 0 END) n_rate_conflict,
              SUM(CASE WHEN mark_conflict THEN 1 ELSE 0 END) n_mark_conflict,
              SUM(CASE WHEN mark_null_mismatch THEN 1 ELSE 0 END) n_mark_null_mismatch,
              SUM(CASE WHEN rate_null_mismatch THEN 1 ELSE 0 END) n_rate_null_mismatch,
              COUNT(DISTINCT sym) n_sym
            FROM j {where}""").fetchone()
        keys = ("n_events", "n_both", "n_hot_only", "n_cold_only", "n_rate_conflict",
                "n_mark_conflict", "n_mark_null_mismatch", "n_rate_null_mismatch", "n_symbols")
        return dict(zip(keys, [int(x or 0) for x in r]))

    # 重复键：并集内部不可能重复（FULL OUTER JOIN 一对一），所以分别查两源
    dup_cold = d.execute("""
        SELECT COUNT(*) FROM (SELECT sym, funding_time, COUNT(*) k FROM (
          SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) sym, funding_time
          FROM read_parquet({lit}, filename=true)
          WHERE funding_time > {a} AND funding_time <= {b}) GROUP BY 1,2 HAVING k>1)
    """.format(lit=lit, a=a, b=b)).fetchone()[0]
    dup_hot = d.execute("""
        SELECT COUNT(*) FROM (SELECT symbol, funding_time, COUNT(*) k FROM hot.funding_history
          WHERE exchange='binance' AND market='um' AND funding_time > ? AND funding_time <= ?
          GROUP BY 1,2 HAVING k>1)""", [a, b]).fetchone()[0]

    # 冲突明细（按标的聚合 + 前 20 条逐事件）
    conf_rows = d.execute("""
        SELECT sym, funding_time, src, c_rate, h_rate, c_mark, h_mark,
               rate_conflict, mark_conflict, mark_null_mismatch, rate_null_mismatch,
               c_source, h_source
        FROM j WHERE rate_conflict OR mark_conflict OR mark_null_mismatch OR rate_null_mismatch
        ORDER BY funding_time LIMIT 200""").fetchall()
    conf_cols = [c[0] for c in d.description]
    conflicts = []
    for r in conf_rows:
        row = dict(zip(conf_cols, r))
        row["funding_time_utc"] = iso(row.pop("funding_time"))
        conflicts.append(row)

    # 逐事件明细：母集 48 全量 + 全池异常行
    d.execute(f"""
        COPY (SELECT sym, funding_time, src, c_rate, h_rate, c_mark, h_mark,
                     c_source, h_source, fetched_at, rate_conflict, mark_conflict,
                     mark_null_mismatch, rate_null_mismatch
              FROM j WHERE sym IN {s48} ORDER BY sym, funding_time)
        TO '{(OUT / "03_逐事件明细_母集48.csv").as_posix()}' (HEADER, DELIMITER ',')""")
    n_csv48 = d.execute(
        f"SELECT COUNT(*) FROM j WHERE sym IN {s48}").fetchone()[0]
    d.execute("""
        COPY (SELECT sym, funding_time, src, c_rate, h_rate, c_mark, h_mark,
                     c_source, h_source, fetched_at, rate_conflict, mark_conflict,
                     mark_null_mismatch, rate_null_mismatch
              FROM j WHERE src <> 'both' OR rate_conflict OR mark_conflict
                     OR mark_null_mismatch OR rate_null_mismatch
              ORDER BY sym, funding_time)
        TO '"""+ (OUT / "03_逐事件明细_异常.csv").as_posix() + """' (HEADER, DELIMITER ',')""")
    n_csv_abn = d.execute("""
        SELECT COUNT(*) FROM j WHERE src <> 'both' OR rate_conflict OR mark_conflict
               OR mark_null_mismatch OR rate_null_mismatch""").fetchone()[0]

    # 冷档独有的结算事件：按标的 + 按月分布，供「能补多少」的量化
    cold_only_by_sym = d.execute("""
        SELECT sym, COUNT(*) n, MIN(funding_time), MAX(funding_time)
        FROM j WHERE src='cold_only' GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    cold_only_month = d.execute("""
        SELECT strftime(to_timestamp(funding_time/1000), '%Y-%m') m, COUNT(*) n
        FROM j WHERE src='cold_only' GROUP BY 1 ORDER BY 1""").fetchall()
    hot_only_month = d.execute("""
        SELECT strftime(to_timestamp(funding_time/1000), '%Y-%m') m, COUNT(*) n
        FROM j WHERE src='hot_only' GROUP BY 1 ORDER BY 1""").fetchall()

    # 02 的缺失结论（不在本脚本重算，只引用并注明来源）
    p2 = json.loads((OUT / "02_原生结算周期与缺口段.json").read_text(encoding="utf-8"))

    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "03_逐事件对账",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "window": anchor["window"],
        "键": "(标的, 结算时刻)；时刻为原始毫秒，不截秒",
        "容差": {"rate": RATE_TOL, "mark": MARK_TOL,
                 "为什么": "两源同源（archive 导入 + fapi 增量），一致时应逐位相同；"
                        "留极小容差只为吸收浮点表示，不吸收口径差"},
        "全池": agg(""),
        "母集48": agg(f"WHERE sym IN {s48}"),
        "重复": {"cold_dup_groups": int(dup_cold), "hot_dup_groups": int(dup_hot),
                 "说明": "热库唯一索引 (exchange,market,symbol,funding_time) 保证 0；冷档实测 0"},
        "冲突": {"总数": len(conflicts), "样例": conflicts,
                 "四类定义": ["rate_conflict：两源 rate 差 > 1e-12",
                          "mark_conflict：两源 mark 差 > 1e-9",
                          "mark_null_mismatch：一边 mark 空一边有值（settle_funding 会记 funding_gap）",
                          "rate_null_mismatch：一边 rate 空一边有值"]},
        "冷档独有事件": {
            "symbols": len(cold_only_by_sym),
            "top20": [{"symbol": r[0], "n": int(r[1]), "first_utc": iso(r[2]),
                       "last_utc": iso(r[3])} for r in cold_only_by_sym[:20]],
            "按月": [{"month": r[0], "n": int(r[1])} for r in cold_only_month],
        },
        "热库独有事件": {
            "symbols": d.execute("SELECT COUNT(DISTINCT sym) FROM j WHERE src='hot_only'"
                                 ).fetchone()[0],
            "按月": [{"month": r[0], "n": int(r[1])} for r in hot_only_month],
        },
        "缺失": {
            "来源": "02_原生结算周期与缺口段.json（不在此重算，避免两处口径分叉）",
            "并集缺失_全池": p2["聚合"]["all"]["missing_union"],
            "并集缺失_母集48": p2["聚合"]["mother_48"]["missing_union"],
            "热库缺失_全池": p2["聚合"]["all"]["missing_hot"],
            "热库缺失_母集48": p2["聚合"]["mother_48"]["missing_hot"],
            "不可推算标的数": p2["周期不单一_缺口不可推算"]["count"],
            "读法": "并集缺失=0 且不可推算标的不在母集内 ⇒ 母集 48 的结算事件可判「齐」；"
                   "全池仍有不可推算标的，不能推广",
        },
        "明细文件": {
            "母集48": {"file": "03_逐事件明细_母集48.csv", "rows": int(n_csv48)},
            "异常行": {"file": "03_逐事件明细_异常.csv", "rows": int(n_csv_abn),
                    "内容": "cold_only / hot_only / 四类冲突"},
        },
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (OUT / "03_逐事件对账.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"全池": out["全池"], "母集48": out["母集48"],
                      "重复": out["重复"], "冲突数": len(conflicts),
                      "冷档独有标的数": out["冷档独有事件"]["symbols"],
                      "明细行": out["明细文件"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
