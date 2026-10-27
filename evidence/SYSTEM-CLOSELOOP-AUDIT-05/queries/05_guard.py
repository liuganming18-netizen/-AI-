# -*- coding: utf-8 -*-
"""05 第 5 项：行情来源可追溯 —— 假K线 / 未来数据 / 错误标的 / 外部参数覆盖
是否会改变执行状态（止损价 / 轨迹）。

方法：在隔离实例（adapter:8799，账本 /tmp/wx_audit05）上准备一笔夹具持仓
（entry=100、stop=85），然后逐类注入，**每次都把止损重置回 85.0**，看注入后止损是否被改动。

产物：out/05_行情防线.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = EVID / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_audit05")
PAPER = ROOT / "paper"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
PORT = 8799
ARM = "3a"
SYM = "BTCUSDT"
M15 = 900_000
H4 = 14_400_000

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PORT": str(PORT),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
    "PAPER_LOCK_DIR": str(ROOT / "locks"),
    "PYTHONUNBUFFERED": "1",
}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:                                    # noqa: BLE001
            return e.code, {"_raw": raw[:400]}
    except Exception as exc:                                 # noqa: BLE001
        return -1, {"_error": str(exc)}


def ledger():
    con = sqlite3.connect(PAPER / f"paper_{ARM}.sqlite3")
    con.row_factory = sqlite3.Row
    return con


def fixture_position() -> int:
    """夹具持仓：entry=100、stop=85、qty=10（只用于检验注入是否会改执行状态）。"""
    con = ledger()
    try:
        con.execute("DELETE FROM paper_position")
        con.execute("DELETE FROM paper_stop_history")
        con.execute("DELETE FROM paper_order")
        con.execute("DELETE FROM paper_fill")
        con.execute("DELETE FROM paper_pending")
        now = int(time.time() * 1000)
        con.execute(
            "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,"
            "signal_uid,arm,stop_price,atr_at_entry,entry_fee,model_version,origin,epoch_ms) "
            "VALUES(?,?,?,?,?,'open',?,?,?,?,?,'fixture','fixture',?)",
            (f"paper43_{ARM}", SYM, 10.0, 100.0, now - 3 * H4, "fixture_pos_05", ARM,
             85.0, 1.0, 0.05, 1791939600000))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            "INSERT INTO paper_stop_history(account_id,position_id,old_stop,new_stop,reason,"
            "ref_bar_open_ms,available_at,decision_at,effective_at,created_at) "
            "VALUES(?,?,NULL,?,'initial_stop',?,?,?,?,?)",
            (f"paper43_{ARM}", pid, 85.0, now - 3 * H4, now - 3 * H4, now - 3 * H4,
             now - 3 * H4, now))
        con.commit()
        return int(pid)
    finally:
        con.close()


def reset_stop(pid: int) -> None:
    con = ledger()
    try:
        con.execute("UPDATE paper_position SET stop_price=85.0, status='open', exit_ts=NULL, "
                    "exit_reason=NULL, exit_price=NULL WHERE position_id=?", (pid,))
        con.execute("DELETE FROM paper_stop_history WHERE position_id=? AND reason<>'initial_stop'", (pid,))
        con.commit()
    finally:
        con.close()


def stop_of(pid: int) -> float:
    con = ledger()
    try:
        r = con.execute("SELECT stop_price,status FROM paper_position WHERE position_id=?", (pid,)).fetchone()
        return {"stop": float(r["stop_price"]), "status": r["status"]} if r else {}
    finally:
        con.close()


def trails(pid: int) -> int:
    con = ledger()
    try:
        return con.execute("SELECT COUNT(*) FROM paper_stop_history WHERE position_id=? "
                           "AND reason='ft_low4h_trail'", (pid,)).fetchone()[0]
    finally:
        con.close()


def main() -> None:
    for k, v in ENV.items():
        os.environ[k] = v
    log = open(OUT / "05_iso_adapter.log", "w", encoding="utf-8")
    srv = subprocess.Popen([PY, str(EVID / "queries/_iso_adapter.py")], cwd=str(BACKEND),
                           env={**os.environ}, stdout=log, stderr=subprocess.STDOUT,
                           start_new_session=True)
    res: dict = {"生成时刻": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "隔离实例": f"127.0.0.1:{PORT}"}
    try:
        ok = False
        for _ in range(60):
            code, _b = http("GET", "/api/health", timeout=3)
            if code == 200:
                ok = True
                break
            time.sleep(0.5)
        res["实例就绪"] = ok
        if not ok:
            raise SystemExit("隔离实例未就绪，见 out/05_iso_adapter.log")

        pid = fixture_position()
        res["夹具持仓"] = {"position_id": pid, "entry": 100.0, "stop": 85.0}
        now = int(time.time() * 1000)

        # 记录基准 4h bar 与取整步长（解释 92.15 与 92.10 的差）
        sys.path.insert(0, str(BACKEND))
        import paper_engine as PE
        con_mkt = sqlite3.connect(MARKET)
        con_mkt.row_factory = sqlite3.Row
        b4 = dict(con_mkt.execute("SELECT open_time,close_time,open,high,low,close FROM candle_versions "
                                  "WHERE symbol=? AND timeframe='4h' ORDER BY close_time DESC LIMIT 1",
                                  (SYM,)).fetchone())
        con_mkt.close()
        lim = PE.contract_limits(SYM) or {}
        res["基准4h"] = b4
        res["price_step"] = lim.get("price_step")
        res["理论候选"] = {"d=0.05": float(b4["low"]) * 0.95, "d=0.001": float(b4["low"]) * 0.999,
                       "说明": "候选价会按 price_step 向下取整保护"} 

        cases = []

        def run(name: str, body: dict, expect: str, allow_move: bool = False):
            reset_stop(pid)
            before = stop_of(pid)
            code, resp = http("POST", "/api/v1/paper/advance_trailing", body)
            time.sleep(0.2)
            after = stop_of(pid)
            item = (resp.get("items") or [{}])[0] if isinstance(resp.get("items"), list) else {}
            rec = {
                "用例": name, "期望": expect,
                "HTTP": code,
                "ok": item.get("ok"), "reason": item.get("reason"),
                "candidate_stop": item.get("candidate_stop"),
                "stop_updated": item.get("stop_updated"),
                "止损_注入前": before.get("stop"), "止损_注入后": after.get("stop"),
                "持仓状态_注入后": after.get("status"),
                "轨迹条数": trails(pid),
                "missing_reason": resp.get("missing_reason"),
                "quality_status": (resp.get("quality") or {}).get("status"),
                "响应摘要": json.dumps(resp, ensure_ascii=False)[:420],
            }
            if allow_move:      # 基线：本来就该动
                rec["判定"] = "通过" if rec["止损_注入后"] != before.get("stop") else "不通过"
            else:               # 反例：不得动
                rec["判定"] = "通过" if rec["止损_注入后"] == before.get("stop") else "不通过"
            cases.append(rec)
            print(json.dumps(rec, ensure_ascii=False))

        # 结构合法的伪造 K 线（high>=max(o,c)、low<=min(o,c)、正价格、open<close）
        FAKE = {"open": 200.0, "high": 210.0, "low": 200.0, "close": 205.0}

        # A 基线：不带 candle_4h（走库里最后一根已闭合 4h），显式 trail_d
        run("A 基线（库里4h + trail_d=0.05）", {"arm": ARM, "position_id": pid, "trail_d": 0.05},
            "止损应上移到「库里最近已闭合 4h 低价 × 0.95」（允许取整差）", allow_move=True)
        # 用同一根 bar 复算期望值，避免把「动了」当成「动对了」
        con_m = sqlite3.connect(MARKET)
        con_m.row_factory = sqlite3.Row
        _b = dict(con_m.execute("SELECT low FROM candle_versions WHERE symbol=? AND timeframe='4h' "
                                "AND close_time<=? ORDER BY close_time DESC LIMIT 1",
                                (SYM, int(time.time() * 1000))).fetchone())
        con_m.close()
        expect_stop = float(_b["low"]) * (1 - 0.05)
        got = cases[-1]["止损_注入后"]
        cases[-1]["期望止损"] = round(expect_stop, 4)
        cases[-1]["判定"] = "通过" if abs(float(got) - expect_stop) <= 0.02 else "不通过"

        # B 假 K 线：库里不存在、结构合法、low=200 ⇒ 候选止损 = 190
        run("B 假K线（库中不存在，结构合法，low=200）",
            {"arm": ARM, "position_id": pid, "trail_d": 0.05,
             "candle_4h": {"symbol": SYM, "open_time": now - 2 * H4, "close_time": now - H4 - 1, **FAKE}},
            "止损不得被改动")

        # C 未来数据：close_time 在 now 之后
        run("C 未来K线（close_time>now，结构合法）",
            {"arm": ARM, "position_id": pid, "trail_d": 0.05,
             "candle_4h": {"symbol": SYM, "open_time": now, "close_time": now + H4, **FAKE}},
            "止损不得被改动")

        # D 错误标的
        run("D 错误标的（ETHUSDT 的K线推 BTCUSDT 持仓）",
            {"arm": ARM, "position_id": pid, "trail_d": 0.05,
             "candle_4h": {"symbol": "ETHUSDT", "open_time": now - 2 * H4,
                           "close_time": now - H4 - 1, **FAKE}},
            "止损不得被改动")

        # D2 错误标的（用库里**真实存在**的 ETHUSDT 4h bar，绕开「未登记」这条，专测标的一致性）
        con_e = sqlite3.connect(MARKET)
        con_e.row_factory = sqlite3.Row
        eth = con_e.execute("SELECT open_time,close_time,open,high,low,close FROM candle_versions "
                            "WHERE symbol='ETHUSDT' AND timeframe='4h' ORDER BY close_time DESC LIMIT 1").fetchone()
        con_e.close()
        if eth:
            run("D2 错误标的（库里真实存在的 ETHUSDT 4h bar）",
                {"arm": ARM, "position_id": pid, "trail_d": 0.05,
                 "candle_4h": {"symbol": "ETHUSDT", **dict(eth)}},
                "止损不得被改动，应报 symbol_mismatch_with_position")

        # E 结构非法（high<low）
        run("E 结构非法（high<low）",
            {"arm": ARM, "position_id": pid, "trail_d": 0.05,
             "candle_4h": {"symbol": SYM, "open_time": now - 2 * H4, "close_time": now - H4 - 1,
                           "open": 100.0, "high": 90.0, "low": 200.0, "close": 205.0}},
            "止损不得被改动")

        # F 外部参数覆盖：调用方把 trail_d 从 0.05 改成 0.001（候选更高）
        run("F 外部参数覆盖（trail_d=0.001，配置应为 0.05）",
            {"arm": ARM, "position_id": pid, "trail_d": 0.001},
            "止损不得因调用方改参数而变（应仍≈92.1）")

        # G 不传 trail_d：应明确失败，不静默兜底
        run("G 缺 trail_d（应显式失败）", {"arm": ARM, "position_id": pid},
            "ok=false 且 reason=missing_parameter_trail_d")

        # H 不传 position_id（整臂推进）+ 假 K 线
        reset_stop(pid)
        code, resp = http("POST", "/api/v1/paper/advance_trailing",
                          {"arm": ARM, "trail_d": 0.05,
                           "candle_4h": {"symbol": SYM, "open_time": now - 2 * H4,
                                         "close_time": now - H4 - 1, **FAKE}})
        time.sleep(0.2)
        after = stop_of(pid)
        cases.append({"用例": "H 整臂推进 + 假K线", "期望": "止损不得被改动",
                      "HTTP": code, "止损_注入后": after.get("stop"),
                      "判定": "通过" if after.get("stop") == 85.0 else "不通过",
                      "响应摘要": json.dumps(resp, ensure_ascii=False)[:300]})

        res["用例"] = cases
        res["统计"] = {"总": len(cases),
                     "通过": sum(1 for c in cases if c["判定"] == "通过"),
                     "不通过": sum(1 for c in cases if c["判定"] == "不通过")}

        # 止损轨迹正式路由
        code, resp = http("GET", f"/api/v1/paper/stops/{pid}?arm={ARM}")
        res["止损轨迹路由"] = {"HTTP": code, "total": resp.get("total"),
                          "quality": resp.get("quality"),
                          "首条": (resp.get("items") or [{}])[0] if resp.get("items") else None}
        code2, resp2 = http("GET", f"/api/v1/paper/stops/999999?arm={ARM}")
        res["止损轨迹路由_不存在持仓"] = {"HTTP": code2, "quality": resp2.get("quality"),
                                  "missing_reason": resp2.get("missing_reason")}

        # 守护状态（跨进程可见性）
        code3, resp3 = http("GET", f"/api/v1/paper/execution_daemon/status?arm={ARM}")
        res["守护状态接口"] = {"HTTP": code3, "item": resp3.get("item")}
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:                                    # noqa: BLE001
            srv.kill()
        log.close()

    (OUT / "05_行情防线.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")
    print("\n=== 统计 ===")
    print(json.dumps(res.get("统计"), ensure_ascii=False))
    print("轨迹路由:", json.dumps(res.get("止损轨迹路由"), ensure_ascii=False, default=str)[:300])
    print("不存在持仓:", json.dumps(res.get("止损轨迹路由_不存在持仓"), ensure_ascii=False, default=str)[:300])
    print("守护状态:", json.dumps(res.get("守护状态接口"), ensure_ascii=False, default=str)[:300])


if __name__ == "__main__":
    main()
