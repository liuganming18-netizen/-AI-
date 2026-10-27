#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""30_verify_fix.py — 账本口径修复的收口断言。

九条，缺一不算通过：
  1  paper_state.cash 四臂都等于初始权益 5000
  2  account_view() 默认口径 == paper_state.cash（四臂）
  3  recover_report().cash_equals_init_plus_net 四臂全 True
  4  审计行没被删：paper_realized 3a 仍 3 行、且都是 voided=1
  5  修正留痕在：paper_correction 3a 有 1 条 voided_pnl_rollback
  6  接口 /paper/accounts 的 equity 与账本 cash 逐个相等（四臂）
  7  备份文件存在且非空
  8  代码静态检查：recover_report 里 net 查询带 voided 过滤
  9  分叉探针结论 = 两套账已一致

用法：
    python 30_verify_fix.py     # 全过 exit 0
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "out"
API = "http://127.0.0.1:8787"

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"检查": name, "通过": bool(ok), "说明": detail})
    return bool(ok)


def get(url: str):
    # 代理坑：有 HTTP_PROXY 无 no_proxy，必须显式空 ProxyHandler
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def ledger(arm: str) -> dict:
    p = ROOT / f"数据/paper/paper_{arm}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    acc = f"paper43_{arm}"
    st = con.execute("SELECT * FROM paper_state WHERE account_id=?", (acc,)).fetchone()
    act = con.execute("SELECT initial_equity FROM paper_account WHERE account_id=?",
                      (acc,)).fetchone()
    init_eq = float(act[0]) if act and act[0] is not None else 5000.0
    offnet = con.execute(
        "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized "
        "WHERE account_id=? AND COALESCE(voided,0)=0", (acc,)).fetchone()[0]
    n_real = con.execute("SELECT COUNT(*) FROM paper_realized WHERE account_id=?",
                         (acc,)).fetchone()[0]
    n_voided = con.execute("SELECT COUNT(*) FROM paper_realized WHERE account_id=? "
                           "AND COALESCE(voided,0)=1", (acc,)).fetchone()[0]
    n_corr = con.execute("SELECT COUNT(*) FROM paper_correction WHERE account_id=?",
                         (acc,)).fetchone()[0]
    corr = [dict(r) for r in con.execute(
        "SELECT kind,field,delta,reason FROM paper_correction WHERE account_id=?", (acc,))]
    n_open = con.execute("SELECT COUNT(*) FROM paper_position WHERE account_id=? "
                         "AND status='open' AND COALESCE(voided,0)=0", (acc,)).fetchone()[0]
    con.close()
    return {
        "arm": arm, "acc": acc, "initial_equity": init_eq,
        "state_cash": float(st["cash"]), "state_realized": float(st["realized_pnl"]),
        "state_fees": float(st["fees_paid"]),
        "view_cash": init_eq + float(offnet),          # account_view 默认口径
        "expect_cash": init_eq + float(offnet) - 0.0,  # 无持仓时与 view_cash 同
        "n_realized": n_real, "n_voided": n_voided,
        "n_correction": n_corr, "corrections": corr,
        "n_open_positions": n_open,
    }


