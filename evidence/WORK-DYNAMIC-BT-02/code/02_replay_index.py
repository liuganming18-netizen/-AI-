# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜一、逐币 / 逐笔复盘索引（可直接接入）。

产出
────
`out/10_逐笔复盘索引.csv`   每笔一行：run_id → symbol → trade_id → order_seq → K 线位置/时间范围
`out/11_逐币索引.csv`       每个标的：涉及哪些 run、多少笔、K 线文件、时间范围
`out/12_数据位置清单.csv`   每个 (标的, 周期) 的行情文件：路径 / 大小 / 行数 / 首末时间
`out/13_索引完整性核对.json` 索引自检（行数、覆盖、缺失计数）

硬约束
──────
· **不构造组合币 K 线**：K 线一律按标的（symbol）指向真实行情文件与复盘包内的真实 bar，
  不存在 `PORTFOLIO_74PAIRS` 这类合成序列，也不拿空数组充当数据。
· 每个 K 线位置都带**行号**（复盘包 jsonl 的第 N 行）与**首末时间**，可被脚本机械复核。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
BT01 = cm.BT01


def main() -> None:
    idx = cm.load_index()
    odf = pd.read_csv(OUT / "02_逐笔订单核对.csv.gz")
    entry_o = odf[odf["ft_is_entry"]].set_index(["strategy", "trade_id"])
    exit_o = odf[~odf["ft_is_entry"]].set_index(["strategy", "trade_id"])

    rows: List[dict] = []
    replay_sha: Dict[str, str] = {}
    for _, m in idx.iterrows():
        name = m["strategy"]
        p = cm.REPLAY / f"{name}.jsonl.gz"
        replay_sha[name] = cm.sha256_file(p) if p.exists() else ""

    for _, m in idx.iterrows():
        name = m["strategy"]
        block = cm.raw_block(name)
        trades = cm.derive_trade_ids(block["trades"])
        by_id = {t["_trade_id"]: t for t in trades}
        for rec in cm.iter_replay(name):
            tid = int(rec["trade_id"])
            t = by_id[tid]
            sig = rec.get("signal") or {}
            e, x = rec["entry"], rec["exit"]
            st = rec.get("stop_trace") or []
            se = rec.get("stage_events") or []
            tfs = rec.get("timeframes") or {}

            def tf_span(k: str):
                bars = tfs.get(k) or []
                if not bars:
                    return None, None, 0
                return int(bars[0]["t"]), int(bars[-1]["t"]), len(bars)

            b4a, b4b, n4 = tf_span("4h")
            b1a, b1b, n1 = tf_span("1h")
            b15a, b15b, n15 = tf_span("15m")

            eo = entry_o.loc[(name, tid)] if (name, tid) in entry_o.index else None
            xo = exit_o.loc[(name, tid)] if (name, tid) in exit_o.index else None
            eng_init = float(t["initial_stop_loss_abs"])
            # 引擎该字段 = 开仓价 ×(1 + stoploss 类属性 -0.99) = 开仓价×0.01（按价格步进取整）
            # ⇒ 与自定义止损无关，属**配置产物**，不能当初始止损用。
            ratio = eng_init / float(t["open_rate"]) if float(t["open_rate"]) else float("nan")
            is_artifact = bool(0.0099 <= ratio <= 0.02)

            stage_path = "->".join(f"{s['from_stage']}→{s['to_stage']}@{cm.utc_iso(s['t'])}" for s in se)
            missing = []
            if not st:
                missing.append("stop_trace_empty")
            if n4 == 0:
                missing.append("4h_empty")
            if n1 == 0:
                missing.append("1h_empty")
            if n15 == 0:
                missing.append("15m_empty")
            if eo is None:
                missing.append("entry_order_missing")
            if xo is None:
                missing.append("exit_order_missing")

            rows.append({
                "run_id": m["run_id"], "strategy": name, "group": m["group"],
                "param_group": m["param_group"], "symbol": rec["symbol"], "pair": rec["pair"],
                "trade_id": tid,
                "trade_id_status": "derived",
                "trade_id_basis": "(open_timestamp, 白名单索引) 升序 ⇒ 1..n（引擎导出无 trade_id 字段）",
                "candidate_id": rec.get("candidate_id"),
                # ── 时间轴（全部 UTC）──
                "signal_ts_ms": sig.get("t"), "signal_utc": cm.utc_iso(sig.get("t")),
                "confirm_ts_ms": sig.get("confirm_t"), "confirm_utc": cm.utc_iso(sig.get("confirm_t")),
                "entry_ts_ms": e["t"], "entry_utc": cm.utc_iso(e["t"]),
                "exit_ts_ms": x["t"], "exit_utc": cm.utc_iso(x["t"]),
                "holding_hours": round((x["t"] - e["t"]) / 3_600_000.0, 4),
                # ── 委托/成交（订单层，2 条/笔）──
                "entry_order_seq": int(eo["order_seq"]) if eo is not None else None,
                "entry_order_price": float(eo["fill_price"]) if eo is not None else None,
                "entry_order_qty": float(eo["fill_amount"]) if eo is not None else None,
                "entry_order_cost": float(eo["fill_cost"]) if eo is not None else None,
                "entry_order_fee": float(eo["fee_amount_derived"]) if eo is not None else None,
                "entry_order_tag": eo["ft_order_tag"] if eo is not None else None,
                "exit_order_seq": int(xo["order_seq"]) if xo is not None else None,
                "exit_order_price": float(xo["fill_price"]) if xo is not None else None,
                "exit_order_qty": float(xo["fill_amount"]) if xo is not None else None,
                "exit_order_cost": float(xo["fill_cost"]) if xo is not None else None,
                "exit_order_fee": float(xo["fee_amount_derived"]) if xo is not None else None,
                "exit_order_tag": xo["ft_order_tag"] if xo is not None else None,
                "order_id": "MISSING", "order_id_status": "MISSING·导出裁剪(minified=True)",
                "fill_id": "MISSING", "fill_id_status": "MISSING·回测环境无交易所成交回报",
                # 可用的**替代连接键**（明确标注为派生，不是交易所 ID）
                "entry_order_key": f"{m['run_id']}|{rec['symbol']}|{tid}|0",
                "exit_order_key": f"{m['run_id']}|{rec['symbol']}|{tid}|1",
                "order_key_status": "derived·(run_id|symbol|trade_id|order_seq) 四元组，稳定且唯一，非交易所订单号",
                # ── 止损与切档 ──
                "initial_stop_effective": float(st[0]["stop"]) if st else None,
                "initial_stop_basis": ("成交价 × 0.95（按价格步进向下取整）；"
                                       "来源 stage_trace 首行，不是引擎 initial_stop_loss_abs"),
                "engine_initial_stop_loss_abs": eng_init,
                "engine_initial_stop_ratio_vs_entry": round(float(ratio), 8),
                "engine_initial_stop_is_artifact": bool(is_artifact),
                "effective_stop_final": float(st[-1]["stop"]) if st else None,
                "effective_stop_max": float(max(s["stop"] for s in st)) if st else None,
                "stop_updates": len(st),
                "stage_final": int(rec.get("stage_final") or (se[-1]["to_stage"] if se else 1)),
                "n_stage_events": len(se),
                "stage_path": (stage_path or "none"),
                # ── 结果 ──
                "exit_reason": x.get("reason"),
                "profit_abs": float(rec["pnl"]["profit_abs"]),
                "profit_ratio": float(rec["pnl"]["profit_ratio"]),
                "fee_total": float(rec["pnl"]["fees"]),
                "funding_fees": float(rec["pnl"]["funding"]),
                "is_open_at_end": bool(x.get("is_open_at_end")),
                # ── K 线位置（真实标的，非合成）──
                "replay_file": rec["_replay_file"], "replay_line": rec["_replay_line"],
                "replay_sha256": replay_sha.get(name, ""),
                "bars_4h": n4, "bars_1h": n1, "bars_15m": n15,
                "first_4h_utc": cm.utc_iso(b4a), "last_4h_utc": cm.utc_iso(b4b),
                "first_1h_utc": cm.utc_iso(b1a), "last_1h_utc": cm.utc_iso(b1b),
                "first_15m_utc": cm.utc_iso(b15a), "last_15m_utc": cm.utc_iso(b15b),
                "market_4h_file": str(cm.market_file(rec["pair"], "4h")),
                "market_1h_file": str(cm.market_file(rec["pair"], "1h")),
                "market_15m_file": str(cm.market_file(rec["pair"], "15m")),
                "replay_entry": f"{Path(rec['_replay_file']).name}#L{rec['_replay_line']}",
                "data_complete": len(missing) == 0,
                "missing_notes": "|".join(missing),
            })

    df = pd.DataFrame(rows)
    # 逐币内序号（同一标的按入场时刻排序 1..n），方便「逐币逐笔」翻阅
    df = df.sort_values(["symbol", "entry_ts_ms", "strategy"]).reset_index(drop=True)
    df["symbol_trade_seq"] = df.groupby("symbol").cumcount() + 1
    df = df.sort_values(["run_id", "trade_id"]).reset_index(drop=True)
    df.to_csv(OUT / "10_逐笔复盘索引.csv", index=False, encoding="utf-8-sig")

    # ── run 级索引（run → 标的集合 → 逐笔范围）──
    run_rows = []
    for rid, g in df.groupby("run_id"):
        run_rows.append({
            "run_id": rid, "strategy": g["strategy"].iloc[0], "group": g["group"].iloc[0],
            "param_group": g["param_group"].iloc[0],
            "n_trades": int(len(g)), "n_symbols": int(g["symbol"].nunique()),
            "symbols": "/".join(sorted(g["symbol"].unique())),
            "first_entry_utc": g["entry_utc"].min(), "last_exit_utc": g["exit_utc"].max(),
            "profit_abs_sum": round(float(g["profit_abs"].sum()), 8),
            "trades_with_stage_events": int((g["n_stage_events"] > 0).sum()),
            "replay_file": g["replay_file"].iloc[0], "replay_sha256": g["replay_sha256"].iloc[0],
            "index_query": f"10_逐笔复盘索引.csv 中 run_id == {rid}",
        })
    pd.DataFrame(run_rows).sort_values(["group", "param_group"]).to_csv(
        OUT / "09_run级索引.csv", index=False, encoding="utf-8-sig")

    # ── 逐币索引 ──
    per_sym = []
    for sym, g in df.groupby("symbol"):
        per_sym.append({
            "symbol": sym, "pair": g["pair"].iloc[0],
            "n_trades": int(len(g)), "n_runs": int(g["run_id"].nunique()),
            "groups": "/".join(sorted(g["group"].unique())),
            "param_groups": "/".join(sorted(g["param_group"].unique())),
            "first_entry_utc": g["entry_utc"].min(), "last_exit_utc": g["exit_utc"].max(),
            "profit_abs_sum": round(float(g["profit_abs"].sum()), 6),
            "n_with_stage_events": int((g["n_stage_events"] > 0).sum()),
            "bars_4h_min": int(g["bars_4h"].min()), "bars_1h_min": int(g["bars_1h"].min()),
            "bars_15m_min": int(g["bars_15m"].min()),
            "market_4h_file": g["market_4h_file"].iloc[0],
            "market_1h_file": g["market_1h_file"].iloc[0],
            "market_15m_file": g["market_15m_file"].iloc[0],
            "replay_files": int(g["replay_file"].nunique()),
            "entry_example": g.sort_values("entry_ts_ms")["replay_entry"].iloc[0],
        })
    sym_df = pd.DataFrame(per_sym).sort_values("symbol")
    sym_df.to_csv(OUT / "11_逐币索引.csv", index=False, encoding="utf-8-sig")

    # ── 数据位置清单（行情文件真实首末时间）──
    loc_rows = []
    for pair in sorted(df["pair"].unique()):
        for tf in cm.TFS:
            f = cm.market_file(pair, tf)
            rec = {"pair": pair, "symbol": pair.split("/")[0], "timeframe": tf,
                   "file": str(f), "exists": f.exists()}
            if f.exists():
                st = f.stat()
                d = pd.read_feather(f, columns=["date"])
                ts = d["date"].astype("int64").to_numpy()
                rec.update({"bytes": st.st_size, "mtime_utc": cm.utc_iso(int(st.st_mtime * 1000)),
                            "rows": int(len(ts)),
                            "first_utc": cm.utc_iso(int(ts.min())), "last_utc": cm.utc_iso(int(ts.max()))})
            loc_rows.append(rec)
    loc = pd.DataFrame(loc_rows)
    loc.to_csv(OUT / "12_数据位置清单.csv", index=False, encoding="utf-8-sig")

    # ── 自检 ──
    chk = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "索引行数": int(len(df)),
        "应为": int(idx["trades"].sum()),
        "行数一致": bool(len(df) == int(idx["trades"].sum())),
        "涉及标的数": int(df["symbol"].nunique()),
        "涉及run数": int(df["run_id"].nunique()),
        "data_complete_true": int(df["data_complete"].sum()),
        "data_complete_false": int((~df["data_complete"]).sum()),
        "缺失明细": df.loc[~df["data_complete"], "missing_notes"].value_counts().to_dict(),
        "K线周期数_每笔": {k: int(df[f"bars_{k}"].min()) for k in cm.TFS},
        "engine_initial_stop_为artifacts的笔数": int(df["engine_initial_stop_is_artifact"].sum()),
        "engine_initial_stop_为artifacts的占比": round(float(df["engine_initial_stop_is_artifact"].mean()), 6),
        "engine_initial_stop_比值范围": [round(float(df["engine_initial_stop_ratio_vs_entry"].min()), 6),
                                    round(float(df["engine_initial_stop_ratio_vs_entry"].max()), 6)],
        "engine_initial_stop_说明": ("引擎 `initial_stop_loss_abs` = 开仓价 × 0.01（源自类属性 stoploss=-0.99，"
                                 "按价格步进取整）⇒ 与自定义止损无关，**禁止**当初始止损使用；"
                                 "初始止损请取 `initial_stop_effective`（= 成交价×0.95 向下取整）。"),
        "有切档事件的笔数": int((df["n_stage_events"] > 0).sum()),
        "止损更新次数_合计": int(df["stop_updates"].sum()),
        "行情文件_存在": int(loc["exists"].sum()),
        "行情文件_应有": int(len(loc)),
        "行情文件_缺失": int((~loc["exists"]).sum()),
        "行情文件_缺失清单": loc.loc[~loc["exists"], ["pair", "timeframe"]].to_dict("records")[:20],
        "说明": ("K 线位置 = 复盘包 jsonl 行号（可 byte-offset 定位）+ 真实标的行情文件；"
                 "**没有任何组合币合成 K 线**。candles 为空的公共接口不算完成，见 15_入口核查。"),
    }
    (OUT / "13_索引完整性核对.json").write_text(json.dumps(chk, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(chk, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
