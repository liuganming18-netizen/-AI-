# -*- coding: utf-8 -*-
"""第2、3步：从逐笔成交与权益序列独立重算绩效指标。

原则：
  1. 不采信结果包自报的汇总值，一律由逐笔重算；
  2. USDT 金额与收益比例分列，不混用；
  3. 分母为零一律写「不适用」，不写 0，不写无穷；
  4. 回撤分两种口径：平仓权益（不含未实现）与账户权益（含未实现，来自 wallet 序列）；
  5. 独立固定仓位若无独立运行依据，写「未计算」，不从共享账户结果推算。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (OUT, TARGET, find_result_zips, initial_capital, load_bundle,
                    read_json, rel, sha256_file, ts, write_json)


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def per_trade(t: dict) -> dict:
    """逐笔拆成 毛利 / 手续费 / 资金费 / 净额，四个金额互相独立可验。"""
    long = not t.get("is_short", False)
    amount = _f(t.get("amount"))
    op, cp = _f(t.get("open_rate")), _f(t.get("close_rate"))
    gross = (cp - op) * amount * (1 if long else -1)
    fee_open = _f(t.get("fee_open")) * _f(t.get("stake_amount"))
    fee_close = _f(t.get("fee_close")) * amount * cp
    funding = _f(t.get("funding_fees"))
    return {
        "pair": t.get("pair"), "amount": amount,
        "open_rate": op, "close_rate": cp,
        "gross": gross, "fee_open": fee_open, "fee_close": fee_close,
        "fee_total": fee_open + fee_close,
        "funding_raw": funding,
        "profit_abs": _f(t.get("profit_abs")),
        "profit_ratio": _f(t.get("profit_ratio")),
        "exit_reason": t.get("exit_reason", ""),
        "open_date": t.get("open_date"), "close_date": t.get("close_date"),
        "duration_min": _f(t.get("trade_duration")),
        "min_rate": _f(t.get("min_rate")), "max_rate": _f(t.get("max_rate")),
        "stop_loss_ratio": _f(t.get("stop_loss_ratio")),
    }


def resolve_funding_sign(rows: list[dict]) -> tuple[int, float]:
    """资金费字段的正负号以对账残差最小者为准，不预设。返回 (符号, 残差合计)。"""
    if not rows:
        return 1, 0.0
    best, best_res = 1, None
    for sign in (1, -1):
        res = sum(abs(r["profit_abs"] - (r["gross"] - r["fee_total"] + sign * r["funding_raw"]))
                  for r in rows)
        if best_res is None or res < best_res:
            best, best_res = sign, res
    return best, (best_res or 0.0)


def drawdown(series: list[float]) -> dict:
    """最大回撤：峰值到谷值的绝对额与相对比例。序列为空返回不适用。"""
    if len(series) < 2:
        return {"绝对额": None, "比例": None}
    peak, dd_abs, dd_pct = series[0], 0.0, 0.0
    for v in series:
        peak = max(peak, v)
        dd_abs = max(dd_abs, peak - v)
        if peak > 0:
            dd_pct = max(dd_pct, (peak - v) / peak)
    return {"绝对额": dd_abs, "比例": dd_pct}


def main() -> int:
    zips = find_result_zips()
    manifest = read_json(OUT / "01_清单.json", {}) or {}
    if not zips:
        write_json(OUT / "02_独立指标表.json", {
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "结论": "受限",
            "原因": "FT-BASELINE-03 未产出回测结果包，无可重算对象",
            "A_共享账户": None,
            "B_独立固定仓位": {"状态": "未计算",
                              "原因": "无独立固定仓位运行依据；按指令不从共享账户结果推算"},
        })
        print(json.dumps({"status": "BLOCKED", "reason": "no result zip"}, ensure_ascii=False))
        return 2

    zp = zips[-1]
    b = load_bundle(zp)
    cfg = read_json(TARGET / "user_data" / "config.json", {}) or {}
    init = initial_capital(cfg, b.metrics)
    rows = [per_trade(t) for t in b.trades]
    n = len(rows)

    sign, residual = resolve_funding_sign(rows)
    for r in rows:
        r["funding"] = sign * r["funding_raw"]
        r["net_recalc"] = r["gross"] - r["fee_total"] + r["funding"]
        r["residual"] = r["profit_abs"] - r["net_recalc"]

    net = sum(r["profit_abs"] for r in rows)
    wins = [r for r in rows if r["profit_abs"] > 0]
    losses = [r for r in rows if r["profit_abs"] < 0]
    flats = [r for r in rows if r["profit_abs"] == 0]
    win_sum = sum(r["profit_abs"] for r in wins)
    loss_sum = sum(r["profit_abs"] for r in losses)
    avg_win = win_sum / len(wins) if wins else None
    avg_loss = loss_sum / len(losses) if losses else None
    pf = win_sum / abs(loss_sum) if loss_sum else None
    payoff = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None
    win_rate = len(wins) / n if n else None
    exp_trade = net / n if n else None

    # 平仓权益序列（不含未实现盈亏）
    eq_closed, acc = [], init
    for r in sorted(rows, key=lambda x: str(x["close_date"])):
        acc += r["profit_abs"]
        eq_closed.append(acc)
    dd_closed = drawdown([init] + eq_closed)

    # 账户权益序列（含未实现盈亏）
    wallet = [w for w in b.wallet if w.get("total_quote") is not None]
    eq_account = [float(w["total_quote"]) for w in wallet] if wallet else []
    dd_account = drawdown(eq_account)
    # wallet 序列是否真的含未实现盈亏：total_quote 恒等于 balance 时，它只是平仓余额
    unrealized_included = bool(wallet) and any(
        abs(float(w.get("total_quote", 0)) - float(w.get("balance", 0))) > 1e-9 for w in wallet)

    # 交付物没有逐 bar 权益时，用逐笔最低价重建持仓期最差未实现权益（近似，非逐 bar）
    mtm_seq, eq, mtm_ok = [init], init, True
    for r in sorted(rows, key=lambda x: str(x["open_date"])):
        if not r["min_rate"] or not r["amount"]:
            mtm_ok = False
            break
        worst = eq + (r["min_rate"] - r["open_rate"]) * r["amount"]
        mtm_seq += [worst, eq + r["profit_abs"]]
        eq += r["profit_abs"]
    dd_mtm = drawdown(mtm_seq) if mtm_ok and len(mtm_seq) > 2 else {"绝对额": None, "比例": None}

    metrics_A = {
        "口径说明": f"共享账户组合（初始 {init:.2f} USDT，max_open_trades="
                    f"{cfg.get('max_open_trades')}，仓位={cfg.get('stake_amount')}）",
        "数据来源": rel(zp),
        "结果包sha256": sha256_file(zp),
        "结果包修改时间": ts(zp),
        "初始资金_USDT": init,
        "交易笔数": n,
        "盈利笔数": len(wins),
        "亏损笔数": len(losses),
        "持平笔数": len(flats),
        "胜率": win_rate,
        "净收益_USDT": net,
        "净收益率": (net / init) if init else None,
        "平均盈利_USDT": avg_win,
        "平均盈利_占本金比例": (avg_win / init) if (avg_win is not None and init) else None,
        "平均亏损_USDT": avg_loss,
        "平均亏损_占本金比例": (avg_loss / init) if (avg_loss is not None and init) else None,
        "盈亏比_均值口径": payoff,
        "利润因子": pf,
        "每笔期望_USDT": exp_trade,
        "每笔期望_占本金比例": (exp_trade / init) if (exp_trade is not None and init) else None,
        "手续费合计_USDT": sum(r["fee_total"] for r in rows),
        "资金费合计_USDT": sum(r["funding"] for r in rows),
        "资金费符号约定": f"{'+' if sign > 0 else '-'}funding_fees（由残差最小反解）",
        "对账残差合计_USDT": residual,
        "最大回撤_平仓权益_绝对额": dd_closed["绝对额"],
        "最大回撤_平仓权益_比例": dd_closed["比例"],
        "最大回撤_账户权益_绝对额": dd_account["绝对额"] if unrealized_included else None,
        "最大回撤_账户权益_比例": dd_account["比例"] if unrealized_included else None,
        "wallet序列是否含未实现盈亏": unrealized_included,
        "最大回撤_近似账户权益_绝对额": dd_mtm["绝对额"],
        "最大回撤_近似账户权益_比例": dd_mtm["比例"],
        "近似账户权益口径": "逐笔最低价重建持仓期最差未实现权益，非逐 bar 序列，仅作下界参考",
        "账户权益序列点数": len(eq_account),
        "止损位分布": [{"币种": r["pair"], "止损位": r["stop_loss_ratio"],
                        "实际收益": r["profit_ratio"], "退出原因": r["exit_reason"]}
                       for r in rows],
    }
    metrics_B = {
        "口径说明": "独立固定仓位（每笔等额本金、不复利）",
        "状态": "未计算",
        "原因": "本次只有一次共享账户运行，无独立固定仓位的运行依据；"
                "按指令不从共享账户结果换算或复制。",
    }

    write_json(OUT / "02_独立指标表.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "前置检查结论": manifest.get("结论", "未通过"),
        "A_共享账户": metrics_A,
        "B_独立固定仓位": metrics_B,
    })

    cols = ["pair", "open_date", "close_date", "amount", "open_rate", "close_rate",
            "gross", "fee_open", "fee_close", "fee_total", "funding",
            "net_recalc", "profit_abs", "residual", "profit_ratio",
            "duration_min", "exit_reason"]
    with open(OUT / "02_逐笔重算.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: str(x["open_date"])):
            w.writerow(r)

    print(json.dumps({"status": "OK", "trades": n,
                      "net_usdt": round(net, 4),
                      "win_rate": None if win_rate is None else round(win_rate, 4),
                      "profit_factor": None if pf is None else round(pf, 4),
                      "residual": round(residual, 8)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
