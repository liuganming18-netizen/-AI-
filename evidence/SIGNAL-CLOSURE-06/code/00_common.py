# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜证据脚本公共件（只读；不写生产库、不下单、不联网）。

本单口径（全部脚本共用，禁止各脚本自定义）：
  · 统一截止时刻 `upto_ms` = 行情库里**最新一根已收盘 1h 的 close_time**。
  · 冻结参数一律来自 `make_ft_low4h_config()`（生效值），不用 dataclass 默认值。
  · 窗口 = `atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours` = 1160h。
  · **「重启」的定义（本单核心）**：同一实例先 bootstrap 到 `T0`、连续跑到 `Tr`，
    重启后按 `[Tr-1160h, Tr]` **重建**再跑到 `T1`。与 ZERO-CAUSE-05 的 S1/S3 不同 ——
    那两者是「不同时刻各自全新启动」，历史长度不同，不是生产重启。
  · 生产库只读；本目录 `out/` 写证据。
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[3]
BACKEND = BASE / "框架" / "backend"
OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("no_proxy", "*")
for _p in (str(BACKEND), str(BASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

H1 = 3_600_000
H4 = 4 * H1
DAY = 86_400_000


def utc(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def parse_utc_ms(s: str, end_of_second: bool = True) -> int:
    """'YYYY-MM-DDTHH:MM:SSZ' → ms。收盘时刻是 :59:59.999，故默认补 999。"""
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1000) + (999 if end_of_second else 0)


def now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def cfg():
    from 策略.WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config
    return make_ft_low4h_config()


def warmup_ms() -> int:
    c = cfg()
    return int((c.atr_warmup_bars * 4 + c.breakout_max_total_hours + c.cooling_hours) * H1)


def universe():
    import live_pool as LP
    syms, meta = LP.scope_symbols()
    return list(syms), meta


def market_conn():
    import live_store as L
    return L.market_conn()


def last_closed_1h_close_ms():
    con = market_conn()
    try:
        r = con.execute(
            "SELECT MAX(close_time) FROM candles WHERE exchange='binance' AND market='um' "
            "AND timeframe='1h' AND is_closed=1").fetchone()
        return int(r[0]) if r and r[0] is not None else None
    finally:
        con.close()


def history_provider(log=None):
    import history_loader as HL
    import live_store as L
    import project_paths as PP
    return HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=log or (lambda *a: None))


def load_hist(symbols, upto_ms, read_days=180, log=None, keep_from_ms=None):
    """读合并历史（冷层 parquet + 热库）。返回 {sym: {"4h": [...], "1h": [...]}}。

    `keep_from_ms` 是 **1h 的保留起点**，必须与「4h 的事件地板」一致 ——
    否则重放深度一旦超过声明窗口，那段更早的 4h 箱体会因为**没有 1h 数据**
    而永远扫不了低/回踩/突破，状态机卡在 BOX。
    （本单踩过：默认取声明窗口 1160h，导致离线与生产 7/9 币不一致。）
    """
    prov = history_provider(log=log)
    read_start = int(upto_ms) - read_days * DAY
    win_start = int(upto_ms) - warmup_ms()
    keep1 = int(keep_from_ms) if keep_from_ms is not None else win_start
    h4, st4 = prov.load(symbols, ("4h",), read_start, int(upto_ms), keep_from_ms=read_start)
    h1, st1 = prov.load(symbols, ("1h",), read_start, int(upto_ms), keep_from_ms=keep1)
    out = {}
    for s in symbols:
        out[s] = {"4h": (h4.get(s) or {}).get("4h") or [],
                  "1h": (h1.get(s) or {}).get("1h") or []}
    return out, {"4h": st4, "1h": st1}


def state_replay_hours() -> int:
    """生产用的状态重建深度（与 `MultiSymbolSignalEngine.__init__` 同一环境变量）。"""
    try:
        return max(0, int(os.environ.get("WX_STATE_REPLAY_HOURS", "2880")))
    except ValueError:
        return 2880


def replay_ms_from_env() -> int:
    return max(warmup_ms(), state_replay_hours() * H1)


# ── 生产同源会话（SIGNAL-CLOSURE-06 核心件）─────────────────────────────────
# 纪律：**不重写状态机**。所有结论都跑在生产的 `MultiSymbolSignalEngine` 上
# （同一个 `prepare_history` / 同一个 `bootstrap` / 同一个 `step`），
# 只把两个外部依赖换成内存件：
#   · `history_provider` → 内存合并源（接口同 `history_loader.MergedHistory`）
#   · `watcher`          → 内存全池行情（接口同 `PoolMarketWatcher`）
# 这样「被测版本 = 真实运行版本」是**结构上成立**的，不靠人工对齐。
class MemProvider:
    """`MergedHistory.load` 的内存替身：按 `close_time <= upto` 截断。"""

    def __init__(self, hist):
        self.hist = hist
        self.upto_ms = None

    def set_upto(self, ms):
        self.upto_ms = int(ms)

    def load(self, symbols, tfs, start_ms, end_ms, keep_from_ms=None):
        end = int(self.upto_ms if self.upto_ms is not None else end_ms)
        keep = int(keep_from_ms if keep_from_ms is not None else start_ms)
        out, stats = {}, {}
        for s in symbols:
            d = {}
            for tf in tfs:
                bars = (self.hist.get(s) or {}).get(tf) or []
                d[tf] = [b for b in bars
                         if int(b["open_time"]) >= keep and int(b["close_time"]) <= end]
                stats.setdefault(tf, {})[s] = len(d[tf])
            out[s] = d
        return out, {"data_source": "memory", "per_symbol": stats}


class MemPoolWatcher:
    """`PoolMarketWatcher` 的内存替身；`now_ms` 模拟「那一刻的库」。"""

    def __init__(self, hist, symbols=None):
        self.hist = hist
        self.symbols = set(symbols) if symbols else None
        self.now_ms = None
        self._idx = {}
        for s, d in hist.items():
            for tf in ("4h", "1h"):
                self._idx[(s, tf)] = sorted(
                    d.get(tf) or [], key=lambda b: int(b["open_time"]))

    def set_now(self, ms):
        self.now_ms = int(ms)

    def _bars(self, s, tf):
        return self._idx.get((s, tf)) or []

    def global_last_open(self, timeframe):
        mx = None
        for s in (self.symbols or self.hist.keys()):
            for b in self._bars(s, timeframe):
                if self.now_ms is not None and int(b["close_time"]) > self.now_ms:
                    break
                o = int(b["open_time"])
                if mx is None or o > mx:
                    mx = o
        return mx

    def latest_open_by_symbol(self, timeframe):
        out = {}
        for s in (self.symbols or self.hist.keys()):
            mx = None
            for b in self._bars(s, timeframe):
                if self.now_ms is not None and int(b["close_time"]) > self.now_ms:
                    break
                o = int(b["open_time"])
                if mx is None or o > mx:
                    mx = o
            if mx is not None:
                out[s] = mx
        return out

    def get_closed_bars(self, symbol, timeframe, start_open_ms, end_open_ms):
        return [b for b in self._bars(symbol, timeframe)
                if int(b["open_time"]) >= int(start_open_ms)
                and int(b["close_time"]) <= int(end_open_ms)]


def prod_session(syms, hist, init_ms, end_ms, replay_hours=None, cfg_=None,
                 log=None, advance="1h", hook=None, anchor_ms=None,
                 force_step=False):
    """生产的 `MultiSymbolSignalEngine`：bootstrap@init_ms，再增量推进到 end_ms。

    `replay_hours` 只改「重建状态时往回看多远」（= `replay_ms`），不改任何触发条件。
    `anchor_ms` 若给出，则按**绝对锚点**重建（`resolve_replay_floor` 生效），
    这时重放地板与 `replay_hours` 无关 —— 生产就是这么跑的（锚点存在 live 库）。
    `advance`：
      · `"1h"`  —— 按 1h 收盘逐根 `step()`（生产的真实节奏）；
      · `"end"` —— 只在 end_ms 调一次 `step()`（最快，只取终态时用）。
    `hook(eng, t_ms)`：每次 `step()` 之后回调（用于逐根抓状态指纹）。
    """
    import unified_runtime as UR
    cfg_ = cfg_ or cfg()
    rh = state_replay_hours() if replay_hours is None else int(replay_hours)
    prov = MemProvider(hist)
    w = MemPoolWatcher(hist, syms)
    eng = UR.MultiSymbolSignalEngine(list(syms), enable_oi_filter=False,
                                     config=cfg_, history_provider=prov)
    eng.replay_ms = max(eng.warmup_ms, rh * H1)
    eng.state_anchor_ms = int(anchor_ms) if anchor_ms else None
    eng.history_floor_ms = eng.resolve_replay_floor(int(init_ms))
    eng.watcher = w
    prov.set_upto(init_ms)
    w.set_now(init_ms)
    eng.prepare_history(int(init_ms), log=log or (lambda *a: None))
    eng.bootstrap(int(init_ms), log=log or (lambda *a: None))
    if int(end_ms) > int(init_ms):
        if advance == "1h":
            closes = set()
            for s in syms:
                for b in hist.get(s, {}).get("1h") or []:
                    c = int(b["close_time"])
                    if int(init_ms) < c <= int(end_ms):
                        closes.add(c)
            for c in sorted(closes):
                w.set_now(c)
                eng.step(c, log=None)
                if hook is not None:
                    hook(eng, c)
        w.set_now(int(end_ms))
        eng.step(int(end_ms), log=None)
        if hook is not None:
            hook(eng, int(end_ms))
    elif force_step:
        # **`init == end` 必须补一次 `step(end_ms)`**（SIGNAL-CLOSURE-06 实测更正）：
        # `bootstrap` 只按 **4h 收盘**驱动 `process_market`，最后一根 4h 之后
        # 已经闭合的那些 1h 从来没被喂进状态机。生产 `run_forever` 每轮都会
        # `step(now)`，所以「现场重建」之后必然再 step 一次；回放要等价，
        # 就必须显式补这一下。
        # 不补的后果：1h 处理根数会差 3 根（实测 A=2874 / B=2871），
        # 并且会把「重建 vs 连续」的差异虚报成状态差异。
        w.set_now(int(end_ms))
        eng.step(int(end_ms), log=None)
        if hook is not None:
            hook(eng, int(end_ms))
    return eng


def pool_snapshot(eng) -> dict:
    return {s: snapshot(e) for s, e in eng.engines.items()}


def split_window(bars4_all, upto_ms):
    win_start = int(upto_ms) - warmup_ms()
    win = [b for b in bars4_all
           if int(b["open_time"]) >= win_start and int(b["close_time"]) <= int(upto_ms)]
    pre = [b for b in bars4_all
           if int(b["open_time"]) < win_start and int(b["close_time"]) <= int(upto_ms)]
    return pre, win, win_start


# ── 回放件 ────────────────────────────────────────────────────────────────
class FastWatcher:
    """只读的 K 线供给器（替代生产 watcher，接口一致）。"""

    def __init__(self, b4, b1):
        self.b4, self.b1 = b4, b1
        self._o4 = [int(b["open_time"]) for b in b4]
        self._c4 = [int(b["close_time"]) for b in b4]
        self._o1 = [int(b["open_time"]) for b in b1]
        self._c1 = [int(b["close_time"]) for b in b1]

    def get_closed_bars(self, timeframe, start_open_ms, end_open_ms):
        if timeframe == "4h":
            src, o, c = self.b4, self._o4, self._c4
        else:
            src, o, c = self.b1, self._o1, self._c1
        i = bisect.bisect_left(o, int(start_open_ms))
        j = bisect.bisect_right(c, int(end_open_ms))
        return src[i:j] if j > i else []


def run_session(sym, b4_all, b1_all, init_ms, end_ms, cfg_=None, win_start_override=None,
                trace=None):
    """一次「bootstrap@init_ms + 增量推进到 end_ms」。

    `win_start_override` 用于严格同长度对照。
    `trace` 若给一个 dict，则把每根 4h 收盘后的状态指纹写进去（用于找最早分歧）。
    """
    import unified_runtime as UR
    cfg_ = cfg_ or cfg()
    eng = UR.RealSignalEngine(symbol=sym, enable_oi_filter=False, config=cfg_)
    win_start = (int(win_start_override) if win_start_override is not None
                 else int(init_ms) - warmup_ms())
    eng.history_floor_ms = win_start
    pre = [b for b in b4_all
           if int(b["open_time"]) < win_start and int(b["close_time"]) <= int(init_ms)]
    win = [b for b in b4_all
           if int(b["open_time"]) >= win_start and int(b["close_time"]) <= int(init_ms)]
    for b in pre:
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    w = FastWatcher(b4_all, b1_all)
    for bar in win:
        eng.process_market(int(bar["close_time"]), w)
        if trace is not None:
            trace[int(bar["close_time"])] = fingerprint(eng)
    if int(end_ms) > int(init_ms):
        nxt = [b for b in b4_all if int(init_ms) < int(b["close_time"]) <= int(end_ms)]
        for bar in nxt:
            eng.process_market(int(bar["close_time"]), w)
            if trace is not None:
                trace[int(bar["close_time"])] = fingerprint(eng)
    eng.process_market(int(end_ms), w)
    if trace is not None:
        trace[("tail", int(end_ms))] = fingerprint(eng)
    return eng


def fingerprint(eng) -> dict:
    """逐根 4h 的状态指纹 —— 用来定位「最早从哪一根开始不一样」。"""
    ev = eng.sm.active_event
    return {
        "state": ev.current_state.value if ev else "IDLE",
        "event_id": ev.event_id if ev else None,
        "box_formed_ms": ev.box.formation_time_ms if ev else None,
        "box_high": ev.box.high_h if ev else None,
        "box_low": ev.box.low_l if ev else None,
        "cooling_until_ms": eng.sm.cooling_until_ms,
        "atr": eng.last_atr,
        "n_transitions": len(eng.sm.transitions),
        "n_candidates": len(eng.sm.candidates),
        "last_skip_ms": eng.last_box_skipped_warmup_ms,
    }


def snapshot(eng) -> dict:
    """终态快照（跨运行可比；身份只认这些字段）。"""
    ev = eng.sm.active_event
    s = {
        "state": ev.current_state.value if ev else "IDLE",
        "event_id": ev.event_id if ev else None,
        "box_formed_ms": ev.box.formation_time_ms if ev else None,
        "box_high": ev.box.high_h if ev else None,
        "box_low": ev.box.low_l if ev else None,
        "box_atr": ev.box.atr_a if ev else None,
        "box_width_atr": ev.box.box_width_atr if ev else None,
        "box_er": ev.box.efficiency_ratio_er if ev else None,
        "sweep_time_ms": ev.sweep_time_ms if ev else None,
        "sweep_low": ev.sweep_low_s if ev else None,
        "sweep_volume_vs": ev.sweep_volume_vs if ev else None,
        "retest_time_ms": ev.retest_time_ms if ev else None,
        "retest_low": ev.retest_low_lr if ev else None,
        "cooling_until_ms": eng.sm.cooling_until_ms,
        "atr": eng.last_atr,
        "atr_bars_seen": eng.atr_bars_seen,
        "n_transitions": len(eng.sm.transitions),
        "n_completed": len(eng.sm.completed_events),
        "n_candidates": len(eng.sm.candidates),
        "candidate_ids": [c.candidate_id for c in eng.sm.candidates],
        "candidate_times": [int(c.snapshot_time_ms) for c in eng.sm.candidates],
        "completed_event_ids": [e.event_id for e in eng.sm.completed_events],
        "last_terminal_reason": ([t.reason for t in eng.sm.transitions
                                  if t.to_state in ("INVALIDATED", "EXPIRED",
                                                    "VOLUME_REJECTED", "OI_REJECTED",
                                                    "OI_UNAVAILABLE",
                                                    "MISSED_WITHOUT_RETEST")] or [None])[-1],
    }
    return s


def diff_key(s: dict):
    """「有效状态」差异的判定键：阶段 + 箱体身份 + 关键结构时刻 + 候选身份。"""
    return (s["state"], s["box_formed_ms"], s["box_high"], s["box_low"],
            s["sweep_time_ms"], s["retest_time_ms"], tuple(s["candidate_ids"]))


def box_metrics(seg, atr):
    """与生产同口径的箱体度量（宽度 / ER），用于解释「这根为什么建档/没建档」。"""
    import numpy as np
    from 策略.WyckoffMaster.event_trend_v0_1.features import compute_efficiency_ratio
    H = max(float(x["high"]) for x in seg)
    L = min(float(x["low"]) for x in seg)
    er = compute_efficiency_ratio(np.array([float(x["close"]) for x in seg]))
    return {"high_h": H, "low_l": L, "atr_a": atr,
            "width_atr": (H - L) / atr if atr else None, "er": er}


def dump(name: str, obj) -> Path:
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                 encoding="utf-8")
    print(f"[out] {p.name} ({p.stat().st_size:,} B)")
    return p


