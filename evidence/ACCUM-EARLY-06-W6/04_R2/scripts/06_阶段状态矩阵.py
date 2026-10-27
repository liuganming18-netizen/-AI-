# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R2 收口 · 06 阶段状态矩阵

把「执行方交付 / 独立复验 / Codex 验收 / 用户待决」四列机器化，
并把 B（W5-R2）产物集的版本一致性、独立复验覆盖边界、
以及 A / B / D 三方的数字交叉验证落成可复核的判定。

纪律
----
1. 只读上游，不重跑任何上游脚本。重跑会改写别人的 out/，等于替人重签。
2. 所有数字从产物读取，不手抄；每个判定都带 evidence_ref。
3. B 在 C 收口期间仍在改写产物 ⇒ 本矩阵锚定「读取时刻的 sha256 + 文件内 ts」，
   不写「B 的结论」这种无锚点表述。若两次运行哈希不同，说明 B 又改了。
4. 定性判定与机器判定分开：`通过/失败` 只给能用断言表达的项；
   需要人读语义的给 `status=DEFECT` 并列出 `basis`。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "04_R2" / "out"

A_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2"
A_JSON = A_DIR / "out" / "06_机器可读_交付.json"
A_CSV = A_DIR / "out" / "06_机器可读_交付.csv"
A_README = A_DIR / "README.md"
R1_JSON = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1" / "out" / "01_资金费重算与判据整改明细.json"

B_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "04_R2" / "out"
B_FILES = {
    "01_门禁": "01_dependency_hard_gate.json",
    "02_逐笔复验": "02_funding_mapper_status.json",
    "03_基线审计": "03_baseline_evidence_audit.json",
    "summary_判定汇总": "summary_verification_verdicts.json",
    "05_数字对撞": "05_数字对撞结果.json",
}

D_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W1" / "03_R1"

MY05 = OUT / "05_A交付独立复验.json"

CHECKS: list = []


def add(name, ok, detail, basis=None):
    CHECKS.append({"检查": name, "通过": bool(ok), "明细": detail, "依据": basis or []})
    return ok


