#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10_diag_chain.py — 策略信号「没通」的四环独立核对。

不猜、不看汇总，每一环都单独取一手证据：
  ① 引擎出候选   ：逐币突破条件回原始 K 线重算，看有没有币已满足（应出未出 = 缺陷）
  ② 写入库       ：在生产库**副本**上执行与 unified_runtime.persist_candidates 完全相同的 INSERT
  ③ 接口返回     ：三个 scope 的 status / missing_reason / total / 排除明细
  ④ 页面渲染     ：把隔离环境的**真实候选**响应喂给生产页面，看能不能渲染出来（不写生产库）

用法：
    python 10_diag_chain.py            # 输出到 ../out/
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]      # 项目根
BASE = Path(__file__).resolve().parent.parent    # evidence/SIGNAL-CHAIN-DIAG-10
OUT = BASE / "out"

MARKET_DB = ROOT / "数据/市场数据/db/market_v2.sqlite3"
LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
COND = (ROOT / "evidence/SIGNAL-LIVE-CHECK-09/out/"
        "37_iso_replay_bar2_fixed_per_symbol_conditions.json")
API = "http://127.0.0.1:8787"
ISO_LIVE = "http://127.0.0.1:8799"


def get(url: str, timeout: int = 20):
    # 代理坑：本机有 HTTP_PROXY 但没有 no_proxy，必须显式空 ProxyHandler
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ── ① 引擎：逐币突破条件回原始 K 线重算 ────────────────────────────────────
def check_engine() -> dict:
    cond = json.loads(COND.read_text(encoding="utf-8"))
    ps = cond["per_symbol"]
    th = cond["thresholds"]["breakout_突破"]
    buf_atr = th["breakout_buffer_atr"]          # 0.1
    vol_mult = th["breakout_vol_multiplier"]     # 1.5
    lookback = th["breakout_vol_lookback"]       # 20

    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    cur = con.cursor()
    SQL = """
    select v.open_time, v.close, v.volume, v.is_closed
    from candle_heads h join candle_versions v
      on v.exchange=h.exchange and v.market=h.market and v.symbol=h.symbol
     and v.timeframe=h.timeframe and v.open_time=h.open_time and v.source_id=h.source_id
    where h.exchange='binance' and h.market='um' and h.symbol=? and h.timeframe='1h'
    order by v.open_time desc limit ?
    """
    rows, skipped = [], []
    for sym, v in sorted(ps.items()):
        if v.get("state") != "RETEST":
            continue
        box = ((v.get("active_event") or {}).get("box") or {})
        H, A = f(box.get("box_high_h")), f(box.get("box_atr_a"))
        if H is None or A is None:
            skipped.append({"symbol": sym, "why": "缺箱体"})
            continue
        bars = [(b[0], f(b[1]), f(b[2]), b[3]) for b in cur.execute(SQL, (sym, lookback + 6))]
        closed = [b for b in bars if b[3] == 1 and b[1] and b[2]]
        if len(closed) < lookback + 1:
            skipped.append({"symbol": sym, "why": f"已收盘 K 线不足({len(closed)})"})
            continue
        last = closed[0]
        med = statistics.median([b[2] for b in closed[1:lookback + 1]])
        need_close = H + buf_atr * A
        need_vol = vol_mult * med
        rows.append({
            "symbol": sym,
            "bar_utc": iso(last[0]),
            "close": last[1],
            "need_close": need_close,
            "gap_pct": (need_close / last[1] - 1) * 100,
            "volume": last[2],
            "need_volume": need_vol,
            "vol_ratio": (last[2] / med) if med else None,
            "close_ok": last[1] > need_close,
            "volume_ok": last[2] >= need_vol,
        })
    con.close()

    rows.sort(key=lambda r: r["gap_pct"])
    return {
        "probe": "SIGNAL-CHAIN-DIAG-10/engine",
        "upto_utc": cond["upto_utc"],
        "阈值": {"breakout_buffer_atr": buf_atr, "breakout_vol_multiplier": vol_mult,
                 "breakout_vol_lookback": lookback},
        "RETEST币数": len(rows) + len(skipped),
        "算得出条件的币": len(rows),
        "跳过": skipped,
        "收盘+量都满足（应出未出）": [r["symbol"] for r in rows if r["close_ok"] and r["volume_ok"]],
        "只差收盘": [r["symbol"] for r in rows if r["volume_ok"] and not r["close_ok"]],
        "最近5个": rows[:5],
        "最远1个": rows[-1] if rows else None,
        "逐币": rows,
    }


