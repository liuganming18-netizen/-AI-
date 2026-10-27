# -*- coding: utf-8 -*-
"""11｜K 线「能重开」之外还要「图对得上交易」——窗口覆盖机械核对。

为什么单独做这一段（自查出来的判据缺口）：
  07 段只验了「`GET /chart` 返回了 candles」，并把 `window_matches_run_window`
  如实记成 False（因为回退分支的窗口取自 `inputs.json(data_summary.time_range_1h)`，
  不是 `run.json.window`）。**但「窗口不相等」不等于「图是错的」**——只要数据窗口
  包住本 run 窗口、且每笔交易的 entry/exit 都落在图内，图就仍然对得上交易。
  反过来，如果交易落在图外，那「K 线可重开」就是空话。

判据（三态，缺数据记 None，不当 False 也不当 True）：
  C1  run.json.window 有效
  C2  inputs.json.data_summary.time_range_1h 有效（= 回退分支实际取窗口的来源）
  C3  data_window ⊇ run_window            （窗口只许更宽，不许更窄）
  C4  每笔 trade 的 entry_ts/exit_ts ∈ data_window
  C5  07 段实拉的 3 个 run × 2 周期，实际返回的首末 candle 时刻 ⊇ 该 run 全部交易

不连网络、不起服务，纯读本单 out/ 下的落盘文件。
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

RUNS = cm.OUT / "iso_runs"


def _utc(ms):
    if not isinstance(ms, int) or ms <= 0:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonl(p: Path):
    out = []
    if not p.exists():
        return out
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _trade_times(t: dict):
    """交易的两个权威时刻。

    ⚠ 项目既有坑（本单 06 段踩过）：4h 出场路径的成交记录里 `bar_open_time` 是
    4h bar 起点，可能早于入场 ⇒ 不能用成交的 bar 时间当交易时刻，必须用
    `trades.jsonl` 的 entry/exit（由 `paper_position.entry_ts/exit_ts` 投影而来）。
    """
    e = t.get("entry_ts") or t.get("entry_time_ms") or t.get("entry_time")
    x = t.get("exit_ts") or t.get("exit_time_ms") or t.get("exit_time")
    return (int(e) if isinstance(e, (int, float)) else None,
            int(x) if isinstance(x, (int, float)) else None)


def main():
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.is_dir() else []
    per_run = []
    n_c3_ok = n_c3_fail = n_c3_na = 0
    n_c4_ok = n_c4_fail = n_c4_na = 0
    trades_total = 0
    outside = []

    for rd in runs:
        rid = rd.name
        rec = {"run_id": rid}
        # C1
        try:
            rj = json.loads((rd / "run.json").read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            rj = {}
        win = rj.get("window") or {}
        ws, we = win.get("win_start_ms"), win.get("win_end_ms")
        c1 = isinstance(ws, int) and isinstance(we, int) and we > ws > 0
        rec["c1_run_window"] = c1
        rec["run_window"] = [_utc(ws), _utc(we)] if c1 else None

        # C2
        try:
            inp = json.loads((rd / "inputs.json").read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            inp = {}
        tr = ((inp.get("data_summary") or {}).get("time_range_1h") or {})
        ds, de = tr.get("start_ms"), tr.get("end_ms")
        c2 = isinstance(ds, int) and isinstance(de, int) and de > ds > 0
        rec["c2_data_window"] = c2
        rec["data_window"] = [_utc(ds), _utc(de)] if c2 else None
        rec["window_src"] = "inputs.json(data_summary.time_range_1h)"

        # C3：数据窗口必须包住 run 窗口
        if c1 and c2:
            c3 = (ds <= ws) and (de >= we)
            rec["c3_data_covers_run"] = c3
            if c3:
                n_c3_ok += 1
            else:
                n_c3_fail += 1
                rec["c3_delta_ms"] = {"start": ws - ds, "end": we - de}
        else:
            rec["c3_data_covers_run"] = None
            n_c3_na += 1

        # C4：每笔交易的 entry/exit 都落在数据窗口内
        trs = _jsonl(rd / "trades.jsonl")
        rec["n_trades"] = len(trs)
        if not c2:
            rec["c4_trades_inside"] = None
            n_c4_na += 1
        elif not trs:
            rec["c4_trades_inside"] = None   # 无交易 ⇒ 不适用，不算通过
            n_c4_na += 1
        else:
            bad = []
            for t in trs:
                e, x = _trade_times(t)
                trades_total += 1
                ok = (e is not None and x is not None and ds <= e <= de and ds <= x <= de)
                if not ok:
                    bad.append({"trade_id": t.get("trade_id"),
                                "entry": _utc(e), "exit": _utc(x)})
                    outside.append({"run_id": rid, "trade_id": t.get("trade_id"),
                                    "entry": _utc(e), "exit": _utc(x)})
            rec["c4_trades_inside"] = not bad
            rec["c4_bad"] = bad
            if bad:
                n_c4_fail += 1
            else:
                n_c4_ok += 1
        per_run.append(rec)

    # C5：07 段实拉到的 candles 首末时刻 vs 该 run 的交易
    exp = json.loads((cm.OUT / "07_导出核验.json").read_text(encoding="utf-8"))
    c5 = []
    for ch in exp.get("chart_checks", []):
        rid = ch["run_id"]
        rd = RUNS / rid
        trs = _jsonl(rd / "trades.jsonl") if rd.is_dir() else []
        ts = [t for t in (_trade_times(t) for t in trs) if t[0] and t[1]]
        first, last = ch.get("first_t"), ch.get("last_t")
        item = {"run_id": rid, "timeframe": ch["timeframe"],
                "candles": ch.get("candles"),
                "first_candle": _utc(first), "last_candle": _utc(last),
                "n_trades": len(ts)}
        if not ts:
            item["ok"] = None
            item["note"] = "该 run 无交易 ⇒ 不适用（不是通过）"
        elif not isinstance(first, int) or not isinstance(last, int):
            item["ok"] = False
            item["note"] = "载荷缺 first_t/last_t"
        else:
            bad = [{"entry": _utc(e), "exit": _utc(x)}
                   for e, x in ts if not (first <= e <= last and first <= x <= last)]
            item["ok"] = not bad
            item["trades_outside_chart"] = bad
        c5.append(item)

    c5_vals = [i["ok"] for i in c5]
    c5_all = (False if any(v is False for v in c5_vals)
              else (True if any(v is True for v in c5_vals) else None))

    out = {
        "generated_at_utc": cm.utc_now(),
        "runs_root": str(RUNS),
        "n_runs": len(per_run),
        "c3": {"ok": n_c3_ok, "fail": n_c3_fail, "not_applicable": n_c3_na,
               "verdict": "数据窗口包住 run 窗口（只许更宽）"},
        "c4": {"ok": n_c4_ok, "fail": n_c4_fail, "not_applicable": n_c4_na,
               "n_trades_checked": trades_total,
               "verdict": "每笔交易 entry/exit 落在数据窗口内"},
        "trades_outside_data_window": outside,
        "c5_endpoint_sample": c5,
        "c5_all_ok": c5_all,
        "caveat": ("`window_matches_run_window=False`（07 段记录的）**不等于图错**："
                   "回退分支窗口取自 `inputs.json.data_summary.time_range_1h`，"
                   "与 `run.json.window` 不相等是设计如此（数据窗口含预热）。"
                   "本段证明的是「数据窗口 ⊇ run 窗口 且 交易落在图内」，"
                   "**不是**「窗口逐位相等」。"),
        "per_run": per_run,
    }
    p = cm.OUT / "11_K线窗口覆盖.json"
    cm.write_json(p, out)
    print(f"runs={len(per_run)}  C3 ok/fail/na = {n_c3_ok}/{n_c3_fail}/{n_c3_na}"
          f"  C4 ok/fail/na = {n_c4_ok}/{n_c4_fail}/{n_c4_na} (trades={trades_total})"
          f"  C5 all_ok={c5_all}")
    if outside:
        print("  交易落在数据窗口外：", outside[:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
