# -*- coding: utf-8 -*-
"""第5步：逐笔核对入退场时间、数量、价格、费用、净额、退出原因；
给出最大盈利/亏损单与单笔贡献集中度；检测止损异常。

止损异常一旦存在且无法解释，结论直接判「未通过」，不进入正式绩效。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, find_result_zips, load_bundle, read_json, rel, write_json

TOL_PRICE = 1e-9
TOL_RESID = 1e-6


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def detect(rows: list[dict], raw: list[dict]) -> list[dict]:
    out = []
    for r, t in zip(rows, raw):
        flags = []
        reason = (r["exit_reason"] or "").lower()
        sl_ratio = _f(t.get("stop_loss_ratio"), 0.0)
        sl_abs = _f(t.get("stop_loss_abs"))
        long = not t.get("is_short", False)
        is_trailing = "trailing" in reason
        # 1. 固定止损单实际亏损超出止损位（滑点/击穿）
        if reason == "stop_loss" and sl_ratio < 0 and r["profit_ratio"] < sl_ratio - 0.01:
            flags.append(f"止损击穿：止损位{sl_ratio:.2%}，实际{r['profit_ratio']:.2%}")
        # 2. 固定止损单却是盈利（真异常）
        if reason == "stop_loss" and r["profit_ratio"] > 0:
            flags.append(f"固定止损单盈利：{r['profit_ratio']:.2%}")
        # 3. 移动止损盈利：只有当止损位仍低于入场价时才算异常（说明止盈未上移）
        if is_trailing and r["profit_ratio"] > 0 and sl_abs:
            ratcheted = (sl_abs > r["open_rate"]) if long else (sl_abs < r["open_rate"])
            if not ratcheted:
                flags.append(f"移动止损盈利但止损位未上移至成本之上：{r['profit_ratio']:.2%}")
        # 4. 强平
        if r["profit_ratio"] <= -0.99:
            flags.append("强平/穿仓")
        # 5. 净额对不上
        if abs(r["residual"]) > TOL_RESID:
            flags.append(f"净额残差 {r['residual']:.6f} USDT")
        # 6. 平仓价越界
        lo, hi = _f(t.get("min_rate")), _f(t.get("max_rate"))
        if lo and hi and not (lo - TOL_PRICE <= r["close_rate"] <= hi + TOL_PRICE):
            flags.append(f"平仓价 {r['close_rate']} 不在区间 [{lo}, {hi}]")
        # 7. 名义本金与开仓金额偏离（杠杆/单位问题）
        notional = r["amount"] * r["open_rate"]
        stake = _f(t.get("stake_amount"))
        if stake and abs(notional / stake - 1) > 0.01:
            flags.append(f"名义/本金偏离 {notional / stake - 1:.2%}（检查杠杆口径）")
        if flags:
            out.append({"pair": r["pair"], "open_date": r["open_date"],
                        "close_date": r["close_date"], "exit_reason": r["exit_reason"],
                        "profit_ratio": r["profit_ratio"], "stop_loss_ratio": sl_ratio,
                        "flags": flags})
    return out


def main() -> int:
    zips = find_result_zips()
    if not zips:
        write_json(OUT / "03_止损异常.json", {
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "受限", "原因": "无回测结果包，逐笔核对未开始", "异常": [],
        })
        print(json.dumps({"status": "BLOCKED", "reason": "no result zip"}, ensure_ascii=False))
        return 2

    b = load_bundle(zips[-1])
    raw = b.trades
    calc = read_json(OUT / "02_独立指标表.json", {}) or {}
    a = (calc.get("A_共享账户") or {})

    # 复用 20 号脚本落盘的逐笔重算结果，保证两张表同源
    rows = []
    p = OUT / "02_逐笔重算.csv"
    if p.exists():
        with open(p, encoding="utf-8-sig") as f:
            for d in csv.DictReader(f):
                rows.append({k: (_f(v) if k in (
                    "amount", "open_rate", "close_rate", "gross", "fee_open", "fee_close",
                    "fee_total", "funding", "net_recalc", "profit_abs", "residual",
                    "profit_ratio", "duration_min") else v) for k, v in d.items()})

    anomalies = detect(rows, raw)

    # 差异表：重算净额 vs 结果包净额
    diff_rows = []
    for r in rows:
        diff_rows.append({
            "币种": r["pair"],
            "入场时间": r["open_date"], "离场时间": r["close_date"],
            "数量": r["amount"], "开仓价": r["open_rate"], "平仓价": r["close_rate"],
            "毛利_USDT": round(r["gross"], 6),
            "开仓手续费_USDT": round(r["fee_open"], 6),
            "平仓手续费_USDT": round(r["fee_close"], 6),
            "资金费_USDT": round(r["funding"], 6),
            "重算净额_USDT": round(r["net_recalc"], 6),
            "结果包净额_USDT": round(r["profit_abs"], 6),
            "差异_USDT": round(r["residual"], 8),
            "收益率": round(r["profit_ratio"], 6),
            "持仓分钟": r["duration_min"],
            "退出原因": r["exit_reason"],
        })
    with open(OUT / "03_逐笔差异表.csv", "w", newline="", encoding="utf-8-sig") as f:
        if diff_rows:
            w = csv.DictWriter(f, fieldnames=list(diff_rows[0].keys()))
            w.writeheader()
            w.writerows(diff_rows)

    # 最大盈利/亏损与集中度
    net = a.get("净收益_USDT") or 0.0
    profits = sorted([r["profit_abs"] for r in rows], reverse=True)
    top1 = profits[0] if profits else None
    top3 = sum(profits[:3]) if profits else None
    conc = {
        "最大单笔盈利_USDT": top1,
        "最大单笔亏损_USDT": min(profits) if profits else None,
        "Top1贡献占净收益": (top1 / net) if (top1 is not None and net) else None,
        "Top3贡献占净收益": (top3 / net) if (top3 is not None and net) else None,
        "集中度说明": "净收益为负或零时不适用" if not net or net <= 0 else "",
    }

    status = "未通过" if anomalies else "通过"
    write_json(OUT / "03_止损异常.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "状态": status,
        "异常条数": len(anomalies),
        "异常": anomalies,
        "集中度": conc,
        "样本警示": ("仅 %d 笔，不能用于证明策略稳定性；"
                     "胜率、盈亏比的置信区间宽度远大于其点估计差异。" % len(rows))
                    if len(rows) < 30 else "",
    })
    print(json.dumps({"status": status, "trades": len(rows),
                      "anomalies": len(anomalies), "concentration": conc},
                     ensure_ascii=False))
    return 0 if not anomalies else 4


if __name__ == "__main__":
    raise SystemExit(main())