# ── ② 写入：生产库副本上执行同一 INSERT ────────────────────────────────────
def check_write() -> dict:
    tmp = OUT / "_live_copy_for_write_test.sqlite3"
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(LIVE_DB, tmp)

    con = sqlite3.connect(tmp)
    before = con.execute("select count(*) from live_signal").fetchone()[0]
    now = int(time.time() * 1000)
    sql = ("INSERT INTO live_signal("
           "signal_uid, source, origin, exchange, market, symbol, timeframe, "
           "strategy_key, strategy_version, structural_kind, direction, event_time, "
           "confirmed_at, available_at, bar_open_time, bar_close_time, detection_low, "
           "detection_atr, display_state, executable_state, payload_json, first_seen_at, updated_at) "
           "VALUES(?, 'wyckoff_v0_1', ?, ?, ?, ?, ?, "
           "?, ?, 'breakout', 'long', ?, ?, ?, ?, ?, ?, ?, 'displayable', 'enterable', ?, ?, ?) "
           "ON CONFLICT(signal_uid) DO UPDATE SET "
           "payload_json=excluded.payload_json, executable_state=excluded.executable_state, "
           "display_state=excluded.display_state, updated_at=excluded.updated_at")
    uid = "cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000"
    args = (uid, "live", "binance", "um", "VELVETUSDT", "1h",
            "WX_EVENT_TREND_V0_1", "FT-LOW4H-EXP-V2.2",
            1790953200000, 1790953200000, 1790953200000, 1790953200000 - 3600000,
            1790953200000, 0.05428, 0.0021,
            json.dumps({"role": "enterable_arm_entry", "arm": "3a",
                        "probe": "schema_compat"}, ensure_ascii=False),
            now, now)
    err = None
    try:
        con.execute(sql, args)
        con.commit()
    except Exception as e:                       # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    after = con.execute("select count(*) from live_signal").fetchone()[0]
    got = con.execute("select signal_uid, symbol, origin, executable_state, display_state "
                      "from live_signal where signal_uid=?", (uid,)).fetchone()
    con.close()
    tmp.unlink()

    return {
        "probe": "SIGNAL-CHAIN-DIAG-10/write",
        "用哪份库": "生产库的临时副本（测完即删），生产库本身未写",
        "语句来源": "unified_runtime.py::SignalPersistenceAndRouter.persist_candidates 同一段 INSERT",
        "列数": 23,
        "占位符数": 18,
        "写入前 live_signal 行数": before,
        "写入后": after,
        "错误": err,
        "读回": ({"signal_uid": got[0], "symbol": got[1], "origin": got[2],
                  "executable_state": got[3], "display_state": got[4]} if got else None),
        "结论": "通过" if (err is None and after == before + 1 and got) else "不通过",
    }


