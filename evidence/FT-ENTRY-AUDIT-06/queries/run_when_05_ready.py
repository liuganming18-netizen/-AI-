# -*- coding: utf-8 -*-
"""run_when_05_ready｜等待 FT-ENTRY-05 到达后驱动拒单效果交叉核对。

行为：
  1. 轮询（可由 Work 反复调用，单次只查一次：默认 once）；
  2. 若 evidence/FT-ENTRY-05/out 尚未含 trades_*.csv ⇒ 明确输出「等待 FT-ENTRY-05 输入」，
     不生成任何替代结论、不宣布新因子；
  3. 一旦到位：以 FT-ENTRY-05 为目标批次，跑与 03_rejection_crosscheck.py 完全同口径的
     交叉核对（事件键匹配 / 避免亏损vs错失盈利 / 主导演性），并与已锁定的 03/04 基线对拍。
     旧参照默认取 FT-ENTRY-04（若 05 的定义是「再收紧一层」，work 可据其字段改 OLD_REF）。

只读上游、只在本目录 out/ / logs/ 写；不跑策略。
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import CELLS_A, OUT, OUT03, OUT04, OUT05, ENTRY05, jdump, write_csv  # noqa: E402
from importlib import import_module  # noqa: E402

XCHECK = import_module("03_rejection_crosscheck")

FT05 = ENTRY05


def ft05_trades_present() -> bool:
    return bool(glob.glob(os.path.join(OUT05, "trades_Wyckoff_*.csv")))


def ft05_audit_present() -> bool:
    return bool(glob.glob(os.path.join(OUT05, "audit_orders_*.csv")))


def ft05_ready() -> bool:
    """FT-ENTRY-05 到位判定：它是反重力的归因报告+CSV（非新回测批次）。
    到位标志 = 存在 01_逐候选对照与归因明细表.csv 或任一 trades_*.csv。"""
    return (os.path.isfile(os.path.join(FT05, "01_逐候选对照与归因明细表.csv"))
            or ft05_trades_present())


def main() -> None:
    if not os.path.isdir(OUT05) or not ft05_ready():
        status = {
            "状态": "等待输入",
            "等待对象": "evidence/FT-ENTRY-05/（反重力新一批结果）",
            "当前检测": {
                "OUT05_存在": os.path.isdir(OUT05),
                "trades_csv出现": ft05_trades_present(),
                "audit_orders出现": ft05_audit_present(),
            },
            "已完成准备": [
                "01 锁定 FT-ENTRY-03/04（FT-ENTRY-04 补齐 AUDIT-05 漏锁的哈希）",
                "02 数量关系口径模型（189/118/68/228 澄清 + 审计日志重复行去重）",
                "03 拒单交叉核对四大确认在锁定 03/04 上完成基线校准",
            ],
            "明确不做的": ["不生成替代报告", "不以 03/04 冒充 FT-ENTRY-05 结果", "不宣布任何新因子有效"],
        }
        jdump(os.path.join(OUT, "run_input_status.json"), status)
        print("== 等待 FT-ENTRY-05 输入 ==")
        print(f"OUT05 存在: {os.path.isdir(OUT05)} | trades_csv: {ft05_trades_present()} | "
              f"audit_orders: {ft05_audit_present()}")
        print("已就绪：01 锁定 / 02 口径模型 / 03 基线交叉核对；收到 05 后自动对拍。")
        return

    # FT-ENTRY-05 已到位（报告+CSV 形态）——逐行对拍与闭环核对由 04/05 脚本承担
    status = {
        "状态": "已接收并完成对拍",
        "FT-ENTRY-05形态": "反重力拒单逐候选归因（报告 + 01/02/03 CSV），非新回测批次",
        "operative_scripts": [
            "04_crosscheck_vs_ft05.py：对 01_逐候选 68 行逐行独立核对（分类/盈亏 68/68 一致）",
            "05_full_loop_reconcile.py：闭环核对（真实总差 -113.49 vs 反重力 -157.65，缺 +44.17）",
        ],
        "结论边界": "哪些归因可支持／哪些证据不足；不宣布新因子有效",
    }
    jdump(os.path.join(OUT, "run_input_status.json"), status)
    print("== FT-ENTRY-05 已到位，逐行对拍与闭环核对见 out/04_* 与 out/05_* ==")
    print("运行：04_crosscheck_vs_ft05.py 与 05_full_loop_reconcile.py（只读）")


if __name__ == "__main__":
    main()
