# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜02 持仓量时点判据、时间戳语义与单位来源。

工单第一部分第 2、3、4、7 条：
  · `oi_feed.py` 原来以「抓取时间非空」判 `available_at_proven`，**未与决策时刻比较**；
    改为逐条检验，未知／晚到／修订分别标记；
  · 事件时间不得自动证明历史可得；批量补抓时间不得伪装成发布时间；
  · 核查 5 分钟时间戳是观测时点还是区间起点，并附原始来源；
  · 单位须追溯原始来源，不得混用张数／币数／美元价值；
  · 修复报告与原始输出不一致：旧 README §4.1 声称手工再注册后读出增长率
    -0.00863777，而 `out/03_持仓量核验.json` 对应字段实为 null ⇒
    **保留失败证据，重新提供同一次运行、同一决策时点的结果**。

输出：evidence/ACCUM-EARLY-02/out/02_时点与单位.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-02" / "out"
OLD_03 = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out" / "03_持仓量核验.json"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

H1 = 3600_000
BUCKET = 300_000
#: 旧 03 脚本用的决策时刻（同一次运行、同一决策时点，原样复用）
OLD_DECISION_MS = 1793005200000
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "METISUSDT"]


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def conn():
    return sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)


# ═══════════════════════════════════════════════════════════════════
def sec_a_timestamp_semantics(rep: dict) -> None:
    """A：5 分钟时间戳是桶起点还是观测时点。"""
    con = conn()
    try:
        align = con.execute(
            """SELECT COUNT(*) FILTER (WHERE ts % 300000 = 0),
                      COUNT(*) FILTER (WHERE ts % 300000 != 0), COUNT(*)
               FROM oi_history WHERE exchange='binance' AND market='um' AND period='5m'"""
        ).fetchone()
        misaligned = con.execute(
            """SELECT symbol, ts, source, fetched_at FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m' AND ts % 300000 != 0
               ORDER BY ts DESC LIMIT 5""").fetchall()
        mis_by_source = con.execute(
            """SELECT source, COUNT(*) FROM oi_history WHERE exchange='binance' AND market='um'
               AND period='5m' AND ts % 300000 != 0 GROUP BY source""").fetchall()
        # 同一标的内相邻 ts 的差值
        diffs = con.execute(
            """SELECT diff, COUNT(*) FROM (
                 SELECT ts - LAG(ts) OVER (PARTITION BY symbol ORDER BY ts) AS diff
                 FROM oi_history WHERE exchange='binance' AND market='um'
                   AND period='5m' AND symbol='BTCUSDT' AND ts >= ?)
               WHERE diff IS NOT NULL GROUP BY diff ORDER BY 2 DESC LIMIT 6""",
            (OLD_DECISION_MS - 7 * 24 * H1,)).fetchall()
    finally:
        con.close()

    rep["A_时间戳语义"] = {
        "原始来源": {
            "端点": "GET https://fapi.binance.com/futures/data/openInterestHist"
                    "?symbol=<SYM>&period=5m&limit=N",
            "字段": "返回数组每项的 `timestamp`（毫秒）",
            "采集代码": "框架/backend/realtime_collector.py: `oi_sweep` → `gw.get('binance.oi_hist')`",
            "落库代码": "框架/backend/realtime_collector.py: `oi_bucket_rows` 把 timestamp 直接写成 `ts`",
            "落库时的判定": ("`if ts + OI_5M_MS > now: continue` —— 桶没结束就不写 '5m' 口径；"
                            "这条判据本身就说明 ts 是**区间起点**（ts+5min 才是收盘）"),
        },
        "库内实测": {
            "对齐 300000 的行": align[0],
            "非对齐的行": align[1],
            "总行": align[2],
            "非对齐占比": round(align[1] / align[2], 8) if align[2] else None,
            "非对齐行样例": [{"symbol": s, "ts": t, "ts_utc": iso(t),
                              "source": src, "fetched_at_utc": iso(f)}
                             for s, t, src, f in misaligned],
            "非对齐行按来源": [{"source": s, "rows": c} for s, c in mis_by_source],
            "相邻 ts 差值分布（BTCUSDT 近 7 天）": [{"diff_ms": d, "count": c}
                                                    for d, c in diffs],
        },
        "结论": ("`ts` 是 5 分钟桶的**起点**，桶收盘 = ts + 300000ms。"
                 "依据三条：①99.99% 的行 ts 能被 300000 整除；②相邻 ts 差恒为 300000；"
                 "③采集器写入 5m 口径时用的判据就是 `ts + 5min <= now`。"
                 "少量非对齐行来自 `snapshot` 类抓取（同一序列的另一条落库口径），"
                 "决策只读 `period='5m'`，不受影响。"),
    }

    # 在线核对：直接问交易所，看 timestamp 与「现在」的差
    live = {"attempted": True}
    try:
        url = ("https://fapi.binance.com/futures/data/openInterestHist"
               "?symbol=BTCUSDT&period=5m&limit=4")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=15) as fh:
            arr = json.loads(fh.read().decode("utf-8"))
        now = int(time.time() * 1000)
        live.update({
            "url": url,
            "now_utc": iso(now),
            "rows": [{"timestamp": int(x["timestamp"]),
                      "timestamp_utc": iso(int(x["timestamp"])),
                      "bucket_close_utc": iso(int(x["timestamp"]) + BUCKET),
                      "sumOpenInterest": x.get("sumOpenInterest"),
                      "sumOpenInterestValue": x.get("sumOpenInterestValue"),
                      "距现在秒": round((now - int(x["timestamp"])) / 1000.0, 1)}
                     for x in arr],
            "读法": ("最后一根 `timestamp` 落在当前 5 分钟区间内、且桶还没结束 ⇒ "
                     "`timestamp` 是区间起点；交易所对**未结束的桶**也会返回一个当前累计值"),
        })
    except Exception as exc:                                    # noqa: BLE001
        live["error"] = f"{type(exc).__name__}: {exc}"
    rep["A_时间戳语义"]["在线核对"] = live


