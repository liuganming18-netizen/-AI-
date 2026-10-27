# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-04 / 03 · 签收单 + 已知差异表

工单交付清单里的两项，FE07 未到也必须先有实体：
  - 签收版本核对：本窗口交付了哪个版本、路径、哈希，反重力签收时要回执什么
  - 差异表：截至现在**已知**的差异与待核项（不是 FE07 的差异，FE07 未到）

产出：
  handoff/签收单.md
  03_diff_table.csv
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-04"
HANDOFF = OUT / "handoff"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    meta = json.loads((HANDOFF / "handoff_meta.json").read_text(encoding="utf-8"))
    paths = json.loads((HANDOFF / "paths_for_fe07.json").read_text(encoding="utf-8"))
    split = json.loads((HANDOFF / "generation_split.json").read_text(encoding="utf-8"))
    cls = json.loads((OUT / "01_baseline_classification.json").read_text(encoding="utf-8"))
    hand = pd.read_parquet(HANDOFF / "ledger_handoff.parquet")

    # ── 签收单 ──
    a = split["generation_cand_ev"]
    b = split["generation_old_cand"]
    lines = [
        "# ENTRY-AUDIT-04 交接签收单",
        "",
        f"**交接版本**：`{meta['handoff_version']}`",
        f"**生成时刻**：{meta['generated_utc']}（UTC）",
        f"**交付方**：ENTRY-AUDIT-04 窗口",
        f"**接收方**：反重力 FACTOR-ENTRY-07",
        "",
        "## 一、必读文件（按此顺序）",
        "",
        "| 顺序 | 路径 | sha256 | 字节 |",
        "| :-- | :--- | :--- | ---: |",
    ]
    order = ["handoff/handoff_meta.json", "handoff/generation_split.json",
             "handoff/ledger_handoff.parquet", "handoff/ledger_handoff.csv",
             "handoff/signal_index.csv", "handoff/unfilled_22.csv",
             "handoff/coti_duplicate.csv", "handoff/field_dictionary.csv"]
    fmap = {f["path"].replace("evidence/ENTRY-AUDIT-04/", ""): f for f in paths["files"]}
    for i, rel in enumerate(order, 1):
        f = fmap.get(rel)
        if f:
            lines.append(f"| {i} | `{f['path']}` | `{f['sha256']}` | {f['bytes']} |")
    lines += [
        "",
        "## 二、签收时必须回执的四项",
        "",
        "1. **你读的是哪个文件**：给出完整路径 + sha256，必须与上表逐位相同。",
        "2. **你按什么键关联**：必须是 `(run_id, order_id)` 复合键。单用 `order_id` 会跨代际串账"
        "（同一候选在两代际生成相同 `order_id`，213 条成交里 6 组重复 13 行）。",
        "3. **你用的净盈亏公式**：`net_pnl = gross_pnl - fees - funding_fee`；"
        "`fees` / `funding_fee` 是引擎原字段名，**不是**上游的 `total_fees` / `total_funding`。",
        "4. **你的样本分母**：227 行 / 226 唯一信号 / 205 成交 / 22 未成交；"
        "连续回放 163 与窗口回放 42 **必须分开报**，不得相加当同一样本算胜率。",
        "",
        "## 三、样本空间（签收即确认这些数字）",
        "",
        "| 项 | 数值 |",
        "| :--- | ---: |",
        f"| 行数 | {meta['row_semantics']['rows']} |",
        f"| 唯一信号（`signal_key`） | {meta['row_semantics']['unique_signal_key']} |",
        f"| 成交 | {meta['row_semantics']['filled']} |",
        f"| 未成交 | {meta['row_semantics']['unfilled']} |",
        f"| 连续回放成交 / 净盈亏 | {a['filled']} / {a['sum_net_pnl']} |",
        f"| 窗口回放成交 / 净盈亏 | {b['filled']} / {b['sum_net_pnl']} |",
        f"| 合计净盈亏（仅供对账，不是策略指标） | {split['combined_reference_only']['sum_net_pnl']} |",
        "",
        "## 四、三态标记（签收即确认不得改口径）",
        "",
        "| 维度 | 取值 |",
        "| :--- | :--- |",
        f"| 合约规格资格 | {json.dumps(meta['three_state_flags']['spec_qualification']['values'], ensure_ascii=False)}（`verified` 0 条） |",
        f"| 正式口径放行 | {json.dumps(meta['three_state_flags']['formal_pass_allowed']['values'], ensure_ascii=False)} |",
        f"| 执行有效性 | {json.dumps(meta['three_state_flags']['exec_validity']['values'], ensure_ascii=False)} |",
        f"| 研究可用性 | {json.dumps(meta['three_state_flags']['research_usability']['values'], ensure_ascii=False)} |",
        f"| PIT | {meta['pit']['status']} |",
        "",
        "## 五、第二阶段基准（签收即确认这是比对基准）",
        "",
        f"- 基准文件：`evidence/ENTRY-AUDIT-04/01_baseline_features.parquet`"
        f"（sha256 `{sha256(OUT / '01_baseline_features.parquet')}`）",
        f"- 标准混合体系分布：{json.dumps(cls['independent_classification']['category_standard_mixed'], ensure_ascii=False)}",
        f"- 纯 ATR-4h 体系分布：{json.dumps(cls['independent_classification']['category_pure_atr_4h'], ensure_ascii=False)}",
        "- 该基准是按 FE06 契约**独立实现**的，与 FE06 自报标签 227/227 一致。",
        "",
        "## 六、两个已知坑（签收即确认已读）",
        "",
        "1. **`first_move_4h` 有 22 条 `NO_DATA`**，正好等于 22 条未成交。"
        "FE06 契约只规定已成交的四选一，没规定未成交怎么标 ⇒ 这是契约空档，不是错误。"
        "出现 `NO_DATA` 不判 FAIL，但条数必须等于未成交数。",
        "2. **`box_atr_formation` 有 42 条空值**，全部落在窗口回放代际。"
        "这是**有据的缺失不是 0**；填 0 即判「把缺值当 0」。",
        "",
        "## 七、反重力不需要做的事",
        "",
        "- 不需要重跑回测：本交接包的金额全部来自 ENTRY-AUDIT-03 的引擎重放原值，已逐位对账。",
        "- 不需要修 ENTRY-AUDIT-03 的任何文件：它们已冻结（51 个上游/代码/合约零改动、"
        "16 个反重力自冻结产物零改动）。",
        "",
        f"**签收单生成时刻**：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
    ]
    (HANDOFF / "签收单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── 已知差异表 ──
    dup = meta["identity_key_rules"]["collision_stats"]
    fm = cls["fee_independent_fields_frozen"]
    rows = [
        {
            "序号": 1, "类别": "身份键",
            "事项": "order_id 跨代际撞号（同一候选在两代际生成相同 order_id）",
            "证据": (f"引擎输出层 {dup['trade_records']} 条成交里 {dup['dup_signal_groups']} 组重复 "
                     f"（{dup['dup_rows']} 行）；映射到 205 候选后重复 0 行"),
            "影响": "按 order_id 单键关联会取到另一代际那笔，qty 差 0.4%~2.5%",
            "处置": "已改为 (run_id, order_id) 复合键；下游必须同改",
            "状态": "已修复（本交接包）",
        },
        {
            "序号": 2, "类别": "契约缺口",
            "事项": "first_move_4h 出现契约外取值 NO_DATA",
            "证据": f"NO_DATA {fm['first_move_enum_check']['no_data_count']} 条 / "
                    f"未成交 {fm['first_move_enum_check']['unfilled_count']} 条，"
                    f"相等={fm['first_move_enum_check']['no_data_equals_unfilled']}",
            "影响": "若判 FE07 时按四选一枚举硬校验，会误报 22 条违规",
            "处置": "判定规则改为「NO_DATA 不判 FAIL，但条数须等于未成交数」",
            "状态": "已写明（契约待补）",
        },
        {
            "序号": 3, "类别": "已知缺失",
            "事项": "box_atr_formation 42 条空值",
            "证据": f"总缺失 {fm['atr_missing_is_known']['box_atr_formation_missing']} 条，"
                    f"按代际 {fm['atr_missing_is_known']['box_atr_missing_by_generation']}",
            "影响": "这 42 条的箱体几何本身是坏的（box_high/box_low 实为入场价与止损价）",
            "处置": "保留空值，不补 0；FE07 若填 0 即判缺值当 0",
            "状态": "已冻结",
        },
        {
            "序号": 4, "类别": "规格资格",
            "事项": "verified 0 条，formal_pass_allowed 全 false",
            "证据": f"{json.dumps(meta['three_state_flags']['spec_qualification']['values'], ensure_ascii=False)}",
            "影响": "只能叫「可信研究样本」，不能叫「可正式回测」",
            "处置": "需补 spec_changes_*.json 变更证据才能升 verified",
            "状态": "未解决（需外部证据）",
        },
        {
            "序号": 5, "类别": "PIT",
            "事项": "PIT 状态 UNPROVEN",
            "证据": meta["pit"]["limitation"][:120] + "…",
            "影响": "无法证明决策时刻就能拿到这些数据",
            "处置": "需落盘决策时刻的真实快照才可证",
            "状态": "未解决（需改造采集）",
        },
        {
            "序号": 6, "类别": "资金费",
            "事项": f"资金费缺字段 {meta['funding_gaps']['total']} 次（205 候选口径；213 条全量口径 14 次）",
            "证据": meta["funding_gaps"]["reason"],
            "影响": "这些结算没有计入费用，方向未知",
            "处置": "记 gap 不补 0",
            "状态": "已冻结",
        },
        {
            "序号": 7, "类别": "代际分裂",
            "事项": "两代际净盈亏方向相反",
            "证据": f"连续 {a['sum_net_pnl']} / 窗口 {b['sum_net_pnl']}",
            "影响": "合并算胜率会抹平分裂",
            "处置": "分开报，主样本只能是连续回放",
            "状态": "已冻结（禁拼接）",
        },
        {
            "序号": 8, "类别": "待交付",
            "事项": "FE07 未交付，第二阶段七项复核未执行",
            "证据": "evidence/ 下只有 FACTOR-ENTRY-01~06",
            "影响": "本工单不能报整体完成",
            "处置": "FE07 到达后跑 02_fe07_recheck.py",
            "状态": "PENDING_DELIVERY",
        },
    ]
    pd.DataFrame(rows).to_csv(OUT / "03_diff_table.csv", index=False, encoding="utf-8-sig")

    # 签收单加入哈希清单
    lines = []
    for f in sorted(HANDOFF.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS.txt":
            lines.append(f"{sha256(f)}  handoff/{f.name}")
    (HANDOFF / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"签收单 → {HANDOFF / '签收单.md'}")
    print(f"差异表 → {OUT / '03_diff_table.csv'}（{len(rows)} 项）")


if __name__ == "__main__":
    main()
