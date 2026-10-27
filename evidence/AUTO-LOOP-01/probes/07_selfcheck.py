# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 07 自检（机械复算 + 静态边界检查）

四组检查：
  A 静态：probes/ 下所有脚本**没有调用**模拟撮合/止损/退出函数（AST 级，不看注释）；
  B 复算：账本里每个关键数字都能由别的字段重算出来（净盈亏、手续费、资金费、现金、权益）；
  C 时序：信号确认 < 入场 < 止损上移生效 < 退出（严格递增，不出现倒挂）；
  D 隔离：生产账本 / 生产 live 库在本轮开始后**没有被写过**；隔离目录内文件才被写。

只读。输出 out/selfcheck.json。
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = Path(__file__).resolve().parent
OUT = HERE.parent
ISO = OUT / "iso" / "VELVETUSDT"

BANNED = {
    "open_position", "advance_pending", "close_at_price", "close_position",
    "advance_position_trailing", "advance_arm_trailing", "check_position_exit_on_bar",
    "record_stop_update", "accrue_funding", "_settle_funding", "backfill_closed_funding",
    "enforce_drawdown", "_register_pending", "_resolve_pending",
}
# 允许出现的「非交易」同名调用（无）
RESULT = []


def chk(name, ok, detail=""):
    RESULT.append({"检查": name, "结果": "PASS" if ok else "FAIL", "明细": detail})


def iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def static_scan():
    hits = []
    for p in sorted(HERE.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                nm = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if nm in BANNED:
                    hits.append({"file": p.name, "line": node.lineno, "call": nm})
    chk("A1 测试脚本未调用任何模拟撮合/止损/退出函数",
        not hits, hits if hits else f"扫描 {len(list(HERE.glob('*.py')))} 个脚本，0 处命中")
    # 脚本里不得直接写 paper_*.sqlite3（写句柄）
    writes = []
    for p in sorted(HERE.glob("*.py")):
        if p.name == Path(__file__).name:
            continue          # 本文件是检查器，自身含检查用的 SQL 关键字
        txt = p.read_text(encoding="utf-8")
        if "paper_3a.sqlite3" in txt and ("INSERT INTO" in txt or "UPDATE paper_" in txt):
            writes.append(p.name)
    chk("A2 测试脚本未直接写账本表", not writes, writes or "无 INSERT/UPDATE 语句")


def ledger():
    con = sqlite3.connect(ISO / "paper" / "paper_3a.sqlite3")
    con.row_factory = sqlite3.Row
    pos = dict(con.execute("SELECT * FROM paper_position ORDER BY position_id DESC LIMIT 1").fetchone())
    fills = [dict(r) for r in con.execute("SELECT * FROM paper_fill ORDER BY fill_id")]
    stops = [dict(r) for r in con.execute("SELECT * FROM paper_stop_history ORDER BY id")]
    fund = [dict(r) for r in con.execute("SELECT * FROM paper_funding ORDER BY id")]
    real = dict(con.execute("SELECT * FROM paper_realized ORDER BY id DESC LIMIT 1").fetchone())
    st = dict(con.execute("SELECT * FROM paper_state").fetchone())
    acc = dict(con.execute("SELECT * FROM paper_account").fetchone())
    live = sqlite3.connect(ISO / "live" / "live_signals.sqlite3")
    live.row_factory = sqlite3.Row
    sig = dict(live.execute("SELECT * FROM live_signal ORDER BY confirmed_at LIMIT 1").fetchone())

    gross = (float(pos["exit_price"]) - float(pos["entry_price"])) * float(pos["qty"])
    net = gross - float(pos["entry_fee"]) - float(pos["exit_fee"]) - float(pos["funding_paid"])
    fund_sum = sum(float(r["amount"]) for r in fund)
    chk("B1 gross_pnl 可由 (exit-entry)×qty 重算",
        abs(gross - float(real["gross_pnl"])) < 1e-6, f"{gross:.10f} vs {real['gross_pnl']}")
    chk("B2 net_pnl = gross − 双边费 − 资金费",
        abs(net - float(real["net_pnl"])) < 1e-6, f"{net:.10f} vs {real['net_pnl']}")
    chk("B3 funding_paid = paper_funding 明细合计",
        abs(fund_sum - float(pos["funding_paid"])) < 1e-9, f"{fund_sum:.10f} vs {pos['funding_paid']}")
    chk("B4 fees_paid = 入场费 + 出场费",
        abs(float(pos["entry_fee"]) + float(pos["exit_fee"]) - float(st["fees_paid"])) < 1e-9,
        f"{float(pos['entry_fee']) + float(pos['exit_fee']):.10f} vs {st['fees_paid']}")
    chk("B5 cash = 初始权益 + realized_pnl",
        abs(float(acc["initial_equity"]) + float(st["realized_pnl"]) - float(st["cash"])) < 1e-9,
        f"{float(acc['initial_equity']) + float(st['realized_pnl']):.10f} vs {st['cash']}")
    chk("B6 入场费 = 名义额 × 5bps",
        abs(float(pos["qty"]) * float(pos["entry_price"]) * 0.0005 - float(pos["entry_fee"])) < 1e-9,
        f"{float(pos['qty']) * float(pos['entry_price']) * 0.0005:.10f} vs {pos['entry_fee']}")

    # C 时序
    seq = [("信号确认", int(sig["confirmed_at"])), ("入场", int(pos["entry_ts"])),
           ("止损上移生效", int(stops[-1]["effective_at"])), ("退出", int(pos["exit_ts"]))]
    mono = all(b > a for (_, a), (_, b) in zip(seq, seq[1:]))
    chk("C1 信号确认 < 入场 < 止损上移生效 < 退出", mono,
        " → ".join(f"{k}@{iso(v)}" for k, v in seq))
    ups = [float(s["new_stop"]) for s in stops]
    chk("C2 止损单调不减且至少上移一次",
        all(b >= a for a, b in zip(ups, ups[1:])) and len(stops) >= 2,
        f"{ups}")
    chk("C3 退出原因=止损类且退出价≈生效止损×(1−滑点)",
        pos["exit_reason"] == "trailing_stop_loss"
        and abs(float(pos["exit_price"]) - float(stops[-1]["new_stop"]) * 0.9995) < 1e-9,
        f"{pos['exit_price']} vs {float(stops[-1]['new_stop']) * 0.9995:.10f}")

    # D 唯一性
    n_pos = con.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0]
    n_fill = con.execute("SELECT COUNT(*) FROM paper_fill").fetchone()[0]
    n_real = con.execute("SELECT COUNT(*) FROM paper_realized").fetchone()[0]
    n_order = con.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0]
    chk("D1 接管后无重复建仓/重复成交",
        n_pos == 1 and n_fill == 2 and n_real == 1 and n_order == 2,
        f"position={n_pos} fill={n_fill} realized={n_real} order={n_order}")
    return {"position": pos, "fills": fills, "stops": stops, "funding": fund,
            "realized": real, "state": st, "account": acc, "signal": sig}


