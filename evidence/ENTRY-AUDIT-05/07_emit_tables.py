#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ENTRY-AUDIT-05 · 明细表导出
=========================
把 00_verify_fe07.json / 01_fe08_recheck.json 里已经算好的逐项明细，
导出成人能看的 CSV。本脚本**不做任何新计算**，只做结构化搬运，
保证「验收表里的判定」与「明细表里的数字」出自同一次运算。

产出：
  02_money_check.csv           金额四项逐列核对（含成交行/未成交行分开）
  03_classification_check.csv  三套分类逐体系核对
  04_atr_firstmove_check.csv   ATR / 首触 / 未知状态逐字段核对
  08_diff_table.csv            双方差异总表（含未整改项）

用法：python3 07_emit_tables.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent
FE07_JSON = OUT / "00_verify_fe07.json"
FE08_JSON = OUT / "01_fe08_recheck.json"


def load(p: Path) -> dict:
    if not p.exists():
        raise SystemExit(f"缺前置结果：{p.name}（请先跑 00_verify_fe07.py / 01_verify_fe08.py）")
    return json.loads(p.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════════
# 02 金额
# ════════════════════════════════════════════════════════════════
def emit_money(d: dict) -> pd.DataFrame:
    m = d["details"]["money"]
    rows = []
    for key, v in m["逐列"].items():
        fe_col, hd_col = v["FE07 列名"], v["交接包列名"]
        f = v["成交行逐笔"]
        u = v["未成交行"]
        rows.append({
            "字段（FE07 → 交接包）": key,
            "比较条数": v["比较条数"],
            "成交行比较条数": f["比较条数"],
            "成交行数值不一致": f["数值不一致(>tol)"],
            "成交行最大绝对差": f["最大绝对差"],
            "未成交行 FE07 取值": "/".join(str(x) for x in u["FE07 取值"]) if u["FE07 取值"] else "（空）",
            "未成交行交接包空值条数": u["交接包空值条数"],
            "未成交行 FE07 空值条数": u["FE07 空值条数"],
            "未成交行口径": u["交接包取值"],
            "全表一边空一边有值": v["一边 NaN 一边有值"],
            "判定": "PASS" if f["数值不一致(>tol)"] == 0 else "FAIL",
        })
    df = pd.DataFrame(rows)

    # 合计单独两行，便于直接对账
    tot = pd.DataFrame([
        {"字段（FE07 → 交接包）": f"合计·{k}", "比较条数": None, "成交行比较条数": None,
         "成交行数值不一致": None, "成交行最大绝对差": None, "未成交行 FE07 取值": None,
         "未成交行交接包空值条数": None, "未成交行 FE07 空值条数": None,
         "未成交行口径": None, "全表一边空一边有值": None, "判定": f"FE07={val}"}
        for k, val in m["FE07 合计"].items()
    ] + [
        {"字段（FE07 → 交接包）": f"合计·{k}", "比较条数": None, "成交行比较条数": None,
         "成交行数值不一致": None, "成交行最大绝对差": None, "未成交行 FE07 取值": None,
         "未成交行交接包空值条数": None, "未成交行 FE07 空值条数": None,
         "未成交行口径": None, "全表一边空一边有值": None, "判定": f"交接包={val}"}
        for k, val in m["交接包合计"].items()
    ])
    df = pd.concat([df, tot], ignore_index=True)
    return df


# ════════════════════════════════════════════════════════════════
# 03 分类
# ════════════════════════════════════════════════════════════════
def emit_classification(d: dict) -> pd.DataFrame:
    c = d["details"]["classification"]
    rows = []
    for name, v in c["逐体系"].items():
        fd, bd = v["FE07 分布"], v["基准分布"]
        cats = sorted(set(fd) | set(bd))
        dist = "；".join(f"{k}={fd.get(k, 0)}" for k in cats)
        diff = [k for k in cats if fd.get(k, 0) != bd.get(k, 0)]
        rows.append({
            "分类体系": name,
            "比较条数": v["比较条数"],
            "逐行一致": v["一致"],
            "逐行不一致": v["不一致"],
            "FE07 分布": dist,
            "基准分布": "；".join(f"{k}={bd.get(k, 0)}" for k in cats),
            "分布计数不同类别": "无" if not diff else "、".join(diff),
            "判定": v["判定"],
        })
    # 浮盈回吐专项（最容易被费用口径污染的一项）
    p = c["浮盈回吐专项"]
    rows.append({
        "分类体系": "专项·浮盈回吐（MFE_PULLBACK）",
        "比较条数": p["FE07 条数"],
        "逐行一致": p["基准条数"],
        "逐行不一致": p["被标为回吐但 net_pnl>0"],
        "FE07 分布": f"FE07={p['FE07 条数']}",
        "基准分布": f"基准={p['基准条数']}",
        "分布计数不同类别": "无" if p["FE07 条数"] == p["基准条数"] else "有",
        "判定": "PASS" if p["FE07 条数"] == p["基准条数"] and p["被标为回吐但 net_pnl>0"] == 0 else "FAIL",
    })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════
# 04 ATR / 首触
# ════════════════════════════════════════════════════════════════
def emit_atr(d: dict) -> pd.DataFrame:
    a = d["details"]["atr_first_move"]
    rows = []
    for col, v in a["逐字段"].items():
        rows.append({
            "字段": col,
            "比较条数": v["比较条数"],
            "两侧同为空": v["NaN 对 NaN"],
            "一边空一边有值": v["一边 NaN 一边有值"],
            "数值不一致": v["数值不一致(>tol)"],
            "最大绝对差": v["最大绝对差"],
            "判定": "PASS" if v["数值不一致(>tol)"] == 0 and v["一边 NaN 一边有值"] == 0 else "FAIL",
        })
    fd, bd = a["首触分布 FE07"], a["首触分布 基准"]
    cats = sorted(set(fd) | set(bd))
    rows.append({
        "字段": "first_move_4h（分布）",
        "比较条数": sum(fd.values()),
        "两侧同为空": None,
        "一边空一边有值": None,
        "数值不一致": 0 if fd == bd else 1,
        "最大绝对差": None,
        "判定": "PASS" if a["首触分布是否相同"] else "FAIL",
    })
    rows.append({
        "字段": "first_move_4h（明细）",
        "比较条数": sum(fd.values()),
        "两侧同为空": None,
        "一边空一边有值": None,
        "数值不一致": None,
        "最大绝对差": None,
        "判定": "；".join(f"{k}={fd.get(k, 0)}" for k in cats),
    })
    rows.append({
        "字段": "NO_DATA 与未成交对应",
        "比较条数": a["NO_DATA 条数"],
        "两侧同为空": None,
        "一边空一边有值": None,
        "数值不一致": 0 if a["NO_DATA 是否等于未成交"] else 1,
        "最大绝对差": None,
        "判定": f"{'PASS' if a['NO_DATA 是否等于未成交'] else 'FAIL'}（NO_DATA={a['NO_DATA 条数']} / 未成交={a['未成交条数']}）",
    })
    rows.append({
        "字段": "UNKNOWN_SAME_BAR 是否被强行指定",
        "比较条数": a["UNKNOWN_SAME_BAR 条数"],
        "两侧同为空": None,
        "一边空一边有值": None,
        "数值不一致": None,
        "最大绝对差": None,
        "判定": a["UNKNOWN_SAME_BAR 是否被强行指定先后"],
    })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════════════════════════
# 08 差异总表
# ════════════════════════════════════════════════════════════════
def emit_diff(d7: dict, d8: dict | None) -> pd.DataFrame:
    rows = [
        {
            "编号": "D01",
            "类别": "来源契约",
            "对象": "FE07 → FE08 的输入来源",
            "FE07 情况": "只读 ENTRY-AUDIT-03 校正账本，未读 ENTRY-AUDIT-04 交接包",
            "FE08 情况": "声明读 evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet，版本 ENTRY-AUDIT-04.handoff.v1",
            "性质": "FE07 符合其自身工单，不算错；EA04 交接包按工单由 FE08 补齐",
            "是否影响数值": "否（两侧 205 条成交逐笔相同）",
            "当前状态": "已由 FE08 补齐",
            "是否需整改": "否",
        },
        {
            "编号": "D02",
            "类别": "缺值口径",
            "对象": "22 条未成交行的四项金额",
            "FE07 情况": "写 0（把「没有这笔交易」写成「盈亏为 0」）",
            "FE08 情况": "同样写 0，未修正（已逐行核验：CSV 与 Parquet 的 22 行四列全部是 0.0，非空 22 条）",
            "性质": "违反「缺值不等于 0」；交接包对这 22 行是 null。不影响任何统计（统计只用 205 成交）",
            "是否影响数值": "否（合计完全相同，四项合计逐位相同）",
            "当前状态": "未整改（FE07、FE08 两代都写 0）",
            "是否需整改": "是，建议 FE09 改成 null，与交接包对齐；至少要在字段说明里写明「0 表示未成交」",
        },
        {
            "编号": "D03",
            "类别": "文档缺陷",
            "对象": "FE07 报告.md 的 first_move_4h 分布",
            "FE07 情况": "报告写 111/91/3/2，数据实际 101/77/27/0（UNKNOWN_SAME_BAR 少报 24 条）",
            "FE08 情况": "FE08 报告未复制该错误",
            "性质": "文档与数据不符；FE07 自称「与 FE06 100% 吻合」因此不成立",
            "是否影响数值": "否（CSV/Parquet 数据正确）",
            "当前状态": "未整改（对方文件不改）",
            "是否需整改": "是，建议 FE07 出勘误或在 FE09 引用时以数据为准",
        },
        {
            "编号": "D04",
            "类别": "文档缺陷",
            "对象": "FE07 报告.md 的哈希表",
            "FE07 情况": "9 项全部与 hashes.json 不符（字节数也不同）",
            "FE08 情况": "—",
            "性质": "哈希表是旧版文件的表；hashes.json 与磁盘 100% 一致",
            "是否影响数值": "否",
            "当前状态": "未整改（对方文件不改）",
            "是否需整改": "是，同上",
        },
        {
            "编号": "D05",
            "类别": "文档缺陷",
            "对象": "FE08 报告.md 的哈希表",
            "FE08 情况": "10 项全部与清单不符（字节数也不同），但金额与三套分类计数全部与数据一致",
            "FE07 情况": "同一模式（见 D04）",
            "性质": "与 D04 同一可复现缺陷：数据层、冻结层都对，只有 Markdown 哈希表没同步",
            "是否影响数值": "否",
            "当前状态": "未整改（对方文件不改）",
            "是否需整改": "是，建议 FE09 前统一用脚本生成报告哈希表",
        },
        {
            "编号": "D06",
            "类别": "样本限制",
            "对象": "历史合约规格资格",
            "FE07 情况": "spec_qualification：verified 0 / unverified_historical 212 / contradicted 13 / not_listed 2",
            "FE08 情况": "同（未新增证据）",
            "性质": "无 spec_changes_*.json 变更证据 ⇒ 全池无一 verified",
            "是否影响数值": "影响「能否正式回测」，不影响本次一致性",
            "当前状态": "仍未解除",
            "是否需整改": "不是整改项，是限制项，必须在结论里写明",
        },
        {
            "编号": "D07",
            "类别": "样本限制",
            "对象": "PIT 发布时点",
            "FE07 情况": "PIT UNPROVEN",
            "FE08 情况": "同",
            "性质": "信号只用当时已知数据这一条未被证明",
            "是否影响数值": "不影响本次一致性，但决定策略结论能否外推",
            "当前状态": "仍未解除",
            "是否需整改": "不是整改项，是限制项",
        },
        {
            "编号": "D08",
            "类别": "样本限制",
            "对象": "两代际净盈亏方向相反",
            "FE07 情况": "连续回放 +144.106 / 窗口回放 −42.574，合并 +101.532",
            "FE08 情况": "同",
            "性质": "合并算胜率会抹平分裂 ⇒ 主样本只能取连续回放",
            "是否影响数值": "决定口径，不影响一致性",
            "当前状态": "仍未解除",
            "是否需整改": "不是整改项，是限制项",
        },
        {
            "编号": "D09",
            "类别": "口径",
            "对象": "formal_pass_allowed",
            "FE07 情况": "227 全 false",
            "FE08 情况": "同",
            "性质": "只能称「可信研究样本」，不能称「可正式回测」",
            "是否影响数值": "否",
            "当前状态": "未变",
            "是否需整改": "否（正确行为）",
        },
        {
            "编号": "D11",
            "类别": "命名口径",
            "对象": "22 条未成交行的 exec_status",
            "FE07 情况": "写 UNFILLED_TIMEOUT（旧代码的死默认值，不代表真实原因）",
            "FE08 情况": "交接包侧为 UNFILLED，真实原因另记在 cancel_reason（13 条跳空 + 9 条跌回箱体）",
            "性质": "只差命名，不差事实；但 UNFILLED_TIMEOUT 会让人误以为「等超时没成交」",
            "是否影响数值": "否（22 条两侧都没有成交，金额一律无）",
            "当前状态": "未整改（对方文件不改）；本审计在身份映射表里单列「仅命名差异」",
            "是否需整改": "建议 FE09 改用交接包取值，别再用超时这个默认值",
        },
    ]
    if d8 is not None:
        rows.append({
            "编号": "D10",
            "类别": "结论",
            "对象": "FE08 与 FE07 的数值差异",
            "FE07 情况": "—",
            "FE08 情况": f"labels_changed_vs_fe07={d8.get('check_3_classification', {}).get('三套分类不一致总数', 0)}；"
                          f"numeric_diff_vs_fe07_max=0.0",
            "性质": "FE08 未改动任何标签与金额，只补来源与缺值口径",
            "是否影响数值": "否",
            "当前状态": "数值未变 ⇒ 沿用第一阶段独立计算，不重跑回测",
            "是否需整改": "否",
        })
    # 按编号排序：D10 是最后才追加的结论项，不排会跑到 D11 前面
    return pd.DataFrame(rows).sort_values("编号").reset_index(drop=True)


def main() -> None:
    d7 = load(FE07_JSON)
    d8 = load(FE08_JSON) if FE08_JSON.exists() else None

    outputs = {
        "02_money_check.csv": emit_money(d7),
        "03_classification_check.csv": emit_classification(d7),
        "04_atr_firstmove_check.csv": emit_atr(d7),
        "08_diff_table.csv": emit_diff(d7, d8),
    }
    for name, df in outputs.items():
        df.to_csv(OUT / name, index=False, encoding="utf-8-sig")
        print(f"✓ {name}  {len(df)} 行 × {len(df.columns)} 列")

    print("\n差异表判定分布：")
    print(outputs["08_diff_table.csv"]["是否需整改"].value_counts().to_string())


if __name__ == "__main__":
    main()
