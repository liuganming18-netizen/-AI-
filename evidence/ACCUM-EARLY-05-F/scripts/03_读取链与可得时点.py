# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜03 读取链与可得时点（只读）。

回答「决策实际读取」这一层，而不是停在「文件存在」：
  A. 策略到底要哪些字段、由哪段代码读、读哪个周期（带行号取证）；
  B. 04-A 的成交 bar 究竟来自冷档还是热库 —— 按热库 15m 起点切分统计；
  C. 资金费 / 合约规格 / 持仓量 三项非 K 线输入的可得性状态；
  D. 抽样对撞：04-A 产物里记录的 bars_4h/bars_1h 与本单独立统计的冷档行数是否一致
     （抽样只作「读取链确实落到冷档」的证据，不推广为全范围结论）。

只读，产物落本单 out/。
"""
from __future__ import annotations

import glob
import json
import re
import sqlite3
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
CANDLES = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"
RULES = ROOT / "evidence" / "ACCUM-EARLY-04-A" / "rules" / "accum_early_rules.py"
EVID = ROOT / "evidence"


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def line_of(pattern: str, path: Path = RULES) -> list:
    """取证用：把「哪一行用到这个字段」记下来，不手抄行号。"""
    out = []
    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(pattern, ln):
            out.append({"line": i, "text": ln.strip()[:120]})
    return out[:6]


# ── A. 字段需求表（策略实际需要的输入，逐项落到代码）─────────────────────
def field_requirements() -> list:
    return [
        {"timeframe": "4h", "field": "open/high/low/close",
         "用途": "42 根箱体 H/L、Wilder ATR(14)、宽度 2~8A、ER",
         "代码": line_of(r'\["high"\] for b in bars_4h'),
         "实际读取": True, "必需": True},
        {"timeframe": "4h", "field": "volume",
         "用途": "箱体候选量能基准（冻结规则 V20）",
         "代码": line_of(r'o, h, lo_, c, v = b\["open"\]'),
         "实际读取": True, "必需": True},
        {"timeframe": "4h", "field": "low",
         "用途": "跟踪止损：入场后新收盘 4h 的最低价 ×95%",
         "代码": line_of(r'cand = float\(h4\[h4_ptr\]\["low"\]\)'),
         "实际读取": True, "必需": True},
        {"timeframe": "1h", "field": "high/low",
         "用途": "局部压制位（左右各 3 根摆动高点）、局部支撑（前 24 根最低）",
         "代码": line_of(r'bars_1h\[j\]\["high"\]'),
         "实际读取": True, "必需": True},
        {"timeframe": "1h", "field": "close/open",
         "用途": "确认收上压制位、失效判定、收阳/收阴",
         "代码": line_of(r'prev_close = bars_1h\[i - 1\]\["close"\]'),
         "实际读取": True, "必需": True},
        {"timeframe": "1h", "field": "quote_volume + taker_buy_quote_volume",
         "用途": "承接：主动买占比 = 主动买额/成交额；卖压 = 成交额 − 主动买额（推导量）",
         "代码": line_of(r'qv, tbv = bar\.get\("quote_volume"\)'),
         "实际读取": True, "必需": True,
         "风险": "冷档这两列有值，但该根 bar 的 available_at 为空"},
        {"timeframe": "1h", "field": "volume",
         "用途": "原策略破位量能 1.5×V20（前 20 根中位数）",
         "代码": line_of(r'vols = \[x\["volume"\] for x in bars_1h'),
         "实际读取": True, "必需": True},
        {"timeframe": "15m", "field": "open",
         "用途": "成交价：确认后第一根 15m 开盘 ×(1+滑点)",
         "代码": line_of(r'entry_px = float\(entry_bar\["open"\]\)'),
         "实际读取": True, "必需": True},
        {"timeframe": "15m", "field": "low/open/close",
         "用途": "止损判定：低点触及即止损；跳空按开盘价成交",
         "代码": line_of(r'if float\(b\["open"\]\) < stop'),
         "实际读取": True, "必需": True},
        {"timeframe": "15m", "field": "quote_volume",
         "用途": "参与率上限（占上一根 15m 成交额的比例），决定缩量/拒单",
         "代码": [{"line": None, "text": "paper_engine.py::_apply_order_rounding（生产侧）"}],
         "实际读取": "仅生产/04-C 口径", "必需": "取决于是否启用参与率上限"},
        {"timeframe": "—", "field": "funding_rate",
         "用途": "持仓成本（按交易所结算周期，实测 4 小时一次）",
         "代码": [{"line": None, "text": "replay_calc.settle_funding_events → trading_core.funding.settle_funding"}],
         "实际读取": "04-A 未读；04-C/04-D 读", "必需": "正式回测必需，04-A 口径不含"},
        {"timeframe": "—", "field": "合约规格（价格步进/数量步进/最小数量/最小金额）",
         "用途": "下单取整与拒单（min_orderable_qty）",
         "代码": [{"line": None, "text": "paper_engine.contract_limits → 07_526币保守执行近似规格.csv"}],
         "实际读取": "04-A 不读（R 倍数口径绕开数量）；04-C/D 读", "必需": "净额口径必需"},
        {"timeframe": "—", "field": "持仓量 OI",
         "用途": "04-A 明示不参与（历史可得时点不可证）；冻结配置里有 OI 过滤开关",
         "代码": [{"line": None, "text": "04-A discipline: 基线不含 OI 门槛"}],
         "实际读取": False, "必需": False,
         "结论": "按规则用途说明即可，不因缺证明而加门槛"},
    ]


# ── B. 成交 bar 来自哪一层 ────────────────────────────────────────────────
def trade_bar_layers() -> dict:
    hot_first = {}
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    for tf in ("15m", "1h", "4h"):
        r = con.execute("SELECT MIN(open_time) FROM candles WHERE exchange='binance' "
                        "AND market='um' AND timeframe=?", (tf,)).fetchone()
        hot_first[tf] = r[0]
    con.close()

    out = {"hot_first_bar": {k: {"ms": v, "utc": iso(v)} for k, v in hot_first.items()},
           "by_window": {}}
    for tag in ("research_final", "holdout_final"):
        p = EVID / "ACCUM-EARLY-04-A" / "out" / f"01_共同观察范围_{tag}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        fills = []
        for s in d["per_symbol"]:
            for sch, v in (s.get("schemes") or {}).items():
                for t in (v.get("trades") or []):
                    if t.get("status") != "filled":
                        continue
                    fills.append({"symbol": s["symbol"], "scheme": sch,
                                  "entry_ms": t.get("entry_bar_open_ms"),
                                  "exit_ms": t.get("exit_ms"),
                                  "r": t.get("r_multiple")})
        # 层判定：热库 15m 首根之前 ⇒ 只能来自冷档
        h15 = hot_first["15m"]
        only_cold = [f for f in fills if f["entry_ms"] and f["entry_ms"] < h15]
        mixed = [f for f in fills if f["entry_ms"] and f["entry_ms"] >= h15]
        out["by_window"][tag] = {
            "fills": len(fills),
            "entry_only_from_cold": len(only_cold),
            "entry_could_be_hot": len(mixed),
            "cold_share_pct": round(100.0 * len(only_cold) / len(fills), 2) if fills else None,
            "rule": "热库 15m 首根之前没有任何热层数据 ⇒ 这些成交的 bar 只可能来自冷档",
        }
        # 抽样核验：这些成交 bar 在冷档里是否真的存在
        sample = only_cold[:400]
        if sample:
            paths = []
            for s in sorted({f["symbol"] for f in sample}):
                paths += glob.glob(str(CANDLES / s / "15m" / "*.parquet"))
            times = sorted({int(f["entry_ms"]) for f in sample})
            con = duckdb.connect()
            rows = con.execute(
                "SELECT symbol, open_time FROM read_parquet(?) "
                "WHERE list_contains(?, open_time)", [paths, times]).fetchall()
            con.close()
            have = {(r[0], r[1]) for r in rows}
            missing = [f for f in sample if (f["symbol"], int(f["entry_ms"])) not in have]
            out["by_window"][tag]["cold_bar_exists_check"] = {
                "sampled": len(sample), "found": len(sample) - len(missing),
                "missing": len(missing),
                "missing_examples": [{"symbol": f["symbol"], "entry_utc": iso(f["entry_ms"])}
                                     for f in missing[:5]],
                "scope": "抽样上限 400 笔；只证明这些成交 bar 在冷档存在，不推广",
            }
    return out


# ── C. 非 K 线输入的可得性 ────────────────────────────────────────────────
def non_bar_inputs() -> dict:
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    f = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN fetched_at IS NULL THEN 1 ELSE 0 END), "
        "MIN(funding_time), MAX(funding_time), COUNT(DISTINCT symbol), "
        "MIN(fetched_at), MAX(fetched_at) "
        "FROM funding_history").fetchone()
    rate_type = [dict(zip(("rate_type", "n"), r)) for r in con.execute(
        "SELECT rate_type, COUNT(*) FROM funding_history GROUP BY 1")]
    # 抓取延迟分布：fetched_at 是「本机什么时候抓到这一行」，
    # funding_time 是「交易所什么时候结算」。两者差得越远，越说明是事后批量回填。
    lag = con.execute(
        "SELECT "
        " SUM(CASE WHEN fetched_at - funding_time <= 3600000 THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN fetched_at - funding_time <= 86400000 THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN fetched_at - funding_time > 86400000 THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN fetched_at < funding_time THEN 1 ELSE 0 END) "
        "FROM funding_history").fetchone()
    oi = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN fetched_at IS NULL THEN 1 ELSE 0 END), "
        "MIN(ts), MAX(ts), COUNT(DISTINCT symbol) FROM oi_history").fetchone()
    con.close()

    spec = ROOT / "数据" / "研究输入" / "外部迁移_2026-10-16" / "本次离线决策_2026-10-12" / \
        "07_526币保守执行近似规格.csv"
    import csv
    rows = []
    if spec.exists():
        with open(spec, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    dates = sorted({r.get("当前官方材料日期") for r in rows if r.get("当前官方材料日期")})
    hist_proved = sorted({r.get("历史变化已证明") for r in rows})
    purposes = sorted({r.get("用途") for r in rows})
    assumption = sorted({r.get("规格固定假设起止") for r in rows})

    return {
        "funding": {
            "hot_rows": f[0], "fetched_at_null": f[1],
            "fetched_at_null_pct": round(100.0 * f[1] / f[0], 2) if f[0] else None,
            "first_utc": iso(f[2]), "last_utc": iso(f[3]), "symbols": f[4],
            "fetched_at_min_utc": iso(f[5]), "fetched_at_max_utc": iso(f[6]),
            "rate_type": rate_type,
            "fetch_lag": {
                "le_1h": lag[0], "le_24h": lag[1], "gt_24h": lag[2],
                "fetched_before_event": lag[3],
                "gt_24h_pct": round(100.0 * lag[2] / f[0], 2) if f[0] else None,
                "含义": ("fetched_at 有值 ≠ 当时可得。差值 > 24h 的是事后批量回填，"
                         "对历史回放只能算 late/unknown，不能算 proven"),
            },
            "cold_files": len(list((ROOT / "数据" / "市场数据" / "parquet" / "funding" /
                                    "binance" / "um").glob("*.parquet"))),
            "cold_columns": ["funding_time", "funding_rate", "mark_price",
                             "rate_type", "source"],
            "available_at_in_cold": False,
            "结论": ("资金费是「结算后才知道」的量：funding_time 本身是结算时刻；"
                     "热库 fetched_at 100% 有值但绝大多数是事后批量回填 ⇒ "
                     "能对齐结算时刻，不能声明「结算当时本机就拿到了」"),
        },
        "contract_spec": {
            "path": str(spec.relative_to(ROOT)), "exists": spec.exists(),
            "rows": len(rows),
            "official_material_dates": dates,
            "history_change_proved_values": hist_proved,
            "purposes": purposes,
            "assumption_window_text": assumption,
            "结论": ("规格是 2026-10-11 的单时点快照，文件自己写明「历史变化已证明=False」"
                     "「仅拟用于最终证明的研究时段，尚不能确定」⇒ 对 2025 年的回放属于"
                     "用今天的规格，误差方向不可判"),
        },
        "open_interest": {
            "hot_rows": oi[0], "fetched_at_null": oi[1],
            "first_utc": iso(oi[2]), "last_utc": iso(oi[3]), "symbols": oi[4],
            "used_by_04A_rules": False,
            "用途说明": "冻结配置有 OI 过滤开关，但 04-A 的三组规则与基线价格链都不读 OI；"
                        "本单不因 OI 可得时点缺失而给策略加门槛",
        },
    }


# ── D. 抽样对撞：04-A 记的 bars 数 vs 本单独立统计 ────────────────────────
def reader_crosscheck(sample_n: int = 20) -> dict:
    """04-A 的 bars_4h/bars_1h 是它**读取范围**（含预热）的根数，不是窗口内根数。

    所以对比必须按同一范围算：4h 从 w0−400 天、1h 从 w0−30 天、都到 w1+40 天。
    按窗口算会全不一致——那不是缺陷，是口径错。
    """
    p = EVID / "ACCUM-EARLY-04-A" / "out" / "01_共同观察范围_research_final.json"
    if not p.exists():
        return {"applicable": False}
    d = json.loads(p.read_text(encoding="utf-8"))
    w0, w1 = d["window"]["from_ms"], d["window"]["to_ms"]
    DAY = 86_400_000
    scope = {"4h": (w0 - 400 * DAY, w1 + 40 * DAY), "1h": (w0 - 30 * DAY, w1 + 40 * DAY)}
    syms = [s for s in d["per_symbol"] if s.get("bars_4h")][:sample_n]
    paths = []
    for s in syms:
        for tf in ("4h", "1h"):
            paths += glob.glob(str(CANDLES / s["symbol"] / tf / "*.parquet"))
    con = duckdb.connect()
    cmp_rows = []
    for tf, (a, b) in scope.items():
        rows = con.execute(
            "SELECT regexp_extract(filename,'um/([^/]+)/([^/]+)/',1) sym, COUNT(*) "
            "FROM read_parquet(?, filename=true) WHERE is_closed=1 "
            "AND regexp_extract(filename,'um/([^/]+)/([^/]+)/',2)=? "
            "AND open_time >= ? AND open_time < ? GROUP BY 1",
            [paths, tf, a, b]).fetchall()
        mine = {r[0]: r[1] for r in rows}
        for s in syms:
            theirs = s.get("bars_4h" if tf == "4h" else "bars_1h")
            ours = mine.get(s["symbol"])
            cmp_rows.append({"symbol": s["symbol"], "tf": tf, "04A": theirs,
                             "本单冷档": ours,
                             "一致": (theirs == ours) if (theirs is not None and ours is not None) else None})
    con.close()
    ok = [r for r in cmp_rows if r["一致"] is True]
    bad = [r for r in cmp_rows if r["一致"] is False]
    return {
        "scope": (f"抽样 {len(syms)} 个标的 × 2 周期；范围 = 04-A 的实际读取范围"
                  "（4h 前推 400 天 / 1h 前推 30 天 / 都后推 40 天）"),
        "rows": cmp_rows, "matched": len(ok), "mismatched": len(bad),
        "mismatch_examples": bad[:5],
        "note": ("一致 ⇒ 04-A 读的确实是冷档（该窗口热库为 0 行），且读取范围含预热；"
                 "不一致 ⇒ 先查范围口径，不要直接判缺陷"),
    }


# ── C2. 资金费覆盖缺口：热库 vs 冷档 ──────────────────────────────────────
def funding_gap_check(a_ms: int, b_ms: int) -> dict:
    """同一区间，热库 funding_history 与冷档 funding parquet 各有多少结算记录。

    为什么要并排：04-C/04-D 的资金费只从热库读；如果热库缺一段而冷档有，
    那「资金费已计入」这句话就只对了一半。
    """
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    rows = con.execute(
        """
        WITH x AS (
          SELECT symbol, funding_time,
                 LAG(funding_time) OVER (PARTITION BY symbol ORDER BY funding_time) prev
          FROM funding_history WHERE funding_time BETWEEN ? AND ?)
        SELECT symbol, prev, funding_time, (funding_time - prev) / 3600000.0
        FROM x WHERE (funding_time - prev) > 24 * 3600000
        ORDER BY (funding_time - prev) DESC
        """, (a_ms, b_ms)).fetchall()
    hot_cnt = con.execute(
        "SELECT symbol, COUNT(*) FROM funding_history WHERE funding_time BETWEEN ? AND ? "
        "GROUP BY 1", (a_ms, b_ms)).fetchall()
    con.close()

    files = glob.glob(str(ROOT / "数据" / "市场数据" / "parquet" / "funding" /
                         "binance" / "um" / "*.parquet"))
    dcon = duckdb.connect()
    cold_cnt = dcon.execute(
        "SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) sym, COUNT(*) "
        "FROM read_parquet(?) WHERE funding_time BETWEEN ? AND ? GROUP BY 1",
        [files, a_ms, b_ms]).fetchall()
    dcon.close()

    hot = {r[0]: r[1] for r in hot_cnt}
    cold = {r[0]: r[1] for r in cold_cnt}
    syms = sorted(set(hot) | set(cold))
    worse = [{"symbol": s, "hot": hot.get(s, 0), "cold": cold.get(s, 0)}
             for s in syms if cold.get(s, 0) > hot.get(s, 0)]
    worse.sort(key=lambda x: x["cold"] - x["hot"], reverse=True)
    seg = {}
    for s, prev, cur, hrs in rows:
        key = (iso(prev), iso(cur))
        seg.setdefault(key, {"from_utc": key[0], "to_utc": key[1],
                             "symbols": 0, "max_hours": 0})
        seg[key]["symbols"] += 1
        seg[key]["max_hours"] = max(seg[key]["max_hours"], round(hrs, 1))
    segs = sorted(seg.values(), key=lambda x: x["symbols"], reverse=True)
    return {
        "window": {"from_utc": iso(a_ms), "to_utc": iso(b_ms)},
        "hot_symbols": len(hot), "cold_symbols": len(cold),
        "hot_rows": sum(hot.values()), "cold_rows": sum(cold.values()),
        "symbols_cold_more_than_hot": len(worse),
        "cold_surplus_rows": sum(w["cold"] - w["hot"] for w in worse),
        "worst_10": worse[:10],
        "hot_gap_segments": segs[:5],
        "conclusion": ("冷档记录数多于热库 ⇒ 热库缺、冷档有；"
                       "凡只读热库的资金费核算都会在这段里少算"),
    }


def main():
    facts = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "field_requirements": field_requirements(),
        "trade_bar_layers": trade_bar_layers(),
        "non_bar_inputs": non_bar_inputs(),
        "funding_coverage_gap": funding_gap_check(
            1787184000000,   # 2026-08-25
            1790812800000),  # 2026-10-01
        "reader_crosscheck": reader_crosscheck(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "03_读取链与可得时点.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "hot_first": facts["trade_bar_layers"]["hot_first_bar"],
        "layers": {k: {kk: vv for kk, vv in v.items()
                       if kk in ("fills", "entry_only_from_cold", "cold_share_pct")}
                   for k, v in facts["trade_bar_layers"]["by_window"].items()},
        "cold_exists": {k: v.get("cold_bar_exists_check", {}).get("missing")
                        for k, v in facts["trade_bar_layers"]["by_window"].items()},
        "funding": {k: facts["non_bar_inputs"]["funding"][k]
                    for k in ("hot_rows", "fetched_at_null_pct", "symbols")},
        "spec": {k: facts["non_bar_inputs"]["contract_spec"][k]
                 for k in ("rows", "official_material_dates",
                           "history_change_proved_values")},
        "reader_crosscheck": {k: facts["reader_crosscheck"].get(k)
                              for k in ("matched", "mismatched")},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
