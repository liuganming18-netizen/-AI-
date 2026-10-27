#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FT-REPLAY-01 核对脚本（独立复算，不信自报汇总）。

七项检查，每项都从**落盘文件**重新算，不读任何中间汇总：
  A 逐笔一致性   系统 4325 笔净盈亏合计 vs 上游 profit_abs 合计（差额必须 0）
  B 时间链单调   信号 ≤ 确认 ≤ 下单/成交 < 出场（按模式分别统计违反数）
  C 标记可关联   chart.trades[].trade_id 必须能在 trades.jsonl 里找到（孤儿必须 0）
  D 确认只在 C1/C2  C0 的 confirm_time_ms 必须为 null（无此环节）
  E K 线覆盖     抽样调后端 /chart，检查 K 线范围是否覆盖全部标记点
  F 缺失显式     P0 / 计划风险 / 数量的 null 计数（不填 0、不填默认值）
  G 账本隔离     run.json 必须 research_period=true，且目录内无 ledger.sqlite3
  H 批次入口     批次汇总与逐笔行数/净盈亏自洽，且清单逐行带研究期标记（需后端）

用法：
    $PY evidence/FT-REPLAY-01/code_r1/01_核对.py            # 只算 A~D、F、G
    $PY evidence/FT-REPLAY-01/code_r1/01_核对.py --http     # 额外跑 E、H（需后端 8787）
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"
UPSTREAM = PROJECT_ROOT / "evidence" / "FT-ENTRY-09" / "out" / "00_all_27_trades_consolidated.csv"
PREFIX = "run_FT_ENTRY_09_"
BATCH_ID = "runs_ft_replay_01"
API = "http://127.0.0.1:8787/api/v1/backtests"


def _get_json(url: str, timeout: int = 180) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _rows(p: Path) -> List[Dict[str, Any]]:
    if not p.is_file():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _run_dirs() -> List[Path]:
    if not RUNS.is_dir():
        return []
    return sorted(d for d in RUNS.iterdir() if d.is_dir() and d.name.startswith(PREFIX))


