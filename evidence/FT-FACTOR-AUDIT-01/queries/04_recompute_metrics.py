#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FT-FACTOR-AUDIT-01 / 04 锁定反重力批次 + 直接读逐笔结果独立重算指标

只读：evidence/FT-FACTOR-03/{out,user_data,logs}、evidence/FT-FACTOR-02/out（36 币组权威逐笔源）
输出：out/08_批次锁定_v1.json、out/09_逐笔重算指标_v1.csv、out/10_逐笔重算与自报对照_v1.csv、
      out/11_分币与分时段_v1.csv、out/12_归档包与CSV逐笔一致性_v1.csv

原则：不另跑同一套回测；只用对方已产出的逐笔成交记录重算。
组别口径：
  复用旧批次(36币)  → FT-FACTOR-02/out/trades_research_<Strategy>.csv（该组本轮未重跑，为"复用"）
  新增批次(38币)    → FT-FACTOR-03/out/trades_NewBatches38_<Strategy>.csv
  全池合并(74币)    → FT-FACTOR-03/out/trades_ExpandedAll74_<Strategy>.csv
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

csv.field_size_limit(10 ** 9)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BASE))
AG3 = os.path.join(ROOT, "evidence", "FT-FACTOR-03")
AG2 = os.path.join(ROOT, "evidence", "FT-FACTOR-02")
OUT = os.path.join(BASE, "out")

STRATS = [
    ("Baseline (完整过滤)", "WyckoffEventTrendBaseline"),
    ("No OI", "WyckoffAblationNoOI"),
    ("No BreakoutVol", "WyckoffAblationNoBreakoutVol"),
    ("No RetestVol", "WyckoffAblationNoRetestVol"),
    ("No GapFilter", "WyckoffAblationNoGapFilter"),
]

SOURCES = {
    "复用旧批次(36币)": (os.path.join(AG2, "out"), "trades_research_{s}.csv"),
    "新增批次(38币)": (os.path.join(AG3, "out"), "trades_NewBatches38_{s}.csv"),
    "全池合并(74币)": (os.path.join(AG3, "out"), "trades_ExpandedAll74_{s}.csv"),
}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d * 100, (c + r) / d * 100)


def load_trades(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["pnl"] = float(r["profit_abs"])
        r["pr"] = float(r["profit_ratio"])
        r["o"] = int(r["open_timestamp"])
        r["c"] = int(r["close_timestamp"])
    return rows


def dd_variants(trades):
    """返回 (真实平仓权益回撤, 剔除首笔后的回撤)。两者差 1 笔即对方口径差异来源。"""
    def calc(seq):
        eq = peak = mdd = 0.0
        for t in sorted(seq, key=lambda x: x["c"]):
            eq += t["pnl"]
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
        return mdd

    return calc(trades), calc(trades[1:]) if len(trades) > 1 else 0.0


def metrics(trades, label):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 1e-12]
    losses = [t for t in trades if t["pnl"] < -1e-12]
    flats = [t for t in trades if abs(t["pnl"]) <= 1e-12]
    net = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in losses)
    mdd, mdd_x = dd_variants(trades)
    lo, hi = wilson(len(wins), n)
    return {
        "group": label,
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(flats),
        "win_rate_pct": round(len(wins) / n * 100, 4) if n else None,
        "wilson95_low_pct": round(lo, 4) if n else None,
        "wilson95_high_pct": round(hi, 4) if n else None,
        "net_pnl_u": round(net, 6),
        "gross_profit_u": round(gp, 6),
        "gross_loss_u": round(gl, 6),
        "profit_factor": round(gp / abs(gl), 6) if gl else None,
        "avg_win_u": round(gp / len(wins), 6) if wins else None,
        "avg_loss_u": round(gl / len(losses), 6) if losses else None,
        "expectancy_u": round(net / n, 6) if n else None,
        "max_closed_equity_dd_u": round(mdd, 6),
        "dd_excluding_first_trade_u": round(mdd_x, 6),
        "max_win_u": round(max((t["pnl"] for t in trades), default=0.0), 6),
        "max_loss_u": round(min((t["pnl"] for t in trades), default=0.0), 6),
    }


