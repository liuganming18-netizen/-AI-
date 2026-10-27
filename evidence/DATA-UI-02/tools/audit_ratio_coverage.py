#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-UI-02：三个额外比率块的**采集覆盖审计**（可反复跑，用于前后对照）。

对每个应采标的、每个比率块，给出：
  应采 / 有值 / 最新且有效 / 最后成功时间 / 缺失原因 / 缺失分类

缺失分类（互斥，优先级从上到下）：
  上游不支持   该交易所根本没有这个接口（欧易无 `/futures/data`）
  未轮到       轮转窗口还没轮到它（`精简口径未取`）
  限频         被网关推迟（`DeferredRequest` / 预算 / 间隔保护）
  错误         其它失败（解析失败、空返回等）
  过期         有值但距取数时刻超过阈值
  无记录       快照里根本没有这个标的

用法：
  python audit_ratio_coverage.py                      # 用当前 head 口径
  python audit_ratio_coverage.py --n 62 --tag before  # 指定规模与标签
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(EVID, "..", ".."))
BACKEND = os.path.join(ROOT, "框架", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "框架"))

SNAP = os.path.join(BACKEND, "快照", "public_extras")
DB = os.environ.get("MARKET_DB") or os.path.join(
    ROOT, "数据", "市场数据", "db", "market_v2.sqlite3")
STALE_S = 900          # 核心块：与接口侧一致，超过 15 分钟算过期
ROTATION_STALE_S = 2880  # 轮转块：单轮实测 456s × 6 轮 ≈ 38 分钟，阈值 = 周期 × 1.2

# 与 metrics_api.py 的 ROTATION_RATIO_KEYS 保持一致：这三个块按轮转补采
ROTATION_KEYS = ("taker_buy_sell_ratio",
                 "top_account_long_short_ratio",
                 "global_account_long_short_ratio")

BLOCKS = [
    ("taker_buy_sell_ratio", "主动买卖量比"),
    ("top_account_long_short_ratio", "大户账户多空比"),
    ("global_account_long_short_ratio", "全市场账户多空比"),
    ("top_position_long_short_ratio", "大户持仓多空比（核心项，对照用）"),
]


def stale_of(key, uniform=None):
    """该块的过期阈值。uniform 不为空则所有块用同一阈值（用于与旧口径对齐）。"""
    if uniform:
        return uniform
    return ROTATION_STALE_S if key in ROTATION_KEYS else STALE_S


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_ms(s):
    if not s:
        return None
    try:
        return int(datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def classify(block, age_s, stale_s=None):
    """返回 (状态, 原因)。状态 ∈ 有值/过期/未轮到/限频/错误/上游不支持/无记录。"""
    if not block:
        return "无记录", "快照里没有该块"
    if block.get("available") and block.get("value"):
        if age_s is not None and age_s > stale_s:
            return "过期", f"距取数 {age_s:.0f}s，超过 {stale_s}s"
        return "有值", None
    reason = (block.get("reason") or "").strip()
    if "欧易" in reason or "无 /futures/data" in reason:
        return "上游不支持", reason
    if "精简口径未取" in reason or "未取" in reason:
        return "未轮到", reason
    if "DeferredRequest" in reason or "预算" in reason or "间隔保护" in reason:
        return "限频", reason
    return "错误", reason or "未知原因"


def load_index():
    p = os.path.join(SNAP, "_index.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def taker_ratio_derivable(symbols):
    """界面口径的「主动买卖量比」= 实时 K 线派生（买入量 ÷ (总量-买入量)）。

    它与快照里的同名块**不是同一个数据源**：界面不读那个块。
    这里直接查本机库，给「界面实际能算出来多少个」。
    """
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    except Exception:                                             # noqa: BLE001
        return None, None
    try:
        ok = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM realtime_klines "
            "WHERE exchange='binance' AND timeframe='1h' "
            "AND taker_buy_volume IS NOT NULL AND volume IS NOT NULL "
            "AND CAST(volume AS REAL) > 0")}
        whole = con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM realtime_klines "
            "WHERE exchange='binance' AND timeframe='1h' "
            "AND taker_buy_volume IS NOT NULL AND volume IS NOT NULL "
            "AND CAST(volume AS REAL) > 0").fetchone()[0]
        tot = con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM realtime_klines "
            "WHERE exchange='binance' AND timeframe='1h'").fetchone()[0]
    except Exception:                                             # noqa: BLE001
        return None, None
    finally:
        con.close()
    hit = sum(1 for s in symbols if s in ok)
    return (hit, len(symbols)), (whole, tot)


