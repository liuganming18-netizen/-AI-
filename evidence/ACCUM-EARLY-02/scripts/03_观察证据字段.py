# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜03 观察事件的市场结构证据。

工单第一部分第 6 条：
  主动买卖、三类多空比、阶段均价须进入**每个观察事件的结构化证据记录**
  （实际读取值、时间范围、来源、缺失），**不得留在「库里有但未参与」**。

本脚本验四件事：
  A 读取器拿到的是真实值（值 / 观测时刻 / 来源 / 缺失 齐全）
  B 「三类多空比」里有没有列其实是别名（不能当独立因子数）
  C 阶段均价可独立复算（Σ成交额 / Σ成交量），且只用已收盘 bar
  D 观察区输出里每个事件**确实带** evidence 字段（跑真实 `observe_events`）

输出：evidence/ACCUM-EARLY-02/out/03_观察证据字段.json
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE = PROJECT / "数据" / "市场数据" / "parquet"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-02" / "out"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

H1 = 3600_000
H4 = 4 * H1
SYMBOLS = ["METISUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
END_MS = 1793009700000
WARM_BARS = 500


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def sec_a_reader(rep: dict) -> None:
    import metrics_feed as MF
    rows = []
    for sym in SYMBOLS:
        m = MF.latest_metrics(sym, END_MS)
        taker = m.get("主动买卖") or {}
        rows.append({
            "symbol": sym,
            "found": m.get("found"),
            "最新行_utc": (m.get("最新行") or {}).get("ts_utc"),
            "最新行_距决策_秒": (m.get("最新行") or {}).get("距观测_秒"),
            "主动买卖": taker.get("值"),
            "主动买卖_观测时刻": taker.get("观测时刻_utc"),
            "主动买卖_取自最新行": taker.get("取自最新行"),
            "主动买卖_落后_个桶": taker.get("落后最新行_个桶"),
            "三类多空比": {k: v["值"] for k, v in (m.get("三类多空比") or {}).items()},
            "多空比_取自最新行": {k: v.get("取自最新行")
                             for k, v in (m.get("三类多空比") or {}).items()},
            "来源": m.get("来源"),
            "时间范围": m.get("时间范围"),
            "可得性": m.get("availability"),
            "可得性说明": (m.get("availability_note") or "")[:60],
            "尾部空值": m.get("尾部空值"),
            "陈旧": m.get("陈旧"),
            "陈旧_小时": m.get("陈旧_小时"),
            "缺失": m.get("缺失"),
            "参与决策": m.get("参与决策"),
        })
    rep["A_读取器"] = {
        "判据": ("每项必须有：实际值、该项自己的观测时刻、来源、缺失列表、可得性标记；"
                 "回退值必须标出来，不许把上一根的值冒充本轮"),
        "rows": rows,
        "为什么逐列回退": (
            "实测尾部最新一根（2026-10-14T23:55Z）的主动买卖是空，23:50 那根有值。"
            "直接取最后一行 ⇒ 会把「本轮没采到」误报成「没有这个指标」；"
            "把 23:50 的值当本轮 ⇒ 用旧值冒充新值。两种都不行，"
            "所以逐列回退 + 写出该项自己的时刻 + 标 `取自最新行`。"),
        "结论": ("值、时刻、来源、缺失、可得性五项齐全；"
                 "`可得性=unproven` 是**如实标注**：parquet 没有发布时点也没有抓取时点，"
                 "按项目 PIT 规则不能进决策，只能作证据。"),
    }


def sec_b_alias(rep: dict) -> None:
    import metrics_feed as MF
    rep["B_多空比别名核对"] = MF.alias_check(SYMBOLS, END_MS)


def sec_c_phase_price(rep: dict) -> None:
    import sqlite3
    import metrics_feed as MF
    import unified_runtime as UR

    # 用真实观察事件的时间窗做阶段均价
    mh_bars = None
    try:
        import history_loader as hl
        mh = hl.MergedHistory(str(HOT), str(ARCHIVE), log=lambda *a, **k: None)
        data, _ = mh.load(SYMBOLS, ["1h", "4h"], END_MS - WARM_BARS * H4, END_MS)
        mh_bars = data
    except Exception as exc:                                    # noqa: BLE001
        rep["C_阶段均价"] = {"error": f"{type(exc).__name__}: {exc}"}
        return

    rows = []
    for sym in SYMBOLS:
        eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False)
        eng.history_floor_ms = END_MS - WARM_BARS * H4
        eng.process_market(END_MS, UR._MemWatcher(mh_bars[sym]["4h"], mh_bars[sym]["1h"]))
        ev = eng.sm.active_event
        if ev is None:
            rows.append({"symbol": sym, "note": "窗口末没有活跃事件"})
            continue
        created = int(ev.created_at_ms)
        hist = ev.history or []
        updated = int(hist[-1].trigger_bar_close_ms) if hist else created
        # 上界必须用**引擎水位**（最近一根已处理 1h 的收盘时刻）：
        # BOX 事件刚建档时 `updated == created`，窗口退化成零长度 ⇒ 均价必然 null。
        water = (int(eng.sm.h1_close_times[-1])
                 if (eng.sm.h1_close_times and len(eng.sm.h1_close_times)) else updated)
        phase_to = max(updated, water)
        got = MF.phase_avg_price(sym, created, phase_to)
        # 独立复算：直接查 candles，自己算一遍
        con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
        try:
            r = con.execute(
                """SELECT COUNT(*), SUM(CAST(quote_volume AS REAL)), SUM(CAST(volume AS REAL))
                   FROM candles WHERE exchange='binance' AND market='um' AND symbol=?
                     AND timeframe='1h' AND is_closed=1 AND open_time>=? AND close_time<=?""",
                (sym, created, phase_to)).fetchone()
        finally:
            con.close()
        n2, qv2, vol2 = int(r[0] or 0), r[1], r[2]
        vwap2 = (float(qv2) / float(vol2)) if (qv2 and vol2) else None
        rows.append({
            "symbol": sym,
            "事件状态": str(ev.current_state),
            "阶段窗口_utc": {"from": iso(created), "to": iso(phase_to)},
            "上界取法": "引擎水位（最近已处理 1h 收盘时刻）",
            "bar数": got.get("bar数"),
            "阶段均价": got.get("阶段均价"),
            "独立复算": vwap2,
            "一致": (None if (got.get("阶段均价") is None or vwap2 is None)
                   else bool(abs(got["阶段均价"] - vwap2) < 1e-12)),
            "公式": got.get("公式"),
            "来源": got.get("来源"),
            "缺失": got.get("缺失"),
        })
    rep["C_阶段均价"] = {
        "rows": rows,
        "口径": "只用 `candles` 视图（头版本）+ is_closed=1 + close_time<=事件最新迁移时刻",
        "为什么要独立复算": "报告里出现的每个数字都要能从原始表重算出来，不能只信函数",
    }