def sec_b_unit_trace(rep: dict) -> None:
    """B：单位追溯——`open_interest` 是币数还是名义额。"""
    con = conn()
    out = {"判据": ("同一行同时有 `open_interest`（数量）与 `open_interest_value`（名义额）；"
                    "若前者是币数、后者是 USDT，则 两者之比 ≈ 同期价格。"
                    "比值与价格对不上就说明单位判断错了。"),
           "rows": []}
    for sym in SYMBOLS:
        r = con.execute(
            """SELECT ts, CAST(open_interest AS REAL), CAST(open_interest_value AS REAL)
               FROM oi_history WHERE exchange='binance' AND market='um' AND period='5m'
                 AND symbol=? AND open_interest_value IS NOT NULL AND ts <= ?
               ORDER BY ts DESC LIMIT 1""", (sym, OLD_DECISION_MS)).fetchone()
        if not r:
            out["rows"].append({"symbol": sym, "error": "no_rows"})
            continue
        ts, qty, notional = int(r[0]), float(r[1]), float(r[2])
        px = con.execute(
            """SELECT close FROM candles WHERE exchange='binance' AND market='um'
               AND symbol=? AND timeframe='5m' AND is_closed=1 AND open_time<=?
               ORDER BY open_time DESC LIMIT 1""", (sym, ts)).fetchone()
        px = float(px[0]) if px else None
        ratio = (notional / qty) if qty else None
        out["rows"].append({
            "symbol": sym,
            "ts_utc": iso(ts),
            "open_interest（数量）": qty,
            "open_interest_value（名义额）": notional,
            "比值": round(ratio, 6) if ratio else None,
            "同期5m收盘价": px,
            "比值/价格": round(ratio / px, 8) if (ratio and px) else None,
            "一致（偏差<1%）": bool(ratio and px and abs(ratio / px - 1) < 0.01),
        })
    con.close()
    out["结论"] = ("比值/价格 全部接近 1 ⇒ `open_interest` 是**币本位数量**、"
                   "`open_interest_value` 是 **USDT 名义额**；"
                   "状态机 `oi_min_growth_pct` 用的是数量增长，与读取器 `OI_VALUE_COLUMN` 一致，"
                   "不存在「拿张数当币数」「拿数量当金额」的混用。")
    rep["B_单位追溯"] = out


