# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜入口可用性核查 + 真实接入样例导出。

不做的事
────────
· 不改公共前端 / 公共 API / 公共索引；
· 不把「HTTP 200」或「candles 为空」当成通过。

核查项
──────
1. 公共运行列表能否列出本批次 run（决定「有没有可点开的入口」）；
2. 按 run_id 直查 chart 接口返回什么（candles 是否为空、其它载荷是否有数据）；
3. 前端是否存在渲染单 run 组合回测的页面（只读检索，不评价其正确性）。
"""
from __future__ import annotations

import importlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import List

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
API = "http://127.0.0.1:8787/api/v1"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 必须绕代理，否则 127.0.0.1 会被发给代理


def _native(v):
    """把 numpy / pandas 标量转成原生类型，保证 JSON 可序列化且不丢精度。"""
    import numpy as np
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    try:
        if pd.isna(v):
            return None
    except Exception:                                                 # noqa: BLE001
        pass
    return v


def get(path: str, timeout: int = 30):
    try:
        with _OP.open(f"{API}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as e:                                            # noqa: BLE001
        return None, {"error": str(e)[:200]}


def main() -> None:
    idx = cm.load_index()
    rid0 = idx["run_id"].iloc[0]

    # ── 1. 列表接口 ──
    st_list, d_list = get("/backtests")
    items = (d_list.get("items") or []) if isinstance(d_list, dict) else []
    listed_ids = {str(x.get("run_id") or x.get("id")) for x in items if isinstance(x, dict)}
    ours_listed = [r for r in idx["run_id"] if r in listed_ids]

    # ── 2. 单 run 直查 ──
    st_run, d_run = get(f"/backtests/{rid0}")
    st_chart, d_chart = get(f"/backtests/{rid0}/chart")
    ch = (d_chart.get("chart") or {}) if isinstance(d_chart, dict) else {}
    st_tr, d_tr = get(f"/backtests/{rid0}/trades")
    st_eq, d_eq = get(f"/backtests/{rid0}/equity")

    # ── 3. 前端是否有可渲染该 run 的入口（只读检索） ──
    web = cm.BASE / "UI界面/web/src"
    fe_hits: List[str] = []
    if web.exists():
        for p in web.rglob("*.tsx"):
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:                                        # noqa: BLE001
                continue
            if "backtests/" in t and "chart" in t:
                fe_hits.append(str(p.relative_to(cm.BASE)))
    fe_hits = sorted(set(fe_hits))[:10]

    res = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "后端": API,
        "1_运行列表": {
            "status": st_list,
            "返回条数": len(items),
            "本批次run是否出现": bool(ours_listed),
            "出现条数": len(ours_listed),
            "结论": ("列表里**没有**本批次 run ⇒ 前端「运行列表 → 点开某个 run」这条路走不通"
                     if not ours_listed else "列表已包含本批次 run"),
            "已知原因": ("`backtest_store.list_runs()` 只扫默认根 `runs/`，不扫同级批次目录 "
                       "`runs_work_dynamic_bt_01/`（BT-UI-01 范畴，属公共代码，本单不改）"),
        },
        "2_按run_id直查": {
            "run_id": rid0,
            "run_detail_status": st_run,
            "trades_status": st_tr, "trades_items": len((d_tr or {}).get("items") or []),
            "equity_status": st_eq, "equity_items": len((d_eq or {}).get("items") or []),
            "chart_status": st_chart,
            "chart_candles": len(ch.get("candles") or []),
            "chart_signals": len(ch.get("signals") or []),
            "chart_trades": len(ch.get("trades") or []),
            "chart_equity_curve": len(ch.get("equity_curve") or []),
            "chart_blocked_reason": (ch.get("chart_blocked_reason") or "")[:300],
        },
        "3_前端入口": {
            "检索目录": str(web),
            "命中文件": fe_hits,
            "说明": ("前端有调用 `backtests/{id}/chart` 的组件，但该组件按**单标的 symbol+window** 取 K 线；"
                     "组合回测无单标的可传 ⇒ 即使强行打开也是空图。本单不构造组合币 K 线来「填满」它。"),
        },
        "判定": None,
        "判定依据": ("入口可用 = ① 列表能列出 run ② 点开后 1h 主图有真实 K 线 ③ 4h 背景与执行周期可切换。"
                   "本轮 ① 不满足、③ 不满足（candles=[]），只有 ② 的组合级载荷（权益/逐笔/信号）可用 "
                   "⇒ **部分完成，整体不得标完成**。"),
    }
    res["判定"] = "BLOCKED_PARTIAL"
    (OUT / "15_入口核查.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))

    # ── 真实接入样例：挑 3 笔（无切档 / 一次切档 / 两次切档）──
    ix = pd.read_csv(OUT / "10_逐笔复盘索引.csv")
    picks = []
    for cond, tag in [
        (ix["n_stage_events"] == 0, "no_stage_change"),
        (ix["n_stage_events"] == 1, "one_stage_change"),
        (ix["n_stage_events"] >= 2, "multi_stage_change"),
    ]:
        sub = ix[cond]
        if len(sub):
            picks.append((tag, sub.sort_values("entry_ts_ms").iloc[0]))
    samples = []
    for tag, row in picks:
        rec = None
        p = Path(row["replay_file"])
        import gzip
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno == int(row["replay_line"]):
                    rec = json.loads(line)
                    break
        samples.append({
            "case": tag,
            "run_id": row["run_id"],
            "symbol": row["symbol"],
            "trade_id": int(row["trade_id"]),
            "replay_ref": row["replay_entry"],
            "index_row": {k: _native(v) for k, v in row.items()},
            "replay_record_excerpt": {
                "signal": rec["signal"], "entry": rec["entry"], "exit": rec["exit"],
                "pnl": rec["pnl"],
                "stop_trace_head": rec["stop_trace"][:3],
                "stop_trace_tail": rec["stop_trace"][-2:],
                "stage_events": rec["stage_events"],
                "timeframes": {k: {"n": len(v), "first": v[0], "last": v[-1]}
                               for k, v in rec["timeframes"].items() if v},
            },
        })
    (OUT / "14_接入样例.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    with open(OUT / "14b_接入样例_逐笔.jsonl", "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"\n样例已导出 {len(samples)} 笔：{ [s['case'] for s in samples] }")


if __name__ == "__main__":
    main()