def load_snapshot(ex, mid):
    p = os.path.join(SNAP, f"{ex}__{mid}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                             # noqa: BLE001
        return None


def expected_symbols(n):
    """用采集器**同一函数**算应采标的（不复制一份逻辑，避免口径漂移）。"""
    try:
        import realtime_collector as rc
        universe = rc.load_universe()
        return rc.public_extras_head_symbols(universe, n=n), rc
    except Exception as exc:                                      # noqa: BLE001
        print(f"无法加载采集器口径：{type(exc).__name__}: {exc}", file=sys.stderr)
        return [], None


def main():
    global ROTATION_STALE_S
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None, help="应采标的数（默认按采集器口径）")
    ap.add_argument("--tag", default="", help="标签，写进输出文件名")
    ap.add_argument("--from-snapshots", action="store_true",
                    help="应采 = 快照目录里**已有**的标的（用于取「改前」基线）")
    ap.add_argument("--uniform-stale-s", type=int, default=None,
                    help="所有块用同一过期阈值（用于与旧口径逐项对齐）")
    ap.add_argument("--rotation-stale-s", type=int, default=ROTATION_STALE_S,
                    help=f"轮转块的过期阈值，默认 {ROTATION_STALE_S}s"
                         "（= 轮转周期 2400s × 1.2；与 metrics_api.ROTATION_STALE_S 同源）")
    a = ap.parse_args()

    ROTATION_STALE_S = a.rotation_stale_s

    if a.from_snapshots:
        syms = sorted(
            os.path.basename(p)[len("binance__"):-len(".json")]
            for p in os.listdir(SNAP)
            if p.startswith("binance__") and p.endswith(".json"))
        rc = None
    else:
        syms, rc = expected_symbols(a.n)
    if not syms:
        return 2
    idx = load_index()
    now = now_ms()

    rows = []
    for s in syms:
        snap = load_snapshot("binance", s)
        ls = ((snap or {}).get("long_short") or {})
        rec = {"symbol": s, "snapshot": bool(snap),
               "refreshed_at": (snap or {}).get("snapshot_refreshed_at")}
        for key, _name in BLOCKS:
            blk = ls.get(key) or {}
            fetched = iso_ms(blk.get("fetched_at"))
            age = (now - fetched) / 1000.0 if fetched else None
            th = stale_of(key, a.uniform_stale_s)
            status, reason = classify(blk, age, th)
            rec[key] = {"status": status, "value": blk.get("value"),
                        "fetched_at": blk.get("fetched_at"),
                        "age_s": round(age, 1) if age is not None else None,
                        "reason": reason, "stale_s": th,
                        "carried_over": bool(blk.get("carried_over"))}
        rows.append(rec)

    # 汇总
    summary = {}
    for key, name in BLOCKS:
        cnt = {}
        for r in rows:
            st = r[key]["status"]
            cnt[st] = cnt.get(st, 0) + 1
        summary[key] = {"name": name, "expected": len(rows), "counts": cnt,
                        "ok": cnt.get("有值", 0),
                        "stale_s": stale_of(key, a.uniform_stale_s)}

    # 界面实际取数口径（避免把「快照同名块」误读成「界面缺数据」）
    # 必须在 json.dump 之前算好并放进 out，否则 JSON 里没有这个键，
    # 下游 make_compare 拿不到，对照表就会漏掉这条关键澄清。
    scoped, whole = taker_ratio_derivable([r["symbol"] for r in rows])

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_n": len(rows),
        "stale_policy": ("统一 %ss" % a.uniform_stale_s if a.uniform_stale_s
                         else "核心块 %ss / 轮转块 %ss" % (STALE_S, ROTATION_STALE_S)),
        "snapshot_index": {k: idx.get(k) for k in
                           ("updated_at", "ratio_extra_round", "ratio_extra_done",
                            "ratio_extra_n", "ratio_extra_window", "ratio_extra_pending_in",
                            "ratio_extra_incomplete", "requests", "elapsed_s")},
        "ui_scope_taker_ratio": ({"scoped": scoped, "whole_pool": whole}
                                 if scoped else None),
        "summary": summary,
        "rows": rows,
    }
    tag = f"_{a.tag}" if a.tag else ""
    jp = os.path.join(EVID, f"覆盖审计{tag}.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    # Markdown
    L = []
    L.append(f"## 采集覆盖审计{'（' + a.tag + '）' if a.tag else ''}\n")
    L.append(f"生成时间（UTC）：`{out['generated_at']}`　应采标的：**{len(rows)}** 个　"
             f"过期阈值：{out['stale_policy']}\n")
    L.append("| 比率块 | 应采 | 有值 | 最新且有效 | 未轮到 | 限频 | 错误 | 过期 | 上游不支持 | 阈值 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for key, name in BLOCKS:
        s = summary[key]
        c = s["counts"]
        L.append(f"| {name} | {s['expected']} | {c.get('有值', 0)} | {c.get('有值', 0)} | "
                 f"{c.get('未轮到', 0)} | {c.get('限频', 0)} | {c.get('错误', 0)} | "
                 f"{c.get('过期', 0)} | {c.get('上游不支持', 0)} | {s['stale_s']}s |")
    L.append("")
    si = out["snapshot_index"]
    L.append(f"最近一轮：`{si.get('updated_at')}`，第 {si.get('ratio_extra_round')} 轮，"
             f"取齐 {si.get('ratio_extra_done')}/{si.get('ratio_extra_n')}，"
             f"待重试 {si.get('ratio_extra_pending_in')}，本轮未齐 {si.get('ratio_extra_incomplete')}，"
             f"请求 {si.get('requests')}，耗时 {si.get('elapsed_s')}s\n")

    # 界面实际取数口径（避免把「快照同名块」误读成「界面缺数据」）
    ui = out.get("ui_scope_taker_ratio")
    if ui:
        scoped, whole = ui["scoped"], ui["whole_pool"]
        L.append("### 界面实际取数口径（关键澄清）\n")
        L.append("界面上的**主动买卖量比**不是读快照里的同名块，而是用实时 K 线派生"
                 "（主动买入量 ÷ (成交量 − 主动买入量)）。所以上表第一行是「快照同名块」的覆盖，"
                 "**不等于界面缺数据**：\n")
        L.append(f"- 本审计 {scoped[1]} 个标的中，界面可算出该比率：**{scoped[0]}/{scoped[1]}**")
        L.append(f"- 币安全池（1h 实时 K 线）可算出该比率：**{whole[0]}/{whole[1]}**")
        L.append("- 只有上游不给主动买入量的交易所（如欧易原生 K 线）才显示为不支持\n")

    # 逐标的明细（只列**非有值**的，避免几百行噪音）
    L.append("### 缺失明细（只列非「有值」）\n")
    L.append("| 标的 | 块 | 状态 | 最后成功 | 原因 |")
    L.append("|---|---|---|---|---|")
    bad = 0
    for r in rows:
        for key, name in BLOCKS:
            cell = r[key]
            if cell["status"] == "有值":
                continue
            bad += 1
            L.append(f"| `{r['symbol']}` | {name} | {cell['status']} | "
                     f"{cell['fetched_at'] or '—'} | {(cell['reason'] or '—')[:70]} |")
    if not bad:
        L.append("| — | — | 无缺失 | — | — |")
    L.append("")

    mp = os.path.join(EVID, f"覆盖审计{tag}.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print(f"\n写出：{jp}\n写出：{mp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
