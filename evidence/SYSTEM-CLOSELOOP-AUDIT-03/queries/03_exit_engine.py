# -*- coding: utf-8 -*-
"""03 跟踪止损与退出 —— 场景2（新 4H K 线触发止损自动上移）+ 场景3（回落触发退出）。

前置：`01_env.py` → `02_causal.py`（03 要接管 02 开出的那笔 3a 持仓）。

**走的是哪条通道**（本工单最关键的一条，如实披露）：
  · 引擎侧：`paper_engine.advance_position_trailing` / `advance_arm_trailing`
    —— 引擎自己实现的 FT-LOW4H 规则（`paper_engine.py:1932 / 2029`），
    不是测试里重写的规则；测试只提供"一根 4H K 线"这个输入；
  · HTTP 侧：隔离 adapter（端口 8799，环境变量全指 /tmp/wx_audit03）
    → `POST /api/v1/paper/advance_trailing` → 引擎；
    → `GET /api/v1/paper/stops/<pid>`（UI 侧止损历史的同一数据源）；
    → `GET /api/v1/paper/positions` / `orders`（闭合核对）。
  ⇒ 三层都经过真实入口，测试没有直接调用 `record_stop_update()` 代替引擎推进
    （`record_stop_update` 只被 `advance_position_trailing` 内部调用）。

**同时单列两条硬事实**：
  · `live_worker`（自动推进 + 15m 步进止损扫描）不可导入，且全工程无生产调用方
    ⇒ 「自动」这一环当前不存在，推进必须靠外部 HTTP 触发；
  · `advance_trailing` 接受调用方传入的 `candle_4h` 且**不校验**它与行情库是否一致
    ⇒ 见 E 段反例（可凭一根编造的 4H K 线把止损推到任意价位）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _iso  # noqa: E402

PORT = 8799
RES: list[dict] = []
DETAIL: dict = {}


def chk(no: str, desc: str, expect, actual, ok: bool, note: str = "") -> None:
    RES.append({"编号": no, "检查项": desc, "预期": str(expect), "实际": str(actual),
                "判定": "通过" if ok else "不通过", "备注": note})
    print(f"  [{'OK ' if ok else 'FAIL'}] {no} {desc} → {actual!r}")


def skip(no: str, desc: str, why: str) -> None:
    RES.append({"编号": no, "检查项": desc, "预期": "—", "实际": why,
                "判定": "未验证", "备注": "前置不可用，非本项本身失败"})
    print(f"  [SKIP] {no} {desc} → {why}")


def sec(t: str) -> None:
    print("=" * 72)
    print(t)


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:                                        # noqa: BLE001
            return e.code, {"raw": raw[:500]}


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_adapter(extra_env: dict | None = None) -> dict:
    env = {**os.environ,
           "PORT": str(PORT),
           "PAPER_LEDGER_DIR": str(_iso.PAPER),
           "PAPER_LEDGER_ALLOW_INIT": "1",
           "LIVE_DIR": str(_iso.LIVE_DIR),
           "LIVE_DB": str(_iso.LIVE_DB),
           "MARKET_DB": str(_iso.MARKET),
           "PAPER_GATE_FILE": str(_iso.GATE),
           "PYTHONPATH": str(_iso.BACKEND),
           **(extra_env or {})}
    log = pathlib.Path("/tmp/wx_audit03_adapter_8799.log")
    f = open(log, "wb")
    p = subprocess.Popen([sys.executable, str(_iso.BACKEND / "adapter.py")],
                         cwd=str(_iso.BACKEND), env=env, stdout=f, stderr=subprocess.STDOUT)
    ok = False
    for _ in range(160):
        if port_open(PORT):
            ok = True
            break
        if p.poll() is not None:
            break
        time.sleep(0.5)
    return {"pid": p.pid, "已监听": ok, "日志": str(log),
            "返回码": p.poll(), "进程": p, "文件句柄": f}


def one_of(body) -> dict:
    """取 advance_trailing 的单笔结果（position_id 分支返回 items=[res]）。"""
    it = body.get("items")
    if isinstance(it, list) and it and isinstance(it[0], dict):
        return it[0]
    return body if isinstance(body, dict) else {}


def main() -> None:
    env = _iso.setup()
    base = env["合成行情"]["bar锚点base"]
    import live_store as L
    import paper_engine as PE

    # ═══════════════════════════════════════════════════════════════
    sec("A. 入口可用性")
    try:
        __import__("live_worker")
        lw_ok, lw_err = True, None
    except Exception as e:                                       # noqa: BLE001
        lw_ok, lw_err = False, f"{type(e).__name__}: {e}"
    chk("A1", "live_worker 可导入（15m 步进止损扫描 + 自动推进的唯一实现）",
        True, lw_ok, lw_ok, lw_err or "")
    has_tr = hasattr(PE, "advance_position_trailing") and hasattr(PE, "advance_arm_trailing")
    chk("A2", "paper_engine 提供 4H 跟踪止损引擎入口", True, has_tr, has_tr,
        "21:57:12 版本新增；开工版没有这两个函数")
    DETAIL["A_入口"] = {"live_worker_可导入": lw_ok, "错误": lw_err,
                     "paper_engine_有trailing入口": has_tr}

    # 从账本取待测持仓（不依赖 HTTP 端点结构）
    con = L.paper_conn("3a", ro=True)
    try:
        rows = [dict(x) for x in con.execute(
            "SELECT position_id,symbol,entry_price,stop_price,qty FROM paper_position "
            "WHERE account_id='paper43_3a' AND status='open' AND COALESCE(voided,0)=0")]
    finally:
        con.close()
    pid = int(rows[0]["position_id"]) if rows else None
    DETAIL["A_待测持仓"] = rows
    print(f"  待测持仓：{rows}")

    sec("B. 启动隔离 adapter（端口 %d，环境变量全指 /tmp/wx_audit03）" % PORT)
    ad = start_adapter()
    DETAIL["B_隔离adapter"] = {"pid": ad["pid"], "已监听": ad["已监听"],
                            "日志": ad["日志"], "返回码": ad["返回码"]}
    chk("B1", "隔离 adapter 在 8799 起来（不动 8787 生产实例）", True, ad["已监听"], ad["已监听"],
        f"pid={ad['pid']}")

    ok_env = ad["已监听"] and pid is not None
    if not ad["已监听"]:
        try:
            print("  日志尾部：")
            print(pathlib.Path(ad["日志"]).read_text(encoding="utf-8", errors="replace")[-2000:])
        except Exception:                                        # noqa: BLE001
            pass

    if not ok_env:
        skip("B2", "02 开出的持仓经 HTTP 可见", "adapter 未起来或无待测持仓")
        for no, d in (("C1", "止损上移"), ("D1", "回落退出"), ("E1", "伪造 4H 反例")):
            skip(no, d, "前置不可用")
    else:
        st, pos = http("GET", "/api/v1/paper/positions")
        items = pos.get("items") if isinstance(pos.get("items"), list) else []
        DETAIL["B_持仓"] = {"status": st, "body": pos}
        print(f"  GET /paper/positions → {st}  条目 {len(items)}")
        chk("B2", "02 开出的持仓经 HTTP 可见（页面数据源同一入口）", ">=1 条持仓",
            f"{len(items)} 条", len(items) >= 1,
            "注意：持仓行没有 status 字段（snapshot 只列未平仓），按条数判")

        # ═══════════════════════════════════════════════════════════
        sec("C. 场景2：新 4H K 线触发止损自动上移")
        h4 = (base // (4 * _iso.H1)) * (4 * _iso.H1)
        barA = {"open_time": h4 - 4 * _iso.H1, "close_time": h4 - 1,
                "open": 100.0, "high": 101.0, "low": 95.0, "close": 99.0,
                "available_at": h4 - 1}
        _iso.add_bar(_iso.SYM, barA["open_time"], (100.0, 101.0, 95.0, 99.0), tf="4h")
        st, r = http("POST", "/api/v1/paper/advance_trailing",
                     {"arm": "3a", "position_id": pid, "candle_4h": barA})
        one = one_of(r)
        DETAIL["C_首次推进"] = {"status": st, "body": r}
        print(f"  POST advance_trailing → {st}  {json.dumps(one, ensure_ascii=False)[:320]}")
        chk("C0", "POST 走的是单笔分支（返回的 position_id 与请求一致）", pid,
            one.get("position_id"), int(one.get("position_id") or -1) == pid)
        chk("C1", "引擎判定止损上移（stop_updated=True）", True, one.get("stop_updated"),
            bool(one.get("stop_updated")))
        chk("C2", "候选止损 = 4H 最低价 95 × (1−0.05) 按 0.1 向下取整 = 90.2",
            90.2, one.get("candidate_stop"),
            abs(float(one.get("candidate_stop") or 0) - 90.2) < 1e-9)
        chk("C3", "生效止损 = max(原止损 85.1, 候选 90.2) = 90.2（只上移）",
            90.2, one.get("effective_stop"),
            abs(float(one.get("effective_stop") or 0) - 90.2) < 1e-9)
        chk("C4", "生效时刻 = 该 4H K 线收盘时刻（不回填、不提前）",
            h4 - 1, one.get("effective_at"), int(one.get("effective_at") or 0) == h4 - 1)

        st, sh = http("GET", f"/api/v1/paper/stops/{pid}?arm=3a")
        hist = sh.get("items") or []
        DETAIL["C_止损历史"] = {"status": st, "body": sh}
        print(f"  GET /paper/stops/{pid} → {st}  {len(hist)} 行")
        hist_db = PE.get_stop_history("3a", pid)
        DETAIL["C_止损历史_引擎侧"] = hist_db
        chk("C5", "止损历史已真实落盘（initial_stop + ft_low4h_trail 共 2 行）",
            "2 行", f"{len(hist_db)} 行：{[x.get('reason') for x in hist_db]}",
            len(hist_db) == 2 and any(x.get("reason") == "ft_low4h_trail" for x in hist_db))
        chk("C5b", "止损历史经 HTTP 端点可读（UI 取数通道）", "200 且 2 行",
            f"{st} 且 {len(hist)} 行", st == 200 and len(hist) == len(hist_db),
            "默认路由表 api/routes.py 未挂 /paper/stops/{id}，只有旧 _route_v1_legacy 有")
        trail_rows = [x for x in hist_db if x.get("reason") == "ft_low4h_trail"]
        chk("C6", "ft_low4h_trail 行的 old_stop/new_stop 与引擎返回一致", "85.1 → 90.2",
            (f"{trail_rows[0].get('old_stop')} → {trail_rows[0].get('new_stop')}"
             if trail_rows else "缺失"),
            bool(trail_rows)
            and abs(float(trail_rows[0].get("old_stop") or 0) - 85.1) < 1e-9
            and abs(float(trail_rows[0].get("new_stop") or 0) - 90.2) < 1e-9)
        # 时间轴一致性：initial_stop 的 effective_at 用成交 bar 开盘时刻，
        # ft_low4h_trail 用 4H 收盘时刻 —— 两者不同轴，按 effective_at 排序会倒挂
        order = [(x.get("reason"), x.get("effective_at")) for x in hist_db]
        init_eff = next((int(x["effective_at"]) for x in hist_db
                         if x.get("reason") == "initial_stop"), None)
        trail_eff = next((int(x["effective_at"]) for x in hist_db
                          if x.get("reason") == "ft_low4h_trail"), None)
        DETAIL["C_止损历史排序"] = order
        chk("C6b", "止损轨迹按 effective_at 排序时顺序正确（初始在前、上移在后）",
            "initial_stop.effective_at <= ft_low4h_trail.effective_at",
            f"initial={init_eff} trail={trail_eff}",
            (init_eff is not None and trail_eff is not None and init_eff <= trail_eff),
            "initial_stop 用成交 bar 开盘时刻（15m 轴），ft_low4h_trail 用 4H 收盘时刻（4h 轴）"
            "⇒ 同一条轨迹混了两条时间轴，UI 按 effective_at 排序会显示成倒序")

        st, r2 = http("POST", "/api/v1/paper/advance_trailing",
                      {"arm": "3a", "position_id": pid, "candle_4h": barA})
        one2 = one_of(r2)
        st, sh2 = http("GET", f"/api/v1/paper/stops/{pid}?arm=3a")
        hist2 = sh2.get("items") or []
        DETAIL["C_重复推进"] = {"status": st, "body": r2, "历史行数": len(hist2)}
        chk("C7", "重复推进同一根 4H K 线 ⇒ 不重复记录、不下移",
            "stop_updated=False 且历史行数不变",
            f"stop_updated={one2.get('stop_updated')} 历史 {len(hist2)} 行",
            (not one2.get("stop_updated")) and len(hist2) == len(hist))

        barB = {"open_time": h4, "close_time": h4 + 4 * _iso.H1 - 1,
                "open": 99.0, "high": 99.5, "low": 94.0, "close": 95.0,
                "available_at": h4 + 4 * _iso.H1 - 1}
        _iso.add_bar(_iso.SYM, barB["open_time"], (99.0, 99.5, 94.0, 95.0), tf="4h")
        st, r3 = http("POST", "/api/v1/paper/advance_trailing",
                      {"arm": "3a", "position_id": pid, "candle_4h": barB})
        one3 = one_of(r3)
        DETAIL["C_更低低点"] = {"status": st, "body": r3}
        chk("C8", "更低低点（94×0.95=89.3 < 90.2）⇒ 止损不下移",
            "stop_updated=False 且 effective_stop=90.2",
            f"stop_updated={one3.get('stop_updated')} effective={one3.get('effective_stop')}",
            (not one3.get("stop_updated"))
            and abs(float(one3.get("effective_stop") or 0) - 90.2) < 1e-9)

        # ═══════════════════════════════════════════════════════════
        sec("D. 场景3：回落触发退出 → 退出订单→成交→持仓→费用→权益闭合")
        barC = {"open_time": h4 + 4 * _iso.H1, "close_time": h4 + 8 * _iso.H1 - 1,
                "open": 95.0, "high": 96.0, "low": 88.0, "close": 89.0,
                "available_at": h4 + 8 * _iso.H1 - 1}
        _iso.add_bar(_iso.SYM, barC["open_time"], (95.0, 96.0, 88.0, 89.0), tf="4h")
        st, r5 = http("POST", "/api/v1/paper/advance_trailing",
                      {"arm": "3a", "position_id": pid, "candle_4h": barC})
        one5 = one_of(r5)
        DETAIL["D_退出推进"] = {"status": st, "body": r5}
        print(f"  POST advance_trailing（low=88 跌破 90.2）→ {st} "
              f"{json.dumps(one5, ensure_ascii=False)[:420]}")
        chk("D1", "4H 最低价跌破生效止损 ⇒ 触发退出", True, one5.get("exit_triggered"),
            bool(one5.get("exit_triggered")))
        er = one5.get("exit_result") or {}
        chk("D2", "退出以止损价为成交基准（不是更差的低点、也不是更早的价）", 90.2,
            er.get("raw_price"), abs(float(er.get("raw_price") or 0) - 90.2) < 1e-9)
        chk("D3", "退出记账成功（close_at_price 返回 ok）", True, er.get("ok"),
            bool(er.get("ok")), str(er.get("reason"))[:150])

        con = L.paper_conn("3a", ro=True)
        try:
            p_row = dict(con.execute("SELECT * FROM paper_position WHERE position_id=?",
                                     (pid,)).fetchone() or {})
            o_rows = [dict(x) for x in con.execute(
                "SELECT * FROM paper_order WHERE event_uid LIKE ? ORDER BY rowid",
                (f"exit:3a:{pid}:%",))]
            f_rows = [dict(x) for x in con.execute(
                "SELECT * FROM paper_fill WHERE position_id=? ORDER BY fill_id", (pid,))]
            rz = [dict(x) for x in con.execute(
                "SELECT * FROM paper_realized WHERE position_id=?", (pid,))]
            stt = dict(con.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone() or {})
            eqr = [dict(x) for x in con.execute(
                "SELECT * FROM paper_equity WHERE account_id='paper43_3a' ORDER BY ts")]
            fund = [dict(x) for x in con.execute(
                "SELECT * FROM paper_funding WHERE position_id=?", (pid,))]
        finally:
            con.close()
        DETAIL["D_账本"] = {"持仓": p_row, "退出订单": o_rows, "成交": f_rows,
                         "已实现": rz, "账户态": stt, "权益曲线": eqr, "资金费": fund}

        chk("D4", "退出生成独立退出订单（side=sell, status=filled）", "1 条 sell/filled",
            f"{len(o_rows)} 条：{[(x['side'], x['status']) for x in o_rows]}",
            len(o_rows) == 1 and o_rows[0]["side"] == "sell" and o_rows[0]["status"] == "filled")
        close_fills = [x for x in f_rows if x["action"] == "close"]
        chk("D5", "退出生成 close 成交，且回指同一退出订单",
            "1 条且 order_id 与退出订单一致", f"{len(close_fills)} 条",
            len(close_fills) == 1 and bool(o_rows)
            and int(close_fills[0]["order_id"]) == int(o_rows[0]["order_id"]))
        chk("D6", "持仓转为 closed 并写明 exit_reason / exit_price", "closed / trailing_stop_loss",
            f"{p_row.get('status')} / {p_row.get('exit_reason')} / {p_row.get('exit_price')}",
            p_row.get("status") == "closed" and p_row.get("exit_reason") == "trailing_stop_loss"
            and p_row.get("exit_price") is not None)

        gross = float(er.get("gross_pnl") or 0)
        efee = float(er.get("entry_fee") or 0)
        xfee = float(er.get("exit_fee") or 0)
        fpaid = float(er.get("funding_paid") or 0)
        net = float(er.get("net_pnl") or 0)
        chk("D7", "净盈亏 = 毛盈亏 − 开仓费 − 平仓费 − 资金费",
            round(gross - efee - xfee - fpaid, 9), round(net, 9),
            abs(net - (gross - efee - xfee - fpaid)) < 1e-9)
        chk("D8", "已实现表 net_pnl 与引擎返回一致", round(net, 9),
            round(float((rz[0] if rz else {}).get("net_pnl") or 0), 9),
            bool(rz) and abs(float(rz[0]["net_pnl"]) - net) < 1e-9)
        chk("D9", "账户已实现盈亏 = 该笔净盈亏", round(net, 9),
            round(float(stt.get("realized_pnl") or 0), 9),
            abs(float(stt.get("realized_pnl") or 0) - net) < 1e-9)
        chk("D10", "现金 = 初始权益 + 已实现净盈亏（手续费不重复扣）", round(5000.0 + net, 9),
            round(float(stt.get("cash") or 0), 9),
            abs(float(stt.get("cash") or 0) - (5000.0 + net)) < 1e-9)
        chk("D11", "累计手续费 = 开仓费 + 平仓费", round(efee + xfee, 9),
            round(float(stt.get("fees_paid") or 0), 9),
            abs(float(stt.get("fees_paid") or 0) - (efee + xfee)) < 1e-9)
        chk("D12", "权益曲线追加退出后的权益", ">=2 行",
            f"{len(eqr)} 行，末行 {eqr[-1]['equity'] if eqr else None}", len(eqr) >= 2)
        chk("D13", "资金费按真实结算事件计（本环境无结算事件 ⇒ 0，且不报错）", 0.0, fpaid,
            abs(fpaid) < 1e-12, f"paper_funding 行数 {len(fund)}")

        st, pos2 = http("GET", "/api/v1/paper/positions")
        it2 = pos2.get("items") if isinstance(pos2.get("items"), list) else []
        st, od = http("GET", "/api/v1/paper/orders?arm=3a&status=filled")
        oit = od.get("items") if isinstance(od.get("items"), list) else []
        DETAIL["D_HTTP核对"] = {"positions": pos2, "orders": od}
        chk("D14", "HTTP 侧不再有 open 持仓（页面同步）", "0 笔 open",
            f"{len([x for x in it2 if x.get('status') == 'open'])} 笔",
            len([x for x in it2 if x.get("status") == "open"]) == 0)
        chk("D15", "HTTP 侧能看到已成交订单", ">=1 条", f"{len(oit)} 条", len(oit) >= 1)

        # ═══════════════════════════════════════════════════════════
        sec("E. 反例：调用方伪造一根行情库里不存在的 4H K 线")
        # 用 3c/ETHUSDT 另开一笔（不与 D 的已平仓冲突），走引擎真实开仓路径
        r_open = PE.open_position("3c", _iso.SYM2, signal_uid="audit03_c9", direction="long",
                                  atr=5.0, now_ms=base, requested_at=base,
                                  event_time=base, available_at=base, origin="replay")
        _iso.add_bar(_iso.SYM2, base, _iso.P_FLAT)
        _iso.add_bar(_iso.SYM2, base + _iso.M15, _iso.P_FLAT)
        adv = PE.advance_pending(T=base + 2 * _iso.M15)
        pid2 = None
        con = L.paper_conn("3c", ro=True)
        try:
            row2 = con.execute("SELECT position_id,entry_price,stop_price FROM paper_position "
                               "WHERE account_id='paper43_3c' AND status='open'").fetchone()
            pid2 = int(row2["position_id"]) if row2 else None
            stop2 = float(row2["stop_price"]) if row2 else None
        finally:
            con.close()
        DETAIL["E_新持仓"] = {"open": r_open, "advance": {k: v for k, v in adv.items() if k != "filled"},
                          "position_id": pid2, "stop": stop2}
        chk("E1", "为反例准备一笔新持仓（3c/ETHUSDT，价格步进 0.01 ⇒ 止损 85.05）", "85.05",
            stop2, pid2 is not None and abs((stop2 or 0) - 85.05) < 1e-9)
        if pid2 is None:
            skip("E2", "伪造 4H K 线是否被采纳", "未能准备持仓")
        else:
            fake = {"open_time": base + 100 * _iso.H1, "close_time": base + 104 * _iso.H1 - 1,
                    "open": 200.0, "high": 210.0, "low": 200.0, "close": 205.0,
                    "available_at": base + 104 * _iso.H1 - 1}
            st, r6 = http("POST", "/api/v1/paper/advance_trailing",
                          {"arm": "3c", "position_id": pid2, "candle_4h": fake})
            one6 = one_of(r6)
            DETAIL["E_伪造4H"] = {"status": st, "body": r6}
            print(f"  POST advance_trailing（伪造 4H low=200）→ {st} "
                  f"{json.dumps(one6, ensure_ascii=False)[:320]}")
            eff = float(one6.get("effective_stop") or 0)
            chk("E2", "伪造 4H K 线（库里不存在、低点 200）不得被采纳",
                "拒收/忽略（止损仍 85.05）",
                f"stop_updated={one6.get('stop_updated')} effective={eff}",
                not (one6.get("stop_updated") and abs(eff - 190.0) < 1e-6),
                "若被采纳：任何能发这个请求的人都能把止损推到任意价位"
                "（该接口无 K 线真伪校验、无 available_at<=now 校验）")

    # ═══════════════════════════════════════════════════════════════
    sec("G. 复核：止损历史端点为何 404（默认路由表 vs 旧回退路由）")
    try:
        ad["进程"].terminate()
        ad["进程"].wait(timeout=15)
    except Exception:                                            # noqa: BLE001
        try:
            ad["进程"].kill()
        except Exception:                                        # noqa: BLE001
            pass
    try:
        ad["文件句柄"].close()
    except Exception:                                            # noqa: BLE001
        pass
    time.sleep(0.8)
    ad2 = start_adapter({"LEGACY_ROUTER": "1"})
    DETAIL["G_LEGACY_ROUTER"] = {"pid": ad2["pid"], "已监听": ad2["已监听"]}
    if not ad2["已监听"]:
        skip("G1", "旧回退路由下止损历史端点可用", "adapter(LEGACY_ROUTER=1) 未起来")
    else:
        st2, sh3 = http("GET", f"/api/v1/paper/stops/{pid}?arm=3a")
        h3 = sh3.get("items") or []
        DETAIL["G_LEGACY_ROUTER"]["body"] = sh3
        print(f"  LEGACY_ROUTER=1  GET /paper/stops/{pid} → {st2}  {len(h3)} 行")
        chk("G1", "旧回退路由（LEGACY_ROUTER=1）下止损历史端点可用", "200 且 2 行",
            f"{st2} 且 {len(h3)} 行", st2 == 200 and len(h3) == 2,
            "证明代码存在、只是没挂到默认路由表")
        try:
            ad2["进程"].terminate()
            ad2["进程"].wait(timeout=15)
        except Exception:                                        # noqa: BLE001
            pass
        try:
            ad2["文件句柄"].close()
        except Exception:                                        # noqa: BLE001
            pass
        time.sleep(0.6)

    # ═══════════════════════════════════════════════════════════════
    sec("F. 收尾：关掉隔离 adapter")
    try:
        ad["进程"].terminate()
        ad["进程"].wait(timeout=15)
    except Exception:                                            # noqa: BLE001
        try:
            ad["进程"].kill()
        except Exception:                                        # noqa: BLE001
            pass
    try:
        ad["文件句柄"].close()
    except Exception:                                            # noqa: BLE001
        pass
    time.sleep(0.6)
    chk("F1", "隔离 adapter 已关闭且 8787 生产实例未受影响",
        "8799 无监听、8787 仍有监听",
        f"8799={port_open(PORT)} 8787={port_open(8787)}",
        (not port_open(PORT)) and port_open(8787))

    out = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": base, "base_时刻": _iso.ms(base),
        "明细": DETAIL,
        "断言": RES,
        "汇总": {"总数": len(RES),
                 "通过": sum(1 for x in RES if x["判定"] == "通过"),
                 "不通过": sum(1 for x in RES if x["判定"] == "不通过"),
                 "未验证": sum(1 for x in RES if x["判定"] == "未验证")},
    }
    _iso.dump("03_跟踪止损与退出.json", out)

    md = ["# 03 跟踪止损与退出（场景2 / 场景3）", "",
          f"- 生成时刻：{out['生成时刻']}",
          f"- base = {base}（{_iso.ms(base)}）", "",
          "## A 入口可用性", ""]
    for k, v in DETAIL["A_入口"].items():
        md.append(f"- {k}：{v}")
    md += [f"- 待测持仓：{json.dumps(DETAIL.get('A_待测持仓'), ensure_ascii=False)}", "",
           "## B 隔离 adapter", ""]
    for k, v in DETAIL["B_隔离adapter"].items():
        md.append(f"- {k}：{v}")
    md += ["", "## C 场景2 止损上移", ""]
    for k in ("C_首次推进", "C_重复推进", "C_更低低点"):
        if k in DETAIL:
            md += [f"### {k}", "", "```json",
                   json.dumps(DETAIL[k], ensure_ascii=False, default=str)[:1200], "```", ""]
    if "C_止损历史" in DETAIL:
        md += ["### 止损历史（UI 同一数据源）", "", "| reason | old_stop | new_stop | effective_at |",
               "|---|---|---|---|"]
        for x in (DETAIL["C_止损历史"]["body"].get("items") or []):
            md.append(f"| {x.get('reason')} | {x.get('old_stop')} | {x.get('new_stop')} | "
                      f"{x.get('effective_at')} |")
        md.append("")
    md += ["## D 场景3 退出与闭合", ""]
    if "D_退出推进" in DETAIL:
        md += ["```json", json.dumps(DETAIL["D_退出推进"], ensure_ascii=False, default=str)[:1600],
               "```", ""]
    if "D_账本" in DETAIL:
        _s = DETAIL["D_账本"]["账户态"]
        md += [f"- 账户态：现金 {_s.get('cash')} 已实现 {_s.get('realized_pnl')} "
               f"累计手续费 {_s.get('fees_paid')}",
               f"- 退出订单 {len(DETAIL['D_账本']['退出订单'])} 条 / 成交 "
               f"{len(DETAIL['D_账本']['成交'])} 条 / 权益曲线 "
               f"{len(DETAIL['D_账本']['权益曲线'])} 行", ""]
    md += ["## E 反例：伪造 4H K 线", ""]
    if "E_伪造4H" in DETAIL:
        md += ["```json", json.dumps(DETAIL["E_伪造4H"], ensure_ascii=False, default=str)[:1200],
               "```", ""]
    md += ["## 断言清单", "", "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |", "|---|---|---|---|---|---|"]
    for x in RES:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | {x['判定']} | "
                  f"{x['备注']} |")
    md += ["", f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
               f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}"]
    (_iso.OUT / "03_跟踪止损与退出.md").write_text("\n".join(md), encoding="utf-8")

    sec(f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
        f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}")
    for x in RES:
        if x["判定"] != "通过":
            print(f"  {x['判定']} {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")


if __name__ == "__main__":
    main()