def check_a(dirs: List[Path]) -> Dict[str, Any]:
    up = 0.0
    n_up = 0
    with UPSTREAM.open("r", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            up += float(r["profit_abs"])
            n_up += 1
    sys_net = 0.0
    n_sys = 0
    for d in dirs:
        for t in _rows(d / "trades.jsonl"):
            sys_net += t.get("net_pnl") or 0
            n_sys += 1
    diff = sys_net - up
    return {
        "check": "A 逐笔一致性",
        "verdict": "PASS" if (n_sys == n_up and abs(diff) < 1e-6) else "FAIL",
        "detail": f"上游 {n_up} 笔 {up:.6f}｜系统 {n_sys} 笔 {sys_net:.6f}｜差额 {diff:.9f}",
        "numbers": {"upstream_trades": n_up, "system_trades": n_sys,
                    "upstream_net": round(up, 6), "system_net": round(sys_net, 6),
                    "diff": round(diff, 9)},
    }


def check_b(dirs: List[Path]) -> Dict[str, Any]:
    bad: Counter = Counter()
    total: Counter = Counter()
    zero_dur = 0
    samples: List[str] = []
    for d in dirs:
        for t in _rows(d / "trades.jsonl"):
            mode = t.get("mode") or "?"
            total[mode] += 1
            sig = t.get("signal_time_ms")
            cfm = t.get("confirm_time_ms")
            order = t.get("order_time_ms")
            entry = t.get("entry_time_ms")
            exit_ = t.get("exit_time_ms")
            problems = []
            if sig is None:
                problems.append("无信号时间")
            if mode in ("C1", "C2") and cfm is None:
                problems.append("C1/C2 无确认时间")
            if cfm is not None and sig is not None and not (sig <= cfm):
                problems.append("信号晚于确认")
            ref = order if order is not None else entry
            if ref is not None and cfm is not None and not (cfm <= ref):
                problems.append("确认晚于下单")
            # 上游本身有 65 笔 open_timestamp == close_timestamp（trade_duration=0、
            # exit_reason=stop_loss，即开仓同一毫秒被止损）⇒ 判据用 `<=`，
            # 但把「同毫秒进出」单独计数如实报出来，不当作正常也不当作错误。
            if entry is not None and exit_ is not None:
                if entry == exit_:
                    zero_dur += 1
                elif entry > exit_:
                    problems.append("入场晚于出场")
            if problems:
                bad[mode] += 1
                if len(samples) < 6:
                    samples.append(f"{t.get('trade_id')}: {'/'.join(problems)}")
    verdict = "PASS" if not bad else "FAIL"
    return {
        "check": "B 时间链单调",
        "verdict": verdict,
        "detail": ("；".join(f"{m}: {bad[m]}/{total[m]} 违反" for m in sorted(total)) or "无数据")
                  + f"｜另：{zero_dur} 笔为上游「同一毫秒进出」（trade_duration=0），非映射错误",
        "numbers": {"violations": dict(bad), "by_mode": dict(total),
                    "upstream_zero_duration": zero_dur},
        "samples": samples,
    }


def check_c(dirs: List[Path]) -> Dict[str, Any]:
    orphan = 0
    no_id = 0
    total = 0
    samples: List[str] = []
    for d in dirs:
        ids = {t.get("trade_id") for t in _rows(d / "trades.jsonl")}
        try:
            chart = json.loads((d / "chart.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for tr in chart.get("trades") or []:
            total += 1
            tid = tr.get("trade_id")
            if not tid:
                no_id += 1
                if len(samples) < 6:
                    samples.append(f"{d.name}: chart.trades 有条目无 trade_id")
            elif tid not in ids:
                orphan += 1
                if len(samples) < 6:
                    samples.append(f"{d.name}: {tid} 不在 trades.jsonl")
    verdict = "PASS" if (orphan == 0 and no_id == 0) else "FAIL"
    return {
        "check": "C 标记可关联真实交易",
        "verdict": verdict,
        "detail": f"chart.trades 共 {total} 条｜无 trade_id {no_id}｜孤儿 {orphan}",
        "numbers": {"chart_trades": total, "no_trade_id": no_id, "orphan": orphan},
        "samples": samples,
    }


def check_d(dirs: List[Path]) -> Dict[str, Any]:
    c0_total = c0_with_cfm = 0
    c12_total = c12_without_cfm = 0
    for d in dirs:
        try:
            chart = json.loads((d / "chart.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mode = ((run.get("spec") or {}).get("timing_mode") or "").upper()
        for t in _rows(d / "trades.jsonl"):
            if mode == "C":
                c0_total += 1
                if t.get("confirm_time_ms") is not None:
                    c0_with_cfm += 1
            else:
                c12_total += 1
                if t.get("confirm_time_ms") is None:
                    c12_without_cfm += 1
        # C0 不应有 confirms 标记
        if mode == "C" and (chart.get("confirms") or []):
            c0_with_cfm += len(chart["confirms"])
    verdict = "PASS" if (c0_with_cfm == 0 and c12_without_cfm == 0) else "FAIL"
    return {
        "check": "D 确认环节只在 C1/C2",
        "verdict": verdict,
        "detail": f"C0 成交 {c0_total} 笔，带确认时间/标记 {c0_with_cfm}｜"
                  f"C1/C2 成交 {c12_total} 笔，缺确认时间 {c12_without_cfm}",
        "numbers": {"c0_trades": c0_total, "c0_with_confirm": c0_with_cfm,
                    "c12_trades": c12_total, "c12_without_confirm": c12_without_cfm},
    }


def check_e(dirs: List[Path], sample: int = 12) -> Dict[str, Any]:
    """K 线必须覆盖「被聚焦那一笔」的四个点：信号 / 确认 / 入场 / 出场。

    为什么要按笔测：一个 run 装的是该「参数组 × 币种」的全部成交，成交可能分散在
    几年里（标记跨度中位 3.2 万根 15m）⇒ 一次画全必然截断。复盘真正要看的是
    「这一笔的四个点在不在图上」，所以用该笔 entry 时刻做 `focus_ms` 去测。
    """
    picks = dirs[:: max(1, len(dirs) // sample)][:sample]
    bad: List[str] = []
    ok = 0
    for d in picks:
        rows = _rows(d / "trades.jsonl")
        if not rows:
            continue
        t0 = rows[0]
        focus = t0.get("entry_time_ms")
        url = f"{API}/{d.name}/chart"
        if isinstance(focus, int):
            url += f"?focus_ms={focus}"
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                payload = json.loads(r.read().decode())
        except Exception as exc:                            # noqa: BLE001
            bad.append(f"{d.name}: 取图失败 {type(exc).__name__}")
            continue
        c = payload.get("chart") or {}
        cs = c.get("candles") or []
        if not cs:
            bad.append(f"{d.name}: K 线为空（{c.get('candles_note') or '无说明'}）")
            continue
        lo, hi = cs[0]["t"], cs[-1]["t"]
        # 只检查**这一笔**对应的点（该笔的 signal_id 关联的候选）
        sid = t0.get("event_id") or (t0.get("source") or {}).get("event_id")
        pts: List[Tuple[str, int]] = []
        for s in c.get("signals") or []:
            if sid and s.get("signal_id") == sid:
                pts.append(("信号", s.get("t")))
        for x in c.get("confirms") or []:
            if sid and x.get("signal_id") == sid:
                pts.append(("确认", x.get("t")))
        for t in c.get("trades") or []:
            if t.get("trade_id") == t0.get("trade_id"):
                pts.append(("入场", (t.get("entry") or {}).get("t")))
                pts.append(("出场", (t.get("exit") or {}).get("t")))
        out = [f"{k}@{v}" for k, v in pts if isinstance(v, int) and not (lo <= v <= hi)]
        if out:
            bad.append(f"{d.name}: 该笔 {len(out)} 个标记在图外"
                       f"（截断={c.get('candles_truncated')}，聚焦={c.get('candles_focused_ms')}）"
                       f" 例: {out[:2]}")
        elif len(pts) < 3:
            bad.append(f"{d.name}: 只找到 {len(pts)} 个标记点（信号/确认/入场/出场 应有 3~4 个）")
        else:
            ok += 1
    verdict = "PASS" if not bad else "FAIL"
    return {
        "check": "E K 线覆盖被聚焦那笔的全部标记",
        "verdict": verdict,
        "detail": f"抽样 {len(picks)} 个 run（各取第一笔，带 focus_ms）：{ok} 个四点全在图内；"
                  f"{len(bad)} 个有问题",
        "numbers": {"sampled": len(picks), "ok": ok, "bad": len(bad)},
        "samples": bad[:8],
    }


def check_f(dirs: List[Path]) -> Dict[str, Any]:
    nulls: Counter = Counter()
    total = 0
    for d in dirs:
        for t in _rows(d / "trades.jsonl"):
            total += 1
            for k in ("P0", "planned_risk_usdt", "qty", "stake_amount", "signal_time_ms"):
                if t.get(k) is None:
                    nulls[k] += 1
            # 铁律：缺就是 null，不许填 0
            for k in ("P0", "planned_risk_usdt"):
                if t.get(k) == 0:
                    nulls[f"{k}_zero"] += 1
    zero_bad = nulls.get("P0_zero", 0) + nulls.get("planned_risk_usdt_zero", 0)
    return {
        "check": "F 缺失显式（不填 0）",
        "verdict": "PASS" if zero_bad == 0 else "FAIL",
        "detail": f"{total} 笔中：P0 缺 {nulls['P0']}｜计划风险缺 {nulls['planned_risk_usdt']}｜"
                  f"数量缺 {nulls['qty']}｜仓位金额缺 {nulls['stake_amount']}｜"
                  f"信号时间缺 {nulls['signal_time_ms']}｜填 0 的 {zero_bad}",
        "numbers": {"trades": total, "null_counts": dict(nulls), "zero_filled": zero_bad},
    }


def check_g(dirs: List[Path]) -> Dict[str, Any]:
    bad: List[str] = []
    for d in dirs:
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bad.append(f"{d.name}: run.json 不可读")
            continue
        if not run.get("research_period"):
            bad.append(f"{d.name}: 缺 research_period 标记")
        reg = (run.get("registration") or {}).get("registered_by")
        if reg != "ft_replay_import":
            bad.append(f"{d.name}: registered_by={reg}（应为 ft_replay_import）")
        if (d / "ledger.sqlite3").exists():
            bad.append(f"{d.name}: 目录内出现 ledger.sqlite3（不应有账本）")
    return {
        "check": "G 账本隔离",
        "verdict": "PASS" if not bad else "FAIL",
        "detail": f"{len(dirs)} 个 run：全部带 research_period + ft_replay_import 登记，"
                  f"无账本文件；异常 {len(bad)}",
        "numbers": {"runs": len(dirs), "bad": len(bad)},
        "samples": bad[:8],
    }


def check_h() -> Dict[str, Any]:
    """H 批次入口：清单非空 + 汇总与逐笔自洽 + 研究期标记透传（要后端在跑）。"""
    bad: List[str] = []
    nums: Dict[str, Any] = {}
    try:
        summ = _get_json(f"{API}/batches/{BATCH_ID}/summary?refresh=1")
        runs = _get_json(f"{API}/batches/{BATCH_ID}/runs")
    except Exception as exc:                                  # noqa: BLE001
        return {
            "check": "H 批次入口自洽",
            "verdict": "FAIL",
            "detail": f"后端未响应：{exc}",
            "numbers": {},
            "samples": [],
        }
    b = summ.get("batch") or {}
    checks = summ.get("checks") or {}
    items = runs.get("items") or []
    nums.update({
        "runs_total": b.get("runs_total"),
        "runs_rows": len(items),
        "trades_total": b.get("trades_total"),
        "trades_rows": checks.get("trades_rows"),
        "net_pnl_sum": b.get("net_pnl_sum"),
        "net_pnl_from_rows": checks.get("net_pnl_from_rows"),
        "checks_trades": checks.get("trades_total_matches_rows"),
        "checks_net": checks.get("net_pnl_matches_rows"),
    })
    if b.get("manifest_batch"):
        bad.append("批次被判成「清单型批次」⇒ run 清单必然为空")
    if b.get("runs_total") != len(items):
        bad.append(f"汇总 runs_total={b.get('runs_total')} 与清单行数 {len(items)} 不一致")
    if len(items) == 0:
        bad.append("批次 run 清单为空")
    if checks.get("trades_total_matches_rows") is not True:
        bad.append(f"汇总笔数与逐笔行数不自洽：{checks.get('trades_total_matches_rows')}")
    if checks.get("net_pnl_matches_rows") is not True:
        bad.append(f"汇总净盈亏与逐笔合计不自洽：{checks.get('net_pnl_matches_rows')}")
    no_flag = [r.get("run_id") for r in items if not r.get("research_period")]
    if no_flag:
        bad.append(f"{len(no_flag)} 个 run 在批次清单里缺 research_period 标记")
    return {
        "check": "H 批次入口自洽",
        "verdict": "PASS" if not bad else "FAIL",
        "detail": (f"批次 {BATCH_ID}：汇总 {b.get('runs_total')} 个 run / "
                   f"{b.get('trades_total')} 笔 / 净盈亏 {b.get('net_pnl_sum')}；"
                   f"清单 {len(items)} 行且逐行带研究期标记；"
                   f"汇总↔逐笔两项核对 "
                   f"{checks.get('trades_total_matches_rows')}/"
                   f"{checks.get('net_pnl_matches_rows')}"),
        "numbers": nums,
        "samples": bad[:8],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FT-REPLAY-01 核对")
    ap.add_argument("--http", action="store_true", help="额外跑 E（调后端 /chart）")
    ap.add_argument("--json", type=str, default="", help="把结果写到该路径")
    args = ap.parse_args(argv)

    dirs = _run_dirs()
    print(f"研究期 run 目录：{len(dirs)} 个（{RUNS}）")
    if not dirs:
        return 2
    results = [check_a(dirs), check_b(dirs), check_c(dirs), check_d(dirs)]
    if args.http:
        results.append(check_e(dirs))
        results.append(check_h())
    results += [check_f(dirs), check_g(dirs)]

    print("")
    for r in results:
        mark = "PASS" if r["verdict"] == "PASS" else "FAIL"
        print(f"[{mark}] {r['check']}")
        print(f"       {r['detail']}")
        for s in (r.get("samples") or [])[:4]:
            print(f"       · {s}")
    bad = [r for r in results if r["verdict"] != "PASS"]
    print("")
    print(f"合计：{len(results) - len(bad)}/{len(results)} 通过")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"run_dirs": len(dirs), "results": results},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {p}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
