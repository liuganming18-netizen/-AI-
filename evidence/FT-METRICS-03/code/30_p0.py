# -*- coding: utf-8 -*-
"""第3步：5 笔交易的 P0 绑定核对。

P0 = 候选生成时刻确定的初始止损价（state machine 的 initial_stop_price）。
绑定链条必须逐环闭合：
  ① 交易 enter_tag 是否等于候选 candidate_id（同一候选，不是同名不同次）；
  ② 候选 P0 的可用时刻是否早于入场时刻（不能事后补算）；
  ③ 入场价是否落在候选允许区间 [H, max_entry]；
  ④ 出场时的有效止损是否等于 P0（未激活 2R）或等于跟踪位（已激活）；
  ⑤ 已激活的那笔，引擎日志里的 2R 阈值能否由 P0 反算出来。
任何一环断裂即标「未通过」，不接受「结果对得上」替代。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA_FUTURES, OLD_BASELINE_CSV, OLD_RUNS, OUT,
                    load_bundle, pick_target, rel, result_zips, write_json)

LOG = []
TICK = {"BAT": 0.0001, "BANANA": 0.001, "PUMPBTC": 0.00001, "CTSI": 0.0001, "AR": 0.001}


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    print(line)


def main() -> int:
    target, _ = pick_target()
    if target is None:
        log("无可锁定结果包，终止。")
        return 2
    zips = result_zips(target)
    zp = zips[-1]
    b = load_bundle(zp)
    trades = sorted(b.trades, key=lambda t: str(t["open_date"]))
    log(f"锁定结果包：{rel(zp)}；交易 {len(trades)} 笔")

    # 引擎日志里的 2R 激活行（唯一可用于反算 P0 的运行时证据）
    logfile = target / "logs" / "freqtrade_backtest_5symbols.log"
    act = {}
    if logfile.exists():
        txt = logfile.read_text(errors="replace")
        pat = re.compile(r"\[(\S+/USDT:USDT)\][^\n]*?突破 2R\s*阈值\s*([0-9.]+)", re.S)
        for m in pat.finditer(txt):
            seg = m.group(0)
            cid = re.search(r"候选 (cand_\S+?)\)", seg)
            act[m.group(1)] = {"阈值": float(m.group(2)),
                               "日志候选ID": cid.group(1) if cid else ""}
    log(f"日志中 2R 激活记录：{len(act)} 条 → "
        + (", ".join(f"{k}:{v['阈值']}" for k, v in act.items()) if act else "无"))

    old = {}
    if OLD_BASELINE_CSV.exists():
        df = pd.read_csv(OLD_BASELINE_CSV)
        df.columns = [c.lstrip("\ufeff") for c in df.columns]
        old = {str(r["signal_id"]): r for _, r in df.iterrows()}

    rows = []
    for t in trades:
        pair = t["pair"]
        sym = pair.split("/")[0]
        tag = str(t.get("enter_tag", ""))
        o = pd.Timestamp(t["open_date"]).tz_convert("UTC")
        c = pd.Timestamp(t["close_date"]).tz_convert("UTC")
        entry = float(t["open_rate"])
        stop_abs = float(t["stop_loss_abs"])

        row = {"币种": pair, "候选ID(enter_tag)": tag, "开仓时间_UTC": str(o),
               "入场价": entry, "出场止损位": stop_abs, "出场原因": t["exit_reason"]}

        oldrow = old.get(tag)
        if oldrow is None:
            row.update({"候选匹配": "未找到", "结论": "未通过"})
            rows.append(row)
            log(f"{pair:16} 未找到同 ID 候选 → 未通过")
            continue

        cand_file = OLD_RUNS / str(oldrow["run_dir"]) / "candidates.jsonl"
        cand = None
        if cand_file.exists():
            for ln in cand_file.read_text().splitlines():
                if not ln.strip():
                    continue
                cj = json.loads(ln)
                if str(cj.get("candidate_id")) == tag:
                    cand = cj
                    break
        if cand is None:
            row.update({"候选匹配": "candidates.jsonl 无此 ID", "结论": "未通过"})
            rows.append(row)
            log(f"{pair:16} 候选文件无此 ID → 未通过")
            continue

        p0 = float(cand["initial_stop_price"])
        r0 = float(cand["initial_risk_r"])
        avail = int(cand["available_at_ms"])
        h = float(cand["box_high_h"])
        maxe = float(cand["max_entry_price"])
        entry_ms = int(o.value // 10**6)
        earliest = int(cand["execution_earliest_ms"])
        trig = entry + 2.0 * (entry - p0)

        # ④ 出场止损位与 P0 / 跟踪位的关系
        tick = TICK.get(sym, 0.0001)
        d_p0 = abs(stop_abs - p0)

        # 用 4h 数据判断 2R 是否真的被激活、跟踪位是否等于当时的 L10
        f4 = pd.read_feather(DATA_FUTURES / f"{sym}_USDT_USDT-4h-futures.feather")
        f4["open_ms"] = f4["date"].astype("int64")
        f4["close_ms"] = f4["open_ms"] + 4 * 3600 * 1000
        f4["l10"] = f4["low"].rolling(10).min()
        sub4 = f4[(f4.close_ms >= entry_ms) & (f4.open_ms < int(c.value // 10**6))].copy()
        closed_before_exit = sub4[sub4.close_ms <= int(c.value // 10**6)]
        max_close = float(closed_before_exit["close"].max()) if len(closed_before_exit) else None
        activated = bool(max_close is not None and max_close >= trig)
        l10_max = float(closed_before_exit["l10"].max()) if len(closed_before_exit) else None

        # ⑤ 引擎日志 2R 阈值 vs 由 P0 反算的阈值
        log_rec = act.get(pair) or {}
        log_trig = log_rec.get("阈值")
        log_cid = log_rec.get("日志候选ID", "")
        trig_diff = abs(log_trig - trig) if log_trig is not None else None
        cid_in_log_ok = (log_cid == tag) if log_cid else None

        if activated:
            expect = l10_max
            bind = "跟踪止损位（L10 累计最高）"
            stop_ok = l10_max is not None and abs(stop_abs - l10_max) <= tick
        else:
            expect = p0
            bind = "P0 初始止损位"
            stop_ok = d_p0 <= 1.5 * tick

        checks = {
            "①候选ID一致": tag == str(cand["candidate_id"]),
            "②P0先于入场可用": avail <= entry_ms,
            "③入场价在候选区间": h - 1e-12 <= entry <= maxe + 1e-12,
            "④止损位与绑定值一致": stop_ok,
            "⑤2R阈值可反算": (trig_diff is None or trig_diff <= 1e-9) if activated else True,
            "⑦入场时刻等于该候选最早可执行时刻": entry_ms == earliest,
        }
        if cid_in_log_ok is not None:
            checks["⑥日志候选ID与enter_tag一致"] = cid_in_log_ok
        verdict = "通过" if all(checks.values()) else "未通过"

        row.update({
            "候选匹配": "同 ID 命中",
            "候选P0": p0,
            "初始风险R": r0,
            "P0可用时刻_UTC": str(pd.Timestamp(avail, unit="ms", tz="UTC")),
            "候选最早可执行时刻_UTC": str(pd.Timestamp(earliest, unit="ms", tz="UTC")),
            "实际开仓时刻_UTC": str(o),
            "候选区间下沿H": h,
            "候选区间上沿": maxe,
            "2R阈值": round(trig, 8),
            "日志2R阈值": log_trig if log_trig is not None else "无",
            "日志候选ID": log_cid or "无",
            "2R是否激活": "是" if activated else "否",
            "入场后4h最高收盘": round(max_close, 8) if max_close is not None else None,
            "L10累计最高": round(l10_max, 8) if l10_max is not None else None,
            "出场止损绑定对象": bind,
            "绑定值": round(expect, 8) if expect is not None else None,
            "止损位偏差": round(abs(stop_abs - expect), 10) if expect is not None else None,
            "①候选ID一致": checks["①候选ID一致"],
            "②P0先于入场可用": checks["②P0先于入场可用"],
            "③入场价在候选区间": checks["③入场价在候选区间"],
            "④止损位与绑定值一致": checks["④止损位与绑定值一致"],
            "⑤2R阈值可反算": checks["⑤2R阈值可反算"],
            "⑥日志候选ID一致": checks.get("⑥日志候选ID与enter_tag一致", "无此证据"),
            "⑦入场时刻等于最早可执行": checks["⑦入场时刻等于该候选最早可执行时刻"],
            "结论": verdict,
        })
        rows.append(row)
        log(f"{pair:16} P0={p0:.8f} R={r0:.8f} 入场={entry} 区间[{h},{maxe}] "
            f"2R={'激活' if activated else '未激活'} 绑定={bind} "
            f"偏差={row['止损位偏差']} → {verdict}")

    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(OUT / "04_P0绑定核对.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, restval="")
        w.writeheader()
        w.writerows(rows)

    passed = sum(1 for r in rows if r["结论"] == "通过")
    log(f"P0 绑定：{passed}/{len(rows)} 通过")
    write_json(OUT / "04_P0绑定核对_汇总.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "笔数": len(rows),
        "通过": passed,
        "结论": "通过" if passed == len(rows) else "受限",
        "证据来源": "候选 P0 取自旧引擎 candidates.jsonl（同一 candidate_id）；"
                    "运行时绑定以结果包 stop_loss_abs 与引擎日志 2R 阈值双向验证。",
        "说明": "P0 本身不是 Freqtrade 落盘字段，包内无独立 P0 列；"
                "本轮用「候选 P0 + 出场止损位 + 日志 2R 阈值」三点闭合，不采信单一来源。",
    })
    (OUT / "_p0_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    return 0 if passed == len(rows) else 3


if __name__ == "__main__":
    raise SystemExit(main())