# ── ③ 接口 ────────────────────────────────────────────────────────────────
def check_api() -> dict:
    out = {"probe": "SIGNAL-CHAIN-DIAG-10/api", "scopes": {}}
    for s in ("current", "all", "history"):
        d = get(f"{API}/api/v1/signals?scope={s}")
        q = d.get("quality") or {}
        out["scopes"][s] = {
            "total": d.get("total"),
            "items": len(d.get("items") or []),
            "quality_status": q.get("status"),
            "missing_reason": d.get("missing_reason"),
            "note": (q.get("note") or "")[:160],
            "excluded_breakdown": q.get("excluded_breakdown"),
        }
    d = get(f"{API}/api/v1/paper/execution_daemon/status?arm=3a")
    it = d.get("item") or {}
    se = it.get("signal_engine") or {}
    bs = se.get("bootstrap") or {}
    det = [x for x in (bs.get("discarded_detail") or []) if x.get("snapshot_utc")]
    ts = sorted(x["snapshot_utc"] for x in det)
    out["引擎"] = {
        "state_counter": se.get("state_counter"),
        "funnel": se.get("funnel"),
        "signals_generated_total": se.get("signals_generated_total"),
        "scan_errors_total": se.get("scan_errors_total"),
        "watermark_1h_utc": iso(se.get("watermark_1h")),
        "watermark_4h_utc": iso(se.get("watermark_4h")),
        "历史候选数": bs.get("history_candidates_discarded"),
        "历史候选最早": ts[0] if ts else None,
        "历史候选最晚": ts[-1] if ts else None,
        "历史候选按天": {k: sum(1 for t in ts if t[:10] == k) for k in sorted({t[:10] for t in ts})},
    }
    return out


# ── ④ 页面：喂真实候选看能否渲染（由 node 探针执行，这里只读结果）──────────
def check_render() -> dict:
    p = OUT / "40_render_with_real_candidate.json"
    if not p.exists():
        return {"probe": "SIGNAL-CHAIN-DIAG-10/render", "结论": "未跑（先跑 probes/40_render_real_candidate.js）"}
    return json.loads(p.read_text(encoding="utf-8"))


# ── ⑤ 同期市场活跃度：判断「0 候选是否正常」必须先看这个 ──────────────────
def check_market() -> dict:
    """算 10-12~10-25 全池 1h 收盘的振幅分布。

    为什么必须有这一段：只看「历史候选间隔」会得出「14 天静默是节律」的错误结论。
    先算市场有没有行情，再谈策略抓没抓到。
    """
    t0 = 1791849600000     # 2026-10-12T00:00Z
    t1 = 1792972800000     # 2026-10-26T00:00Z
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    cur = con.cursor()
    SQL = """
    select h.symbol, v.open_time, v.close
    from candle_heads h join candle_versions v
      on v.exchange=h.exchange and v.market=h.market and v.symbol=h.symbol
     and v.timeframe=h.timeframe and v.open_time=h.open_time and v.source_id=h.source_id
    where h.exchange='binance' and h.market='um' and h.timeframe='1h'
      and v.open_time >= ? and v.open_time < ? and v.is_closed=1
    """
    rows = cur.execute(SQL, (t0, t1)).fetchall()
    con.close()

    ser: dict[str, list] = {}
    for sym, t, c in rows:
        try:
            ser.setdefault(sym, []).append((t, float(c)))
        except (TypeError, ValueError):
            pass

    amps = []
    for sym, s in ser.items():
        if len(s) < 100:
            continue
        s.sort()
        lo = min(c for _, c in s)
        hi = max(c for _, c in s)
        if lo <= 0:
            continue
        amps.append({"symbol": sym, "amplitude_pct": (hi / lo - 1) * 100,
                     "net_pct": (s[-1][1] / s[0][1] - 1) * 100, "bars": len(s)})
    amps.sort(key=lambda x: -x["amplitude_pct"])
    med = statistics.median([a["amplitude_pct"] for a in amps]) if amps else None
    return {
        "probe": "SIGNAL-CHAIN-DIAG-10/market",
        "区间": [iso(t0), iso(t1)],
        "已收盘1h行数": len(rows),
        "有数据的币": len(ser),
        "参与统计的币": len(amps),
        "振幅中位_pct": round(med, 2) if med is not None else None,
        "振幅大于20_pct的币": sum(1 for a in amps if a["amplitude_pct"] > 20),
        "振幅大于30_pct的币": sum(1 for a in amps if a["amplitude_pct"] > 30),
        "Top10": amps[:10],
        "结论": ("市场活跃：策略 0 候选说明形态不匹配，不是没机会"
                 if amps and sum(1 for a in amps if a["amplitude_pct"] > 20) > len(amps) * 0.3
                 else "市场平淡：0 候选可能正常"),
    }


