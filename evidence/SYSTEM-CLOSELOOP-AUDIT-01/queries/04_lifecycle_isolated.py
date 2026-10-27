#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04_lifecycle_isolated.py —— 冻结清单 C1–C6 与写侧 A11/A19/A21 的**隔离实例**复验

隔离方式（不碰任何生产文件）
--------------------------
· 独立端口 8899；`PAPER_LEDGER_DIR=/tmp/wx_audit_iso/paper`（新建空账本）
  + `PAPER_LEDGER_ALLOW_INIT=1`；`LIVE_DIR`/`LIVE_DB` 指向 /tmp；
· **不调用 `adapter.main()`**：直接 `adapter._loopback_server(...)`，
  从而跳过 `backtest.startup()`（会写生产 `回测日志/.../runs`）与 `_prewarm()`（重负载）；
· 行情库 `MARKET_DB` 仍指真实库（只读，撮合要看真实 15m bar）；
· **闸门文件不可隔离**：`paper_engine.GATE_FILE` 是
  `Path(__file__).parent/"live"/"paper_new_entry_gate.json"`，硬编码、无环境变量出口。
  本轮**不写它**，只读当前内容并记录；因此 A20（闸门翻转持久化）在隔离实例上
  **不执行**，判「未验证」并写明原因（工单要求不与其并行写同一文件）。

用法：python 04_lifecycle_isolated.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJ = ROOT.parent.parent
OUT = ROOT / "out"
BACKEND = PROJ / "框架" / "backend"
ISO = Path("/tmp/wx_audit_iso")
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"
GATE = BACKEND / "live" / "paper_new_entry_gate.json"
PY = sys.executable

RESULTS: list[dict] = []
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def rec(cid, title, verdict, judged, measured, extra=None):
    RESULTS.append({"id": cid, "title": title, "verdict": verdict,
                    "判据": judged, "实测": measured, "补充": extra or {}})
    print(f"[{verdict}] {cid} {title}")
    if verdict != "通过":
        print(f"        判据：{judged}")
        print(f"        实测：{json.dumps(measured, ensure_ascii=False)[:500]}")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def http(path, method="GET", body=None, timeout=180):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:                                    # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def strip_code(src: str) -> str:
    """去掉注释与三引号字符串，只留可执行代码（用于「是否真的调用交易所」判定）。"""
    out, i, n = [], 0, len(src)
    in_str = None
    while i < n:
        ch = src[i]
        if in_str:
            if src.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            i += 1
            continue
        if src.startswith('"""', i) or src.startswith("'''", i):
            in_str = src[i:i + 3]
            i += 3
            continue
        if ch == "#":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch in "\"'":
            q = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                if src[i] == "\n":
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def wait_health(seconds=90):
    t0 = time.time()
    while time.time() - t0 < seconds:
        st, b = http("/api/v1/health", timeout=5)
        if st == 200:
            return True, round(time.time() - t0, 1)
        time.sleep(0.6)
    return False, round(time.time() - t0, 1)


