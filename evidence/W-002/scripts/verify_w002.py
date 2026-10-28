#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[W-002] AUDIT-003 遗留问题的独立只读核实（不修改任何生产代码）。

本脚本只回答三个问题，每条都给可复算的判据，不引用执行方自报：

  A 组：退出推进异常后，回测是否**真的熔断**（停止推进 / 停止开仓 / 停止写账本）？
        现状预期：只打 FAILED 标记，事件循环继续跑，后续时间戳照常进开仓入口。

  B 组：价格区间校验在**全仓范围内**的覆盖是否完整？
        （AUDIT-003-PATCH 自述「未做全仓静态证明」，本节用 AST 补齐）
        B1 写账本 SQL 的所在函数全集
        B2 所有 open_position / close_at_price / plan_entry 调用点的参数完整性
        B3 close_at_price 的容差 tol 放宽幅度

  D 组：单事件回放里「缺 low 导致退出判定不完整」是否阻断结论？
        现状预期：只计数上报，调用方从不读 ⇒ 运行仍判 OK。

用法（必须用带 numpy 的解释器）：
    /usr/bin/python3 evidence/W-002/scripts/verify_w002.py
产物：
    evidence/W-002/out/w002_verify.json
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
OUT = PROJECT / "evidence" / "W-002" / "out"
for _p in (str(BACKEND), str(PROJECT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RESULT: dict = {"A_熔断": {}, "B_价格校验覆盖": {}, "D_缺low不阻断": {}, "结论": {}}


# ══════════════════════════════════════════════════════════════════════
# 隔离沙盒（绝不碰生产账本 / 生产行情库）
# ══════════════════════════════════════════════════════════════════════
class Sandbox:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="w002_verify_"))
        self.market = self.dir / "market.sqlite3"
        self.live = self.dir / "live.sqlite3"
        self.ledger = self.dir / "paper"
        self.gate = self.dir / "gate.json"
        self.ledger.mkdir(parents=True, exist_ok=True)
        self._orig = os.environ.copy()

    def __enter__(self):
        os.environ["MARKET_DB"] = str(self.market)
        os.environ["LIVE_DB"] = str(self.live)
        os.environ["PAPER_LEDGER_DIR"] = str(self.ledger)
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
        os.environ["PAPER_GATE_FILE"] = str(self.gate)
        os.environ["ISOLATED_MODE"] = "1"
        os.environ.pop("WX_MARKET_MERGE", None)
        self.gate.write_text(json.dumps({"allow_new_entries": True,
                                         "reason": "w002_verify"}), encoding="utf-8")
        con = sqlite3.connect(str(self.market))
        try:
            con.execute("CREATE TABLE IF NOT EXISTS candles ("
                        "exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,"
                        "open_time INTEGER, close_time INTEGER, open REAL, high REAL,"
                        "low REAL, close REAL, quote_volume REAL, is_closed INTEGER,"
                        "available_at INTEGER)")
            con.execute("CREATE TABLE IF NOT EXISTS funding_history ("
                        "exchange TEXT, market TEXT, symbol TEXT, funding_time INTEGER,"
                        "funding_rate REAL, mark_price REAL, rate_type TEXT)")
            con.commit()
        finally:
            con.close()
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self._orig)
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def make_ledger_position(scheme: str, symbol: str, *, entry_price: float,
                         qty: float, entry_ts: int) -> int:
    """在隔离账本里落一笔 open 持仓（只造被测前提，不走撮合）。"""
    import live_store as L
    con = L.paper_conn(scheme, create=True, initial_equity=2000.0)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
        vals = {"account_id": f"paper43_{scheme}", "symbol": symbol, "qty": qty,
                "entry_price": entry_price, "entry_ts": entry_ts, "status": "open",
                "stop_price": entry_price * 0.95,
                "initial_stop_price": entry_price * 0.95,
                "atr_at_entry": 1.0, "entry_fee": 0.0,
                "signal_uid": f"w002_{symbol}", "arm": scheme, "trail_d": 0.05}
        keys = [k for k in vals if k in cols]
        sql = (f"INSERT INTO paper_position({','.join(keys)}) "
               f"VALUES({','.join('?' * len(keys))})")
        cur = con.execute(sql, tuple(vals[k] for k in keys))
        pid = int(cur.lastrowid)
        con.commit()
        return pid
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# A 组：退出推进异常 ⇒ 是否熔断
# ══════════════════════════════════════════════════════════════════════
def group_a() -> dict:
    import accum_early_entry as A

    out: dict = {}
    with Sandbox():
        scheme = "4a"
        t1, t2 = 1_700_000_000_000, 1_700_000_900_000
        pid = make_ledger_position(scheme, "AAAUSDT", entry_price=100.0,
                                   qty=1.0, entry_ts=t1 - 900_000)
        out["造出的持仓"] = {"scheme": scheme, "position_id": pid, "entry_ts": t1 - 900_000}

        calls = {"try_open": [], "drive": 0}

        def _boom(*a, **k):
            calls["drive"] += 1
            raise RuntimeError("注入：退出推进异常（W-002 A 组）")

        def _fake_try_open(scheme_, symbol_, cand_, cfg_, store_, **kw):
            # 记录「开仓入口被调用」的时刻，以及此刻运行状态是否已经 FAILED
            # 注意：必须读同一个 dict 对象，浅拷贝会看不到后续的状态变更。
            st = run_state_box.get("ref") or {}
            calls["try_open"].append({
                "symbol": symbol_,
                "entry_bar_open_ms": cand_.get("entry_bar_open_ms"),
                "run_status_at_call": st.get("status"),
            })
            return {"ok": False, "reason": "w002_probe_not_executed",
                    "called_paper_engine": False}

        run_state_box: dict = {}
        orig_drive, orig_try = A.drive_position_exit, A.try_open
        A.drive_position_exit = _boom
        A.try_open = _fake_try_open
        try:
            run_state = A.new_run_state()
            run_state_box["ref"] = run_state
            # 6 个时间戳：第 1 个时间戳就抛异常，后面 5 个是否还被送进开仓入口
            per_symbol = [
                {"symbol": f"S{i}USDT", "candidates": [
                    {"scheme": scheme, "signal_uid": f"u{i}",
                     "entry_bar_open_ms": t1 + i * 900_000}]}
                for i in range(6)
            ]
            exits, stats = A._replay_timeline(
                None, cfg={}, per_symbol=per_symbol, schemes=[scheme],
                store=None, legacy_4h_exit=False, run_state=run_state)
            out["run_status"] = run_state["status"]
            out["invalid_reasons"] = run_state["invalid_reasons"]
            out["drive_position_exit_调用次数"] = calls["drive"]
            out["try_open_调用次数"] = len(calls["try_open"])
            out["构造的时间戳数"] = 6
            out["首次异常后仍进入开仓入口的次数"] = sum(
                1 for c in calls["try_open"] if c.get("run_status_at_call") == "FAILED")
            out["try_open_明细"] = calls["try_open"]
            out["timeline_stats"] = stats
            out["异常记录条数"] = sum(1 for e in exits if e.get("reason") == "drive_error")
        finally:
            A.drive_position_exit = orig_drive
            A.try_open = orig_try

    out["判据"] = {
        "熔断是否成立": (out.get("try_open_调用次数", 0) == 0),
        "说明": ("try_open_调用次数=0 ⇒ 异常后不再进入开仓入口（真熔断）；"
                 ">0 ⇒ 只打了 FAILED 标记、事件循环继续，开仓入口仍被调用（未熔断）"),
    }
    return out