def concurrency(trades):
    ev = []
    for t in trades:
        ev.append((t["o"], 1))
        ev.append((t["c"], -1))
    ev.sort()
    cur = mx = 0
    for _, d in ev:
        cur += d
        mx = max(mx, cur)
    return mx


def key(t):
    return (t["pair"], t["o"])


def _ts_utc(v):
    """归档里的 backtest_start_ts 可能是秒或毫秒，统一转 UTC 字符串。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x > 1e11:
        x = x / 1000.0
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return ""


def main():
    os.makedirs(OUT, exist_ok=True)

    # ---------- 1. 批次锁定 ----------
    lock = {
        "work_order": "FT-FACTOR-AUDIT-01",
        "audited_target": "evidence/FT-FACTOR-03（FT-FACTOR-03 交付批次）",
        "audit_mode": "只读对方逐笔产物，不重跑回测",
        "audited_scope_note": "本脚本只核对 WyckoffEventTrendBaseline / WyckoffAblation* 共 10 个回测。"
                              "out/ 下的 WyckoffPVVariant_*（Full74，统一关闭 OI 的价量研究变体）在 19:45~19:53 才落盘并补写报告，"
                              "本审计 19:32 启动时不存在，故不在本脚本范围内；该批已由 queries/06_pv_variant_audit.py 单独核对，"
                              "见 out/18~22 与报告第九节。",
        "artifacts": [],
    }
    disk_hash = {}
    sdir = os.path.join(AG3, "user_data", "strategies")
    for fn in sorted(os.listdir(sdir)):
        if fn.endswith(".py"):
            disk_hash[fn] = sha256_file(os.path.join(sdir, fn))

    archive_check = []
    for fn in sorted(os.listdir(os.path.join(AG3, "out"))):
        if not fn.endswith(".zip"):
            continue
        if "PVVariant" in fn:
            continue  # 该批由 06_pv_variant_audit.py 单独核对，此处跳过
        p = os.path.join(AG3, "out", fn)
        z = zipfile.ZipFile(p)
        names = z.namelist()
        jname = next((n for n in names if n.endswith(".json") and "_config" not in n), None)
        sname = next((n for n in names if n.endswith(".py")), None)
        d = json.loads(z.read(jname))
        strat_key = list(d["strategy"].keys())[0]
        trades_in_zip = d["strategy"][strat_key].get("trades", [])
        inner_sha = sha256_bytes(z.read(sname)) if sname else ""
        lock["artifacts"].append(
            {
                "zip": fn,
                "zip_sha256": sha256_file(p),
                "run_artifact_id": jname.replace(".json", ""),
                "strategy": strat_key,
                "trades_in_archive": len(trades_in_zip),
                "strategy_py_in_zip_sha256": inner_sha,
                "strategy_py_on_disk_sha256": disk_hash.get(f"{strat_key}.py", ""),
                "strategy_py_match": inner_sha == disk_hash.get(f"{strat_key}.py", ""),
                "backtest_start_utc": _ts_utc(d["strategy"][strat_key].get("backtest_start_ts")),
                "backtest_end_utc": _ts_utc(d["strategy"][strat_key].get("backtest_end_ts")),
                "timeframe": d["strategy"][strat_key].get("timeframe"),
            }
        )
        csv_name = f"trades_{fn.replace('backtest-result-', '').replace('.zip', '').replace('-', '_')}.csv"
        csv_path = os.path.join(AG3, "out", csv_name)
        csv_rows = load_trades(csv_path) if os.path.exists(csv_path) else []
        zip_set = sorted((t["pair"], int(t["open_timestamp"]), round(float(t["profit_abs"]), 8)) for t in trades_in_zip)
        csv_set = sorted((t["pair"], t["o"], round(t["pnl"], 8)) for t in csv_rows)
        archive_check.append(
            {
                "zip": fn,
                "csv": csv_name if os.path.exists(csv_path) else "缺失",
                "zip_trades": len(zip_set),
                "csv_trades": len(csv_set),
                "identical": zip_set == csv_set,
                "only_in_zip": len(set(zip_set) - set(csv_set)),
                "only_in_csv": len(set(csv_set) - set(zip_set)),
            }
        )

    # PVVariant 批次：本脚本不核对，只登记（由 06 脚本单独核对）
    pv = []
    for fn in sorted(os.listdir(os.path.join(AG3, "out"))):
        if "PVVariant" in fn and fn.endswith(".zip"):
            p = os.path.join(AG3, "out", fn)
            z = zipfile.ZipFile(p)
            jname = next((n for n in z.namelist() if n.endswith(".json") and "_config" not in n), None)
            d = json.loads(z.read(jname))
            k = list(d["strategy"].keys())[0]
            pv.append({"zip": fn, "strategy": k, "trades": len(d["strategy"][k].get("trades", [])),
                       "zip_sha256": sha256_file(p)})
    lock["pv_variant_batch_audited_separately"] = {
        "note": "该批 19:45~19:53 落盘并补写报告，晚于本审计启动时刻；本脚本不核对，由 queries/06_pv_variant_audit.py 补做，见 out/18~22。",
        "artifacts": pv,
    }

    with open(os.path.join(OUT, "08_批次锁定_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "12_归档包与CSV逐笔一致性_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(archive_check[0].keys()))
        w.writeheader()
        for r in archive_check:
            w.writerow(r)

    # ---------- 2. 逐笔重算 ----------
    metric_rows = []
    trades_by = {}
    for gname, (base_dir, tpl) in SOURCES.items():
        for slabel, strat in STRATS:
            path = os.path.join(base_dir, tpl.format(s=strat))
            trades = load_trades(path) if os.path.exists(path) else []
            trades_by[(gname, slabel)] = trades
            m = metrics(trades, gname)
            m["strategy_group"] = slabel
            m["max_concurrent_positions"] = concurrency(trades)
            m["source_file"] = os.path.relpath(path, ROOT)
            metric_rows.append(m)

    fields = ["group", "strategy_group", "closed_trades", "wins", "losses", "flats", "win_rate_pct",
              "wilson95_low_pct", "wilson95_high_pct", "net_pnl_u", "gross_profit_u", "gross_loss_u",
              "profit_factor", "avg_win_u", "avg_loss_u", "expectancy_u",
              "max_closed_equity_dd_u", "dd_excluding_first_trade_u", "max_win_u", "max_loss_u",
              "max_concurrent_positions", "source_file"]
    with open(os.path.join(OUT, "09_逐笔重算指标_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in metric_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    # ---------- 3. 与自报对照 ----------
    ag06 = {}
    with open(os.path.join(AG3, "out", "06_扩大样本因子消融对照与噪音过滤总表.csv"), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            ag06[(r["样本池组别"], r["策略组别"])] = r

    def f(x):
        try:
            return float(str(x).replace("%", "").replace("U", "").strip())
        except (TypeError, ValueError):
            return None

    recon = []
    for r in metric_rows:
        a = ag06.get((r["group"], r["strategy_group"]), {})
        dd_ag = f(a.get("平仓权益回撤(U)"))
        recon.append(
            {
                "group": r["group"],
                "strategy_group": r["strategy_group"],
                "trades_mine": r["closed_trades"],
                "trades_ag": a.get("平仓笔数", ""),
                "trades_match": str(r["closed_trades"]) == str(a.get("平仓笔数", "")),
                "wl_mine": f"{r['wins']}/{r['losses']}/{r['flats']}",
                "wl_ag": a.get("胜/负/平", ""),
                "winrate_mine": r["win_rate_pct"],
                "winrate_ag": f(a.get("扣费后净胜率")),
                "wilson_mine": f"[{r['wilson95_low_pct']}, {r['wilson95_high_pct']}]",
                "wilson_ag": a.get("Wilson 95%置信区间", ""),
                "net_mine": r["net_pnl_u"],
                "net_ag": f(a.get("净收益(USDT)")),
                "net_diff": round(r["net_pnl_u"] - f(a.get("净收益(USDT)")), 6) if f(a.get("净收益(USDT)")) is not None else "",
                "pf_mine": r["profit_factor"],
                "pf_ag": f(a.get("利润因子(PF)")),
                "expectancy_mine": r["expectancy_u"],
                "expectancy_ag": f(a.get("每笔净期望(U)")),
                "dd_true_mine": r["max_closed_equity_dd_u"],
                "dd_exclfirst_mine": r["dd_excluding_first_trade_u"],
                "dd_ag": dd_ag,
                "dd_diff_vs_true": round(r["max_closed_equity_dd_u"] - dd_ag, 6) if dd_ag is not None else "",
                "dd_ag_equals_exclfirst": (abs(dd_ag - r["dd_excluding_first_trade_u"]) < 0.02) if dd_ag is not None else "",
            }
        )
    with open(os.path.join(OUT, "10_逐笔重算与自报对照_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(recon[0].keys()))
        w.writeheader()
        for x in recon:
            w.writerow(x)

    # ---------- 4. 分币 / 分时段 ----------
    seg_rows = []
    for gname in SOURCES:
        for slabel, _ in STRATS:
            trades = trades_by[(gname, slabel)]
            buckets = [("symbol", defaultdict(list)), ("year", defaultdict(list)), ("quarter", defaultdict(list))]
            for t in trades:
                dt = datetime.fromtimestamp(t["o"] / 1000, tz=timezone.utc)
                buckets[0][1][t["pair"]].append(t)
                buckets[1][1][dt.strftime("%Y")].append(t)
                buckets[2][1][f"{dt.year}Q{(dt.month - 1) // 3 + 1}"].append(t)
            for dim, d in buckets:
                for b, ts in sorted(d.items()):
                    m = metrics(ts, b)
                    seg_rows.append({"group": gname, "strategy_group": slabel, "dim": dim, "bucket": b,
                                     "trades": m["closed_trades"], "wins": m["wins"], "losses": m["losses"],
                                     "win_rate_pct": m["win_rate_pct"], "net_pnl_u": m["net_pnl_u"],
                                     "expectancy_u": m["expectancy_u"]})
    with open(os.path.join(OUT, "11_分币与分时段_v1.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(seg_rows[0].keys()))
        w.writeheader()
        for r in seg_rows:
            w.writerow(r)

    # ---------- 5. 无效标的检查 ----------
    ineligible = ["AEROUSDT", "AIXBTUSDT", "AVAUSDT", "CGPTUSDT"]
    inel = []
    for (g, s), ts in trades_by.items():
        for t in ts:
            if t["pair"].split("/")[0] in ineligible:
                inel.append({"group": g, "strategy": s, "pair": t["pair"], "open": t["o"], "pnl": t["pnl"]})
    lock["ineligible_symbols_in_pool"] = ineligible
    lock["ineligible_symbols_trades_found"] = inel
    with open(os.path.join(OUT, "08_批次锁定_v1.json"), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2)

    # ---------- 6. 摘要 ----------
    print("=== 归档 vs CSV 逐笔一致性 ===")
    for r in archive_check:
        print(f"  {r['zip'][:50]:52s} zip={r['zip_trades']:3d} csv={r['csv_trades']:3d} identical={r['identical']}")
    print("=== 策略文件哈希与归档一致 ===")
    print("  ", all(a["strategy_py_match"] for a in lock["artifacts"]), [a["strategy"] for a in lock["artifacts"] if not a["strategy_py_match"]])
    print("=== 重算 vs 自报（回撤特别标注）===")
    for x in recon:
        print(f"  {x['group']:14s} {x['strategy_group']:20s} n={x['trades_mine']:3d} "
              f"net={x['net_mine']:>9.2f}/{x['net_ag'] if x['net_ag'] is not None else 'NA':<8} "
              f"dd_true={x['dd_true_mine']:>8.3f} dd_ag={x['dd_ag'] if x['dd_ag'] is not None else 'NA':<8} "
              f"ag==exclFirst={x['dd_ag_equals_exclfirst']}")
    print("=== 无效标的（研究期内无法完成 200 根 4h 预热）产生的交易 ===")
    print("  ", inel if inel else "无（0 笔）")
    print("=== PVVariant 批次（本脚本不核对，由 06 脚本单独核对）===")
    for x in pv:
        print("  ", x["zip"], x["strategy"], "trades=", x["trades"])


if __name__ == "__main__":
    main()