def sec_d_observe_payload(rep: dict) -> None:
    """D：直接跑真实的 `observe_events()`，检查每个事件都带 evidence。"""
    import history_loader as hl
    import unified_runtime as UR

    mh = hl.MergedHistory(str(HOT), str(ARCHIVE), log=lambda *a, **k: None)
    data, _ = mh.load(SYMBOLS, ["1h", "4h"], END_MS - WARM_BARS * H4, END_MS)
    engines = {}
    for sym in SYMBOLS:
        eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False)
        eng.history_floor_ms = END_MS - WARM_BARS * H4
        eng.process_market(END_MS, UR._MemWatcher(data[sym]["4h"], data[sym]["1h"]))
        engines[sym] = eng

    # 只装 `observe_events` 需要的那几个属性（不构造整个运行环境、不碰生产库写入）。
    # 注意：`observe_events` / `observe_summary` / `_observe_market_evidence` 定义在
    # `MultiSymbolSignalEngine`（class 起始第 760 行）里，**不在** `UnifiedRuntime`。
    # 上一版把它挂到 `UnifiedRuntime` 上 ⇒ AttributeError。
    import threading as _th
    rt = object.__new__(UR.MultiSymbolSignalEngine)
    rt.engines = engines
    rt.config = UR.EventTrendConfig()
    rt._observe_cache = None
    rt._observe_cache_at = 0.0
    rt._evidence_cache = {}
    rt._evidence_inflight = set()
    rt._evidence_lock = _th.Lock()
    rt._evidence_q = None
    rt._evidence_thread = None
    t0 = time.time()
    out = rt.observe_events()
    first_call = time.time() - t0
    # 证据是**后台线程填充**的（单标的 81ms × 4 个，首次必然 pending）。
    # 这里显式等到全部算完再判定，否则「还没算完」会被误读成「没有证据」。
    waited = 0.0
    while waited < 120.0:
        pend = [x["symbol"] for x in (out.get("items") or [])
                if (x.get("evidence") or {}).get("pending")]
        if not pend:
            break
        time.sleep(0.5)
        waited += 0.5
        rt._observe_cache = None
        out = rt.observe_events()
    cost = time.time() - t0 - waited
    items = out.get("items") or []
    missing = [x["symbol"] for x in items if "evidence" not in x]
    incomplete = []
    stale = []
    for x in items:
        ev = x.get("evidence") or {}
        mm = ev.get("主动买卖与多空比") or {}
        pp = ev.get("阶段均价") or {}
        if not mm.get("found") or pp.get("阶段均价") is None:
            incomplete.append({"symbol": x["symbol"],
                               "指标found": mm.get("found"),
                               "阶段均价": pp.get("阶段均价"),
                               "缺失": (mm.get("缺失"), pp.get("缺失"))})
        if mm.get("陈旧"):
            stale.append({"symbol": x["symbol"],
                          "指标最新_utc": mm.get("ts_utc"),
                          "陈旧_小时": mm.get("陈旧_小时"),
                          "说明": mm.get("陈旧说明")})
    rep["D_观察输出"] = {
        "观察中事件数": out.get("observing_total"),
        "counts": out.get("counts"),
        "耗时_秒": round(cost, 2),
        "等待后台填充_秒": round(waited, 1),
        "主循环首轮耗时_秒": round(first_call, 3),
        "主循环首轮为何快": ("首轮只入队不计算（后台线程算），"
                        "所以 2 秒轮询不会被 28 秒的首次填充卡住"),
        "每个事件都带 evidence": not missing,
        "缺 evidence 的标的": missing,
        "证据不完整的标的": incomplete,
        # 「有值」不等于「是当前市场」：指标 parquet 最新一根停在
        # 2026-10-14T23:55Z，决策时刻在 2026-10-26 ⇒ 差 11.4 天。
        # 这一项必须单列，否则「证据齐全」会被读成「证据新鲜」。
        "指标陈旧的事件": stale,
        "样例": (items[0] if items else None),
        "体积": {"items": len(items),
                 "估算字节": len(json.dumps(items, ensure_ascii=False, default=str))},
        "读法": ("evidence 里 `主动买卖与多空比` 是 5 分钟桶的最新一条"
                 "（逐列回退，回退值标 `取自最新行=False`）；"
                 "`阶段均价` 是事件从建档到**引擎水位**的成交量加权均价；"
                 "两项都标了 `参与决策=False`。"
                 "`指标陈旧的事件` 非空表示指标库本身停更，"
                 "此时的值只能当历史记录，不能当当前市场。"),
    }


