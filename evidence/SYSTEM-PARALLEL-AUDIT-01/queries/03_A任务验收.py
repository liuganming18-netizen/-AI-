# -*- coding: utf-8 -*-
"""03 A 任务独立验收（SYSTEM-PARALLEL-AUDIT-01 并行工作 3 之一）。

A 任务验收重点（工单原文）
--------------------------
「A：本金与风险参数真实生效，已有账户不重置，缺配置拒新单且不阻断退出。」

三个可机械判定的命题：

    A1  本金与风险参数**真实生效**
        视图写 2000/5000/20000 ⇒ 空账户的 equity() 与下单数量必须随之变化。
        判据：qty ≈ (视图本金 × risk_per_trade) ÷ stop_dist，误差 < 1 个数量步长。

    A2  已有账户**不重置**
        先用 5000 建账并持仓，再把视图改成 20000，**同一账本**：
        · account_view().initial_equity 必须仍是 5000（不被改写）
        · cash 不得跳变成 20000（不注资）
        · 带 expected=20000 打开账本必须**拒绝**

    A3  缺配置**拒新单**且**不阻断退出**
        视图指向不存在的文件 ⇒ approved_params()._config_valid = False
        · open_position 必须拒绝，reason 指向配置
        · 已有持仓仍能正常平仓（账本闭合）

⚠ 为什么必须用子进程
--------------------
`live_store.py:54` 有 `LEDGER_DIR = get_ledger_dir()`，是**模块级常量**，在
import 那一刻就固定了。如果先 `import live_store` 再改 `PAPER_LEDGER_DIR`，
`paper_conn()` 仍会用 import 时的路径（默认 `数据/paper`）——**隔离失效，
会写生产账本**。

我第一版就踩了这个坑（12:07 那次运行）。补救方式：每个场景起一个**独立
子进程**，环境变量在进程启动前设好，模块常量自然正确。
已验证：那次运行未在生产账本留下任何 `sig_a1_*` 痕迹（见 out/03_污染自查.json）。

产物：out/03_A任务验收.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_a_task")
PY = sys.executable

M15 = 900_000
SYM = "BTCUSDT"

FILES = [
    "框架/backend/live_store.py",
    "框架/backend/paper_engine.py",
    "框架/backend/strategy_contracts.py",
    "框架/backend/strategies/wx_execution.py",
    "框架/backend/live_api.py",
]


def view_payload(eq: float) -> dict:
    """视图骨架：除 initial_equity_USDT 外全部用冻结规则值，避免混入别的变量。"""
    return {
        "schema_version": "research_policy_2.2",
        "spec_version": "PARALLEL_A_TASK_PROBE",
        "strategy_version": "FT-LOW4H-A-TASK",
        "model_version": "FT-LOW4H-A-TASK",
        "purpose": "audit_probe",
        "production_config": False,
        "live_trading_authorized": False,
        "execution": {"detail_timeframe": "15m", "signal_execution_delay_minutes": 15,
                      "fee_each_side": 0.0005, "slippage_each_side": 0.0003,
                      "signal_exit_delay_minutes": 15},
        "stops": {"rule": "FT-LOW4H", "stoploss_s": 0.05, "trail_d": 0.05,
                  "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
                  "max_holding_hours": 240, "trailing_enabled": True},
        "risk": {"initial_equity_USDT": eq,
                 "risk_per_trade_equity_fraction": 0.005,
                 "leverage": 1, "max_positions": 3,
                 "single_name_notional_equity_cap": 0.30,
                 "gross_long_notional_equity_cap": 0.90,
                 "cash_or_free_margin_reserve_fraction": 0.10,
                 "portfolio_initial_stop_risk_cap": 0.015,
                 "entry_participation_previous_15m_quote_cap": 0.001,
                 "maximum_account_drawdown_fraction": 0.20,
                 "short_enabled": False},
    }


def sha_of(rel: str) -> dict:
    p = BASE / rel
    b = p.read_bytes()
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))}


def build_market(market: pathlib.Path, price: float = 100.0) -> None:
    if market.exists():
        market.unlink()
    con = sqlite3.connect(market)
    con.executescript("""
    CREATE TABLE candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE funding_history(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    now = int(time.time() * 1000)
    b = (now // M15) * M15
    # quote_volume 必须足够大，否则「参与率帽」(0.1% × 前一根 15m 成交额)
    # 会先于「风险预算」触发，导致 qty 被截成同一个值 ⇒ 测不出本金差异。
    # 取 5e9 USDT/根 ⇒ 帽 = 5e6，远大于最大测试预算 20000×0.005=100 USDT。
    for k in range(-40, 0):
        ot = b + k * M15
        con.execute("INSERT INTO candle_versions VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (SYM, "15m", ot, ot + M15 - 1, f"{price:.2f}", f"{price*1.005:.2f}",
                     f"{price*0.995:.2f}", f"{price:.2f}", "50000", "5000000000", 10,
                     "25000", "2500000000", 1, ot + M15 - 1))
        con.execute("INSERT INTO candle_heads VALUES('binance','um',?,?,?,?)", (SYM, "15m", ot, 1))
    con.commit()
    con.close()


def mk_env(tag: str) -> dict:
    root = ROOT / tag
    if root.exists():
        shutil.rmtree(root)
    for d in ("paper", "live", "locks"):
        (root / d).mkdir(parents=True, exist_ok=True)
    market = root / "market.sqlite3"
    build_market(market)
    (root / "gate.json").write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    return {"root": root, "paper": root / "paper", "live": root / "live",
            "locks": root / "locks", "market": market, "gate": root / "gate.json"}


def env_vars(env: dict, view: pathlib.Path | None) -> dict:
    e = dict(os.environ)
    e.update({
        "PAPER_LEDGER_DIR": str(env["paper"]),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "PAPER_LOCK_DIR": str(env["locks"]),
        "LIVE_DIR": str(env["live"]),
        "LIVE_DB": str(env["live"] / "live_signals.sqlite3"),
        "MARKET_DB": str(env["market"]),
        "PAPER_GATE_FILE": str(env["gate"]),
        "PYTHONUNBUFFERED": "1",
    })
    e.pop("PAPER_INITIAL_EQUITY", None)
    if view is None:
        e.pop("EFFECTIVE_VIEW", None)
    else:
        e["EFFECTIVE_VIEW"] = str(view)
    return e


# ══════════════════════════════════════════════════════════════════════════
# worker：在独立子进程里跑一个场景，环境变量在 import 前已就绪
# ══════════════════════════════════════════════════════════════════════════
def fill_clock() -> tuple[int, int, int]:
    """算出让挂单**真实成交**所需的三元时刻 (t_req, t_now, b)。

    成交条件（见 `paper_engine.open_position` 时钟段）：
        order_request + delay <= bar_open <= attempt, 且 attempt - bar_open <= 1h
    而 `bar = bar_at_or_after(earliest_fill)` 取第一根 `open_time >= earliest_fill`。
    取 t_req = b-4·M15、t_now = b-2·M15 ⇒ earliest = b-3·M15，
    市场里恰有 open_time = b-3·M15 的 bar，lag = 1 根 15m < 1h。
    """
    now = int(time.time() * 1000)
    b = (now // M15) * M15
    return b - 4 * M15, b - 2 * M15, b


def worker_a1(eq: float) -> dict:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import live_store as L
    import paper_engine as PE

    out = {"场景": f"A1_eq_{int(eq)}", "视图本金": eq,
           "LEDGER_DIR": str(L.LEDGER_DIR), "INIT_EQUITY": L.INIT_EQUITY,
           "get_configured_initial_equity": None}
    try:
        out["get_configured_initial_equity"] = L.get_configured_initial_equity()
    except Exception as e:
        out["get_configured_initial_equity"] = f"ERR {e}"

    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    p = PE.approved_params()
    out["_config_valid"] = p.get("_config_valid")
    out["approved_initial_equity_USDT"] = p.get("initial_equity_USDT")
    out["approved_stoploss_s"] = p.get("stoploss_s")

    t_req, t_now, _b = fill_clock()
    con = L.paper_conn("3a")
    try:
        av = PE.account_view("3a", con)
        eqrow = PE.equity(con, "3a", con, p)
        out["account_view.initial_equity"] = av.get("initial_equity")
        out["account_view.cash"] = av.get("cash")
        out["equity().equity"] = eqrow.get("equity")
        out["equity().initial_equity"] = eqrow.get("initial_equity")
        r = PE.open_position("3a", SYM, signal_uid=f"sig_a1_{int(eq)}", direction="long",
                             atr=1.0, now_ms=t_now, event_time=t_req,
                             available_at=t_req, requested_at=t_req,
                             first_request_ms=t_req, origin="replay")
        out["open_ok"] = r.get("ok")
        out["open_reason"] = r.get("reason")
        out["bar_open_time"] = r.get("bar_open_time")
        # 从落库结果读实际数量与止损（比读返回体更可信）
        pos = PE.open_positions(con, "3a")
        if pos:
            out["落库持仓数"] = len(pos)
            out["落库qty"] = float(pos[0]["qty"])
            out["落库entry"] = float(pos[0]["entry_price"])
            sp = pos[0]["initial_stop_price"] if pos[0]["initial_stop_price"] is not None \
                else pos[0]["stop_price"]
            out["落库stop"] = float(sp) if sp is not None else None
        else:
            out["落库持仓数"] = 0
    finally:
        con.close()
    return out


def worker_a2_phase1() -> dict:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import live_store as L
    import paper_engine as PE
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    p = PE.approved_params()
    con = L.paper_conn("3a")
    t_req, t_now, _b = fill_clock()
    try:
        r = PE.open_position("3a", SYM, signal_uid="sig_a2_open", direction="long",
                             atr=1.0, now_ms=t_now, event_time=t_req,
                             available_at=t_req, requested_at=t_req,
                             first_request_ms=t_req, origin="replay")
        av = PE.account_view("3a", con)
        eq = PE.equity(con, "3a", con, p)
        return {"阶段": "① 用 5000 建账并开仓", "open_ok": r.get("ok"),
                "open_reason": r.get("reason"),
                "持仓数": len(PE.open_positions(con, "3a")),
                "account_view.initial_equity": av.get("initial_equity"),
                "account_view.cash": av.get("cash"),
                "equity": eq.get("equity"),
                "LEDGER_DIR": str(L.LEDGER_DIR)}
    finally:
        con.close()


def worker_a2_phase2() -> dict:
    """视图已切到 20000，同一账本。"""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import live_store as L
    import paper_engine as PE
    p = PE.approved_params()
    out = {"阶段": "② 视图切 20000，同一账本", "LEDGER_DIR": str(L.LEDGER_DIR),
           "approved_initial_equity_USDT": p.get("initial_equity_USDT"),
           "get_configured_initial_equity": L.get_configured_initial_equity()}
    # 先不传 expected 打开，看账户是否被改写
    try:
        con = L.paper_conn("3a", strict_identity=False)
        try:
            av = PE.account_view("3a", con)
            eq = PE.equity(con, "3a", con, p)
            out["account_view.initial_equity"] = av.get("initial_equity")
            out["account_view.cash"] = av.get("cash")
            out["equity"] = eq.get("equity")
        finally:
            con.close()
    except Exception as e:
        out["打开账本异常"] = f"{type(e).__name__}: {e}"[:300]

    # 再带 expected=20000 打开，应拒绝
    try:
        con = L.paper_conn("3a", strict_identity=True, initial_equity=20000.0)
        con.close()
        out["身份核对"] = {"raised": False, "结论": "未拒绝（疑似未校验）"}
    except Exception as e:
        out["身份核对"] = {"raised": True, "type": type(e).__name__,
                           "msg": str(e)[:300]}
    return out


def worker_a3() -> dict:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import live_store as L
    import paper_engine as PE
    out = {"场景": "A3", "LEDGER_DIR": str(L.LEDGER_DIR)}
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()

    # ① 缺配置下 approved_params
    p = PE.approved_params()
    out["approved_params"] = {
        "_config_valid": p.get("_config_valid"),
        "_config_error": p.get("_config_error"),
        "_source": p.get("_source"),
        "stoploss_s": p.get("stoploss_s"),
        "trail_d": p.get("trail_d"),
        "initial_equity_USDT": p.get("initial_equity_USDT"),
    }

    # ② 缺配置下开新单
    con = L.paper_conn("3a")
    t_req, t_now, _b = fill_clock()
    try:
        try:
            r = PE.open_position("3a", SYM, signal_uid="sig_a3_new", direction="long",
                                 atr=1.0, now_ms=t_now, event_time=t_req,
                                 available_at=t_req, requested_at=t_req,
                                 first_request_ms=t_req, origin="replay")
            out["开新单"] = {"ok": r.get("ok"), "reason": r.get("reason")}
            out["判定_拒新单"] = (r.get("ok") is False)
        except Exception as e:
            out["开新单"] = {"异常": f"{type(e).__name__}: {e}"[:300]}
            out["判定_拒新单"] = True

        # ③ 已有持仓能否在缺配置下正常退出
        pos = PE.open_positions(con, "3a")
        out["持仓数"] = len(pos)
        if pos:
            try:
                ex = PE.close_at_price("3a", int(pos[0]["position_id"]),
                                       raw_price=110.0, now_ms=t_now,
                                       reason="audit_force_exit")
                out["平仓"] = {k: str(v)[:100] for k, v in (ex or {}).items()}
                left = PE.open_positions(con, "3a")
                out["平仓后剩余持仓"] = len(left)
                out["判定_退出不阻断"] = (len(left) == 0)
            except Exception as e:
                out["平仓"] = {"异常": f"{type(e).__name__}: {e}"[:300]}
                out["判定_退出不阻断"] = False
        else:
            out["判定_退出不阻断"] = None
    finally:
        con.close()
    return out


def worker_a3_seed() -> dict:
    """在有效配置下开一笔仓，为 A3 的退出测试备料。"""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import live_store as L
    import paper_engine as PE
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    con = L.paper_conn("3a")
    t_req, t_now, _b = fill_clock()
    try:
        r = PE.open_position("3a", SYM, signal_uid="sig_a3_seed", direction="long",
                             atr=1.0, now_ms=t_now, event_time=t_req,
                             available_at=t_req, requested_at=t_req,
                             first_request_ms=t_req, origin="replay")
        return {"备料开仓": {"ok": r.get("ok"), "reason": r.get("reason")},
                "持仓数": len(PE.open_positions(con, "3a")),
                "LEDGER_DIR": str(L.LEDGER_DIR)}
    finally:
        con.close()


def run_worker(fn: str, args: list[str]) -> dict:
    """起子进程跑 worker，返回其 JSON。"""
    cmd = [PY, __file__, "--worker", fn] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       cwd=str(BASE))
    if p.returncode != 0:
        return {"worker_error": f"rc={p.returncode}",
                "stderr": (p.stderr or "")[-1500:], "stdout": (p.stdout or "")[-500:]}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"worker_error": "无法解析 JSON", "stdout": (p.stdout or "")[-800:]}