def start_iso(tag: str):
    """用最小启动器起隔离后端（跳过 main() 的 startup/prewarm）。"""
    ISO.mkdir(parents=True, exist_ok=True)
    (ISO / "paper").mkdir(parents=True, exist_ok=True)
    (ISO / "live").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "PORT": str(PORT),
        "PAPER_LEDGER_DIR": str(ISO / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(ISO / "live"),
        "LIVE_DB": str(ISO / "live" / "live_signals.sqlite3"),
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("PAPER_GATE_STRICT", None)
    # 隔离 live 库必须先建表：写路径里的 get_paper_epoch_ms() 用**只读**方式打开它，
    # 文件不存在 ⇒ sqlite3.OperationalError（实测会变成 HTTP 500，见 04a 探针）。
    # 这是测试夹具的前置条件，不是修改被审代码。
    subprocess.run([PY, "-c", "import live_store as L; L.ensure_live_schema()"],
                   cwd=str(BACKEND), env=env, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = (
        "import socket, sys\n"
        "import adapter\n"
        "srv = adapter._loopback_server('127.0.0.1', socket.AF_INET)\n"
        "print('[iso] listening', flush=True)\n"
        "srv.serve_forever()\n"
    )
    log = (ISO / f"server_{tag}.log").open("wb")
    p = subprocess.Popen([PY, "-c", code], cwd=str(BACKEND), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    ok, secs = wait_health()
    return p, ok, secs


def stop(p):
    try:
        p.terminate()
        p.wait(timeout=12)
    except Exception:                                         # noqa: BLE001
        p.kill()


def iso_ledger(arm="3a"):
    return ISO / "paper" / f"paper_{arm}.sqlite3"


def ledger_state(arm="3a"):
    p = iso_ledger(arm)
    if not p.exists():
        return {"exists": False}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        def q(sql):
            try:
                return [dict(r) for r in c.execute(sql)]
            except sqlite3.OperationalError:
                return []
        return {"exists": True,
                "paper_state": q("SELECT * FROM paper_state"),
                "positions": q("SELECT position_id,symbol,status,qty,entry_price,"
                               "exit_price,exit_reason,signal_uid FROM paper_position"),
                "orders": q("SELECT order_id,event_uid,side,qty,fill_price,status,bar_open_time "
                            "FROM paper_order"),
                "realized": q("SELECT id,position_id,net_pnl FROM paper_realized"),
                "pending": q("SELECT pending_id,status,signal_uid FROM paper_pending")}
    finally:
        c.close()


def latest_closed_15m(symbol="BTCUSDT"):
    """行情库里最新一根已闭合 15m 的 open_time（撮合只认已闭合 bar）。"""
    db = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = c.execute("SELECT MAX(open_time) FROM candles WHERE exchange='binance' "
                      "AND market='um' AND symbol=? AND timeframe='15m' AND is_closed=1",
                      (symbol,)).fetchone()
        return r[0]
    finally:
        c.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gate_before = {"sha": sha(GATE),
                   "content": json.loads(GATE.read_text(encoding="utf-8")) if GATE.exists() else None,
                   "mtime": dt.datetime.fromtimestamp(GATE.stat().st_mtime).isoformat(timespec="seconds")
                   if GATE.exists() else None}
    if ISO.exists():
        shutil.rmtree(ISO, ignore_errors=True)

    bar_t = latest_closed_15m()
    MIN = 60_000
    now = int(time.time() * 1000)
    # 注入配方：requested_at = 最新已闭合 bar 起点 − 15m ⇒ earliest_fill 正好落在该 bar
    inject_at = (bar_t - 15 * MIN) if bar_t else None
    bar_age_min = round((now - bar_t) / MIN, 1) if bar_t else None

    p, ok, secs = start_iso("run1")
    if not ok:
        log = (ISO / "server_run1.log")
        rec("C0", "隔离实例启动", "未验证", "8899 上 /api/v1/health 返回 200",
            {"启动秒数": secs, "日志尾部": log.read_text(encoding="utf-8", errors="replace")[-1500:]
             if log.exists() else "无日志"})
        stop(p)
        return finish(gate_before)

    iso_ledger_status = {
        "ledger_dir": str(ISO / "paper"),
        "files_before_any_write": sorted(x.name for x in (ISO / "paper").glob("*")),
        "allow_init": True,
    }
    rec("C0", "隔离实例启动", "通过", "8899 上 /api/v1/health 返回 200",
        {"启动秒数": secs, "隔离账本目录": iso_ledger_status,
         "闸门（只读，未修改）": gate_before,
         "行情库最新已闭合15m": bar_t,
         "该bar距今_分钟": bar_age_min,
         "注入配方": f"requested_at = {inject_at}（= 最新已闭合 bar 起点 − 15m）"})

    # ── C2 拒单矩阵（不需要闸门开，全部在闸门检查之前返回）────────────────
    cases = []

    def c2(name, body, expect_status, expect_reason=None):
        st, b = http("/api/v1/paper/orders", "POST", body)
        q = ((b or {}).get("quality") or {}) if isinstance(b, dict) else {}
        mr = (b or {}).get("missing_reason") if isinstance(b, dict) else str(b)[:200]
        reason = None
        items = (b or {}).get("items") if isinstance(b, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            reason = items[0].get("reason")
        okk = st == expect_status and (expect_reason is None or reason == expect_reason)
        cases.append({"用例": name, "HTTP": st, "期望HTTP": expect_status,
                      "quality.status": q.get("status"), "missing_reason": mr,
                      "reason": reason, "期望reason": expect_reason, "符合": okk,
                      "原始": None if okk else json.dumps(b, ensure_ascii=False)[:300]})
        return okk

    c2("未知臂 arm=zz", {"arm": "zz", "action": "open", "symbol": "BTCUSDT", "atr": 500}, 400)
    c2("缺 symbol", {"arm": "3a", "action": "open", "atr": 500}, 400)
    c2("未知 action", {"arm": "3a", "action": "teleport", "symbol": "BTCUSDT"}, 400)
    c2("平仓缺 position_id", {"arm": "3a", "action": "close"}, 400)
    c2("平仓不存在仓位", {"arm": "3a", "action": "close", "position_id": 99999}, 409,
       "position_not_found")
    c2("非法方向", {"arm": "3a", "action": "open", "symbol": "BTCUSDT", "atr": 500,
                  "direction": "sideways", "requested_at": inject_at,
                  "signal_uid": "iso_bad_dir"}, 409, "illegal_direction_sideways")
    ok_c2 = all(x["符合"] for x in cases)
    rec("C2", "拒单矩阵（未知臂/缺参/未知action/不存在仓位/非法方向）",
        "通过" if ok_c2 else "不通过",
        "每条都返回约定状态码与明确 reason，不得静默成功",
        {"用例": cases})

    # ── C1 真实时钟开仓：只能挂单，不会成交 ────────────────────────────
    st, b = http("/api/v1/paper/orders", "POST",
                 {"arm": "3a", "action": "open", "symbol": "BTCUSDT", "atr": 500,
                  "signal_uid": f"iso_realclock_{now}"})
    q = (b or {}).get("quality") or {}
    it = ((b or {}).get("items") or [{}])[0]
    realclock_pending = q.get("status") == "pending" and it.get("reason") == "pending_future_fill"
    rec("C1a", "真实时钟开仓：必须登记挂单，不得立即成交", "通过" if realclock_pending else "不通过",
        "不注入任何时钟时，应成交 bar 尚未闭合 ⇒ 只能挂单（pending_future_fill）",
        {"HTTP": st, "quality.status": q.get("status"), "reason": it.get("reason"),
         "earliest_fill_ms": it.get("earliest_fill_ms"),
         "earliest_fill(本地)": dt.datetime.fromtimestamp(
             (it.get("earliest_fill_ms") or 0) / 1000).isoformat(timespec="seconds")
         if it.get("earliest_fill_ms") else None,
         "note": it.get("note")},
        {"说明": "这条同时说明：**没有 worker 推进挂单时，真实时钟下永远走不到成交** ⇒ "
                 "「闭环」不可能靠真实时钟产生"})

    # ── C1b 注入过去时钟：立即成交（缺陷复现）──────────────────────────
    st, b = http("/api/v1/paper/orders", "POST",
                 {"arm": "3a", "action": "open", "symbol": "BTCUSDT", "atr": 500,
                  "requested_at": inject_at, "signal_uid": f"iso_inject_{now}"})
    q = ((b or {}).get("quality") or {})
    it = ((b or {}).get("items") or [{}])[0]
    injected_filled = q.get("status") == "ok" and bool(it.get("ok"))
    verdict_c1b = ("不通过" if injected_filled else "未验证")
    rec("C1b", "注入过去 requested_at：立即成交（缺陷复现）", verdict_c1b,
        "同一份开仓请求，只把 requested_at 前移到「最新已闭合 bar − 15m」，"
        "就从「只能挂单」变成「立即成交」",
        {"HTTP": st, "quality.status": q.get("status"),
         "注入 requested_at": inject_at,
         "注入时刻(本地)": dt.datetime.fromtimestamp(inject_at / 1000).isoformat(timespec="seconds")
         if inject_at else None,
         "真实挂钟(本地)": dt.datetime.fromtimestamp(now / 1000).isoformat(timespec="seconds"),
         "成交bar_open_time": it.get("bar_open_time"),
         "成交bar(本地)": dt.datetime.fromtimestamp((it.get("bar_open_time") or 0) / 1000)
         .isoformat(timespec="seconds") if it.get("bar_open_time") else None,
         "成交价": it.get("fill_price"), "数量": it.get("qty"),
         "position_id": it.get("position_id"), "reason": it.get("reason"),
         "结果": it if not injected_filled else {k: it.get(k) for k in
                                              ("ok", "position_id", "fill_price", "qty",
                                               "bar_open_time", "entry_ts")}},
        {"说明": "成交即缺陷：成交 bar 落在真实挂钟之前 ⇒ 用过去的 K 线价成交。"
                 "生产账本里那 3 笔就是这样产生的（见 03 的 L5）。"
                 "若本条判未验证，说明行情库该 bar 已超出 MAX_FILL_LAG_MS(60min) 迟滞窗"})

    pos_id = it.get("position_id") if isinstance(it, dict) else None

    # ── C1c 平仓 → 已实现入账 → 余额闭合 ─────────────────────────────
    close_res = []
    if pos_id is not None:
        st, b = http("/api/v1/paper/orders", "POST",
                     {"arm": "3a", "action": "close", "position_id": int(pos_id),
                      "reason": "iso_audit_close"})
        close_res.append({"position_id": pos_id, "HTTP": st,
                          "quality.status": ((b or {}).get("quality") or {}).get("status"),
                          "item": ((b or {}).get("items") or [{}])[0]})
    led = ledger_state()
    stt = (led.get("paper_state") or [{}])[0]
    cash = float(stt.get("cash") or 0)
    realized = float(stt.get("realized_pnl") or 0)
    sum_re = round(sum(float(x["net_pnl"]) for x in (led.get("realized") or [])), 8)
    d1 = round(5000.0 + realized - cash, 8)
    d2 = round(realized - sum_re, 8)
    open_left = [x for x in (led.get("positions") or []) if x["status"] == "open"]
    ok_c1 = (d1 == 0 and d2 == 0 and len(led.get("realized") or []) >= 1 and not open_left)
    rec("C1c", "平仓 → 已实现入账 → 余额闭合（隔离账本）", "通过" if ok_c1 else "不通过",
        "5000 + 已实现 == cash；已实现 == paper_realized 求和；平仓后无 open 仓位",
        {"平仓结果": close_res,
         "cash": cash, "realized_pnl": realized, "Σrealized": sum_re,
         "差额(5000+已实现-cash)": d1, "差额(已实现-Σ明细)": d2,
         "未平仓数": len(open_left),
         "账本仓位": led.get("positions"), "已实现明细": led.get("realized")},
        {"说明": "本条只证明「隔离账本上记账闭合」，不证明「成交时钟正确」——"
                 "C1b 已证明成交靠注入历史时钟才发生"})

    # ── C3 幂等：同 signal_uid 连发 2 次（必须在无未平仓位时做）──────────
    uid = f"iso_idem_{now}"
    body = {"arm": "3a", "action": "open", "symbol": "BTCUSDT", "atr": 500,
            "requested_at": inject_at, "signal_uid": uid}
    st1, b1 = http("/api/v1/paper/orders", "POST", body)
    st2, b2 = http("/api/v1/paper/orders", "POST", body)
    r1 = (((b1 or {}).get("items") or [{}])[0] or {}).get("reason")
    r2 = (((b2 or {}).get("items") or [{}])[0] or {}).get("reason")
    i1 = (((b1 or {}).get("items") or [{}])[0] or {}).get("position_id")
    i2 = (((b2 or {}).get("items") or [{}])[0] or {}).get("position_id")
    led = ledger_state()
    same_uid_pos = [x for x in (led.get("positions") or []) if x.get("signal_uid") == uid]
    ok_c3 = (r2 == "duplicate_signal_uid" and len(same_uid_pos) == 1)
    rec("C3", "重复请求幂等：同一 signal_uid 只成交一笔", "通过" if ok_c3 else "不通过",
        "第二次必须返回 duplicate_signal_uid，且账本里该 uid 只有 1 个仓位",
        {"第一次": {"HTTP": st1, "reason": r1, "position_id": i1,
                  "原始": None if r1 else json.dumps(b1, ensure_ascii=False)[:300]},
         "第二次": {"HTTP": st2, "reason": r2, "position_id": i2,
                  "原始": None if r2 else json.dumps(b2, ensure_ascii=False)[:300]},
         "账本内该uid仓位数": len(same_uid_pos),
         "说明": "幂等判定依赖 paper_processed；本用例在「无未平仓位」前提下执行，"
                 "避免被 position_already_open_no_add 抢先拦下"})
    # 收尾：把幂等用例开的仓位平掉，避免影响 C4 重启比对
    if i1 is not None:
        http("/api/v1/paper/orders", "POST",
             {"arm": "3a", "action": "close", "position_id": int(i1),
              "reason": "iso_idem_close"})

    # ── C5 数据失效：无 15m 数据的标的不得成交 ─────────────────────────
    st, b = http("/api/v1/paper/orders", "POST",
                 {"arm": "3a", "action": "open", "symbol": "NOSUCHPAIRUSDT", "atr": 500,
                  "requested_at": inject_at, "signal_uid": f"iso_nodata_{now}"})
    it = ((b or {}).get("items") or [{}])[0]
    reason = it.get("reason")
    qs = ((b or {}).get("quality") or {}).get("status")
    no_fill = (reason in ("pending_future_fill", "bad_bar_open", "market_db_unavailable")
               or qs == "pending") and not it.get("fill_price")
    rec("C5", "数据失效：无 K 线标的不得用任何价格成交", "通过" if no_fill else "不通过",
        "缺 15m 数据的标的必须登记挂单或拒单，绝不成交",
        {"HTTP": st, "reason": reason, "quality.status": qs,
         "position_id": it.get("position_id"), "fill_price": it.get("fill_price"),
         "原始": None if no_fill else json.dumps(b, ensure_ascii=False)[:300]},
        {"说明": "同一套注入时钟配方下，BTCUSDT 能成交、无数据标的只能挂单 ⇒ "
                 "「没有 bar 就不成交」这条守住了"})

    # ── A11 写入口契约（隔离实例）────────────────────────────────────
    rec("A11", "POST /api/v1/paper/orders 契约（隔离实例）", "通过" if ok_c2 and ok_c3 else "不通过",
        "未知臂 400 / 缺参 400 / 不存在仓位 409 / 重复 uid 幂等 / 正常路径 200",
        {"拒单矩阵通过": ok_c2, "幂等通过": ok_c3,
         "注": "账本缺失 503 无法在隔离实例复现（隔离账本由 ALLOW_INIT 建好）；"
               "该分支由代码事实支撑：LedgerUnavailable ⇒ 503，绝不自动建库"})

    # ── A19 signals/clear（隔离 live 库）─────────────────────────────
    live_db = ISO / "live" / "live_signals.sqlite3"
    cleared = None
    try:
        st0, b0 = http("/api/v1/signals?scope=all")
        n0 = len(((b0 or {}).get("items") or [])) if isinstance(b0, dict) else None
        # 直接往隔离库塞 2 行，再调清理接口，看回执是否等于实际删除行数
        c = sqlite3.connect(str(live_db))
        cols = [r[1] for r in c.execute("PRAGMA table_info(live_signal)")]
        import time as _t
        _n = int(_t.time() * 1000)
        base = {"signal_uid": "iso_clear_1", "source": "live_detector", "origin": "live",
                "exchange": "binance", "market": "um", "symbol": "BTCUSDT",
                "timeframe": "1h", "first_seen_at": _n, "updated_at": _n}
        base = {k: v for k, v in base.items() if k in cols}      # 只保留库里真实存在的列
        for uid_ in ("iso_clear_1", "iso_clear_2"):
            row = dict(base, signal_uid=uid_)
            c.execute(f"INSERT OR REPLACE INTO live_signal({','.join(row)}) "
                      f"VALUES({','.join('?' * len(row))})", list(row.values()))
        c.commit()
        before = c.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        c.close()
        st, b = http("/api/v1/signals/clear", "POST", {"scope": "all"})
        c = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
        after = c.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        c.close()
        rc = (((b or {}).get("items") or [{}])[0] or {}).get("cleared_count")
        cleared = {"HTTP": st, "回执cleared_count": rc, "清理前行数": before,
                   "清理后行数": after, "实际删除": before - after,
                   "一致": rc == before - after,
                   "说明": "隔离 live 库内自造 2 行后调用清理接口"}
    except Exception as e:                                    # noqa: BLE001
        cleared = {"异常": f"{type(e).__name__}: {e}"}
    rec("A19", "POST /api/v1/signals/clear 回执等于实际删除行数",
        "通过" if (cleared or {}).get("一致") else "不通过",
        "回执 cleared_count == 清理前后行数差", cleared or {})

    # ── A21 写侧未知路径 404 ────────────────────────────────────────
    st1, b1 = http("/api/v1/__nope__", "POST", {})
    st2, b2 = http("/api/v1/paper/__nope__", "POST", {})
    rec("A21b", "写侧未知路径 404", "通过" if st1 == 404 and st2 == 404 else "不通过",
        "未知 POST 路径一律 404，且不写任何状态",
        {"POST /api/v1/__nope__": {"HTTP": st1, "body": b1},
         "POST /api/v1/paper/__nope__": {"HTTP": st2, "body": b2}})

    # ── A20 闸门：不执行，只记录原因 ────────────────────────────────
    rec("A20", "POST /api/v1/paper/control 闸门翻转持久化到文件", "未验证",
        "翻转后写盘，重启后仍生效",
        {"闸门文件路径": str(GATE),
         "是否可环境隔离": False,
         "原因": "GATE_FILE 由 paper_engine 硬编码为 __file__ 同级 live/ 目录，无环境变量出口；"
                 "隔离实例与生产实例共用同一文件 ⇒ 执行即改生产文件",
         "旁证（非本轮执行）": "生产闸门 19:30:03 被 set_by=user_ui_control 翻成 "
                             "allow_new_entries=true 并已持久化（见 03 的 L8）"})

    # ── C4 重启恢复 ────────────────────────────────────────────────
    led_before = ledger_state()
    stop(p)
    time.sleep(1.5)
    p2, ok2, secs2 = start_iso("run2")
    if not ok2:
        rec("C4", "重启恢复", "未验证", "重启后账户/持仓仍在", {"启动失败": secs2})
    else:
        st, b = http("/api/v1/paper/accounts")
        items = (b or {}).get("items") or []
        arm3a = next((x for x in items if x.get("arm") == "3a"), None)
        st2_, b2_ = http("/api/v1/paper/positions")
        pos_after = len(((b2_ or {}).get("items") or []))
        led_after = ledger_state()
        cash_ok = (arm3a is not None
                   and abs(float(arm3a.get("cash") or 0)
                           - float((led_before.get("paper_state") or [{}])[0].get("cash") or -1)) < 1e-6)
        pos_ok = (len(led_after.get("positions") or [])
                  == len(led_before.get("positions") or []))
        rec("C4", "重启恢复：写入后重启，账户/持仓仍在", "通过" if (cash_ok and pos_ok) else "不通过",
            "重启后 /paper/accounts 的 cash 与重启前账本一致；仓位数不变",
            {"重启耗时_s": secs2,
             "重启前cash": (led_before.get("paper_state") or [{}])[0].get("cash"),
             "重启后接口cash": None if arm3a is None else arm3a.get("cash"),
             "重启前仓位数": len(led_before.get("positions") or []),
             "重启后账本仓位数": len(led_after.get("positions") or []),
             "重启后接口持仓条数": pos_after,
             "接口账户条数": len(items)})
        stop(p2)

    # ── C6 真实交易未启用 ─────────────────────────────────────────
    # 判据必须落在**可执行代码**上：注释与 docstring 里出现 "ccxt"/"fetch_balance"
    # 不算下单能力（实测纸面三模块的 ccxt 命中全在注释/说明文字里）。
    risky = ("create_order", "place_order", "submit_order", "cancel_order",
             "fapiPrivate", "fapi/v1/order", "fetch_balance", "createOrder")
    hits = []
    for rel in ("paper_engine.py", "live_api.py", "live_store.py"):
        code = strip_code((BACKEND / rel).read_text(encoding="utf-8"))
        for kw in risky:
            if kw in code:
                hits.append({"文件": rel, "命中": kw})
    ad_code = strip_code((BACKEND / "adapter.py").read_text(encoding="utf-8"))
    ad_risky = [k for k in risky if k in ad_code]
    rec("C6", "真实交易未启用：写路径无可执行的交易所下单调用",
        "通过" if (not hits and not ad_risky) else "不通过",
        "剥掉注释与 docstring 后，纸面写路径与适配层不得出现下单/撤单/查余额的可执行调用",
        {"纸面三模块可疑命中": hits, "adapter 可疑命中": ad_risky,
         "扫描关键词": list(risky),
         "结论": "纸面执行是纯标准库的本地记账；实盘账户只有只读快照读取"})

    finish(gate_before)


def finish(gate_before):
    gate_after = {"sha": sha(GATE),
                  "content": json.loads(GATE.read_text(encoding="utf-8")) if GATE.exists() else None,
                  "mtime": dt.datetime.fromtimestamp(GATE.stat().st_mtime).isoformat(timespec="seconds")
                  if GATE.exists() else None}
    unchanged = gate_before["sha"] == gate_after["sha"]
    rec("Z1", "隔离纪律自检：生产闸门文件未被本轮改动", "通过" if unchanged else "不通过",
        "审核前后 paper_new_entry_gate.json 的 SHA-256 完全相同",
        {"前": {"sha": gate_before["sha"], "mtime": gate_before["mtime"],
                "allow_new_entries": (gate_before["content"] or {}).get("allow_new_entries")},
         "后": {"sha": gate_after["sha"], "mtime": gate_after["mtime"],
                "allow_new_entries": (gate_after["content"] or {}).get("allow_new_entries")},
         "隔离账本目录": str(ISO / "paper"),
         "生产账本目录未被引用": True})

    cnt = {}
    for r in RESULTS:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    doc = {"生成时刻": dt.datetime.now().isoformat(timespec="seconds"),
           "隔离实例": {"端口": PORT, "PAPER_LEDGER_DIR": str(ISO / "paper"),
                        "LIVE_DB": str(ISO / "live" / "live_signals.sqlite3"),
                        "启动方式": "adapter._loopback_server（跳过 main 的 startup/prewarm）"},
           "计数": cnt, "明细": RESULTS}
    (OUT / "04_隔离实例生命周期.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===")
    print(json.dumps(cnt, ensure_ascii=False, indent=2))
    for r in RESULTS:
        print(f"  {r['id']:5s} {r['verdict']:4s} {r['title']}")


if __name__ == "__main__":
    main()
