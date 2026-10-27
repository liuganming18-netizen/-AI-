# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜入口复验（批量）：45 个 run × 三周期 + 51 个成交标的。

为什么单独一个脚本
──────────────────
`04_samples_and_entry.py` 只抽查了 1 个 run 的 1 个标的，就被写成「入口不可用」；
后来公共代码补上了 `?symbol=` / `?timeframe=`，也只在 AVAX 上抽验过。
抽查不能当结论 —— 本脚本把**每一个 run、每一个成交标的、每一个周期**都跑一遍，
并且不满足于「有 K 线」：必须逐 bar 与**回测实际消费的 feather 行情**全等。

判定口径（缺一不可，缺任一条即不算可用）
────────────────────────────────────────
1. HTTP 200 且 `chart.candles` 非空；
2. `candles_scope == "market_continuous"` 且 `candles_gaps == 0`（连续，不是各笔窗口并集）；
3. 每根 bar 的时间按该周期整除对齐（mod step == 0）；
4. 逐 bar 与 feather 的 (o,h,l,c,v) 在 1e-9 内全等 —— 即「引擎当时吃的数」与「图上画的数」是同一份；
5. 元数据自洽：`chart_partial=False`、`chart_blocked_reason is None`、
   `display_timeframe == 请求周期`、`exec_timeframe == spec.exec_timeframe`（执行周期语义未被污染）。

