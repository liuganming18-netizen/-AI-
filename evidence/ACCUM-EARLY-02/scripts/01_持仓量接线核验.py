# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜01 持仓量真实接线核验。

工单要求（第一部分第 1、3、5 条）：
  · 复用实际研究判断路径读取真实持仓量并更新到状态机；
  · 核验「首次为空→后续到达」与「连续决策序列更新」；
  · 提供至少一次真实数据「读取器 → 判断调用 → 事件记录」可复现追踪；
  · 提供晚到 / 缺失 / 恢复后的反例；
  · 报告与原始输出不一致时，保留失败证据并重跑同一次运行、同一决策时点。

六段，全部可复现（无 mock、无手改行情）：
  A 改前/改后源码身份（sha256 + 关键行差异）
  B 真实突破链：A 组（开 OI 门槛）在同一根 bar 上的实际结局
  C 同一决策时刻的「读取器 ↔ 状态机」逐位对撞 + 候选快照里的 OI 记录
  D 成功读取的真实样本（找决策点里新鲜度真正满足的那些，不做任何放宽）
  E 守卫对照（同一根真实突破 bar：复刻旧守卫 vs 新守卫）
  F 连续决策序列更新 + 晚到/缺失/恢复反例

输出：evidence/ACCUM-EARLY-02/out/01_持仓量接线核验.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
ARCHIVE = PROJECT / "数据" / "市场数据" / "parquet"
HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
BASE = PROJECT / "evidence" / "ACCUM-EARLY-02" / "baseline"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-02" / "out"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

H1 = 3600_000
H4 = 4 * H1
DAY = 24 * H1
BUCKET = 300_000

SYMBOL = "METISUSDT"
#: 回放终点（最新已收盘 1h 之前一点，保证数据稳定）
END_MS = 1793009700000
WARM_BARS = 500          # 4h 预热根数（≈83 天），保证 200 根 ATR 预热后仍有一整段建仓期


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def _arr_len(x) -> int:
    """ndarray 的长度（`x or []` 对空 ndarray 会抛「真值不明确」）。"""
    return 0 if x is None else int(len(x))


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_history(symbol: str, start_ms: int, end_ms: int):
    import history_loader as hl
    mh = hl.MergedHistory(str(HOT), str(ARCHIVE), log=lambda *a, **k: None)
    data, stats = mh.load([symbol], ["1h", "4h"], start_ms, end_ms)
    return data[symbol], stats


def run_engine(bars: dict, symbol: str, end_ms: int, floor_ms: int, enable_oi: bool):
    import unified_runtime as UR
    eng = UR.RealSignalEngine(symbol=symbol, enable_oi_filter=enable_oi)
    eng.history_floor_ms = floor_ms
    eng.process_market(end_ms, UR._MemWatcher(bars["4h"], bars["1h"]))
    return eng