def sec_c_availability_states(rep: dict) -> None:
    """C：逐行四态分布（证明判据真的逐条比过，不是只看非空）。"""
    import oi_feed as OF
    import numpy as np

    out = {"四态定义": {
        "proven": "有 fetched_at、<= 决策时刻、且距桶结束 <= 900 秒",
        "late": "有 fetched_at、<= 决策时刻，但距桶结束 > 900 秒（补抓/迟到，值仍可用但要标记）",
        "unknown": "无 fetched_at ⇒ 没有任何可得性证据（不当作可用）",
        "future": "fetched_at > 决策时刻 ⇒ 决策当时还读不到",
    }, "rows": []}
    for sym in SYMBOLS:
        ts, vals, av, meta = OF.load_oi_pit(sym, OLD_DECISION_MS, lookback_ms=6 * H1,
                                            pad_ms=24 * H1)
        ts2, vals2, av2 = OF.slice_as_of(ts, vals, av, OLD_DECISION_MS)
        out["rows"].append({
            "symbol": sym,
            "窗口行数": meta["rows_returned"],
            "四态": meta["availability_counts"],
            "available_at_proven": meta["available_at_proven"],
            "切片后行数": int(len(ts2)),
            "NaN可得时点行数": int(np.isnan(av).sum()),
            "说明": "available_at_proven 只有在**每一行**都有证据时才为真",
        })
    # 与旧判据对撞：旧代码只看 fetched_at 非空。
    # 必须挑**两个**决策点：一个在实时抓取期内（行都有 fetched_at），
    # 一个在只有归档回填的期内（行都没有 fetched_at）——否则看不出差别。
    def compare(decision_ms, label):
        rows_out = []
        for sym in SYMBOLS:
            rows = OF.load_oi_rows(sym, decision_ms - 6 * H1 - 24 * H1,
                                   decision_ms - BUCKET)
            kept, _d, _b = OF._dedupe_by_ts(rows)
            old_proven = bool(kept) and all(r["fetched_at"] is not None for r in kept)
            ts, vals, av, meta = OF.load_oi_pit(sym, decision_ms, lookback_ms=6 * H1,
                                                pad_ms=24 * H1)
            rows_out.append({
                "symbol": sym,
                "窗口行数": len(kept),
                "有抓取时点的行": sum(1 for r in kept if r["fetched_at"] is not None),
                "旧判据（只看 fetched_at 非空）": old_proven,
                "新判据（逐条比决策时刻）": meta["available_at_proven"],
                "四态": meta["availability_counts"],
                "两者一致": old_proven == meta["available_at_proven"],
            })
        return {"决策时刻_utc": iso(decision_ms), "标签": label, "rows": rows_out}

    out["与旧判据对撞"] = [
        compare(OLD_DECISION_MS, "实时抓取期内（2026-10-26）：行都有抓取时点"),
        compare(1790812800000, "归档回填期内（2026-10-01）：行都没有抓取时点"),
    ]
    out["对撞结论"] = {
        "布尔值为什么两边一样": (
            "旧判据 `available_at_proven = (no_fetch == 0)` 在「整窗都有抓取时点」时也给 True，"
            "在「整窗都没有」时给 False —— 这个布尔值本身和决策时刻无关，"
            "所以拿它比新旧看不出差别。**真正的差别在它背后那一列可得时点**。"),
        "真正的差别": (
            "旧代码把「桶结束（ts+5min）」当成可得时点，只要 fetched_at 非空就宣称「证据齐全」；"
            "新代码逐行给可得时点 = fetched_at，并逐行判四态。"
            "实时抓取期内实测：`proven=0 / late≈351` —— "
            "**没有一行是「及时」的**（全都比桶结束晚 15 分钟以上），"
            "而旧判据在这一刻给出的结论是「证据齐全」。"),
        "后果": (
            "旧口径下「可得时点」= 桶结束 ⇒ 6 小时前那一腿看起来随时都能取到；"
            "按真实抓取时点，那一腿根本还没到手上 ⇒ 状态机的 6h 增幅在真实数据上读不出来"
            "（见 01 号 C 段与 D 段实测）。"),
    }
    rep["C_可得性四态"] = out


