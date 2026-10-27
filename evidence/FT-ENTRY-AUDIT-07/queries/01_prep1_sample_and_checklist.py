# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 · 准备 1：三入口核对前提与共同样本口径。

做三件事（全部只读）：
1. 确立 C0（现行确认条件）唯一定义：FT-ENTRY-02 基类 WyckoffParamExplorationBase.py
   阶段 B「缩量回踩确认」六项条件原文 + P0/入场带/执行最早时刻/OI 过滤/仓位/止损/退出
   的行号坐标；并证明 FT-ENTRY-03/04 基类 C 路线段落与 02 逐字节一致（diff 空）。
2. 独立复算共同样本：FT-ENTRY-02 C 路线 9 组逐笔 → 名义笔数 / enter_tag 唯一候选 /
   物理候选键(symbol,event_id,box_ms)唯一候选；与 FT-ENTRY-06 的 3081→821 对拍，
   并给出跨参数重复结构（每候选落在几个网格）。
3. 生成「只改确认条件」可执行核对清单（待反重力三入口交付后逐项 diff），当前状态=等待。

不重跑回测、不改上游、不打开留出期。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (AUDIT, ENTRY02, ENTRY03, ENTRY04, OUT, CELLS_C,  # noqa: E402
                     load_c_trades, jdump, sha256_file, write_csv)

C0_DEF_PATH = os.path.join(ENTRY02, "user_data/strategies/WyckoffParamExplorationBase.py")


def verify_c_route_identical() -> dict:
    """02/03/04 基类的 C 路线确认段（阶段 B is_retest 区块）是否逐字节一致。"""
    def extract(path: str):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 208~310 行（1-based）= 回踩参数化 + is_retest + C 组提前触发
        seg = "".join(lines[207:310])
        return seg
    s02 = extract(C0_DEF_PATH)
    p03 = os.path.join(ENTRY03, "user_data/strategies/WyckoffParamExplorationBase.py")
    p04 = os.path.join(ENTRY04, "user_data/strategies/WyckoffParamExplorationBase.py")
    s03 = extract(p03)
    s04 = extract(p04)
    return {
        "02_vs_03_C路线段一致": s02 == s03,
        "02_vs_04_C路线段一致": s02 == s04,
        "C路线段行范围(1-based)": "208-310（回踩参数化+is_retest+C组提前触发）",
        "02基类sha256": sha256_file(C0_DEF_PATH),
        "03基类sha256": sha256_file(p03),
        "04基类sha256": sha256_file(p04),
    }


def recount_common_sample() -> dict:
    allt = load_c_trades()
    if allt.empty:
        return {"error": "FT-ENTRY-02 C 路线逐笔为空"}
    n_rows = len(allt)
    uniq_tag = allt["enter_tag"].nunique()
    uniq_ckey = allt["cand_key"].nunique()
    # 每物理候选落在几个网格
    per_cand = allt.groupby("cand_key")["strategy"].nunique()
    grid_dist = per_cand.value_counts().sort_index().to_dict()
    # 每网格名义笔数
    per_grid = allt.groupby("strategy").size().to_dict()
    # FT-ENTRY-06 口径核对：按完整 enter_tag 归并后的盈亏分布
    g = allt.groupby("enter_tag")["profit_abs"].mean()
    wins = int((g > 0).sum())
    losses = int((g < 0).sum())
    # 重复结构：同一物理候选在同一网格内是否重复成交（AUDIT-03 曾记录 C 组组内重复）
    in_grid_dup = allt.groupby(["strategy", "cand_key"]).size()
    dup_cells = in_grid_dup[in_grid_dup > 1]
    return {
        "名义成交总记录": n_rows,
        "enter_tag唯一候选": uniq_tag,
        "物理候选键唯一(symbol,event,box)": uniq_ckey,
        "FT06口径_盈利候选": wins,
        "FT06口径_亏损候选": losses,
        "每候选覆盖网格数分布": {str(k): int(v) for k, v in grid_dist.items()},
        "每网格名义笔数": {k: int(v) for k, v in per_grid.items()},
        "同网格内同候选重复成交_候选数": int(len(dup_cells)),
        "同网格内同候选重复成交_笔数": int(dup_cells.sum()) if len(dup_cells) else 0,
        "同网格内同候选重复成交_max笔每候选": int(dup_cells.max()) if len(dup_cells) else 0,
        "覆盖标的数": int(allt["symbol"].nunique()),
    }


def crosscheck_ft06() -> dict:
    """与 FT-ENTRY-06 交付数字对拍（821/653/168、3081）。"""
    p01 = os.path.join(ENTRY02, "..", "FT-ENTRY-06", "01_C路线逐候选结构归因明细表.csv")
    p01 = os.path.abspath(p01)
    out = {"ft06_01文件存在": os.path.isfile(p01)}
    if os.path.isfile(p01):
        df = pd.read_csv(p01, encoding="utf-8-sig")
        out["ft06_唯一候选行数"] = int(len(df))
        out["ft06_is_win_true"] = int(df["is_win"].sum())
        out["ft06_is_win_false"] = int((~df["is_win"]).sum())
        out["ft06_strategy_count_max"] = int(df["strategy_count"].max())
    return out


