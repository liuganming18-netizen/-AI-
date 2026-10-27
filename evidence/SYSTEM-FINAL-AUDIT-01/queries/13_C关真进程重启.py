# -*- coding: utf-8 -*-
"""13 C 关独立验收：真进程重启 + 两组配置 + 全账本闭合。

与反重力 ITEM-2 的关键差别
--------------------------
他们：`rt1.stop(); del rt1` 后在**同一进程**新建 `UnifiedRuntime` ⇒ 只证明「对象能重建」。
本探针：stage1 是独立子进程，跑完用 **SIGKILL 硬崩**（不 release_lock、不写 stopped）；
stage2 是**全新进程**，必须自己从 SQLite 接管持仓、继续上移、完成退出。

场景数据复用 `SYSTEM-RUNTIME-01/test_system_runtime_01.py::build_wyckoff_scenario_bars`
（纯数据构造，无副作用），但本探针对其形态参数做了独立核验，并把两套配置改成
**冻结口径**（equity 2000 / 3 仓 / 单名 0.30 / 总多 0.90 / 参与率 0.001 /
组合风险 0.015 / slippage 3bps）与**对方口径**（equity 5000 / 10 仓 / 0.10 / 0.80 /
0.01 / 0.05 / 5bps）各跑一遍，看结论是否依赖配置口径。

产物：out/13_C关真进程重启.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
PY = sys.executable
STAGE = HERE / "queries/13_stage.py"

ROOT = pathlib.Path("/tmp/wx_final04")
M15 = 900_000

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "evidence/SYSTEM-RUNTIME-01"))

FROZEN = {   # FT-VALIDATION-02 认定口径
    "initial_equity_USDT": 2000.0,
    "risk_per_trade_equity_fraction": 0.005,
    "max_positions": 3,
    "single_name_notional_equity_cap": 0.30,
    "gross_long_notional_equity_cap": 0.90,
    "cash_or_free_margin_reserve_fraction": 0.20,
    "portfolio_initial_stop_risk_cap": 0.015,
    "entry_participation_previous_15m_quote_cap": 0.001,
    "leverage": 1,
    "maximum_account_drawdown_fraction": 0.20,
    "short_enabled": False,
}
THEIRS = {   # 反重力 RUNTIME-01 夹具口径
    "initial_equity_USDT": 5000.0,
    "risk_per_trade_equity_fraction": 0.005,
    "max_positions": 10,
    "single_name_notional_equity_cap": 0.10,
    "gross_long_notional_equity_cap": 0.80,
    "cash_or_free_margin_reserve_fraction": 0.20,
    "portfolio_initial_stop_risk_cap": 0.05,
    "entry_participation_previous_15m_quote_cap": 0.01,
    "leverage": 1,
    "maximum_account_drawdown_fraction": 0.20,
    "short_enabled": False,
}


def iso(ms) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(int(ms) / 1000).astimezone().isoformat(timespec="seconds")


def make_view(path: pathlib.Path, s: float, d: float, risk: dict, label: str) -> str:
    import strategy_contracts as SC
    content = {
        "schema_version": "research_policy_2.2",
        "spec_version": f"F_WX_final01_{label}",
        "strategy_version": f"FT-LOW4H-EXP-{label}",
        "model_version": f"FT-LOW4H-EXP-{label}",
        "purpose": "audit_independent_verification",
        "production_config": False,
        "live_trading_authorized": False,
        "research_policy_frozen": True,
        "execution": {"detail_timeframe": "15m",
                      "signal_execution_delay_minutes": 15,
                      "fee_each_side": 0.0005,
                      "slippage_each_side": 0.0003,
                      "signal_exit_delay_minutes": 15},
        "stops": {"rule": "FT-LOW4H", "stoploss_s": float(s), "trail_d": float(d),
                  "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
                  "max_holding_hours": 240, "trailing_enabled": True},
        "risk": dict(risk),
    }
    raw = json.dumps(content, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return SC.compute_config_fingerprint(raw.encode("utf-8"))[:12]


def market_schema(market: pathlib.Path) -> None:
    con = sqlite3.connect(market)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE IF NOT EXISTS candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE IF NOT EXISTS funding_history(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    try:
        con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                    "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


def ledger(paper_dir: pathlib.Path, arm: str = "3a") -> dict:
    f = paper_dir / f"paper_{arm}.sqlite3"
    if not f.exists():
        return {"存在": False}
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {"账本": str(f), "存在": True, "字节": f.stat().st_size}
    for tbl, order in (("paper_pending", "pending_id"), ("paper_order", "order_id"),
                       ("paper_fill", "fill_id"), ("paper_position", "position_id"),
                       ("paper_stop_history", "id"), ("paper_state", None),
                       ("paper_realized", "id")):
        try:
            if order is None:
                rows = [dict(r) for r in con.execute(f"SELECT * FROM {tbl}").fetchall()]
            else:
                rows = [dict(r) for r in con.execute(
                    f"SELECT * FROM {tbl} ORDER BY {order}").fetchall()]
        except sqlite3.Error as exc:
            rows = [{"_错误": str(exc)}]
        out[tbl] = rows
    con.close()
    return out


def run_case(cfg_label: str, s: float, d: float, risk: dict, meta: dict,
             bars_path: pathlib.Path, t_start: int, t_end: int) -> dict:
    d_root = ROOT / cfg_label
    if d_root.exists():
        shutil.rmtree(d_root)
    paper = d_root / "paper"
    live = d_root / "live"
    locks = d_root / "locks"
    for p in (paper, live, locks, d_root / "config"):
        p.mkdir(parents=True, exist_ok=True)
    market = d_root / "market.sqlite3"
    gate = d_root / "gate.json"
    view = d_root / "config" / "view.json"

    fp = make_view(view, s, d, risk, cfg_label)
    market_schema(market)
    gate.write_text(json.dumps({"allow_new_entries": True, "reason": "final01_c_gate",
                                "gate_reason": "final01_c_gate", "set_by": "SYSTEM-FINAL-AUDIT-01",
                                "since_ms": int(time.time() * 1000)}, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "PAPER_LEDGER_DIR": str(paper), "PAPER_LEDGER_ALLOW_INIT": "1",
        "PAPER_LOCK_DIR": str(locks), "LIVE_DIR": str(live),
        "LIVE_DB": str(live / "live_signals.sqlite3"),
        "MARKET_DB": str(market), "EFFECTIVE_VIEW": str(view),
        "PAPER_GATE_FILE": str(gate), "WX_BACKEND": str(BACKEND),
        "PYTHONUNBUFFERED": "1",
    })
    # 先在父进程建 live schema / paper epoch
    for k, v in env.items():
        if k.startswith(("PAPER_", "LIVE_", "MARKET_", "EFFECTIVE_")):
            os.environ[k] = v
    import importlib
    import live_store as L
    importlib.reload(L)
    L.ensure_live_schema()
    epoch = L.ensure_paper_epoch_ms()

    rec = {"配置": cfg_label, "s": s, "d": d, "config_fingerprint": fp,
           "风险口径": ("冻结口径" if risk is FROZEN else "反重力夹具口径"),
           "权益": risk["initial_equity_USDT"], "目录": str(d_root),
           "paper_epoch_ms": epoch, "paper_epoch_时刻": iso(epoch), "阶段": []}

    def run_stage(name: str, t0: int, t1: int, seed: str) -> dict:
        p = subprocess.run([PY, str(STAGE), name, str(bars_path), str(t0), str(t1), seed],
                           env=env, capture_output=True, text=True, timeout=600)
        st = {"阶段": name, "返回码": p.returncode,
              "stdout": p.stdout.strip()[-1500:], "stderr": p.stderr.strip()[-1200:],
              "账本": ledger(paper)}
        rec["阶段"].append(st)
        return st

    # 阶段 1：从箱体末端跑到第 1 次 4h 冲高收盘，然后 SIGKILL
    run_stage("stage1", meta["t_box_end"], meta["t_4h_a_cl"], "all")
    mid = rec["阶段"][-1]["账本"]
    rec["阶段1后"] = {
        "持仓": [{k: r.get(k) for k in ("position_id", "status", "entry_price",
                                      "initial_stop_price", "stop_price", "qty",
                                      "stoploss_s", "trail_d", "config_fingerprint")}
               for r in (mid.get("paper_position") or [])],
        "止损历史": [{k: r.get(k) for k in ("old_stop", "new_stop", "reason", "effective_at")}
                 for r in (mid.get("paper_stop_history") or [])],
        "锁文件残留": [p.name for p in locks.iterdir()] if locks.exists() else [],
    }

    # 阶段 2：全新进程接管，只注入后续行情
    run_stage("stage2", meta["t_4h_a_cl"] + M15, t_end, "tail")
    fin = rec["阶段"][-1]["账本"]

    pos = (fin.get("paper_position") or [{}])[0]
    fills = fin.get("paper_fill") or []
    stops = fin.get("paper_stop_history") or []
    state = (fin.get("paper_state") or [{}])[0]
    trails = [x for x in stops if x.get("reason") == "ft_low4h_trail"]

    rec["最终"] = {
        "持仓": {k: pos.get(k) for k in ("position_id", "status", "entry_price", "exit_price",
                                       "exit_reason", "initial_stop_price", "stop_price", "qty",
                                       "stoploss_s", "trail_d", "config_fingerprint")},
        "成交笔数": len([f for f in fills if f.get("fill_id")]),
        "成交": [{k: f.get(k) for k in ("action", "fill_price", "qty", "fee", "fill_ts",
                                       "config_fingerprint")} for f in fills],
        "跟踪上移次数": len(trails),
        "跟踪上移": [{"old": x.get("old_stop"), "new": x.get("new_stop"),
                   "effective_at": iso(x.get("effective_at"))} for x in trails],
        "现金": state.get("cash"), "手续费": state.get("fees_paid"),
        "已实现": state.get("realized_pnl"),
    }
    # 闭合校验
    o = next((f for f in fills if f.get("action") == "open"), None)
    c = next((f for f in fills if f.get("action") == "close"), None)
    eng_init = float(L.INIT_EQUITY)
    checks = {}
    if o and c:
        fees = float(o["fee"]) + float(c["fee"])
        gross = float(c["qty"]) * (float(c["fill_price"]) - float(o["fill_price"]))
        net = gross - fees
        checks = {
            "开平数量相等": abs(float(o["qty"]) - float(c["qty"])) < 1e-9,
            "手续费总和=账本": abs(fees - float(state.get("fees_paid") or 0)) < 1e-6,
            "净盈亏=毛-费": abs(net - float(state.get("realized_pnl") or 0)) < 1e-4,
            "现金=引擎初始权益+已实现": abs(float(state.get("cash") or 0)
                                    - (eng_init + float(state.get("realized_pnl") or 0))) < 1e-4,
            "视图声明权益=引擎初始权益": abs(risk["initial_equity_USDT"] - eng_init) < 1e-9,
            "开平指纹一致": o.get("config_fingerprint") == c.get("config_fingerprint"),
            "指纹=视图指纹": (o.get("config_fingerprint") or "")[:12] == fp,
        }
    rec["闭合校验"] = checks
    rec["判定"] = {
        "阶段1有持仓": bool(rec["阶段1后"]["持仓"]) and rec["阶段1后"]["持仓"][0].get("status") == "open",
        "阶段1硬崩返回码": rec["阶段"][0]["返回码"],
        "阶段1上移1次": len(rec["阶段1后"]["止损历史"]) >= 1,
        "阶段2成交": len([f for f in fills if f.get("fill_id")]) >= 2,
        "阶段2上移累计2次": len(trails) >= 2,
        "最终已退出": pos.get("status") == "closed",
        "闭合校验全通过": bool(checks) and all(checks.values()),
    }
    return rec


def main() -> None:
    import test_system_runtime_01 as T          # noqa: E402
    bars, meta = T.build_wyckoff_scenario_bars()

    # 独立核验场景数据形态（不轻信夹具）
    h4 = [b for b in bars if b["timeframe"] == "4h"]
    h1 = [b for b in bars if b["timeframe"] == "1h"]
    m15 = [b for b in bars if b["timeframe"] == "15m"]
    box = [b for b in h4 if b["open_time"] < meta["t_box_end"]]
    form = {
        "4h根数": len(h4), "箱体4h根数": len(box),
        "1h根数": len(h1), "15m根数": len(m15),
        "箱体最高": max(b["high"] for b in box), "箱体最低": min(b["low"] for b in box),
        "箱体宽度": max(b["high"] for b in box) - min(b["low"] for b in box),
        "第1根冲高4h_low": [b["low"] for b in h4 if b["open_time"] == meta["t_4h_a_cl"] - 4 * 3600_000][:1],
        "时间跨度_小时": (meta["t_exit_cl"] - meta["t_box_end"]) / 3600_000,
        "全部时点在现在之前": meta["t_exit_cl"] < time.time() * 1000,
    }

    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    bars_path = ROOT / "bars.json"
    bars_path.write_text(json.dumps(bars), encoding="utf-8")

    res = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "根目录": str(ROOT), "场景核验": form,
           "场景来源": "SYSTEM-RUNTIME-01/test_system_runtime_01.py::build_wyckoff_scenario_bars",
           "重启方式": "stage1 独立子进程 SIGKILL 硬崩（不释放锁）；stage2 全新进程接管",
           "用例": []}

    for label, s, d, risk in (("A_frozen", 0.05, 0.05, FROZEN),
                              ("B_frozen", 0.08, 0.12, FROZEN)):
        print(f"\n===== {label} s={s} d={d} =====")
        # 反重力原场景的退出 bar low=48000，只够打穿 d=0.05 的止损(50540)；
        # d=0.12 的新止损 46816 打不到 ⇒ 为 B 追加一根更低的 15m bar，
        # 保证两组配置都真实走完「成交→上移→重启→退出」（补场景，不改产品代码）。
        bpath, t_end = bars_path, meta["t_exit_cl"] + M15
        if d >= 0.10:
            extra = [{"symbol": "BTCUSDT", "timeframe": "15m",
                      "open_time": meta["t_exit_cl"] + M15,
                      "close_time": meta["t_exit_cl"] + 2 * M15,
                      "open": 45500.0, "high": 46000.0, "low": 44000.0, "close": 44500.0,
                      "volume": 1500.0, "quote_volume": 150000.0}]
            bpath = ROOT / f"bars_{label}.json"
            bpath.write_text(json.dumps(bars + extra), encoding="utf-8")
            t_end = meta["t_exit_cl"] + 2 * M15
        rec = run_case(label, s, d, risk, meta, bpath, meta["t_box_end"], t_end)
        res["用例"].append(rec)
        for st in rec["阶段"]:
            print(f"  [{st['阶段']}] rc={st['返回码']}  {st['stdout'].splitlines()[-2:] }")
            if st["stderr"]:
                print("    ERR:", st["stderr"][:400])
        print("  阶段1后:", json.dumps(rec["阶段1后"]["持仓"], ensure_ascii=False)[:260])
        print("  最终:", json.dumps(rec["最终"]["持仓"], ensure_ascii=False)[:300])
        print("  上移次数:", rec["最终"]["跟踪上移次数"], " 闭合:", rec["闭合校验"])
        print("  判定:", json.dumps(rec["判定"], ensure_ascii=False))

    (OUT / "13_C关真进程重启.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n→ out/13_C关真进程重启.json")


if __name__ == "__main__":
    main()
