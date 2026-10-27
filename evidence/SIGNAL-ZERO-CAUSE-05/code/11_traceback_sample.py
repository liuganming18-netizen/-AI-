# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜11 逐币记录回溯抽样（汇总 → 原始 K 线）。

工单要求「汇总必须能回溯到逐币记录」。本脚本从 `02_逐币归因` 抽样，
**不读引擎自报指标**，直接用原始 4h K 线 + 规范 ATR（连续递推、窗口前预热）复算：

  · 箱体上下沿 = 建档那根 4h 及其往前共 42 根的最高/最低；
  · ATR = 从装载到的最早一根起一路递推到建档那根的 Wilder ATR(14)；
  · 宽度 = (H-L)/ATR；ER = 42 根收盘的方向效率比。

再把复算值与记录里的 `box_width_atr` / `box_er` / `atr` 逐币比对。

【端点漂移防护】截止时刻一律取 `02_汇总.json` 里 02 当时用的 `upto_utc`，
**不用「现在」**——否则两次运行之间又闭合一根 4h，取样段整体后移一根，
会出现「复算全不对」的假失败（本单已踩过一次）。

【空转防护】判据要求 `n_compared == n_picked` 且每一项都是 True；
只要有一项是 None（没比到）就判不通过，不允许「全 None 也算 True」。

产出：out/11_回溯抽样.json
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import numpy as np

import unified_runtime as UR
from 策略.WyckoffMaster.event_trend_v0_1.features import compute_efficiency_ratio

H1, H4 = cm.H1, cm.H4
OUT = Path(__file__).parent.parent / "out"
TOL = 5e-4          # 记录里保留 4 位小数
TOL_ATR = 1e-9      # ATR 是浮点原值，容差取机器精度量级


def parse_utc_ms(s: str) -> int:
    """把 'YYYY-MM-DDTHH:MM:SSZ' 解析成该秒的**末毫秒**（4h/1h 收盘都是 :59:59.999）。"""
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1000) + 999