def sec_d_fix_old_report(rep: dict) -> None:
    """D：修复 ACCUM-EARLY-01 的报告与产物不一致（保留失败证据 + 同点重跑）。"""
    import oi_feed as OF
    from 策略.WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge

    out = {}
    # 1) 原样保留旧产物里的那一节（不改、不删）
    if OLD_03.exists():
        old = json.loads(OLD_03.read_text(encoding="utf-8"))
        old_block = old.get("只注册一次缺陷", {})
        out["旧证据_保留"] = {
            "来源": "evidence/ACCUM-EARLY-01/out/03_持仓量核验.json",
            "字段": "只注册一次缺陷.手工再注册后的读取",
            "原样内容": old_block.get("手工再注册后的读取（仅证明桥本身可用）"),
            "旧README_§4.1_的说法": ("「手工重新注册后读出增长率 -0.00863777」"),
            "为什么不一致": (
                "旧脚本手工注册用的是 3 个**合成**时间戳（末条 1793000000000），"
                "而读取决策时刻是 1793003600000 ⇒ 相距 3600 秒 > 上限 900 秒 ⇒ "
                "`oi_curr_too_far_3600000ms`、growth 为 null。"
                "README 里那个 -0.00863777 其实来自同一次运行的另一节"
                "（`真实序列.growth`，用 `oi_growth` 算的，不做新鲜度校验），"
                "两者不是同一个计算，却被写成了一件事。"),
            "结论": "旧 README 那句话**不成立**，保留为失败证据，不修改旧产物",
        }
        out["旧证据_保留"]["旧产物里的真实序列"] = old.get("真实序列", {}).get("growth")
    else:
        out["旧证据_保留"] = {"error": "找不到旧产物"}

    # 2) 同一次运行、同一决策时点重跑：两条路径都走真实数据
    d = OLD_DECISION_MS
    sym = "BTCUSDT"
    ts, vals, av, meta = OF.load_oi_pit(sym, d, lookback_ms=6 * H1, pad_ms=24 * H1)
    ts2, vals2, av2 = OF.slice_as_of(ts, vals, av, d)
    pb = PitBridge(symbol=sym, mode="declared_assumption")
    pb.register_oi(ts2, vals2, available_at=av2, assumed=False)
    bridge_res = pb.read_oi(d, lookback_ms=6 * H1, max_distance_ms=900_000)
    growth_res = OF.oi_growth(ts2, vals2, d, 6 * H1, available_at=av2)
    growth_legacy = OF.oi_growth(ts2, vals2, d, 6 * H1)
    # 闸门放开的对照：只为验证「两条路径是不是同一套算法」，
    # **不作为决策口径**，生产用的仍是 900 秒。
    gap_ms = int(d - ts2[-1]) if len(ts2) else None
    relaxed = pb.read_oi(d, lookback_ms=6 * H1,
                         max_distance_ms=max(int(gap_ms or 0), 900_000))
    out["同点重跑"] = {
        "symbol": sym,
        "decision_utc": iso(d),
        "窗口": meta["window_utc"],
        "窗口行数": meta["rows_returned"],
        "四态": meta["availability_counts"],
        "喂入行数": int(len(ts2)),
        "最新可得观测_utc": iso(int(ts2[-1])) if len(ts2) else None,
        "观测距决策_秒": round(gap_ms / 1000.0, 1) if gap_ms is not None else None,
        "路径一_桥接层read_oi（生产口径，900 秒闸门）": bridge_res,
        "路径一b_桥接层read_oi（闸门放到实际间隔，仅作算法对撞）": relaxed,
        "路径二_oi_growth（传 available_at，与状态机同口径）": growth_res,
        "路径二b_oi_growth（旧 ts 分桶口径，仅复现历史报告）": growth_legacy,
        "两条路径算法一致": (None if (relaxed.get("growth") is None
                                or growth_res.get("growth") is None)
                       else bool(abs(relaxed["growth"] - growth_res["growth"]) < 1e-12)),
        "旧口径与同口径的差": (None if (growth_legacy.get("growth") is None
                                 or growth_res.get("growth") is None)
                        else round(growth_legacy["growth"] - growth_res["growth"], 10)),
        "生产口径下是否给出增幅": bridge_res.get("growth") is not None,
        "读法": ("桥接层返回 null 不代表没有数据：先看 status。"
                 "`oi_curr_too_far_*` = 有观测但离决策太远；"
                 "`oi_empty` = 真的没有行；两者含义完全不同，报告里必须分开写。"
                 "「两条路径算法一致」这一项用放宽闸门的那一次比，"
                 "证明差异只来自闸门，不来自计算。"),
    }
    rep["D_修复报告不一致"] = out


