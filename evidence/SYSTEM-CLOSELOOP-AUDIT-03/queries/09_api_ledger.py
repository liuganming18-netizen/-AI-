# -*- coding: utf-8 -*-
"""09 账本闭环 / 执行身份 / 版本一致 —— 59 条清单里靠"读库+读源码"判定的那批。

**全程只读生产**：`数据/paper/*.sqlite3` 用 ro=True 打开，闸门与信号库只读字节，
回测 run 目录只读 json/jsonl。不写、不改、不补造。

覆盖条目：L1 L1b L2 L3 L6 L4a L4b L5b A10 C6 NEW-IDENTITY A20 X3c NEW-REALIZED L7
产物：out/09_账本身份.json / .md
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sqlite3
import sys

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
PROD_PAPER = PROJ / "数据" / "paper"
ARMS = ["3a", "3b", "3c", "3d"]
INIT_EQ = 5000.0

ITEMS: list[dict] = []
DETAIL: dict = {}


def chk(no, desc, expect, actual, ok, note=""):
    ITEMS.append({"编号": no, "检查项": desc, "预期": str(expect), "实际": str(actual),
                  "判定": "通过" if ok else "不通过", "备注": note})
    print(("  [OK ] " if ok else "  [FAIL] ") + f"{no} {desc} → {actual!r}"
          + (f"  ({note})" if note else ""))


def sec(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def ro(arm):
    p = PROD_PAPER / f"paper_{arm}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def read(p):
    return pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DETAIL["生成时刻"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # ══════════════════════════════════════════════════════════════
    sec("A. 纸面账本闭合（逐臂 + 跨臂）")
    per = {}
    for arm in ARMS:
        p = PROD_PAPER / f"paper_{arm}.sqlite3"
        if not p.exists():
            per[arm] = {"存在": False}
            continue
        con = ro(arm)
        try:
            st = dict(con.execute("SELECT * FROM paper_state WHERE account_id=?",
                                  (f"paper43_{arm}",)).fetchone() or {})
            pos = [dict(r) for r in con.execute("SELECT * FROM paper_position")]
            fill = [dict(r) for r in con.execute("SELECT * FROM paper_fill")]
            real = [dict(r) for r in con.execute("SELECT * FROM paper_realized")]
            ords = [dict(r) for r in con.execute("SELECT * FROM paper_order")]
            pend = [dict(r) for r in con.execute("SELECT * FROM paper_pending")]
            eq = [dict(r) for r in con.execute("SELECT * FROM paper_equity")]
        finally:
            con.close()
        cash = f(st.get("cash"))
        realized = f(st.get("realized_pnl"))
        fees = f(st.get("fees_paid"))
        fund = f(st.get("funding_paid"))
        sum_fill_fee = sum(f(x.get("fee")) for x in fill)
        sum_real = sum(f(x.get("net_pnl")) for x in real)
        per[arm] = {
            "存在": True, "cash": cash, "realized": realized, "fees_paid": fees,
            "funding_paid": fund, "n_pos": len(pos), "n_fill": len(fill),
            "n_realized": len(real), "n_order": len(ords), "n_pending": len(pend),
            "n_equity": len(eq), "Σfill.fee": sum_fill_fee, "Σrealized.net_pnl": sum_real,
            "open": [x for x in pos if x.get("status") == "open"],
            "positions": pos, "fills": fill, "realized_rows": real, "orders": ords,
            "pending": pend,
        }
    DETAIL["逐臂"] = {k: {kk: vv for kk, vv in v.items()
                        if kk not in ("positions", "fills", "realized_rows", "orders", "pending")}
                    for k, v in per.items()}

    # L1 每臂：cash == 初始 + 已实现（手续费已含在 realized 里，不重复扣）
    bad1 = {}
    for arm in ARMS:
        v = per[arm]
        if not v["存在"]:
            bad1[arm] = "库不存在"
            continue
        d = v["cash"] - (INIT_EQ + v["realized"])
        if abs(d) > 1e-6:
            bad1[arm] = f"残差 {d:+.6f}"
    chk("L1", "每臂现金 = 初始权益 + 已实现（逐臂闭合，差额 <1e-6）", "4 臂全闭合",
        bad1 or "4 臂残差均 <1e-6", not bad1,
        "手续费已含在 net_pnl 内，若再减一次就是重复扣（历史踩坑）")

    # L1b 跨臂加总
    tot_cash = sum(v["cash"] for v in per.values() if v["存在"])
    tot_real = sum(v["realized"] for v in per.values() if v["存在"])
    d_tot = tot_cash - (INIT_EQ * 4 + tot_real)
    chk("L1b", "四臂跨账户加总闭合：Σcash = 4×初始 + Σ已实现", 0.0, round(d_tot, 9),
        abs(d_tot) < 1e-6, f"Σcash={tot_cash:.6f} Σrealized={tot_real:.6f}")

    # L2 手续费 / 资金费各计一次
    bad2 = {}
    for arm in ARMS:
        v = per[arm]
        if not v["存在"]:
            continue
        if abs(v["fees_paid"] - v["Σfill.fee"]) > 1e-6:
            bad2[arm] = f"fees_paid {v['fees_paid']:.9f} ≠ Σfill.fee {v['Σfill.fee']:.9f}"
        # 现金里不得再减一次费用：cash = init + realized 已由 L1 证明
    chk("L2", "累计手续费 = 成交行手续费合计（各计一次，现金里不重复扣）", "4 臂一致",
        bad2 or "4 臂一致", not bad2)

    # L3 标识贯通：signal_uid → order → fill → position → realized
    # 注意：`paper_order` **没有 position_id 列**，它用 `event_uid` 关联信号 ⇒ 按 event_uid 比，
    # 拿 position_id 去比会一律"无订单行"（假失败）。
    chain_bad = {}
    close_no_order = {}
    for arm in ARMS:
        v = per[arm]
        if not v["存在"]:
            continue
        for p in v["positions"]:
            pid, uid = p.get("position_id"), p.get("signal_uid")
            if not uid:
                chain_bad[f"{arm}#{pid}"] = "持仓缺 signal_uid"
                continue
            o = [x for x in v["orders"] if x.get("event_uid") == uid]
            fl = [x for x in v["fills"] if x.get("position_id") == pid]
            if not o:
                chain_bad[f"{arm}#{pid}"] = "无订单行（按 event_uid 也找不到）"
            elif not fl:
                chain_bad[f"{arm}#{pid}"] = "无成交行"
            elif p.get("status") == "closed":
                if not [x for x in v["realized_rows"] if x.get("position_id") == pid]:
                    chain_bad[f"{arm}#{pid}"] = "已平仓但无已实现行"
                if not [x for x in fl if x.get("action") == "close"]:
                    chain_bad[f"{arm}#{pid}"] = "已平仓但无平仓成交行"
        # 平仓成交是否回指订单（新代码要求；旧记录为 NULL）
        for x in v["fills"]:
            if x.get("action") == "close" and x.get("order_id") is None:
                close_no_order[f"{arm}#pos{x.get('position_id')}"] = "平仓成交 order_id 为 NULL"
    DETAIL["L3平仓无订单"] = close_no_order
    chk("L3", "标识贯通 signal_uid→order(event_uid)→fill→position→realized", "全部贯通",
        chain_bad or "全部贯通", not chain_bad,
        "paper_order 用 event_uid 关联，不是 position_id")
    chk("L3b", "平仓成交必须回指退出订单（order_id 非空）", "0 条 NULL",
        close_no_order or "0 条 NULL", not close_no_order,
        "这 3 笔是 21:47 之前的旧记录；21:47 版本已在 close_at_price 里补写退出订单并绑定"
        "（03 的 D5 已在隔离环境证明新代码会写）")

    # L6 期末持仓与账本一致，未靠强制结算伪装已实现
    n_open = sum(len(v["open"]) for v in per.values() if v["存在"])
    n_real_on_open = 0
    for arm in ARMS:
        v = per[arm]
        if not v["存在"]:
            continue
        open_ids = {x["position_id"] for x in v["open"]}
        n_real_on_open += len([x for x in v["realized_rows"]
                               if x.get("position_id") in open_ids])
    chk("L6", "未平仓持仓不得有已实现行（不靠强制结算伪装已实现）", "0 条",
        f"open={n_open} 笔，其已实现行={n_real_on_open} 条", n_real_on_open == 0,
        "生产账本当前无未平仓持仓时本条为空集通过，需配合 03/07 的隔离环境闭合证据")

    # L4a 初始止损可重建：|stop − (entry − 3×ATR)| < 1e-6，且轨迹首行 = initial_stop
    bad4 = {}
    detail4 = []
    for arm in ARMS:
        v = per[arm]
        if not v["存在"]:
            continue
        for p in v["positions"]:
            entry, stop, atr = f(p.get("entry_price")), f(p.get("stop_price")), f(p.get("atr_at_entry"))
            exp = entry - 3.0 * atr
            row = {"arm": arm, "pid": p.get("position_id"), "entry": entry, "stop": stop,
                   "atr": atr, "期望stop": round(exp, 9), "差": round(stop - exp, 9)}
            detail4.append(row)
            if abs(stop - exp) > 1e-6:
                bad4[f"{arm}#{p.get('position_id')}"] = f"stop {stop} ≠ entry−3ATR {exp:.6f}"
    DETAIL["L4a逐笔"] = detail4
    chk("L4a", "初始止损可重建：stop_price = 入场价 − 3×ATR（逐笔，容差 1e-6）",
        "全部成立", bad4 or f"{len(detail4)} 笔全部成立", not bad4,
        "3×ATR = 冻结视图 uniform_stop_atr_mult")

    # L4b 跟踪上移由引擎调用（静态：唯一调用方 + 运行时证据在 03/05）
    pe_src = read(BACKEND / "paper_engine.py")
    callers = [i + 1 for i, l in enumerate(pe_src.splitlines())
               if re.search(r"record_stop_update\s*\(", l) and "def " not in l]
    detail_l4b = {"record_stop_update 调用点行号": callers}
    # 找出调用点所在函数
    lines = pe_src.splitlines()
    def enclosing(ln):
        for i in range(ln - 1, -1, -1):
            m = re.match(r"def\s+(\w+)", lines[i])
            if m:
                return m.group(1)
        return None
    detail_l4b["调用点所属函数"] = [enclosing(x) for x in callers]
    DETAIL["L4b静态"] = detail_l4b
    ok_l4b = (detail_l4b["调用点所属函数"] == ["advance_position_trailing"])
    chk("L4b", "record_stop_update 只被引擎推进函数调用（不是测试直接调）",
        "唯一调用方 = advance_position_trailing", detail_l4b, ok_l4b,
        "运行时证据：03 的 C 段 stop_updated=True 经 POST /paper/advance_trailing 产生；"
        "05 的 D 段重复推进只上移一次")

    # ══════════════════════════════════════════════════════════════
    sec("B. 执行身份：只读声明 / 写入口 / 实际路由 三处是否一致")
    la = read(BACKEND / "live_api.py")
    routes = read(BACKEND / "api" / "routes.py")
    adapter = read(BACKEND / "adapter.py")
    ret = read(BACKEND / "legacy_retirement.py")

    # L5b 写入口是否从请求体读 requested_at / now_ms 参与成交时钟
    pe_lines = pe_src.splitlines()
    l5b_hits = []
    for i, l in enumerate(pe_lines, 1):
        if re.search(r"requested_at|now_ms", l) and re.search(r"body|payload|req\.get|data\.get", l):
            l5b_hits.append((i, l.strip()[:140]))
    has_replay_gate = "is_replay = (origin == \"replay\")" in pe_src
    DETAIL["L5b"] = {"payload取值命中": l5b_hits, "is_replay闸门存在": has_replay_gate,
                     "闸门原文行": [l.strip() for l in pe_lines
                                 if "is_replay = (origin" in l]}
    chk("L5b", "成交时钟不接受调用方注入（payload 的 requested_at/now_ms 只在 replay 下生效）",
        "存在 is_replay 闸门且非 replay 一律用真实挂钟",
        f"is_replay 闸门={'有' if has_replay_gate else '无'}；payload 直读命中 {len(l5b_hits)} 处",
        has_replay_gate, "闸门行：" + (DETAIL['L5b']['闸门原文行'][0]
                                     if DETAIL['L5b']['闸门原文行'] else "—"))

    # C6 是否存在真实交易所下单调用（只看**真实调用**，不看注释/文档字符串/常量）
    c6_hits = []
    call_pat = re.compile(r"\.\s*(create_order|create_market_order|create_limit_order|"
                          r"futures_create_order|futures_create_orders|"
                          r"create_orders|create_trade)\s*\(")
    import_pat = re.compile(r"^\s*(import\s+ccxt|from\s+ccxt)")
    for f_ in ("live_api.py", "paper_engine.py", "adapter.py", "live_store.py"):
        src = read(BACKEND / f_)
        for i, l in enumerate(src.splitlines(), 1):
            s = l.strip()
            if not s or s.startswith("#"):
                continue
            # 整行在字符串里（三引号文档）也跳过：行内没有代码调用形态
            if call_pat.search(l) or import_pat.match(l):
                c6_hits.append((f_, i, s[:130]))
    DETAIL["C6"] = c6_hits
    chk("C6", "写路径不存在真实交易所下单调用（只认真实调用，不认注释/常量）", "0 处",
        f"{len(c6_hits)} 处" + (f"：{c6_hits[:2]}" if c6_hits else ""), not c6_hits,
        "扫 create_order / create_market_order / futures_create_order / import ccxt 四类")

    # A10 三处表述一致
    # adapter 里的写法是 `seg[:2] == ["paper", "stops"]`（不是 seg==["paper","stops",id]）
    v1_route_has_stops = bool(re.search(r'seg\[:2\]\s*==\s*\[\s*"paper"\s*,\s*"stops"\s*\]', adapter))
    v2_default_has_stops = "paper/stops" in routes or '"stops"' in routes
    connected = "paper.connected" in ret and "True" in ret
    DETAIL["A10"] = {"adapter旧回退路由含paper/stops": v1_route_has_stops,
                     "默认路由表含paper/stops": v2_default_has_stops,
                     "legacy_retirement声称paper已连通": connected}
    chk("A10", "写入口/路由注册/状态声明三处一致（不得声明已连通而路由缺失）",
        "三处一致",
        f"旧回退路由有 stops={v1_route_has_stops}；默认路由表有 stops={v2_default_has_stops}；"
        f"状态声明 paper.connected=True", v1_route_has_stops and v2_default_has_stops,
        "默认路由表漏挂 `/paper/stops/{id}` ⇒ 声明'已连通'与可访问性不一致（07 的 C5/C6 已从 UI 侧证到）")

    # NEW-IDENTITY：UI 写路径落到哪个核心 / 哪份参数 / 什么版本
    ui_write = sorted({m for m in re.findall(r"postPaper\w+|postClearSignals", read(
        PROJ / "UI界面" / "web" / "src" / "api" / "liveTrading.ts"))})
    model_ver = re.search(r'MODEL_VERSION\s*=\s*"([^"]+)"', pe_src)
    view_file = re.search(r"EFFECTIVE_VIEW\s*=\s*([^\n]+)", pe_src)
    DETAIL["NEW-IDENTITY"] = {
        "UI写函数": ui_write,
        "MODEL_VERSION": model_ver.group(1) if model_ver else None,
        "参数文件": view_file.group(1).strip() if view_file else None,
        "paper_open_gate": "PAPER_GATE_STRICT" in pe_src,
        "legacy_retired_arms": re.findall(r'RETIRED\w*\s*=\s*[\[{]([^\]}]+)', ret)[:2],
    }
    chk("NEW-IDENTITY", "UI 写路径 → 服务端 paper 核心 → 冻结参数视图 → 版本常量，四处可对齐",
        "四处都能指到同一套（paper_engine + 冻结视图 + MODEL_VERSION）",
        f"UI={ui_write} MODEL_VERSION={DETAIL['NEW-IDENTITY']['MODEL_VERSION']} "
        f"参数={DETAIL['NEW-IDENTITY']['参数文件']}",
        bool(model_ver) and bool(view_file) and bool(ui_write),
        "UI 只调 /paper/orders 与 /paper/toggle_gate 两类写接口，均落在 paper_engine；"
        "旧 F_WX 3a–3d 已封存，但四臂账本仍在被读写（见 05 的 E 段）")

    # ══════════════════════════════════════════════════════════════
    sec("C. 隔离闸门：翻转落盘 + 重启保持（A20）")
    g = pathlib.Path("/tmp/wx_audit05/gate.json")
    a20 = {"隔离闸门存在": g.exists()}
    if g.exists():
        body = json.loads(g.read_text(encoding="utf-8"))
        a20.update({"内容": body, "allow": body.get("allow_new_entries"),
                    "set_by": body.get("set_by")})
    # 重启保持：另起进程读同一个文件（不经过内存状态）
    import subprocess
    code = ("import json,sys;sys.path.insert(0,r'%s');import os;"
            "os.environ['PAPER_GATE_FILE']=r'%s';import paper_engine as PE;"
            "print(json.dumps(PE.new_entry_gate(),ensure_ascii=False))"
            % (BACKEND, g))
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin", "PAPER_GATE_FILE": str(g),
                             "PYTHONPATH": str(BACKEND)}, timeout=120)
    a20["新进程读到的闸门"] = (cp.stdout or "").strip()[:400]
    a20["stderr"] = (cp.stderr or "").strip()[-300:]
    DETAIL["A20"] = a20
    new_gate = {}
    try:
        new_gate = json.loads(a20["新进程读到的闸门"])
    except Exception:                                            # noqa: BLE001
        pass
    chk("A20", "隔离闸门翻转落盘，且新进程重启后仍读到同一状态",
        "allow_new_entries=False 且新进程一致",
        f"文件 allow={a20.get('allow')} 新进程 allow={new_gate.get('allow_new_entries')}",
        a20.get("allow") is False and new_gate.get("allow_new_entries") is False,
        "07 的 C9/C10/D4 已证明 UI 点击把它写下去；本条证明重启后不丢")

    # ══════════════════════════════════════════════════════════════
    sec("D. 生产信号库里的合成测试信号（X3c，只读取证，不删不改）")
    ldb = BACKEND / "live" / "live_signals.sqlite3"
    sig = []
    if ldb.exists():
        con = sqlite3.connect(f"file:{ldb}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            # 真实表名是 `live_signal`（不是 signals），字段是 signal_uid
            sig = [dict(r) for r in con.execute(
                "SELECT signal_uid AS uid, symbol, display_state, executable_state, "
                "origin, strategy_version FROM live_signal ORDER BY rowid DESC LIMIT 5000")]
        finally:
            con.close()
    syn = [x for x in sig if re.match(r"^(sig_closeloop_|sig_ui_test_|sig_test_)", str(x["uid"]))]
    DETAIL["X3c"] = {"总行数": len(sig), "合成前缀行数": len(syn),
                     "全部信号": sig, "合成样本": [x["uid"] for x in syn]}
    chk("X3c", "当前信号区不得混入合成测试信号（sig_closeloop_/sig_ui_test_/sig_test_）",
        "0 条", f"{len(syn)}/{len(sig)} 条为合成前缀", not syn,
        "工单：不删除、不替用户批准清理 ⇒ 只取证并标注，清理范围留待用户决定。"
        "这些信号已被标 voided=1（void_reason=synthetic_test_isolation）")

    # ══════════════════════════════════════════════════════════════
    sec("E. 回测 run：目录 / summary / 逐笔 三者对账 + 已实现三分（L7 / NEW-REALIZED）")
    runs = []
    for base in (PROJ / "回测日志", PROJ / "框架" / "backend" / "backtest_results"):
        if not base.exists():
            continue
        for p in sorted(base.glob("**/summary.json"))[:400]:
            runs.append(p)
    DETAIL["L7_run数"] = len(runs)
    print(f"  找到 summary.json {len(runs)} 个（最多取 400）")

    # 字段名必须回文件确认（这里踩过：trades.jsonl 用的是 net_pnl，不是 profit_abs；
    # summary 用的是 trading_summary.net_pnl / reconcile.sum_net_pnl_closed）。
    l7_bad, l7_ok, l7_detail = {}, 0, []
    nr_detail = []
    for sp in runs[:400]:
        d = sp.parent
        try:
            sm = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            l7_bad[str(d)] = "summary.json 解析失败"
            continue
        ts = sm.get("trading_summary") or {}
        rc = sm.get("reconcile") or {}
        idn = sm.get("identity") or {}
        fn = sm.get("funnel") or {}
        tj = d / "trades.jsonl"
        n_tr, sum_net = 0, 0.0
        real_closed = real_force = 0.0
        n_force = n_normal = 0
        if tj.exists():
            for line in tj.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:                                # noqa: BLE001
                    continue
                n_tr += 1
                pnl = f(t.get("net_pnl"))
                sum_net += pnl
                if t.get("exit_reason") == "force_exit":
                    real_force += pnl
                    n_force += 1
                else:
                    real_closed += pnl
                    n_normal += 1
        # L7 三处对账：summary.trade_count / 逐笔行数 / funnel.executed_trades
        cnts = [x for x in (ts.get("trade_count"), n_tr, fn.get("executed_trades")) if x is not None]
        ok_cnt = len(set(int(x) for x in cnts)) <= 1
        # 金额对账：Σ逐笔 net_pnl vs summary net_pnl
        d_amt = None
        if ts.get("net_pnl") is not None:
            d_amt = round(sum_net - f(ts.get("net_pnl")), 6)
        ok_amt = d_amt is None or abs(d_amt) < 1e-4
        if ok_cnt and ok_amt:
            l7_ok += 1
        else:
            l7_bad[d.name] = {"笔数": cnts, "金额差": d_amt}
        l7_detail.append({"run": d.name, "trade_count": ts.get("trade_count"), "逐笔行数": n_tr,
                          "executed_trades": fn.get("executed_trades"),
                          "summary.net_pnl": ts.get("net_pnl"),
                          "Σ逐笔net_pnl": round(sum_net, 6), "金额差": d_amt,
                          "identity.chain_ok": idn.get("chain_ok"),
                          "reconcile.cash_identity_ok": rc.get("cash_identity_ok"),
                          "unrealized_net_open": rc.get("unrealized_net_open")})
        if n_tr or ts.get("trade_count"):
            # 只把**有 trading_summary.net_pnl** 的 run 纳入三分对账：
            # 早期 legacy run 没有 trading_summary，net_pnl 读成 0 会把它变成"空对空"，
            # 拉出一个几千 USDT 的假残差（这里踩过一次）。
            if ts.get("net_pnl") is not None:
                nr_detail.append({
                    "run": d.name, "笔数": n_tr,
                    "已实现(非force_exit)": round(real_closed, 6),
                    "期末技术结算(force_exit)": round(real_force, 6),
                    "盯市(unrealized_net_open)": f(rc.get("unrealized_net_open")),
                    "summary.net_pnl": f(ts.get("net_pnl")),
                    "残差": round(real_closed + real_force
                                + f(rc.get("unrealized_net_open")) - f(ts.get("net_pnl")), 6),
                    "n_force": n_force, "n_normal": n_normal})
    DETAIL["L7"] = {"核对": len(l7_detail), "一致": l7_ok, "不一致": l7_bad,
                    "样本": l7_detail[:5],
                    "identity.chain_ok 全 True": all(x["identity.chain_ok"] is not False
                                                    for x in l7_detail),
                    "cash_identity_ok 全 True": all(x["reconcile.cash_identity_ok"] is not False
                                                    for x in l7_detail)}
    chk("L7", "回测 run 三处对账：summary.trade_count / trades.jsonl 行数 / funnel.executed_trades，"
              "且 Σ逐笔 net_pnl = summary.net_pnl",
        "全部一致", f"核对 {len(l7_detail)} 个，一致 {l7_ok}，不一致 {len(l7_bad)}",
        not l7_bad and l7_ok > 0,
        "字段名：trades.jsonl 用 net_pnl（不是 profit_abs）；summary 用 trading_summary.net_pnl")
    chk("L7b", "summary 自带的 identity.chain_ok 与 reconcile.cash_identity_ok 全部为 True",
        "全部 True",
        f"chain_ok 全 True={DETAIL['L7']['identity.chain_ok 全 True']}；"
        f"cash_identity_ok 全 True={DETAIL['L7']['cash_identity_ok 全 True']}",
        DETAIL["L7"]["identity.chain_ok 全 True"] and DETAIL["L7"]["cash_identity_ok 全 True"],
        "这是回测侧自己算的标识贯通与现金恒等式，与生产账本的 L3 互补")

    # NEW-REALIZED 三分：已实现 / 盯市 / 期末技术结算（force_exit）
    cand = [x for x in nr_detail if x["n_force"] > 0]
    tot_real = sum(x["已实现(非force_exit)"] for x in nr_detail)
    tot_force = sum(x["期末技术结算(force_exit)"] for x in nr_detail)
    tot_mark = sum(x["盯市(unrealized_net_open)"] for x in nr_detail)
    n_force = sum(x["n_force"] for x in nr_detail)
    n_normal = sum(x["n_normal"] for x in nr_detail)
    sm_tot = sum(x["summary.net_pnl"] for x in nr_detail)
    resid = round((tot_real + tot_force + tot_mark) - sm_tot, 6)
    per_run_bad = {x["run"]: x["残差"] for x in nr_detail if abs(x["残差"]) > 1e-4}
    DETAIL["NEW-REALIZED"] = {
        "run数": len(nr_detail), "含force_exit的run数": len(cand),
        "已实现_笔数": n_normal, "已实现_金额": round(tot_real, 6),
        "期末技术结算_笔数": n_force, "期末技术结算_金额": round(tot_force, 6),
        "盯市_金额": round(tot_mark, 6),
        "Σsummary.net_pnl": round(sm_tot, 6), "残差": resid,
        "逐run残差超限": per_run_bad, "样本": cand[:3],
    }
    chk("NEW-REALIZED", "已实现 / 盯市 / 期末技术结算三分，逐 run 加总 = summary.net_pnl",
        "每个 run 残差 <1e-4",
        f"{len(nr_detail)} 个 run（含 trading_summary.net_pnl）：已实现 {n_normal} 笔 "
        f"{tot_real:.6f} + 期末技术结算 {n_force} 笔 {tot_force:.6f} + 盯市 {tot_mark:.6f} "
        f"= {tot_real + tot_force + tot_mark:.6f}；Σsummary={sm_tot:.6f}；残差={resid}；"
        f"逐run超限 {len(per_run_bad)} 个",
        abs(resid) < 1e-4 and not per_run_bad and (n_normal + n_force) > 0,
        "force_exit 单列为'期末技术结算'，不混进已实现；"
        f"这 {len(nr_detail)} 个 run 里含 force_exit 的 {len(cand)} 个 ⇒ "
        "期末结算这一项在数据上是 0，属'无样本'而非'已证明不混算'")

    # ══════════════════════════════════════════════════════════════
    sec("F. 汇总")
    ok_n = sum(1 for x in ITEMS if x["判定"] == "通过")
    ng_n = sum(1 for x in ITEMS if x["判定"] == "不通过")
    nv_n = sum(1 for x in ITEMS if x["判定"] == "未验证")
    print(f"合计 {len(ITEMS)} 条：通过 {ok_n}，不通过 {ng_n}，未验证 {nv_n}")
    for x in ITEMS:
        if x["判定"] != "通过":
            print(f"  不通过 {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")

    res = {"生成时刻": DETAIL["生成时刻"], "明细": DETAIL, "断言": ITEMS,
           "汇总": {"总数": len(ITEMS), "通过": ok_n, "不通过": ng_n, "未验证": nv_n}}
    (OUT / "09_账本身份.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = ["# 09 账本闭环 / 执行身份 / 版本一致", "",
          f"- 生成时刻：{res['生成时刻']}", "- 全程只读生产（`mode=ro`），不写不删不补造", "",
          "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |", "|---|---|---|---|---|---|"]
    for x in ITEMS:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | "
                  f"{x['判定']} | {x['备注']} |")
    md += ["", f"**合计 {len(ITEMS)} 条：通过 {ok_n}，不通过 {ng_n}，未验证 {nv_n}**"]
    (OUT / "09_账本身份.md").write_text("\n".join(md), encoding="utf-8")
    print(f"已写出 {OUT/'09_账本身份.json'} / .md")


if __name__ == "__main__":
    main()