# ══════════════════════════════════════════════════════════════════════════
def main() -> None:
    # ---------- 污染自查（先做，任何结论之前）----------
    prod = BASE / "数据/paper"
    pollute = {"生产账本目录": str(prod), "文件": [], "探针痕迹": []}
    if prod.exists():
        for f in sorted(prod.glob("*.sqlite3")):
            st = f.stat()
            pollute["文件"].append({
                "name": f.name, "bytes": st.st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))})
            try:
                con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                tabs = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                for t in tabs:
                    cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
                    for c in cols:
                        try:
                            n = con.execute(
                                f"SELECT COUNT(*) FROM {t} "
                                f"WHERE CAST({c} AS TEXT) LIKE '%sig_a1_%' "
                                f"OR CAST({c} AS TEXT) LIKE '%sig_a2_%' "
                                f"OR CAST({c} AS TEXT) LIKE '%sig_a3_%'").fetchone()[0]
                        except Exception:
                            continue
                        if n:
                            pollute["探针痕迹"].append(f"{f.name}:{t}.{c}={n}")
                con.close()
            except Exception as e:
                pollute.setdefault("读取异常", []).append(f"{f.name}: {e}")
    pollute["判定"] = ("未污染" if not pollute["探针痕迹"]
                       else f"发现 {len(pollute['探针痕迹'])} 处探针痕迹，需处理")
    (OUT / "03_污染自查.json").write_text(
        json.dumps(pollute, ensure_ascii=False, indent=1), encoding="utf-8")

    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True, exist_ok=True)

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "A 任务独立验收：本金真实生效 / 已有账户不重置 / 缺配置拒新单不阻断退出",
        "边界": "只读业务代码；全部在 /tmp/wx_a_task 隔离环境；子进程保证模块常量正确",
        "隔离方式": "每个场景独立子进程，环境变量在 import 前注入（LEDGER_DIR 是模块级常量）",
        "文件SHA": {f: sha_of(f) for f in FILES},
        "污染自查": pollute,
        "场景": {},
    }

    # ================= A1 =================
    a1 = []
    for eq in (2000.0, 5000.0, 20000.0):
        env = mk_env(f"a1_{int(eq)}")
        view = env["root"] / f"view_{int(eq)}.json"
        view.write_text(json.dumps(view_payload(eq), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        # 用 env 参数跑子进程：需要把 env 传进去
        cmd_env = env_vars(env, view)
        cmd = [PY, __file__, "--worker", "a1", str(eq)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           cwd=str(BASE), env=cmd_env)
        if p.returncode != 0:
            a1.append({"场景": f"A1_eq_{int(eq)}", "视图本金": eq,
                       "worker_error": f"rc={p.returncode}",
                       "stderr": (p.stderr or "")[-1200:]})
            continue
        try:
            rec = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            a1.append({"场景": f"A1_eq_{int(eq)}", "视图本金": eq,
                       "worker_error": "解析失败", "stdout": (p.stdout or "")[-600:]})
            continue
        # 判定
        budget = eq * 0.005
        rec["预算_视图x0.005"] = budget
        rec["判定_equity随视图变"] = (
            rec.get("equity().equity") is not None
            and abs(float(rec["equity().equity"]) - eq) < 1e-6)
        rec["判定_account_view随视图变"] = (
            rec.get("account_view.initial_equity") is not None
            and abs(float(rec["account_view.initial_equity"]) - eq) < 1e-6)
        qty, stop = rec.get("落库qty"), rec.get("落库stop")
        if qty and stop:
            dist = abs(float(rec["落库entry"]) - float(stop))
            expect = budget / dist if dist > 0 else None
            rec["期望qty_预算除stop_dist"] = round(expect, 6) if expect else None
            rec["判定_qty按视图预算"] = (expect is not None
                                    and abs(float(qty) - expect) < 0.002)
        else:
            rec["期望qty_预算除stop_dist"] = None
            rec["判定_qty按视图预算"] = None
        a1.append(rec)

    ok_a1 = [x for x in a1 if x.get("判定_equity随视图变") and x.get("判定_qty按视图预算")]
    res["场景"]["A1_本金真实生效"] = a1
    res["场景"]["A1_结论"] = ("通过" if len(ok_a1) == 3 and len(a1) == 3
                             else ("不通过" if a1 else "未验证"))

    # ================= A2 =================
    env2 = mk_env("a2")
    v5000 = env2["root"] / "view_5000.json"
    v5000.write_text(json.dumps(view_payload(5000.0), ensure_ascii=False, indent=2),
                     encoding="utf-8")
    ph1 = run_worker_env("a2p1", env_vars(env2, v5000))
    v20000 = env2["root"] / "view_20000.json"
    v20000.write_text(json.dumps(view_payload(20000.0), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    ph2 = run_worker_env("a2p2", env_vars(env2, v20000))
    a2 = {"步骤": [ph1, ph2]}
    ie2 = ph2.get("account_view.initial_equity")
    cash2 = ph2.get("account_view.cash")
    a2["判定_账户未被改写"] = (ie2 is not None and abs(float(ie2) - 5000.0) < 1e-6)
    a2["判定_未注资"] = (cash2 is not None and float(cash2) < 15000.0)
    a2["判定_身份核对拒绝"] = bool((ph2.get("身份核对") or {}).get("raised"))
    a2["判定_合计"] = (a2["判定_账户未被改写"] and a2["判定_未注资"]
                      and a2["判定_身份核对拒绝"])
    res["场景"]["A2_已有账户不重置"] = a2
    res["场景"]["A2_结论"] = "通过" if a2["判定_合计"] else "不通过"

    # ================= A3 =================
    env3 = mk_env("a3")
    vok = env3["root"] / "view_5000.json"
    vok.write_text(json.dumps(view_payload(5000.0), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    seed = run_worker_env("a3seed", env_vars(env3, vok))
    # 视图指向不存在的文件（注意：unset 会回落工程内默认视图，那是有效配置，
    # 必须用一个确实不存在的路径才能模拟「缺配置」）
    absent = env3["root"] / "view_absent.json"
    a3 = run_worker_env("a3", env_vars(env3, absent))
    a3["备料"] = seed
    a3["缺配置路径"] = str(absent)
    a3["缺配置路径存在"] = absent.exists()
    res["场景"]["A3_缺配置拒新单不阻断退出"] = a3
    res["场景"]["A3_结论"] = (
        "通过" if (a3.get("判定_拒新单") and a3.get("判定_退出不阻断"))
        else ("部分通过" if a3.get("判定_拒新单") else "不通过"))

    # ================= 总判定 =================
    res["A任务总判定"] = {
        "A1_本金真实生效": res["场景"]["A1_结论"],
        "A2_已有账户不重置": res["场景"]["A2_结论"],
        "A3_缺配置拒新单不阻断退出": res["场景"]["A3_结论"],
        "合计": "通过" if all(res["场景"][k] == "通过"
                             for k in ("A1_结论", "A2_结论", "A3_结论"))
                else "不通过",
        "说明": "任一子项不通过即整体不通过；不得把局部通过升级为系统通过",
    }

    (OUT / "03_A任务验收.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    print(f"污染自查：{pollute['判定']}")
    for x in pollute["探针痕迹"]:
        print(f"  !! {x}")
    print(f"\n文件 SHA：{len(res['文件SHA'])} 个")
    print("\n=== A1 本金真实生效 ===")
    for x in a1:
        if "worker_error" in x:
            print(f"  视图 {x['视图本金']:>8.0f}  WORKER ERROR {x['worker_error']}")
            print(f"    {x.get('stderr','')[:300]}")
            continue
        print(f"  视图 {x['视图本金']:>8.0f} valid={x.get('_config_valid')} "
              f"LEDGER={x.get('LEDGER_DIR')}")
        print(f"    av.initial={x.get('account_view.initial_equity')} "
              f"eq.equity={x.get('equity().equity')} "
              f"qty={x.get('落库qty')} 期望={x.get('期望qty_预算除stop_dist')}")
        print(f"    equity随视图变={x.get('判定_equity随视图变')} "
              f"qty按视图预算={x.get('判定_qty按视图预算')}")
    print(f"  → {res['场景']['A1_结论']}")
    print("\n=== A2 已有账户不重置 ===")
    for s in a2["步骤"]:
        print(f"  {json.dumps(s, ensure_ascii=False)[:300]}")
    print(f"  账户未被改写={a2['判定_账户未被改写']} 未注资={a2['判定_未注资']} "
          f"身份核对拒绝={a2['判定_身份核对拒绝']} → {res['场景']['A2_结论']}")
    print("\n=== A3 缺配置拒新单不阻断退出 ===")
    print(f"  approved_params: {json.dumps(a3.get('approved_params'), ensure_ascii=False)}")
    print(f"  开新单: {json.dumps(a3.get('开新单'), ensure_ascii=False)[:240]}")
    print(f"  平仓: {json.dumps(a3.get('平仓'), ensure_ascii=False)[:240]}")
    print(f"  拒新单={a3.get('判定_拒新单')} 退出不阻断={a3.get('判定_退出不阻断')} "
          f"→ {res['场景']['A3_结论']}")
    print(f"\n=== A 任务总判定：{res['A任务总判定']['合计']} ===")
    print("\n→ out/03_A任务验收.json")


def run_worker_env(fn: str, env: dict) -> dict:
    cmd = [PY, __file__, "--worker", fn]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       cwd=str(BASE), env=env)
    if p.returncode != 0:
        return {"worker_error": f"rc={p.returncode}",
                "stderr": (p.stderr or "")[-1200:], "stdout": (p.stdout or "")[-400:]}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"worker_error": "无法解析 JSON", "stdout": (p.stdout or "")[-800:]}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        fn = sys.argv[2]
        if fn == "a1":
            print(json.dumps(worker_a1(float(sys.argv[3])), ensure_ascii=False))
        elif fn == "a2p1":
            print(json.dumps(worker_a2_phase1(), ensure_ascii=False))
        elif fn == "a2p2":
            print(json.dumps(worker_a2_phase2(), ensure_ascii=False))
        elif fn == "a3seed":
            print(json.dumps(worker_a3_seed(), ensure_ascii=False))
        elif fn == "a3":
            print(json.dumps(worker_a3(), ensure_ascii=False))
        else:
            print(json.dumps({"error": f"unknown worker {fn}"}))
            sys.exit(2)
    else:
        main()
