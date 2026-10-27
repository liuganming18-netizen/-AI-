#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03 生产/研究执行时点差异：实际调用与影响范围（工单 D 第 2 条后半）

本脚本**不新增任何规则**，只做三件事：
  A. 静态：把两侧的公式与调用点从源码里抽出来（AST + 原文片段），不靠文字转述；
  B. 运行：在**隔离账本**里真实调用 `paper_engine.advance_position_trailing`，
     用同一根 4h K 线、不同 `decision_at`（= 挂钟落点）跑，记录 `effective_at` / `exit_ts`；
     研究侧用同一根 `c4` 对源码表达式求值（不跑整段回放，如实标注）；
  C. 影响：列出 `effective_at` / `exit_ts` 的全部消费点（含唯一索引），区分「已实测」与「未验证」。

判据（任一失败 ⇒ 退出码 1）：
  1. 两侧公式确实不同（源码片段逐字记录，且 `max(` 与 `// M15` 各自只出现在一侧）；
  2. 生产侧 `decision_at` 的实际实参是挂钟（`now` / `now_ms`），研究侧是 `c4`；
  3. 运行探针：`effective_at` 随 `decision_at` 变化，且**只在 15m 整点上**与研究侧相等；
  4. 探针必须落在本窗 `runs/` 下，且不触碰生产账本目录。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w1r1 as W  # noqa: E402

RESEARCH = W.ROOT / W.CODE
PROD = W.ROOT / W.PE
CALLERS = [W.ROOT / "框架/backend/paper_loop.py", W.ROOT / "框架/backend/unified_runtime.py"]
LEDGER = W.RUNS / "ledger_effective"


def _seg(src: str, node) -> str:
    return (ast.get_source_segment(src, node) or "").strip()


