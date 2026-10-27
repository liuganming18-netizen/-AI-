# -*- coding: utf-8 -*-
"""01 冻结分析口径（在计算任何逐笔指标之前写死）。

诚实声明：本脚本运行时，上游 `00_4小时最低价跟踪退出综合研究报告.md` 已被读过
（不读它就无法知道要核对哪些结论），但**尚未读取任何一份 trades_*.csv / out/*.csv 的数值**，
也未运行任何重算。冻结的是"怎么算、按什么判"，不是"结果是多少"。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

import _lib as L

TZ = timezone(timedelta(hours=8))


def sha(p):
    p = L.BASE_DIR / p
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


PLAN = {
    "工单": "FT-LOW4H-GRID-AUDIT-02",
    "冻结时刻": datetime.now(TZ).isoformat(timespec="seconds"),
    "对象": "evidence/FT-LOW4H-GRID-01/（修复后 144 组：4 档初始止损 s × 4 档跟踪距离 d × 9 组入场参数）",
    "上游版本指纹": {
        "基类_WyckoffLow4hGridBase.py": sha("evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py"),
        "运行脚本_01_run_all_grid_backtests.py": sha("evidence/FT-LOW4H-GRID-01/code/01_run_all_grid_backtests.py"),
        "对账脚本_02_reconcile_and_analyze.py": sha("evidence/FT-LOW4H-GRID-01/code/02_reconcile_and_analyze.py"),
        "配置_config.json": sha("evidence/FT-LOW4H-GRID-01/user_data/config.json"),
        "上游报告": sha("evidence/FT-LOW4H-GRID-01/00_4小时最低价跟踪退出综合研究报告.md"),
        "旧审核快照_错误基类": sha("evidence/FT-LOW4H-GRID-AUDIT-01/out/快照_基类_WyckoffLow4hGridBase_20261022T153043.py"),
    },

    # ---------- 一、来源核实口径 ----------
    "来源核实": {
        "规模判据": "144 个策略名 = 16 组合 × 9 参数组，无缺、无重、无越界；组合代号与派生类属性一致",
        "压缩包口径": "144 个 backtest-result-<策略>.zip 与 144 份 trades_<策略>.csv 一一对应；"
                     "其余 .zip 必须逐个点名说明用途并排除出结果集",
        "运行时版本证据（优先级从高到低）": [
            "① zip 内嵌的 <run>_<策略>.py：这是回测进程当时真正加载的派生类源码，逐文件比 SHA",
            "② zip 内嵌的 <run>_config.json：运行时配置（max_open_trades / dry_run_wallet / fee / datadir / 币池）",
            "③ 每组日志首行时间戳（进程启动）与末次写入时间（结束），与基类 mtime 的先后关系",
            "④ 行为反证：若 stoploss_s / trail_d 未被读取，四档初始止损会塌缩成同一比例——"
            "用逐笔 open_rate 与首次生效止损的比例分布反推实际读到的 s、d",
        ],
        "当前源码 SHA 的地位": "只作为'事后状态'记录，不能替代①②③④；任何一组缺少①②③④之一即列入'来源未证实'",
        "旧错误产物": "evidence/FT-LOW4H-GRID-AUDIT-01/ 全量保留，标记为'针对已作废版本'，不删除、不引用其结论",
    },

    # ---------- 二、逐笔执行口径 ----------
    "执行核对": {
        "初始止损": "P0 = 实际成交价 open_rate × (1 − s)",
        "跟踪候选": "Pcand = 最近一根已收盘 4h K 线的 low × (1 − d)，"
                    "已收盘判据 = 4h 桶起点 + 4h ≤ 决策时刻（决策时刻取 15m K 线开盘时间）",
        "生效止损": "Peff = max(P0, 上一生效止损, Pcand)，只上移不下移",
        "重建方式": "逐 15m K 线重放引擎语义：先按 dir_correct（当前止损 < 本根最低价）决定是否调用，"
                    "再按 adjust_stop_loss 只升不降，最后判 low ≤ 止损；止损 > 本根最高价时按开盘价成交（跳空）",
        "禁止": "不得用 trailing_stop_loss 标签代替机制判断；不得把新止损追溯套用到生效前的 K 线；"
                "不得为'新止损高于当前价'的情形伪造更优成交价",
        "分类": "初始止损 / 跟踪盈利 / 跟踪亏损 / 期末技术结算，四类由重建结果判定，不读引擎标签",
    },

    # ---------- 三、账户与指标口径 ----------
    "指标口径": {
        "层级": {
            "L1 单账户": "每组独立 2000 USDT 账户；1R = 开仓时权益 × 0.5%（首笔恒 10 USDT）",
            "L2 跨参数组中位": "同一组合 9 个参数组的中位数（9 组共用入场事件，不是 9 次独立重复）",
            "L3 组间合计": "同一组合 9 个参数组各自 2000 账户的盈亏相加 = 18,000 USDT 名义本金的结果，"
                          "**不等于**单账户收益，禁止与 L1/L2 混排比较",
        },
        "R": "R_i = profit_abs_i / (开仓时权益 × 0.005)；权益 = 2000 + Σ(close_ms ≤ open_ms 的已实现盈亏)",
        "净收益": "profit_abs 求和；profit_abs = (close − open) × amount − 手续费 + funding_fees",
        "手续费": "amount × (open_rate + close_rate) × 0.0005，逐笔断言；fee_open/fee_close 是费率不是金额",
        "资金费": "−Σ(rate × mark × amount)；时间戳先归一整秒，窗口 [open_ms, close_ms] 闭区间，"
                  "标记价取最近一个非空值（跳过 NaN 前向填充）",
        "权益曲线": "主口径 MTM：15m 收盘盯市，equity(t) = 2000 + Σ已实现 + Σ未平仓[(P15(t) − open)×amount "
                    "− 开仓费 − 已发生资金费]；对照 A 不含资金费分摊、B 资金费开仓即全扣、C 已实现曲线",
        "回撤": "主口径 = MTM 曲线最大回撤（含未平仓浮盈浮亏）；已实现曲线回撤只作对照，不得用于结论",
        "期末结算": "exit_reason = force_exit 的单列为'期末技术结算'，其盈亏单独报告，不计入已实现胜率",
    },

    # ---------- 四、稳定性判据（沿用 AUDIT-02，不改门槛） ----------
    "稳定性判据": {
        "C1": "L2 中位净收益 > 0",
        "C2": "9 组中盈利组数 ≥ 7",
        "C3": "缩尾 ±5R 后 L2 中位仍 > 0",
        "C4": "剔除首尾 1% 交易后 L2 中位仍 > 0",
        "C5": "剔除贡献最大的 3 个币后 L2 中位仍 > 0",
        "C6": "13 个假设结束日（每季末 + 全期）中 ≥ 9 个为正",
        "C7": "跨 4 个 s 档位（同 d）名次稳定：该组合在自身 d 列的 4 个 s 中排名 ≤ 2",
        "C8": "改善不能只来自'两边都成交'的交易：与上一轮配对时，新增/减少/跳过交易之外的净改善占比 ≤ 50%",
        "通过门槛": "C1~C8 中 ≥ 5 条满足才算'稳定'；C8 为必要条件",
    },
    "统计方法": {
        "配对": "同参数组内 本轮 − 上一轮（FT-STOP-GRID-02）逐事件配对；同时报告新增/减少/跳过交易",
        "符号检验": "精确二项双侧，α=0.05",
        "bootstrap": "按事件块重采样（同一事件跨组合相关），2000 次，报中位与 95% 区间",
        "多重比较": "BH-FDR，族 = 全部组合×参数组的配对比较",
        "不可归因": "本轮同时改了启动时点、参考 K 线、跟踪距离三项，任何差异不得归因于单一因素",
    },

    # ---------- 五、判定用语 ----------
    "判定用语": {
        "通过": "该项全部判据满足，且证据可被脚本机械复算",
        "不通过": "存在明确反例，已定位到具体行/笔",
        "未核实": "证据链缺失（无法证明运行时版本、或页面不可见），不计入通过",
        "来源未证实": "该组的运行时版本无法证明，其结果单列，不混入通过结果",
    },
    "不做的事": [
        "不自行选定生产参数、不追加组合、不重跑 144 组回测",
        "不打开留出期、不启动新实验、不改生产代码与原账本",
        "不因当前源码正确就宣布新结果通过（必须走运行时证据）",
        "不沿用旧审核的'不通过'结论（旧审核针对已作废的 82e2a3e1 版本）",
    ],
}


def main() -> None:
    L.OUT.mkdir(parents=True, exist_ok=True)
    p = L.OUT / "01_冻结分析计划.json"
    if p.exists():
        raise SystemExit("冻结文件已存在，拒绝覆盖（口径必须先于结果）")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(PLAN, f, ensure_ascii=False, indent=2)
    print("冻结完成:", p)
    print("冻结时刻:", PLAN["冻结时刻"])
    print("上游基类 SHA:", (PLAN["上游版本指纹"]["基类_WyckoffLow4hGridBase.py"] or "")[:16])
    print("旧审核快照 SHA:", (PLAN["上游版本指纹"]["旧审核快照_错误基类"] or "")[:16])


if __name__ == "__main__":
    main()
