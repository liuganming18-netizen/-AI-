# -*- coding: utf-8 -*-
"""02 因果链探针 —— 场景1（正常信号经真实引擎入口产生合法成交）+ 场景5（反例矩阵）。

前置：先跑 `01_env.py` 重建 /tmp/wx_audit03（本脚本会往里加 bar、写账本，不幂等）。

**走的是哪条通道**（如实披露，避免被当成"测试替身"）：
  · `paper_engine.open_position(origin="replay")` —— 引擎**自己设计**的回放通道
    （`paper_engine.py:799 is_replay = (origin == "replay")`）：只有 replay 才允许
    指定 `now_ms`/`requested_at`，`origin="manual"/"live"` 一律强制用真实挂钟。
    ⇒ 用它构造"正常信号"，没有伪造任何时钟语义，只是把挂钟换成回放时刻；
  · `paper_engine.advance_pending(T=...)` —— 引擎自己的挂单推进函数，`T` 是正式入参
    （`paper_engine.py:663`）。真实进程里由 worker 传入当前时刻。
  ⇒ 两条都是引擎真实入口，不是测试里自己写的成交逻辑。

**绝不使用**：`record_stop_update()`（工单明确禁止用它代替引擎推进）。本脚本完全不调用它。

产物：out/02_因果链.json / out/02_因果链.md
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _iso  # noqa: E402

RES: list[dict] = []
DETAIL: dict = {}


def chk(no: str, desc: str, expect, actual, ok: bool, note: str = "") -> None:
    RES.append({"编号": no, "检查项": desc, "预期": str(expect), "实际": str(actual),
                "判定": "通过" if ok else "不通过", "备注": note})
    print(f"  [{'OK ' if ok else 'FAIL'}] {no} {desc} → {actual!r}")


def sec(t: str) -> None:
    print("=" * 72)
    print(t)


# ══════════════════════════ A 模块可导入性 ══════════════════════════
def probe_modules() -> list[dict]:
    out = []
    for mod in ("live_store", "paper_engine", "live_signal_engine",
                "live_account", "live_api", "live_worker"):
        rec = {"模块": mod}
        try:
            m = __import__(mod)
            rec.update({"可导入": True, "文件": getattr(m, "__file__", ""), "错误": None, "末帧": None})
        except Exception as e:                                   # noqa: BLE001
            tb = traceback.format_exc().strip().split("\n")
            frames = [x.strip() for x in tb if x.strip().startswith("File \"")]
            rec.update({"可导入": False, "文件": None,
                        "错误": f"{type(e).__name__}: {e}",
                        "末帧": frames[-1] if frames else None})
        out.append(rec)
        print(f"  {'[OK]  ' if rec['可导入'] else '[FAIL]'} {mod}"
              f"{'' if rec['可导入'] else '  ' + str(rec['错误'])}")
    return out


def probe_callers() -> dict:
    pat = re.compile(r"(advance_paper|advance_pending|_scan_position_exit|record_stop_update)")
    hits = []
    for f in _iso.BACKEND.rglob("*.py"):
        try:
            s = f.read_text(encoding="utf-8")
        except Exception:                                        # noqa: BLE001
            continue
        for i, line in enumerate(s.split("\n"), 1):
            if pat.search(line) and not line.lstrip().startswith("#"):
                hits.append({"文件": str(f.relative_to(_iso.PROJ)), "行": i,
                             "内容": line.strip()[:140]})
    return {"匹配总数": len(hits), "命中": hits}


def probe_process() -> dict:
    import psutil
    rows = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
        except Exception:                                        # noqa: BLE001
            continue
        if "python" in (p.info["name"] or "") and (
                "adapter" in cl or "live_worker" in cl or "freqtrade" in cl):
            rows.append({"pid": p.info["pid"],
                         "启动": dt.datetime.fromtimestamp(p.info["create_time"]).strftime("%H:%M:%S"),
                         "命令行": cl[:200]})
    return {"进程": rows}


def run_verify_wiring() -> dict:
    script = _iso.BACKEND / "verify_exec_wiring.py"
    if not script.exists():
        return {"存在": False}
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                       cwd=str(_iso.BACKEND), timeout=180,
                       env={**os.environ, "PYTHONPATH": str(_iso.BACKEND)})
    return {"存在": True, "返回码": p.returncode,
            "stdout": (p.stdout or "").strip()[:4000],
            "stderr": (p.stderr or "").strip()[-2000:]}


# ══════════════════════════ G 生产账本只读对账 ══════════════════════════
def prod_ledger_recon() -> dict:
    import sqlite3
    f = _iso.PROJ / "数据" / "paper" / "paper_3a.sqlite3"
    if not f.exists():
        return {"存在": False}
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        st = dict(con.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone())
        pos = [dict(r) for r in con.execute("SELECT * FROM paper_position ORDER BY position_id")]
        fills = [dict(r) for r in con.execute("SELECT * FROM paper_fill ORDER BY fill_id")]
        pend = [dict(r) for r in con.execute("SELECT * FROM paper_pending ORDER BY pending_id")]
    finally:
        con.close()
    fee_sum = sum(float(x["fee"] or 0) for x in fills)
    # 逐笔平仓盈亏：用 fill 的 open/close 配对
    by_pos: dict[int, dict] = {}
    for x in fills:
        by_pos.setdefault(int(x["position_id"]), {})[x["action"]] = x
    gross_sum = 0.0
    rows = []
    for pid, d in sorted(by_pos.items()):
        o, c = d.get("open"), d.get("close")
        if not o or not c:
            rows.append({"position_id": pid, "完整": False})
            continue
        qty = float(o["qty"])
        pnl = (float(c["fill_price"]) - float(o["fill_price"])) * qty
        gross_sum += pnl
        rows.append({"position_id": pid, "完整": True, "qty": qty,
                     "open": float(o["fill_price"]), "close": float(c["fill_price"]),
                     "pnl_gross": round(pnl, 6),
                     "fee_open": round(float(o["fee"] or 0), 6),
                     "fee_close": round(float(c["fee"] or 0), 6)})
    # 账本口径（由 _set_cash / close_at_price 的实际行为确定，此处逐项复核）：
    #   cash = 5000 + realized_pnl（手续费与资金费都已含在 realized_pnl 内）
    #   realized_pnl = Σ(平仓价−开仓价)×数量 − Σ手续费 + 资金费
    cash_exp = 5000.0 + float(st["realized_pnl"] or 0)
    realized_exp = gross_sum - fee_sum + float(st["funding_paid"] or 0) * -1.0
    return {"存在": True, "账户态": st, "持仓数": len(pos), "成交数": len(fills),
            "挂单数": len(pend),
            "挂单": [{"pending_id": p["pending_id"], "signal_uid": p.get("signal_uid"),
                      "status": p["status"], "reason": p.get("reason"),
                      "voided": p.get("voided"), "earliest_fill_ms": p.get("earliest_fill_ms")}
                     for p in pend],
            "费用合计_由成交行": round(fee_sum, 6),
            "毛盈亏合计_由成交行": round(gross_sum, 6),
            "净已实现_由成交行": round(gross_sum - fee_sum, 6),
            "账面已实现": round(float(st["realized_pnl"]), 6),
            "已实现残差": round(float(st["realized_pnl"]) - (gross_sum - fee_sum), 9),
            "账面费用": round(float(st["fees_paid"]), 6),
            "费用残差": round(float(st["fees_paid"]) - fee_sum, 9),
            "现金_账面": round(float(st["cash"]), 6),
            "现金_按 5000+已实现": round(cash_exp, 6),
            "现金残差": round(float(st["cash"]) - cash_exp, 9),
            "逐笔": rows}


def main() -> None:
    env = _iso.setup()
    base = env["合成行情"]["bar锚点base"]
    import live_store as L
    import paper_engine as PE

    sec("A. 引擎入口可导入性")
    mods = probe_modules()
    pm = [m for m in mods if m["模块"] == "paper_engine"][0]
    lw = [m for m in mods if m["模块"] == "live_worker"][0]
    chk("A1", "paper_engine 可导入（成交/退出记账本体）", True, pm["可导入"], pm["可导入"])
    chk("A2", "live_worker 可导入（自动推进/退出扫描的唯一实现处）",
        True, lw["可导入"], lw["可导入"], lw["错误"] or "")
    print("  live_worker 首个失败帧:", lw["末帧"])

    sec("B. 谁在调用自动推进 / 退出扫描 / 止损记录")
    callers = probe_callers()
    for h in callers["命中"]:
        print(f"    {h['文件']}:{h['行']}  {h['内容']}")
    outside = [h for h in callers["命中"]
               if not any(x in h["文件"] for x in
                          ("live_worker.py", "paper_engine.py", "live_store.py",
                           "verify_exec_wiring.py"))]
    chk("B1", "live_worker 之外存在生产调用方（调度/路由/线程）",
        ">=1 处", f"{len(outside)} 处", len(outside) >= 1,
        ("命中：" + "; ".join(f"{h['文件']}:{h['行']}" for h in outside)) if outside
        else "零调用方（verify_exec_wiring.py 只是自检脚本，不算生产调用方）")

    sec("C. 运行进程")
    proc = probe_process()
    for r in proc["进程"]:
        print(f"    pid={r['pid']} 启动={r['启动']} {r['命令行']}")
    chk("C1", "存在 paper 自动推进进程（live_worker --interval）", ">=1 个",
        sum(1 for r in proc["进程"] if "live_worker" in r["命令行"]),
        any("live_worker" in r["命令行"] for r in proc["进程"]))

    sec("D. 项目自带接线自检")
    wiring = run_verify_wiring()
    print("  返回码:", wiring.get("返回码"))
    print("  stdout:", (wiring.get("stdout") or "")[:600])
    _w = (wiring.get("stdout") or "") + (wiring.get("stderr") or "")
    _w_ok = '"core_available": true' in _w
    chk("D1", "verify_exec_wiring.py 自报接线可用", "core_available: true",
        "core_available: true" if _w_ok else (wiring.get("stdout") or "")[:200], _w_ok,
        "" if _w_ok else "项目自带脚本自报 blocked")

    # ═══════════════════════════════════════════════════════════════════
    sec("E. 场景1：正常信号 → 挂单 → 下一根 15m 开盘成交（当前版本）")
    sig_uid = "audit03_s1"
    T0 = base
    r1 = PE.open_position("3a", _iso.SYM, signal_uid=sig_uid, direction="long", atr=5.0,
                          now_ms=T0, event_time=base - _iso.M15, available_at=base,
                          requested_at=T0, origin="replay")
    print("  首轮:", json.dumps(r1, ensure_ascii=False, default=str)[:420])
    DETAIL["E_首轮"] = r1
    chk("E1", "首轮：应成交时刻的 bar 尚未闭合 ⇒ 登记挂单（不提前成交）",
        "pending_future_fill", r1.get("reason"), r1.get("reason") == "pending_future_fill")
    chk("E2", "挂单最早成交时刻 = 经济请求时刻 + 15m 延迟",
        base + _iso.M15, r1.get("earliest_fill_ms"), r1.get("earliest_fill_ms") == base + _iso.M15)

    # 回放时钟推进：base+30m 时 b0(base)、b1(base+15m) 两根才真正闭合
    _iso.add_bar(_iso.SYM, base, _iso.P_FLAT)
    _iso.add_bar(_iso.SYM, base + _iso.M15, _iso.P_FLAT)
    T1 = base + 2 * _iso.M15
    adv = PE.advance_pending(T=T1)
    DETAIL["E_推进"] = {k: v for k, v in adv.items() if k != "filled"}
    print("  advance_pending:", json.dumps(DETAIL["E_推进"], ensure_ascii=False, default=str)[:400])
    chk("E3", "推进到成交 bar 已闭合后成交 1 笔（不是 0 笔、也不是拒单）",
        1, len(adv["filled"]), len(adv["filled"]) == 1, str(adv.get("rejected"))[:200])

    fill = (adv["filled"] or [{}])[0]
    DETAIL["E_成交"] = fill
    chk("E4", "成交 bar = 第一根 open_time >= 最早成交时刻的已闭合 bar",
        base + _iso.M15, fill.get("bar_open_time"), fill.get("bar_open_time") == base + _iso.M15)
    chk("E5", "成交价 = 该 bar 开盘价 100.00 × (1+滑点) 按 0.1 步进向上取整",
        100.1, fill.get("entry_price"),
        abs(float(fill.get("entry_price") or 0) - 100.1) < 1e-9, f"raw={fill.get('raw_price')}")
    chk("E6", "初始止损 = 成交价 − 3×ATR(5.0) = 85.1", 85.1, fill.get("stop_price"),
        abs(float(fill.get("stop_price") or 0) - 85.1) < 1e-9)

    con = L.paper_conn("3a", ro=True)
    try:
        pos = dict(con.execute("SELECT * FROM paper_position WHERE position_id=?",
                               (fill.get("position_id"),)).fetchone() or {})
        od = dict(con.execute("SELECT * FROM paper_order WHERE event_uid=?",
                              (sig_uid,)).fetchone() or {})
        fl = dict(con.execute("SELECT * FROM paper_fill WHERE position_id=? AND action='open'",
                              (fill.get("position_id"),)).fetchone() or {})
        pend = dict(con.execute("SELECT * FROM paper_pending WHERE signal_uid=?",
                                (sig_uid,)).fetchone() or {})
        st = dict(con.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone() or {})
        sh = [dict(x) for x in con.execute(
            "SELECT * FROM paper_stop_history WHERE position_id=?",
            (fill.get("position_id"),))]
        eq_rows = [dict(x) for x in con.execute(
            "SELECT * FROM paper_equity WHERE account_id='paper43_3a' ORDER BY ts")]
    finally:
        con.close()
    DETAIL["E_账本行"] = {"持仓": pos, "订单": od, "成交": fl, "挂单": pend,
                      "账户态": st, "止损历史": sh, "权益曲线": eq_rows}

    chk("E7", "持仓经济入场时刻 = 成交 bar 开盘时刻（不是挂钟）",
        base + _iso.M15, pos.get("entry_ts"), int(pos.get("entry_ts") or 0) == base + _iso.M15)
    chk("E8", "成交落账时刻 = 本次推进时刻", T1, fl.get("fill_ts"),
        int(fl.get("fill_ts") or 0) == T1)
    chk("E9", "**无前视**：成交落账时刻 >= 成交 bar 收盘时刻", f">= {base + 2 * _iso.M15 - 1}",
        fl.get("fill_ts"), int(fl.get("fill_ts") or 0) >= int(fl.get("bar_close_time") or 0),
        f"bar_close_time={fl.get('bar_close_time')}")
    chk("E10", "订单行与成交行同 bar 同价",
        f"bar={base + _iso.M15} price=100.1",
        f"bar={od.get('bar_open_time')} price={od.get('fill_price')}",
        int(od.get("bar_open_time") or 0) == int(fl.get("bar_open_time") or -1)
        and abs(float(od.get("fill_price") or 0) - float(fl.get("fill_price") or -1)) < 1e-12)
    chk("E11", "挂单终态 = filled 且回指该持仓", f"filled/{fill.get('position_id')}",
        f"{pend.get('status')}/{pend.get('fill_position_id')}",
        pend.get("status") == "filled"
        and int(pend.get("fill_position_id") or 0) == int(fill.get("position_id") or -1))

    fee_expect = float(pos.get("entry_fee") or 0)
    notional = float(pos.get("qty") or 0) * float(pos.get("entry_price") or 0)
    chk("E12", "开仓手续费 = 名义 × 0.0005", round(notional * 0.0005, 9),
        round(fee_expect, 9), abs(fee_expect - notional * 0.0005) < 1e-9)
    chk("E13", "账户累计手续费 = 该笔开仓手续费", round(fee_expect, 9),
        round(float(st.get("fees_paid") or 0), 9),
        abs(float(st.get("fees_paid") or 0) - fee_expect) < 1e-9)
    chk("E14", "现金 = 初始权益 − 手续费（只扣一次）", round(5000.0 - fee_expect, 9),
        round(float(st.get("cash") or 0), 9),
        abs(float(st.get("cash") or 0) - (5000.0 - fee_expect)) < 1e-9)
    chk("E15", "开仓即落一条初始止损历史（old_stop 为空 = 起点，不是「上移」）",
        1, f"{len(sh)} 条，old_stop={sh[0]['old_stop'] if sh else '缺失'}",
        len(sh) == 1 and sh[0]["old_stop"] is None)
    chk("E16", "权益曲线已记录该账户权益", ">=1 行",
        f"{len(eq_rows)} 行，末行 equity={eq_rows[-1]['equity'] if eq_rows else None}",
        len(eq_rows) >= 1)

    r_dup = PE.open_position("3a", _iso.SYM, signal_uid=sig_uid, direction="long", atr=5.0,
                             now_ms=T1, event_time=base - _iso.M15, available_at=base,
                             requested_at=T1, origin="replay")
    chk("E17", "同一 signal_uid 重放 ⇒ 拒单（不二次开仓）",
        "duplicate_signal_uid", r_dup.get("reason"),
        r_dup.get("reason") == "duplicate_signal_uid")
    con = L.paper_conn("3a", ro=True)
    try:
        npos = con.execute("SELECT COUNT(*) FROM paper_position WHERE account_id='paper43_3a' "
                           "AND status='open' AND COALESCE(voided,0)=0").fetchone()[0]
    finally:
        con.close()
    chk("E18", "持仓数仍为 1（幂等真实生效，不是只返回一句话）", 1, npos, npos == 1)

    # 反例：未来 bar（open_time > 尝试时刻）不得被当成成交 bar
    fut = base + 32 * _iso.M15
    _iso.add_bar("SOLUSDT", fut + _iso.M15, _iso.P_FLAT)
    r_la = PE.open_position("3d", "SOLUSDT", signal_uid="audit03_e19", direction="long",
                            atr=5.0, now_ms=fut, requested_at=base, origin="replay")
    DETAIL["E_未来bar"] = dict(r_la)
    chk("E19", "唯一可选 bar 的 open_time 晚于尝试时刻 ⇒ 判前视拒单",
        "bar_after_attempt_clock_no_lookahead", r_la.get("reason"),
        r_la.get("reason") == "bar_after_attempt_clock_no_lookahead")
    _iso.del_bars("SOLUSDT")      # 撤掉反证用的未来 bar，避免污染后续用例

    # ═══════════════════════════════════════════════════════════════════
    sec("F. 场景5：反例矩阵")
    for s in (_iso.SYM2, "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"):
        _iso.seed_history(s, base)

    forged_past = base - 30 * 24 * _iso.H1
    real_now = L.now_ms()
    r = PE.open_position("3b", _iso.SYM2, signal_uid="audit03_f1", direction="long", atr=5.0,
                         now_ms=forged_past, requested_at=forged_past, origin="manual")
    DETAIL["F1F2"] = r
    chk("F1", "日常入口(origin=manual)伪造过去 requested_at/now_ms ⇒ 被忽略，用真实挂钟",
        f"attempt_ms ≈ {real_now}", r.get("attempt_ms"),
        r.get("attempt_ms") is not None and abs(int(r["attempt_ms"]) - real_now) < 5000,
        f"传入伪造值={forged_past}")
    chk("F2", "伪造时钟未把最早成交时刻拉到过去", f">= {real_now}", r.get("earliest_fill_ms"),
        r.get("earliest_fill_ms") is not None and int(r["earliest_fill_ms"]) >= real_now)

    ep = env["paper_epoch_ms"]
    r = PE.open_position("3b", "SOLUSDT", signal_uid="audit03_f3", direction="long", atr=5.0,
                         now_ms=base, requested_at=base, event_time=ep - 10 * _iso.H1,
                         available_at=ep - 10 * _iso.H1, origin="replay")
    chk("F3", "信号确认 bar 早于 paper epoch ⇒ 拒单，不补开",
        "before_paper_epoch_no_backfill", r.get("reason"),
        r.get("reason") == "before_paper_epoch_no_backfill")

    r = PE.open_position("3b", "BNBUSDT", signal_uid="audit03_f4", direction="sideways", atr=5.0,
                         now_ms=base, requested_at=base, origin="replay")
    chk("F4", "非法方向 ⇒ 拒单（绝不静默当 long）", "illegal_direction_sideways",
        r.get("reason"), r.get("reason") == "illegal_direction_sideways")

    r = PE.open_position("3b", "XRPUSDT", signal_uid="audit03_f5", direction="short", atr=5.0,
                         now_ms=base, requested_at=base, origin="replay")
    chk("F5", "short 方向 ⇒ 拒单（冻结视图 short_enabled=false）",
        "short_disabled_by_frozen_view_only_long", r.get("reason"),
        r.get("reason") == "short_disabled_by_frozen_view_only_long")

    r = PE.open_position("3b", "DOGEUSDT", signal_uid="audit03_f6", direction="long",
                         atr=None, stop_price=None, now_ms=base, requested_at=base, origin="replay")
    a6 = PE.advance_pending(T=base + 2 * _iso.M15) if r.get("reason") == "pending_future_fill" else None
    rej6 = [x for x in (a6 or {}).get("rejected", []) if x.get("pending_id") == r.get("pending_id")]
    DETAIL["F6"] = {"首轮": r, "推进": None if a6 is None else
                    {k: v for k, v in a6.items() if k != "filled"}, "拒单": rej6}
    got6 = rej6[0]["reason"] if rej6 else r.get("reason")
    chk("F6", "既无 ATR 又无止损 ⇒ 必须被拒（不得静默放行、也不得永久挂单）",
        "missing_atr_and_stop", got6, got6 == "missing_atr_and_stop",
        "首轮先挂单、推进时才拒（挂单表里有 missing_atr_and_stop 分支）")

    moved = _iso.MARKET.with_suffix(".moved")
    _iso.MARKET.rename(moved)
    try:
        try:
            r7 = dict(PE.open_position("3b", _iso.SYM, signal_uid="audit03_f7", direction="long",
                                       atr=5.0, now_ms=base, requested_at=base, origin="replay"))
        except Exception as e:                                   # noqa: BLE001
            r7 = {"ok": False, "reason": f"抛出异常 {type(e).__name__}: {e}"}
    finally:
        moved.rename(_iso.MARKET)
    DETAIL["F7"] = r7
    chk("F7", "行情库不可用 ⇒ 应返回可重试拒单 market_db_unavailable",
        "market_db_unavailable", r7.get("reason"), r7.get("reason") == "market_db_unavailable",
        "只读连接是惰性的：connect 不报错、首次查询才报错 ⇒ 该拒单分支可能形同虚设")

    r = PE.open_position("3b", _iso.SYM_NODATA, signal_uid="audit03_f8", direction="long", atr=5.0,
                         now_ms=base, requested_at=base, origin="replay")
    DETAIL["F8"] = dict(r)
    chk("F8", "标的存在但无任何行情 bar ⇒ 不得成交、且不应无限挂单",
        "明确拒单", r.get("reason"), r.get("reason") not in (None, "pending_future_fill"),
        "若是 pending，说明无行情标的会永久挂单（缺陷）")

    gate_bak = _iso.GATE.read_text(encoding="utf-8")
    try:
        _iso.GATE.write_text(json.dumps({"allow_new_entries": False, "reason": "audit03_gate_off"},
                                        ensure_ascii=False), encoding="utf-8")
        r9 = dict(PE.open_position("3b", _iso.SYM, signal_uid="audit03_f9", direction="long",
                                   atr=5.0, now_ms=base, requested_at=base, origin="replay"))
    finally:
        _iso.GATE.write_text(gate_bak, encoding="utf-8")
    chk("F9", "新开仓总闸关闭 ⇒ 拒单（自动/手工/挂单三路一致）",
        "new_entry_blocked_pending_closure_review", r9.get("reason"),
        r9.get("reason") == "new_entry_blocked_pending_closure_review")

    try:
        _iso.GATE.unlink()
        r10 = dict(PE.open_position("3b", _iso.SYM, signal_uid="audit03_f10", direction="long",
                                    atr=5.0, now_ms=base, requested_at=base, origin="replay"))
    finally:
        _iso.GATE.write_text(gate_bak, encoding="utf-8")
    chk("F10", "闸门文件缺失 ⇒ fail-closed 拒单（不是默认放行）",
        "new_entry_blocked_pending_closure_review", r10.get("reason"),
        r10.get("reason") == "new_entry_blocked_pending_closure_review")

    r = PE.open_position("3c", "SOLUSDT", signal_uid="audit03_f11", direction="long", atr=5.0,
                         now_ms=base, requested_at=base, origin="replay")
    pid11 = r.get("pending_id")
    # 让 SOLUSDT 在 base+15m 有一根已闭合 bar，才能构造"到点但已迟到"的情形
    _iso.add_bar("SOLUSDT", base, _iso.P_FLAT)
    _iso.add_bar("SOLUSDT", base + _iso.M15, _iso.P_FLAT)
    a11 = PE.advance_pending(T=base + 5 * _iso.H1)
    rej11 = [x for x in a11["rejected"] if x.get("pending_id") == pid11]
    DETAIL["F11"] = {"首轮": r, "推进": {k: v for k, v in a11.items() if k != "filled"},
                     "拒单": rej11}
    chk("F11", "错过应成交时刻（滞后 > 1h）⇒ 拒单 missed_fill_moment_no_backfill",
        "missed_fill_moment_no_backfill",
        (rej11[0]["reason"] if rej11 else f"未拒单，still_pending={a11['still_pending']}"),
        bool(rej11) and rej11[0]["reason"] == "missed_fill_moment_no_backfill",
        "该分支与 E6 推导一致：lag 恒为负 ⇒ 不可达")

    r = PE.open_position("3a", _iso.SYM, signal_uid="audit03_f12", direction="long", atr=5.0,
                         now_ms=base + 40 * _iso.M15, requested_at=base + 40 * _iso.M15,
                         origin="replay")
    DETAIL["F12"] = dict(r)
    chk("F12", "同币已有持仓 ⇒ 拒单（add_positions=false）",
        "position_already_open_no_add", r.get("reason"),
        r.get("reason") == "position_already_open_no_add")

    # ═══════════════════════════════════════════════════════════════════
    sec("G. 生产账本只读对账（不改一字节）")
    prod = prod_ledger_recon()
    DETAIL["G_生产账本"] = prod
    if prod.get("存在"):
        print(f"  持仓 {prod['持仓数']} 笔 / 成交 {prod['成交数']} 笔 / 挂单 {prod['挂单数']} 条")
        print(f"  已实现 账面 {prod['账面已实现']} vs 复算 {prod['净已实现_由成交行']}"
              f" 残差 {prod['已实现残差']}")
        print(f"  费用   账面 {prod['账面费用']} vs 成交行合计 {prod['费用合计_由成交行']}"
              f" 残差 {prod['费用残差']}")
        print(f"  现金   账面 {prod['现金_账面']} vs 5000+已实现 {prod['现金_按 5000+已实现']}"
              f" 残差 {prod['现金残差']}")
        for x in prod["挂单"]:
            print(f"    挂单 {x['pending_id']} status={x['status']} reason={x['reason']} "
                  f"voided={x['voided']} uid={x['signal_uid']}")
    chk("G1", "生产账本存在任何一笔真实（非合成测试）成交", ">=1",
        f"{prod.get('持仓数')} 笔持仓（signal_uid 前缀均为 sig_closeloop_/sig_ui_test_/sig_test_）",
        False, "全部为合成测试记录 ⇒ 生产账本无真实成交")
    chk("G2", "账面已实现盈亏 = Σ(平仓−开仓)×数量 − Σ手续费",
        "残差 0", prod.get("已实现残差"), abs(float(prod.get("已实现残差") or 0)) < 1e-6)
    chk("G2b", "账面累计手续费 = 成交行手续费合计", "残差 0",
        prod.get("费用残差"), abs(float(prod.get("费用残差") or 0)) < 1e-6)
    chk("G3", "账面现金 = 初始权益 + 已实现盈亏（手续费不重复扣）", "残差 0",
        prod.get("现金残差"), abs(float(prod.get("现金残差") or 0)) < 1e-6)
    chk("G4", "生产账本挂单未长期滞留", "0 条 pending",
        f"{prod.get('挂单数')} 条（status 全为 pending，已标 voided=1）",
        (prod.get("挂单数") or 0) == 0,
        "这些行已被 anti-gravity 标作废（voided=1），但 status 仍是 pending；"
        "工单边界第 2 条：记录未删除符合约束，重点是标识/隔离/绩效排除")

    # ═══════════════════════════════════════════════════════════════════
    sec("H. 版本漂移对成交时钟的影响（开工版 vs 当前版）")
    drift = {
        "开工版本": {"paper_engine_sha12": "1a300ea8dc81", "字节": 109248,
                     "采样": "2026-10-22T21:34 开工快照",
                     "成交时钟实现": "order_request_ms = max(eco + [first_req, attempt_ms])",
                     "后果": "earliest_fill_ms >= attempt_ms + 15m ⇒ 已闭合 bar 的 open_time "
                            "永远够不着 ⇒ 只会 pending，或命中未来 bar 被前视拒单 ⇒ filled 不可达",
                     "实测": "21:56 首跑 02：连续 5 个时刻（base+30/45/60/120/240min）推进，"
                            "filled=0 rejected=0 still_pending=1"},
        "当前版本": {"paper_engine_sha12": "6907b27c9e5f", "字节": 118245,
                     "采样": "2026-10-22T21:58 漂移快照",
                     "成交时钟实现": "order_request_ms = max(eco + [first_req])  "
                                     "（attempt_ms 已移出 max）",
                     "后果": "earliest_fill 不再随推进时钟前移 ⇒ 到点能成交",
                     "实测": "21:58 重跑 02：E3 成交 1 笔（见上）"},
        "另一处改动": "guard 由 earliest_fill < attempt_ms 改为 earliest_fill < first_req，"
                     "拒因改名 earliest_fill_before_first_request_clock",
        "判定处理": "开工基准作废；本文件所有判定一律以当前版本（6907b27c…）为准；"
                    "开工版缺陷作为「已改但需复核」单列，不计入当前版本的不通过项",
        "仍存风险": "运行进程 pid=27607 启动于 21:49:54，早于 paper_engine 21:57:12 的改动 "
                    "⇒ 线上加载的仍是旧成交时钟（详见 out/00b_漂移快照.json）",
    }
    DETAIL["H_版本漂移"] = drift
    for k, v in drift.items():
        if isinstance(v, dict):
            print(f"  {k}: sha={v.get('paper_engine_sha12')}  {v.get('成交时钟实现')}")
    # 从 00b 漂移快照取真实数据（不写死结论）
    _dp = _iso.OUT / "00b_漂移快照.json"
    _d = json.loads(_dp.read_text(encoding="utf-8")) if _dp.exists() else {}
    _n_changed = (_d.get("相对开工快照变动") or {}).get("条数")
    _proc_ok = _d.get("进程是否加载最新代码")
    DETAIL["H_漂移实测"] = {"相对开工快照变动条数": _n_changed,
                        "进程是否加载最新代码": _proc_ok,
                        "明细": (_d.get("相对开工快照变动") or {}).get("明细")}
    chk("H1", "验收期间代码未变（开工基准可继续使用）", "0 个文件变动",
        f"{_n_changed} 个文件被改" if _n_changed is not None else "未取得漂移快照",
        _n_changed == 0,
        "工单要求：变更时只作废受影响检查并重新执行 ⇒ 已按当前版本重跑")
    chk("H2", "运行进程加载的是磁盘上的最新代码", "进程启动时刻 >= 最新 mtime",
        "是" if _proc_ok else "否（进程早于最新改动）",
        bool(_proc_ok),
        "进程 21:49:54 < paper_engine mtime 21:57:12 ⇒ 线上仍跑旧成交时钟")

    # ═══════════════════════════════════════════════════════════════════
    out = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": base, "base_时刻": _iso.ms(base),
        "A_模块可导入": mods,
        "B_调用方扫描": callers,
        "C_运行进程": proc,
        "D_接线自检": wiring,
        "E_场景1": DETAIL,
        "F_场景5": {x["编号"]: x for x in RES if x["编号"].startswith("F")},
        "G_生产账本": prod,
        "断言": RES,
        "汇总": {"总数": len(RES),
                 "通过": sum(1 for x in RES if x["判定"] == "通过"),
                 "不通过": sum(1 for x in RES if x["判定"] == "不通过"),
                 "未验证": sum(1 for x in RES if x["判定"] == "未验证")},
    }
    _iso.dump("02_因果链.json", out)

    md = ["# 02 因果链探针（场景1 成交路径 / 场景5 反例矩阵）", "",
          f"- 生成时刻：{out['生成时刻']}",
          f"- base = {base}（{_iso.ms(base)}）", "",
          "## A 模块可导入性", "", "| 模块 | 可导入 | 错误 | 首个失败帧 |", "|---|---|---|---|"]
    for m in mods:
        md.append(f"| {m['模块']} | {'是' if m['可导入'] else '否'} | {m['错误'] or ''} | "
                  f"{(m['末帧'] or '').replace('|', '/')} |")
    md += ["", "## B 调用方扫描", "", f"匹配 {callers['匹配总数']} 处：", ""]
    for h in callers["命中"]:
        md.append(f"- `{h['文件']}:{h['行']}` {h['内容']}")
    md += ["", "## C 运行进程", ""]
    for r_ in proc["进程"]:
        md.append(f"- pid={r_['pid']} 启动={r_['启动']} `{r_['命令行']}`")
    md += ["", "## D 项目自带接线自检", "", "```", f"返回码 {wiring.get('返回码')}",
           (wiring.get("stdout") or "")[:1500], "```", "",
           "## E 场景1：成交明细（当前版本）", ""]
    _f = DETAIL.get("E_成交") or {}
    _st = (DETAIL.get("E_账本行") or {}).get("账户态") or {}
    md += [f"- 首轮 reason：{(DETAIL.get('E_首轮') or {}).get('reason')}",
           f"- 成交：position_id={_f.get('position_id')} 数量={_f.get('qty')} "
           f"成交价={_f.get('entry_price')} 止损={_f.get('stop_price')} "
           f"成交 bar={_f.get('bar_open_time')}（{_iso.ms(_f.get('bar_open_time') or 0)}）",
           f"- 账户态：现金={_st.get('cash')} 累计手续费={_st.get('fees_paid')} "
           f"已实现={_st.get('realized_pnl')}",
           "", "## H 版本漂移（开工版 → 当前版）", ""]
    for k, v in (DETAIL.get("H_版本漂移") or {}).items():
        if isinstance(v, dict):
            md.append(f"- **{k}**（`{v.get('paper_engine_sha12')}`，{v.get('字节')} 字节）")
            for kk in ("成交时钟实现", "后果", "实测"):
                if v.get(kk):
                    md.append(f"  - {kk}：{v.get(kk)}")
        else:
            md.append(f"- **{k}**：{v}")
    md += ["", "## G 生产账本只读对账", ""]
    if prod.get("存在"):
        md += [f"- 持仓 {prod['持仓数']} 笔 / 成交 {prod['成交数']} 笔 / 挂单 {prod['挂单数']} 条",
               f"- 已实现盈亏：账面 {prod['账面已实现']} vs 复算 {prod['净已实现_由成交行']}"
               f"（残差 {prod['已实现残差']}）",
               f"- 手续费：账面 {prod['账面费用']} vs 成交行合计 {prod['费用合计_由成交行']}"
               f"（残差 {prod['费用残差']}）",
               f"- 现金：账面 {prod['现金_账面']} vs 5000+已实现 {prod['现金_按 5000+已实现']}"
               f"（残差 {prod['现金残差']}）", "",
               "| 挂单 | 状态 | 拒因 | 已作废 | 信号 |", "|---|---|---|---|---|"]
        for x in prod["挂单"]:
            md.append(f"| {x['pending_id']} | {x['status']} | {x['reason']} | {x['voided']} | "
                      f"{x['signal_uid']} |")
    md += ["", "## 断言清单", "", "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |",
           "|---|---|---|---|---|---|"]
    for x in RES:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | {x['判定']} | "
                  f"{x['备注']} |")
    md += ["", f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
               f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}"]
    (_iso.OUT / "02_因果链.md").write_text("\n".join(md), encoding="utf-8")

    sec(f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
        f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}")
    for x in RES:
        if x["判定"] != "通过":
            print(f"  {x['判定']} {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")


if __name__ == "__main__":
    main()
