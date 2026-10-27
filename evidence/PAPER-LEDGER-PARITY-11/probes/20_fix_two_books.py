#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20_fix_two_books.py — 把 `paper_state` 校正到正式口径，并留痕、可回滚。

缺陷（已由 10_two_books_parity.py 证明）：
  · `paper_state.cash` = 4995.654545，其中 -4.345455 来自 3 笔 `voided=1` 的合成测试
  · `account_view()` 默认口径（接口 / 风控 / 显示）= 初始权益 + Σnet_pnl(voided=0) = 5000.00
  · 差额 +4.345455 **恒定、不收敛** ⇒ 接口永远比账本多报这笔钱，且随每笔交易传递
  · `recover_report()` 同一函数内 `n_real` 过滤了 voided，`net` 没过滤 ⇒ 同口径不一致

本脚本只做**数据**修正；代码那处由人改 `paper_engine.py`（见 README 的「改了什么」）。

安全措施：
  · 默认 dry-run，必须显式 `--apply` 才写
  · 写前把账本整库备份到 `out/backup_paper_3a_<UTC>.sqlite3`
  · 写前校验「fees_paid 全部来自作废记录」，不等就不动（防误伤真实手续费）
  · 写后在 `paper_correction` 落一条留痕（该表专为此设计，当前为空）
  · 只改 `paper_state` 的 3 个数值字段，**不删任何审计行**（`paper_realized` 3 行原样保留）

用法：
    python 20_fix_two_books.py            # dry-run，只打印将要做什么
    python 20_fix_two_books.py --apply    # 真正执行
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "out"
LEDGER = ROOT / "数据/paper/paper_3a.sqlite3"