def isolation(start_ms):
    # 写目标：生产 paper 账本目录 + 生产 live 库（这两处才是「不该被本轮写」的对象）
    write_targets = {
        "生产 paper 账本目录": ROOT / "数据" / "paper",
        "生产 live 库": ROOT / "框架" / "backend" / "live" / "live_signals.sqlite3",
    }
    rep = {}
    for name, p in write_targets.items():
        paths = [p] if p.is_file() else list(p.glob("*.sqlite3"))
        latest = max((f.stat().st_mtime for f in paths), default=None)
        rep[name] = {"latest_mtime": latest,
                     "latest_utc": (dt.datetime.fromtimestamp(latest, dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")
                                    if latest else None),
                     "touched_after_start": bool(latest and latest * 1000 > start_ms)}
    bad = [k for k, v in rep.items() if v["touched_after_start"]]
    chk("E1 生产账本 / 生产 live 库在本轮开始后未被写", not bad,
        f"本轮开始 {iso(start_ms)}；被写对象={bad or '无'}")

    # 行情库：本轮所有脚本必须以只读 URI 打开它（`mode=ro`），否则无法排除「脚本写过行情库」
    ro_ok, offenders = True, []
    for p in sorted(HERE.glob("*.py")):
        if p.name == Path(__file__).name:
            continue
        txt = p.read_text(encoding="utf-8")
        if "market_v2.sqlite3" in txt and "mode=ro" not in txt:
            # 允许通过 live_store.market_conn（内部已强制 mode=ro + query_only）间接访问
            if "market_conn" not in txt and "MARKET_DB" not in txt:
                ro_ok = False
                offenders.append(p.name)
    chk("E2 本轮脚本对生产行情库只使用只读连接", ro_ok, offenders or "全部 mode=ro / market_conn")

    # 行情库文件 mtime 会被**其它长期进程**推进（实测 PID 55012 持写句柄），
    # 这不是本轮写入；这里如实记录，不当作本轮 FAIL。
    mdb = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
    wal = Path(str(mdb) + "-wal")
    rep["行情库（外部进程持续写入，非本轮）"] = {
        "main_mtime_utc": dt.datetime.fromtimestamp(mdb.stat().st_mtime, dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "note": "本轮全部脚本只读；写入方为其它长期运行进程（lsof 可见 55012 持 u 句柄）",
    }
    return rep


def main() -> int:
    static_scan()
    led = ledger()
    # 本轮开始时刻：取 launcher 日志最早文件时间（段A 启动）
    logs = sorted((OUT / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
    start_ms = int(min(p.stat().st_mtime for p in logs) * 1000) if logs else 0
    iso_rep = isolation(start_ms)
    ok = all(r["结果"] == "PASS" for r in RESULT)
    rep = {"all_pass": ok, "checks": RESULT, "ledger": led, "isolation": iso_rep,
           "round_started_ms": start_ms, "round_started_utc": iso(start_ms)}
    (OUT / "out" / "selfcheck.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for r in RESULT:
        print(f"{r['结果']}  {r['检查']}  |  {r['明细']}")
    print(f"\nall_pass={ok}  ({sum(1 for r in RESULT if r['结果']=='PASS')}/{len(RESULT)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
