# -*- coding: utf-8 -*-
"""04_最小因果验证方案｜只交方案与可用输入，不跑任何回测。

工单要求：
(a) 哪些已有研究区间有真实信号可做前缀检查；
(b) 成交价等于／高于 H 的拒单怎样通过真实引擎验证。

本脚本只做：把「可用输入」逐项盘点到位，给出可执行的最小方案、预期证据与停止条件，
并列出证据缺口。不下载、不改时间戳、不打开 2026 留出、不重跑整套实验。

产出：
- out/04_前缀锚点_v1.csv        （可直接作为 --timerange 端点的真实信号时刻）
- out/04_可用输入盘点_v1.json    （引擎/策略/数据/信号四类输入的存在性与路径）
- out/04_最小因果验证方案_v1.json（方案 A 前缀检查 / 方案 B 拒单真实引擎验证 / 方案 C PIT 检查）
- out/04_证据缺口_v1.csv
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (CELLS_A, ENTRY04_OUT, EV, HOLDOUT_START, OUT,  # noqa: E402
                     RESEARCH_END, RESEARCH_START, jdump, load_fullsweep_runs,
                     load_l1, load_whitelist_70, norm_pair, read_csv, write_csv)

RS, RE = RESEARCH_START, RESEARCH_END
FT_BIN = os.path.join(EV, "FT-BASELINE-01/.venv/bin/freqtrade")
V31 = os.path.join(EV, "..", "数据", "研究输入", "WyckoffMaster_v31")
V31_COV = os.path.join(V31, "逐币覆盖_v31.csv")


def rejected_footprint() -> pd.DataFrame:
    rows = []
    for code, strat, _, _ in CELLS_A:
        p = os.path.join(ENTRY04_OUT, f"audit_orders_{strat}.csv")
        if not os.path.isfile(p):
            continue
        a = pd.read_csv(p)
        real = a[a["reason"] != "retry_prohibited_already_evaluated"].drop_duplicates(
            subset=["candidate_id", "planned_execution_time_ms"])
        ge = real[real["reason"].isin(["fill_price_ge_frozen_h_greater",
                                       "fill_price_ge_frozen_h_equal"])]
        for _, r in ge.iterrows():
            rows.append({"code": code, "strategy": strat,
                         "candidate_id": r["candidate_id"],
                         "pair": norm_pair(r["pair"]),
                         "fill_price": r["fill_price"], "frozen_H": r["frozen_H"],
                         "planned_execution_time": r.get("planned_execution_time")})
    return pd.DataFrame(rows)


def main() -> None:
    l1 = load_l1()
    w70 = load_whitelist_70()
    fs = load_fullsweep_runs()
    s1 = set(l1["symbol"])

    rej = rejected_footprint()
    rej_coins = sorted(set(rej["pair"]))
    rej_cands = sorted(set(rej["candidate_id"]))

    # ── 前缀锚点：真实信号时刻（入场时刻 / 计划执行时刻）──
    anchors = []
    # (1) FT-ENTRY-04 审计日志里的候选（含被拒与放行）= 已知真实候选
    for code, strat, _, _ in CELLS_A:
        p = os.path.join(ENTRY04_OUT, f"audit_orders_{strat}.csv")
        if not os.path.isfile(p):
            continue
        a = pd.read_csv(p)
        real = a[a["reason"] != "retry_prohibited_already_evaluated"].drop_duplicates(
            subset=["candidate_id", "planned_execution_time_ms"])
        for _, r in real.iterrows():
            anchors.append({
                "锚点来源": "FT-ENTRY-04 audit_orders（已知候选）",
                "symbol": norm_pair(r["pair"]), "code": code, "strategy": strat,
                "candidate_id": r["candidate_id"],
                "信号时刻": r.get("planned_execution_time"),
                "决策性质": ("被拒(fill>=H)" if r["reason"] in
                            ("fill_price_ge_frozen_h_greater", "fill_price_ge_frozen_h_equal")
                            else ("放行(fill<H)" if r["reason"] == "fill_price_lt_frozen_h"
                                  else "原边界拒单")),
                "fill_price": r.get("fill_price"), "frozen_H": r.get("frozen_H"),
            })
    # (2) FULL_SWEEP 真实成交入场时刻（可扩展币的真实信号）
    t = fs[fs["trades"] > 0].copy()
    t_rows = []
    for _, r in t.iterrows():
        tl = r.get("trade_list") or []
        for tr in (tl if isinstance(tl, list) else []):
            t_rows.append({"symbol": r["symbol"], "group": r["group"],
                           "t_in": tr.get("t_in"), "why": tr.get("why")})
    td = pd.DataFrame(t_rows)
    if not td.empty:
        td["symbol"] = td["symbol"].astype(str)
        exp_sig = set(read_csv(os.path.join(OUT, "03_可扩展样本表_v1.csv"))
                      .query("可否做最小因果验证")["symbol"])
        td = td[td["symbol"].isin(exp_sig)]
        # 每币取最早的 3 个不同入场时刻，作为候选锚点
        td = td.sort_values("t_in").drop_duplicates(subset=["symbol", "t_in"])
        for sym, g in td.groupby("symbol"):
            for _, r in g.head(3).iterrows():
                anchors.append({
                    "锚点来源": "FULL_SWEEP runs.jsonl trade_list.t_in（可扩展币真实成交）",
                    "symbol": sym, "code": "", "strategy": "",
                    "candidate_id": "", "信号时刻": pd.to_datetime(r["t_in"], unit="ms", utc=True)
                    .strftime("%Y-%m-%d %H:%M:%S"),
                    "决策性质": "旧引擎窗口内成交（窗口可能截断）",
                    "fill_price": "", "frozen_H": "",
                })
    anc = pd.DataFrame(anchors)
    write_csv(anc, "04_前缀锚点_v1.csv")

    # ── 可用输入盘点 ──
    v31 = read_csv(V31_COV)
    v31_coins = set()
    if not v31.empty:
        d = v31["path"].str.extract(r"/([A-Z0-9]+)\.parquet")[0]
        v31_coins = set(d.dropna())
    is15 = val15 = None
    if not v31.empty:
        is15 = set(v31[v31["dataset"] == "is/ohlcv/15m"]["path"].str.extract(r"/([A-Z0-9]+)\.parquet")[0].dropna())
        val15 = set(v31[v31["dataset"] == "val/ohlcv/15m"]["path"].str.extract(r"/([A-Z0-9]+)\.parquet")[0].dropna())

    datadir_empty = True
    for root, _, files in os.walk(os.path.join(EV, "FT-ENTRY-04/user_data/data")):
        if files:
            datadir_empty = False
            break

    inputs = {
        "真实引擎": {
            "路径": os.path.relpath(FT_BIN, EV),
            "存在": os.path.isfile(FT_BIN),
            "版本": "freqtrade 2026.9",
            "用法": "freqtrade backtesting -c <config> --strategy <name> --strategy-path <dir> "
                    "--timerange <START-END> --cache none --export trades",
        },
        "策略": {
            "目录": "evidence/FT-ENTRY-04/user_data/strategies",
            "A路线格数": len(CELLS_A), "Baseline": 1,
            "约束实现": "基类 WyckoffParamExplorationBase.confirm_trade_entry（A 路线 fill_price >= frozen_H → REJECT）",
            "对照": "evidence/FT-ENTRY-03/user_data/strategies（无该约束，基类为 FT-ENTRY-03 版）",
        },
        "数据_v31_物理隔离": {
            "根": "数据/研究输入/WyckoffMaster_v31",
            "is_覆盖": "2021-10-01~2024-12-31（覆盖冻结研究期）",
            "is_币数": len(is15) if is15 else None,
            "val_覆盖": "2024-10-01~2026-04-14（止于旧 Holdout 起点 2026-04-15 之前，不含 Holdout）",
            "val_币数": len(val15) if val15 else None,
            "L1_241_在is内": len(s1 & is15) if is15 else None,
            "L1_241_在val内": len(s1 & val15) if val15 else None,
            "拒单9币_在is内": len(set(rej_coins) & is15) if is15 else None,
            "拒单9币_在val内": len(set(rej_coins) & val15) if val15 else None,
            "禁用目录": "数据/研究输入/WyckoffMaster/（v3.0 全时段含 Holdout，已标 DEPRECATED 勿用于选型）",
        },
        "freqtrade数据包": {
            "FT-ENTRY-04_datadir是否为空": datadir_empty,
            "含义": ("FT-ENTRY-04 跑完后 datadir 已被清空（0 文件）；任何真实引擎重跑都需"
                     "先用 FT-DATA-01 的转换方法把 v31 parquet 本地转成 freqtrade feather"
                     "（OHLCV 6 列 / OI 3 列 / funding 2 列、不填 0、UTC naive、严格递增）。"
                     "这是本地格式转换，不属于新增下载。"),
            "FT-DATA-01转换口径": "evidence/FT-DATA-01/00_输入包说明.md + manifest.json 的 13 项 PASS 检查",
        },
        "信号_真实可用": {
            "拒单足迹_去重拒单数": int(len(rej)),
            "拒单足迹_唯一候选": len(rej_cands),
            "拒单足迹_币": rej_coins,
            "拒单足迹_每格数": rej.groupby("code").size().to_dict(),
            "前缀锚点总数": int(len(anc)),
            "前缀锚点_已知候选类": int((anc["锚点来源"].str.contains("audit_orders")).sum()) if not anc.empty else 0,
            "前缀锚点_可扩展币真实成交类": int((anc["锚点来源"].str.contains("trade_list")).sum()) if not anc.empty else 0,
        },
    }

    # ── 方案 ──
    plan = {
        "工单": "FT-RESEARCH-01｜最小后续因果验证方案（只交方案，不执行）",
        "总原则": [
            "不重跑整套实验：只做最小差分/前缀核对，规模以「币 × 格 × 2（约束开/关）」计",
            "不新增下载：数据一律取自本机 v3.1 物理隔离目录",
            "不改时间戳、不打开 2026 留出数据",
            "所有引擎运行必须落在 is/ 窗（2021-10-01~2024-12-31）或 val/ 窗（2024-10-01~2026-04-14），"
            "二者均不含旧 Holdout（2026-04-15~2026-10-15）",
            "结论限定为「机制是否成立」，不据此宣布新因子或新策略有效",
        ],
        "方案A_前缀检查（因果/无前瞻）": {
            "问题": "已有研究区间里哪些有真实信号，可用来做前缀（截断）检查，证明信号不依赖未来数据",
            "可用区间与信号": {
                "区间1_冻结研究期_is": {
                    "窗口": "2021-12-01~2024-12-31（is/ 数据自 2021-10-01 起，含 220 根 4h 预热）",
                    "L1有候选币": 125, "L1有成交币": 100,
                    "其中可扩展171_有候选": 92, "有成交": 71,
                    "信号锚点来源": "FT-ENTRY-04 audit_orders_*.csv 的 planned_execution_time（已知候选，最可靠）",
                },
                "区间2_val_窗": {
                    "窗口": "2024-10-01~2026-04-14（止于 Holdout 之前）",
                    "L1有候选币": 107, "L1有成交币": 86,
                    "其中可扩展171_有候选": 76, "有成交": 60,
                    "信号锚点来源": "FULL_SWEEP runs.jsonl trade_list.t_in（旧引擎窗口内真实成交）",
                    "注意": "val 窗已被 FULL_SWEEP 扫描过（OOS-READY-01 归入 S4 已接触），"
                            "故 val 只能用于机制核对，不能当干净样本外",
                },
                "不可用区间": {
                    "旧Holdout_2026-04-15~2026-10-15": "已消耗 37 笔唯一交易 +234.38U，已污染，禁用",
                    "S0/S1/S6/S7": "数据资格不合格或无数据",
                },
            },
            "最小设计": {
                "对象": "先做拒单足迹 9 币（1000BONK/ARPA/AR/CHR/BNB/COTI/APE/API3/BEL），"
                        "这些币同时具备 is/ 数据与已知候选锚点；确有必要再扩到 92 个可扩展有信号币",
                "锚点": "out/04_前缀锚点_v1.csv（已生成：审计日志候选时刻 + FULL_SWEEP 真实成交时刻）",
                "做法": ("对每个锚点时刻 T_p，跑一次 `--timerange 20211201-<T_p 前一根 15m>`（前缀）"
                         "与一次全期 `--timerange 20211201-20241231`（基准），"
                         "比较两者在 < T_p 区间内产生的候选集合"),
                "通过条件": "前缀运行的候选集合 = 基准运行同期候选集合（子集关系且无缺失）",
                "失败含义": "基准有、前缀无的候选 ⇒ 该候选的生成依赖 T_p 之后的数据 ⇒ 前瞻污染，必须停",
                "规模": "每币 (1 基准 + k 前缀)，k=2~3 即可覆盖早/中/晚三个锚点 ⇒ 9 币约 27~36 次回测",
                "配套": "预热校验：4h 需 220 根（200 预热 + 42 箱体），is/ 自 2021-10-01 起满足",
            },
            "预期证据": [
                "两次运行的 enter_tag / candidate_id 清单（同期）",
                "H（箱顶）与 L/A 在两次运行中逐候选一致",
                "若不一致，输出差异候选的 fill/H/planned_execution_time",
            ],
        },
        "方案B_成交价>=H拒单的真实引擎验证": {
            "问题": "成交价等于/高于 H 的拒单，怎样用真实引擎（而非替代报告）验证",
            "手法": "同币同格、只切基类的 A 路线约束分支，做 ON/OFF 差分；以 FT-ENTRY-04 运行期"
                    "自写 audit_orders_*.csv 为权威预期，交叉验证 freqtrade 逐笔 trades_*.csv",
            "最小设计": {
                "对象币": rej_coins,
                "格": "先取 1 格（建议 A01 = Wyckoff_A_L06_V08，深 Δ，拒单 5 个）；"
                      "如需覆盖三种 Δ，再取 A04/A07 ⇒ 共 3 格",
                "两组基类": {
                    "OFF（对照）": "evidence/FT-ENTRY-03/user_data/strategies（无 fill<H 约束）",
                    "ON（实验）": "evidence/FT-ENTRY-04/user_data/strategies（A 路线 fill_price>=frozen_H → REJECT）",
                },
                "命令": [
                    f"{os.path.relpath(FT_BIN, EV)} backtesting -c <cfg_off> --strategy <S> "
                    f"--strategy-path evidence/FT-ENTRY-03/user_data/strategies "
                    f"--timerange 20211201-20241231 --cache none --export trades",
                    f"{os.path.relpath(FT_BIN, EV)} backtesting -c <cfg_on> --strategy <S> "
                    f"--strategy-path evidence/FT-ENTRY-04/user_data/strategies "
                    f"--timerange 20211201-20241231 --cache none --export trades",
                ],
                "pairlist": "只放该格涉及的拒单币（≤9 个），不改其它任何参数",
            },
            "通过条件": [
                "OFF 组：该格在 9 币上出现 fill_price >= frozen_H 的成交，逐笔可与 FT-ENTRY-03 的 "
                "trades_*.csv 对上（enter_tag 主键）",
                "ON 组：上述成交全部消失，且消失集合恰等于 audit_orders_*.csv 中 reason 为 "
                "fill_price_ge_frozen_h_greater/equal 的去重候选集（A01 格为 5 个）",
                "ON 组放行笔数与 audit 的 PASSED 数一致，且逐笔 open_rate < frozen_H",
                "Baseline 不受影响（约束只作用于 A 路线）",
            ],
            "规模": "3 格 × 2 组 = 6 次回测（若只做 1 格则 2 次）",
            "前置": "需先用 FT-DATA-01 口径把 9 币的 v31 parquet 本地转成 freqtrade feather（本地转换，非下载）",
        },
        "方案C_PIT与OI检查（可选，最小）": {
            "问题": "候选所用 OI 在决策时刻是否真的可得（FRAME-PIT-01）",
            "做法": "对同一批候选，用 flow_oi/pit_features.py 的唯一受控实现复算 ΔOI_6h 与 "
                    "available_at，核对 桶起点+5m <= decision_time",
            "通过条件": "每个候选的 OI 输入满足该不等式；不满足者记 pit_qualified=False",
            "硬边界": "declared_lag 不升级资格；OI 无 fetched_at ⇒ 全池 1h OI 因子资格仍为 0，不得宣称 PIT 隔离",
            "规模": "纯本地复算，不需要跑引擎",
        },
        "不做的事": [
            "不跑 9 格 × 70 币的整套回测",
            "不扩展 C 路线",
            "不打开 2026 留出数据、不读取 WyckoffMaster/v3.0 含 Holdout 目录",
            "不据此宣布任何新因子/新策略有效",
        ],
    }
    jdump(os.path.join(OUT, "04_最小因果验证方案_v1.json"), plan)
    jdump(os.path.join(OUT, "04_可用输入盘点_v1.json"), inputs)

    # ── 证据缺口 ──
    gaps = [
        {"缺口": "freqtrade datadir 已空（FT-ENTRY-04 下 0 文件）",
         "影响": "方案 A/B 的真实引擎重跑前必须本地重建数据包",
         "现有缓解": "v3.1 is/val parquet 齐全（241/241 币）+ FT-DATA-01 转换口径与 13 项 PASS 检查",
         "是否阻断": "是（但不构成新增下载）"},
        {"缺口": "1h OI 因子正式资格全池为 0（OI 无 fetched_at，5m→1h 映射与发布时点未证）",
         "影响": "任何含 OI 门槛的候选都不具正式 PIT 资格；A/B 方案只能验机制，不能验 PIT 隔离",
         "现有缓解": "FRAME-PIT-02 的 pit_features.py 是唯一受控实现；strict 模式可复算",
         "是否阻断": "否（限定结论口径即可）"},
        {"缺口": "历史合约规格 0 verified（官方无历史规格归档）",
         "影响": "真实历史盈亏无法宣称已按当时规格修正",
         "现有缓解": "PRECISION-04 已把 6 个样本 SPECCHANGE 记为 unverified_historical",
         "是否阻断": "否（机制核对不受影响）"},
        {"缺口": "可扩展 171 币在时间维度上已被 FULL_SWEEP 扫描（241/241 币全接触）",
         "影响": "不能说这些币『从未用于研究』；只能说未进入 A 路线 70 白名单的正式 9 格研究",
         "现有缓解": "接触台账 out/02_接触台账_逐币_v1.csv 已逐币记录接触来源与深度",
         "是否阻断": "否（口径必须写清）"},
        {"缺口": "val 窗（2024-10-01~2026-04-14）已被 FULL_SWEEP 扫描（S4）",
         "影响": "val 不能当干净样本外；只可用于机制/前缀核对",
         "现有缓解": "out/02_接触台账_逐段_v1.csv 已标 S2~S5 为已接触",
         "是否阻断": "否"},
        {"缺口": "样本外空间为 0：旧 Holdout 已污染，S0/S1/S6/S7 数据不合格",
         "影响": "无法做无偏的样本外验证；只能做机制核对",
         "现有缓解": "OOS-READY-01 结论照录；新样本需前向采集约 18~24 个月",
         "是否阻断": "是（对样本外结论）"},
        {"缺口": "9 格实为 8 个独立实验（A07=A08 逐笔 SHA-256 相同）",
         "影响": "多重比较分母应按 8 计，不是 9",
         "现有缓解": "FT-ENTRY-AUDIT-05/06 已记录",
         "是否阻断": "否"},
        {"缺口": "可扩展有信号币仅 92 个，且集中在少数币（错过盈利由 BEL/BONK/CHR 主导）",
         "影响": "任何跨币统计都会被少数币主导",
         "现有缓解": "out/03_可扩展样本表_v1.csv 逐币列信号可用性",
         "是否阻断": "否（须在结论中声明）"},
        {"缺口": "ACCUM-STRAT-01（震荡吸筹）代码/配置/币池/逐笔本机全无",
         "影响": "该研究线不可复现，不能作为可扩展样本的证据来源",
         "现有缓解": "已标『仅剩汇总表，本机不可复现』",
         "是否阻断": "否（不引用）"},
    ]
    write_csv(pd.DataFrame(gaps), "04_证据缺口_v1.csv")

    print("== 最小因果验证方案（只交方案）==")
    print(f"  方案A 前缀锚点: {len(anc)} 个（审计日志候选 + FULL_SWEEP 真实成交）")
    print(f"   冻结研究期信号: L1 125 候选/100 成交；可扩展171 中 92 候选/71 成交")
    print(f"   val 窗信号: L1 107 候选/86 成交；可扩展171 中 76 候选/60 成交")
    print(f"  方案B 拒单足迹: {len(rej)} 个去重拒单 / {len(rej_cands)} 唯一候选 / {len(rej_coins)} 币")
    print(f"   币: {rej_coins}")
    print(f"   datadir 空: {datadir_empty} ⇒ 需本地重建数据包（非下载）")
    print(f"  证据缺口: {len(gaps)} 项（2 项阻断）")


if __name__ == "__main__":
    main()