ARM = "3a"
ACC = f"paper43_{ARM}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LEDGER)
    con.row_factory = sqlite3.Row

    # ── 1. 读当前值 + 前置校验 ────────────────────────────────────────────
    st = con.execute("SELECT * FROM paper_state WHERE account_id=?", (ACC,)).fetchone()
    act = con.execute("SELECT initial_equity FROM paper_account WHERE account_id=?",
                      (ACC,)).fetchone()
    init_eq = float(act[0]) if act and act[0] is not None else 5000.0
    cur = {k: float(st[k]) for k in ("cash", "realized_pnl", "fees_paid", "funding_paid")}

    rows = [dict(r) for r in con.execute(
        "SELECT id,symbol,net_pnl,entry_fee,exit_fee,voided,void_reason FROM paper_realized "
        "WHERE account_id=? ORDER BY id", (ACC,))]
    voided = [r for r in rows if int(r["voided"] or 0) == 1]
    live = [r for r in rows if int(r["voided"] or 0) == 0]

    voided_net = sum(float(r["net_pnl"]) for r in voided)
    voided_fee = sum(float(r["entry_fee"]) + float(r["exit_fee"]) for r in voided)
    live_net = sum(float(r["net_pnl"]) for r in live)
    live_fee = sum(float(r["entry_fee"]) + float(r["exit_fee"]) for r in live)

    target = {
        "cash": init_eq + live_net,          # 正式口径
        "realized_pnl": live_net,
        "fees_paid": live_fee,
        "funding_paid": cur["funding_paid"],  # 无资金费事件，保持原值
    }

    # 前置校验 1：作废净额应等于当前 realized_pnl（说明那笔钱全部来自作废记录）
    c1 = abs(voided_net - cur["realized_pnl"]) < 1e-6
    # 前置校验 2：fees_paid 应等于作废记录的手续费合计（否则可能混了真实手续费）
    c2 = abs(voided_fee - cur["fees_paid"]) < 1e-6
    # 前置校验 3：没有未作废的已实现记录（有的话本脚本不该动，交人工）
    c3 = len(live) == 0
    # 前置校验 4：没有未平仓持仓（有的话现金还要减未平仓费，本脚本不处理）
    n_open = con.execute("SELECT COUNT(*) FROM paper_position WHERE account_id=? "
                         "AND status='open' AND COALESCE(voided,0)=0", (ACC,)).fetchone()[0]
    c4 = n_open == 0

    ok = c1 and c2 and c3 and c4
    plan = {
        "probe": "PAPER-LEDGER-PARITY-11/fix",
        "generated_utc": now_iso(),
        "账本": str(LEDGER.relative_to(ROOT)),
        "模式": "apply" if args.apply else "dry-run",
        "初始权益": init_eq,
        "修正前": cur,
        "修正后": target,
        "delta": {k: round(target[k] - cur[k], 10) for k in cur},
        "作废记录": [{"id": r["id"], "net_pnl": r["net_pnl"],
                      "fee": round(float(r["entry_fee"]) + float(r["exit_fee"]), 10),
                      "void_reason": r["void_reason"]} for r in voided],
        "作废净额合计": round(voided_net, 10),
        "作废手续费合计": round(voided_fee, 10),
        "未作废记录数": len(live),
        "未平仓持仓数": n_open,
        "前置校验": {
            "作废净额 == 当前 realized_pnl": c1,
            "作废手续费 == 当前 fees_paid": c2,
            "无未作废已实现记录": c3,
            "无未平仓持仓": c4,
        },
        "前置校验全过": ok,
    }

    if not ok:
        plan["结论"] = "前置校验未过，拒绝修改（可能混有真实交易，需人工判断）"
        (OUT / "20_fix_two_books.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        con.close()
        print(json.dumps(plan["前置校验"], ensure_ascii=False, indent=1))
        print(f"\n{plan['结论']}")
        return 2

    # ── 2. dry-run 到此为止 ───────────────────────────────────────────────
    if not args.apply:
        plan["结论"] = "dry-run：校验全过，未写入。加 --apply 执行"
        (OUT / "20_fix_two_books.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        con.close()
        print(f"初始权益 {init_eq}")
        print(f"修正前 {cur}")
        print(f"修正后 {target}")
        print(f"delta  {plan['delta']}")
        print(f"\n{plan['结论']}")
        return 0

    # ── 3. 备份 → 写库 → 留痕 ─────────────────────────────────────────────
    con.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = OUT / f"backup_paper_3a_{stamp}.sqlite3"
    shutil.copy2(LEDGER, bak)
    plan["备份"] = str(bak.relative_to(ROOT))
    plan["备份大小"] = bak.stat().st_size

    con = sqlite3.connect(LEDGER)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            "UPDATE paper_state SET cash=?, realized_pnl=?, fees_paid=?, funding_paid=?, "
            "updated_at=? WHERE account_id=?",
            (target["cash"], target["realized_pnl"], target["fees_paid"],
             target["funding_paid"], 1792700000000, ACC))
        detail = json.dumps({
            "before": cur, "after": target,
            "why": ("3 笔 paper_realized 已标 voided=1（synthetic_test_isolation），"
                    "但其亏损与手续费仍留在 paper_state，导致账本底座与接口/风控口径"
                    "（初始权益+Σnet(voided=0)）差 4.345455，且差额恒定不收敛。"
                    "本次只校正 paper_state 数值，审计行 paper_realized 3 行原样保留。"),
            "backup": str(bak.name),
            "evidence": "evidence/PAPER-LEDGER-PARITY-11/",
        }, ensure_ascii=False)
        con.execute(
            "INSERT INTO paper_correction(account_id,kind,ref_id,symbol,field,delta,reason,"
            "detail,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ACC, "voided_pnl_rollback", None, None, "paper_state", round(
                target["cash"] - cur["cash"], 10),
             "voided_synthetic_test_pnl_not_rolled_back", detail, 1792700000000))
        con.commit()

        after = con.execute("SELECT * FROM paper_state WHERE account_id=?", (ACC,)).fetchone()
        plan["写入后实际值"] = {k: float(after[k]) for k in
                          ("cash", "realized_pnl", "fees_paid", "funding_paid")}
        plan["paper_correction 条数"] = con.execute(
            "SELECT COUNT(*) FROM paper_correction WHERE account_id=?", (ACC,)).fetchone()[0]
        plan["paper_realized 行数（应仍为 3）"] = con.execute(
            "SELECT COUNT(*) FROM paper_realized WHERE account_id=?", (ACC,)).fetchone()[0]
        plan["结论"] = "已修正"
    except Exception as exc:                                  # noqa: BLE001
        con.rollback()
        plan["结论"] = f"写入失败已回滚: {type(exc).__name__}: {exc}"
        plan["回滚方式"] = f"cp {bak} {LEDGER}"
        (OUT / "20_fix_two_books.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        con.close()
        print(plan["结论"])
        return 1
    finally:
        try:
            con.close()
        except Exception:                                     # noqa: BLE001
            pass

    (OUT / "20_fix_two_books.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"备份 → {bak}")
    print(f"修正前 {cur}")
    print(f"写入后 {plan['写入后实际值']}")
    print(f"paper_correction {plan['paper_correction 条数']} 条 / "
          f"paper_realized 仍 {plan['paper_realized 行数（应仍为 3）']} 行")
    print(f"\n{plan['结论']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