# ══════════════════════════════════════════════════════════════════════
# B 组：全仓静态证明（AST）
# ══════════════════════════════════════════════════════════════════════
LEDGER_TABLES = ("paper_realized", "paper_position", "paper_order",
                 "paper_fill", "paper_stop_history", "paper_pending")
GATE_FUNCS = ("open_position", "close_at_price", "close_position", "plan_entry")
TRADE_FUNCS = ("close_at_price", "open_position", "plan_entry")


def _iter_py(root: Path):
    for p in root.rglob("*.py"):
        s = str(p)
        if "__pycache__" in s or "/tests/" in s or "_legacy" in s:
            continue
        yield p


def _func_of(tree: ast.AST, lineno: int) -> str:
    best, best_line = "<module>", -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno and node.lineno > best_line:
                best, best_line = node.name, node.lineno
    return best


def group_b() -> dict:
    roots = [BACKEND, PROJECT / "策略"]
    writes, calls = [], []
    for root in roots:
        if not root.exists():
            continue
        for p in _iter_py(root):
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except Exception:
                continue
            rel = str(p.relative_to(PROJECT))
            for node in ast.walk(tree):
                # B1：写账本 SQL
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    v = node.value
                    if any(f"INTO {t}" in v or f"UPDATE {t}" in v for t in LEDGER_TABLES):
                        tables = [t for t in LEDGER_TABLES
                                  if f"INTO {t}" in v or f"UPDATE {t}" in v]
                        writes.append({"file": rel, "line": node.lineno,
                                       "func": _func_of(tree, node.lineno),
                                       "tables": tables,
                                       "sql_head": v.strip().split("\n")[0][:90]})
                # B2：撮合/计划函数调用点
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = None
                    if isinstance(fn, ast.Attribute):
                        name = fn.attr
                    elif isinstance(fn, ast.Name):
                        name = fn.id
                    if name in GATE_FUNCS:
                        kw = {k.arg for k in node.keywords if k.arg}
                        calls.append({
                            "file": rel, "line": node.lineno, "func": name,
                            "caller": _func_of(tree, node.lineno),
                            "has_bar_low": "bar_low" in kw,
                            "has_bar_high": "bar_high" in kw,
                            "kwargs": sorted(kw),
                        })
    # 容差
    tol_info = {}
    try:
        import inspect
        import replay_calc as RC
        import paper_engine as PE
        tol_info = {
            "validate_execution_price_in_bar_默认tol": inspect.signature(
                RC.validate_execution_price_in_bar).parameters["tol"].default,
            "close_at_price_默认tol": inspect.signature(
                PE.close_at_price).parameters["tol"].default,
            "scan_exit_默认tol": inspect.signature(RC.scan_exit).parameters["tol"].default,
        }
    except Exception as exc:                       # noqa: BLE001
        tol_info = {"error": repr(exc)}

    # 判定
    out = {"写账本SQL": writes, "撮合调用点": calls, "容差": tol_info}
    out["写账本所在文件数"] = len({w["file"] for w in writes})
    out["写账本所在函数"] = sorted({f'{w["file"]}::{w["func"]}' for w in writes})
    out["close_at_price调用点总数"] = sum(1 for c in calls if c["func"] == "close_at_price")
    out["close_at_price_缺高低价的调用点"] = [
        c for c in calls if c["func"] == "close_at_price"
        and not (c["has_bar_low"] and c["has_bar_high"])]
    out["plan_entry调用点总数"] = sum(1 for c in calls if c["func"] == "plan_entry")
    out["plan_entry_缺高低价的调用点"] = [
        c for c in calls if c["func"] == "plan_entry"
        and not (c["has_bar_low"] and c["has_bar_high"])]
    out["open_position调用点总数"] = sum(1 for c in calls if c["func"] == "open_position")
    out["说明_open_position"] = ("open_position 的高低价由函数内部从成交 bar 读取，"
                                 "调用点无需传参；需核的是函数体内门禁位置")
    return out


