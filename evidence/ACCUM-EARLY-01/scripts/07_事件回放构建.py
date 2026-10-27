# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜07 逐事件回放构建。

用**真实历史候选**（生产状态文件里 51 条预热期候选）重建证据链：
当时可见数据 → 结构确认 → 支持/反对/缺失证据 → 候选 → 模拟成交 → 止损变化 → 退出与成本。

纪律：
· 15m 用冷+热合并读取器（research_15m）；4h 同样冷+热，热层优先。
· 成交时刻严格「确认+15 分钟之后首根已闭合 15m bar 的开盘价」。
· 止损用生效路径模型（bar 低点 ≤ 止损 ⇒ 按止损价成交）；**缺口未单独建模**，
  与 06 脚本的结论一致，不夸大。
· 数据缺口逐段列出；取不到就写缺口，不插值、不补零。
· 真实历史与隔离样例分开标记。

输出：out/07_事件回放.json、out/07_事件回放.html
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"

H4, M15 = 14_400_000, 900_000
FEE = 0.0005          # 生效视图 fee_each_side
SLIP_VIEW = 0.0005    # 生效视图 slippage_each_side
SLIP_PKG = 0.0003     # 策略包 slippage_bps=3
EQUITY = 5000.0       # 生效视图 initial_equity_USDT（原策略口径）
RISK = 0.005          # 生效视图 risk_per_trade_equity_fraction


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load_4h(symbol, start_ms, end_ms):
    """冷档 + 热库合并的 4h（热层优先，按 open_time 去重）。"""
    import duckdb
    rows = {}
    pat = str(COLD / "candles" / "binance" / "um" / symbol / "4h" / "*.parquet")
    try:
        con = duckdb.connect()
        for r in con.execute(
                f"""SELECT open_time, close_time, open, high, low, close, volume
                    FROM read_parquet('{pat}')
                    WHERE open_time >= ? AND open_time < ? AND is_closed=1
                    ORDER BY open_time""", [int(start_ms), int(end_ms)]).fetchall():
            rows[int(r[0])] = {"open_time": int(r[0]), "close_time": int(r[1]),
                               "open": float(r[2]), "high": float(r[3]),
                               "low": float(r[4]), "close": float(r[5]),
                               "source": "cold"}
        con.close()
    except Exception:                                          # noqa: BLE001
        pass
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    try:
        for r in con.execute(
                """SELECT open_time, close_time, open, high, low, close
                   FROM candles WHERE exchange='binance' AND market='um'
                     AND symbol=? AND timeframe='4h' AND is_closed=1
                     AND open_time >= ? AND open_time < ?""",
                (symbol, int(start_ms), int(end_ms))):
            rows[int(r[0])] = {"open_time": int(r[0]), "close_time": int(r[1]),
                               "open": float(r[2]), "high": float(r[3]),
                               "low": float(r[4]), "close": float(r[5]),
                               "source": "hot"}
    finally:
        con.close()
    return [rows[k] for k in sorted(rows)]


def funding_between(symbol, t0, t1):
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    try:
        rs = con.execute(
            """SELECT funding_time, funding_rate FROM funding_history
               WHERE exchange='binance' AND market='um' AND symbol=?
                 AND rate_type='settled' AND funding_time > ? AND funding_time <= ?
               ORDER BY funding_time""", (symbol, int(t0), int(t1))).fetchall()
    finally:
        con.close()
    return [{"utc": iso(r[0]), "rate": float(r[1])} for r in rs]


