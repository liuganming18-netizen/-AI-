# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜共用：合成 1h 序列、已知箱体、隔离库、调用轨迹。

本模块**不含任何判定逻辑**，只做三件事：
  1. 造确定性的合成 1h 序列（每根数字写死，可逐根复算）；
  2. 提供「已知箱体」替身（`atr` / `low_l` / `high_h` 显式给定，不跑 `build_boxes`）；
  3. 给 `accum_early_entry` 的三个纯函数装**调用轨迹记录器**（只记不改值）。

纪律：
  · 行情源是合成数据，不读生产行情库、不写生产账本；
  · 观察库落在本窗 `runs/` 下，`ObserveStore.assert_isolated()` 会核一遍；
  · 记录器只包一层，调用原函数，返回原值（不改语义）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
W2 = HERE.parents[1]                      # evidence/ACCUM-EARLY-06-W2/01
PROJECT = HERE.parents[4]                 # 工程根（parents[0]=scripts, [1]=01, [2]=06-W2, [3]=evidence）
BACKEND = PROJECT / "框架" / "backend"
OUT = W2 / "out"
RUNS = W2 / "runs"
CONFIG_DIR = W2 / "config"

for _p in (str(PROJECT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

H = 3_600_000
M15 = 900_000
H4 = 14_400_000

import accum_early_entry as AEE          # noqa: E402


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ts(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


T0 = ts("2026-06-01T00:00:00Z")          # 合成序列起点（1h 第 0 根开盘）
SYMBOL = "W2SYNTHUSDT"


# ══════════════════════════════════════════════════════════════════════════
# 一、合成 1h 序列
# ══════════════════════════════════════════════════════════════════════════
def bar(open_ms: int, o, h, l, c, qv, tbq) -> dict:
    """一根 1h。`close_time` = 开盘 + 1h − 1ms（与真实交易所口径一致）。"""
    return {
        "open_time": int(open_ms), "close_time": int(open_ms) + H - 1,
        "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        "volume": 1000.0,
        "quote_volume": (None if qv is None else float(qv)),
        "taker_buy_quote_volume": (None if tbq is None else float(tbq)),
        "source": "synthetic_06w2",
        "available_at": None,          # 合成数据无可得时点证据 ⇒ 只能声称「计算不超前」
    }


#: 静默根：高 103.9 / 低 103.4 / 收 103.7 / 买占比 0.5 / 主动卖出额 500
QUIET = dict(o=103.6, h=103.9, l=103.4, c=103.7, qv=1000.0, tbq=500.0)

#: 扫低根：低 103.1（破 24 根参照 103.4，未破 0.5A 容差 102.9）/ 收 103.8（收回 +0.1A 以上）
SWEEP = dict(o=103.5, h=103.85, l=103.1, c=103.8, qv=1000.0)

#: 突破根：收 104.0 > 压制位 103.9（平顶 103.9 的摆动高点）/ 收阳
CONFIRM = dict(o=103.7, h=104.1, l=103.7, c=104.0, qv=1000.0)

#: 三档主动买量：通过(0.7) / 不通过(0.3) / 缺字段(None)
TBQ_PASS, TBQ_FAIL, TBQ_MISSING = 700.0, 300.0, None

SWEEP_I = 60          # 扫低根下标
CONFIRM_I = 61        # 突破根下标（A/B 用：紧接扫低根）
CONFIRM_FAR_I = 86    # 突破根下标（C 用：距扫低根 26 根，避开「扫低根落在基准窗口内」的干扰）
FORM_I = 50           # 箱体建档根下标（早于扫低根）
BOX = {"formation_ms": T0 + FORM_I * H + H - 1,
       "high_h": 110.0, "low_l": 100.0, "atr": 1.0,
       "width_atr": 10.0, "er": 0.0}


def series(sweep_tbq, confirm_tbq, *, confirm_i=CONFIRM_I, sweep_i=SWEEP_I,
           n_bars=None) -> list:
    """造一条 1h 序列：静默根 → 扫低根（下标 sweep_i）→ 突破根（下标 confirm_i）。"""
    n_bars = int(n_bars if n_bars is not None else confirm_i + 1)
    bars = []
    for i in range(n_bars):
        ms = T0 + i * H
        if i == sweep_i:
            b = dict(SWEEP, tbq=sweep_tbq)
        elif i == confirm_i:
            b = dict(CONFIRM, tbq=confirm_tbq)
        else:
            b = dict(QUIET)
        bars.append(bar(ms, **b))
    return bars


#: 三类用例（同一套期望，修前修后都用它判）
CASES = {
    "A_扫低承接通过_突破根不通过": dict(sweep_tbq=TBQ_PASS, confirm_tbq=TBQ_FAIL,
                                        confirm_i=CONFIRM_I,
                                        expect_candidate=True,
                                        expect_terminal=None),
    "B_扫低不通过_突破根通过": dict(sweep_tbq=TBQ_FAIL, confirm_tbq=TBQ_PASS,
                                    confirm_i=CONFIRM_I,
                                    expect_candidate=False,
                                    expect_terminal=None),
    "C_扫低根数据不足": dict(sweep_tbq=TBQ_MISSING, confirm_tbq=TBQ_PASS,
                             confirm_i=CONFIRM_FAR_I,
                             expect_candidate=False,
                             expect_terminal="DATA_INSUFFICIENT"),
}


# ══════════════════════════════════════════════════════════════════════════
# 二、合成 feed（只提供状态机需要的读口）
# ══════════════════════════════════════════════════════════════════════════
class SynthFeed:
    """`at_or_after("15m")` + `load()` + `_cache`（`_bar_source` 用）。"""

    def __init__(self, bars_1h, bars_4h=None):
        self.bars_1h = list(bars_1h)
        self.bars_4h = list(bars_4h or [])
        self._cache = {("1h", SYMBOL): self.bars_1h,
                       ("4h", SYMBOL): self.bars_4h,
                       ("15m", SYMBOL): self._mk_15m()}

    def _mk_15m(self):
        """从第 0 根 1h 起铺 15m：价格 = 该小时静默价 103.7，开盘即成交参考。"""
        out = []
        end = T0 + (CONFIRM_FAR_I + 48) * H
        ms = T0
        while ms < end:
            out.append({"open_time": ms, "close_time": ms + M15 - 1,
                        "open": 103.7, "high": 103.75, "low": 103.65, "close": 103.7,
                        "volume": 100.0, "quote_volume": 1000.0,
                        "taker_buy_quote_volume": 500.0,
                        "source": "synthetic_06w2", "available_at": None})
            ms += M15
        return out

    def load(self, timeframe, symbol, start_ms, end_ms):
        return self._cache.get((timeframe, symbol), [])

    def gaps(self, timeframe, symbol):
        return {"synthetic": True, "continuous": True}

    def available_at_report(self, timeframe, symbol):
        return {"filled": 0, "total": len(self._cache.get((timeframe, symbol), []))}

    def at_or_after(self, timeframe, symbol, ts_ms, span_ms=0):
        for b in self._cache.get((timeframe, symbol)) or []:
            if int(b["open_time"]) >= int(ts_ms):
                return b
        return None

    def close(self):
        pass


# ══════════════════════════════════════════════════════════════════════════
# 三、隔离观察库
# ══════════════════════════════════════════════════════════════════════════
def fresh_store(tag: str):
    """每个用例一个独立 sqlite，落在本窗 `runs/` 下（不碰生产库）。"""
    RUNS.mkdir(parents=True, exist_ok=True)
    p = RUNS / f"observe_{tag}.sqlite3"
    if p.exists():
        p.unlink()
    st = AEE.open_store(p)
    st.assert_isolated()
    return st


# ══════════════════════════════════════════════════════════════════════════
# 四、调用轨迹记录器（只记不改）
# ══════════════════════════════════════════════════════════════════════════
class Trace:
    """记录 `_absorption` / `_resistance` / `_sweep_check` 每次调用的 bar 下标与时刻。

    这是「判断使用了哪一根 bar」的唯一硬证据：不看注释、不看自报，看调用参数。
    """

    def __init__(self):
        self.calls = []
        self.max_index = {"absorption": -1, "resistance": -1, "sweep_check": -1}
        self._orig = {}

    def install(self):
        self._orig["absorption"] = AEE._absorption
        self._orig["resistance"] = AEE._resistance
        self._orig["sweep_check"] = AEE._sweep_check
        AEE._absorption = self._wrap("absorption", AEE._absorption)
        AEE._resistance = self._wrap("resistance", AEE._resistance)
        AEE._sweep_check = self._wrap("sweep_check", AEE._sweep_check)
        return self

    def uninstall(self):
        for k, fn in self._orig.items():
            setattr(AEE, "_" + k, fn)
        return self

    def _wrap(self, name, fn):
        def inner(bars, i, *a, **kw):
            self.calls.append({
                "fn": name, "i": int(i),
                "bar_open_ms": int(bars[i]["open_time"]),
                "bar_close_ms": int(bars[i]["close_time"]),
                "bar_open_utc": iso(bars[i]["open_time"]),
                "bar_close_utc": iso(bars[i]["close_time"]),
                "n_bars_in_series": len(bars),
            })
            self.max_index[name] = max(self.max_index[name], int(i))
            return fn(bars, i, *a, **kw)
        inner.__name__ = fn.__name__ + "_traced"
        return inner

    def by_fn(self, name):
        return [c for c in self.calls if c["fn"] == name]


# ══════════════════════════════════════════════════════════════════════════
# 五、驱动状态机（不是单函数调用：走 SchemeObserver.on_bar）
# ══════════════════════════════════════════════════════════════════════════
def run_observer(case_name: str, bars, cfg, *, scheme="4a", box=None, tag=None):
    """逐根推进状态机，返回动作时间线 + 库内迁移记录 + 候选。

    **不直接调 `_absorption`**：只喂 `on_bar(bars, i, atr)`，让状态机自己决定
    什么时候读哪一根。
    """
    box = dict(box or BOX)
    store = fresh_store(tag or case_name.replace("/", "_"))
    feed = SynthFeed(bars)
    meta = AEE._scheme_identity()[scheme]
    obs = AEE.SchemeObserver(scheme, SYMBOL, box, cfg, store, feed,
                             strategy_id=meta["strategy_id"],
                             rule_version=meta["rule_version"])
    obs.open_event(evidence={"box": {k: box[k] for k in
                                     ("formation_ms", "high_h", "low_l", "atr")},
                             "data_kind": "synthetic_06w2"},
                   data_quality="ok")

    trace = Trace().install()
    timeline = []
    try:
        for i, b in enumerate(bars):
            acts = obs.on_bar(bars, i, float(box["atr"]))
            for a in acts:
                timeline.append({
                    "bar_i": i, "bar_close_ms": int(b["close_time"]),
                    "bar_close_utc": iso(b["close_time"]),
                    "kind": a.get("kind"), "state": a.get("state") or a.get("to"),
                    "reason": a.get("reason"),
                    "payload": a.get("payload") or {},
                })
            if obs.done:
                break
    finally:
        trace.uninstall()

    transitions = [dict(r) for r in store.con.execute(
        "SELECT from_state, to_state, at_ms, reason FROM observe_transition "
        "WHERE event_id=? ORDER BY rowid", (obs.event_id,))]
    cands = [dict(r) for r in store.con.execute(
        "SELECT candidate_id, confirm_close_ms, status, status_reason, "
        "conditions_json, signal_uid FROM accum05_candidate WHERE event_id=?",
        (obs.event_id,))]
    obs_cands = [dict(r) for r in store.con.execute(
        "SELECT candidate_id, created_ms, state, note FROM observe_candidate "
        "WHERE event_id=? ORDER BY seq_in_event", (obs.event_id,))]
    gaps = [dict(r) for r in store.con.execute(
        "SELECT field, reason, from_ms, to_ms FROM observe_data_gap WHERE event_id=?",
        (obs.event_id,))]
    ev = dict(store.con.execute(
        "SELECT state, closed_reason FROM observe_event WHERE event_id=?",
        (obs.event_id,)).fetchone())
    store.close()

    return {"case": case_name, "event_id": obs.event_id,
            "final_state": ev["state"], "closed_reason": ev["closed_reason"],
            "observer_state": obs.state, "done": obs.done,
            "n_candidates": len(obs_cands), "n_accum05_candidates": len(cands),
            "candidates": obs_cands, "accum05_candidates": cands,
            "timeline": timeline, "transitions": transitions, "gaps": gaps,
            "trace": trace.calls, "trace_max_index": dict(trace.max_index),
            "sweep_i": getattr(obs, "sweep_i", None),
            "sweep_bar_ms": getattr(obs, "sweep_bar_ms", None),
            "sweep_absorption": _jsonable(getattr(obs, "sweep_absorption", None)),
            "last_conditions": _jsonable(getattr(obs, "_last_conditions", None))}


def explain_ratio(bars, i, cfg) -> dict:
    """**独立重算**（不调用 `_absorption`）：这根 bar 的承接算术长什么样。

    用来「逐根解释」判定结果，同时充当反向对照：若引擎的结论与这里算的不一致，
    说明两边必有一处错，而不是「看起来对」。
    """
    sp = cfg["doc"]["shared"]["sell_pressure"]
    n = int(sp["n"])
    mult = float(sp["buy_ratio_mult"])
    sp_mult = sp["sell_pressure_mult"]
    cur = bars[i]

    def ratio(b):
        qv, tbq = b.get("quote_volume"), b.get("taker_buy_quote_volume")
        if qv in (None, 0) or tbq is None:
            return None, None
        return float(tbq) / float(qv), float(qv) - float(tbq)

    r, sell = ratio(cur)
    seg = bars[i - n:i] if i - n >= 0 else []
    rs, ss = [], []
    for b in seg:
        rr, s2 = ratio(b)
        if rr is not None:
            rs.append(rr)
            ss.append(s2)
    med = (sorted(rs)[len(rs) // 2] if len(rs) % 2
           else (sorted(rs)[len(rs) // 2 - 1] + sorted(rs)[len(rs) // 2]) / 2) if rs else None
    med_s = (sorted(ss)[len(ss) // 2] if len(ss) % 2
             else (sorted(ss)[len(ss) // 2 - 1] + sorted(ss)[len(ss) // 2]) / 2) if ss else None
    hi, lo, cl = float(cur["high"]), float(cur["low"]), float(cur["close"])
    return {
        "bar_i": i, "bar_close_utc": iso(cur["close_time"]),
        "open": cur["open"], "high": hi, "low": lo, "close": cl,
        "quote_volume": cur["quote_volume"],
        "taker_buy_quote_volume": cur["taker_buy_quote_volume"],
        "buy_ratio": r, "baseline_n": n, "baseline_filled": len(rs),
        "baseline_median_ratio": med,
        "buy_ratio_threshold": (None if med is None else med * mult),
        "buy_ratio_ok": (None if (r is None or med is None) else r >= med * mult),
        "sell_amount": sell,
        "baseline_median_sell": med_s,
        "sell_threshold": (None if med_s is None else med_s * float(sp_mult)),
        "sell_ok": (None if (sell is None or med_s is None)
                    else sell <= med_s * float(sp_mult)),
        "upper_half_threshold": lo + 0.5 * (hi - lo),
        "upper_half_ok": cl >= lo + 0.5 * (hi - lo),
    }


def _jsonable(o):
    if o is None:
        return None
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    return o


def conds_of(res: dict) -> dict:
    """把时间线/条件表压成「逐条条件名 → 结果」的字典，便于对撞。"""
    out = {}
    for c in (res.get("last_conditions") or []):
        if isinstance(c, dict) and "name" in c:
            out[c["name"]] = c.get("result")
    return out


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                 encoding="utf-8")
    print(json.dumps({"written": str(p), "bytes": p.stat().st_size}, ensure_ascii=False))
    return p


def load_out(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_fixture_config():
    """本窗测试配置（由 05-B fixture 复制，只改 `approval_ref` 一句）。"""
    os.environ["ACCUM05_FIXTURE_OK"] = "1"          # fixture 档的显式开关
    os.environ["ACCUM05_CONFIG"] = str(CONFIG_DIR / "fixture_rules_06w2.json")
    return AEE.load_config(CONFIG_DIR / "fixture_rules_06w2.json")


def sha256_file(p) -> dict:
    import hashlib
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    h = hashlib.sha256()
    n = 0
    with p.open("rb") as fh:
        while True:
            b = fh.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
            n += len(b)
    return {"path": str(p), "exists": True, "bytes": p.stat().st_size,
            "sha256": h.hexdigest(), "hashed_bytes": n}
