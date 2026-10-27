# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-05｜02 原生结算周期推断 + 缺口段（热库单独 / 热冷并集）。

三件必须分开的事
----------------
1. **周期**：不能一律按 8 小时。实测 528 个标的里 391 个众数周期是 4h、136 个 8h、1 个 1h。
   用 8h 当分母，4h 币的缺口会被判成「正常间隔」⇒ 完整率虚高。
2. **缺口段**：热库单独算一遍（复现 05-F 的 G4），并集（热∪冷）再算一遍（冷档能补多少）。
3. **边缘 vs 内部**：窗口头尾不足一个周期的那一段不算缺口，单独列 `edge`，
   否则「窗口末端 09:00 不是结算点」会被误报成缺口（本单第一版就踩了这个）。

缺口个数是**推算**：`d > 1.5 × step ⇒ 缺 round(d/step) − 1 个`。
本机没有交易所原生周期元数据（`funding_interval_meta.sqlite3` 与
`fundingIntervalHours` 快照都不存在）⇒ 周期只能实测推断，标 `inferred`；
「周期从 4h 改成 8h」与「真的漏了结算」在数据上长得一样，因此缺口数是**上界**。

只读；不补零；不跨交易所。
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"

HOUR = 3_600_000


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_of(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp() * 1000)


def window():
    d = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    w = d["window"]
    return int(w["start_ms"]), int(w["end_ms"]), w["start_utc"], w["end_utc"]


def build(d) -> None:
    files = sorted(str(p) for p in COLD_DIR.glob("*.parquet"))
    lit = "[" + ",".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    d.execute(f"ATTACH '{HOT}' AS hot (READ_ONLY)")
    d.execute(f"CREATE TABLE cold AS SELECT regexp_extract(filename,'um/([^/]+)\\.parquet',1) "
              f"AS sym, funding_time FROM read_parquet({lit}, filename=true)")
    d.execute("CREATE TABLE hotf AS SELECT symbol AS sym, funding_time FROM hot.funding_history "
              "WHERE exchange='binance' AND market='um'")
    d.execute("CREATE TABLE uni AS SELECT sym, funding_time FROM cold "
              "UNION SELECT sym, funding_time FROM hotf")


def modal_step(d, table: str) -> dict:
    """每标的众数周期（0.25h 粒度）+ 众数占比。只用并集，避免热库缺口污染周期推断。"""
    d.execute(f"""
        CREATE TABLE d_{table} AS
        SELECT sym, funding_time,
               (funding_time - LAG(funding_time) OVER (PARTITION BY sym ORDER BY funding_time))
                 / 3600000.0 AS dh FROM {table}""")
    d.execute(f"""
        CREATE TABLE q_{table} AS
        SELECT sym, ROUND(dh*4)/4 AS h, COUNT(*) AS n
        FROM d_{table} WHERE dh IS NOT NULL AND dh <= 48 GROUP BY 1,2""")
    d.execute(f"""
        CREATE TABLE m_{table} AS
        SELECT sym, h AS step_h, n AS step_n,
               ROW_NUMBER() OVER (PARTITION BY sym ORDER BY n DESC, h ASC) AS rn
        FROM q_{table}""")
    d.execute(f"""CREATE TABLE tot_{table} AS SELECT sym, COUNT(*) AS n_all
                  FROM d_{table} WHERE dh IS NOT NULL AND dh <= 48 GROUP BY 1""")
    rows = d.execute(f"""
        SELECT m.sym, m.step_h, ROUND(100.0*m.step_n/t.n_all, 2)
        FROM m_{table} m JOIN tot_{table} t USING(sym) WHERE m.rn=1""").fetchall()
    return {r[0]: {"step_h": float(r[1]), "share_pct": float(r[2])} for r in rows}


def load_times(d, table: str) -> dict:
    out = defaultdict(list)
    for sym, t in d.execute(
            f"SELECT sym, funding_time FROM {table} ORDER BY sym, funding_time").fetchall():
        out[sym].append(int(t))
    return out


def gaps(times: list, step_h: float, a: int, b: int) -> dict:
    """[a,b] 内的缺口：内部段（两头都有事件）与窗口边缘分开计。"""
    step = int(round(step_h * HOUR))
    inw = [t for t in times if a < t <= b]
    interior, missing = [], 0
    for p, q in zip(inw, inw[1:]):
        dd = q - p
        if dd > 1.5 * step:
            miss = max(1, int(round(dd / step)) - 1)
            missing += miss
            interior.append({"from_ms": p, "to_ms": q, "hours": round(dd / HOUR, 2),
                             "missing": miss})
    head = (inw[0] - a) if inw else None
    tail = (b - inw[-1]) if inw else None
    return {
        "n_events": len(inw), "missing_interior": missing,
        "interior_segments": interior,
        "edge": {"head_hours": round(head / HOUR, 2) if head is not None else None,
                 "tail_hours": round(tail / HOUR, 2) if tail is not None else None,
                 "note": "窗口头尾不足一个周期的不算缺口；本单不补、不外推"},
    }