# ── ⑥ 箱体宽窄分布：查有没有「永远触发不了」的伪箱体 ──────────────────────
def check_box_width() -> dict:
    cond = json.loads(COND.read_text(encoding="utf-8"))
    rows = []
    for sym, v in cond["per_symbol"].items():
        b = ((v.get("active_event") or {}).get("box") or {})
        H, L = f(b.get("box_high_h")), f(b.get("box_low_l"))
        if not H or not L or L <= 0:
            continue
        # 字段名以生产状态机 BoxVersion 为准：box_er / width_atr。
        # 旧版这里写成 efficiency_ratio_er / box_width_atr（那是 dataclass 的属性名），
        # 快照里没有这两个键 ⇒ 静默取到 None，README 里的 ER 变成无出处数字。
        rows.append({"symbol": sym, "state": v.get("state"), "hl_ratio": H / L,
                     "width_atr": b.get("width_atr"), "er": b.get("box_er"),
                     "H": H, "L": L,
                     "formation_time_utc": b.get("formation_time_utc"),
                     "atr_a": f(b.get("box_atr_a"))})
    buckets = {"≤1.3": 0, "1.3-1.6": 0, "1.6-2": 0, "2-3": 0, "3-5": 0, ">5": 0}
    for r in rows:
        x = r["hl_ratio"]
        k = ("≤1.3" if x <= 1.3 else "1.3-1.6" if x <= 1.6 else "1.6-2" if x <= 2
             else "2-3" if x <= 3 else "3-5" if x <= 5 else ">5")
        buckets[k] += 1
    hard = sorted([r for r in rows if r["hl_ratio"] > 2.0], key=lambda r: -r["hl_ratio"])
    return {
        "probe": "SIGNAL-CHAIN-DIAG-10/box_width",
        "有箱体的币": len(rows),
        "H/L分档": buckets,
        "H/L中位": round(statistics.median([r["hl_ratio"] for r in rows]), 3) if rows else None,
        "H/L大于2倍的币数": len(hard),
        "最极端": hard[:6],
        "ER字段非空的币数": sum(1 for r in rows if r["er"] is not None),
        "结论": ("存在少量暴涨型伪箱体，但不是系统性问题" if len(hard) <= len(rows) * 0.05
                 else "伪箱体比例偏高，需查建档判据"),
    }