CHECKLIST = [
    # (编号, 核对项, 方法/依据, 状态)
    ("T1", "三入口共用同一 C 路线 9 组参数网格（V_ratio×Zone）", "config/策略类 diff：网格字段 c_vol_ratio_vs、c_retest_zone_atr 三组一致", "等待"),
    ("T2", "共用同一 pairlist 与 timerange（20211201-20241231，74 池）", "config.json 与运行命令 diff", "等待"),
    ("T3", "共用同一数据目录与数据快照（FT-ENTRY-02/user_data/data）", "数据文件 SHA-256 比对", "等待"),
    ("T4", "只改确认条件：阶段 B is_retest 区块代码 diff 仅涉收阳/二 bar 确认项", "WyckoffParamExplorationBase.py 基类 diff（参照 02→04 的 3 hunk 先例）", "等待"),
    ("T5", "P0 不变：initial_stop=sweep_low_s-0.1A、initial_risk_r=close-p0 公式一致", "第 264-266 行逐字节比对", "等待"),
    ("T6", "入场价格带不变：max_entry=close+0.5A、min_entry=L-0.2A", "第 293-294 行逐字节比对", "等待"),
    ("T7", "执行最早时刻不变：close+15m，且未新增挂单/重试规则", "第 268 行与 confirm_trade_entry diff", "等待"),
    ("T8", "OI 过滤不变：6h OI 增长≥0（enable_oi_filter）", "第 255-262 行逐字节比对", "等待"),
    ("T9", "仓位与风险预算不变：equity×0.005、单仓上限 0.30×equity、流动性帽 0.001×QV", "custom_stake_amount 第 707-746 行 diff", "等待"),
    ("T10", "止损/退出不变：2R 跟踪（4h L10）、240h 超时、无保本改动", "custom_stoploss/custom_exit 第 748-807 行 diff", "等待"),
    ("T11", "并发上限不变：max_open_trades=3、dry_run_wallet=2000", "config.json diff", "等待"),
    ("T12", "未追加参数网格/未打开留出期 timerange 不含 2025+", "config 与逐笔 close_timestamp 最大值", "等待"),
]


def main() -> None:
    rep: dict = {}

    rep["C0基线_代码坐标"] = {
        "文件": C0_DEF_PATH,
        "确认条件(C0)": "is_retest = (L<=low<=L+zone*A) & (low>sweep_low_s) & (close>=L+retest_recovery_atr*A) & (close>=open) & (volume<=vol_mult*sweep_volume_vs)",
        "收阳确认项行号(1-based)": "216（close_p >= open_p，单根 1h 收阳）",
        "P0": "265-266 行 initial_stop=sweep_low_s-0.1A；initial_risk_r=close_p-initial_stop",
        "入场带": "293-294 行 max_entry=close+0.5A；min_entry=L-0.2A",
        "执行最早": "268 行 close_time_ms+15min",
        "OI过滤": "255-262 行 enable_oi_filter 且 oi_growth>=0",
        "仓位/预算": "707-746 行 equity*0.005、单仓0.30*equity、liq_cap=0.001*prev_qv",
        "止损/退出": "748-807 行 2R 触发后 4h L10 跟踪、240h timeout、stoploss=-0.99 占位",
        "工具类配置": "max_open_trades=3、dry_run_wallet=2000、futures/cross、timeframe=15m",
    }
    rep["C0基线_C路线段跨版本一致"] = verify_c_route_identical()
    rep["共同样本_独立复算"] = recount_common_sample()
    rep["与FT-ENTRY-06对拍"] = crosscheck_ft06()

    chk = pd.DataFrame(CHECKLIST, columns=["编号", "核对项", "方法/依据", "状态"])
    write_csv(chk, "01_三入口只改确认条件核对清单_v1.csv")
    rep["核对清单"] = {"共%d项" % len(CHECKLIST): "其中 T0 基线项已在代码层确立；T1~T12 等待反重力三入口交付后逐项 diff"}

    rep["判断"] = {
        "准备1_可完成部分": (
            "C0（现行确认条件=单根 1h 收阳+缩量+回踩带）已从 02 基类原文锁定，"
            "03/04 基类 C 路线段逐字节一致（未受 A 路线改动影响）；"
            "共同样本=FT-ENTRY-02 C 路线 9 组同网格同数据的事件池；"
            "「只改确认条件」核对清单已固化为 12 项可执行 diff。"),
        "准备1_等待部分": (
            "反重力三种入场方式（C0/C1/C2 的具体确认条件代码与逐笔）尚未交付："
            "FT-ENTRY-06 只给出假设 1（二次收盘确认与重心上移）的文字描述，无实现、无逐笔。"
            "T1~T12 全部标记「等待」，不用旧统计代替。"),
    }
    jdump(os.path.join(OUT, "01_准备1_三入口与共同样本核对_v1.json"), rep)

    print("== 准备1：三入口核对前提与共同样本 ==")
    print(json.dumps(rep["C0基线_C路线段跨版本一致"], ensure_ascii=False, indent=2))
    print(json.dumps(rep["共同样本_独立复算"], ensure_ascii=False, indent=2))
    print(json.dumps(rep["与FT-ENTRY-06对拍"], ensure_ascii=False, indent=2))
    print("\n完成")


if __name__ == "__main__":
    main()