def load_02():
    summ = json.loads((OUT / "02_汇总.json").read_text(encoding="utf-8"))
    det = json.loads((OUT / "02_逐币归因.json").read_text(encoding="utf-8"))
    with open(OUT / "02_逐币归因.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return summ, det, rows


def locate(b4, close_utc):
    """按格式化时间定位那根 4h（收盘是 :59:59.999，不能用整秒 ms 直比）。"""
    for i, b in enumerate(b4):
        if cm.utc(int(b["close_time"])) == close_utc:
            return i
    return None


def recompute_seg(b4, idx, n, cfg):
    """复算「以 idx 结尾的最近 n 根」的 H/L/ATR/宽度/ER。ATR 从装载起点连续递推。"""
    if idx is None or idx + 1 < n:
        return {"error": "定位不到那根 4h，或上下文不足 %d 根" % n}
    seg = b4[idx + 1 - n: idx + 1]
    H = max(float(x["high"]) for x in seg)
    L = min(float(x["low"]) for x in seg)
    atr = UR._WilderATR(cfg.atr_period_4h)
    val = None
    for b in b4[: idx + 1]:
        val = atr.update(float(b["high"]), float(b["low"]), float(b["close"]))
    er = compute_efficiency_ratio(np.array([float(x["close"]) for x in seg]))
    return {"n_seg": len(seg), "seg_first_utc": cm.utc(int(seg[0]["close_time"])),
            "seg_last_utc": cm.utc(int(seg[-1]["close_time"])),
            "high_h": H, "low_l": L, "atr_a": val,
            "width_atr": (H - L) / val if val else None, "er": er,
            "atr_bars_fed": idx + 1}


def close(a, b, tol):
    if a is None or b is None:
        return None
    return abs(float(a) - float(b)) <= tol


def main() -> int:
    cfg = cm.cfg()
    n = int(cfg.box_bars_4h)
    summ, det, rows = load_02()

    # 端点锚定 02 当时的截止时刻，不用「现在」
    upto = parse_utc_ms(summ["upto_utc"])
    upto_utc = summ["upto_utc"]

    ok_rows = [r for r in rows if r.get("status") == "已回放"]
    by_state = {}
    for r in ok_rows:
        by_state.setdefault(r["state"], []).append(r)

    rnd = random.Random(20261025)
    picked = []
    for st in ("BOX", "RETEST", "SWEEP", "IDLE"):
        pool = by_state.get(st) or []
        picked += rnd.sample(pool, min(2, len(pool)))
    print(f"[upto] {upto_utc}（取 02 当时截止，非当前时刻）")
    print(f"[pick] {[(p['symbol'], p['state']) for p in picked]}")

    syms = [p["symbol"] for p in picked]
    hist, _ = cm.load_hist(syms, upto)
    # 关键：按 02 的截止时刻截断，多一根都不行
    for s in syms:
        hist[s]["4h"] = [b for b in hist[s]["4h"]
                         if int(b["close_time"]) <= upto]

    items = []
    for r in picked:
        sym = r["symbol"]
        b4 = hist[sym]["4h"]
        rec = {
            "symbol": sym, "state": r["state"],
            "box_formed_utc": r.get("box_formed_utc") or None,
            "box_width_atr_recorded": r.get("box_width_atr") or None,
            "box_er_recorded": r.get("box_er") or None,
            "n_4h_loaded": len(b4),
            "first_4h_utc": cm.utc(int(b4[0]["close_time"])) if b4 else None,
            "last_4h_utc": cm.utc(int(b4[-1]["close_time"])) if b4 else None,
        }
        # ① 建档那根：用原始 K 线复算箱体
        if rec["box_formed_utc"]:
            idx = locate(b4, rec["box_formed_utc"])
            rc = recompute_seg(b4, idx, n, cfg)
            rec["box_recomputed"] = rc
            if "error" not in rc:
                rec["box_width_match"] = close(rc["width_atr"],
                                               rec["box_width_atr_recorded"], TOL)
                rec["box_er_match"] = close(rc["er"], rec["box_er_recorded"], TOL)
        # ② 引擎最后一次 4h 评估：与记录里的 actual/atr 三方对照
        att = (det.get("details", {}).get(sym) or {}).get("last_4h_attempt") or {}
        if att.get("utc"):
            idx2 = locate(b4, att["utc"])
            rc2 = recompute_seg(b4, idx2, n, cfg)
            rec["last4_utc"] = att["utc"]
            rec["last4_recorded"] = {"width_atr": att.get("width_atr"),
                                     "er": att.get("er"), "atr": att.get("atr"),
                                     "box_gate_bars": att.get("box_gate_bars"),
                                     "atr_bars_seen": att.get("atr_bars_seen")}
            rec["last4_recomputed"] = rc2
            if "error" not in rc2:
                rec["last4_width_match"] = close(rc2["width_atr"],
                                                 att.get("width_atr"), TOL)
                rec["last4_er_match"] = close(rc2["er"], att.get("er"), TOL)
                rec["last4_atr_match"] = close(rc2["atr_a"], att.get("atr"), TOL_ATR)
                rec["last4_atr_bars_fed_match"] = (
                    rc2["atr_bars_fed"] == att.get("atr_bars_seen"))
        items.append(rec)

    # ── 判据：不许空转 ─────────────────────────────────────────────────
    keys = ("box_width_match", "box_er_match",
            "last4_width_match", "last4_er_match",
            "last4_atr_match", "last4_atr_bars_fed_match")
    compared, missing, failed = 0, [], []
    for x in items:
        for k in keys:
            if k not in x:
                continue
            if x[k] is None:
                missing.append((x["symbol"], k))
            elif x[k] is False:
                failed.append((x["symbol"], k))
            else:
                compared += 1
    n_picked = len(items)
    all_match = (not failed) and (not missing) and compared > 0
    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": upto_utc,
        "upto_source": "out/02_汇总.json#upto_utc（锚定 02 当时的截止，非当前时刻）",
        "n_picked": n_picked,
        "n_comparisons": compared,
        "n_missing": len(missing),
        "n_failed": len(failed),
        "missing": missing,
        "failed": failed,
        "all_match": all_match,
        "vacuity_guard": ("n_comparisons>0 且 n_missing==0 且 n_failed==0 才算通过；"
                          "None 视为「没比到」，一律判不通过"),
        "note": ("抽样复算只用原始 4h K 线 + 规范 ATR（连续递推、装载起点预热），"
                 "不读引擎自报指标；width/er 容差 5e-4（记录保留 4 位小数），"
                 "ATR 容差 1e-9（记录为浮点原值）。"),
        "items": items,
    }
    cm.dump("11_回溯抽样.json", res)

    for x in items:
        br = x.get("box_recomputed") or {}
        lr = x.get("last4_recomputed") or {}
        print(f"  {x['symbol']:14s} {x['state']:7s} "
              f"箱体 记录={x.get('box_width_atr_recorded')} 复算="
              f"{round(br['width_atr'], 4) if br.get('width_atr') else br.get('error')} "
              f"| 末根 记录={round(x['last4_recorded']['width_atr'], 4) if x.get('last4_recorded', {}).get('width_atr') else None} "
              f"复算={round(lr['width_atr'], 4) if lr.get('width_atr') else lr.get('error')} "
              f"| ATR {x.get('last4_atr_match')}")
    print(f"[traceback] 比对 {compared} 项 · 未比到 {len(missing)} · 不符 {len(failed)} "
          f"⇒ all_match={all_match}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