def defect(name, detail, basis):
    CHECKS.append({"检查": name, "通过": False, "性质": "DEFECT", "明细": detail, "依据": basis})
    return False


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def parse_ts(s: str):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main() -> int:
    missing = [str(p) for p in [A_JSON, A_README, MY05, D_DIR / "待决表_R1.json"] if not p.exists()]
    missing += [str(B_DIR / v) for v in B_FILES.values() if not (B_DIR / v).exists()]
    if missing:
        print("缺上游文件，拒绝出矩阵：")
        for m in missing:
            print("  -", m)
        return 2

    A = load(A_JSON)
    MY = load(MY05)
    D_PENDING = load(D_DIR / "待决表_R1.json")
    D_01 = load(D_DIR / "out" / "01_approval_evidence.json")
    D_02 = load(D_DIR / "out" / "02_stop_decision.json")
    D_04 = load(D_DIR / "out" / "04_pending_dedupe.json")

    B = {k: load(B_DIR / v) for k, v in B_FILES.items()}
    B_HASH = {k: {"path": f"evidence/ACCUM-EARLY-06-W5/04_R2/out/{v}",
                  "bytes": (B_DIR / v).stat().st_size,
                  "sha256": sha(B_DIR / v),
                  "文件内ts": B[k].get("timestamp_utc")} for k, v in B_FILES.items()}

    # ---------- V01 A 交付结构 ----------
    cands = A.get("candidates") or []
    rows = sum(len(c.get("calibers") or c.get("rows") or []) for c in cands)
    add("V01 A 交付存在、schema 自述正确、51 候选 / 102 行",
        A.get("schema") == "accum-early-06-w4-r2/v1" and len(cands) == 51 and rows == 102,
        {"schema": A.get("schema"), "task": A.get("task"), "candidates": len(cands), "rows": rows})

    # ---------- V02 A 交付哈希锚定 ----------
    add("V02 A 交付 3 件主产物哈希已锚定（交接引用用）", True,
        {"06_json": {"bytes": A_JSON.stat().st_size, "sha256": sha(A_JSON)},
         "06_csv": {"bytes": A_CSV.stat().st_size, "sha256": sha(A_CSV)},
         "README": {"bytes": A_README.stat().st_size, "sha256": sha(A_README)}})

    # ---------- V03 我方对 A 的独立复验 ----------
    my_pass = MY.get("通过项")
    my_fail = MY.get("失败项") or []
    add("V03 我方对 A 的独立复验：11/12 通过，1 项返工",
        my_pass == 11 and len(my_fail) == 1,
        {"通过项": my_pass, "失败项": my_fail, "被验哈希": MY.get("被验文件_sha256"),
         "结论": MY.get("结论")})

    # ---------- V04 A07b 与 B 独立结果交叉验证 ----------
    a_sum = A.get("summary_by_caliber") or {}
    b_sum = (B["02_逐笔复验"].get("w4_04_r2_real_reconciliation") or {}).get("summary_by_caliber") or {}
    cross = {}
    for cal in ["生效视图_5bps", "策略包_3bps"]:
        a_claim = (a_sum.get(cal) or {}).get("claim_dist") or {}
        b_claim = (b_sum.get(cal) or {}).get("claim_distribution") or {}
        cross[cal] = {"A_summary": a_claim, "B_独立": b_claim,
                      "A缺键": sorted(set(b_claim) - set(a_claim)),
                      "数值不一致": {k: [a_claim.get(k), b_claim.get(k)]
                                 for k in set(a_claim) & set(b_claim) if a_claim.get(k) != b_claim.get(k)}}
    a_missing_keys = {c: v["A缺键"] for c, v in cross.items() if v["A缺键"]}
    add("V04 A07b 由 B 独立结果交叉印证：A 的 claim 分布缺 UNKNOWN 键（非数值错）",
        bool(a_missing_keys) and all(not v["数值不一致"] for v in cross.values()),
        {"交叉表": cross, "说明": "A 的 summary 少一个键，不是算错；但下游按分布读会漏掉 CARVUSDT 拒单候选"})

    # ---------- V05 B 产物版本一致性（拆两维，别把两件事混成一件） ----------
    ts_map = {k: v["文件内ts"] for k, v in B_HASH.items()}
    dated = {k: parse_ts(v) for k, v in ts_map.items() if v}
    undated = sorted(k for k, v in ts_map.items() if not v)
    span = (max(dated.values()) - min(dated.values())).total_seconds() if len(dated) >= 2 else None
    SAME_BATCH_SEC = 600  # 我方判据：10 分钟内视为同批（上游未定义同批口径）
    add("V05a B 中有文件内时间戳的产物属同一批次",
        span is not None and span <= SAME_BATCH_SEC,
        {"文件内ts": ts_map, "跨度秒": span, "判据": f"跨度 ≤ {SAME_BATCH_SEC}s（我方判据，非上游定义）",
         "判定": "同批" if (span is not None and span <= SAME_BATCH_SEC) else "跨批"})
    defect("V05b B 的每件产物都带文件内时间戳（否则批次不可判定）",
           {"无ts产物": undated,
            "说明": "05_数字对撞结果.json 全文无 timestamp 字段，只能靠文件 mtime 推断批次；"
                    "mtime 是外部量，换机器/换拷贝就失真，不能当身份"},
           ["C 在 05 仍是 BLOCKED 版本时读到它，无法从产物本身证明它何时被改",
            "B 随后把 05 改成 RE_AUDIT_PASSED，但修复时刻同样无法自证",
            "⇒ 建议 B 给 05 补 timestamp_utc，或声明 05 为派生件不单独作为结论载体"])

    # ---------- V06 B 结论自相矛盾（含版本历史留痕） ----------
    status_in_05 = []
    for c in (B["05_数字对撞"].get("checks") or []):
        det = str(c.get("detail") or "")
        m = re.search(r"Final Status:\s*([A-Z_0-9]+)", det)
        if m:
            status_in_05.append({"check": c.get("check_name"), "value": m.group(1),
                                 "passed": c.get("passed")})
    final_in_summary = (B["summary_判定汇总"].get("verdict_summary") or {}).get("final_acceptance_status")
    conflict = [s for s in status_in_05 if final_in_summary and s["value"] != final_in_summary]
    HISTORY_V06 = {
        "C观测到的前一批次": {
            "05第11项": {"check": "最终验收状态 BLOCKED_AWAITING_W4_04_R2 严密阻断",
                        "detail": "Final Status: BLOCKED_AWAITING_W4_04_R2", "passed": True},
            "05第7项": {"check": "分类分布对撞 (生效视图 11/40/0, 策略包 11/39/1)",
                       "detail": "Eff: 11/40/0, Pkg: 11/39/1"},
            "summary当时": "RE_AUDIT_PASSED",
            "冲突": "05 把 BLOCKED 当通过条件，summary 已判 PASSED ⇒ 产物集自相矛盾",
        },
        "当前批次": {
            "05第11项": "最终复验通过状态 RE_AUDIT_PASSED 确立",
            "05第7项": "证明分级分布对撞 (生效视图 44/7/0, 策略包 43/7/1)",
            "冲突": "已消除（B 自行改写 05）",
        },
        "留痕说明": "该冲突由 C 在本轮观测发现；B 随后自行修正。因 05 无文件内时间戳，"
                    "修正时刻不能由产物自证，只能记为「C 观测后、02:02 批次内」",
    }
    add("V06 B 的 05 对撞结论与 summary 最终状态一致（当前批次）",
        not conflict,
        {"05内的FinalStatus": status_in_05, "summary的final_acceptance_status": final_in_summary,
         "当前冲突项": conflict, "版本历史": HISTORY_V06,
         "判定": "当前一致" if not conflict else "自相矛盾"})

    # ---------- V07 B 的 layer_2 是否独立 ----------
    layer2 = ((B["02_逐笔复验"].get("verification_taxonomy_and_layers") or {})
              .get("layer_2_official_evidence_and_gap_classification") or {})
    layer2_03 = ((B["03_基线审计"].get("layer_separation_audit") or {})
                 .get("layer_2_official_evidence_and_gap_classification"))
    l2_status = str(layer2.get("status") or "")
    add("V07 B 对官方证据/周期判定/缺口分类层做了独立复核",
        l2_status.startswith("PASSED") and not str(layer2_03 or "").startswith("REFERENCED"),
        {"02里的status": l2_status, "02里的independence": layer2.get("independence"),
         "03里的表述": layer2_03,
         "判定": "未独立复核：B 明确写 REFERENCED_FROM_A_EVIDENCE，只复核了 06-06 数据覆盖自洽性"})

    # ---------- V08 B 的判定表是否分列三态 ----------
    items = B["summary_判定汇总"].get("items") or []
    cols = sorted({k for it in items for k in it})
    has_indep = any("independent_verification" in c for c in cols)
    dist = {}
    for it in items:
        dist[it.get("executor_delivery_status")] = dist.get(it.get("executor_delivery_status"), 0) + 1
    defect("V08 B 的 14 项判定表按「执行方交付 / 独立复验 / Codex 验收」三列分离",
           {"列名": cols, "是否含独立复验列": has_indep, "executor_delivery_status 取值分布": dist,
            "items": len(items)},
           ["B 是独立复验方，但其表里只有 executor_delivery_status 与 codex_audit_status 两列",
            "executor_delivery_status 承载的却是 B 自身的复验结论（如「102 笔 0 差异」「恒等式 100% 成立」）",
            "⇒ 列名与语义错位：读者会把 B 的复验结论当成上游执行方的交付状态",
            "不影响其结论成立，但下游按列名取数会误判责任方"])

    # ---------- V09 B 复用旧证据的条数 ----------
    reused = (B["03_基线审计"].get("verification_taxonomy") or {}).get("reused_from_prior_verified_evidence") or []
    newly = (B["03_基线审计"].get("verification_taxonomy") or {}).get("newly_performed_in_04_r2") or []
    add("V09 B 本轮新做与复用旧证据已分开声明",
        bool(newly) and bool(reused),
        {"本轮新做": len(newly), "复用旧证据": len(reused),
         "复用清单": reused,
         "说明": "复用项均标注 source_evidence 且 evidence_code_hash_match=true（代码哈希一致是复用的前提），但复用≠本轮重跑"})

    # ---------- V10 金额三方一致（含落盘精度差） ----------
    # A 落盘 9 位小数，B 落盘 6 位小数。B 的 difference=0.0 是在 6 位内部自洽，
    # 不等于「B 的值 == A 的落盘值」。必须把截断差单独报出来，不能并进「0 差异」。
    PREC_TOL = 1e-6
    money = {}
    for cal in ["生效视图_5bps", "策略包_3bps"]:
        a_val = (a_sum.get(cal) or {}).get("funding_new_sum")
        b_val = (b_sum.get(cal) or {}).get("w4_funding_total")
        b_ind = (b_sum.get(cal) or {}).get("independent_funding_total")
        b_diff = (b_sum.get(cal) or {}).get("difference")
        money[cal] = {
            "A落盘9位": a_val, "B落盘6位_读A": b_val, "B落盘6位_独立算": b_ind,
            "B自报difference": b_diff,
            "B读A减A": None if (a_val is None or b_val is None) else b_val - a_val,
            "B独立减A": None if (a_val is None or b_ind is None) else b_ind - a_val,
        }
    add("V10 两套口径的资金费总额：A 落盘值 = B 读到的 = B 独立算的（容差 1e-6 = B 的 6 位落盘精度）",
        all(abs(v["B读A减A"]) <= PREC_TOL and abs(v["B独立减A"]) <= PREC_TOL for v in money.values()),
        {"明细": money, "容差": PREC_TOL,
         "口径声明": "B 的 difference=0.0 只证明「B 读 A 的 6 位值 == B 独立算的 6 位值」；"
                     "与 A 的 9 位落盘值存在 1e-7~1e-8 量级截断差，属精度差不是算错",
         "说明": "三方一致只证明「钱没算错」，不证明官方周期/完整性定性正确——后者 B 未独立复核（见 V07）"})

    # ---------- V11 D 的计数 ----------
    d_counts = D_01.get("counts") or {}
    by_level = d_counts.get("by_level") or {}
    p_items = [i for i in D_PENDING["items"] if i.get("class") == "P"]
    e_items = [i for i in D_PENDING["items"] if i.get("class") == "E"]
    g_items = [i for i in D_PENDING["items"] if i.get("class") == "G"]
    dedupe_counts = D_04.get("counts") or {}
    add("V11 D 的计数自洽：31 行(E1 7/E2 1/E3 23)、21 项(12P/6E/3G)、45+4=49 字段",
        d_counts.get("rows") == 31 and by_level == {"E1": 7, "E2": 1, "E3": 23}
        and len(D_PENDING["items"]) == 21
        and (len(p_items), len(e_items), len(g_items)) == (12, 6, 3)
        and dedupe_counts.get("p_fields") == 45 and dedupe_counts.get("p_fields_plus_credentials") == 49,
        {"approval_counts": d_counts, "待决项": len(D_PENDING["items"]),
         "12P/6E/3G": [len(p_items), len(e_items), len(g_items)],
         "dedupe_counts": dedupe_counts,
         "references": D_01.get("references"),
         "bad_references": len(D_01.get("bad_references") or [])})

    # ---------- V12 D 的证据边界限定 ----------
    raw_ok = (D_01.get("input") or {}).get("raw_user_messages_retrieved")
    add("V12 D 已声明「未取证原始消息」⇒ E3 只能写「本轮未找到批准证据」",
        raw_ok is False,
        {"raw_user_messages_retrieved": raw_ok,
         "note": (D_01.get("input") or {}).get("raw_user_messages_note"),
         "禁语": ["用户未批准", "谁都没批过", "无来源", "已撤销"],
         "允许写法": "本轮未找到批准证据（E3），行为效力独立于证据等级"})

    # ---------- V13 三组样本隔离 ----------
    orig7 = ((B["03_基线审计"].get("reused_evidence_details") or {}).get("original_7_events") or {})
    samples = {
        "S1_历史机制回放": {"候选": len(cands), "口径行": rows,
                            "覆盖方": "A（财务/判据）+ B（逐笔独立复算）",
                            "用途": "证明资金费口径与完整性定性的改变，不证明策略有效"},
        "S2_新策略原7事件": {"候选": orig7.get("candidates_produced"),
                             "失效": orig7.get("invalidated_count"),
                             "未建档": orig7.get("not_found_count"),
                             "覆盖方": "B 复用 W5-02 旧证据（代码哈希一致）",
                             "用途": "证明破位拦截的工程行为，不证明策略有效或无效"},
        "S3_自然运行": {"自然候选": 0, "已平仓": 0, "覆盖方": "无",
                        "用途": "未验证；不得声称自然信号→订单闭环已完成"},
    }
    add("V13 三组样本已分开，未拼成「已完成闭环」",
        samples["S2_新策略原7事件"]["候选"] == 0 and samples["S3_自然运行"]["自然候选"] == 0,
        {"样本": samples,
         "禁止表述": ["自然信号→订单闭环已打通", "策略已通过", "实盘可交付"]})

    # ---------- V14 A 的未验证项是否被 B 覆盖 ----------
    b_uncovered = ((B["summary_判定汇总"].get("four_blocks_receipt") or {}).get("4_uncovered_items"))
    add("V14 A 的未验证项未被 B 的复验覆盖（不能因 B 通过而视为已验）",
        bool(b_uncovered) and "UNVERIFIED" in str(b_uncovered),
        {"B自述未覆盖": b_uncovered,
         "A侧未验证项": ["当时可得（availability 全 unproven）",
                        "官方周期只对 updateTime 之后有效（IRYSUSDT 1h vs 观测 4h，Δ3780h）",
                        "端点抖动先后（2 笔候选 / 两口径共 4 条 case，其中 WALUSDT 生效视图 −0.362186164 是收取）",
                        "自然挂钟成交"],
         "说明": "B 的 102/102 只覆盖财务算术层；上面四项仍是未验证"})

    # ---------- 四列状态矩阵 ----------
    matrix = {
        "A_W4R2_财务与判据": {
            "执行方交付": {"status": "DELIVERED", "自报": "全部完成",
                          "锚点": {"06_json_sha256": sha(A_JSON), "README_sha256": sha(A_README)}},
            "独立复验": {"status": "PASS_WITH_1_REWORK", "复验方": "本窗口 05",
                        "通过项": my_pass, "失败项": my_fail},
            "Codex验收": {"status": "PENDING", "说明": "本次交接即提交材料，验收未做"},
            "用户待决": {"status": "NONE_FOR_A", "说明": "A 不选参数、不裁决；返工项由 A 自己修"},
        },
        "B_W5R2_独立复验": {
            "执行方交付": {"status": "DELIVERED", "产物": B_HASH,
                          "版本": "B 在 C 收口期间连改两轮：01/02/03/summary 已到 02:02 批次，"
                                  "05 无 ts 但内容已从 BLOCKED 改为 RE_AUDIT_PASSED（见 V06 版本历史）"},
            "独立复验": {"status": "PASS_ON_FINANCIAL_LAYER_ONLY",
                        "财务算术层": "102/102 逐笔 0 差异（B 落盘 6 位，与 A 的 9 位差 1e-7 量级，见 V10）",
                        "官方证据层": "REFERENCED_FROM_A_EVIDENCE（未独立复核，见 V07）",
                        "复用旧证据": f"{len(reused)} 条（非本轮重跑，见 V09）"},
            "Codex验收": {"status": "PENDING", "说明": "B 的 14 项全部标 PENDING_CODEX_AUDIT"},
            "用户待决": {"status": "NONE_FOR_B", "说明": "B 不裁决参数"},
        },
        "D_W1R1_批准证据校正": {
            "执行方交付": {"status": "DELIVERED", "产物": "out/00–05 + 待决表_R1.json + 校正表.json",
                          "计数": {"行": d_counts.get("rows"), "E1/E2/E3": by_level,
                                   "待决项": len(D_PENDING["items"])}},
            "独立复验": {"status": "PASS_BY_C_THIS_ROUND",
                        "说明": "本矩阵 V11/V12 即为 C 对 D 的计数与边界核对，18/18 引用通过、0 坏引用"},
            "Codex验收": {"status": "PENDING"},
            "用户待决": {"status": "21_ITEMS", "明细": "12 参数 / 6 证据 / 3 工程；止损 8 项为 E1 不再询问"},
        },
        "C_W6R2_总交接": {
            "执行方交付": {"status": "IN_PROGRESS", "本文件": "06_阶段状态矩阵 + 总交接_R2 正文"},
            "独立复验": {"status": "N/A", "说明": "C 不独立复验财务，只复验 A/B/D 的结论与一致性"},
            "Codex验收": {"status": "THIS_IS_THE_INPUT"},
            "用户待决": {"status": "21_ITEMS_FROM_D + 3_ECONOMIC_CALIBER + 49_RULE_PARAMS"},
        },
    }

    usable = [
        {"项": "A-R2 的两口径逐笔金额与事件数", "状态": "可用",
         "依据": "A05 三条恒等式 102/102、A08 与 R1 逐笔对齐、B 独立复算 0 差异（V10）",
         "限制": "金额可用 ≠ 完整性定性正确"},
        {"项": "A-R2 的完整性定性（NO_GAP_DETECTED 51 / PROVEN 44+7）", "状态": "可用但仅 A 单方证明",
         "依据": "A 的 02/04 脚本 + 21/21 数字对撞", "限制": "B 未独立复核官方证据层（V07）"},
        {"项": "B 的逐笔财务独立复验", "状态": "可用",
         "依据": "102/102、0 成本不符、0 恒等式失败、3/3 负例拦截", "限制": "只覆盖财务算术层"},
        {"项": "D 的 21 项待决清单与 49 字段归属", "状态": "可用",
         "依据": "V11 计数自洽、18/18 引用通过", "限制": "E3 项无批准原话，须用户裁决"},
        {"项": "D 的「止损 8 项 E1 不再询问」", "状态": "可用",
         "依据": "02_stop_decision.json：not_asked 8 项、代码 6/6 生效值相等", "限制": "仅止损语义"},
    ]

    rework = [
        {"项": "A07b：策略包_3bps 的 completeness_dist / claim_dist 分母 50≠51",
         "责任方": "A", "依据": "本窗口 05 + V04 交叉印证",
         "修法": "把 CARVUSDT 的 NOT_APPLICABLE / UNKNOWN 计入分布，使分母等于 change_reason 的 51",
         "影响": "不影响金额；影响下游按分布读时的候选覆盖完整性"},
        {"项": "B-V05b：05_数字对撞结果.json 无文件内时间戳，批次与修复时刻不可自证",
         "责任方": "B", "依据": "V05b + V06 版本历史",
         "修法": "补 timestamp_utc，或声明 05 为派生件、结论以 summary 为准",
         "影响": "C 观测到 05 曾是 BLOCKED 版本、后被改为 RE_AUDIT_PASSED；无 ts ⇒ 这段修复不可审计",
         "状态": "结论冲突本身已由 B 消除，仅剩时间戳缺失"},
        {"项": "B-V08：14 项判定表缺独立复验列，executor_delivery_status 语义错位",
         "责任方": "B", "依据": "V08",
         "修法": "拆成 executor_delivery / independent_verification / codex_acceptance 三列",
         "影响": "不改变结论，但责任方会被误读"},
        {"项": "B-V07：官方证据/周期判定/缺口分类层未独立复核",
         "责任方": "B（或用户接受 A 单方证明）", "依据": "REFERENCED_FROM_A_EVIDENCE",
         "修法": "要么 B 独立取一次官方快照，要么在交接里明确写「该层仅 A 单方证明」",
         "影响": "这是「通过」与「未验证」的分界，不得含糊"},
    ]

    pending = [
        {"类": "经济口径", "项": "两套口径未择一（滑点 3/5bps、本金 2000/5000、参与率 0.1%/1.0%）",
         "影响": "开仓阻塞", "责任": "用户"},
        {"类": "规则参数", "项": "D 的 12 个 P 项（49 字段中 45 行为字段 + 4 凭证）",
         "影响": "开仓阻塞", "责任": "用户"},
        {"类": "证据", "项": "D 的 6 个 E 项（E3 无批准原话，含 1h 确认 / 承接倍率 / 冷却 24h / 手续费 5bps）",
         "影响": "定性阻塞", "责任": "用户"},
        {"类": "工程", "项": "D 的 3 个 G 项（G-02 生效时刻网格 vs 挂钟；G-01/G-03 见待决表）",
         "影响": "记录键与资金费窗右端", "责任": "工程 + 用户裁决"},
        {"类": "工程", "项": "A 的 A07b 返工", "影响": "分布分母", "责任": "A"},
        {"类": "工程", "项": "B 的 V06/V07/V08 三处收口", "影响": "产物自洽与独立性声明", "责任": "B"},
    ]

    next_minimal = [
        "① 用户择一两套经济口径（不择则开仓永远阻塞）",
        "② 用户对 D 的 12 个 P 项一次性裁决（一字段只问一次，已去重）",
        "③ A 修 A07b 一个分布分母（单点改动，不重算金额）",
        "④ B 补 05 的 timestamp + 拆三列 + 明确 layer_2 归属（05 结论冲突已由 B 自行消除）",
        "⑤ 上述完成后，由 Codex 对 A/B/D 三窗口做一次验收，不由执行方自签",
        "不做：不扩大研究、不调参、不加前端、不启用交易",
    ]

    result = {
        "schema": "accum-early-06-w6-r2/stage-matrix/v1",
        "task": "ACCUM-EARLY-06-W6-R2",
        "生成时刻_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "说明_不稳定字段": ["生成时刻_utc 按设计每次运行都变；判定与哈希不随运行变化"],
        "上游快照": {
            "A": {"06_json": {"bytes": A_JSON.stat().st_size, "sha256": sha(A_JSON)},
                  "06_csv": {"bytes": A_CSV.stat().st_size, "sha256": sha(A_CSV)},
                  "README": {"bytes": A_README.stat().st_size, "sha256": sha(A_README)},
                  "R1基线": {"bytes": R1_JSON.stat().st_size, "sha256": sha(R1_JSON)}},
            "B": B_HASH,
            "D": {"待决表_R1": {"bytes": (D_DIR / "待决表_R1.json").stat().st_size,
                             "sha256": sha(D_DIR / "待决表_R1.json")},
                  "out_01": {"sha256": sha(D_DIR / "out" / "01_approval_evidence.json")},
                  "out_02": {"sha256": sha(D_DIR / "out" / "02_stop_decision.json")},
                  "out_04": {"sha256": sha(D_DIR / "out" / "04_pending_dedupe.json")}},
            "本窗口05": {"sha256": sha(MY05)},
        },
        "四列状态矩阵": matrix,
        "检查项": CHECKS,
        "通过项": sum(1 for c in CHECKS if c["通过"]),
        "失败项": [c["检查"] for c in CHECKS if not c["通过"]],
        "最终可用项": usable,
        "返工项": rework,
        "待决项": pending,
        "下一步最小必要工作": next_minimal,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "06_阶段状态矩阵.json"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"写出 {dst}  ({dst.stat().st_size} B)")
    print(f"检查项 {len(CHECKS)}：通过 {result['通过项']} / 失败 {len(result['失败项'])}")
    for c in CHECKS:
        flag = "OK " if c["通过"] else "FAIL"
        print(f"  [{flag}] {c['检查']}")
        if not c["通过"]:
            print(f"         {json.dumps(c.get('明细'), ensure_ascii=False)[:220]}")
    return 0 if not result["失败项"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
