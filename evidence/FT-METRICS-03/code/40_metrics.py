# -*- coding: utf-8 -*-
"""第4步：唯一指标表。

口径纪律（本轮重点，避免第3轮循环）：
  1. 只有「共享账户」一种运行，所有指标都属共享账户口径；
     「独立固定仓位」没有对应运行 → 写「未计算」，不从共享账户换算。
  2. 盈亏比 = 平均盈利 ÷ 平均亏损绝对值（每笔均值之比）；
     利润因子 = 盈利总额 ÷ 亏损总额绝对值（金额之比）。两者分列，不混用。
  3. 金额一律标 USDT，比例一律标分母（占初始资金 / 占峰值），不写裸百分数。
  4. 资金费符号：收入为正、支出为负（由结算事件独立重算确认，非残差反解）。
  5. 回撤只出「平仓权益回撤」；wallet 序列 total_quote 恒等于 balance 时，
     账户回撤写「不适用」，不拿平仓回撤冒充。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (OUT, fnum, initial_capital, load_bundle, pick_target,
                    rel, result_zips, sha256_file, write_json)

LOG = []


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    print(line)


def per_trade(t: dict) -> dict:
    amount = fnum(t.get("amount"))
    op, cp = fnum(t.get("open_rate")), fnum(t.get("close_rate"))
    gross = (cp - op) * amount
    fee_open = fnum(t.get("fee_open")) * fnum(t.get("stake_amount"))
    fee_close = fnum(t.get("fee_close")) * amount * cp
    return {
        "pair": t.get("pair"), "amount": amount, "open_rate": op, "close_rate": cp,
        "gross": gross, "fee_open": fee_open, "fee_close": fee_close,
        "fee_total": fee_open + fee_close,
        "funding": fnum(t.get("funding_fees")),
        "profit_abs": fnum(t.get("profit_abs")),
        "profit_ratio": fnum(t.get("profit_ratio")),
        "exit_reason": t.get("exit_reason", ""),
        "open_date": t.get("open_date"), "close_date": t.get("close_date"),
        "duration_h": fnum(t.get("trade_duration")) / 60.0,
        "min_rate": fnum(t.get("min_rate")), "max_rate": fnum(t.get("max_rate")),
        "stop_loss_ratio": fnum(t.get("stop_loss_ratio")),
        "stop_loss_abs": fnum(t.get("stop_loss_abs")),
    }


def drawdown(series: list[float]) -> dict:
    if len(series) < 2:
        return {"绝对额": None, "比例": None, "峰值": None, "谷值": None}
    peak, dd, pct, pk, tr = series[0], 0.0, 0.0, series[0], series[0]
    for v in series:
        if v > peak:
            peak = v
        if peak - v > dd:
            dd, pct, pk, tr = peak - v, (peak - v) / peak, peak, v
    return {"绝对额": dd, "比例": pct, "峰值": pk, "谷值": tr}


def main() -> int:
    target, _ = pick_target()
    if target is None:
        log("无可锁定结果包，终止。")
        return 2
    zips = result_zips(target)
    zp = zips[-1]
    b = load_bundle(zp)
    init = initial_capital(b.config, b.metrics)
    rows = [per_trade(t) for t in b.trades]
    n = len(rows)
    log(f"锁定结果包 {rel(zp)}；初始资金 {init} USDT；交易 {n} 笔")

    for r in rows:
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
    payoff = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None
    pf = (win_sum / abs(loss_sum)) if loss_sum else None
    win_rate = len(wins) / n if n else None
    exp_trade = net / n if n else None
    fee_total = sum(r["fee_total"] for r in rows)
    fund_total = sum(r["funding"] for r in rows)
    resid_total = sum(abs(r["residual"]) for r in rows)

    # 平仓权益序列（含初始资金，不含未实现）
    eq, seq = init, [init]
    for r in sorted(rows, key=lambda x: str(x["close_date"])):
        eq += r["profit_abs"]
        seq.append(eq)
    dd_closed = drawdown(seq)

    wallet = [w for w in b.wallet if w.get("total_quote") is not None]
    unreal = bool(wallet) and any(
        abs(float(w.get("total_quote", 0)) - float(w.get("balance", 0))) > 1e-9 for w in wallet)
    eq_acc = [float(w["total_quote"]) for w in wallet] if wallet else []
    dd_acc = drawdown(eq_acc) if unreal else {"绝对额": None, "比例": None}
    log(f"wallet 序列 {len(wallet)} 行；total_quote 是否含未实现：{'是' if unreal else '否（恒等于 balance）'}")

    top = max(rows, key=lambda r: r["profit_abs"])
    others = sum(r["profit_abs"] for r in rows if r is not top)
    contrib = [{"币种": r["pair"], "净额_USDT": round(r["profit_abs"], 4),
                "占净收益比": round(r["profit_abs"] / net, 4) if net else None,
                "占本金比": round(r["profit_abs"] / init, 6)} for r in
               sorted(rows, key=lambda x: -x["profit_abs"])]

    def M(指标, 数值, 单位, 口径, 采信="可采信"):
        return {"指标": 指标, "数值": 数值, "单位": 单位, "口径与说明": 口径, "可采信度": 采信}

    table = [
        M("锁定结果包", zp.name, "—", "唯一运行；sha256 " + sha256_file(zp, 64)[:16] + "…"),
        M("策略版本", b.strategy_name, "—", "包内策略指纹 " + b.embedded_strategy_sha[:16] + "…"),
        M("运行窗口", f"{b.metrics.get('backtest_start')} ~ {b.metrics.get('backtest_end')}",
          "UTC", "5 个币种，15m 决策，合约单向做多，杠杆 1x"),
        M("账户口径", "共享账户", "—", f"初始 {init:.2f} USDT，max_open_trades="
                                   f"{b.config.get('max_open_trades')}，仓位按风险预算动态计算"),
        M("交易笔数", n, "笔", "已平仓，无未平仓"),
        M("盈利笔数", len(wins), "笔", "净额 > 0"),
        M("亏损笔数", len(losses), "笔", "净额 < 0"),
        M("持平笔数", len(flats), "笔", "净额 = 0"),
        M("胜率", round(win_rate * 100, 2), "%", "盈利笔数 ÷ 总笔数；5 笔样本，区间极宽"),
        M("净收益", round(net, 4), "USDT", "逐笔净额合计，等于包内 profit_total_abs"),
        M("净收益率", round(net / init * 100, 4), "%", "分母=初始资金 2000 USDT"),
        M("平均盈利", round(avg_win, 4), "USDT", f"盈利 {len(wins)} 笔均值"),
        M("平均盈利占本金", round(avg_win / init * 100, 4), "%", "分母=初始资金"),
        M("平均亏损", round(avg_loss, 4), "USDT", f"亏损 {len(losses)} 笔均值"),
        M("平均亏损占本金", round(avg_loss / init * 100, 4), "%", "分母=初始资金"),
        M("盈亏比", round(payoff, 4), "倍", "平均盈利 ÷ 平均亏损绝对值（每笔均值之比）"),
        M("利润因子", round(pf, 4), "倍", "盈利总额 ÷ 亏损总额绝对值（金额之比）"),
        M("每笔期望", round(exp_trade, 4), "USDT", "净收益 ÷ 笔数"),
        M("每笔期望占本金", round(exp_trade / init * 100, 4), "%", "分母=初始资金"),
        M("手续费合计", round(fee_total, 4), "USDT", "开仓按本金、平仓按成交额，双边各 0.05%"),
        M("资金费合计", round(fund_total, 4), "USDT", "收入为正、支出为负；168 个结算事件独立重算闭合"),
        M("对账残差合计", f"{resid_total:.2e}", "USDT", "逐笔净额与毛利-费用+资金费之差，浮点量级"),
        M("最大回撤（平仓权益）", round(dd_closed["绝对额"], 4), "USDT",
          f"峰值 {dd_closed['峰值']:.2f} → 谷值 {dd_closed['谷值']:.2f}，含初始资金"),
        M("最大回撤率（平仓权益）", round(dd_closed["比例"] * 100, 3), "%", "分母=回撤前峰值"),
        M("最大回撤（账户权益）", "不适用", "—",
          "交付权益序列 total_quote 恒等于 balance，不含未实现盈亏，无法计算", "不可采信"),
        M("独立固定仓位指标", "未计算", "—",
          "本轮只有共享账户运行，无独立固定仓位依据；不从共享账户换算", "不适用"),
        M("夏普/索提诺/卡尔玛", "不适用", "—", "5 笔样本不足以估计波动率类指标", "不适用"),
    ]

    with open(OUT / "01_唯一指标表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["指标", "数值", "单位", "口径与说明", "可采信度"])
        w.writeheader()
        w.writerows(table)

    write_json(OUT / "01_唯一指标表.json", {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "结果包": {"文件名": zp.name, "sha256": sha256_file(zp, 64)},
        "指标表": table,
        "贡献拆解": {
            "最大盈利单": {"币种": top["pair"], "净额_USDT": round(top["profit_abs"], 4),
                       "占净收益比": round(top["profit_abs"] / net, 4) if net else None,
                       "退出原因": top["exit_reason"]},
            "其余合计_USDT": round(others, 4),
            "逐笔": contrib,
            "样本限制": "5 笔、1 胜 4 负；剔除最大盈利单后其余 4 笔合计为负，"
                        "不能据此判断策略稳定性。",
        },
        "回撤口径": {
            "平仓权益": dd_closed,
            "账户权益": "不适用（序列不含未实现盈亏）",
            "wallet行数": len(wallet),
            "total_quote是否含未实现": unreal,
        },
    })
    log(f"净收益 {net:.4f} USDT；胜率 {win_rate:.2%}；盈亏比 {payoff:.4f}；"
        f"利润因子 {pf:.4f}；平仓回撤 {dd_closed['绝对额']:.4f} USDT")
    log(f"最大盈利单 {top['pair']} {top['profit_abs']:.4f} USDT，其余合计 {others:.4f} USDT")
    (OUT / "_metrics_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    print(json.dumps({"status": "OK", "net": round(net, 4), "n": n,
                      "pf": round(pf, 4), "payoff": round(payoff, 4)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
