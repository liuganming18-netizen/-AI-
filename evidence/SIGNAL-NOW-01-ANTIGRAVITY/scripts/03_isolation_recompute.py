# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤3：独立副本复算时间边界 / 15 分钟执行 / 超时停机 / 幂等 / 倍数门禁。

反重力纪律
----------
- **不复用 Work 的 iso/ 目录，也不调用 Work 的脚本**；断言按工单条目自己重写。
- 全路径重定向到本单自己的临时目录：`PAPER_LEDGER_DIR` / `LIVE_DIR` / `LIVE_DB` /
  `MARKET_DB` / `PAPER_LOCK_DIR` / `PAPER_GATE_FILE` / `CONTRACT_SPEC_CSV`。
- 行情库**自建**（不复制 13 GiB 的生产热库），只插受控 15m bar。
- 每个场景**独立账本目录**，互不污染。
- 运行前后对生产 5 个库做 sha256 对撞，证明零写入。

隔离代价（必须如实记录，不得当成生产行为）
----------------------------------------
1. `origin="replay"` 是唯一能让 `now_ms` / `requested_at` 生效的入口：
   `paper_engine.py:1092-1095`
       real_wall_ms = L.now_ms()
       is_replay = (origin in ("replay","test") or pending_id is not None)
       attempt_ms = int(now_ms) if (is_replay and now_ms is not None) else real_wall_ms
       req_ms     = int(requested_at) if (is_replay and requested_at is not None) else attempt_ms
   用 `origin="signal"`（生产日常路径）时传入的时钟会被**丢弃**并改用真实挂钟，
   任何「历史时刻」场景都无法构造。因此本单全部场景使用 `origin="replay"`。
2. 代价：`is_replay=True` 会**同时跳过** `earliest_fill_before_first_request_clock`
   这条严格因果校验（`paper_engine.py:1158` 的 `if not is_replay and ...`）。
   ⇒ 本单**不能**用它证明「成交不早于首次请求挂钟」；该条由步骤 4 的时间字段
   对照与步骤 2 的 `test_probe_succ` 溯源另行核查。
3. 合约规格用本单自建 CSV（同一表头、同一读取代码路径），不碰生产规格文件。
4. 水位键（`live_start_ms` / `paper_epoch_ms`）按生产实测值写入**隔离库**，
   以便验证 `open_position` 的 epoch 默认值到底是 `effective_floor_ms()`
   （10-23T09:47:24Z）还是旧的 `get_paper_epoch_ms()`（10-14T01:00Z）。

场景与工单条目对应
------------------
  T1/T2/T2b/T3/T4 → 时间边界（正例 / 4 天前确认不得续期 / 停机不追补 / epoch 门禁 / 幂等）
  D1..D5          → 15 分钟执行（落点 / 未到点挂单 / available_at 顺延 / 未收盘 / 前视）
  S1..S4          → 超时与停机（超 1h 拒 / 停机前收盘拒 / 反例正常成交 / 闭 bar 后记账可分辨）
  M0/M1/M2        → 未批准仓位倍数不得改变基准风险
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"

MIN = 60_000
BAR = 15 * MIN
H1 = 3_600_000

# 生产实测水位（步骤 1 已独立采样，见 out/01_身份与加载证据.json）
LIVE_START_MS = 1_792_748_844_240     # 2026-10-23T09:47:24Z
PAPER_EPOCH_MS = 1_791_939_600_000    # 2026-10-14T01:00:00Z


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def utc(x):
    if x is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(x) / 1000))


def utc_ms(x):
    if x is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(x) / 1000)) + \
        f".{int(x) % 1000:03d}Z"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


PROD_DBS = {
    "live_signals": BACKEND / "live/live_signals.sqlite3",
    "paper_3a": PROJ / "数据/paper/paper_3a.sqlite3",
    "paper_3b": PROJ / "数据/paper/paper_3b.sqlite3",
    "paper_3c": PROJ / "数据/paper/paper_3c.sqlite3",
    "paper_3d": PROJ / "数据/paper/paper_3d.sqlite3",
}

BASE = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)        # 晚于 effective_floor
SIG_OLD = datetime(2026, 10, 23, 9, 47, 25, tzinfo=timezone.utc)   # 与 test_probe_succ 同型
HOLE_DAY = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc)     # 落在 9 天 8 小时边界洞里