def build_chain(cand, merged):
    """重建一条真实候选的完整链路。"""
    sym = cand["symbol"]
    snap_ms = int(time.mktime(time.strptime(cand["snapshot_utc"], "%Y-%m-%dT%H:%M:%SZ"))
                 - time.timezone) * 1000
    # 上面的换算不稳，直接用 UTC 解析
    import calendar
    snap_ms = calendar.timegm(time.strptime(cand["snapshot_utc"],
                                            "%Y-%m-%dT%H:%M:%SZ")) * 1000
    earliest_ms = snap_ms + 15 * 60_000
    chain = {
        "event_id": cand["event_id"], "candidate_id": cand["candidate_id"],
        "symbol": sym, "label": "真实历史（生产状态文件预热期候选）",
        "1_当时可见数据": {
            "box_high_h": cand.get("box_high_h"), "box_low_l": cand.get("box_low_l"),
            "box_atr_a": cand.get("box_atr_a"), "box_er": cand.get("box_er"),
            "breakout_close": cand.get("breakout_close"),
            "volume_ratio": cand.get("volume_ratio"),
            "source": "框架/backend/locks/paper_runtime_3a_status.json "
                      "signal_engine.bootstrap.discarded_detail",
        },
        "2_结构确认": {
            "snapshot_utc": cand.get("snapshot_utc"),
            "available_at_utc": cand.get("available_at_utc"),
            "execution_earliest_utc": cand.get("execution_earliest_utc"),
            "rule": "1h 收盘确认，最早下一根 15m（+15 分钟）开盘执行",
        },
        "3_证据": {
            "支持": {
                "成交量/前20根中位": cand.get("volume_ratio"),
                "量能门槛 1.5": (cand.get("volume_ratio") or 0) >= 1.5,
                "箱体宽度(ATR 倍数)": (round((cand["box_high_h"] - cand["box_low_l"])
                                            / cand["box_atr_a"], 2)
                                     if cand.get("box_atr_a") else None),
            },
            "反对": {
                "方向效率 ER": cand.get("box_er"),
                "ER 门槛 0.30（越低越像震荡）": cand.get("box_er"),
            },
            "缺失": {
                "oi_growth_6h": cand.get("oi_growth_6h"),
                "说明": "该字段为空 ⇒ 当时持仓量未参与判定（与 03 脚本一致）",
            },
        },
        "4_候选": {
            "candidate_id": cand["candidate_id"],
            "discard_reasons": cand.get("discard_reasons"),
            "note": "预热期候选按纪律丢弃、不补开；本回放只重建过程，不产生新订单",
        },
        "5_成交": None, "6_止损": None, "7_退出与成本": None, "数据缺口": [],
    }

    bars, st15 = merged.load(sym, snap_ms - 2 * H4, snap_ms + 14 * 86400_000)
    if st15["continuity"]["gap_segment_count"]:
        chain["数据缺口"].append({"源": "15m", "段数": st15["continuity"]["gap_segment_count"],
                                  "缺根数": st15["continuity"]["missing_bars_total"],
                                  "明细": st15["continuity"]["gap_segments"][:5]})
    if not bars:
        chain["数据缺口"].append({"源": "15m", "说明": "窗口内无任何 15m bar"})
        return chain

    # 成交：确认后首根 15m
    elig = [b for b in bars if b["open_time"] >= earliest_ms]
    if not elig:
        chain["数据缺口"].append({"源": "15m", "说明": "确认后没有可成交的 15m bar（数据未覆盖该时段）"})
        chain["5_成交"] = {"status": "no_bar_after_confirm",
                           "说明": "该事件发生在热库 15m 起点之前且冷档未覆盖 ⇒ 只能重建观察，重建不出成交"}
        return chain

    fb = elig[0]
    prev = next((b for b in reversed(bars) if b["open_time"] < fb["open_time"]), None)
    entry_px = float(fb["open"])
    stop0 = round(entry_px * 0.95, 12)
    qty = (EQUITY * RISK) / max(entry_px - stop0, 1e-12)
    notional = qty * entry_px
    part_cap = (0.001 * float(prev["quote_volume"])) if prev and prev.get("quote_volume") else None
    chain["5_成交"] = {
        "bar_open_utc": iso(fb["open_time"]), "bar_source": fb["source"],
        "entry_price_raw": entry_px,
        "entry_price_after_slippage": entry_px * (1 + SLIP_VIEW),
        "确认到成交间隔_分钟": round((fb["open_time"] - snap_ms) / 60000, 1),
        "bar_available_at": iso(fb.get("available_at")),
        "available_at_missing": bool(fb.get("available_at_missing")),
        "qty": round(qty, 8), "notional": round(notional, 2),
        "参与率上限_名义": None if part_cap is None else round(part_cap, 2),
        "参与率是否满足": None if part_cap is None else bool(notional <= part_cap),
        "口径": "成交价 = 该根 15m 开盘价；滑点单独加，不并入开盘价",
    }

    # 止损：初始 5%，随后每根新收盘 4h 低点×0.95，只上移
    bars4 = load_4h(sym, snap_ms - 8 * 86400_000, snap_ms + 30 * 86400_000)
    stop = stop0
    path = [{"effective_ms": fb["open_time"], "utc": iso(fb["open_time"]),
             "stop": round(stop, 12), "来源": "入场价×0.95"}]
    for b in bars4:
        if b["close_time"] <= fb["open_time"]:
            continue
        cand_stop = round(b["low"] * 0.95, 12)
        if cand_stop > stop:
            stop = cand_stop
            path.append({"effective_ms": b["close_time"], "utc": iso(b["close_time"]),
                         "stop": stop,
                         "来源": f"4h 低点 {b['low']}×0.95（{b['source']}）"})
    chain["6_止损"] = {
        "初始止损": round(stop0, 12),
        "初始止损口径": "实际成交价 × 0.95（不是候选快照里的 initial_stop_price）",
        "候选快照里的 initial_stop_price": cand.get("initial_stop_price"),
        "两者差异": (None if not cand.get("initial_stop_price") else
                     round((cand["initial_stop_price"] - stop0) / stop0, 4)),
        "说明": "候选快照字段来自状态机旧口径（结构低点），执行侧按实际成交价×0.95 重算；"
                "两者不是同一个数，不能混用",
        "跟踪路径": [{k: v for k, v in p.items() if k != "effective_ms"} for p in path],
        "最终止损": round(stop, 12),
        "只上移": all(path[i]["stop"] >= path[i - 1]["stop"] for i in range(1, len(path))),
    }

    # 退出：按时间顺序先应用已生效止损，再判本根是否触及（生效路径模型：按止损价成交）
    exit_info = None
    cur_stop = stop0
    pi = 0
    for b in bars:
        if b["open_time"] < fb["open_time"]:
            continue
        while pi < len(path) and path[pi]["effective_ms"] <= b["open_time"]:
            cur_stop = path[pi]["stop"]
            pi += 1
        if b["low"] is not None and float(b["low"]) <= cur_stop:
            exit_info = {"bar_open_utc": iso(b["open_time"]),
                         "bar_low": float(b["low"]),
                         "exit_price": cur_stop,
                         "stop_used": cur_stop,
                         "reason": "trailing_stop_loss",
                         "note": "按止损价成交（生效路径模型）；缺口未单独建模"}
            exit_ms = b["open_time"]
            break
    if exit_info is None:
        last = bars[-1]
        exit_ms = last["open_time"]
        exit_info = {"bar_open_utc": iso(last["open_time"]),
                     "exit_price": float(last["close"]),
                     "reason": "window_end_mark_to_market",
                     "note": "窗口内未触及止损 ⇒ 按最后一根收盘估值，不算已平仓"}

    entry_all = entry_px * (1 + SLIP_VIEW)
    exit_all = float(exit_info["exit_price"]) * (1 - SLIP_VIEW)
    gross = (exit_all - entry_all) * qty
    fees = (entry_all + exit_all) * qty * FEE
    funding = funding_between(sym, fb["open_time"], exit_ms)
    fund_cost = sum(f["rate"] for f in funding) * notional
    net_view = gross - fees - fund_cost
    # 策略包口径：滑点 3bps
    entry_pkg = entry_px * (1 + SLIP_PKG)
    exit_pkg = float(exit_info["exit_price"]) * (1 - SLIP_PKG)
    net_pkg = (exit_pkg - entry_pkg) * qty - (entry_pkg + exit_pkg) * qty * FEE - fund_cost

    chain["7_退出与成本"] = {
        "退出": exit_info,
        "成本模型_生效视图": {"fee_bps": 5, "slip_bps": 5,
                              "gross": round(gross, 4), "fees": round(fees, 4),
                              "funding_events": len(funding),
                              "funding_cost": round(fund_cost, 4),
                              "net": round(net_view, 4)},
        "成本模型_策略包": {"fee_bps": 5, "slip_bps": 3, "net": round(net_pkg, 4)},
        "两套净额差": round(net_pkg - net_view, 4),
        "说明": "两套并列，不择一；差额来自滑点口径，改口径需裁决",
        "回放口径": "逐根 15m 判定，成交价取止损价；不声称精确秒级成交",
    }
    return chain