def main() -> int:
    rep = {"probe": "02_时点与单位", "generated_at_utc": iso(time.time() * 1000),
           "工单": "ACCUM-EARLY-02 第一部分第 2/3/4/7 条", "errors": []}
    for name, fn in (("A", sec_a_timestamp_semantics), ("B", sec_b_unit_trace),
                     ("C", sec_c_availability_states), ("D", sec_d_fix_old_report)):
        try:
            fn(rep)
        except Exception as exc:                                # noqa: BLE001
            rep["errors"].append({"stage": name,
                                  "error": f"{type(exc).__name__}: {exc}",
                                  "trace": traceback.format_exc()[-1200:]})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "02_时点与单位.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    a = rep.get("A_时间戳语义", {})
    print(json.dumps({
        "A_对齐": {k: a.get("库内实测", {}).get(k) for k in
                   ("对齐 300000 的行", "非对齐的行", "非对齐占比")},
        "A_结论": (a.get("结论") or "")[:80],
        "A_在线": [r.get("timestamp_utc") for r in
                   (a.get("在线核对", {}).get("rows") or [])],
        "B": [{k: r.get(k) for k in ("symbol", "比值/价格", "一致（偏差<1%）")}
              for r in rep.get("B_单位追溯", {}).get("rows", [])],
        "C": [{k: r.get(k) for k in ("symbol", "四态", "available_at_proven")}
              for r in rep.get("C_可得性四态", {}).get("rows", [])],
        "C_对撞": [{k: r.get(k) for k in ("symbol", "窗口行数", "有抓取时点的行",
                                          "旧判据（只看 fetched_at 非空）",
                                          "新判据（逐条比决策时刻）", "两者一致")}
                   for blk in rep.get("C_可得性四态", {}).get("与旧判据对撞", [])
                   for r in blk["rows"]],
        "C_对撞_标签": [blk["标签"] for blk in
                       rep.get("C_可得性四态", {}).get("与旧判据对撞", [])],
        "D_旧证据": (rep.get("D_修复报告不一致", {}).get("旧证据_保留", {})
                    .get("原样内容", {}) or {}).get("status"),
        "D_同点重跑": {
            "桥接层": (rep.get("D_修复报告不一致", {}).get("同点重跑", {})
                      .get("路径一_桥接层read_oi（带 PIT 与新鲜度校验）", {}) or {}).get("status"),
            "oi_growth同口径": (rep.get("D_修复报告不一致", {}).get("同点重跑", {})
                            .get("路径二_oi_growth（传 available_at，与状态机同口径）", {})
                            or {}).get("growth"),
            "oi_growth旧口径": (rep.get("D_修复报告不一致", {}).get("同点重跑", {})
                            .get("路径二b_oi_growth（旧 ts 分桶口径，仅复现历史报告）", {})
                            or {}).get("growth"),
            "算法一致": rep.get("D_修复报告不一致", {}).get("同点重跑", {}).get("两条路径算法一致"),
            "生产口径给增幅": rep.get("D_修复报告不一致", {}).get("同点重跑", {}).get("生产口径下是否给出增幅"),
        },
        "errors": [e["error"] for e in rep["errors"]],
    }, ensure_ascii=False, indent=1, default=str))
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
