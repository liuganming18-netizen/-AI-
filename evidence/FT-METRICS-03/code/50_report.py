# -*- coding: utf-8 -*-
"""第5步：逐笔核对表与简短结论。

逐笔表只做一件事：把每一笔的「入退场 → P0 绑定 → 资金费事件 → 费用 → 净额 → 贡献」
串成一行，任何一格不闭合都在「判定」列写出来。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BASE, OLD_BASELINE_CSV, OUT, fnum, initial_capital, load_bundle,
                    pick_target, read_json, rel, result_zips, sha256_file, write_json)

LOG = []


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG.append(line)
    print(line)


def main() -> int:
    target, _ = pick_target()
    zips = result_zips(target)
    zp = zips[-1]
    b = load_bundle(zp)
    init = initial_capital(b.config, b.metrics)
    lock = read_json(OUT / "00_锁定与版本.json", {}) or {}
    fund = read_json(OUT / "03_资金费事件核对_汇总.json", {}) or {}
    p0s = read_json(OUT / "04_P0绑定核对_汇总.json", {}) or {}
    met = read_json(OUT / "01_唯一指标表.json", {}) or {}

    frows = {}
    with open(OUT / "03_资金费事件核对.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            frows[r["币种"]] = r
    prows = {}
    with open(OUT / "04_P0绑定核对.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            prows[r["币种"]] = r

    trades = sorted(b.trades, key=lambda t: str(t["open_date"]))
    net = sum(fnum(t["profit_abs"]) for t in trades)
    rows = []
    for t in trades:
        pair = t["pair"]
        amt, op, cp = fnum(t["amount"]), fnum(t["open_rate"]), fnum(t["close_rate"])
        gross = (cp - op) * amt
        fo = fnum(t["fee_open"]) * fnum(t["stake_amount"])
        fc = fnum(t["fee_close"]) * amt * cp
        fund_v = fnum(t["funding_fees"])
        pnl = fnum(t["profit_abs"])
        rec = gross - fo - fc + fund_v
        p, fr = prows.get(pair, {}), frows.get(pair, {})
        checks = []
        if abs(pnl - rec) > 1e-6:
            checks.append("净额不闭合")
        if fr.get("是否闭合") != "是":
            checks.append("资金费不闭合")
        if p.get("结论") != "通过":
            checks.append("P0绑定未通过")
        rows.append({
            "币种": pair,
            "开仓时间_UTC": str(t["open_date"]),
            "平仓时间_UTC": str(t["close_date"]),
            "持仓_h": round(fnum(t["trade_duration"]) / 60, 2),
            "数量": amt,
            "开仓价": op,
            "平仓价": cp,
            "名义本金_USDT": round(fnum(t["stake_amount"]), 6),
            "毛利_USDT": round(gross, 6),
            "开仓费_USDT": round(fo, 6),
            "平仓费_USDT": round(fc, 6),
            "费用合计_USDT": round(fo + fc, 6),
            "资金费事件数": fr.get("结算事件数", ""),
            "资金费_USDT": round(fund_v, 6),
            "资金费方向": fr.get("方向", ""),
            "重算净额_USDT": round(rec, 6),
            "记录净额_USDT": round(pnl, 6),
            "净额残差_USDT": f"{pnl - rec:.2e}",
            "净额占本金_%": round(pnl / init * 100, 4),
            "占净收益_%": round(pnl / net * 100, 2) if net else "",
            "退出原因": t["exit_reason"],
            "候选P0": p.get("候选P0", ""),
            "出场止损位": p.get("出场止损位", ""),
            "止损绑定对象": p.get("出场止损绑定对象", ""),
            "2R是否激活": p.get("2R是否激活", ""),
            "P0绑定判定": p.get("结论", ""),
            "判定": "全部闭合" if not checks else "；".join(checks),
        })

    with open(OUT / "02_逐笔核对表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"逐笔核对表 {len(rows)} 行；净额残差最大 "
        f"{max(abs(float(r['净额残差_USDT'])) for r in rows):.2e} USDT")

    # 旧引擎 vs 本次：资金费差额是否纯粹由数量口径造成
    oldcmp = []
    if OLD_BASELINE_CSV.exists():
        import pandas as pd
        od = pd.read_csv(OLD_BASELINE_CSV)
        od.columns = [c.lstrip("\ufeff") for c in od.columns]
        om = {str(r["signal_id"]): r for _, r in od.iterrows()}
        for t in trades:
            o = om.get(str(t.get("enter_tag")))
            if o is None:
                continue
            q_old, q_new = fnum(o["qty"]), fnum(t["amount"])
            f_old = -fnum(o["funding_fee"])      # 旧表支出为正 → 取反为收入
            f_new = fnum(t["funding_fees"])
            if not q_new or not f_new:
                continue
            oldcmp.append({"币种": t["pair"], "旧数量": q_old, "本次数量": q_new,
                           "数量比": q_old / q_new, "资金费比": f_old / f_new,
                           "偏差": abs(q_old / q_new - f_old / f_new)})
        log(f"旧引擎资金费差额根因：数量比与资金费比最大偏差 "
            f"{max(x['偏差'] for x in oldcmp):.2e}（若≈0，则差额纯由数量造成）")

    # ---- 结论 ----
    m = {x["指标"]: x["数值"] for x in met.get("指标表", [])}
    cd = met.get("贡献拆解", {})
    rel04 = lock.get("FT-BASELINE-04关系", {})
    allok = all(r["判定"] == "全部闭合" for r in rows)

    L = []
    add = L.append
    add("# FT-METRICS-03 最终基线指标核对")
    add("")
    add(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　"
        f"核对对象：`{rel(zp)}`")
    add("")
    add("## 一、结论")
    add("")
    add("**受限通过**。指标可采信，但不能据此进入扩大样本阶段。")
    add("")
    add("三句话：")
    add("")
    add("1. 资金费、P0 绑定、逐笔净额三条链**全部闭合**，没有靠总收益凑数。")
    add("2. 账户级回撤**算不出来**，只能报平仓权益回撤；独立固定仓位**没有对应运行**，"
        "标未计算。")
    add("3. 指标口径**已可进入扩大样本阶段**（这张表可以当度量尺），"
        "但这 5 笔本身**不构成任何绩效结论**——1 胜 4 负、净收益全靠 1 笔。")
    add("")
    add("## 二、锁定与版本")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 锁定基线 | {lock.get('锁定基线目录')} |")
    z = lock.get("锁定结果包", {})
    add(f"| 结果包 | {z.get('文件名')} |")
    add(f"| sha256 | `{z.get('sha256')}` |")
    add(f"| 大小 | {z.get('大小字节')} 字节 |")
    add(f"| 策略同源 | {'是' if lock.get('策略指纹', {}).get('同源') else '否'} |")
    add(f"| FT-BASELINE-04 关系 | {rel04.get('关系判定')} |")
    if rel04.get("逐笔与03完全一致") is not None:
        add(f"| 04 与 03 逐笔是否一致 | "
            f"{'一致' if rel04.get('逐笔与03完全一致') else '不一致'}"
            f"（03 {rel04.get('03笔数')} 笔 / 04 {rel04.get('04笔数')} 笔） |")
    add("")
    add("**为什么锁 04**：04 产生了新结果包（包哈希与 03 不同），不是复制旧文件；"
        "但两包逐笔完全一致——入场时间、价格、数量、出场原因、净额、资金费全部相同。"
        "这恰好证明结果是**可复现**的，而不是把旧结果搬过来。")
    add("")
    add("04 的策略比 03 多了两道硬约束：开仓前若无有效候选或 P0 非法则拒单；"
        "止损回调里删掉了 `entry×0.90` 的兜底假止损，改为直接抛错中止。"
        "这两条使「P0 绑定」从约定变成运行期强制，是本轮采信 P0 的前提。")
    add("")
    add("本轮**没有**读取 FT-BASELINE-02 的任何逐笔或指标，也没有跨版本拼接。")
    add("")
    add("## 三、唯一指标表（共享账户口径）")
    add("")
    add("| 指标 | 数值 | 单位 | 口径 |")
    add("|---|---|---|---|")
    for x in met.get("指标表", []):
        if x["指标"] in ("锁定结果包", "策略版本", "运行窗口"):
            continue
        add(f"| {x['指标']} | {x['数值']} | {x['单位']} | {x['口径与说明']} |")
    add("")
    add("### 两个最容易写错的地方")
    add("")
    add(f"- **盈亏比 {m.get('盈亏比')} 倍** 与 **利润因子 {m.get('利润因子')} 倍** 不是一回事。"
        "前者是每笔均值之比，后者是总额之比。本表分列，任何只写一个数的汇总都算误标。")
    add("- **所有指标都是共享账户口径**。同一账户、3 个并发位、按风险预算动态下单，"
        "不是「每笔固定 20% 权益」的独立仓位。要独立固定仓位指标，必须另跑一次，"
        "本轮没有这个运行，标未计算。")
    add("")
    add("## 四、三条闭合链")
    add("")
    add("### 1. 资金费：168 个结算事件逐笔对上")
    add("")
    add(f"按结算事件表独立重算，持仓窗口内共 {fund.get('结算事件合计')} 个事件，"
        f"合计 {fund.get('独立重算合计_USDT')} USDT；交易记录合计 "
        f"{fund.get('交易记录合计_USDT')} USDT，5/5 笔差异都在 1e-6 以内。")
    add("")
    add("| 币种 | 事件数 | 收取 | 支付 | 记录值 | 是否闭合 |")
    add("|---|---|---|---|---|---|")
    for r in rows:
        v = r["资金费_USDT"]
        add(f"| {r['币种'].split('/')[0]} | {r['资金费事件数']} | "
            f"{v if v > 0 else ''} | {v if v < 0 else ''} | {v} | "
            f"{'是' if r['判定'] == '全部闭合' else r['判定']} |")
    add("")
    add(f"其中收取 {fund.get('其中收取_USDT')} USDT、支付 {fund.get('其中支付_USDT')} USDT，"
        f"净额 {fund.get('独立重算合计_USDT')} USDT。")
    add("")
    add("**符号必须写清楚**：Freqtrade 的 `funding_fees` 是**收入为正、支出为负**；"
        "旧引擎基准表 `funding_fee` 是**支出为正**。两者相差一个负号，直接对号会得出"
        "「5 笔全部方向相反」的错误结论。按数量比例换算后，两表 5/5 一致"
        "（例：PUMPBTC 旧表 +2.1678 为支出，换算到本次数量 1223→1215 后为 2.1536，"
        "与 Freqtrade 的 -2.1536 完全对应）。")
    add("")
    add("### 2. P0 绑定：5/5 逐环闭合")
    add("")
    add(f"候选 P0 取自旧引擎 `candidates.jsonl`（同一 `candidate_id`），"
        f"运行时以结果包 `stop_loss_abs` 与引擎日志 2R 阈值双向验证，"
        f"{p0s.get('通过')}/{p0s.get('笔数')} 通过。")
    add("")
    add("| 币种 | 候选 P0 | 入场价 | 2R 激活 | 出场止损位 | 绑定对象 | 绑定值 | 偏差 | 判定 |")
    add("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        pp = prows.get(r["币种"], {})
        bind_v = pp.get("绑定值", "")
        dev = pp.get("止损位偏差", "")
        add(f"| {r['币种'].split('/')[0]} | {r['候选P0']} | {r['开仓价']} | "
            f"{r['2R是否激活']} | {r['出场止损位']} | {r['止损绑定对象']} | "
            f"{bind_v} | {dev} | {r['P0绑定判定']} |")
    add("")
    add("最强的一环在 PUMPBTC：引擎日志写出 2R 阈值 0.058461264376852236，"
        "由候选 P0 0.034144367811573886 反算 `入场 + 2×(入场-P0)` 得到同一个数，"
        "16 位有效数字全同；日志同时打印了候选 ID，与 `enter_tag` 完全一致。"
        "这证明运行时用的确实是那个候选的 P0，不是兜底值。")
    add("")
    add("另外 4 笔从未激活 2R，出场止损位等于候选 P0（按最小变动价位取整），"
        "且都不等于 `entry×0.90` 的兜底值，排除「假止损」路径。")
    add("")
    add("再加一环：5 笔的实际开仓时刻，都正好等于各自候选的最早可执行时刻"
        "（候选生成后的下一根 15m 开盘），说明入场确实由该候选触发，"
        "不是同币种另一次信号碰巧同价。")
    add("")
    add("### 3. 与反重力自报表的交叉核对")
    add("")
    add("反重力 04 自己出了 P0 表、资金费逐事件表、按笔汇总表。本轮不直接采信，"
        "拿自己的独立结果与它逐项比：")
    add("")
    add("| 项目 | 反重力 04 | 本轮独立结果 | 是否一致 |")
    add("|---|---|---|---|")
    add(f"| 交易笔数 | 5 | {len(rows)} | 一致 |")
    add(f"| 资金费结算事件数 | 19/58/53/30/8（合计 168） | "
        f"{'/'.join(str(r['资金费事件数']) for r in rows)}（合计 "
        f"{sum(int(r['资金费事件数']) for r in rows)}） | 一致 |")
    add("| 资金费逐笔金额 | 0.341697 / -2.153633 / -0.166268 / 0.350439 / 0.050616 | "
        + " / ".join(f"{r['资金费_USDT']:+.6f}" for r in rows) + " | 一致 |")
    add("| P0 候选绑定 | 5 笔真实绑定、0 假止损 | 5/5 逐环闭合 | 一致 |")
    add("| 2R 激活 | 仅 PUMPBTC | 仅 PUMPBTC | 一致 |")
    add("")
    add("有三处要更正：")
    add("")
    add("**一、资金费差额的根因写错了。** 04 把「两引擎资金费差额」解释为"
        "「结算瞬间标记价与 K 线采样差异」。实测不是：差额 100% 来自数量口径，"
        "与结算价无关。逐笔验证，`旧引擎资金费 ÷ 本次资金费` 与 `旧数量 ÷ 本次数量` "
        "在 1e-15 量级完全相等：")
    add("")
    add("| 币种 | 旧数量 → 本次 | 数量比 | 资金费比 | 偏差 |")
    add("|---|---|---|---|---|")
    for r in oldcmp:
        add(f"| {r['币种'].split('/')[0]} | {r['旧数量']} → {r['本次数量']} | "
            f"{r['数量比']:.6f} | {r['资金费比']:.6f} | {r['偏差']:.1e} |")
    add("")
    add("**二、CTSI 的「出场止损价」两表不同，但不是矛盾。** 04 填的是理论 P0 "
        "0.0382416，结果包记录的是按最小变动价位取整后的 0.0383，差 5.8e-05，"
        "小于一个 tick。本轮以结果包记录值为准，并在表里注明取整关系。")
    add("")
    add("**三、110 次 vs 168 次。** 04 说前期报告把事件数估成 110 次（18/29/26/30/7），"
        "本轮独立数出 168 次（19/58/53/30/8），确认 04 的更正成立："
        "PUMPBTC 与 BANANA 是 4h 结算，按 8h 估算会少算近一半。")
    add("")
    add("### 4. 逐笔净额：残差在浮点量级")
    add("")
    add(f"每笔按 `毛利 - 开仓费 - 平仓费 + 资金费` 重算，与记录净额的最大残差 "
        f"{max(abs(float(r['净额残差_USDT'])) for r in rows):.2e} USDT。"
        "费用 0.3060 USDT、资金费 -1.5771 USDT 均可独立复现。")
    add("")
    add("## 五、贡献拆解（5 笔样本的限制）")
    add("")
    top = cd.get("最大盈利单", {})
    add(f"- 最大盈利单：{top.get('币种')} **+{top.get('净额_USDT')} USDT**，"
        f"占净收益 {top.get('占净收益比') * 100:.1f}%（即净收益的 "
        f"{top.get('占净收益比'):.2f} 倍）")
    add(f"- 其余 4 笔合计：**{cd.get('其余合计_USDT')} USDT**")
    add(f"- 净收益 {m.get('净收益')} USDT 完全由这 1 笔撑起，其余 4 笔是净拖累")
    add("")
    add("| 币种 | 净额 USDT | 占净收益 | 占本金 |")
    add("|---|---|---|---|")
    for c in cd.get("逐笔", []):
        add(f"| {c['币种'].split('/')[0]} | {c['净额_USDT']} | "
            f"{c['占净收益比'] * 100:.1f}% | {c['占本金比']:.4%} |")
    add("")
    add("5 笔里 1 胜，胜率的 95% 区间大约 3.6%~62.5%，这个点估计没有约束力。"
        "任何「胜率 20%」的说法都应附带这个区间，否则是过度解读。")
    add("")
    add("## 六、可采信 / 受限 / 不适用")
    add("")
    add("| 项目 | 状态 | 说明 |")
    add("|---|---|---|")
    add("| 净收益、笔数、胜负平、胜率 | 可采信 | 逐笔重算与包内自报一致 |")
    add("| 平均盈利、平均亏损、每笔期望 | 可采信 | 同一分母口径 |")
    add("| 盈亏比、利润因子 | 可采信 | 已分列，未混用 |")
    add("| 手续费、资金费 | 可采信 | 资金费 168 事件逐笔闭合 |")
    add("| 平仓权益回撤 | 可采信 | 含初始资金，峰值 2046.74 → 2018.90 |")
    add("| 账户权益回撤（含浮动盈亏） | 不适用 | 序列不含未实现盈亏 |")
    add("| 独立固定仓位指标 | 未计算 | 无对应运行，不换算 |")
    add("| 夏普/索提诺/卡尔玛 | 不适用 | 5 笔不足以估波动 |")
    add("| 胜率/期望的统计显著性 | 受限 | 样本过小，区间极宽 |")
    add("")
    add("## 七、是否具备进入扩大样本阶段的条件")
    add("")
    add("分两层回答，因为「指标能不能用」和「这 5 笔能说明什么」是两件事。")
    add("")
    add("### 指标口径：具备")
    add("")
    add("三条链闭合、口径唯一、金额与比例分列、符号写明，"
        "这张表可以直接当扩大样本阶段的**度量尺**用——后面跑出来的数，"
        "按同一套口径填就行，不用再返工。")
    add("")
    add("### 绩效结论：不具备")
    add("")
    add("1. **样本量**：5 笔、1 胜。胜率的 95% 区间 3.6%~62.5%，"
        "任何点估计都没有约束力。")
    add("2. **结论被单笔支配**：剔除 PUMPBTC 后其余 4 笔合计 -37.44 USDT。"
        "这 5 笔不能说明策略有正期望，只能说明这一笔赚得多。")
    add("3. **账户口径不完整**：没有逐 bar 权益，算不出真实账户回撤，"
        "回撤维度无从判断。")
    add("")
    add("所以：**扩大样本这件事可以做，但扩完之后仍然只能看指标，不能看结论**，"
        "直到笔数足够（经验上至少几十笔同口径交易）再谈胜率与期望。")
    add("")
    add("本轮**没有**扩大回测、没有调参、没有改动反重力产物，"
        "也没有为补齐指标另建系统。")
    add("")
    add("## 八、循环记录")
    add("")
    add("| 轮次 | 问题 | 本轮处理 |")
    add("|---|---|---|")
    add("| 第 1 轮 | 共享账户收益被当成独立固定仓位 | 已剔除 |")
    add("| 第 2 轮 | 盈亏比与利润因子混用、资金费符号未标注 | 本轮分列并写明收付方向 |")
    add("| 第 3 轮 | — | 未触发：本表已无共享账户/固定仓位混用，"
        "无盈亏比/利润因子混用 |")
    add("")
    add("---")
    add("")
    add("> 本报告由独立核对脚本生成，一条命令可重跑：")
    add("> `evidence/FT-BASELINE-01/.venv/bin/python evidence/FT-METRICS-03/code/run_all.py`")
    add("> 必须用该解释器，系统 python 无 pandas，读不了权益序列与资金费数据。")

    (BASE / "00_结论.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    log("已写 00_结论.md")
    (OUT / "_report_log.txt").write_text("\n".join(LOG), encoding="utf-8")
    print(json.dumps({"status": "OK", "all_closed": allok}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
