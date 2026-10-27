# -*- coding: utf-8 -*-
"""修复验证：信号落库 -> 界面可见；扫描限定交易所/市场；暂停后可重试。

只写临时目录（PAPER_LEDGER_DIR / LIVE_DIR / MARKET_DB 全部指向 temp），
不碰生产 live 库、不碰生产账本、不碰生产行情库。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="rt_fix_"))
LIVE_DIR = TMP / "live"
LEDGER = TMP / "paper"
MARKET = TMP / "market.sqlite3"
GATE = TMP / "gate.json"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
LEDGER.mkdir(parents=True, exist_ok=True)

os.environ.update({
    "LIVE_DIR": str(LIVE_DIR),
    "LIVE_DB": str(LIVE_DIR / "live_signals.sqlite3"),
    "PAPER_LEDGER_DIR": str(LEDGER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "PAPER_GATE_FILE": str(GATE),
    "MARKET_DB": str(MARKET),
    "PAPER_LOCK_DIR": str(TMP / "locks"),
})

# 临时行情库（含 candles + candle_versions，够 open_position 的只读查询不报错）
con = sqlite3.connect(MARKET)
con.execute("CREATE TABLE candles(exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,"
            "open_time INTEGER, close_time INTEGER, open TEXT, high TEXT, low TEXT, close TEXT,"
            "volume TEXT, quote_volume TEXT, is_closed INTEGER)")
con.execute("CREATE TABLE candle_versions(exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,"
            "open_time INTEGER, close_time INTEGER, open TEXT, high TEXT, low TEXT, close TEXT,"
            "volume TEXT, quote_volume TEXT, trade_count INTEGER, taker_buy_volume TEXT,"
            "taker_buy_quote_volume TEXT, source_id INTEGER, available_at INTEGER, is_closed INTEGER)")
con.commit()
con.close()

import live_store as L  # noqa: E402
L.ensure_live_schema()

NOW = int(time.time() * 1000)
T0 = NOW - 3 * 3600 * 1000
L.set_run("live_start_ms", T0)
L.set_run("paper_epoch_ms", T0)

# 预建四臂账本（隔离建档），否则 live_api 的 ATTACH 会引用不存在的 paper_order 表
for _arm in L.ARMS:
    L.paper_conn(_arm).close()

from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot  # noqa: E402
import unified_runtime as UR  # noqa: E402
import paper_engine as PE  # noqa: E402

out = {}


def make_cand(sig_ms: int, cid: str, symbol: str = "BTCUSDT") -> CandidateSnapshot:
    return CandidateSnapshot.create(
        candidate_id=cid, event_id=f"evt_{cid}", strategy_version="v0.1",
        symbol=symbol, venue="binance",
        snapshot_time_ms=sig_ms, available_at_ms=sig_ms,
        box_high_h=60000.0, box_low_l=58000.0, box_atr_a=500.0, box_er=0.35,
        sweep_low_s=58100.0, retest_low_lr=58300.0,
        breakout_close=60100.0, breakout_volume=1200.0, v20_median_volume=1000.0,
        volume_ratio=1.2, oi_breakout=1.0, oi_6h_ago=1.0, oi_growth_6h=0.0,
        initial_stop_price=58250.0, initial_risk_r=1850.0,
        max_entry_price=60350.0, min_entry_price=60000.0,
        execution_earliest_ms=sig_ms + 900_000,
    )


router = UR.SignalPersistenceAndRouter(arm="3a")

# ── V1 正常落库：payload 必须带事件角色，界面口径必须能看到 ───────────────────
SIG = NOW - 60_000
st1 = router.persist_candidates([make_cand(SIG, f"cand_v1_{SIG}")], origin="live", now_ms=NOW)
import live_api  # noqa: E402

res = live_api.live_signals({})
item = (res.get("items") or [{}])[0]
out["V1_落库与可见"] = {
    "persist_stats": st1,
    "api_total": res.get("total"),
    "api_items": len(res.get("items") or []),
    "item_role": item.get("role"),
    "item_arm": item.get("arm"),
    "item_strategy_key": item.get("strategy_key"),
    "item_executable_state": item.get("executable_state"),
}

# ── V2 旧行无角色：再次落库必须补上 payload.role（原 INSERT OR IGNORE 会永久丢）──
old_uid = f"cand_v2_{SIG}"
c = sqlite3.connect(str(L.LIVE_DB))
c.execute("INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
          "strategy_key,direction,event_time,confirmed_at,available_at,display_state,"
          "executable_state,payload_json,first_seen_at,updated_at) "
          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (old_uid, "wyckoff_v0_1", "live", "binance", "um", "BTCUSDT", "1h",
           "3a", "long", SIG, SIG, SIG, "displayable", "enterable",
           json.dumps({"arm": "3a"}), NOW, NOW))     # 注意：payload 无 role
c.commit()
c.close()
before = live_api.live_signals({}).get("total")
st2 = router.persist_candidates([make_cand(SIG, old_uid)], origin="live", now_ms=NOW)
after_res = live_api.live_signals({})
c = sqlite3.connect(str(L.LIVE_DB))
c.row_factory = sqlite3.Row
row = c.execute("SELECT payload_json FROM live_signal WHERE signal_uid=?", (old_uid,)).fetchone()
c.close()
out["V2_旧行补角色"] = {
    "persist_stats": st2,
    "api_total_before": before,
    "api_total_after": after_res.get("total"),
    "payload_role_after": json.loads(row["payload_json"]).get("role"),
}

# ── V3 扫描限定：混入其它交易所/市场的可执行行，不得被路由 ────────────────────
c = sqlite3.connect(str(L.LIVE_DB))
for uid, exch, mkt in (("cand_v3_okx", "okx", "swap"), ("cand_v3_spot", "binance", "spot")):
    c.execute("INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
              "strategy_key,direction,event_time,confirmed_at,available_at,display_state,"
              "executable_state,payload_json,first_seen_at,updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (uid, "wyckoff_v0_1", "live", exch, mkt, "ETHUSDT", "1h", "3a", "long",
               SIG, SIG, SIG, "displayable", "enterable",
               json.dumps({"role": "enterable_arm_entry", "arm": "3a"}), NOW, NOW))
c.commit()
c.close()

scan = router._scan_actionable(L.live_conn(ro=True), origin="live")
out["V3_扫描限定"] = {
    "scanned_uids": sorted(r["signal_uid"] for r in scan),
    "cross_venue_excluded": all(r["exchange"] == "binance" and r["market"] == "um" for r in scan),
}

# ── V4 闸门暂停：只报积压、不落任何状态；恢复后可重新扫描 ─────────────────────
GATE.write_text(json.dumps({"allow_new_entries": False, "reason": "probe_paused"}), encoding="utf-8")
r_paused = router.route_pending_orders(NOW, origin="live")


def _counts():
    cc = sqlite3.connect(str(L.LEDGER_DIR / "paper_3a.sqlite3"))
    try:
        return {
            "pending": cc.execute("SELECT COUNT(*) FROM paper_pending").fetchone()[0],
            "processed": cc.execute("SELECT COUNT(*) FROM paper_processed").fetchone()[0],
            "orders": cc.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0],
            "blocked": cc.execute("SELECT COUNT(*) FROM paper_blocked").fetchone()[0],
        }
    finally:
        cc.close()


out["V4_暂停期"] = {"route_result": r_paused, "ledger_counts": _counts()}

GATE.write_text(json.dumps({"allow_new_entries": True, "reason": "probe_resume"}), encoding="utf-8")
r_resumed = router.route_pending_orders(NOW, origin="live")
out["V5_恢复后"] = {"route_result": r_resumed, "ledger_counts": _counts()}

# ── V6 再跑一轮：已挂单/已处理的不重复登记 ───────────────────────────────────
r_again = router.route_pending_orders(NOW + 1000, origin="live")
out["V6_重复轮"] = {"route_result": r_again, "ledger_counts": _counts()}

# ── V7 可重试拒单（行情库不可用）不得被永久拉黑 ──────────────────────────────
os.environ["MARKET_DB"] = str(TMP / "no_such_market.sqlite3")
SIG7 = NOW - 30_000
uid7 = f"cand_v7_{SIG7}"
router.persist_candidates([make_cand(SIG7, uid7, symbol="ETHUSDT")], origin="live", now_ms=NOW)
r7 = router.route_pending_orders(NOW, origin="live")
os.environ["MARKET_DB"] = str(MARKET)
cc = sqlite3.connect(str(L.LEDGER_DIR / "paper_3a.sqlite3"))
cc.row_factory = sqlite3.Row
proc7 = cc.execute("SELECT action FROM paper_processed WHERE signal_uid=?", (uid7,)).fetchone()
ord7 = cc.execute("SELECT status,reject_reason FROM paper_order WHERE event_uid=?", (uid7,)).fetchone()
cc.close()
out["V7_可重试拒单不拉黑"] = {
    "route_result": r7,
    "processed_row": dict(proc7) if proc7 else None,
    "order_row": dict(ord7) if ord7 else None,
    "ledger_counts": _counts(),
}

# ── V8 恢复后重试成功：以「恢复时刻」为请求锚点登记挂单，不补旧价 ──────────────
# 说明：日常实例的 first_req 恒为真实挂钟，故积压信号恢复后登记的是「未来 15m 开盘」
# 的挂单，而不是按信号老时间回填成交 —— 这是不补开历史信号的正确行为。
SIG8 = NOW - 2 * 3600 * 1000
uid8 = f"cand_v8_{SIG8}"
bar_open8 = SIG8 + 900_000
cc = sqlite3.connect(str(MARKET))
cc.execute("INSERT INTO candles VALUES('binance','um','SOLUSDT','15m',?,?,"
           "'100','101','99','100.5','1000','100000',1)", (bar_open8, bar_open8 + 900_000 - 1))
cc.commit()
cc.close()
router.persist_candidates([make_cand(SIG8, uid8, symbol="SOLUSDT")], origin="live", now_ms=NOW)
r8 = router.route_pending_orders(NOW, origin="live")
cc = sqlite3.connect(str(L.LEDGER_DIR / "paper_3a.sqlite3"))
cc.row_factory = sqlite3.Row
proc8 = cc.execute("SELECT action FROM paper_processed WHERE signal_uid=?", (uid8,)).fetchone()
pen8 = cc.execute("SELECT status, earliest_fill_ms FROM paper_pending WHERE signal_uid=?",
                  (uid8,)).fetchone()
cc.close()
out["V8_恢复后重试成功"] = {
    "route_result": r8,
    "processed_row": dict(proc8) if proc8 else None,
    "pending_row": dict(pen8) if pen8 else None,
    "pending_earliest_fill_is_future": bool(pen8 and int(pen8["earliest_fill_ms"]) > NOW),
    "ledger_counts": _counts(),
}

# ── V9 终态拒单分支：只有 missed_fill_moment / before_paper_epoch 才写 processed ──
_orig_open = PE.open_position
try:
    PE.open_position = lambda *a, **k: {"ok": False, "reason": "missed_fill_moment_no_backfill"}
    SIG9 = NOW - 20_000
    uid9 = f"cand_v9_{SIG9}"
    router.persist_candidates([make_cand(SIG9, uid9, symbol="XRPUSDT")], origin="live", now_ms=NOW)
    r9 = router.route_pending_orders(NOW, origin="live")
finally:
    PE.open_position = _orig_open
cc = sqlite3.connect(str(L.LEDGER_DIR / "paper_3a.sqlite3"))
cc.row_factory = sqlite3.Row
proc9 = cc.execute("SELECT action FROM paper_processed WHERE signal_uid=?", (uid9,)).fetchone()
cc.close()
out["V9_终态拒单写黑名单"] = {
    "route_result": r9,
    "processed_row": dict(proc9) if proc9 else None,
    "ledger_counts": _counts(),
}

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
shutil.rmtree(TMP, ignore_errors=True)