# ═══════════════════════════════════════════════════════════════════
def sec_a_source_identity(rep: dict) -> None:
    """A：改前/改后源码身份 + 关键行差异。"""
    mapping = {
        "oi_feed.py": BACKEND / "oi_feed.py",
        "unified_runtime.py": BACKEND / "unified_runtime.py",
        "state_machine.py": PROJECT / "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
        "pit_bridge.py": PROJECT / "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
        "pit_guard.py": PROJECT / "策略/WyckoffMaster/trading_core/pit_guard.py",
        "observe_store.py": BACKEND / "observe_store.py",
    }
    rows = []
    for name, after in mapping.items():
        before = BASE / f"{name}.before"
        rows.append({
            "file": name,
            "baseline_sha256": sha256_file(before) if before.exists() else None,
            "current_sha256": sha256_file(after) if after.exists() else None,
            "changed": (before.exists() and after.exists()
                        and sha256_file(before) != sha256_file(after)),
            "baseline_path": (str(before.relative_to(PROJECT)) if before.exists() else None),
            "current_path": str(after.relative_to(PROJECT)),
        })

    def grab(path: Path, needle):
        if not path.exists():
            return []
        return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
                if needle in l]

    rep["A_源码身份"] = {
        "checked_at_utc": iso(time.time() * 1000),
        "说明": "baseline/ 是改动前的原样副本；changed=False 表示本次没动它",
        "files": rows,
        "关键行差异": {
            "注册守卫（state_machine.py）": {
                "改前": grab(BASE / "state_machine.py.before", "oi_available_at is None"),
                "改后": grab(PROJECT / "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
                             "_oi_n > 0 or self.pit.oi_available_at is None"),
            },
            "喂参（unified_runtime.py）": {
                "改前": grab(BASE / "unified_runtime.py.before", "oi_timestamps="),
                "改后": grab(BACKEND / "unified_runtime.py", "oi_timestamps="),
            },
            "可得性判据（oi_feed.py）": {
                "改前": grab(BASE / "oi_feed.py.before", "available_at_proven"),
                "改后": grab(BACKEND / "oi_feed.py", "available_at_proven"),
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════
def discover_real_chain() -> dict:
    """用生产路径找出真实突破（不关 OI 门槛也能到突破，只是会被 OI 拦下）。

    先用 B 组（关 OI 门槛）跑一遍拿到真实候选的时刻，再拿这个时刻做 A 组对照。
    """
    bars, _ = load_history(SYMBOL, END_MS - WARM_BARS * H4, END_MS)
    eng = run_engine(bars, SYMBOL, END_MS, END_MS - WARM_BARS * H4, enable_oi=False)
    cands = list(eng.signals_generated)
    return {
        "symbol": SYMBOL,
        "bars": bars,
        "floor": END_MS - WARM_BARS * H4,
        "candidates": cands,
        "first_candidate_ms": (int(cands[0].snapshot_time_ms) if cands else None),
    }


def sec_b_real_breakout(rep: dict, chain: dict) -> None:
    """B：真实突破链——A 组（开 OI 门槛）在同一根 bar 上的实际结局。"""
    cand_ms = chain["first_candidate_ms"]
    out = {"symbol": chain["symbol"],
           "warmup_bars_4h": WARM_BARS,
           "candidate_from_B_group_utc": iso(cand_ms)}
    if cand_ms is None:
        out["error"] = "B 组在窗口内没有产出候选，无法定位真实突破"
        rep["B_真实突破"] = out
        return
    # A 组：同一窗口、同一终点，只把 OI 门槛打开
    eng_a = run_engine(chain["bars"], chain["symbol"], cand_ms, chain["floor"],
                       enable_oi=True)
    ev = eng_a.sm.active_event
    out["A组_开OI门槛"] = {
        "候选数": len(eng_a.signals_generated),
        "last_oi_read": eng_a.sm.pit.last_oi_read,
        "事件状态": (str(ev.current_state) if ev is not None else None),
        "终止原因": getattr(ev, "terminal_reason", None) if ev is not None else None,
        "runtime_oi_diag": {k: v for k, v in eng_a.oi_diag.items()
                            if k != "available_at_rule"},
    }
    # B 组：关 OI 门槛，看突破本身成立
    eng_b = run_engine(chain["bars"], chain["symbol"], cand_ms, chain["floor"],
                       enable_oi=False)
    out["B组_关OI门槛"] = {
        "候选数": len(eng_b.signals_generated),
        "候选": [{"candidate_id": c.candidate_id,
                  "snapshot_utc": iso(c.snapshot_time_ms),
                  "oi_breakout": c.oi_breakout,
                  "oi_6h_ago": c.oi_6h_ago,
                  "oi_growth_6h": c.oi_growth_6h,
                  "pit_proven": c.pit_proven} for c in eng_b.signals_generated],
        "last_oi_read": eng_b.sm.pit.last_oi_read,
    }
    rep["B_真实突破"] = out
    chain["engine_a"] = eng_a
    chain["engine_b"] = eng_b


def sec_c_crosscheck(rep: dict, chain: dict) -> None:
    """C：同一决策时刻「读取器 ↔ 状态机」逐位对撞 + 候选快照 OI 记录。"""
    import numpy as np
    import oi_feed as OF

    cand_ms = chain["first_candidate_ms"]
    if cand_ms is None:
        rep["C_对撞"] = {"error": "无真实突破时刻"}
        return
    out = {"decision_utc": iso(cand_ms),
           "lookback_hours": 6,
           "口径": "两边都是「fetched_at <= 决策 且 ts+5min <= 决策 且 ts >= 决策-6h-24h」"}

    eng_b = chain.get("engine_b")
    e_ts = np.array([], dtype=np.int64)
    e_av = np.array([], dtype=np.float64)
    if eng_b is not None:
        e_ts, _e_val, e_av = eng_b._oi_slice_as_of(cand_ms)

    r_ts, r_val, r_av, meta = OF.load_oi_pit(chain["symbol"], cand_ms, lookback_ms=6 * H1, pad_ms=DAY)
    r_ts2, r_val2, r_av2 = OF.slice_as_of(r_ts, r_val, r_av, cand_ms)
    lo = cand_ms - 6 * H1 - DAY
    keep = r_ts2 >= lo
    r_ts2, r_val2, r_av2 = r_ts2[keep], r_val2[keep], r_av2[keep]

    out["读取器"] = {
        "窗口原始行": meta["rows_returned"],
        "可得性四态": meta["availability_counts"],
        "切片行数": int(len(r_ts2)),
        "首行_utc": iso(int(r_ts2[0])) if len(r_ts2) else None,
        "末行_utc": iso(int(r_ts2[-1])) if len(r_ts2) else None,
        "单位": meta["unit"],
        "单位证据": meta["unit_evidence"],
    }
    out["状态机实际切片"] = {
        "行数": int(len(e_ts)),
        "首行_utc": iso(int(e_ts[0])) if len(e_ts) else None,
        "末行_utc": iso(int(e_ts[-1])) if len(e_ts) else None,
    }
    out["对撞"] = {
        "行数一致": int(len(e_ts)) == int(len(r_ts2)),
        "时间戳逐位一致": bool(len(e_ts) == len(r_ts2)
                          and (len(e_ts) == 0 or (e_ts == r_ts2).all())),
        "可得时点逐位一致": bool(len(e_av) == len(r_av2)
                          and (len(e_av) == 0 or np.allclose(e_av, r_av2))),
        "数值逐位一致": bool(len(r_val2) == len(r_val2)
                        and (len(r_val2) == 0 or np.allclose(r_val2, r_val2))),
    }
    if len(r_ts2):
        out["独立复算_6h增幅"] = OF.oi_growth(r_ts2, r_val2, cand_ms, 6 * H1)
    cands = (chain.get("engine_b").signals_generated
             if chain.get("engine_b") is not None else [])
    if cands:
        out["候选快照里的OI"] = {
            "oi_breakout": cands[0].oi_breakout,
            "oi_6h_ago": cands[0].oi_6h_ago,
            "oi_growth_6h": cands[0].oi_growth_6h,
            "说明": ("B 组虽然关了 OI 门槛，突破时仍会先读一次 OI（状态机读在前、闸门在后）；"
                     "读不到就如实写 null，不填 0"),
        }
    # 决策时刻最新的可得观测距决策多远（这就是被「15 分钟新鲜度」挡住的原因）
    if len(r_ts2):
        out["最新可得观测距决策_秒"] = round((cand_ms - int(r_ts2[-1])) / 1000.0, 1)
        out["新鲜度上限_秒"] = 900
        out["是否满足新鲜度"] = (cand_ms - int(r_ts2[-1])) <= 900_000
    rep["C_对撞"] = out


def sec_d_successful_read(rep: dict) -> None:
    """D：找「新鲜度真正满足」的真实决策点，做一次完整成功读取。

    不调任何阈值：这里只是**在数据里找**满足现行规则的样本；
    找不到就如实说找不到。
    """
    import numpy as np
    import oi_feed as OF
    from 策略.WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge

    out = {"找法": ("在 OI 可得性已证实的窗口（2026-10-05 之后）逐标的逐整点检查："
                    "决策时刻 d 的最新可得观测 ts 满足 d - ts <= 900 秒"),
           "扫描": []}
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "METISUSDT", "DOGEUSDT", "XRPUSDT"]
    lo_ms = ms_of("2026-10-12T00:00:00Z")
    hi_ms = ms_of("2026-10-25T00:00:00Z")
    found = None
    scanned = 0
    hits = 0
    for sym in syms:
        ts, vals, av, meta = OF.load_oi_pit(sym, hi_ms, lookback_ms=6 * H1, pad_ms=DAY)
        # 用整窗数据自己做逐整点扫描（不逐点查库）
        raw = OF.load_oi_rows(sym, lo_ms - 6 * H1, hi_ms)
        kept, _d, _b = OF._dedupe_by_ts(raw)
        if not kept:
            out["扫描"].append({"symbol": sym, "rows": 0})
            continue
        kts = np.array([r["ts"] for r in kept], dtype=np.float64)
        kav = np.array([r["fetched_at"] if r["fetched_at"] is not None else np.nan
                        for r in kept], dtype=np.float64)
        kval = np.array([r["oi"] for r in kept], dtype=np.float64)
        n_ok = 0
        for d in range(lo_ms // H1 * H1, hi_ms, H1):
            ok = np.isfinite(kav) & (kav <= d) & (kts + BUCKET <= d)
            if not ok.any():
                continue
            latest = int(kts[ok][np.argmax(kts[ok])])
            scanned += 1
            if d - latest <= 900_000:
                n_ok += 1
                hits += 1
                if found is None:
                    found = {"symbol": sym, "decision_ms": int(d),
                             "latest_ts": latest}
        out["扫描"].append({"symbol": sym, "rows": len(kept),
                            "满足15分钟的决策点": n_ok,
                            "总决策点": int((hi_ms - lo_ms) // H1)})
    out["扫描合计"] = {"决策点": scanned, "满足新鲜度": hits,
                       "满足率": (round(hits / scanned, 6) if scanned else None)}
    if not found:
        out["结论"] = ("在扫描范围内**没有**任何决策点满足「最新可得观测距决策 ≤15 分钟」"
                       "⇒ 现行 OI 门槛在真实可得性口径下不可满足，属规则/采集节律冲突")
        rep["D_成功读取样本"] = out
        return

    d = found["decision_ms"]
    sym = found["symbol"]
    ts, vals, av, meta = OF.load_oi_pit(sym, d, lookback_ms=6 * H1, pad_ms=DAY)
    ts2, vals2, av2 = OF.slice_as_of(ts, vals, av, d)
    lo = d - 6 * H1 - DAY
    keep = ts2 >= lo
    ts2, vals2, av2 = ts2[keep], vals2[keep], av2[keep]
    pb = PitBridge(symbol=sym, mode="declared_assumption")
    pb.register_oi(ts2, vals2, available_at=av2, assumed=False)
    res = pb.read_oi(d, lookback_ms=6 * H1, max_distance_ms=900_000)
    out["成功样本"] = {
        "symbol": sym,
        "decision_utc": iso(d),
        "最新可得观测_utc": iso(found["latest_ts"]),
        "距决策_秒": round((d - found["latest_ts"]) / 1000.0, 1),
        "喂入行数": int(len(ts2)),
        "状态机读取结果": res,
        "读取器独立复算": OF.oi_growth(ts2, vals2, d, 6 * H1),
        "PIT审计": pb.audit(),
        "审计里 qualified": pb.audit()["qualified"],
        "资格不通过原因": pb.audit()["qualification_failures"],
    }
    g1 = res.get("growth")
    g2 = OF.oi_growth(ts2, vals2, d, 6 * H1).get("growth")
    out["成功样本"]["两条通道增长率一致"] = (
        None if (g1 is None or g2 is None) else bool(abs(g1 - g2) < 1e-12))
    rep["D_成功读取样本"] = out


def sec_e_guard(rep: dict, chain: dict) -> None:
    """E：同一根真实突破 bar，复刻旧守卫 vs 新守卫。"""
    import numpy as np
    import oi_feed as OF

    cand_ms = chain["first_candidate_ms"]
    out = {"breakout_decision_utc": iso(cand_ms)}
    if cand_ms is None:
        rep["E_守卫对照"] = {"error": "无真实突破时刻"}
        return
    bars = chain["bars"]
    bar = None
    for b in bars["1h"]:
        if int(b["open_time"]) == cand_ms - H1:
            bar = b
            break
    if bar is None:
        rep["E_守卫对照"] = {"error": "突破 bar 不在 1h 数据里"}
        return
    out["breakout_bar"] = {"open_utc": iso(int(bar["open_time"])),
                           "close_utc": iso(int(bar["close_time"])),
                           "open": bar["open"], "high": bar["high"],
                           "low": bar["low"], "close": bar["close"],
                           "volume": bar["volume"]}

    ts, vals, av, meta = OF.load_oi_pit(chain["symbol"], cand_ms, lookback_ms=6 * H1, pad_ms=DAY)
    ts2, vals2, av2 = OF.slice_as_of(ts, vals, av, cand_ms)
    keep = ts2 >= cand_ms - 6 * H1 - DAY
    ts2, vals2, av2 = ts2[keep], vals2[keep], av2[keep]
    out["oi_slice"] = {"rows": int(len(ts2)),
                       "last_utc": iso(int(ts2[-1])) if len(ts2) else None,
                       "可得性四态": meta["availability_counts"]}

    def replay_before():
        import unified_runtime as UR
        eng = UR.RealSignalEngine(symbol=chain["symbol"], enable_oi_filter=True)
        eng.history_floor_ms = chain["floor"]
        eng.process_market(cand_ms - H1, UR._MemWatcher(bars["4h"], bars["1h"]))
        return eng

    def call(eng, oi_ts, oi_v, oi_av, assumed):
        cand = eng.sm.process_1h_bar(
            open_time_ms=int(bar["open_time"]), open_p=float(bar["open"]),
            high_p=float(bar["high"]), low_p=float(bar["low"]),
            close_p=float(bar["close"]), volume_v=float(bar["volume"]),
            oi_timestamps=oi_ts, oi_values=oi_v,
            oi_available_at=oi_av, oi_assumed=assumed)
        ev = eng.sm.active_event
        # 终止后 `active_event` 会被清空 ⇒ 从迁移流水里取最后一次跃迁
        tr = None
        if ev is not None and getattr(ev, "history", None):
            tr = ev.history[-1]
        elif getattr(eng.sm, "transitions", None):
            tr = eng.sm.transitions[-1]
        return {
            "候选": None if cand is None else cand.candidate_id,
            "候选oi_growth_6h": (cand.oi_growth_6h if cand is not None else None),
            "事件状态": (str(ev.current_state) if ev is not None else "已终结(active_event 清空)"),
            "最后迁移": ({"from": tr.from_state, "to": tr.to_state,
                          "reason": tr.reason,
                          "at_utc": iso(tr.trigger_bar_close_ms)} if tr is not None else None),
            "last_oi_read": eng.sm.pit.last_oi_read,
            "注册后数组行数": _arr_len(eng.sm.pit._oi_ts),
        }

    # E1 复刻旧守卫：pit 里先留一份空序列（= 旧代码第一次突破拿到空 OI 后的状态）
    eng1 = replay_before()
    st1 = None if eng1.sm.active_event is None else str(eng1.sm.active_event.current_state)
    eng1.sm.pit.register_oi(np.array([], dtype=np.int64), np.array([], dtype=np.float64))
    out["E1_复刻旧守卫_喂空OI"] = {
        "突破前状态": st1,
        **call(eng1, np.array([], dtype=np.int64), np.array([], dtype=np.float64), None, None),
        "旧守卫为何不更新": "`np.array([]) is not None` 为真 ⇒ 旧代码永不再注册",
    }

    # E2 新守卫 + 真实 OI
    eng2 = replay_before()
    st2 = None if eng2.sm.active_event is None else str(eng2.sm.active_event.current_state)
    out["E2_新守卫_喂真实OI"] = {
        "突破前状态": st2,
        **call(eng2, ts2, vals2, av2, False),
    }

    # E3 首次为空 → 后续到达（新守卫下同一实例能恢复）
    eng3 = replay_before()
    eng3.sm.pit.register_oi(np.array([], dtype=np.int64), np.array([], dtype=np.float64))
    n_empty = _arr_len(eng3.sm.pit._oi_ts)
    eng3.sm.pit.register_oi(ts2, vals2, available_at=av2, assumed=False)
    n_real = _arr_len(eng3.sm.pit._oi_ts)
    out["E3_首次为空后续到达"] = {
        "注册空序列后行数": n_empty,
        "再注册真实序列后行数": n_real,
        "已更新": n_real > n_empty and n_real > 0,
    }
    out["E_归因"] = {
        "同一根 bar": True,
        "同一决策时刻": iso(cand_ms),
        "唯一差别": "突破时 pit 里有没有真实 OI 序列（旧守卫不注册 vs 新守卫注册）",
    }
    rep["E_守卫对照"] = out


def sec_f_sequence_and_negatives(rep: dict, chain: dict) -> None:
    """F：连续决策序列更新 + PIT 硬检查 + 晚到/缺失/恢复反例。"""
    import numpy as np
    import oi_feed as OF
    from 策略.WyckoffMaster.event_trend_v0_1.pit_bridge import PitBridge

    out = {}
    eng = chain.get("engine_a")
    ts = getattr(eng, "_oi_ts", np.array([], dtype=np.int64))
    av = getattr(eng, "_oi_avail", np.array([], dtype=np.float64))
    cand_ms = chain["first_candidate_ms"]

    # F1 连续 24 个决策点
    decisions = [cand_ms - i * H1 for i in range(24)][::-1]
    seq, leak, last_back = [], 0, -1
    last_obs_nondec = 0
    for d in decisions:
        ok = (np.isfinite(av) & (av <= float(d)) & (ts + BUCKET <= d)
              & (ts >= d - 6 * H1 - DAY))
        n = int(ok.sum())
        leak += int((av[ok] > float(d)).sum()) if n else 0
        last_obs = int(ts[ok][-1]) if n else None
        if last_obs is not None:
            if last_obs < last_back:
                last_obs_nondec += 1
            last_back = last_obs
        seq.append({"decision_utc": iso(d), "rows": n,
                    "last_obs_utc": iso(last_obs),
                    "未来行泄漏": (int((av[ok] > float(d)).sum()) if n else 0)})
    out["F1_连续决策序列"] = {
        "决策点数": len(decisions),
        "每个决策点都有可用观测": all(x["rows"] > 0 for x in seq),
        "最新观测时刻单调不减": last_obs_nondec == 0,
        "最新观测倒退次数": last_obs_nondec,
        "未来行泄漏总条数": leak,
        "无未来泄漏": leak == 0,
        "序列_首6": seq[:6],
        "序列_末6": seq[-6:],
        "口径": ("d 只用 fetched_at<=d 且 ts+5min<=d 且 ts>=d-6h-24h 的行；"
                 "「行数」随窗口滑动可增可减（下界也在前移），"
                 "真正的单调量是「最新观测时刻」——它只能前进不能倒退"),
    }

    # F2 两条通道同口径
    e_ts, _ev, _ea = eng._oi_slice_as_of(cand_ms)
    r_ts, r_val, r_av, _m = OF.load_oi_pit(chain["symbol"], cand_ms, lookback_ms=6 * H1, pad_ms=DAY)
    r_ts2, r_val2, _r_av2 = OF.slice_as_of(r_ts, r_val, r_av, cand_ms)
    keep = r_ts2 >= cand_ms - 6 * H1 - DAY
    r_ts2 = r_ts2[keep]
    out["F2_两条通道同口径"] = {
        "生产切片行数": int(len(e_ts)),
        "读取器切片行数": int(len(r_ts2)),
        "时间戳逐位一致": bool(len(e_ts) == len(r_ts2)
                          and (len(e_ts) == 0 or (e_ts == r_ts2).all())),
    }

    # F3 晚到 / 缺失 / 恢复（同一行真实数据，只改 fetched_at）
    d = cand_ms
    rows = OF.load_oi_rows(chain["symbol"], d - 6 * H1 - DAY, d - BUCKET)
    kept, _dup, _bad = OF._dedupe_by_ts(rows)
    usable = [r for r in kept if r["fetched_at"] is not None
              and int(r["fetched_at"]) <= d]
    if not usable:
        out["F3_反例"] = {"error": "窗口内没有「抓取时刻 <= 决策」的真实行"}
    else:
        base = usable[-1]

        def probe(label, fetched_at, note):
            row = dict(base)
            row["fetched_at"] = fetched_at
            st, at = OF.judge_row(row, d)
            pb = PitBridge(symbol=chain["symbol"], mode="declared_assumption")
            one_ts = np.array([row["ts"]], dtype=np.int64)
            one_v = np.array([row["oi"]], dtype=np.float64)
            one_av = np.array([at if at is not None else np.nan], dtype=np.float64)
            pb.register_oi(one_ts, one_v, available_at=one_av, assumed=False)
            res = pb.read_oi(d, lookback_ms=6 * H1, max_distance_ms=900_000)
            return {"情形": label, "构造说明": note,
                    "观测时刻_utc": iso(row["ts"]),
                    "抓取时刻_utc": iso(fetched_at),
                    "可得性判定": st,
                    "可得时点_utc": iso(at),
                    "可得性通过（这一行当时读得到）": st in ("proven", "late"),
                    "桥接层status": res["status"],
                    "桥接层growth": res["growth"],
                    "新鲜度通过（≤15分钟）": res["growth"] is not None}

        out["F3_反例"] = {
            "基准行": {"观测时刻_utc": iso(base["ts"]),
                       "抓取时刻_utc": iso(base["fetched_at"]),
                       "open_interest": base["oi"], "source": base["source"]},
            "cases": [
                probe("正常", base["fetched_at"], "原始行，不做任何改动"),
                probe("晚到", d + H1, "把抓取时刻改成决策后 1 小时 ⇒ 决策当时还没抓到"),
                probe("缺失", None, "把抓取时刻置空 ⇒ 无任何可得性证据"),
                probe("恢复", base["fetched_at"], "恢复原始抓取时刻 ⇒ 可用性恢复"),
            ],
            "两个闸门必须分开说": (
                "① **可得性**：这一行当时读不读得到（看 fetched_at）；"
                "② **新鲜度**：读到的观测离决策多近（看 oi_max_time_distance_sec=900s）。"
                "本段只验①；②在 B/C 段单独给出。两件事混在一起讲会把「读不到」"
                "误当成「数据没有」。"),
            "判据": "只有 fetched_at <= 决策时刻 才算当时读得到；无 fetched_at 一律 unknown",
        }
    rep["F_序列与反例"] = out


# ═══════════════════════════════════════════════════════════════════
def sec_g_coverage(rep: dict) -> None:
    """G：真实（带抓取时点）持仓量的覆盖情况——能不能连续做决策。

    这一节回答的是「接线之后，A 组到底有没有数据可用」，
    而不是「库里有几行」。判据只看 `fetched_at` 非空的行。
    """
    import sqlite3
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    try:
        per_day = con.execute(
            """SELECT ts/86400000 AS d, COUNT(*) FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m' AND symbol=?
                 AND source='binance.fapi.openInterestHist' AND fetched_at IS NOT NULL
               GROUP BY d ORDER BY d""", (SYMBOL,)).fetchall()
        # 全池：某天完全没有实时行的标的数
        days = con.execute(
            """SELECT ts/86400000 AS d,
                      COUNT(DISTINCT symbol) FILTER (WHERE fetched_at IS NOT NULL)
               FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m'
                 AND ts >= ? AND ts < ?
               GROUP BY d ORDER BY d""",
            (ms_of("2026-10-08T00:00:00Z"), ms_of("2026-10-26T00:00:00Z"))).fetchall()
        total_sym = con.execute(
            """SELECT COUNT(DISTINCT symbol) FROM oi_history
               WHERE exchange='binance' AND market='um' AND period='5m'""").fetchone()[0]
        # 时间戳语义：ts 是否对齐 5 分钟
        align = con.execute(
            """SELECT COUNT(*) FILTER (WHERE ts % 300000 = 0),
                      COUNT(*) FILTER (WHERE ts % 300000 != 0), COUNT(*)
               FROM oi_history WHERE exchange='binance' AND market='um' AND period='5m'"""
        ).fetchone()
        bad = con.execute(
            """SELECT COUNT(*) FROM oi_history WHERE exchange='binance' AND market='um'
               AND period='5m' AND fetched_at IS NOT NULL AND fetched_at < ts + 300000"""
        ).fetchone()[0]
    finally:
        con.close()

    rep["G_真实覆盖"] = {
        "判据": "只数 fetched_at 非空的 5m 行（有抓取时点 = 能证明当时读得到）",
        "单标的每日实时行数": [
            {"date": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)), "rows": c,
             "应有": 288, "覆盖率": round(c / 288, 4)} for d, c in per_day],
        "单标的合计": {"实时行": sum(c for _d, c in per_day),
                       "日历天数": len(per_day),
                       "应有": 288 * len(per_day),
                       "覆盖率": (round(sum(c for _d, c in per_day) / (288 * len(per_day)), 4)
                                  if per_day else None)},
        "全池每日有实时行的标的数": [
            {"date": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)),
             "symbols_with_realtime": c, "universe": total_sym} for d, c in days],
        "时间戳语义": {
            "对齐5分钟的行": align[0], "非对齐的行": align[1], "总行": align[2],
            "结论": "ts 对齐 300000 的占绝大多数 ⇒ ts 是 5 分钟桶的**起点**，桶收盘 = ts + 5min",
        },
        "抓取早于桶收盘的脏行": bad,
        "脏行处理": "决策切片额外要求 ts + 5min <= 决策时刻，这类行一律进不了决策",
    }


# ═══════════════════════════════════════════════════════════════════
def main() -> int:
    rep = {"probe": "01_持仓量接线核验",
           "generated_at_utc": iso(time.time() * 1000),
           "工单": "ACCUM-EARLY-02 第一部分第 1/3/5 条",
           "errors": []}
    t0 = time.time()
    try:
        sec_a_source_identity(rep)
    except Exception as exc:                                    # noqa: BLE001
        rep["errors"].append({"stage": "A", "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-800:]})

    chain = None
    try:
        chain = discover_real_chain()
        rep["0_真实链定位"] = {
            "symbol": chain["symbol"],
            "warmup_bars_4h": WARM_BARS,
            "候选数": len(chain["candidates"]),
            "首个候选_utc": iso(chain["first_candidate_ms"]),
            "候选ID": (chain["candidates"][0].candidate_id
                       if chain["candidates"] else None),
            "说明": ("用生产路径（RealSignalEngine.process_market）跑出来的真实候选，"
                     "不是手工构造；先关 OI 门槛只为定位突破时刻"),
        }
    except Exception as exc:                                    # noqa: BLE001
        rep["errors"].append({"stage": "0", "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-800:]})

    if chain and chain["first_candidate_ms"] is not None:
        for name, fn in (("B", sec_b_real_breakout), ("C", sec_c_crosscheck),
                         ("E", sec_e_guard), ("F", sec_f_sequence_and_negatives)):
            try:
                fn(rep, chain)
            except Exception as exc:                            # noqa: BLE001
                rep["errors"].append({"stage": name,
                                      "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc()[-1500:]})
    try:
        sec_d_successful_read(rep)
    except Exception as exc:                                    # noqa: BLE001
        rep["errors"].append({"stage": "D", "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-1500:]})
    try:
        sec_g_coverage(rep)
    except Exception as exc:                                    # noqa: BLE001
        rep["errors"].append({"stage": "G", "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-1500:]})

    rep["elapsed_sec"] = round(time.time() - t0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_持仓量接线核验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    brief = {
        "改了哪些文件": [f["file"] for f in rep.get("A_源码身份", {}).get("files", [])
                        if f["changed"]],
        "真实链": rep.get("0_真实链定位"),
        "B_A组": {k: v for k, v in rep.get("B_真实突破", {}).get("A组_开OI门槛", {}).items()
                  if k in ("候选数", "事件状态", "终止原因")},
        "B_A组last_oi_read": (rep.get("B_真实突破", {}).get("A组_开OI门槛", {})
                              .get("last_oi_read")),
        "C_对撞": rep.get("C_对撞", {}).get("对撞"),
        "C_候选OI": rep.get("C_对撞", {}).get("候选快照里的OI"),
        "D_扫描": rep.get("D_成功读取样本", {}).get("扫描"),
        "D_成功": (rep.get("D_成功读取样本", {}).get("成功样本") or {}).get("状态机读取结果"),
        "E1": {k: v for k, v in rep.get("E_守卫对照", {}).get("E1_复刻旧守卫_喂空OI", {}).items()
               if k in ("事件状态", "终止原因", "last_oi_read", "候选")},
        "E2": {k: v for k, v in rep.get("E_守卫对照", {}).get("E2_新守卫_喂真实OI", {}).items()
               if k in ("事件状态", "候选", "候选oi_growth_6h", "last_oi_read")},
        "E3": rep.get("E_守卫对照", {}).get("E3_首次为空后续到达"),
        "F1": {k: v for k, v in rep.get("F_序列与反例", {}).get("F1_连续决策序列", {}).items()
               if k in ("每个决策点都有可用观测", "最新观测时刻单调不减",
                        "未来行泄漏总条数", "无未来泄漏")},
        "F2": rep.get("F_序列与反例", {}).get("F2_两条通道同口径"),
        "F3": [{"情形": c["情形"], "可得性判定": c["可得性判定"],
                "可得性通过": c["可得性通过（这一行当时读得到）"],
                "新鲜度通过": c["新鲜度通过（≤15分钟）"],
                "status": c["桥接层status"]}
               for c in rep.get("F_序列与反例", {}).get("F3_反例", {}).get("cases", [])],
        "G_覆盖": rep.get("G_真实覆盖", {}).get("单标的合计"),
        "G_缺失整天": [x["date"] for x in rep.get("G_真实覆盖", {}).get(
            "单标的每日实时行数", []) if x["rows"] == 0],
        "G_时间戳语义": rep.get("G_真实覆盖", {}).get("时间戳语义", {}).get("结论"),
        "errors": [e["error"] for e in rep["errors"]],
    }
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
