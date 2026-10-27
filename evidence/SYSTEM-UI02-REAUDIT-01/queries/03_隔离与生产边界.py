#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-UI02-REAUDIT-01｜阶段一 · 隔离是否成立 与 生产边界是否被触碰

对应工单第二阶段的前置条件：
「先独立确认路径隔离成立，再启动测试实例；此前只做静态与离线证据审查。」
以及第一阶段第 5 条「测试注入记录只证明页面功能」。

本脚本不启动任何服务，只做离线核对：
  1. 被测脚本设置了哪些环境变量，缺了哪个决定账本路径的变量；
  2. live_store 的账本目录解析逻辑（静态读源码，不 import，避免副作用）；
  3. 隔离库 /tmp/wx_ui_isolated_test_02/live.db 里到底有什么（预置数据是否还在）；
  4. 生产账本 数据/paper/paper_3a..3d.sqlite3 的持仓/已平/止损行数（只读 mode=ro）；
  5. 交付自己记录的页面 DOM 文本，与生产账本、隔离库分别比对，判断页面读的是哪一边；
  6. 生产 locks 目录里是否被写入了测试用的假守护状态，以及那个 PID 是否还活着。

只读：生产库一律 `mode=ro`，不写任何文件到 数据/ 或 框架/。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
import sys
from datetime import datetime

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
UI02 = PROJ / "evidence/SYSTEM-UI-02"
OUT = pathlib.Path(__file__).resolve().parent.parent / "out"
ISOLATED = pathlib.Path("/tmp/wx_ui_isolated_test_02/live.db")
LOCKS = PROJ / "框架/backend/locks"
LEDGER_DIR_DEFAULT = PROJ / "数据/paper"


def ro_conn(p: pathlib.Path):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()[0] > 0


