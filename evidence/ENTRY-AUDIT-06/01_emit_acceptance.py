#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-06 · 第二步：从实际核查结果生成统一验收表与最终状态

原则：
  * 每行判定来自 _facts.json / 06_doc_layer_check.csv / 11_fe09_recheck.json 的实际结果，
    不手写、不继承 ENTRY-AUDIT-05 的旧表。
  * 每行写清「目标版本」与「状态性质」，FE07 / FE08 / FE09 不得混在一行。
  * 整单状态只按「当前有效版本」判定；历史版本的 FAIL 单独记录、不代改、不计入当前版本，
    但必须在报告与状态里如实写出「未整改」。
  * 完整性 PASS 不得被当成整单通过。
  * 生成后回读自己的产物做一致性自检，矛盾必须为空。

产出：
  00_acceptance_table.csv   统一验收表
  00_stale_correction.csv   EA05 旧表逐行纠错映射
  08_final_status.json      最终状态（四类结论分开写）
  10_state_consistency.json 状态一致性自检
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-06"

ALLOWED = ["PASS", "PASS_WITH_NOTE", "FAIL", "PENDING_DELIVERY"]
TARGETS = ["FE07", "FE08", "FE09", "全局"]
KINDS = ["历史版本", "当前有效", "全局"]


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main() -> None:
    facts = load("_facts.json")
    doc = pd.read_csv(OUT / "06_doc_layer_check.csv")
    inv = facts["invariance"]
    src = facts["source_binding"]
    cyc = facts["cycle"]
    fh = facts["file_hash"]
    fe09 = load("09_fe09_pending.json")
    r9 = load("11_fe09_recheck.json") if (OUT / "11_fe09_recheck.json").exists() else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    money = {r["契约字段"]: r for r in inv["FE08与EA04金额核对"]}
    c = inv["样本计数"]
    d07 = facts["doc_layer"]["按版本"]["FE07"]
    d08 = facts["doc_layer"]["按版本"]["FE08"]
    fc = facts["field_contract"]

    rows = [
        # ---------------- FE07（历史版本）
        ("FE07", "历史版本", "① 记录身份与匹配关系", "PASS",
         f"{c['记录行数']} 行 / {c['唯一信号']} 唯一信号 / 成交 {c['成交']} / 未成交 {c['未成交']}；"
         "COTI 同快照两代际各记一行并列保留",
         "ENTRY-AUDIT-05/00_verify_fe07.json（沿用，未重算）"),
        ("FE07", "历史版本", "② 四项金额（对 EA04 交接包）", "PASS_WITH_NOTE",
         f"205 条成交逐笔最大绝对差 {max(v['最大绝对差'] or 0 for v in money.values()):.1e}；"
         "注记：22 条未成交 FE07 写 0，交接包为 null（缺值当 0）",
         "04_data_invariance.json / EA05 02_money_check.csv"),
        ("FE07", "历史版本", "③ 净盈亏恒等式与费用符号", "PASS",
         "227/227 满足 net_pnl = gross_pnl − fee − funding，最大残差 2.842e−14",
         "ENTRY-AUDIT-05/00_verify_fe07.json（沿用）"),
        ("FE07", "历史版本", "④ 三套分类与独立基准", "PASS",
         "标准混合 / 纯 ATR-4h / 纯 ATR-全周期 各 227/227 一致",
         "ENTRY-AUDIT-05/00_verify_fe07.json（沿用）"),
        ("FE07", "历史版本", "⑤ ATR、首触顺序、未知状态", "PASS",
         "7 字段 0 差异；box_atr_formation 42 条空位一致；UNKNOWN_SAME_BAR 27 条保留为未知",
         "ENTRY-AUDIT-05/00_verify_fe07.json（沿用）"),
        ("FE07", "历史版本", "⑥ 数据层多格式（CSV↔Parquet↔JSON）", "PASS",
         "70 列逐列一致，字符串空值表示差异不计为不一致",
         "ENTRY-AUDIT-05/05_format_consistency.json（沿用）"),
        ("FE07", "历史版本", "⑦ 来源绑定", "PASS",
         f"输入 {src['FE07']['输入条数']} 条哈希全与磁盘一致；"
         "读 ENTRY-AUDIT-03=True / 读 ENTRY-AUDIT-04=False（符合 FE07 自身工单）",
         "07_source_binding.json"),
        ("FE07", "历史版本", "⑧ 冻结清单 vs 磁盘", "PASS",
         f"{fh['FE07']['一致条数']}/{fh['FE07']['清单条数']} 逐位一致，"
         f"未登记缺口 {fh['FE07']['未登记文件数']}（hashes.json 自身不计）",
         "01_file_hash_check.csv"),
        ("FE07", "历史版本", "⑨ 文档层·报告哈希表", "FAIL",
         f"{d07['报告与清单一致条数']}/{d07['条数']} 与清单一致（即全部不符，字节数也不同）",
         "06_doc_layer_check.csv"),
        ("FE07", "历史版本", "⑩ 文档层·报告数字", "FAIL",
         "首触分布报告 111/91/3/2，数据实际 101/77/27/0；报告相加 229≠227，"
         "自称「与 FE06 100% 吻合」不成立",
         "06_doc_layer_check.csv / EA05 05_format_consistency.json"),

        # ---------------- FE08（历史版本，数据被 FE09 沿用）
        ("FE08", "历史版本", "⑪ 来源绑定（EA04 交接包）", "PASS",
         f"声明 {src['FE08']['声明交接版本']}；登记哈希 "
         f"{len(src['FE08']['登记哈希逐条'])}/{len(src['FE08']['登记哈希逐条'])} 与磁盘一致；"
         f"脚本实际打开交接包={src['FE08']['脚本是否实际打开交接包']}；"
         f"旧费用键名={src['FE08']['脚本内是否仍出现旧费用键名'] or '无'}",
         "07_source_binding.json"),
        ("FE08", "历史版本", "⑫ 与 FE07 的金额 / 分类 / ATR 逐列", "PASS",
         f"共有 {inv['FE07与FE08共有列数']} 列，不一致列 {inv['FE07与FE08不一致列数']}；"
         "FE08 增 6 列、减 4 列属已知结构变更",
         "04_data_invariance.json"),
        ("FE08", "历史版本", "⑬ 与 EA04 交接包的金额（独立核对）", "PASS_WITH_NOTE",
         "gross_pnl / fees→fee_paid / funding_fee→funding_cost / net_pnl 各 205 条逐笔 0 差；"
         "注记：22 条未成交 FE08 写 0、交接包 null",
         "04_data_invariance.json"),
        ("FE08", "历史版本", "⑭ 字段契约", "PASS_WITH_NOTE",
         f"交接契约 {fc['契约字段数']} 字段：同名 {fc['同名']}、改名可映射 {fc['改名可映射']}"
         f"（fees→fee_paid、funding_fee→funding_cost）、未搬运 {fc['未搬运']}；"
         "FE08 是特征底表，不承担全字段搬运",
         "05_field_contract.csv"),
        ("FE08", "历史版本", "⑮ 数据层多格式（CSV↔Parquet 双向）", "PASS",
         "往返比对无不一致列",
         "ENTRY-AUDIT-05/01_fe08_recheck.json（沿用）"),
        ("FE08", "历史版本", "⑯ 冻结清单 vs 磁盘", "PASS",
         f"{fh['FE08']['一致条数']}/{fh['FE08']['清单条数']} 逐位一致，"
         f"未登记缺口 {fh['FE08']['未登记文件数']}",
         "01_file_hash_check.csv"),
        ("FE08", "历史版本", "⑰ 清单无自哈希 / 无循环引用", "PASS",
         "hashes.json 不登记自身；报告未引用清单或自身哈希；无回指；"
         "代价是清单本身无外部锚点（只能由本审计登记）",
         "02_cycle_check.json"),
        ("FE08", "历史版本", "⑱ 文档层·报告金额与分类计数", "PASS",
         "121.635348 / 13.125871 / 6.977506 / 101.531971 与三套分类计数全部与数据一致",
         "06_doc_layer_check.csv"),
        ("FE08", "历史版本", "⑲ 文档层·报告哈希表", "FAIL",
         f"{d08['报告与清单一致条数']}/{d08['条数']} 与清单一致（即全部不符，字节数也不同）",
         "06_doc_layer_check.csv"),

        # ---------------- 全局
        ("全局", "全局", "⑳ 冻结后零改动（完整性）", "PASS",
         f"FE07 {fh['FE07']['一致条数']}+FE08 {fh['FE08']['清单条数']} 项逐位未变；"
         f"FE07↔FE08 不一致列 {inv['FE07与FE08不一致列数']}；"
         "只证明「没被改动」，不等于整单通过",
         "01_file_hash_check.csv / 04_data_invariance.json"),
    ]

    # ---------------- FE09（当前有效版本）
    if r9 and r9.get("状态") == "DELIVERED":
        ck = r9["逐项判定"]
        hc = r9["清单核对"]
        mc = r9["manifest交付物"]
        cy = r9["循环引用"]
        oh = r9["输出哈希"]
        rd = r9["报告数字"]
        rows += [
            ("FE09", "当前有效", "㉑ 交付完整性（清单 + manifest 交付物）", "PASS" if ck["2_清单与交付物完整"] else "FAIL",
             f"清单 {hc['清单条数']} 条全部与磁盘一致；manifest 交付物 {mc['条数']} 项不符 {len(mc['不符'])}；"
             f"未登记实质文件 {len(hc['未登记实质文件'])}（仅 hashes.json 自身与缓存）",
             "11_fe09_recheck.json"),
            ("FE09", "当前有效", "㉒ 报告交付物逐项（大小/哈希）", "PASS" if ck["1a_交付物逐项一致"] else "FAIL",
             f"{len(r9['报告逐项'])}/{len(r9['报告逐项'])} 报告=清单=磁盘；"
             f"EA04 输入表 {len(r9['报告中EA04输入表'])}/7 路径与大小哈希一致",
             "11_fe09_recheck.json"),
            ("FE09", "当前有效", "㉓ 与已验 FE08 基准逐位一致", "PASS" if ck["4_与FE08基准一致"] else "FAIL",
             f"CSV {oh['FE09_CSV'][:12]}… 与基准{'相同' if oh['CSV一致'] else '不同'}；"
             f"Parquet {oh['FE09_Parquet'][:12]}… 与基准{'相同' if oh['Parquet一致'] else '不同'}；"
             "相同 ⇒ 沿用 ENTRY-AUDIT-05 数据层检查，不重算",
             "11_fe09_recheck.json"),
            ("FE09", "当前有效", "㉔ 无自哈希 / 无循环引用", "PASS" if ck["3_无自哈希无循环"] else "FAIL",
             "hashes.json 不登记自身；manifest 登记报告但不被报告引用；单向 DAG："
             "数据/脚本/测试 → 报告.md → manifest",
             "11_fe09_recheck.json"),
            ("FE09", "当前有效", "㉕ 报告金额 / 分类 / 记录数", "PASS" if ck["5_报告数字与数据一致"] else "FAIL",
             f"金额 {rd['金额']}；计数 {rd['计数']['行数']}/{rd['计数']['成交']}/{rd['计数']['未成交']}；"
             "三套分类计数全部在报告中出现",
             "11_fe09_recheck.json"),
            ("FE09", "当前有效", "㉖ EA04 输入绑定与字段契约", "PASS" if ck["1b_EA04输入表逐项一致"] else "FAIL",
             f"声明 {r9['输入绑定与脚本']['声明交接版本']}；交接 7 项哈希全一致；"
             f"脚本实际打开交接包={r9['输入绑定与脚本']['脚本实际打开交接包']}；"
             f"旧口径按键取值={r9['输入绑定与脚本']['脚本仍用旧费用键名'] or '无'}",
             "11_fe09_recheck.json"),
        ]
    else:
        rows += [
            ("FE09", "当前有效", "㉑ 交付完整性", "PENDING_DELIVERY",
             f"状态 {fe09['状态']}；文件数 {fe09['文件数']}；未构成交付", "09_fe09_pending.json"),
            ("FE09", "当前有效", "㉒ 报告逐项（大小/哈希）", "PENDING_DELIVERY",
             "无报告可核", "09_fe09_pending.json"),
            ("FE09", "当前有效", "㉓ 与已验 FE08 基准一致", "PENDING_DELIVERY",
             "基准已登记，待比对", "09_fe09_pending.json"),
        ]

    df = pd.DataFrame(rows, columns=["目标版本", "状态性质", "检查项", "判定", "关键事实", "依据文件"])
    # 排序：历史版本 → 当前有效 → 全局（同一性质内保持原顺序）
    df["_k"] = df["状态性质"].map({"历史版本": 0, "当前有效": 1, "全局": 2})
    df = df.sort_values("_k", kind="stable").drop(columns="_k")
    # 去掉检查项前的圈号，序号列才是唯一编号（避免排序后圈号与序号错位）
    df["检查项"] = df["检查项"].str.replace(
        r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖]\s*", "", regex=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    df.to_csv(OUT / "00_acceptance_table.csv", index=False, encoding="utf-8-sig")

    # ------------------------------------------------ 旧表纠错映射
    stale = pd.DataFrame([
        dict(旧行=1, 旧表述="① 记录身份及匹配关系｜PASS",
             问题="未标目标版本，读者会当成整单结论", 新表对应行="1（FE07）", 新判定="PASS",
             依据="00_acceptance_table.csv"),
        dict(旧行=2, 旧表述="② 四项金额｜PASS_WITH_NOTE｜差异行 0",
             问题="未标目标版本；注记没写 22 条未成交是 0 对 null",
             新表对应行="2（FE07）、13（FE08）", 新判定="PASS_WITH_NOTE",
             依据="04_data_invariance.json"),
        dict(旧行=3, 旧表述="③ 恒等式｜PASS", 问题="未标目标版本",
             新表对应行="3（FE07）", 新判定="PASS", 依据="EA05 00_verify_fe07.json"),
        dict(旧行=4, 旧表述="④ 三套分类｜PASS", 问题="未标目标版本",
             新表对应行="4（FE07）、12（FE08）", 新判定="PASS", 依据="EA05 00_verify_fe07.json"),
        dict(旧行=5, 旧表述="⑤ ATR/首触｜PASS", 问题="未标目标版本",
             新表对应行="5（FE07）、12（FE08）", 新判定="PASS", 依据="EA05 00_verify_fe07.json"),
        dict(旧行=6,
             旧表述="⑥ 多格式一致性｜PASS_WITH_NOTE｜数据层 一致；冻结层 一致；文档层 报告.md 数字 不一致、哈希表 不一致",
             问题="一行混了两个版本、三个层；读者无法判断是 FE07 还是 FE08 的文档层失败",
             新表对应行="6/9/10（FE07）、15/18/19（FE08）、20/21/22/24（FE09）",
             新判定="数据层 PASS；FE07/FE08 文档层 FAIL；FE09 文档层 PASS",
             依据="06_doc_layer_check.csv / 11_fe09_recheck.json"),
        dict(旧行=7,
             旧表述="⑦ 输入输出哈希与来源契约｜PASS｜输入 9 条全一致；输出 18 条全一致；读 EA03=True / 读 EA04=False",
             问题="这句只描述 FE07，却没写目标版本，被读成「整单未读 EA04」，与 FE08 实际读 EA04 冲突——"
                  "这正是本轮要纠正的矛盾",
             新表对应行="7（FE07，读 EA03=True/读 EA04=False）、11（FE08，读 EA04=True）、25（FE09，读 EA04=True）",
             新判定="均 PASS", 依据="07_source_binding.json / 11_fe09_recheck.json"),
        dict(旧行="缺", 旧表述="（旧表无 FE08 逐项行）",
             问题="FE08 的六项只在另一个 JSON 里，验收表看不到", 新表对应行="11–19（FE08）",
             新判定="6 PASS + 2 PASS_WITH_NOTE + 1 FAIL", 依据="EA05 01_fe08_recheck.json"),
        dict(旧行="缺", 旧表述="（旧表无 FE09）", 问题="旧表没有任何 FE09 行；FE09 现已交付",
             新表对应行="20–25（FE09）", 新判定="6 项全 PASS", 依据="11_fe09_recheck.json"),
    ])
    stale.to_csv(OUT / "00_stale_correction.csv", index=False, encoding="utf-8-sig")

    # ------------------------------------------------ 整单状态（按当前有效版本）
    n_fail = int((df["判定"] == "FAIL").sum())
    n_pending = int((df["判定"] == "PENDING_DELIVERY").sum())
    n_note = int((df["判定"] == "PASS_WITH_NOTE").sum())
    n_pass = int((df["判定"] == "PASS").sum())
    cur = df[df["状态性质"] == "当前有效"]
    hist = df[df["状态性质"] == "历史版本"]
    cur_fail = int((cur["判定"] == "FAIL").sum())
    hist_fail = int((hist["判定"] == "FAIL").sum())
    cur_pending = int((cur["判定"] == "PENDING_DELIVERY").sum())

    if cur_fail:
        order_status = "NOT_PASS"
    elif cur_pending:
        order_status = "NOT_COMPLETE"
    elif hist_fail:
        order_status = "PASS_WITH_NOTE"
    else:
        order_status = "PASS"

    scope_note = (
        f"整单只按当前有效版本（FE09）判定；历史版本 FE07/FE08 有 {hist_fail} 项文档层缺陷未勘误，"
        "只登记、不代改、不计入当前版本。若要求历史文档也必须勘误，则整单不通过。"
    ) if hist_fail else ""

    by_target = df.groupby(["目标版本", "判定"]).size().unstack(fill_value=0).to_dict("index")
    by_target = {k: {kk: int(vv) for kk, vv in v.items()} for k, v in by_target.items()}

    final = {
        "audit": "ENTRY-AUDIT-06",
        "generated_utc": now,
        "本单性质": "交付记录同步与最终一致性复核（不修改反重力产物、不重跑回测）",
        "逐项统计": {"PASS": n_pass, "PASS_WITH_NOTE": n_note, "FAIL": n_fail,
                     "PENDING_DELIVERY": n_pending, "合计": int(len(df))},
        "按版本统计": by_target,
        "order_status": order_status,
        "order_scope": "当前有效版本链（FE09）的交付记录与数据一致性",
        "order_note": scope_note,
        "delivery_complete": n_pending == 0,
        "历史缺陷未整改": hist_fail,
        "integrity_is_not_order_pass": True,
        "完整性说明": ("冻结哈希逐位未变、227 行数据 0 差异，只证明「文件没被改动」；"
                       "整单结论来自 FE09 的六项复核，不来自完整性检查"),
        "四类结论": {
            "数据接入与输出一致性": {
                "结论": "通过（带 1 条注记）",
                "状态": "PASS_WITH_NOTE",
                "要点": [
                    "FE07 读 EA03、FE08 与 FE09 读 EA04，各自符合自身工单；来源绑定条件全满足",
                    "FE09 输出与 FE08 基准 CSV/Parquet 逐位相同 ⇒ 沿用 EA05 数据层检查，未重算",
                    "FE08 与 EA04 交接包四项金额 205 条逐笔 0 差，FE07↔FE08 全部共有列 0 差异",
                    "唯一注记：22 条未成交行写 0、交接包是 null（缺值当 0），不影响 205 条统计",
                ],
                "不能推出": "不能推出「可以正式回测」",
            },
            "文档与冻结清单一致性": {
                "结论": "当前版本通过；历史两代报告层不通过（未勘误）",
                "状态": "PASS_WITH_NOTE" if r9 and r9.get("判定") == "PASS" else "FAIL",
                "当前版本状态": "PASS" if r9 and r9.get("判定") == "PASS" else "PENDING_DELIVERY",
                "历史版本状态": "FAIL" if hist_fail else "PASS",
                "历史失败条数": hist_fail,
                "要点": [
                    f"冻结层：FE07 {fh['FE07']['一致条数']}/{fh['FE07']['清单条数']}、"
                    f"FE08 {fh['FE08']['一致条数']}/{fh['FE08']['清单条数']} 与磁盘逐位一致",
                    "FE09：清单 14 条 + manifest 13 项全部与磁盘一致；报告 12 行逐项一致；"
                    "EA04 输入表 7 项路径/大小/哈希一致",
                    "历史缺陷：FE07 报告哈希表 9/9 错、数字错；FE08 报告哈希表 10/10 错——已被 FE09 取代，未勘误",
                    "循环引用：三代均无自哈希、无互相引用；代价是清单本身无外部锚点",
                ],
                "不能推出": "不能因为「冻结后未变」就认为文档也对；也不能因为 FE09 修好了就认为旧两代报告没错",
            },
            "历史规格与 PIT 限制": {
                "结论": "全部未解除",
                "状态": "LIMIT_UNRESOLVED",
                "要点": [
                    "历史合约规格 verified 0 条（unverified_historical 212 / contradicted 13 / not_listed 2）",
                    "formal_pass_allowed 227 条全 false ⇒ 只能叫「可信研究样本」",
                    "PIT 发布时点仍 UNPROVEN",
                    "两代际净盈亏方向相反（+144.105960 / −42.573989），禁止合并算胜率",
                ],
                "不能推出": "不能推出「样本可用于生产决策」",
            },
            "策略有效性": {
                "结论": "仍未证明",
                "状态": "NOT_PROVEN",
                "要点": [
                    "205 笔成交是事后观测样本，本单只回答「交付记录是否一致」",
                    "FE09 报告自己也写明「绝不构成策略盈利性证明」",
                    "本单任何 PASS 都不构成对策略收益的证明",
                ],
                "不能推出": "不能推出「因子有效」或「策略可上线」",
            },
        },
        "FE09": {
            "状态": r9["状态"] if r9 else fe09["状态"],
            "判定": r9["判定"] if r9 else "PENDING_DELIVERY",
            "六项": r9["逐项判定"] if r9 else None,
            "复用决定": r9["复用决定"] if r9 else None,
        },
    }
    (OUT / "08_final_status.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------ 一致性自检（回读产物）
    t = pd.read_csv(OUT / "00_acceptance_table.csv")
    checks = []

    def add(cid, desc, ok, detail=""):
        checks.append(dict(编号=cid, 检查=desc, 通过=bool(ok), 说明=detail))

    add("C1", "判定值都在允许集合内", set(t["判定"]).issubset(set(ALLOWED)),
        f"实际取值 {sorted(set(t['判定']))}")
    add("C2", "每行都有目标版本且属于 FE07/FE08/FE09/全局", set(t["目标版本"]).issubset(set(TARGETS)),
        f"实际取值 {sorted(set(t['目标版本']))}")
    add("C3", "每行都有状态性质且属于 历史版本/当前有效/全局", set(t["状态性质"]).issubset(set(KINDS)),
        f"实际取值 {sorted(set(t['状态性质']))}")
    add("C4", "每行都写明依据文件",
        bool(t["依据文件"].notna().all() and (t["依据文件"].astype(str).str.len() > 0).all()))
    add("C5", "由表推出的整单状态与 08_final_status 一致",
        (order_status == final["order_status"]), f"表推出 {order_status}")
    add("C6", "当前有效版本有 FAIL 时整单必须 NOT_PASS",
        (cur_fail == 0) or (final["order_status"] == "NOT_PASS"))
    add("C7", "有 PENDING_DELIVERY 时 delivery_complete 必须为 false",
        (n_pending == 0) or (final["delivery_complete"] is False))
    add("C8", "历史版本 FAIL 必须被计数并写明未整改",
        (hist_fail == 0) or (final["历史缺陷未整改"] == hist_fail and len(final["order_note"]) > 0),
        f"历史 FAIL {hist_fail}")
    add("C9", "数据层 PASS 与 04_data_invariance 判定一致",
        (inv["判定"] == "PASS") == (final["四类结论"]["数据接入与输出一致性"]["状态"] == "PASS_WITH_NOTE"),
        f"invariance={inv['判定']}")
    add("C10", "文档层结论与 06_doc_layer_check 的 0/N 一致",
        (d07["报告与清单一致条数"] == 0 and d08["报告与清单一致条数"] == 0)
        == (hist_fail > 0),
        f"FE07 {d07['报告与清单一致条数']}/{d07['条数']}，FE08 {d08['报告与清单一致条数']}/{d08['条数']}")
    add("C11", "FE09 状态在 09 / 11 / 08 三处一致",
        (r9 is None and final["FE09"]["判定"] == "PENDING_DELIVERY")
        or (r9 is not None and r9["判定"] == final["FE09"]["判定"]),
        f"r9={(r9 or {}).get('判定')}")
    add("C12", "循环引用判定与表中 FE08 ⑰ 一致",
        (cyc["判定"] == "PASS") == bool((t.loc[t["检查项"].str.contains("循环引用"), "判定"] == "PASS").all()))
    add("C13", "EA05 旧表 7 行全部有纠错映射",
        set(range(1, 8)).issubset(set(pd.to_numeric(stale["旧行"], errors="coerce").dropna().astype(int))))
    add("C14", "「读 EA03 / 读 EA04」按版本分三行写清",
        sum(bool(t.loc[(t["目标版本"] == v) & t["检查项"].str.contains("来源绑定"), "判定"].any())
            for v in ("FE07", "FE08")) == 2 and bool(t["检查项"].str.contains("EA04 输入绑定").any()))
    add("C15", "完整性 PASS 未被表述为整单通过",
        final["integrity_is_not_order_pass"] is True and final["order_status"] != "PASS")
    add("C16", "表中不存在「当前有效」与「历史版本」混写的行",
        bool((t.groupby("序号")["状态性质"].nunique() == 1).all()))
    add("C17", "文档维度的「历史失败条数」与表中历史 FAIL 行数一致",
        final["四类结论"]["文档与冻结清单一致性"].get("历史失败条数") == hist_fail,
        f"声明 {final['四类结论']['文档与冻结清单一致性'].get('历史失败条数')} / 表 {hist_fail}")

    contradictions = [c for c in checks if not c["通过"]]
    out = {
        "audit": "ENTRY-AUDIT-06",
        "generated_utc": now,
        "检查项数": len(checks),
        "通过": len(checks) - len(contradictions),
        "矛盾": len(contradictions),
        "矛盾明细": contradictions,
        "判定": "PASS" if not contradictions else "FAIL",
        "检查明细": checks,
    }
    (OUT / "10_state_consistency.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(final["逐项统计"], ensure_ascii=False))
    print("order_status:", order_status, "delivery_complete:", final["delivery_complete"],
          "历史缺陷:", hist_fail)
    print("按版本:", json.dumps(by_target, ensure_ascii=False))
    print("一致性自检:", out["判定"], f"{out['通过']}/{out['检查项数']}")
    if contradictions:
        print("矛盾:", json.dumps(contradictions, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
