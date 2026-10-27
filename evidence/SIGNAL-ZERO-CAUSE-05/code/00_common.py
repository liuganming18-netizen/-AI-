# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜证据脚本公共件（只读；不写生产库、不下单、不联网）。

本单口径（全部脚本共用，禁止各脚本自定义）：
  · 统一截止时刻 `upto_ms` = 行情库里**最新一根已收盘 1h 的 close_time**；
    所有逐币结论都在这个时刻上取，保证「同一份行情、相同截止时间」。
  · 冻结参数一律来自 `make_ft_low4h_config()`（生效值），不用 dataclass 默认值。
  · 窗口 = `atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours` = 1160h（与生产同源）。
  · 生产库只读；本目录 `out/` 写证据。
"""
from __future__ import annotations

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

#: 本单判据用到的阈值（全部来自冻结配置，脚本内不得另立常数）
SPEC_RULE_LINE = 17          # 「ATR…至少预热 200 根完整 4h K 线」
SPEC_BOX_LINE = 32           # 「每个 4h 收盘检查最近 42 根 bar…」
SPEC_SPLIT_LINE = 106        # 「每段允许读取之前 200 根 4h 数据预热特征」


def utc(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def cfg():
    """生效的冻结配置（FT-LOW4H）。**不是** EventTrendConfig 的 dataclass 默认值。"""
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
    """库里最新一根已收盘 1h 的 close_time —— 全单统一截止时刻。"""
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


def load_hist(symbols, upto_ms, read_days=180, log=None):
    """读合并历史（冷层 parquet + 热库）。返回 {sym: {"4h": [...], "1h": [...]}}。"""
    prov = history_provider(log=log)
    read_start = int(upto_ms) - read_days * DAY
    win_start = int(upto_ms) - warmup_ms()
    h4, st4 = prov.load(symbols, ("4h",), read_start, int(upto_ms), keep_from_ms=read_start)
    h1, st1 = prov.load(symbols, ("1h",), read_start, int(upto_ms), keep_from_ms=win_start)
    out = {}
    for s in symbols:
        out[s] = {"4h": (h4.get(s) or {}).get("4h") or [],
                  "1h": (h1.get(s) or {}).get("1h") or []}
    return out, {"4h": st4, "1h": st1}


def split_window(bars4_all, upto_ms):
    """把 4h 序列切成（窗口前 pre / 窗口内 win），窗口起点 = upto_ms - 1160h。"""
    win_start = int(upto_ms) - warmup_ms()
    win = [b for b in bars4_all
           if int(b["open_time"]) >= win_start and int(b["close_time"]) <= int(upto_ms)]
    pre = [b for b in bars4_all
           if int(b["open_time"]) < win_start and int(b["close_time"]) <= int(upto_ms)]
    return pre, win, win_start


# ── 引擎终态快照（跨运行可比；身份只认这些字段）─────────────────────────────
def engine_state(eng) -> dict:
    sm = eng.sm
    ev = sm.active_event
    dq = eng.data_quality or {}
    st = {
        "state": ev.current_state.value if ev else "IDLE",
        "event_id": ev.event_id if ev else None,
        "box_high": ev.box.high_h if ev else None,
        "box_low": ev.box.low_l if ev else None,
        "box_atr": ev.box.atr_a if ev else None,
        "box_er": ev.box.efficiency_ratio_er if ev else None,
        "box_width_atr": ev.box.box_width_atr if ev else None,
        "box_formed_ms": ev.box.formation_time_ms if ev else None,
        "sweep_time_ms": ev.sweep_time_ms if ev else None,
        "sweep_low": ev.sweep_low_s if ev else None,
        "sweep_volume_vs": ev.sweep_volume_vs if ev else None,
        "retest_time_ms": ev.retest_time_ms if ev else None,
        "retest_low": ev.retest_low_lr if ev else None,
        "cooling_until_ms": sm.cooling_until_ms,
        "atr": eng.last_atr,
        "atr_bars_seen": eng.atr_bars_seen,
        "box_gate_bars": eng.box_gate_bars,
        "last_box_skipped_warmup_ms": eng.last_box_skipped_warmup_ms,
        "processed_1h_max": (max(eng.processed_1h_opens) if eng.processed_1h_opens else None),
        "processed_4h_max": (max(eng.processed_4h_opens) if eng.processed_4h_opens else None),
        "n_transitions": len(sm.transitions),
        "n_completed": len(sm.completed_events),
        "n_candidates": len(sm.candidates),
        "data_invalid": bool(dq.get("invalid")),
        "data_quality": dict(dq),
    }
    # 阶段进入时间 = 活跃事件最后一次迁移的触发时刻（没有任何迁移时为建档时刻）
    if ev and ev.history:
        st["stage_entered_ms"] = int(ev.history[-1].trigger_bar_close_ms)
        st["stage_entered_reason"] = ev.history[-1].reason
    else:
        st["stage_entered_ms"] = None
        st["stage_entered_reason"] = None
    # 阶段内已用时长（用于判断「超时」还剩多少）
    if ev:
        base = {"BOX": ev.created_at_ms, "SWEEP": ev.sweep_time_ms,
                "RETEST": ev.retest_time_ms}.get(st["state"], ev.created_at_ms)
        st["stage_elapsed_h"] = round((int(eng.last_cycle_ms or 0) - int(base)) / H1, 2) \
            if base and eng.last_cycle_ms else None
    else:
        st["stage_elapsed_h"] = None
    return st


def transitions_of(eng):
    """状态机迁移流水（逐条，含时刻/原因/指标快照），用于回溯阶段进入与淘汰原因。"""
    out = []
    for t in eng.sm.transitions:
        out.append({
            "transition_id": t.transition_id,
            "event_id": t.event_id,
            "from_state": t.from_state,
            "to_state": t.to_state,
            "trigger_bar_close_ms": int(t.trigger_bar_close_ms),
            "reason": t.reason,
            "metrics": t.metrics_snapshot,
        })
    return out


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