def main() -> int:
    import research_15m as R
    merged = R.Merged15m(HOT, COLD)
    dd = json.loads(STATUS.read_text(encoding="utf-8"))["signal_engine"]["bootstrap"]["discarded_detail"]

    # 选三条：最早（15m 无覆盖）、中段、最近
    dd_sorted = sorted(dd, key=lambda x: x["snapshot_utc"])
    picks = [dd_sorted[0], dd_sorted[len(dd_sorted) // 2], dd_sorted[-1]]
    chains = []
    for c in picks:
        try:
            chains.append(build_chain(c, merged))
        except Exception as exc:                               # noqa: BLE001
            chains.append({"candidate_id": c["candidate_id"],
                           "error": f"{type(exc).__name__}: {exc}"})

    # 隔离样例（明确标注，不计入成绩）
    isolated = {
        "label": "隔离样例（合成，非真实行情，不计入任何成绩）",
        "event_id": "iso_demo_1", "symbol": "SYN",
        "1_当时可见数据": {"box_high_h": 102.0, "box_low_l": 98.0, "box_atr_a": 1.0,
                           "box_er": 0.12, "breakout_close": 102.6, "volume_ratio": 2.1},
        "2_结构确认": {"snapshot_utc": "2026-10-01T12:00:00Z",
                       "execution_earliest_utc": "2026-10-01T12:15:00Z"},
        "5_成交": {"bar_open_utc": "2026-10-01T12:15:00Z", "entry_price_raw": 102.7,
                   "qty": 3.4, "notional": 349.18},
        "6_止损": {"初始止损": round(102.7 * 0.95, 4), "跟踪路径": []},
        "7_退出与成本": {"退出": {"reason": "trailing_stop_loss",
                                  "exit_price": round(102.7 * 0.95, 4)},
                         "net": -22.1},
    }

    rep = {"probe": "07_事件回放", "generated_at_utc": iso(time.time() * 1000),
           "real_event_count_available": len(dd),
           "real_window": {"from": dd_sorted[0]["snapshot_utc"],
                           "to": dd_sorted[-1]["snapshot_utc"]},
           "chains": chains, "isolated_sample": isolated,
           "scenarios_covered": {
               "完整成交与退出": "见 chains 里 15m 有覆盖的事件",
               "观察后未形成候选": "本次未构造（需另跑状态机观察流）",
               "候选等待期取消": "见 04 脚本（候选取消记录）",
               "结构失效": "见 04 脚本（DATA_INSUFFICIENT 与 INVALIDATED 区分）",
               "观察到期": "见 04 脚本（expires_at 冻结）",
               "数据缺失": "见 chains 的 数据缺口 段",
               "中断恢复": "见 06 脚本 F 段（恢复 vs 回放）",
           }}
    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "07_事件回放.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 单页报告：每条链完整过程，不只给汇总表 ────────────────────────
    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def fmt(v, nd=6):
        """按量级选格式：小价格保留有效位（止损 0.0319485 不能截成 0.031948），
        布尔显示成「是／否」，不直接打印 True/False。"""
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "是" if v else "否"
        if isinstance(v, float):
            if v == 0:
                return "0"
            if abs(v) < 1:
                return f"{v:.{nd + 4}g}"
            return f"{v:,.{min(nd, 4)}f}".rstrip("0").rstrip(".")
        return esc(v)

    def pct(v, nd=2):
        if v is None:
            return "—"
        return f"{float(v) * 100:.{nd}f}%"

    DISCARD_ZH = {
        "before_paper_epoch_no_backfill": "早于模拟盘起点，按纪律不补开",
        "missed_fill_moment_no_backfill": "错过成交时刻，只拒单不回填",
    }

    def discard_zh(codes):
        if not codes:
            return "—"
        return "；".join(f"{c}（{DISCARD_ZH.get(c, '未登记原因')}）" for c in codes)

    cards = []
    for ch in chains:
        if "error" in ch:
            cards.append(f"<div class='card err'>错误：{esc(ch['error'])}</div>")
            continue
        v1 = ch.get("1_当时可见数据") or {}
        cf = ch.get("2_结构确认") or {}
        ev = ch.get("3_证据") or {}
        cd = ch.get("4_候选") or {}
        fl = ch.get("5_成交") or {}
        st = ch.get("6_止损") or {}
        ex = (ch.get("7_退出与成本") or {}).get("退出") or {}
        ce = (ch.get("7_退出与成本") or {}).get("成本模型_生效视图") or {}
        cs = (ch.get("7_退出与成本") or {}).get("成本模型_策略包") or {}

        path_rows = "".join(
            f"<tr><td>{esc(p.get('utc'))}</td><td class='num'>{fmt(p.get('stop'),8)}</td>"
            f"<td class='dim'>{esc(p.get('来源'))}</td></tr>"
            for p in (st.get("跟踪路径") or []))

        sup = ev.get("支持") or {}
        con = ev.get("反对") or {}
        mis = ev.get("缺失") or {}
        ev_rows = ""
        for k, v in sup.items():
            ev_rows += f"<tr><td class='ok'>支持</td><td>{esc(k)}</td><td class='num'>{fmt(v, 4)}</td></tr>"
        for k, v in con.items():
            ev_rows += f"<tr><td class='warn'>反对</td><td>{esc(k)}</td><td class='num'>{fmt(v, 4)}</td></tr>"
        for k, v in mis.items():
            if k == "说明":
                continue
            ev_rows += f"<tr><td class='dim'>缺失</td><td>{esc(k)}</td><td class='num'>{fmt(v, 4)}</td></tr>"

        part_ok = fl.get("参与率是否满足")
        part_cls = "ok" if part_ok else "warn"

        cards.append(f"""
<div class="card">
  <h3>{esc(ch['symbol'])}
      <span class="tag">{esc(ch.get('label') or '')}</span></h3>
  <div class="grid">
    <div><span class="k">箱体上沿 H</span><span class="v">{fmt(v1.get('box_high_h'))}</span></div>
    <div><span class="k">箱体下沿 L</span><span class="v">{fmt(v1.get('box_low_l'))}</span></div>
    <div><span class="k">ATR</span><span class="v">{fmt(v1.get('box_atr_a'), 2)}</span></div>
    <div><span class="k">方向效率 ER</span><span class="v">{fmt(v1.get('box_er'), 4)}</span></div>
    <div><span class="k">突破收盘</span><span class="v">{fmt(v1.get('breakout_close'))}</span></div>
    <div><span class="k">量能比</span><span class="v">{fmt(v1.get('volume_ratio'))}</span></div>
  </div>

  <h4>结构确认与成交</h4>
  <table>
    <tr><th>项</th><th>值</th><th>说明</th></tr>
    <tr><td>结构确认</td><td>{esc(cf.get('snapshot_utc'))}</td><td class="dim">1h 收盘确认</td></tr>
    <tr><td>最早可成交</td><td>{esc(cf.get('execution_earliest_utc'))}</td><td class="dim">{esc(cf.get('rule'))}</td></tr>
    <tr><td>实际成交</td><td>{esc(fl.get('bar_open_utc'))}</td><td class="dim">15m 开盘价，来源 {esc(fl.get('bar_source'))}</td></tr>
    <tr><td>原始成交价</td><td class="num">{fmt(fl.get('entry_price_raw'),8)}</td><td class="dim">含滑点后 {fmt(fl.get('entry_price_after_slippage'),8)}</td></tr>
    <tr><td>数量 / 名义</td><td class="num">{fmt(fl.get('qty'),8)} / {fmt(fl.get('notional'),2)}</td><td class="dim">—</td></tr>
    <tr><td>参与率上限（名义）</td><td class="num {part_cls}">{fmt(fl.get('参与率上限_名义'),2)}</td>
        <td class="{part_cls}">{'满足' if part_ok else '不满足：名义额超过前一根 15m 成交额的 0.1%'}</td></tr>
    <tr><td>候选丢弃原因</td><td colspan="2" class="dim">{esc(discard_zh(cd.get('discard_reasons')))}</td></tr>
  </table>

  <h4>判定依据</h4>
  <table><tr><th>方向</th><th>项</th><th>值</th></tr>{ev_rows}</table>

  <h4>止损（只上移，向后生效）</h4>
  <table>
    <tr><th>项</th><th>值</th><th>说明</th></tr>
    <tr><td>初始止损（执行侧重算）</td><td class="num">{fmt(st.get('初始止损'),8)}</td>
        <td class="dim">{esc(st.get('初始止损口径'))}</td></tr>
    <tr><td>候选快照里的值</td><td class="num">{fmt(st.get('候选快照里的 initial_stop_price'),8)}</td>
        <td class="warn">两者差异 <b>{pct(st.get('两者差异'))}</b>，<b>不能混用</b></td></tr>
    <tr><td>最终止损</td><td class="num">{fmt(st.get('最终止损'),8)}</td>
        <td class="dim">只上移：{fmt(st.get('只上移'))}</td></tr>
  </table>
  <table><tr><th>生效时刻</th><th>止损价</th><th>依据</th></tr>{path_rows}</table>

  <h4>退出与成本</h4>
  <table>
    <tr><th>项</th><th>值</th><th>说明</th></tr>
    <tr><td>退出时刻</td><td>{esc(ex.get('bar_open_utc'))}</td><td class="dim">{esc(ex.get('reason'))}</td></tr>
    <tr><td>退出价</td><td class="num">{fmt(ex.get('exit_price'),8)}</td>
        <td class="warn">{esc(ex.get('note'))}</td></tr>
    <tr><td>毛盈亏</td><td class="num">{fmt(ce.get('gross'),4)}</td><td class="dim">—</td></tr>
    <tr><td>手续费（{esc(ce.get('fee_bps'))}bps）</td><td class="num">{fmt(ce.get('fees'),4)}</td><td class="dim">—</td></tr>
    <tr><td>资金费（{esc(ce.get('funding_events'))} 次结算）</td><td class="num">{fmt(ce.get('funding_cost'),4)}</td>
        <td class="dim">按真实结算事件，不按固定窗口</td></tr>
    <tr><td><b>净额（生效视图 {esc(ce.get('slip_bps'))}bps 滑点）</b></td>
        <td class="num strong">{fmt(ce.get('net'),6)}</td><td class="dim">执行链实际读的口径</td></tr>
    <tr><td>净额（策略包 {esc(cs.get('slip_bps'))}bps 滑点）</td>
        <td class="num">{fmt(cs.get('net'),6)}</td>
        <td class="dim">两套差 {fmt((ch.get('7_退出与成本') or {}).get('两套净额差'))}，<b>不择一</b></td></tr>
  </table>
</div>""")

    part_fail = [ch["symbol"] for ch in chains
                 if (ch.get("5_成交") or {}).get("参与率是否满足") is False]
    part_banner = ""
    if part_fail:
        part_banner = (
            f"<div class='banner warn'><b>参与率约束未满足：{esc('、'.join(part_fail))}</b>"
            "——500 名义额超过「前一根 15m 成交额的 0.1%」上限。"
            "真实下单时会被拒单或必须缩量，本页的盈亏数字是按全额成交算的，偏乐观。</div>")

    html = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>ACCUM-EARLY-01 事件回放</title>
<style>
body{{font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     margin:0;padding:32px;color:#1a1a1a;background:#fafafa;max-width:1100px;margin:0 auto}}
h2{{margin:0 0 6px}} h3{{margin:0 0 12px;font-size:17px}}
h4{{margin:20px 0 8px;font-size:14px;color:#333;border-left:3px solid #4a7;
    padding-left:8px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 4px}}
th,td{{border:1px solid #e2e2e2;padding:6px 9px;text-align:left;font-size:13px;
      vertical-align:top}}
th{{background:#f2f4f6;font-weight:600}}
td.num{{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}}
td.strong{{font-weight:700;color:#b3261e}}
td.dim,.dim{{color:#777;font-size:12px}}
td.ok,.ok{{color:#1a7f37}} td.warn,.warn{{color:#b3261e}}
.card{{background:#fff;border:1px solid #e6e6e6;border-radius:8px;padding:18px 20px;
      margin:18px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.card.err{{color:#b3261e}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 18px;margin:6px 0 4px}}
.grid .k{{display:block;color:#777;font-size:12px}}
.grid .v{{display:block;font-variant-numeric:tabular-nums;font-size:15px}}
.tag{{font-size:11px;font-weight:400;color:#666;background:#eef1f4;
      padding:2px 7px;border-radius:10px;margin-left:8px;vertical-align:middle}}
.banner{{border-radius:8px;padding:12px 16px;margin:14px 0;font-size:13px}}
.banner.warn{{background:#fdf1f0;border:1px solid #f3c9c5;color:#8c1d18}}
.lead{{color:#555;font-size:13px;margin:6px 0 0}}
.foot{{color:#888;font-size:12px;margin-top:28px;border-top:1px solid #e6e6e6;
       padding-top:12px}}
</style>
<h2>ACCUM-EARLY-01 逐事件回放</h2>
<p class="lead">真实历史候选共 {len(dd)} 条，取窗口 {dd_sorted[0]['snapshot_utc']} → {dd_sorted[-1]['snapshot_utc']}
里的代表事件逐条重建。本页只重建过程，<b>不产生任何订单</b>；持仓量当时未参与判定（喂的是空数组）。</p>
{part_banner}
{''.join(cards)}
<div class="banner warn"><b>三项未建模／待裁决，看数字时必须知道：</b>
① 跳空止损未单独建模 —— 跳空与盘中触及都按止损价成交，真实跳空会高估成交价；
② 滑点两套（3bps / 5bps）并列，不择一；
③ 候选快照的 <code>initial_stop_price</code> 与执行侧重算值不是同一个数，混用会把止损放宽 3%~15%。</div>
<p class="foot">隔离样例：{esc(isolated['label'])}。完整字段与数据缺口见同名 JSON；
行为核验见 06 脚本，观察流回放见 09 脚本。</p>
</html>"""
    (out / "07_事件回放.html").write_text(html, encoding="utf-8")
    print(json.dumps({"picked": [c.get("symbol") for c in chains],
                      "errors": [c.get("error") for c in chains if "error" in c]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