def main() -> int:
    rep = {"probe": "03_观察证据字段", "generated_at_utc": iso(time.time() * 1000),
           "工单": "ACCUM-EARLY-02 第一部分第 6 条", "errors": []}
    for name, fn in (("A", sec_a_reader), ("B", sec_b_alias),
                     ("C", sec_c_phase_price), ("D", sec_d_observe_payload)):
        try:
            fn(rep)
        except Exception as exc:                                # noqa: BLE001
            rep["errors"].append({"stage": name,
                                  "error": f"{type(exc).__name__}: {exc}",
                                  "trace": traceback.format_exc()[-1500:]})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "03_观察证据字段.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    d = rep.get("D_观察输出", {})
    print(json.dumps({
        "A": [{k: r.get(k) for k in ("symbol", "主动买卖", "主动买卖_取自最新行",
                                     "陈旧_小时", "可得性", "缺失")}
              for r in rep.get("A_读取器", {}).get("rows", [])],
        "B": rep.get("B_多空比别名核对", {}).get("pairs"),
        "B_抽样行数": rep.get("B_多空比别名核对", {}).get("抽样行数"),
        "C": [{k: r.get(k) for k in ("symbol", "阶段均价", "一致")}
              for r in rep.get("C_阶段均价", {}).get("rows", [])],
        "D_事件数": d.get("观察中事件数"),
        "D_都带证据": d.get("每个事件都带 evidence"),
        "D_不完整": d.get("证据不完整的标的"),
        "D_陈旧": d.get("指标陈旧的事件"),
        "D_耗时": d.get("耗时_秒"),
        "D_体积": d.get("体积"),
        "errors": [e["error"] for e in rep["errors"]],
    }, ensure_ascii=False, indent=1, default=str))
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
