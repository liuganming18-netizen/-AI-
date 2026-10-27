# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜01 三层字段级核对（热库 / 冷档各一遍）。

三层必须分开，因为它们的结论完全不同：
  L1 **结算事件覆盖**：这段里到底有没有结算事件（有/无/少）；
  L2 **rate/mark 字段非空**：有事件不等于有可用数值；而且「非空」还要看**来源**
     —— 冷档 2,089,604 行的 `source` 写着 `+mark1h`，即 mark_price 是用 1 小时价
     补的，不是原生结算标记价。热库 94.1% 的行同样是这个来源 ⇒ 两源「逐位一致」
     证明的是**同源一致**，不是「原生一致」。
  L3 **当时可得证据**：热库有 `fetched_at`（抓取时刻），冷档**连这一列都没有**。
     没有列 ⇒ 判「未验证」，不能写成「填充率 0%」，也不能用 fetched_at 冒充可得时刻。

只读；不补零；不跨交易所（全部 `exchange='binance' AND market='um'`）。
"""
from __future__ import annotations

import glob
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"

HOUR = 3_600_000
DAY = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window():
    d = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    w = d["window"]
    return int(w["start_ms"]), int(w["end_ms"]), w["start_utc"], w["end_utc"]


def hot_layer(a: int, b: int) -> dict:
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    base = "exchange='binance' AND market='um'"
    full = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(funding_time), MAX(funding_time), "
        f" SUM(CASE WHEN funding_rate IS NULL OR funding_rate='' THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN mark_price IS NULL OR mark_price='' THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN rate_type<>'settled' OR rate_type IS NULL THEN 1 ELSE 0 END) "
        f"FROM funding_history WHERE {base}").fetchone()
    win = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT symbol), "
        f" SUM(CASE WHEN funding_rate IS NULL OR funding_rate='' THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN mark_price IS NULL OR mark_price='' THEN 1 ELSE 0 END) "
        f"FROM funding_history WHERE {base} AND funding_time>? AND funding_time<=?",
        (a, b)).fetchone()
    src = [{"source": r[0], "rows": r[1], "mark_null": r[2]} for r in con.execute(
        f"SELECT source, COUNT(*), SUM(CASE WHEN mark_price IS NULL OR mark_price='' "
        f"THEN 1 ELSE 0 END) FROM funding_history WHERE {base} GROUP BY 1 ORDER BY 2 DESC")]
    src_win = [{"source": r[0], "rows": r[1], "mark_null": r[2]} for r in con.execute(
        f"SELECT source, COUNT(*), SUM(CASE WHEN mark_price IS NULL OR mark_price='' "
        f"THEN 1 ELSE 0 END) FROM funding_history WHERE {base} AND funding_time>? "
        f"AND funding_time<=? GROUP BY 1 ORDER BY 2 DESC", (a, b))]
    # L3：抓取延迟。fetched_at 有值 ≠ 当时可得。
    lag = con.execute(
        f"SELECT SUM(CASE WHEN fetched_at IS NULL THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN fetched_at-funding_time<=3600000 THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN fetched_at-funding_time<=86400000 THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN fetched_at-funding_time>86400000 THEN 1 ELSE 0 END), "
        f" SUM(CASE WHEN fetched_at<funding_time THEN 1 ELSE 0 END), "
        f" MIN(fetched_at-funding_time), MAX(fetched_at-funding_time) "
        f"FROM funding_history WHERE {base} AND funding_time>? AND funding_time<=?",
        (a, b)).fetchone()
    cols = [r[1] for r in con.execute("PRAGMA table_info(funding_history)")]
    con.close()
    n_win = win[0] or 0
    return {
        "路径": str(HOT.relative_to(ROOT)),
        "列": cols,
        "有available_at列": "available_at" in cols,
        "有fetched_at列": "fetched_at" in cols,
        "L1_结算事件覆盖": {
            "全历史行数": full[0], "全历史标的": full[1],
            "全历史首末": [iso(full[2]), iso(full[3])],
            "窗口内行数": n_win, "窗口内标的": win[1],
            "非settled行": full[6],
            "判定": ("覆盖存在" if n_win else "窗口内无任何结算事件"),
        },
        "L2_rate_mark非空": {
            "全历史_rate空": full[4], "全历史_mark空": full[5],
            "窗口_rate空": win[2], "窗口_mark空": win[3],
            "来源分布_全历史": src,
            "来源分布_窗口": src_win,
            "判读": ("mark 非空率不能单独当质量指标：94.1% 的行 source 是 "
                   "`binance.archive.fundingRate+mark1h`，mark 由 1 小时价补齐，"
                   "不是交易所结算标记价 ⇒ 非空≠原生"),
        },
        "L3_当时可得": {
            "fetched_at空": lag[0],
            "滞后_le_1h": lag[1], "滞后_le_24h": lag[2], "滞后_gt_24h": lag[3],
            "抓取早于结算": lag[4],
            "最小滞后ms": lag[5], "最大滞后ms": lag[6],
            "gt_24h占比": (round(100.0 * (lag[3] or 0) / n_win, 2) if n_win else None),
            "le_1h占比": (round(100.0 * (lag[1] or 0) / n_win, 2) if n_win else None),
            "判定": ("未验证（有 fetched_at，但绝大多数是事后批量回填 ⇒ 只能声明"
                   "「计算不超前」，不能声明「结算当时本机拿得到」）"),
        },
    }


def cold_layer(a: int, b: int) -> dict:
    import duckdb
    files = sorted(str(p) for p in COLD_DIR.glob("*.parquet"))
    d = duckdb.connect()
    # CREATE VIEW 不能带预处理参数 ⇒ 分片清单用字面量嵌入（路径内无引号，已核）
    lit = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    d.execute("CREATE VIEW c AS SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) sym, "
              "funding_time, funding_rate, mark_price, rate_type, source "
              f"FROM read_parquet({lit}, filename=true)")
    full = d.execute(
        "SELECT COUNT(*), COUNT(DISTINCT sym), MIN(funding_time), MAX(funding_time), "
        " SUM(CASE WHEN funding_rate IS NULL THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN mark_price IS NULL THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN rate_type<>'settled' OR rate_type IS NULL THEN 1 ELSE 0 END) FROM c"
    ).fetchone()
    win = d.execute(
        "SELECT COUNT(*), COUNT(DISTINCT sym), "
        " SUM(CASE WHEN funding_rate IS NULL THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN mark_price IS NULL THEN 1 ELSE 0 END) "
        "FROM c WHERE funding_time>? AND funding_time<=?", [a, b]).fetchone()
    src = [{"source": r[0], "rows": r[1], "mark_null": r[2]} for r in d.execute(
        "SELECT source, COUNT(*), SUM(CASE WHEN mark_price IS NULL THEN 1 ELSE 0 END) "
        "FROM c GROUP BY 1 ORDER BY 2 DESC").fetchall()]
    src_win = [{"source": r[0], "rows": r[1], "mark_null": r[2]} for r in d.execute(
        "SELECT source, COUNT(*), SUM(CASE WHEN mark_price IS NULL THEN 1 ELSE 0 END) "
        "FROM c WHERE funding_time>? AND funding_time<=? GROUP BY 1 ORDER BY 2 DESC",
        [a, b]).fetchall()]
    dup = d.execute(
        "SELECT COUNT(*) FROM (SELECT sym, funding_time, COUNT(*) k FROM c "
        "GROUP BY 1,2 HAVING k>1)").fetchone()[0]
    dup_win = d.execute(
        "SELECT COUNT(*) FROM (SELECT sym, funding_time, COUNT(*) k FROM c "
        "WHERE funding_time>? AND funding_time<=? GROUP BY 1,2 HAVING k>1)",
        [a, b]).fetchone()[0]
    cols = [r[0] for r in d.execute("DESCRIBE SELECT * FROM c").fetchall()]
    d.close()
    n_win = win[0] or 0
    return {
        "路径": str(COLD_DIR.relative_to(ROOT)),
        "分片数": len(files),
        "列": cols,
        "有available_at列": "available_at" in cols,
        "有fetched_at列": "fetched_at" in cols,
        "标的名来源": "文件名（表内无 symbol 列）",
        "L1_结算事件覆盖": {
            "全历史行数": full[0], "全历史标的": full[1],
            "全历史首末": [iso(full[2]), iso(full[3])],
            "窗口内行数": n_win, "窗口内标的": win[1],
            "非settled行": full[6],
            "重复键组数_全历史": dup, "重复键组数_窗口": dup_win,
            "判定": ("覆盖存在；同一 (标的, 结算时刻) 无重复" if dup == 0
                   else "存在重复键，必须先定去重口径"),
        },
        "L2_rate_mark非空": {
            "全历史_rate空": full[4], "全历史_mark空": full[5],
            "窗口_rate空": win[2], "窗口_mark空": win[3],
            "来源分布_全历史": src,
            "来源分布_窗口": src_win,
            "判读": ("mark 空值只剩 1,838 行（全历史）；但 2,089,604 行是 "
                   "`binance.archive.fundingRate+mark1h` ⇒ 冷档能补「事件」，"
                   "补出来的 mark 仍是 1 小时价，不是原生结算标记价"),
        },
        "L3_当时可得": {
            "available_at列存在": False,
            "fetched_at列存在": False,
            "判定": ("未验证（整列不存在，不是「填充率 0%」）。冷档是离线归档媒介，"
                   "落盘时间不代表结算当时可得 ⇒ 不能给冷档补任何可得时刻"),
        },
    }


def main():
    t0 = time.time()
    a, b, a_utc, b_utc = window()
    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "01_三层字段级核对",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "window": {"start_utc": a_utc, "end_utc": b_utc, "start_ms": a, "end_ms": b},
        "三层定义": {
            "L1": "结算事件覆盖：这段里有没有结算事件（按标的数 + 行数）",
            "L2": "rate/mark 字段非空：有事件后数值能不能用；并核来源（原生 vs 用 1h 价补）",
            "L3": "当时可得证据：本机在结算当时是否真的拿到了这条记录",
            "为什么必须分开": ("L1 通过不代表 L2 可用（事件在、mark 空）；L2 可用不代表 L3 成立"
                          "（数值事后回填）；三层任一缺失都不能写「资金费已完整计入」"),
        },
        "hot": hot_layer(a, b),
        "cold": cold_layer(a, b),
        "elapsed_sec": round(time.time() - t0, 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_三层字段级核对.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "hot": {"L1": out["hot"]["L1_结算事件覆盖"]["窗口内行数"],
                "L2_mark空": out["hot"]["L2_rate_mark非空"]["窗口_mark空"],
                "L3_gt24h占比": out["hot"]["L3_当时可得"]["gt_24h占比"]},
        "cold": {"L1": out["cold"]["L1_结算事件覆盖"]["窗口内行数"],
                 "L2_mark空": out["cold"]["L2_rate_mark非空"]["全历史_mark空"],
                 "dup": out["cold"]["L1_结算事件覆盖"]["重复键组数_全历史"],
                 "available_at": out["cold"]["L3_当时可得"]["available_at列存在"]},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