# ══════════════════════════════════════════════════════════════════════
# D 组：缺 low ⇒ 退出判定不完整是否阻断
# ══════════════════════════════════════════════════════════════════════
def group_d() -> dict:
    import replay_calc as RC
    M15 = 900_000
    entry_bar_ms = 1_700_000_000_000
    bars = [
        # 开盘没穿止损（open 110 > stop 95），但缺 low ⇒ 无法判盘中是否触及
        {"open_time": entry_bar_ms, "open": 110.0, "high": 112.0,
         "low": None, "close": 111.0},
        {"open_time": entry_bar_ms + M15, "open": 111.0, "high": 112.0,
         "low": 108.0, "close": 110.0},
    ]
    ex = RC.scan_exit(bars_15m=bars, entry_ms=entry_bar_ms - 1,
                      entry_bar_ms=entry_bar_ms, stop_path=[],
                      initial_stop=95.0, gap_model="bar_open")
    out = {
        "scan_exit返回": {k: ex.get(k) for k in
                         ("hit", "kind", "price_valid", "n_bars_no_low",
                          "n_bars_no_open", "note")},
    }
    # 谁消费 n_bars_no_low
    src = (BACKEND / "replay_calc.py").read_text(encoding="utf-8")
    consumers = []
    for i, line in enumerate(src.splitlines(), 1):
        if "n_bars_no_low" in line:
            consumers.append({"line": i, "text": line.strip()[:100]})
    out["n_bars_no_low_出现位置"] = consumers
    out["判据"] = {
        "缺low是否被判成未触及": ex.get("hit") is False,
        "是否阻断结论": bool(ex.get("reject_code")),
        "说明": ("hit=False 且 reject_code=None ⇒ 缺 low 的 bar 被当作「没触及」，"
                 "运行仍可判 OK；只有 note 文本提示，调用方不读该字段即不阻断"),
    }
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("A_熔断", group_a), ("B_价格校验覆盖", group_b),
                     ("D_缺low不阻断", group_d)):
        try:
            RESULT[name] = fn()
            print(f"[完成] {name}")
        except Exception as exc:                       # noqa: BLE001
            RESULT[name] = {"error": repr(exc), "trace": traceback.format_exc()[-1500:]}
            print(f"[异常] {name}: {exc!r}")

    a = RESULT.get("A_熔断") or {}
    b = RESULT.get("B_价格校验覆盖") or {}
    d = RESULT.get("D_缺low不阻断") or {}
    RESULT["结论"] = {
        "A_是否已熔断": a.get("判据", {}).get("熔断是否成立"),
        "A_try_open调用次数": a.get("try_open_调用次数"),
        "B_close_at_price缺参调用点数": len(b.get("close_at_price_缺高低价的调用点") or []),
        "B_plan_entry缺参调用点数": len(b.get("plan_entry_缺高低价的调用点") or []),
        "B_写账本文件数": b.get("写账本所在文件数"),
        "D_缺low是否阻断": d.get("判据", {}).get("是否阻断结论"),
    }
    (OUT / "w002_verify.json").write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(RESULT["结论"], ensure_ascii=False, indent=2))
    print(f"产物：{OUT / 'w002_verify.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