不做的事
────────
· 不写回任何 run 目录（只 GET）；
· 不把 HTTP 200、也不把「candles 非空但其实是并集」当成通过。
"""
from __future__ import annotations

import importlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
API = "http://127.0.0.1:8787/api/v1"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 绕代理，否则 127.0.0.1 会发给代理

STEP = {"4h": 14_400_000, "1h": 3_600_000, "15m": 900_000}
TFS = ("1h", "4h", "15m")            # 1h 主图优先，其次 4h 背景、15m 执行
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
TOL = 1e-9

_FEATHER_CACHE: Dict[Tuple[str, str], Optional[dict]] = {}


def get_json(path: str, timeout: int = 300) -> Tuple[Optional[int], dict]:
    last = None
    for attempt in range(2):
        try:
            with OP.open(f"{API}{path}", timeout=timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:                                     # noqa: BLE001
                return e.code, {"error": "non-json error body"}
        except Exception as e:                                    # noqa: BLE001
            last = e
            time.sleep(0.5)
    return None, {"error": f"{type(last).__name__}: {last}"[:300]}


def feather_map(symbol: str, tf: str) -> Optional[dict]:
    """该标的该周期的真实行情：`{t: (o,h,l,c,v)}`，来源是回测实际消费的 feather。"""
    key = (symbol, tf)
    if key in _FEATHER_CACHE:
        return _FEATHER_CACHE[key]
    row = LOC[(LOC["symbol"] == symbol) & (LOC["timeframe"] == tf)]
    if not len(row):
        _FEATHER_CACHE[key] = None
        return None
    p = Path(row["file"].iloc[0])
    if not p.exists():
        _FEATHER_CACHE[key] = None
        return None
    df = pd.read_feather(p)
    # feather 的 date 是 datetime64[ms] ⇒ 不能按 ns 除 1e6（会把毫秒当秒，差 1000 倍）
    t = ((df["date"] - EPOCH) // pd.Timedelta("1ms")).astype("int64")
    m = {int(a): (float(b), float(c), float(d), float(e), float(f))
         for a, b, c, d, e, f in zip(t, df["open"], df["high"], df["low"], df["close"], df["volume"])}
    _FEATHER_CACHE[key] = m
    return m


def spec_exec_tf(run_id: str) -> str:
    """本 run 的策略执行周期（取 run.json 的 spec.exec_timeframe，只读）。

    `GET /backtests/{id}` 返回的是摘要，不含 spec；这里读盘上的 run.json 取真值，
    用来核对 chart 返回的 `exec_timeframe` 有没有被「显示周期」污染。
    """
    key = ("__exec__", run_id)
    if key in _FEATHER_CACHE:
        return _FEATHER_CACHE[key]                             # type: ignore[return-value]
    try:
        d = json.loads((cm.BATCH / run_id / "run.json").read_text(encoding="utf-8"))
        v = str((d.get("spec") or {}).get("exec_timeframe") or "15m")
    except Exception:                                          # noqa: BLE001
        v = "15m"
    _FEATHER_CACHE[key] = v                                    # type: ignore[assignment]
    return v


def check_one(run_id: str, symbol: str, tf: str, exec_tf_expected: str) -> dict:
    t0 = time.time()
    st, d = get_json(f"/backtests/{run_id}/chart?symbol={symbol}&timeframe={tf}")
    el = time.time() - t0
    ch = (d.get("chart") or {}) if isinstance(d, dict) else {}
    bars = ch.get("candles") or []
    rec = {
        "run_id": run_id, "symbol": symbol, "timeframe": tf,
        "http": st, "elapsed_s": round(el, 2),
        "bars": len(bars),
        "candles_scope": ch.get("candles_scope"),
        "candles_gaps": ch.get("candles_gaps"),
        "candles_truncated": ch.get("candles_truncated"),
        "display_timeframe": ch.get("display_timeframe"),
        "exec_timeframe": ch.get("exec_timeframe"),
        "chart_partial": ch.get("chart_partial"),
        "chart_blocked_reason": (ch.get("chart_blocked_reason") or None),
        "candles_error": (ch.get("candles_error") or None),
        "trades_in_chart": len(ch.get("trades") or []),
        "signals_in_chart": len(ch.get("signals") or []),
        "symbol_in_run": ch.get("symbol_in_run"),
        "chart_symbol": ch.get("symbol"),
        "feather_rows": None, "feather_overlap": None, "feather_exact": None,
        "align_ok": None, "gaps_ok": None, "meta_ok": None, "ok": False, "fail": [],
    }
    if st != 200:
        rec["fail"].append(f"http={st}")
        return rec
    if not bars:
        rec["fail"].append("candles=0")
        return rec

    ts = [int(c["t"]) for c in bars]
    rec["align_ok"] = all(t % STEP[tf] == 0 for t in ts)
    rec["gaps_ok"] = (ch.get("candles_scope") == "market_continuous"
                      and (ch.get("candles_gaps") in (0, None))
                      and all(ts[i + 1] - ts[i] == STEP[tf] for i in range(len(ts) - 1)))
    rec["meta_ok"] = (ch.get("chart_partial") is False
                      and not ch.get("chart_blocked_reason")
                      and str(ch.get("display_timeframe")) == tf
                      and str(ch.get("exec_timeframe")) == exec_tf_expected
                      and str(ch.get("symbol", "")).replace("USDT", "").upper() == symbol.upper())

    fm = feather_map(symbol, tf)
    if fm is None:
        rec["fail"].append("feather 缺失（无法对账）")
    else:
        rec["feather_rows"] = len(fm)
        hit = [c for c in bars if int(c["t"]) in fm]
        ex = [c for c in hit
              if all(abs(fm[int(c["t"])][i] - float(c[k])) <= TOL
                     for i, k in enumerate(("o", "h", "l", "c", "v")))]
        rec["feather_overlap"] = len(hit)
        rec["feather_exact"] = len(ex)
        if len(hit) != len(bars):
            rec["fail"].append(f"feather 对不上的 bar {len(bars) - len(hit)} 根")
        if len(ex) != len(bars):
            rec["fail"].append(f"数值不全等 {len(bars) - len(ex)} 根")

    for k, msg in (("align_ok", "时间未按周期对齐"),
                   ("gaps_ok", "非连续行情（有断档或非 market_continuous）"),
                   ("meta_ok", "元数据不自洽（partial/blocked/display/exec/symbol）")):
        if rec[k] is False:
            rec["fail"].append(msg)
    rec["ok"] = not rec["fail"]
    return rec


def main() -> None:
    global LOC
    idx = cm.load_index()
    LOC = pd.read_csv(OUT / "12_数据位置清单.csv", encoding="utf-8-sig")
    ix = pd.read_csv(OUT / "10_逐笔复盘索引.csv")

    # ── 0. 列表接口：本批次 45 个 run 是否都能列出来 ──
    st_list, d_list = get_json("/backtests?limit=500")
    items = (d_list.get("items") or []) if isinstance(d_list, dict) else []
    listed = {str(x.get("run_id") or x.get("id")) for x in items if isinstance(x, dict)}
    ours = [r for r in idx["run_id"] if r in listed]

    # ── 1. 每个 run：取成交额/成交笔数最多的标的 + 首笔标的，三周期 ──
    rows: List[dict] = []
    for rid in idx["run_id"]:
        exec_tf = spec_exec_tf(rid)
        sub = ix[ix["run_id"] == rid]
        if not len(sub):
            continue
        cnt = sub["symbol"].value_counts()
        picks = [cnt.index[0]] + ([cnt.index[-1]] if len(cnt) > 1 and cnt.index[-1] != cnt.index[0] else [])
        for sym in picks:
            for tf in TFS:
                rows.append(check_one(rid, sym, tf, exec_tf))

    # ── 2. 51 个成交标的：每个标的在「它自己成交过的那个 run」上验三周期 ──
    sym_rows: List[dict] = []
    for sym, g in ix.groupby("symbol"):
        rid = g.sort_values("entry_ts_ms").iloc[0]["run_id"]
        exec_tf = spec_exec_tf(rid)
        for tf in TFS:
            r = check_one(rid, sym, tf, exec_tf)
            r["phase"] = "symbol_matrix"
            sym_rows.append(r)

    for r in rows:
        r["phase"] = "run_matrix"

    allrows = rows + sym_rows
    df = pd.DataFrame(allrows)
    df.to_csv(OUT / "17_入口复验_批量.csv", index=False, encoding="utf-8-sig")

    # ── 3. 负例：非法周期必须拒绝、不在 run 内的标的必须显式说明 ──
    rid0 = idx["run_id"].iloc[0]
    st_bad_tf, d_bad_tf = get_json(f"/backtests/{rid0}/chart?symbol=AVAX&timeframe=1d")
    st_bad_sym, d_bad_sym = get_json(f"/backtests/{rid0}/chart?symbol=ZZZZ&timeframe=1h")
    ch_bs = (d_bad_sym.get("chart") or {}) if isinstance(d_bad_sym, dict) else {}

    def agg(phase: str) -> dict:
        s = df[df["phase"] == phase]
        if not len(s):
            return {}
        runs = s["run_id"].nunique()
        ok_runs = s[s["ok"]]["run_id"].nunique()
        return {
            "请求数": int(len(s)),
            "run数": int(runs),
            "全部周期通过的run数": int(ok_runs),
            "通过请求数": int(s["ok"].sum()),
            "失败请求数": int((~s["ok"]).sum()),
            "bars合计": int(s["bars"].sum()),
            "逐bar全等合计": int(s["feather_exact"].fillna(0).sum()),
            "覆盖标的数": int(s["symbol"].nunique()),
            "失败样例": s[~s["ok"]][["run_id", "symbol", "timeframe", "fail"]].head(10).to_dict("records"),
        }

    res = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "后端": API,
        "0_运行列表": {
            "http": st_list, "返回条数": len(items),
            "本批次run数": int(len(idx)), "已列出": len(ours),
            "结论": "全部列出" if len(ours) == len(idx) else f"缺 {len(idx) - len(ours)} 个",
        },
        "1_按run复验": agg("run_matrix"),
        "2_按标的复验": agg("symbol_matrix"),
        "3_负例": {
            "非法周期1d": {"http": st_bad_tf,
                           "missing_reason": str(d_bad_tf.get("missing_reason") or "")[:200],
                           "判定": "拒绝（400）" if st_bad_tf == 400 else "未按预期拒绝"},
            "不在run内的标的": {"http": st_bad_sym, "bars": len(ch_bs.get("candles") or []),
                                "symbol_in_run": ch_bs.get("symbol_in_run"),
                                "blocked_reason": str(ch_bs.get("chart_blocked_reason") or "")[:200],
                                "判定": ("显式说明且不出图" if not (ch_bs.get("candles") or []) else "出了图（需核）")},
        },
    }
    ok_all = (len(ours) == len(idx)
              and res["1_按run复验"].get("失败请求数") == 0
              and res["2_按标的复验"].get("失败请求数") == 0
              and st_bad_tf == 400)
    res["判定"] = "PASS_BACKEND" if ok_all else "FAIL_BACKEND"
    res["判定口径"] = ("后端「按标的 × 三周期」出图可用 = 列表能列出全部 run + 每请求 K 线连续且"
                       "与 feather 逐 bar 全等 + 元数据自洽 + 非法周期被拒。"
                       "注意：本判定**只覆盖后端**，前端「1h 主图 + 多图联动」是否真能打开联动，"
                       "不在本脚本口径内。")
    (OUT / "18_入口复验结论.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