def dump_csv(name: str, rows, fields=None) -> Path:
    import csv
    p = OUT / name
    if not rows:
        p.write_text("", encoding="utf-8")
        print(f"[out] {p.name} (0 行)")
        return p
    fields = fields or list(rows[0].keys())
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[out] {p.name} ({len(rows)} 行)")
    return p


def api(path: str, timeout: float = 20.0):
    """读本机 API（必须绕过代理：本机有 HTTP_PROXY 而无 no_proxy）。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(f"http://127.0.0.1:8787{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def sha256_file(p) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def explain_zero_signal(eng, syms, upto_ms: int) -> list:
    """零信号逐币归因（工单第 4 条）：每个币当前卡在哪一关、为什么。

    只读引擎终态，不改任何状态。`why` 按阶段给，措辞与状态机实际迁移原因对齐，
    不写「大概/可能」——每一条都能回到该币的 `state / box / sweep / retest` 字段。
    """
    out = []
    for s in syms:
        e = eng.engines.get(s)
        if e is None:
            out.append({"symbol": s, "state": "SKIPPED",
                        "why": (getattr(eng, "skipped_reasons", {}) or {}).get(s, "no_engine")})
            continue
        d = snapshot(e)
        why = ""
        if d["state"] == "IDLE":
            cool = d.get("cooling_until_ms") or 0
            if cool and cool > int(upto_ms):
                why = f"冷却中，至 {utc(cool)}"
            else:
                tr = d.get("last_terminal_reason")
                why = (f"无活跃事件：上一事件以「{tr}」终止，此后无新箱体达标"
                       if tr else "无活跃事件：窗口内无新箱体达标（宽度/ER/暖机门槛）")
        elif d["state"] == "BOX":
            why = "箱体已建档，尚未出现「扫低」"
        elif d["state"] == "SWEEP":
            why = "已扫低，尚未「回踩」确认"
        elif d["state"] == "RETEST":
            why = "已回踩，尚未「突破」收盘确认"
        elif d["state"] == "CANDIDATE":
            why = "已出候选"
        else:
            why = f"阶段 {d['state']}"
        out.append({
            "symbol": s, "state": d["state"], "why": why,
            "event_id": d.get("event_id"),
            "box_utc": utc(d.get("box_formed_ms")),
            "box_width_atr": d.get("box_width_atr"), "box_er": d.get("box_er"),
            "atr": d.get("atr"), "atr_bars_seen": d.get("atr_bars_seen"),
            "cooling_until_utc": utc(d.get("cooling_until_ms")),
            "n_candidates": d.get("n_candidates"),
            "n_completed": d.get("n_completed"),
            "last_terminal_reason": d.get("last_terminal_reason"),
            "sweep_utc": utc(d.get("sweep_time_ms")),
            "retest_utc": utc(d.get("retest_time_ms")),
        })
    return out