# ══════════════════════════════════════════════════════════════════════════
# 自建行情库
# ══════════════════════════════════════════════════════════════════════════
CANDLES_DDL = """
CREATE TABLE IF NOT EXISTS candles (
  exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
  open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
  trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
  source_id INTEGER, available_at INTEGER, is_closed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (exchange, market, symbol, timeframe, open_time)
);
"""


def build_market(path: Path, spec: dict) -> dict:
    con = sqlite3.connect(str(path))
    con.executescript(CANDLES_DDL)
    for sym, cfg in spec.items():
        for (ot, price, closed, close_shift) in cfg["bars"]:
            ct = ot + BAR - 1 + (close_shift or 0)
            con.execute(
                "INSERT OR REPLACE INTO candles(exchange,market,symbol,timeframe,open_time,"
                "close_time,open,high,low,close,volume,quote_volume,trade_count,"
                "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                "VALUES('binance','um',?,'15m',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sym, ot, ct, f"{price:.4f}", f"{price * 1.002:.4f}", f"{price * 0.998:.4f}",
                 f"{price * 1.001:.4f}", "1000", "1000000000", 500, "500",
                 "500000000", 1, ct + 2000, 1 if closed else 0))
    con.commit()
    con.close()
    return {sym: len(cfg["bars"]) for sym, cfg in spec.items()}


def grid(start: datetime, end: datetime, base_price: float, step: float = 0.1,
         closed: int = 1):
    """价格 = 基准 + 序号×步长 ⇒ 成交价可反查取自哪一根 bar。"""
    out, i = [], 0
    t = start
    while t <= end:
        out.append((ms(t), round(base_price + i * step, 4), closed, 0))
        t += timedelta(minutes=15)
        i += 1
    return out