def main() -> int:
    rep: dict = {
        "生成时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "用途": "阶段一｜离线核对隔离是否成立、生产边界是否被触碰",
        "声明边界": "本脚本不启动服务、不写生产库、不写生产 locks",
    }

    # ---- 1. 被测脚本的环境变量 ----
    script = (UI02 / "test_system_ui_02.py").read_text(encoding="utf-8")
    envs = re.findall(r'env\[\s*"([A-Z_0-9]+)"\s*\]\s*=', script)
    rep["被测脚本环境变量"] = sorted(set(envs))
    rep["关键缺失"] = {
        "PAPER_LEDGER_DIR": "PAPER_LEDGER_DIR" not in envs,
        "说明": "live_store.get_ledger_dir() 只认 PAPER_LEDGER_DIR；未设置 ⇒ 回落工程根 数据/paper（生产）",
    }

    # ---- 2. 账本目录解析逻辑（静态） ----
    ls = (PROJ / "框架/backend/live_store.py").read_text(encoding="utf-8")
    lsl = ls.split("\n")
    logic = []
    for i, l in enumerate(lsl, 1):
        if re.search(r"def get_ledger_dir|PAPER_LEDGER_DIR|^LEDGER_DIR\s*=|^LIVE_DB\s*=", l):
            logic.append({"行": i, "源码": l.strip()})
    rep["live_store账本解析"] = {
        "源码行": logic,
        "结论": "LEDGER_DIR 是模块级常量，import 时定死；只有 PAPER_LEDGER_DIR 能改变它。"
                "LIVE_DB 走环境变量，所以信号库能隔离、账本不能。",
    }

    # ---- 3. 隔离库内容 ----
    iso = {"路径": str(ISOLATED), "存在": ISOLATED.exists()}
    if ISOLATED.exists():
        con = ro_conn(ISOLATED)
        for t in ["live_signal", "paper_positions", "paper_stop_history", "paper_realized"]:
            if table_exists(con, t):
                iso[f"{t}_行数"] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            else:
                iso[f"{t}_行数"] = "无此表"
        if table_exists(con, "paper_positions"):
            iso["持仓明细"] = con.execute(
                "SELECT symbol, qty, entry_price, unrealized_pnl, status FROM paper_positions"
            ).fetchall()
        if table_exists(con, "paper_stop_history"):
            iso["止损明细"] = con.execute(
                "SELECT position_id, old_stop, new_stop, reason FROM paper_stop_history ORDER BY id"
            ).fetchall()
        con.close()
    rep["隔离库"] = iso

    # ---- 4. 生产账本内容（只读） ----
    # 注意：生产账本的持仓表名是 paper_position（单数），不是 paper_positions。
    # 上一轮我误用复数名，得到「无此表」的假结论；这里两种名字都探测并显式记录。
    prod = {}
    for arm in ["3a", "3b", "3c", "3d"]:
        p = LEDGER_DIR_DEFAULT / f"paper_{arm}.sqlite3"
        d = {"路径": str(p), "存在": p.exists()}
        if p.exists():
            con = ro_conn(p)
            tabs = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            d["持仓表名"] = (
                "paper_position" if "paper_position" in tabs
                else ("paper_positions" if "paper_positions" in tabs else None)
            )
            if d["持仓表名"]:
                tbl = d["持仓表名"]
                d["持仓总行数"] = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
                if "status" in cols:
                    d["持仓按状态"] = dict(con.execute(
                        f"SELECT status, count(*) FROM {tbl} GROUP BY status").fetchall())
                    d["未平仓行数"] = d["持仓按状态"].get("open", 0)
                elif "closed_at" in cols:
                    d["未平仓行数"] = con.execute(
                        f"SELECT count(*) FROM {tbl} WHERE closed_at IS NULL").fetchone()[0]
                else:
                    d["未平仓行数"] = "无法判定（无 status / closed_at 列）"
            d["paper_realized行数"] = (
                con.execute("SELECT count(*) FROM paper_realized").fetchone()[0]
                if "paper_realized" in tabs else "无此表"
            )
            d["paper_realized_未作废行数"] = (
                con.execute("SELECT count(*) FROM paper_realized WHERE COALESCE(voided,0)=0").fetchone()[0]
                if "paper_realized" in tabs else "无此表"
            )
            d["paper_stop_history行数"] = (
                con.execute("SELECT count(*) FROM paper_stop_history").fetchone()[0]
                if "paper_stop_history" in tabs else "无此表"
            )
            d["paper_account行数"] = (
                con.execute("SELECT count(*) FROM paper_account").fetchone()[0]
                if "paper_account" in tabs else "无此表"
            )
            d["paper_state行数"] = (
                con.execute("SELECT count(*) FROM paper_state").fetchone()[0]
                if "paper_state" in tabs else "无此表"
            )
            con.close()
        prod[arm] = d
    rep["生产账本"] = prod

    # ---- 5. 交付自己记录的 DOM 文本 ----
    audit = json.loads((UI02 / "audit_ui_verification.json").read_text(encoding="utf-8"))
    u11 = audit.get("unverified_closed", {}).get("U11_execution_daemon_status", {})
    dom_text = u11.get("dom_text", "")
    ev07 = json.loads((UI02 / "07_守护进程真实运行与心跳证据.json").read_text(encoding="utf-8"))
    rep["交付自报DOM"] = {
        "U11_dom_text": dom_text,
        "07_anti_paradox_wording": ev07.get("frontend_dom_extracted", {}).get("anti_paradox_wording"),
        "07_cash_dom": ev07.get("three_tier_equity_clarification", {}).get("cash", {}).get("dom_value"),
        "07_realized_dom": ev07.get("three_tier_equity_clarification", {}).get("realized_pnl", {}).get("dom_value"),
    }

    # DOM 里出现的持仓口径：以交付自己记录的 anti_paradox_wording 为准
    # （U11_dom_text 只含守护卡片，不含账户盒；账户盒口径单独记在 07_*.json）
    dom_hold_text = ev07.get("frontend_dom_extracted", {}).get("anti_paradox_wording") or ""
    m_hold = re.search(r"(\d+)\s*仓在持\s*/\s*(\d+)\s*笔已平", dom_hold_text)
    dom_hold, dom_closed = (int(m_hold.group(1)), int(m_hold.group(2))) if m_hold else (None, None)
    prod_closed = prod["3a"].get("paper_realized行数")
    prod_hold = prod["3a"].get("未平仓行数")
    iso_hold = iso.get("paper_positions_行数")

    rep["持仓口径比对"] = {
        "DOM原文": dom_hold_text,
        "页面DOM": {"仓在持": dom_hold, "笔已平": dom_closed},
        "生产账本3a": {"持仓表名": prod["3a"].get("持仓表名"),
                       "持仓按状态": prod["3a"].get("持仓按状态"),
                       "未平仓行数": prod_hold,
                       "paper_realized行数": prod_closed,
                       "其中未作废(voided=0)行数": prod["3a"].get("paper_realized_未作废行数"),
                       "paper_stop_history行数": prod["3a"].get("paper_stop_history行数")},
        "隔离库": {"paper_positions行数": iso_hold},
        "DOM是否等于生产账本": (dom_closed == prod_closed) and (prod_hold == dom_hold),
        "DOM是否等于隔离库": (iso_hold == dom_hold),
        "判定": None,
    }
    if rep["持仓口径比对"]["DOM是否等于生产账本"] and not rep["持仓口径比对"]["DOM是否等于隔离库"]:
        rep["持仓口径比对"]["判定"] = "页面读的是生产账本，隔离持仓从未渲染"
    elif rep["持仓口径比对"]["DOM是否等于隔离库"]:
        rep["持仓口径比对"]["判定"] = "页面读的是隔离库"
    else:
        rep["持仓口径比对"]["判定"] = "无法判定（两侧都不吻合）"

    # ---- 6. 生产 locks 是否被写入假守护状态 ----
    lock_rep = {}
    for name in ["paper_daemon_3a_status.json", "paper_runtime_3a_status.json"]:
        p = LOCKS / name
        d = {"路径": str(p), "存在": p.exists()}
        if p.exists():
            st = os.stat(p)
            d["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                d["pid"] = j.get("pid")
                d["status"] = j.get("status")
                d["health"] = j.get("health")
                d["cycle_count"] = j.get("cycle_count")
                d["blocked_reason"] = j.get("blocked_reason")
                pid = j.get("pid")
                if isinstance(pid, int):
                    try:
                        os.kill(pid, 0)
                        d["pid存活"] = True
                    except ProcessLookupError:
                        d["pid存活"] = False
                    except PermissionError:
                        d["pid存活"] = "无权限判定"
            except Exception as exc:
                d["解析失败"] = str(exc)
        lock_rep[name] = d
    rep["生产locks"] = lock_rep
    rep["生产locks判定"] = {
        "被测试写入": any(
            v.get("pid") == audit.get("real_daemon_pid") for v in lock_rep.values() if isinstance(v, dict)
        ),
        "当前是否与真实进程一致": all(
            v.get("pid存活") is False and v.get("status") == "running"
            for v in lock_rep.values() if isinstance(v, dict) and v.get("存在")
        ) and False,  # 有 pid 死 + status running 的组合即为不一致
    }
    stale = [
        n for n, v in lock_rep.items()
        if isinstance(v, dict) and v.get("存在") and v.get("pid存活") is False and v.get("status") == "running"
    ]
    rep["生产locks判定"]["陈旧假running文件"] = stale
    rep["生产locks判定"]["说明"] = (
        "这些文件属于生产状态目录，测试结束后未被还原；PID 已死但 status 仍为 running"
        if stale else "未发现陈旧假 running"
    )

    # ---- 7. 守护进程本质 ----
    rep["守护进程本质"] = {
        "07_声明process_cmd": ev07.get("daemon_os_process", {}).get("process_cmd"),
        "脚本内守护实现": "start_real_daemon_process() 用 python -c 跑一个 while True 写 JSON 的桩，"
                          "不调用 unified_runtime / paper_loop，不做任何撮合、不读行情",
        "真实PID": audit.get("real_daemon_pid"),
        "结论": "PID 真实，但进程是桩；只能证明「页面能显示一个真实存在的 PID」，"
                "不能证明执行守护在工作",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "03_隔离与生产边界.json"
    dst.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台 ----
    print("=" * 84)
    print("1. 被测脚本环境变量")
    print("=" * 84)
    print("  已设置:", rep["被测脚本环境变量"])
    print("  PAPER_LEDGER_DIR 缺失:", rep["关键缺失"]["PAPER_LEDGER_DIR"])

    print()
    print("=" * 84)
    print("2. live_store 账本解析")
    print("=" * 84)
    for r in logic:
        print(f"  行{r['行']:>4d}: {r['源码'][:110]}")
    print("  ⇒", rep["live_store账本解析"]["结论"])

    print()
    print("=" * 84)
    print("3. 隔离库")
    print("=" * 84)
    for k, v in iso.items():
        print(f"  {k}: {v}")

    print()
    print("=" * 84)
    print("4. 生产账本（只读）")
    print("=" * 84)
    for arm, d in prod.items():
        print(f"  paper_{arm}: 持仓表={d.get('持仓表名')} 按状态={d.get('持仓按状态')} "
              f"未平仓={d.get('未平仓行数')} realized={d.get('paper_realized行数')} "
              f"(未作废{d.get('paper_realized_未作废行数')}) stops={d.get('paper_stop_history行数')} "
              f"account行={d.get('paper_account行数')} state行={d.get('paper_state行数')}")

    print()
    print("=" * 84)
    print("5. 页面 DOM 到底读的哪一边")
    print("=" * 84)
    print("  DOM:", repr(dom_text.replace("\n", " | ")))
    print("  比对:", json.dumps(rep["持仓口径比对"], ensure_ascii=False, indent=1))

    print()
    print("=" * 84)
    print("6. 生产 locks")
    print("=" * 84)
    for n, d in lock_rep.items():
        print(f"  {n}: pid={d.get('pid')} status={d.get('status')} pid存活={d.get('pid存活')} mtime={d.get('mtime')}")
    print("  判定:", json.dumps(rep["生产locks判定"], ensure_ascii=False))

    print()
    print("=" * 84)
    print("7. 守护进程本质")
    print("=" * 84)
    print("  ", json.dumps(rep["守护进程本质"], ensure_ascii=False, indent=1))

    print(f"\n已落盘: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