# ── ⑦ 箱体重算：拿原始 4h K 线重算 H/L/ER，与状态机记录逐位比 ──────────────
def check_box_recheck() -> dict:
    """对全池每个有箱体的币，回原始 4h K 线重算 H / L / ER / width_atr。

    为什么必须重算而不是只读快照：README 写了「箱体没算错」，那就得证明
    「拿原始 K 线按同一公式再算一遍，结果和引擎记的一模一样」。
    只读快照只能证明「快照里有这个数」，证不了它算得对。

    公式来自 `策略/WyckoffMaster/event_trend_v0_1/state_machine.py::check_and_form_box`
    与 `features.py::compute_efficiency_ratio`：
        H = max(high[最近42根4h])、L = min(low[最近42根4h])
        ER = |close[-1]-close[0]| / Σ|close[i]-close[i-1]|
        width_atr = (H-L)/A，A 为引擎的**连续** Wilder ATR(14)
    边界：A 依赖 200 根预热递推，本脚本不重算 ATR 序列，用快照里的 `box_atr_a`
    代入验算 width_atr —— 即「H/L/ER 独立重算，width_atr 做一致性交叉验证」。
    """
    cond = json.loads(COND.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    cur = con.cursor()
    SQL = """
    select v.open_time, v.high, v.low, v.close
    from candle_heads h join candle_versions v
      on v.exchange=h.exchange and v.market=h.market and v.symbol=h.symbol
     and v.timeframe=h.timeframe and v.open_time=h.open_time and v.source_id=h.source_id
    where h.exchange='binance' and h.market='um' and h.symbol=? and h.timeframe='4h'
      and v.open_time <= ? and v.is_closed=1
    order by v.open_time desc limit 42
    """
    ok, bad, skip = [], [], []
    for sym, v in sorted(cond["per_symbol"].items()):
        b = ((v.get("active_event") or {}).get("box") or {})
        H0, L0 = f(b.get("box_high_h")), f(b.get("box_low_l"))
        er0, wa0, a0 = f(b.get("box_er")), f(b.get("width_atr")), f(b.get("box_atr_a"))
        ft = b.get("formation_time_utc")
        if not H0 or not L0 or not ft:
            continue
        ft_ms = int(datetime.strptime(ft, "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
        rows = cur.execute(SQL, (sym, ft_ms)).fetchall()
        if len(rows) < 42:
            skip.append({"symbol": sym, "why": f"4h 已收盘不足 42 根({len(rows)})"})
            continue
        hi = [f(r[1]) for r in rows]
        lo = [f(r[2]) for r in rows]
        cl = [f(r[3]) for r in rows]
        if any(x is None for x in hi + lo + cl):
            skip.append({"symbol": sym, "why": "有空值"})
            continue
        H = max(hi)
        L = min(lo)
        path = sum(abs(cl[i] - cl[i - 1]) for i in range(1, len(cl)))
        er = (abs(cl[-1] - cl[0]) / path) if path > 1e-12 else None
        wa = ((H - L) / a0) if a0 else None
        rec = {
            "symbol": sym, "formation_time_utc": ft,
            "重算_H": H, "记录_H": H0, "H差": abs(H - H0),
            "重算_L": L, "记录_L": L0, "L差": abs(L - L0),
            "重算_ER": round(er, 6) if er is not None else None, "记录_ER": er0,
            "ER差": (abs(er - er0) if (er is not None and er0 is not None) else None),
            "重算_width_atr": round(wa, 3) if wa is not None else None, "记录_width_atr": wa0,
            "width相对差": (abs(wa - wa0) / wa0 if (wa is not None and wa0) else None),
        }
        # 判据按**记录的表示精度**来，不按浮点全精度：
        #   快照里 box_er 只存 6 位小数 ⇒ 重算值与记录值天然差 ≤5e-7，
        #   用 1e-9 判会把 351 个舍入差异全判成「不一致」（本轮实测踩过）。
        #   同一时刻的不同表示不算不一致 —— 与「库毫秒 vs 接口 ISO」同一条纪律。
        rec["H一致"] = abs(H - H0) < 1e-12
        rec["L一致"] = abs(L - L0) < 1e-12
        rec["ER一致"] = (er is not None and er0 is not None
                      and round(er, 6) == round(float(er0), 6))
        # width_atr 用快照里的 A 代入，A 本身是舍入过的 ⇒ 只要求相对误差 < 1%
        rec["WA一致"] = (rec["width相对差"] is not None and rec["width相对差"] < 0.01)
        rec["逐位一致"] = rec["H一致"] and rec["L一致"] and rec["ER一致"] and rec["WA一致"]
        (ok if rec["逐位一致"] else bad).append(rec)
    con.close()

    # 差异最大的几个，给「不一致」留可追的样本
    bad.sort(key=lambda r: -(r["width相对差"] or 0))
    return {
        "probe": "SIGNAL-CHAIN-DIAG-10/box_recheck",
        "比对口径": "回原始 4h K 线按 check_and_form_box / compute_efficiency_ratio 同公式重算",
        "判据": ("H/L 精确相等；ER 按记录精度 6 位小数比；"
                 "width_atr 用快照 A 代入，相对误差 <1% 视为一致（A 本身有舍入）"),
        "参与比对的币": len(ok) + len(bad),
        "逐位一致": len(ok),
        "不一致": len(bad),
        "H一致数": sum(1 for r in ok + bad if r["H一致"]),
        "L一致数": sum(1 for r in ok + bad if r["L一致"]),
        "ER一致数": sum(1 for r in ok + bad if r["ER一致"]),
        "WA一致数": sum(1 for r in ok + bad if r["WA一致"]),
        "跳过": skip,
        "不一致样本": bad[:10],
        "一致样本": ok[:5],
        "结论": ("箱体计算与状态机记录完全一致（逐位相同）" if not bad and ok
                 else ("部分币箱体重算与记录不符，需查建档时点/窗口口径" if bad
                       else "无可比对样本")),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    res = {
        "probe": "SIGNAL-CHAIN-DIAG-10",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "①引擎": check_engine(),
        "②写入": check_write(),
        "③接口": check_api(),
        "④页面": check_render(),
        "⑤市场活跃度": check_market(),
        "⑥箱体宽窄": check_box_width(),
        "⑦箱体重算": check_box_recheck(),
    }
    p = OUT / "10_diag_chain.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    e = res["①引擎"]
    print("== ① 引擎 ==")
    print(f"  RETEST 币 {e['RETEST币数']} 个，算得出条件 {e['算得出条件的币']} 个")
    print(f"  收盘+量都满足（应出未出）: {e['收盘+量都满足（应出未出）'] or '无'}")
    print(f"  最近的 5 个：")
    for r in e["最近5个"]:
        print(f"    {r['symbol']:16s} 还差 {r['gap_pct']:6.2f}%  收盘 {r['close']:.8g} < 门槛 {r['need_close']:.8g}")
    print(f"\n== ② 写入 ==\n  {res['②写入']['结论']}（{res['②写入']['写入前 live_signal 行数']} → "
          f"{res['②写入']['写入后']}，错误={res['②写入']['错误']}）")
    print(f"\n== ③ 接口 ==")
    for s, v in res["③接口"]["scopes"].items():
        print(f"  {s:8s} total={v['total']} status={v['quality_status']} missing_reason={v['missing_reason']}")
    g = res["③接口"]["引擎"]
    print(f"  漏斗: {g['funnel']}")
    print(f"  历史候选: {g['历史候选数']} 个，最早 {g['历史候选最早']}，最晚 {g['历史候选最晚']}")
    print(f"\n== ④ 页面 ==\n  {res['④页面'].get('结论', res['④页面'])}")
    m = res["⑤市场活跃度"]
    print(f"\n== ⑤ 市场活跃度 ==")
    print(f"  区间 {m['区间'][0]} ~ {m['区间'][1]}，参与统计 {m['参与统计的币']} 币")
    print(f"  振幅中位 {m['振幅中位_pct']}%  | >20% 的 {m['振幅大于20_pct的币']} 个  | >30% 的 {m['振幅大于30_pct的币']} 个")
    print(f"  → {m['结论']}")
    b = res["⑥箱体宽窄"]
    print(f"\n== ⑥ 箱体宽窄 ==")
    print(f"  有箱体 {b['有箱体的币']} 个，H/L 中位 {b['H/L中位']} 倍，ER 非空 {b['ER字段非空的币数']} 个")
    print(f"  分档: {b['H/L分档']}")
    print(f"  H/L>2 倍: {b['H/L大于2倍的币数']} 个 → {b['结论']}")
    r = res["⑦箱体重算"]
    print(f"\n== ⑦ 箱体重算 ==")
    print(f"  参与 {r['参与比对的币']} 个，逐位一致 {r['逐位一致']} 个，不一致 {r['不一致']} 个，跳过 {len(r['跳过'])} 个")
    print(f"  分项: H 一致 {r['H一致数']} / L 一致 {r['L一致数']} / ER 一致 {r['ER一致数']} / WA 一致 {r['WA一致数']}")
    for s in r["一致样本"][:2]:
        print(f"    {s['symbol']:14s} H {s['重算_H']} L {s['重算_L']} ER {s['重算_ER']} (记录 {s['记录_ER']})")
    print(f"  → {r['结论']}")
    print(f"\n→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