def main() -> int:
    ARMS = ("3a", "3b", "3c", "3d")
    L = {a: ledger(a) for a in ARMS}

    # 1 paper_state.cash == 初始权益
    check("1 paper_state.cash 四臂都等于初始权益",
          all(abs(v["state_cash"] - v["initial_equity"]) < 1e-6 for v in L.values()),
          str({a: round(v["state_cash"], 6) for a, v in L.items()}))

    # 2 两套账口径一致
    check("2 account_view 默认口径 == paper_state.cash（四臂）",
          all(abs(v["view_cash"] - v["state_cash"]) < 1e-6 for v in L.values()),
          str({a: round(v["view_cash"] - v["state_cash"], 9) for a, v in L.items()}))

    # 3 recover_report 自洽
    try:
        sys.path.insert(0, str(ROOT / "框架/backend"))
        import paper_engine as PE                     # noqa: E402
        rr = PE.recover_report()
        check("3 recover_report 四臂 cash_equals_init_plus_net 全 True",
              all(rr[a]["cash_equals_init_plus_net"] for a in ARMS),
              str({a: rr[a]["cash_equals_init_plus_net"] for a in ARMS}))
        rr_detail = {a: {k: rr[a][k] for k in
                         ("cash", "realized_net_sum", "expected_cash",
                          "cash_equals_init_plus_net")} for a in ARMS}
    except Exception as exc:                            # noqa: BLE001
        check("3 recover_report 四臂 cash_equals_init_plus_net 全 True", False,
              f"调用失败 {type(exc).__name__}: {exc}")
        rr_detail = {"error": f"{type(exc).__name__}: {exc}"}

    # 4 审计行没被删
    v3a = L["3a"]
    check("4 审计行未被删：3a 的 paper_realized 仍 3 行且全为 voided=1",
          v3a["n_realized"] == 3 and v3a["n_voided"] == 3,
          f"n_realized={v3a['n_realized']} n_voided={v3a['n_voided']}")

    # 5 修正留痕
    check("5 修正留痕在：paper_correction 3a 有 1 条 voided_pnl_rollback",
          v3a["n_correction"] == 1
          and v3a["corrections"][0]["kind"] == "voided_pnl_rollback"
          and v3a["corrections"][0]["reason"] == "voided_synthetic_test_pnl_not_rolled_back",
          f"corrections={v3a['corrections']}")

    # 6 接口与账本对撞
    try:
        d = get(f"{API}/api/v1/paper/accounts")
        items = {it.get("arm"): it for it in (d.get("items") or [])}
        bad = []
        for a in ARMS:
            it = items.get(a)
            if it is None:
                bad.append(f"{a}:缺")
                continue
            if abs(float(it.get("equity") or -1) - L[a]["state_cash"]) > 1e-6:
                bad.append(f"{a}:equity={it.get('equity')} vs cash={L[a]['state_cash']:.6f}")
        q = (d.get("quality") or {})
        check("6 接口 equity == 账本 cash（四臂）且 status=ok",
              not bad and q.get("status") == "ok" and d.get("missing_reason") is None,
              f"差异={bad} status={q.get('status')} missing_reason={d.get('missing_reason')}")
        api_detail = {a: {"equity": items.get(a, {}).get("equity"),
                          "cash": items.get(a, {}).get("cash")} for a in ARMS}
    except Exception as exc:                            # noqa: BLE001
        check("6 接口 equity == 账本 cash（四臂）且 status=ok", False,
              f"接口调用失败 {type(exc).__name__}: {exc}")
        api_detail = {"error": str(exc)}

    # 7 备份存在
    baks = sorted(OUT.glob("backup_paper_3a_*.sqlite3"))
    check("7 备份文件存在且非空",
          bool(baks) and baks[-1].stat().st_size > 0,
          f"{baks[-1].name if baks else '无'} "
          f"{(baks[-1].stat().st_size if baks else 0)} bytes")

    # 8 代码静态检查
    src = (ROOT / "框架/backend/paper_engine.py").read_text(encoding="utf-8")
    i = src.index("def recover_report")
    seg = src[i:i + 4000]
    m = re.search(r"net = con\.execute\((.*?)\)\.fetchone\(\)", seg, re.S)
    net_sql = m.group(1) if m else ""
    check("8 代码静态检查：recover_report 的 net 查询带 voided 过滤",
          "COALESCE(voided,0)=0" in net_sql,
          net_sql.replace("\n", " ")[:160] if net_sql else "未匹配到 net 查询")

    # 9 分叉探针结论
    p = OUT / "10_two_books_parity.json"
    pj = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    check("9 分叉探针结论 = 两套账已一致",
          "已一致" in str(pj.get("结论", "")),
          str(pj.get("结论", "缺 out/10_two_books_parity.json")))

    passed = sum(1 for r in results if r["通过"])
    total = len(results)
    ok_all = passed == total

    out = {
        "probe": "PAPER-LEDGER-PARITY-11/verify_fix",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "总断言": total, "通过": passed, "失败": total - passed,
        "结论": "全过" if ok_all else "有失败",
        "断言明细": results,
        "账本快照": {a: {k: v[k] for k in ("state_cash", "view_cash", "n_realized",
                                           "n_voided", "n_correction",
                                           "n_open_positions")} for a, v in L.items()},
        "recover_report": rr_detail,
        "接口": api_detail,
        "备份": str(baks[-1].name) if baks else None,
    }
    (OUT / "30_verify_fix.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for r in results:
        print(f"[{'OK' if r['通过'] else 'FAIL'}] {r['检查']}"
              + (f"  |  {r['说明']}" if r["说明"] and not r["通过"] else ""))
    print(f"\n{passed}/{total} 通过 → {OUT / '30_verify_fix.json'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