def make_contract_spec(path: Path, symbols: list[str]) -> None:
    """与生产同表头、同读取代码路径（`contract_limits` 直读 CSV）。"""
    header = ["币", "当前报价数量单位", "当前计价币", "当前保证金币", "价格步进", "数量步进",
              "最小数量", "最大数量", "市价数量步进", "市价最小数量", "市价最大数量",
              "最小订单金额", "规格固定假设起止", "当前官方材料日期", "历史变化已证明",
              "用途", "来源", "来源校验码"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for s in symbols:
            w.writerow([s, s.replace("USDT", ""), "USDT", "USDT",
                        "0.0001", "0.0001", "0.0001", "10000000",
                        "0.0001", "0.0001", "10000000", "5",
                        "隔离构造", "隔离构造", "False",
                        "隔离复算用，不是真实规格",
                        "SIGNAL-NOW-01-ANTIGRAVITY/03", "-"])


# ══════════════════════════════════════════════════════════════════════════
# 场景定义
# ══════════════════════════════════════════════════════════════════════════
def scenarios():
    B = BASE
    S = []

    # ── T1 正例 ────────────────────────────────────────────────────────────
    S.append(dict(key="T1", symbol="ISO01USDT", uid="iso_t1",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 100.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=15))))

    # ── T2 4 天前确认 + 今天首次请求 ⇒ 必须拒（不得续期）─────────────────────
    # process_start 刻意设为「信号确认之前」，把停机判据排除在外，
    # 使拒单原因**只能是** lag 判据 ⇒ 才证明得了「不续期」这条语义本身。
    S.append(dict(key="T2", symbol="ISO02USDT", uid="iso_t2",
                  event=ms(SIG_OLD), avail=None, first_req=ms(B),
                  process_start=ms(SIG_OLD - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(SIG_OLD.replace(minute=15, second=0, microsecond=0),
                            B + timedelta(minutes=30), 200.0, 0.01),
                  expect="rejected", expect_reason="missed_fill_moment_no_backfill"))

    # ── T2b 同上，但进程在应成交 bar 收盘后才启动 ⇒ 停机判据也应拦 ───────────
    S.append(dict(key="T2b", symbol="ISO03USDT", uid="iso_t2b",
                  event=ms(SIG_OLD), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(SIG_OLD.replace(minute=15, second=0, microsecond=0),
                            B + timedelta(minutes=30), 200.0, 0.01),
                  expect="rejected",
                  expect_reason="fill_point_closed_before_process_start"))

    # ── T3 epoch 门禁：10-20 落在「9 天 8 小时边界洞」内 ─────────────────────
    # 若默认 epoch 仍是 get_paper_epoch_ms()(10-14T01:00) ⇒ 会被放行（缺陷仍在）；
    # 若默认已是 effective_floor_ms()(10-23T09:47:24) ⇒ 拒单（已修）。
    S.append(dict(key="T3", symbol="ISO04USDT", uid="iso_t3",
                  event=ms(HOLE_DAY), avail=None, first_req=ms(B),
                  process_start=ms(HOLE_DAY - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(HOLE_DAY, B + timedelta(hours=1), 300.0, 0.01),
                  expect="rejected", expect_reason="before_paper_epoch_no_backfill"))

    # ── T4 幂等 + 不续期重单 ───────────────────────────────────────────────
    S.append(dict(key="T4", symbol="ISO05USDT", uid="iso_t4",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 400.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=15)),
                  then_second_call="duplicate_signal_uid"))

    # ── D1 15 分钟落点 ─────────────────────────────────────────────────────
    S.append(dict(key="D1", symbol="ISO06USDT", uid="iso_d1",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 500.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=15))))

    # ── D2 应成交 bar 尚不存在 ⇒ 挂单，不提前成交 ───────────────────────────
    S.append(dict(key="D2", symbol="ISO07USDT", uid="iso_d2",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=5)),
                  bars=grid(B - timedelta(hours=2), B, 600.0),      # 只到 00:00
                  expect="pending_future_fill"))

    # ── D3 available_at 晚 10 分钟 ⇒ 落点顺延到 00:30 ───────────────────────
    S.append(dict(key="D3", symbol="ISO08USDT", uid="iso_d3",
                  event=ms(B), avail=ms(B + timedelta(minutes=10)),
                  first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=45, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 700.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=30))))

    # ── D4 缺陷探针：is_closed=1 但尝试时刻早于该 bar 收盘 ──────────────────
    S.append(dict(key="D4", symbol="ISO09USDT", uid="iso_d4",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B),
                  now=ms(B + timedelta(minutes=20)),    # 00:20 < 00:29:59.999 收盘
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 800.0),
                  expect="probe", expect_bar_open=ms(B + timedelta(minutes=15))))

    # ── D5 前视探针：bar_open 晚于尝试时刻 ⇒ 应拒 ───────────────────────────
    S.append(dict(key="D5", symbol="ISO10USDT", uid="iso_d5",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=10)),    # 00:10 < 应成交 bar 开盘 00:15
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 900.0),
                  expect="rejected",
                  expect_reason="bar_after_attempt_clock_no_lookahead"))

    # ── S1 滞后超过 MAX_FILL_LAG_MS(1h) ⇒ 拒 ───────────────────────────────
    S.append(dict(key="S1", symbol="ISO11USDT", uid="iso_s1",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=90, seconds=5)),   # lag = 75m5s > 60m
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=3), 1000.0),
                  expect="rejected", expect_reason="missed_fill_moment_no_backfill"))

    # ── S2 停机：应成交 bar 在进程启动前已收盘 ⇒ 拒 ─────────────────────────
    S.append(dict(key="S2", symbol="ISO12USDT", uid="iso_s2",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B + timedelta(minutes=40)),     # 00:40 > 00:29:59.999
                  now=ms(B + timedelta(minutes=41)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 1100.0),
                  expect="rejected",
                  expect_reason="fill_point_closed_before_process_start"))

    # ── S3 反例（防新判据过严）：进程在 bar 收盘前已运行 ⇒ 正常成交 ──────────
    S.append(dict(key="S3", symbol="ISO13USDT", uid="iso_s3",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B + timedelta(minutes=10)),     # 00:10 < 收盘
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 1200.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=15))))

    # ── S4 闭 bar 后记账可分辨 ─────────────────────────────────────────────
    S.append(dict(key="S4", symbol="ISO14USDT", uid="iso_s4",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=grid(B - timedelta(hours=2), B + timedelta(hours=2), 1300.0),
                  expect="filled", expect_bar_open=ms(B + timedelta(minutes=15))))

    # ── M0/M1/M2：同 symbol 同价同 bar，只差 payload ────────────────────────
    _mbars = grid(B - timedelta(hours=2), B + timedelta(hours=2), 1400.0)
    S.append(dict(key="M0", symbol="ISOMUSDT", uid="iso_m0",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=_mbars, expect="filled",
                  expect_bar_open=ms(B + timedelta(minutes=15))))
    S.append(dict(key="M1", symbol="ISOMUSDT", uid="iso_m1",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=_mbars, expect="filled",
                  expect_bar_open=ms(B + timedelta(minutes=15)),
                  payload={"position_multiplier": 0.5}))
    S.append(dict(key="M2", symbol="ISOMUSDT", uid="iso_m2",
                  event=ms(B), avail=None, first_req=ms(B),
                  process_start=ms(B - timedelta(hours=1)),
                  now=ms(B + timedelta(minutes=30, seconds=5)),
                  bars=_mbars, expect="filled",
                  expect_bar_open=ms(B + timedelta(minutes=15)),
                  payload={"position_multiplier": 0.5,
                           "position_multiplier_approved": True}))
    return S


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "3 独立副本复算（时间边界/15分钟/超时停机/幂等/倍数）",
         "at_utc": utc_ms(time.time() * 1000),
         "method": "原地导入生产模块 + 全路径环境变量重定向 + 自建受控行情库 + 每场景独立账本",
         "isolation_cost": [
             'origin="replay" 是唯一能让 now_ms/requested_at 生效的入口'
             '（paper_engine.py:1092-1095 用 is_replay 短路）；生产日常路径 origin="signal" '
             '会丢弃外部时钟改用真实挂钟，历史时刻场景无法构造。',
             '代价：is_replay=True 同时跳过 paper_engine.py:1158 的 '
             'earliest_fill_before_first_request_clock 校验 ⇒ 本脚本不能证明'
             '「成交不早于首次请求挂钟」，该条由步骤 2/4 另行核查。',
             "合约规格用本单自建 CSV（同表头、同读取代码路径），不碰生产规格文件。",
             "水位键按生产实测值写入隔离库，用于验证 epoch 默认值口径。"]}

    R["prod_before"] = {k: {"sha256": sha256_file(p) if p.exists() else None,
                            "size": p.stat().st_size if p.exists() else None}
                        for k, p in PROD_DBS.items()}

    iso_root = ROOT / "iso"
    iso_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="now01_ag_", dir=str(iso_root)))
    paper_dir = tmp / "paper"
    live_dir = tmp / "live"
    lock_dir = tmp / "locks"
    for d in (paper_dir, live_dir, lock_dir):
        d.mkdir(parents=True, exist_ok=True)
    market_db = tmp / "market.sqlite3"
    spec_csv = tmp / "contract_spec.csv"
    gate_file = lock_dir / "paper_new_entry_gate.json"
    live_db = live_dir / "live_signals.sqlite3"

    S = scenarios()
    symbols = sorted({sc["symbol"] for sc in S})
    make_contract_spec(spec_csv, symbols)

    spec = {}
    for sc in S:
        spec[sc["symbol"]] = {"bars": sc["bars"]}
    R["market_db"] = {"path": str(market_db), "symbols": build_market(market_db, spec),
                      "note": "自建受控 15m bar；价格 = 基准 + 序号×步长，便于反查取自哪一根"}
    R["contract_spec_csv"] = {"path": str(spec_csv), "symbols": symbols,
                              "note": "同表头同读取路径；步进 0.0001 以容纳合成价格"}

    # 门禁文件必须显式放行：new_entry_gate() 是 fail-closed（缺文件 ⇒ 全拒）
    gate_file.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_recompute_now01_antigravity",
        "set_by": "SIGNAL-NOW-01-ANTIGRAVITY", "since_ms": LIVE_START_MS,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")

    # ── 环境重定向（必须在 import 之前）──
    os.environ.update({
        "PAPER_LEDGER_DIR": str(paper_dir),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(live_dir),
        "LIVE_DB": str(live_db),
        "MARKET_DB": str(market_db),
        "PAPER_LOCK_DIR": str(lock_dir),
        "PAPER_GATE_FILE": str(gate_file),
        "PAPER_GATE_STRICT": "0",
        "CONTRACT_SPEC_CSV": str(spec_csv),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    sys.path.insert(0, str(BACKEND))
    import live_store as L            # noqa: E402
    import paper_engine as PE         # noqa: E402

    L.ensure_live_schema()
    # 水位键按生产实测值写入隔离库（只写隔离库）
    L.set_run("live_start_ms", LIVE_START_MS)
    L.set_run("paper_epoch_ms", PAPER_EPOCH_MS)

    R["env"] = {k: os.environ.get(k) for k in
                ("PAPER_LEDGER_DIR", "LIVE_DB", "MARKET_DB", "PAPER_LOCK_DIR",
                 "PAPER_GATE_FILE", "CONTRACT_SPEC_CSV", "PAPER_LEDGER_ALLOW_INIT")}
    R["watermarks"] = {"live_start_ms": LIVE_START_MS, "live_start_utc": utc_ms(LIVE_START_MS),
                       "paper_epoch_ms": PAPER_EPOCH_MS, "paper_epoch_utc": utc_ms(PAPER_EPOCH_MS),
                       "readback_live_start": L.get_live_start_ms(),
                       "readback_paper_epoch": L.get_paper_epoch_ms()}
    R["effective_floor_ms"] = int(L.effective_floor_ms())
    R["effective_floor_utc"] = utc_ms(L.effective_floor_ms())
    _ap = PE.approved_params() or {}
    R["approved_params_subset"] = {k: _ap.get(k) for k in
                                   ("signal_execution_delay_minutes", "stoploss_s",
                                    "risk_per_trade_equity_fraction",
                                    "initial_equity_USDT", "single_name_notional_equity_cap",
                                    "portfolio_initial_stop_risk_cap",
                                    "entry_participation_previous_15m_quote_cap",
                                    "slippage_each_side", "fee_each_side",
                                    "leverage", "max_positions")}
    R["max_fill_lag_ms"] = PE.MAX_FILL_LAG_MS
    R["effective_view"] = {"path": str(PE.EFFECTIVE_VIEW),
                           "exists": Path(PE.EFFECTIVE_VIEW).exists(),
                           "sha256": (sha256_file(Path(PE.EFFECTIVE_VIEW))
                                      if Path(PE.EFFECTIVE_VIEW).exists() else None),
                           "note": "生产运行时实际读取的生效视图（未改、未复制，只取哈希）"}
    R["gate"] = PE.new_entry_gate()

    results = []
    for sc in S:
        d = tmp / f"ledger_{sc['key']}"
        d.mkdir(parents=True, exist_ok=True)
        os.environ["PAPER_LEDGER_DIR"] = str(d)
        rec = {"key": sc["key"], "symbol": sc["symbol"], "expect": sc["expect"],
               "event_utc": utc_ms(sc["event"]), "avail_utc": utc_ms(sc["avail"]),
               "first_request_utc": utc_ms(sc["first_req"]),
               "attempt_utc": utc_ms(sc["now"]),
               "process_start_utc": utc_ms(sc["process_start"]),
               "payload": sc.get("payload"),
               "expect_bar_open": sc.get("expect_bar_open"),
               "expect_bar_open_utc": utc_ms(sc.get("expect_bar_open")),
               "expect_reason": sc.get("expect_reason")}
        try:
            res = PE.open_position(
                "3a", sc["symbol"], signal_uid=sc["uid"],
                direction="long", atr=None, stop_price=None,
                now_ms=sc["now"], event_time=sc["event"], available_at=sc["avail"],
                requested_at=sc["first_req"], signal_payload=sc.get("payload"),
                origin="replay", pending_id=None, epoch_ms=None,
                first_request_ms=sc["first_req"], entry_mode="three_buy_points",
                stop_mode="uniform", stoploss_s=None, trail_d=None,
                model_version=None, config_fingerprint=None,
                process_start_ms=sc["process_start"])
            rec["res"] = {k: res.get(k) for k in
                          ("ok", "reason", "pending_id", "order_request_ms",
                           "earliest_fill_ms", "bar_open_time", "bar_close_time",
                           "epoch_ms", "attempt_ms", "lag_ms", "requested_at",
                           "first_request_ms", "signal_bar_right_ms", "note")}
            rec["result_ok"] = res.get("ok")
            rec["result_reason"] = res.get("reason")
            rec["result_order_request_ms"] = res.get("order_request_ms")
            rec["result_earliest_fill_ms"] = res.get("earliest_fill_ms")
            rec["result_bar_open_time"] = res.get("bar_open_time")
            rec["result_bar_open_utc"] = utc_ms(res.get("bar_open_time"))
            rec["result_bar_close_time"] = res.get("bar_close_time")
            rec["result_bar_close_utc"] = utc_ms(res.get("bar_close_time"))
            rec["result_epoch_ms"] = res.get("epoch_ms")
            rec["result_epoch_utc"] = utc_ms(res.get("epoch_ms"))
            rec["result_lag_ms"] = res.get("lag_ms")
        except Exception as exc:                                  # noqa: BLE001
            rec["exception"] = f"{type(exc).__name__}: {exc}"
            rec["result_ok"] = None
            rec["result_reason"] = None
            rec["result_bar_open_time"] = None
            rec["result_bar_open_utc"] = None
            rec["result_bar_close_time"] = None
            rec["result_bar_close_utc"] = None
            rec["result_epoch_ms"] = None
            rec["result_epoch_utc"] = None
            rec["result_lag_ms"] = None

        con = L.paper_conn("3a", ro=True)
        try:
            con.row_factory = sqlite3.Row
            rec["positions"] = [dict(r) for r in con.execute("SELECT * FROM paper_position")]
            rec["fills"] = [dict(r) for r in con.execute("SELECT * FROM paper_fill")]
            rec["orders"] = [dict(r) for r in con.execute("SELECT * FROM paper_order")]
            rec["pendings"] = [dict(r) for r in con.execute("SELECT * FROM paper_pending")]
            rec["anchors"] = [dict(r) for r in con.execute("SELECT * FROM paper_request_anchor")]
            rec["processed"] = [dict(r) for r in con.execute("SELECT * FROM paper_processed")]
        finally:
            con.close()

        # T4 第二次调用（幂等 + 不续期）
        if sc.get("then_second_call"):
            try:
                res2 = PE.open_position(
                    "3a", sc["symbol"], signal_uid=sc["uid"], direction="long",
                    atr=None, stop_price=None, now_ms=sc["now"] + 1000,
                    event_time=sc["event"], available_at=sc["avail"],
                    requested_at=sc["first_req"] + 60_000, signal_payload=None,
                    origin="replay", pending_id=None, epoch_ms=None,
                    first_request_ms=sc["first_req"] + 60_000,
                    entry_mode="three_buy_points", stop_mode="uniform",
                    stoploss_s=None, trail_d=None, model_version=None,
                    config_fingerprint=None, process_start_ms=sc["process_start"])
                rec["second_call"] = {"ok": res2.get("ok"), "reason": res2.get("reason"),
                                      "expect": sc["then_second_call"]}
            except Exception as exc:                              # noqa: BLE001
                rec["second_call"] = {"exception": f"{type(exc).__name__}: {exc}"}
            con = L.paper_conn("3a", ro=True)
            try:
                rec["anchors_after_second"] = [
                    {"account_id": r[0], "event_uid": r[1],
                     "first_request_ms": r[2], "first_request_utc": utc_ms(r[2]),
                     "created_at": r[3]}
                    for r in con.execute("SELECT account_id,event_uid,first_request_ms,"
                                         "created_at FROM paper_request_anchor")]
                rec["position_count_after_second"] = con.execute(
                    "SELECT COUNT(*) FROM paper_position").fetchone()[0]
            finally:
                con.close()

        results.append(rec)

    R["scenarios"] = results
    byk = {r["key"]: r for r in results}

    # ── 场景判定 ──
    def judge(rec):
        k, exp = rec["key"], rec["expect"]
        if rec.get("exception"):
            return False, f"抛异常：{rec['exception']}"
        if exp == "filled":
            ok = bool(rec["result_ok"]) and len(rec["fills"]) == 1
            if ok and rec.get("expect_bar_open") is not None:
                ok = rec["result_bar_open_time"] == rec["expect_bar_open"]
            return ok, ("成交且执行 bar 落点正确" if ok else
                        f"未按预期成交或落点不符（reason={rec['result_reason']}）")
        if exp == "rejected":
            ok = (not rec["result_ok"]) and rec["result_reason"] == rec["expect_reason"]
            return ok, (f"按预期拒单 {rec['result_reason']}" if ok
                        else f"拒单原因不符（实际 {rec['result_reason']}）")
        if exp == "pending_future_fill":
            ok = (not rec["result_ok"]) and rec["result_reason"] == "pending_future_fill" \
                and len(rec["pendings"]) == 1 and rec["pendings"][0].get("status") == "pending" \
                and len(rec["fills"]) == 0
            return ok, ("登记挂单、未提前成交" if ok
                        else f"未按预期挂单（reason={rec['result_reason']}）")
        return None, "探针场景，不做通过判定"

    A = []
    for rec in results:
        ok, why = judge(rec)
        rec["verdict"] = {"ok": ok, "why": why}
        if ok is not None:
            A.append({"scenario": rec["key"], "ok": ok, "why": why})

    # ── 专项断言 ──
    extra = []

    # 1) epoch 默认口径（T3）：默认必须是 effective_floor 而不是 paper_epoch
    t3 = byk["T3"]
    extra.append({
        "assert": "open_position 的 epoch 默认值 = effective_floor_ms(10-23T09:47:24Z)，"
                  "不是 get_paper_epoch_ms(10-14T01:00Z)",
        "ok": (t3.get("result_epoch_ms") == R["effective_floor_ms"]),
        "detail": f"T3 event={t3['event_utc']}（落在 9 天 8 小时洞内） "
                  f"默认 epoch={t3['result_epoch_utc']} 期望={R['effective_floor_utc']} "
                  f"结果={t3['result_reason']}",
        "note": "若为 False ⇒ 默认仍取 paper_epoch，边界洞仍存在"})

    # 2) 4 天前确认不得续期（T2）：order_request 必须取信号自身时刻
    t2 = byk["T2"]
    extra.append({
        "assert": "T2 4 天前确认：order_request_ms 取信号自身时刻（不取今天首次请求）",
        "ok": (t2.get("res", {}).get("order_request_ms") == ms(SIG_OLD)),
        "detail": f"event={t2['event_utc']} first_request={t2['first_request_utc']} "
                  f"order_request={utc_ms(t2.get('res', {}).get('order_request_ms'))} "
                  f"lag={t2.get('result_lag_ms')}ms 结果={t2['result_reason']}",
        "note": "若 order_request 被推到首次请求时刻 ⇒ 旧信号被续期（C1 未修）"})

    # 3) D4 未收盘 bar 是否被提前成交
    d4 = byk["D4"]
    _d4_fill_ts = d4["fills"][0]["fill_ts"] if d4["fills"] else None
    extra.append({
        "assert": "D4 未收盘 bar 不得成交（尝试时刻 < bar_close）",
        "ok": len(d4["fills"]) == 0,
        "detail": f"尝试={d4['attempt_utc']} bar_open={d4['result_bar_open_utc']} "
                  f"bar_close={d4['result_bar_close_utc']} 结果={d4['result_reason']} "
                  f"成交数={len(d4['fills'])} fill_ts={utc_ms(_d4_fill_ts)} "
                  f"（fill_ts 早于 bar_close {(_d4_fill_ts or 0) - (d4['result_bar_close_time'] or 0)}ms）",
        "note": "为 False ⇒ 引擎只信 is_closed 标记，代码层没有 bar_close<=attempt 校验；"
                "安全性完全依赖采集器不提前置 is_closed=1"})

    # 4) T4 幂等 + 不续期 + 不产生第二笔持仓
    t4 = byk["T4"]
    extra.append({"assert": "T4 同 uid 第二次请求 ⇒ duplicate_signal_uid",
                  "ok": (t4.get("second_call") or {}).get("reason") == "duplicate_signal_uid",
                  "detail": str(t4.get("second_call"))})
    _a0 = (t4.get("anchors") or [{}])[0].get("first_request_ms")
    extra.append({"assert": "T4 第二次请求不刷新 first_request（不续期重单）",
                  "ok": bool(t4.get("anchors_after_second")) and all(
                      a["first_request_ms"] == _a0 for a in t4["anchors_after_second"]),
                  "detail": f"首次={utc_ms(_a0)} 第二次后={t4.get('anchors_after_second')}"})
    extra.append({"assert": "T4 第二次请求不产生第二笔持仓",
                  "ok": t4.get("position_count_after_second") == 1,
                  "detail": f"position_count={t4.get('position_count_after_second')}"})

    # 5) S4 闭 bar 后记账可分辨
    s4 = byk["S4"]
    if s4["fills"] and s4["positions"]:
        f, p = s4["fills"][0], s4["positions"][0]
        # 注意：成交路径的返回 dict **不含** attempt_ms（只有拒单路径才带），
        # 所以这里用场景自身登记的尝试时刻比对，不用 res.attempt_ms。
        _attempt = [sc["now"] for sc in S if sc["key"] == "S4"][0]
        extra.append({
            "assert": "S4 entry_ts == bar_open、fill_ts == 尝试时刻、且 fill_ts > bar_close",
            "ok": (p["entry_ts"] == f["bar_open_time"] and f["fill_ts"] == _attempt
                   and f["fill_ts"] > f["bar_close_time"]),
            "detail": f"entry_ts={utc_ms(p['entry_ts'])} bar_open={utc_ms(f['bar_open_time'])} "
                      f"bar_close={utc_ms(f['bar_close_time'])} fill_ts={utc_ms(f['fill_ts'])} "
                      f"尝试={utc_ms(_attempt)} 回填量={f['fill_ts'] - f['bar_open_time']}ms",
            "note": "entry_ts 回填到 bar 开盘是既定口径（entry_ts 语义 = 执行 bar 开盘），"
                    "但 fill_ts 必须晚于 bar 收盘 ⇒ 二者差值可分辨「实时成交」与「事后补记」"})
    else:
        extra.append({"assert": "S4 entry_ts/fill_ts 可分辨", "ok": False,
                      "detail": f"S4 未成交：{s4['result_reason']}"})

    # 6) 成交价来源
    d1 = byk["D1"]
    if d1["fills"]:
        f = d1["fills"][0]
        mc = sqlite3.connect(str(market_db))
        try:
            row = mc.execute("SELECT open,is_closed,high,low FROM candles WHERE symbol=? "
                             "AND timeframe='15m' AND open_time=?",
                             (d1["symbol"], f["bar_open_time"])).fetchone()
        finally:
            mc.close()
        extra.append({
            "assert": "D5 成交价来自该执行 bar 的 open（raw_price==bar.open）且该 bar is_closed=1",
            "ok": (row is not None and abs(float(row[0]) - float(f["raw_price"])) < 1e-9
                   and int(row[1]) == 1),
            "detail": f"raw_price={f['raw_price']} bar.open={row[0] if row else None} "
                      f"is_closed={row[1] if row else None} "
                      f"bar[{row[3] if row else '-'}, {row[2] if row else '-'}]"})
    else:
        extra.append({"assert": "成交价来源核对", "ok": False,
                      "detail": f"D1 未成交：{d1['result_reason']}"})

    # 7) 倍数门禁（M0 基准 / M1 未批准 / M2 已批准，同 symbol 同价同 bar）
    def q(k):
        return byk[k]["positions"][0]["qty"] if byk[k]["positions"] else None
    q0, q1, q2 = q("M0"), q("M1"), q("M2")
    extra.append({
        "assert": "M1 未批准 0.5x ⇒ 下单量与无倍数基准完全一致（风险不变）",
        "ok": (q0 is not None and q1 is not None and abs(q1 / q0 - 1.0) < 1e-9),
        "detail": f"qty(M0 无倍数)={q0} qty(M1 未批准0.5x)={q1} 比值={q1 / q0 if q0 else None}"})
    extra.append({
        "assert": "M2 已批准 0.5x ⇒ 下单量约减半（证明 M1 的「不变」不是恒真）",
        "ok": (q0 is not None and q2 is not None and abs(q2 / q0 - 0.5) < 0.02),
        "detail": f"qty(M2 已批准0.5x)={q2} qty(M0)={q0} 比值={q2 / q0 if q0 else None}"})

    R["assertions"] = A
    R["extra_assertions"] = extra
    n_ok = sum(1 for a in A if a["ok"]) + sum(1 for a in extra if a["ok"])
    n_all = len(A) + len(extra)
    R["summary"] = {"scenario_assertions": len(A),
                    "scenario_passed": sum(1 for a in A if a["ok"]),
                    "extra_assertions": len(extra),
                    "extra_passed": sum(1 for a in extra if a["ok"]),
                    "total": n_all, "passed": n_ok}

    R["prod_after"] = {k: {"sha256": sha256_file(p) if p.exists() else None,
                           "size": p.stat().st_size if p.exists() else None}
                       for k, p in PROD_DBS.items()}
    R["prod_untouched"] = {k: (R["prod_before"][k]["sha256"] == R["prod_after"][k]["sha256"])
                           for k in PROD_DBS}
    R["market_db_is_production"] = (str(market_db) == str(PROJ / "数据/市场数据/db/market_v2.sqlite3"))
    R["tmp_dir"] = str(tmp)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "03_隔离复算.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"隔离根目录：{tmp}")
    print(f"effective_floor = {R['effective_floor_utc']}  MAX_FILL_LAG_MS={R['max_fill_lag_ms']}")
    print(f"参数：{R['approved_params_subset']}")
    print(f"门禁：allow_new_entries={R['gate']['allow_new_entries']}")
    print("\n=== 场景判定 ===")
    for rec in results:
        v = rec["verdict"]
        flag = {True: "PASS", False: "FAIL", None: "探针"}[v["ok"]]
        print(f"  [{flag}] {rec['key']:4s} {rec['symbol']:11s} "
              f"reason={str(rec['result_reason']):42s} bar_open={rec['result_bar_open_utc']} "
              f"| {v['why']}")
    print("\n=== 专项断言 ===")
    for a in extra:
        print(f"  [{'PASS' if a['ok'] else 'FAIL'}] {a['assert']}\n        {a['detail']}")
    print(f"\n合计 {R['summary']['passed']}/{R['summary']['total']}")
    print("\n=== 生产库零写入校验 ===")
    for k, v in R["prod_untouched"].items():
        print(f"  {k}: 未变={v}")
    print(f"  行情库指向生产={R['market_db_is_production']}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