def cluster_hours(seg_list: list, n_sym: int, min_share=0.5, max_gap_h=24) -> list:
    """把「某小时有缺口的标的数」压成连续区间，用来复现/反驳 05-F 的「主缺口段」。"""
    cnt = defaultdict(set)
    for sym, seg in seg_list:
        h0 = seg["from_ms"] // HOUR
        h1 = max(h0, (seg["to_ms"] - 1) // HOUR)
        for h in range(int(h0), int(h1) + 1):
            cnt[h].add(sym)
    if not cnt:
        return []
    th = max(2, int(math.ceil(min_share * n_sym)))
    hs = sorted(h for h, s in cnt.items() if len(s) >= th)
    if not hs:
        return []
    runs, cur = [], [hs[0]]
    for h in hs[1:]:
        if h - cur[-1] <= max_gap_h:
            cur.append(h)
        else:
            runs.append(cur); cur = [h]
    runs.append(cur)
    out = []
    for r in runs:
        peak = max(len(cnt[h]) for h in r)
        out.append({"from_utc": iso(r[0] * HOUR), "to_utc": iso((r[-1] + 1) * HOUR),
                    "hours": r[-1] + 1 - r[0], "peak_symbols": peak,
                    "threshold_symbols": th,
                    "rule": f"连续小时桶内缺事件标的数 ≥ {th}（标的数 {round(min_share*100)}%）"})
    out.sort(key=lambda x: -x["peak_symbols"])
    return out


def main():
    import duckdb
    t0 = time.time()
    a, b, a_utc, b_utc = window()
    anchor = json.loads((OUT / "00_范围锚定.json").read_text(encoding="utf-8"))
    syms48 = set(anchor["universe"]["symbols"])

    d = duckdb.connect()
    build(d)
    step_uni = modal_step(d, "uni")
    step_hot = modal_step(d, "hotf")
    uni_t = load_times(d, "uni")
    hot_t = load_times(d, "hotf")

    dist = defaultdict(int)
    for s in step_uni.values():
        dist[s["step_h"]] += 1
    dist_rows = [{"step_h": h, "symbols": n, "占比pct": round(100.0 * n / len(step_uni), 2)}
                 for h, n in sorted(dist.items(), key=lambda kv: -kv[1])]

    # 周期一致性：热库单独推断的周期 vs 并集推断的周期（缺口会不会把周期推断带偏）
    step_mismatch = [s for s in step_uni
                     if s in step_hot and abs(step_uni[s]["step_h"] - step_hot[s]["step_h"]) > 1e-9]

    per_sym, agg = {}, defaultdict(lambda: defaultdict(float))
    hot_segments_for_cluster = []
    unreliable = []
    for sym, s in step_uni.items():
        st = s["step_h"]
        reliable = s["share_pct"] >= 95.0
        gu = gaps(uni_t[sym], st, a, b)
        gh = gaps(hot_t.get(sym, []), st, a, b)
        # 周期不单一的标的（众数占比 <95%）缺口**不可推算**：4h 改 8h 与真漏结算同形
        mu = gu["missing_interior"] if reliable else None
        mh = gh["missing_interior"] if reliable else None
        filled = (mh - mu) if reliable else None
        if not reliable:
            unreliable.append({"symbol": sym, "step_h": st, "step_share_pct": s["share_pct"],
                               "why": "众数占比 <95% ⇒ 周期在窗口内变过，缺口数不可推算",
                               "union_segments_observed": len(gu["interior_segments"])})
        row = {
            "symbol": sym, "step_h": st, "step_share_pct": s["share_pct"],
            "grid_reliable": reliable,
            "n_events_window_union": gu["n_events"],
            "n_events_window_hot": gh["n_events"],
            "missing_interior_union": mu,
            "missing_interior_hot": mh,
            "filled_by_cold": filled,
            "fill_rate_pct": (round(100.0 * filled / mh, 2) if mh else None),
            "max_gap_hours_union": max((x["hours"] for x in gu["interior_segments"]), default=0),
            "max_gap_hours_hot": max((x["hours"] for x in gh["interior_segments"]), default=0),
            "edge_union": gu["edge"], "edge_hot": gh["edge"],
            "union_segments": [{"from_utc": iso(x["from_ms"]), "to_utc": iso(x["to_ms"]),
                                "hours": x["hours"], "missing": x["missing"]}
                               for x in gu["interior_segments"]],
            "hot_segments": [{"from_utc": iso(x["from_ms"]), "to_utc": iso(x["to_ms"]),
                              "hours": x["hours"], "missing": x["missing"]}
                             for x in gh["interior_segments"]],
            "in_mother_48": sym in syms48,
        }
        for x in gh["interior_segments"]:
            if reliable:
                hot_segments_for_cluster.append((sym, x))
        per_sym[sym] = row
        for bucket in ("mother_48" if sym in syms48 else "other", "all"):
            agg[bucket]["n_events_union"] += gu["n_events"]
            agg[bucket]["n_events_hot"] += gh["n_events"]
            agg[bucket]["symbols"] += 1
            agg[bucket]["max_gap_hours_union"] = max(
                agg[bucket]["max_gap_hours_union"], row["max_gap_hours_union"])
            agg[bucket]["max_gap_hours_hot"] = max(
                agg[bucket]["max_gap_hours_hot"], row["max_gap_hours_hot"])
            if reliable:
                agg[bucket]["missing_union"] += mu
                agg[bucket]["missing_hot"] += mh
                agg[bucket]["filled_by_cold"] += filled
                agg[bucket]["symbols_with_union_gap"] += (1 if mu else 0)
                agg[bucket]["symbols_with_hot_gap"] += (1 if mh else 0)
            else:
                agg[bucket]["symbols_unreliable_grid"] += 1

    agg_out = {}
    for k, v in agg.items():
        v = dict(v)
        v.setdefault("missing_union", 0); v.setdefault("missing_hot", 0)
        v.setdefault("filled_by_cold", 0); v.setdefault("symbols_unreliable_grid", 0)
        exp_u = v["n_events_union"] + v["missing_union"]
        exp_h = v["n_events_hot"] + v["missing_hot"]
        v["expected_union"] = exp_u
        v["expected_hot"] = exp_h
        v["coverage_union_pct"] = round(100.0 * v["n_events_union"] / exp_u, 3) if exp_u else None
        v["coverage_hot_pct"] = round(100.0 * v["n_events_hot"] / exp_h, 3) if exp_h else None
        # 8h 分母反例：同一批事件，若一律按 8h 推算「应该有多少次结算」
        exp_8h = int((b - a) / (8 * HOUR)) * int(v["symbols"])
        v["expected_if_8h"] = exp_8h
        v["events_vs_8h_ratio"] = (round(v["n_events_union"] / exp_8h, 3) if exp_8h else None)
        v["8h分母问题"] = ("按 8h 推算的期望事件数比实际观测还少（比值 >1）⇒ 8h 假设当场被"
                       "数据否证；完整率若以 8h 为分母，4h 币的缺口全被判成「正常间隔」")
        agg_out[k] = v

    clusters = cluster_hours(hot_segments_for_cluster, len(step_uni))
    # 「主缺口段涉及多少标的」必须与「窗口内有任意缺口的标的数」分开：
    # 前者 = 与该段相交的热库缺口段所属标的（去重），这才与 05-F 的「407 个标的」可比。
    if clusters:
        c0 = ms_of(clusters[0]["from_utc"]); c1 = ms_of(clusters[0]["to_utc"])
        hit = {s for s, seg in hot_segments_for_cluster
               if seg["from_ms"] < c1 and seg["to_ms"] > c0}
        clusters[0]["symbols_overlapping"] = len(hit)
        clusters[0]["max_segment_hours_within"] = round(max(
            (min(seg["to_ms"], c1) - max(seg["from_ms"], c0)) / HOUR
            for s, seg in hot_segments_for_cluster if s in hit), 2)
        clusters[0]["overlap_rule"] = "热库内部缺口段与该段相交的标的去重计数（与「窗口内任意缺口标的数」不同口径）"

    # ── 标的数口径映射：为什么 05-F 写 407，本单算 309 ────────────────────
    # 四个定义都算出来，谁也不是「错」，混用才会错。
    def _count_hot_gap_symbols(threshold_h: float, only_binance: bool,
                               a2: int, b2: int) -> int:
        con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
        where = ("exchange='binance' AND market='um' AND " if only_binance else "")
        rows = con.execute(
            f"SELECT symbol, funding_time FROM funding_history WHERE {where}"
            f"funding_time BETWEEN ? AND ? ORDER BY symbol, funding_time", (a2, b2)).fetchall()
        con.close()
        last, hit = {}, set()
        for sym, t in rows:
            p = last.get(sym)
            if p is not None and (t - p) > threshold_h * HOUR:
                hit.add(sym)
            last[sym] = t
        return len(hit)

    fa = ms_of("2026-08-20T00:00:00Z"); fb = ms_of("2026-10-01T00:00:00Z")   # 05-F 用的窗口
    mapping = [
        {"定义": "05-F 报告值（G4 缺口段「407 个标的」）",
         "值": (json.loads((ROOT / "evidence" / "ACCUM-EARLY-05-F" / "out" /
                            "03_读取链与可得时点.json").read_text(encoding="utf-8"))
                .get("funding_coverage_gap", {}).get("hot_gap_segments", [{}])[0].get("symbols")),
         "口径": "05-F 自己的脚本：热库、窗口 2026-08-20→2026-10-01、相邻间隔 >24h，未按交易所过滤"},
        {"定义": "本单复现：同上口径但**不过滤交易所**",
         "值": _count_hot_gap_symbols(24.0, False, fa, fb),
         "口径": "LAG>24h、05-F 窗口、热库全表（binance/um + okx/swap 混算）"},
        {"定义": "本单复现：同上但**只 binance/um**",
         "值": _count_hot_gap_symbols(24.0, True, fa, fb),
         "口径": "LAG>24h、05-F 窗口、只 binance/um"},
        {"定义": "本单口径（主口径）：1.5×实测周期 + 可靠网格 + 05-C 窗口",
         "值": int(agg_out["all"]["symbols_with_hot_gap"]),
         "口径": "只统计众数占比 ≥95% 的标的；窗口 2026-06-27→2026-10-26"},
        {"定义": "本单口径 + 含周期不单一标的（仅参考）",
         "值": sum(1 for r in per_sym.values() if r["hot_segments"]),
         "口径": "含 109 个众数占比 <95% 的标的 ⇒ 会把「改周期」误报成「漏结算」"},
    ]
    for m in mapping:
        m["差在哪"] = ("未按交易所过滤 / 阈值不同 / 是否剔除周期变化标的 / 窗口不同，"
                    "任一不同都会改数；引用时必须带口径，不能只引数字")

    out = {
        "task": "ACCUM-EARLY-06-05",
        "probe": "02_原生结算周期与缺口段",
        "generated_at_utc": iso(int(time.time() * 1000)),
        "window": {"start_utc": a_utc, "end_utc": b_utc,
                   "span_hours": round((b - a) / HOUR, 2)},
        "方法": {
            "周期": "相邻结算时刻差的众数（0.25h 粒度），只用并集，避免热库缺口污染",
            "缺口": "d > 1.5×step ⇒ 缺 round(d/step)−1 个；内部段与窗口边缘分开",
            "缺口数是上界": ("「周期从 4h 改成 8h」与「真漏结算」在数据上同形，"
                        "无原生元数据不能区分 ⇒ 标 inferred/上界"),
            "原生元数据缺失": ("本机无 `funding_interval_meta.sqlite3`（"
                          "`strategies/funding_interval_meta_source.py` 的伴生库 401MB 不在本仓库），"
                          "也无 `fundingIntervalHours` 快照 ⇒ 不得写「已与交易所周期元数据核对」"),
        },
        "周期分布": dist_rows,
        "周期分布_结论": {
            "非8h标的数": sum(n for h, n in dist.items() if abs(h - 8.0) > 1e-9),
            "总标的": len(step_uni),
            "热库单独推断与并集不一致的标的数": len(step_mismatch),
            "不一致样例": step_mismatch[:10],
        },
        "聚合": agg_out,
        "热库缺口段聚类": clusters,
        "标的数口径映射": mapping,
        "周期不单一_缺口不可推算": {
            "count": len(unreliable),
            "含义": ("这些标的众数占比 <95%（周期在窗口内变过），缺口数**不计算**，"
                   "只登记观察到的间隔段；把它们算进缺口会把「改周期」误报成「漏结算」"),
            "样例": unreliable[:20],
        },
        "per_symbol_48": {s: per_sym[s] for s in syms48},
        "per_symbol_all": per_sym,
        "elapsed_sec": round(time.time() - t0, 2),
    }
    (OUT / "02_原生结算周期与缺口段.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"周期分布": dist_rows, "聚合": agg_out,
                      "热库缺口段聚类": out["热库缺口段聚类"][:3],
                      "周期不一致标的数": len(step_mismatch)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