def static_side() -> dict:
    """抽取两侧公式与调用点。"""
    out = {"formulas": [], "call_sites": []}
    for rel, want in ((W.CODE, "eff"), (W.PE, "eff_ms")):
        src = (W.ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if want in names:
                    out["formulas"].append({
                        "file": rel, "line": node.lineno, "target": want,
                        "expr": _seg(src, node.value),
                        "has_grid": "// M15" in _seg(src, node.value),
                        "has_wallclock_max": "max(dec_ms" in _seg(src, node.value),
                    })
    for p in [PROD] + CALLERS:
        rel = str(p.relative_to(W.ROOT))
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name != "advance_position_trailing":
                    continue
                kw = {k.arg: _seg(src, k.value) for k in node.keywords if k.arg}
                out["call_sites"].append({
                    "file": rel, "line": node.lineno,
                    "decision_at": kw.get("decision_at"),
                    "now_ms": kw.get("now_ms"),
                    "all_kwargs": kw,
                })
    return out


def _candle():
    ot = int(datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    ct = ot + 4 * 3600 * 1000 - 1
    return ot, ct, {"symbol": "SOLUSDT", "open": 74.61, "high": 75.0, "low": 74.0,
                    "close": 74.64, "open_time": ot, "close_time": ct, "available_at": None}


def runtime_probe() -> dict:
    """隔离账本里真调生产函数；四种挂钟落点 + 一个「整点对齐」对照。"""
    if str(W.ROOT) not in sys.path:
        sys.path.insert(0, str(W.ROOT))
    if str(W.BACKEND) not in sys.path:
        sys.path.insert(0, str(W.BACKEND))
    os.environ["PAPER_LEDGER_DIR"] = str(LEDGER)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    if LEDGER.exists():
        shutil.rmtree(LEDGER)
    LEDGER.mkdir(parents=True, exist_ok=True)

    import live_store as L
    import paper_engine as PE

    ot, ct, candle = _candle()
    M15 = 900_000
    grid = (ct // M15 + 1) * M15              # 研究侧源码表达式（下面用源码片段求值复核）

    # 研究侧：把源码表达式取出来求值（同一根 c4），避免手抄公式
    expr = None
    for f in static_side()["formulas"]:
        if f["file"] == W.CODE and f["target"] == "eff" and f["has_grid"]:
            expr = f["expr"]
    assert expr, "未找到研究侧生效时刻表达式"
    # `eff = c4 if legacy_4h_exit else (c4 // M15 + 1) * M15`：按非 legacy 分支（正式口径）求值
    research_eff = eval(expr, {"__builtins__": {}},                              # noqa: S307
                        {"c4": ct, "M15": M15, "legacy_4h_exit": False})

    con = L.paper_conn("4a", create=True)
    # 两笔：一笔只上移（止损 70.0 < 候选 70.3），一笔触发退出（止损 75.0 ≥ 4h 最低 74.0）
    for stop in (70.0, 75.0):
        con.execute(
            "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,stop_price,trail_d)"
            " VALUES(?,?,?,?,?,?,?,?)",
            ("paper43_4a", "SOLUSDT", 1.0, 75.0, ot - 3600 * 1000, "open", stop, 0.05))
    con.commit()
    pids = [r["position_id"] for r in con.execute("SELECT position_id FROM paper_position ORDER BY position_id")]
    con.close()

    cases = [
        {"name": "挂钟=4h 收盘那一刻（11:59:59.999）", "decision_at": ct},
        {"name": "挂钟=收盘+1ms（12:00:00.000，恰在网格）", "decision_at": ct + 1},
        {"name": "挂钟=收盘+3 分钟（12:02:59.999）", "decision_at": ct + 180_000},
        {"name": "挂钟=收盘+15 分钟+1ms（12:15:00.000，恰在网格）", "decision_at": ct + 900_001},
    ]
    rows = []
    for c in cases:
        dt = c["decision_at"]
        r1 = PE.advance_position_trailing("4a", pids[0], candle, now_ms=dt, decision_at=dt, trail_d=0.05)
        rows.append({
            "case": c["name"], "decision_at": dt, "decision_at_utc": W.iso(dt),
            "position": "只上移", "ok": r1.get("ok"), "reason": r1.get("reason"),
            "candidate_stop": r1.get("candidate_stop"), "effective_stop": r1.get("effective_stop"),
            "stop_updated": r1.get("stop_updated"), "exit_triggered": r1.get("exit_triggered"),
            "effective_at": r1.get("effective_at"), "effective_at_utc": W.iso(r1.get("effective_at")),
            "on_15m_grid": (r1.get("effective_at") or 0) % M15 == 0,
            "equals_research_eff": r1.get("effective_at") == research_eff,
        })

    # 退出路径：单独一笔，止损 75.0，4h 最低 74.0 击穿 ⇒ 走 close_at_price(exit_ts=eff_ms)
    dt = ct + 180_000
    r2 = PE.advance_position_trailing("4a", pids[1], candle, now_ms=dt, decision_at=dt, trail_d=0.05)
    con = L.paper_conn("4a", ro=True)
    pos = dict(con.execute("SELECT position_id,status,exit_ts,exit_reason FROM paper_position WHERE position_id=?",
                           (pids[1],)).fetchone())
    hist = [dict(x) for x in con.execute(
        "SELECT position_id,effective_at,decision_at,new_stop FROM paper_stop_history ORDER BY id")]
    con.close()

    return {
        "ledger_dir": str(LEDGER.relative_to(W.ROOT)),
        "candle": {"open_time": ot, "close_time": ct,
                   "close_time_utc": W.iso(ct), "low": candle["low"],
                   "high": candle["high"], "open": candle["open"], "close": candle["close"]},
        "probe_positions": [{"position_id": pids[0], "stop_price": 70.0, "purpose": "只上移（候选 70.3 > 70.0）"},
                            {"position_id": pids[1], "stop_price": 75.0, "purpose": "触发退出（4h 最低 74.0 ≤ 75.0）"}],
        "research_eff": research_eff, "research_eff_utc": W.iso(research_eff),
        "research_eff_expr": expr,
        "grid_15m": M15,
        "stop_cases": rows,
        "exit_case": {
            "decision_at": dt, "decision_at_utc": W.iso(dt),
            "exit_triggered": r2.get("exit_triggered"),
            "effective_at": r2.get("effective_at"), "effective_at_utc": W.iso(r2.get("effective_at")),
            "exit_result_exit_ts": (r2.get("exit_result") or {}).get("exit_ts"),
            "exit_result_net_pnl": (r2.get("exit_result") or {}).get("net_pnl"),
            "position_row": pos,
            "on_15m_grid": (pos.get("exit_ts") or 0) % M15 == 0,
            "delta_vs_research_ms": (pos.get("exit_ts") or 0) - research_eff,
        },
        "stop_history": hist,
    }


def impact_scope() -> dict:
    """`effective_at` / `exit_ts` 的消费点：逐文件统计 + 唯一索引。"""
    files = sorted(W.BACKEND.glob("*.py"))
    hits = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "effective_at" in line or "exit_ts" in line:
                hits.append({"file": str(p.relative_to(W.ROOT)), "line": i, "text": line.strip()[:150]})
    idx = []
    for line in (W.BACKEND / "live_store.py").read_text(encoding="utf-8").splitlines():
        if "ix_paper_stop_hist_pos_eff" in line:
            idx.append(line.strip())
    return {
        "hits_total": len(hits),
        "unique_index_on_effective_at": idx,
        "funding_window_rule": [
            {"file": W.PE, "line": 1761,
             "text": "`exit_ts` = **经济出场时刻**…资金费计费窗 = `(entry_ts, exit_ts]`，"
                     "因此 `exit_ts` 必须与 `entry_ts` **同一时间轴**（bar 时刻），否则会漏收/多收结算事件。"},
            {"file": W.PE, "line": 2633, "text": "bar_close_time=ref_close_ms, exit_ts=eff_ms,"},
        ],
        "consumers": [
            {"what": "paper_stop_history.effective_at", "role": "唯一索引 (position_id, effective_at) 的去重键 + 历史排序键"},
            {"what": "paper_position.exit_ts", "role": "资金费计费窗右端点 (entry_ts, exit_ts]；页面/接口展示"},
            {"what": "close_at_price(exit_ts=)", "role": "经济出场时刻"},
            {"what": "返回值 effective_at", "role": "调用方/接口透出"},
        ],
        "not_affected": ["止损候选价 low_4h×(1−d)", "只上移判定 cand_stop > cur_stop",
                         "退出触发条件 low_4h <= cur_stop", "滑点/手续费/取整"],
        "hits_sample": hits[:12],
    }


def main() -> int:
    problems: list = []
    st = static_side()
    run = runtime_probe()
    imp = impact_scope()

    # 1. 两侧公式不同
    res = [f for f in st["formulas"] if f["file"] == W.CODE]
    pro = [f for f in st["formulas"] if f["file"] == W.PE]
    W.require(bool(res) and bool(pro), "未抽到两侧公式", problems)
    W.require(any(f["has_grid"] for f in res), "研究侧公式未见 // M15 网格", problems)
    W.require(any(f["has_wallclock_max"] for f in pro), "生产侧公式未见 max(dec_ms, ...)", problems)
    W.require(not any(f["has_grid"] for f in pro), "生产侧也出现网格对齐，与预期不符", problems)
    W.require(not any(f["has_wallclock_max"] for f in res), "研究侧也出现挂钟 max，与预期不符", problems)

    # 2. 实际实参：生产=挂钟（now/now_ms/clk 均为循环时钟），研究=c4（bar 收盘）
    CLOCK_ARGS = ("now", "now_ms", "clk", "clock", "L.now_ms()")
    dt_args = [c["decision_at"] for c in st["call_sites"]]
    W.require(all(a in CLOCK_ARGS for a in dt_args if a),
              f"生产侧 decision_at 实参不全是挂钟：{dt_args}", problems)
    W.require(not any(("close" in (a or "")) or ("c4" in (a or "")) for a in dt_args),
              f"生产侧 decision_at 出现 bar 时刻实参：{dt_args}", problems)
    W.require(len(st["call_sites"]) >= 2, f"生产侧调用点少于 2 个：{st['call_sites']}", problems)
    # 研究侧：eff 的来源是 4h 收盘 c4（不是挂钟）
    W.require(any(f["file"] == W.CODE and "c4" in f["expr"] for f in st["formulas"]),
              "研究侧公式未以 c4 为输入", problems)

    # 3. 运行探针
    eq = [r for r in run["stop_cases"] if r["equals_research_eff"]]
    neq = [r for r in run["stop_cases"] if not r["equals_research_eff"]]
    # 生产侧 effective_at 就是挂钟落点本身（max(now, c4) 在 now>=c4 时等于 now）
    W.require(all(r["effective_at"] == r["decision_at"] for r in run["stop_cases"]),
              "存在 effective_at != decision_at 的情形，与 max(dec_ms, ref_close_ms) 预期不符", problems)
    # 研究侧一定是「c4 之后的下一个 15m 整点」
    W.require(run["research_eff"] > run["candle"]["close_time"], "研究侧生效时刻不晚于 4h 收盘", problems)
    W.require(run["research_eff"] % run["grid_15m"] == 0, "研究侧生效时刻不在 15m 网格上", problems)
    # 相等只发生在挂钟恰落在网格上时
    W.require(bool(eq) and bool(neq), "探针未同时出现「与研究侧相等」和「不等」两种情形", problems)
    W.require(all(r["on_15m_grid"] for r in eq), "相等的情形竟然不在 15m 网格上", problems)
    W.require(any(not r["on_15m_grid"] for r in neq),
              "所有不等的情形都落在 15m 网格上，未能证明生产侧会写出网格外时刻", problems)
    W.require(run["exit_case"]["exit_triggered"] is True, "退出探针未触发", problems)
    W.require(run["exit_case"]["position_row"]["exit_ts"] == run["exit_case"]["decision_at"],
              "exit_ts 未等于挂钟落点，与「exit_ts=eff_ms」不符", problems)

    # 4. 隔离
    W.require(str(run["ledger_dir"]).startswith("evidence/ACCUM-EARLY-06-W1/03_R1/runs/"),
              f"探针账本不在本窗 runs/ 下：{run['ledger_dir']}", problems)
    W.require(os.environ.get("PAPER_LEDGER_DIR") == str(LEDGER), "PAPER_LEDGER_DIR 未被指向本窗", problems)

    payload = {
        "schema": "accum-early-06-w1-r1/effective-time/v1",
        "checked_at_utc": W.now_iso(),
        "inputs": {"code": {"file": W.CODE, "sha256": W.sha256_file(RESEARCH)},
                   "paper_engine": {"file": W.PE, "sha256": W.sha256_file(PROD)}},
        "static": st,
        "runtime": run,
        "impact": imp,
        "unverified": [
            "未跑完整研究回放（drive_position_exit 需真实 15m 行情与持仓存储），研究侧只对源码表达式求值；"
            "因此「逐笔差异」未实测。",
            "生产循环发现 4h 收盘的延迟上界未实测（示例取 3 分钟；实际取决于循环周期与负载）。",
            "两进程并发推进同一持仓时的重复记录风险未测（顺序调用下第二次为 no-op，已实测）。",
            "资金费在 (entry_ts, exit_ts] 边界上的逐笔差额未实测（只定位到机制：exit_ts 进入该窗口）。",
        ],
        "problems": problems,
        "verdict": "pass" if not problems else "fail",
        "statement": ("研究侧生效时刻 = 4h 收盘后的下一个 15m 整点；生产侧 = max(挂钟, 4h 收盘)。"
                      "两者只在挂钟恰好落在 15m 整点时相等。差异影响记录时刻、止损历史去重键与资金费计费窗右端点；"
                      "不影响止损价、只上移判定与退出触发条件。本单不据此新增或修改任何规则。"),
    }
    W.dump("03_effective_time.json", payload)

    print(json.dumps({
        "research_eff_utc": run["research_eff_utc"],
        "cases": [{"case": r["case"], "effective_at_utc": r["effective_at_utc"],
                   "on_grid": r["on_15m_grid"], "eq_research": r["equals_research_eff"]}
                  for r in run["stop_cases"]],
        "exit_ts_utc": W.iso(run["exit_case"]["position_row"]["exit_ts"]),
        "delta_vs_research_ms": run["exit_case"]["delta_vs_research_ms"],
        "problems": len(problems),
        "verdict": payload["verdict"],
    }, ensure_ascii=False, indent=1))
    for p in problems:
        print("PROBLEM", p)
    return 0 if payload["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
