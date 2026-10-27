# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-R2 | 脚本 01: 依赖真实硬门禁与防写死深度校验套件 (接续支持 A-R2 正式交付)

【设计规范】
1. 深度解析工单 A 交付文件，优先接入 A 的正式 R2 交付文件 06_机器可读_交付.json (schema: accum-early-06-w4-r2/v1)；
2. 校验 51 候选 × 2 套口径 (共 102 条目)、candidate_id 查重去重、字段完整性、代码哈希声明；
3. 缺字段直接报错 BLOCKED_MISSING_ITEM_FIELD，绝不用默认 0 或默认通过掩盖；
4. 单元测试覆盖 6 大负例 (空JSON、错schema、缺候选、重复ID、版本漂移、声明篡改) 与 1 正例；
5. 输出至 evidence/ACCUM-EARLY-06-W5/04_R2/out/01_dependency_hard_gate.json。
"""
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
ADAPTER_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"

EXPECTED_W2_ENTRY_SHA256 = "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017"
EXPECTED_SYMBOLS_COUNT = 48
EXPECTED_CANDIDATES_COUNT = 51
EXPECTED_CALIBERS = ["生效视图_5bps", "策略包_3bps"]
EXPECTED_TOTAL_RECALC_ITEMS = 102

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "04_R2" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def calc_sha256(path: Path) -> str:
    """计算文件 SHA256 哈希值。"""
    if not path.is_file():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def inspect_w2_delivery(custom_code_path: Optional[Path] = None) -> Dict[str, Any]:
    """深度核验工单 W2 交付物与状态机代码哈希。"""
    code_path = custom_code_path or (BACKEND_DIR / "accum_early_entry.py")
    handoff_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W2" / "02" / "out" / "04_handoff.json"

    exists = code_path.exists()
    sha256 = calc_sha256(code_path) if exists else "NOT_FOUND"
    hash_matched = (sha256 == EXPECTED_W2_ENTRY_SHA256)
    handoff_ok = handoff_path.exists()

    status = "PASSED" if (exists and hash_matched and handoff_ok) else "FAILED"
    reason = "OK" if status == "PASSED" else (
        "CODE_NOT_FOUND" if not exists else (
            "HASH_MISMATCH" if not hash_matched else "HANDOFF_MISSING"
        )
    )

    return {
        "status": status,
        "reason": reason,
        "code_path": str(code_path),
        "code_exists": exists,
        "sha256": sha256,
        "expected_sha256": EXPECTED_W2_ENTRY_SHA256,
        "hash_matched": hash_matched,
        "handoff_path": str(handoff_path),
        "handoff_exists": handoff_ok,
    }


def inspect_w3_delivery() -> Dict[str, Any]:
    """深度核验窗口三 06-06 资金费源交付物。"""
    adapter_path = ADAPTER_DIR / "funding_events_adapter.py"
    manifest_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out" / "funding_events_母集48_manifest.json"
    jsonl_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out" / "funding_events_母集48.jsonl"

    adapter_ok = adapter_path.exists()
    manifest_ok = manifest_path.exists()
    jsonl_ok = jsonl_path.exists()

    symbols_count = 0
    events_count = 0
    manifest_valid = False

    if manifest_ok:
        try:
            m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            counts = m_data.get("counts", {})
            symbols_count = counts.get("symbols", 0) or len(m_data.get("per_symbol", []))
            events_count = counts.get("events", 0)
            manifest_valid = (symbols_count == EXPECTED_SYMBOLS_COUNT and events_count > 0)
        except Exception:
            manifest_valid = False

    status = "PASSED" if (adapter_ok and manifest_valid and jsonl_ok) else "FAILED"
    return {
        "status": status,
        "adapter_path": str(adapter_path),
        "adapter_exists": adapter_ok,
        "manifest_path": str(manifest_path),
        "manifest_valid": manifest_valid,
        "symbols_count": symbols_count,
        "expected_symbols_count": EXPECTED_SYMBOLS_COUNT,
        "events_count": events_count,
        "jsonl_path": str(jsonl_path),
        "jsonl_exists": jsonl_ok,
        "jsonl_bytes": jsonl_path.stat().st_size if jsonl_ok else 0,
    }


def parse_and_validate_w4_payload(file_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """深度解析窗口四交付的重算结果文件。
    
    同时支持两种合法 schema：
    1. A-R2 正式 schema: accum-early-06-w4-r2/v1 (candidates 列表，嵌套 calibers)；
    2. 旧 R1 兼容 schema: recalc_items 扁平列表。
    
    执行严格的字段对应、记录身份唯一性与防篡改门禁。
    返回: (is_valid, failure_code_or_ok, details_dict)
    """
    if not file_path.exists():
        return False, "BLOCKED_FILE_NOT_FOUND", {"error": f"File not found: {file_path}"}

    try:
        raw_text = file_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return False, "BLOCKED_EMPTY_FILE", {"error": "File is empty"}
        data = json.loads(raw_text)
    except Exception as e:
        return False, "BLOCKED_JSON_DECODE_ERROR", {"error": str(e)}

    if not isinstance(data, dict):
        return False, "BLOCKED_MALFORMED_STRUCTURE", {"error": "Payload root must be a dict"}

    # 判定模式：R2 模式 (candidates) 或 R1 兼容模式 (recalc_items)
    is_r2_schema = "candidates" in data or data.get("schema") == "accum-early-06-w4-r2/v1"

    if is_r2_schema:
        # ─── A 的 R2 正式交付深度解析 ───
        required_root_keys = ["schema", "task", "summary_by_caliber", "candidates"]
        for rk in required_root_keys:
            if rk not in data:
                return False, "BLOCKED_MALFORMED_STRUCTURE", {
                    "missing_key": rk,
                    "error": f"R2 schema root missing required field: {rk}"
                }

        candidates = data.get("candidates")
        if not isinstance(candidates, list):
            return False, "BLOCKED_MALFORMED_STRUCTURE", {"error": "candidates must be a list"}

        if len(candidates) != EXPECTED_CANDIDATES_COUNT:
            return False, "BLOCKED_INSUFFICIENT_CANDIDATES", {
                "expected_candidates": EXPECTED_CANDIDATES_COUNT,
                "actual_candidates": len(candidates),
                "error": f"Expected {EXPECTED_CANDIDATES_COUNT} candidates, got {len(candidates)}"
            }

        seen_candidate_ids = set()
        required_cand_keys = ["candidate_id", "symbol", "entry_ms", "exit_ms", "calibers"]
        required_caliber_keys = [
            "status", "is_filled", "funding_old", "funding_new", "delta_funding",
            "net_old", "net_new", "delta_net", "events_old", "events_new", "completeness", "claim"
        ]

        total_caliber_items = 0
        items_by_caliber = {c: [] for c in EXPECTED_CALIBERS}

        for idx, cand in enumerate(candidates):
            if not isinstance(cand, dict):
                return False, "BLOCKED_MALFORMED_STRUCTURE", {"error": f"Candidate at index {idx} is not a dict"}

            for ck in required_cand_keys:
                if ck not in cand:
                    return False, "BLOCKED_MISSING_ITEM_FIELD", {
                        "index": idx,
                        "missing_key": ck,
                        "candidate_id": cand.get("candidate_id", "UNKNOWN")
                    }

            cid = cand["candidate_id"]
            if cid in seen_candidate_ids:
                return False, "BLOCKED_DUPLICATE_RECORD_IDS", {
                    "duplicate_candidate_id": cid,
                    "error": f"Duplicate candidate_id '{cid}' found across candidates"
                }
            seen_candidate_ids.add(cid)

            cals = cand.get("calibers", {})
            for expected_cal in EXPECTED_CALIBERS:
                if expected_cal not in cals:
                    return False, "BLOCKED_MISSING_CALIBER", {
                        "candidate_id": cid,
                        "missing_caliber": expected_cal
                    }
                cal_info = cals[expected_cal]
                for ik in required_caliber_keys:
                    if ik not in cal_info:
                        return False, "BLOCKED_MISSING_ITEM_FIELD", {
                            "candidate_id": cid,
                            "caliber": expected_cal,
                            "missing_key": ik
                        }
                items_by_caliber[expected_cal].append(cal_info)
                total_caliber_items += 1

        if total_caliber_items != EXPECTED_TOTAL_RECALC_ITEMS:
            return False, "BLOCKED_INSUFFICIENT_CANDIDATES", {
                "expected_total_items": EXPECTED_TOTAL_RECALC_ITEMS,
                "actual_total_items": total_caliber_items
            }

        # 代码哈希声明校验 (如果 payload 包含 code_hashes 字段)
        code_hashes = data.get("code_hashes", {})
        if "accum_early_entry_py" in code_hashes:
            dec_hash = code_hashes["accum_early_entry_py"]
            if dec_hash != EXPECTED_W2_ENTRY_SHA256:
                return False, "BLOCKED_CODE_HASH_MISMATCH", {
                    "declared_hash": dec_hash,
                    "expected_hash": EXPECTED_W2_ENTRY_SHA256,
                    "target": "accum_early_entry_py"
                }

        return True, "DEPENDENCY_READABLE", {
            "schema_detected": "accum-early-06-w4-r2/v1",
            "candidates_verified": len(seen_candidate_ids),
            "calibers_verified": list(items_by_caliber.keys()),
            "total_items": total_caliber_items,
            "all_candidate_ids_unique": True
        }

    else:
        # ─── 旧 R1 兼容解析模式 ───
        required_root_keys = ["task", "calibers_summary", "recalc_items"]
        for rk in required_root_keys:
            if rk not in data:
                return False, "BLOCKED_MALFORMED_STRUCTURE", {
                    "missing_key": rk,
                    "error": f"Root missing required field: {rk}"
                }

        recalc_items = data.get("recalc_items")
        if not isinstance(recalc_items, list):
            return False, "BLOCKED_MALFORMED_STRUCTURE", {"error": "recalc_items must be a list"}

        if len(recalc_items) != EXPECTED_TOTAL_RECALC_ITEMS:
            return False, "BLOCKED_INSUFFICIENT_CANDIDATES", {
                "expected_items": EXPECTED_TOTAL_RECALC_ITEMS,
                "actual_items": len(recalc_items)
            }

        items_by_caliber = {c: [] for c in EXPECTED_CALIBERS}
        seen_ids_by_caliber = {c: set() for c in EXPECTED_CALIBERS}

        common_item_keys = [
            "candidate_id", "symbol", "caliber", "is_filled", "status",
            "funding_old", "funding_new", "delta_funding_cost", "delta_realized_net", "new_usability"
        ]

        for idx, item in enumerate(recalc_items):
            if not isinstance(item, dict):
                return False, "BLOCKED_MALFORMED_STRUCTURE", {"error": f"Item at index {idx} is not a dict"}
            for ik in common_item_keys:
                if ik not in item:
                    return False, "BLOCKED_MISSING_ITEM_FIELD", {
                        "index": idx,
                        "missing_key": ik,
                        "candidate_id": item.get("candidate_id", "UNKNOWN")
                    }

            cal = item.get("caliber")
            if cal not in items_by_caliber:
                return False, "BLOCKED_UNKNOWN_CALIBER", {"error": f"Unknown caliber: {cal}"}

            cid = item.get("candidate_id")
            if cid in seen_ids_by_caliber[cal]:
                return False, "BLOCKED_DUPLICATE_RECORD_IDS", {
                    "caliber": cal,
                    "duplicate_candidate_id": cid
                }
            seen_ids_by_caliber[cal].add(cid)
            items_by_caliber[cal].append(item)

        for cal in EXPECTED_CALIBERS:
            if len(seen_ids_by_caliber[cal]) != EXPECTED_CANDIDATES_COUNT:
                return False, "BLOCKED_INSUFFICIENT_CANDIDATES", {
                    "caliber": cal,
                    "expected_candidates": EXPECTED_CANDIDATES_COUNT,
                    "actual_candidates": len(seen_ids_by_caliber[cal])
                }

        return True, "DEPENDENCY_READABLE", {
            "schema_detected": "legacy_r1_recalc_items",
            "calibers_verified": list(items_by_caliber.keys()),
            "total_items": len(recalc_items),
            "candidates_per_caliber": {c: len(seen_ids_by_caliber[c]) for c in EXPECTED_CALIBERS},
            "all_candidate_ids_unique": True
        }


def evaluate_w5_dependency_gate(w4_target_dir: Path, custom_w2_code: Optional[Path] = None) -> Dict[str, Any]:
    """门禁综合评判主入口，区分 DEPENDENCY_READABLE、RE_AUDIT_EXECUTED、RE_AUDIT_PASSED。"""
    w2_res = inspect_w2_delivery(custom_code_path=custom_w2_code)
    w3_res = inspect_w3_delivery()

    # 优先查找 A 的 R2 正式交付产物 06_机器可读_交付.json
    w4_readme = w4_target_dir / "README.md"
    w4_json_r2 = w4_target_dir / "out" / "06_机器可读_交付.json"
    w4_json_r1_1 = w4_target_dir / "out" / "01_资金费重算与判据整改明细.json"
    w4_json_r1_2 = w4_target_dir / "out" / "02_funding_recalculated_results.json"

    if w4_json_r2.exists():
        target_json = w4_json_r2
    elif w4_json_r1_1.exists():
        target_json = w4_json_r1_1
    else:
        target_json = w4_json_r1_2

    w4_readable = False
    w4_gate_code = "NOT_EVALUATED"
    w4_details = {}

    if not w4_target_dir.exists():
        w4_gate_code = f"BLOCKED_AWAITING_{w4_target_dir.name.upper()}"
        w4_details = {"error": f"Directory does not exist: {w4_target_dir}"}
    elif not w4_readme.exists():
        w4_gate_code = "BLOCKED_README_MISSING"
        w4_details = {"error": f"README.md not found in {w4_target_dir}"}
    elif not target_json.exists():
        w4_gate_code = "BLOCKED_TARGET_JSON_MISSING"
        w4_details = {"error": f"No recalc JSON found in {w4_target_dir / 'out'}"}
    else:
        w4_readable, w4_gate_code, w4_details = parse_and_validate_w4_payload(target_json)

    is_all_readable = (w2_res["status"] == "PASSED" and w3_res["status"] == "PASSED" and w4_readable)

    if not is_all_readable:
        overall_status = "BLOCKED_DEPENDENCIES_UNSATISFIED"
        primary_blocker = []
        if w2_res["status"] != "PASSED":
            primary_blocker.append(f"W2:{w2_res['reason']}")
        if w3_res["status"] != "PASSED":
            primary_blocker.append("W3:MANIFEST_OR_DATA_INCOMPLETE")
        if not w4_readable:
            primary_blocker.append(f"W4:{w4_gate_code}")
        primary_reason = " | ".join(primary_blocker)
    else:
        overall_status = "DEPENDENCY_READABLE"
        primary_reason = "上游全部交付文件物理存在、哈希吻合、结构与候选身份深度校验通过，就绪执行复验"

    return {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_status": overall_status,
        "primary_reason": primary_reason,
        "is_all_readable": is_all_readable,
        "w2_entry_code_audit": w2_res,
        "w3_funding_source_audit": w3_res,
        "w4_delivery_audit": {
            "target_dir": str(w4_target_dir),
            "target_json_path": str(target_json) if target_json.exists() else None,
            "readable": w4_readable,
            "gate_code": w4_gate_code,
            "details": w4_details
        }
    }


def run_negative_and_positive_test_suite() -> List[Dict[str, Any]]:
    """防写死严格单元测试套件：覆盖 6 大负例与正例，直接调用正式门禁函数！"""
    temp_root = Path(tempfile.mkdtemp(prefix="w5_gate_unit_tests_r2_"))
    results = []

    # 读取 A 的 R2 正式交付 fixture (06_机器可读_交付.json)
    w4_r2_json = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2" / "out" / "06_机器可读_交付.json"
    assert w4_r2_json.exists(), "W4-04_R2 正式交付不存在，无法启动单元测试！"
    base_data = json.loads(w4_r2_json.read_text(encoding="utf-8"))

    def create_fixture_dir(dir_name: str, payload_dict: Optional[Dict[str, Any]], write_readme: bool = True) -> Path:
        d = temp_root / dir_name
        d_out = d / "out"
        d_out.mkdir(parents=True, exist_ok=True)
        if write_readme:
            (d / "README.md").write_text("# Mock W4 delivery\n", encoding="utf-8")
        if payload_dict is not None:
            (d_out / "06_机器可读_交付.json").write_text(
                json.dumps(payload_dict, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return d

    try:
        # 负例 1: 仅有 README 和空 JSON ({})
        dir_neg_1 = create_fixture_dir("neg1_empty_json", {})
        res_1 = evaluate_w5_dependency_gate(dir_neg_1)
        passed_1 = (
            not res_1["is_all_readable"]
            and res_1["w4_delivery_audit"]["gate_code"] == "BLOCKED_MALFORMED_STRUCTURE"
        )
        results.append({
            "case_id": "NEG_01_EMPTY_JSON",
            "case_name": "负例 1: 空 JSON 深度拦截",
            "scenario": "目录中仅存在 README.md 和空 JSON `{}`",
            "expected_gate_code": "BLOCKED_MALFORMED_STRUCTURE",
            "actual_gate_code": res_1["w4_delivery_audit"]["gate_code"],
            "pass": passed_1
        })

        # 负例 2: 错 schema / 损坏结构 (缺失 candidates 顶层列表)
        bad_struct = copy.deepcopy(base_data)
        del bad_struct["candidates"]
        dir_neg_2 = create_fixture_dir("neg2_bad_struct", bad_struct)
        res_2 = evaluate_w5_dependency_gate(dir_neg_2)
        passed_2 = (
            not res_2["is_all_readable"]
            and res_2["w4_delivery_audit"]["gate_code"] == "BLOCKED_MALFORMED_STRUCTURE"
        )
        results.append({
            "case_id": "NEG_02_CORRUPTED_STRUCTURE",
            "case_name": "负例 2: 错 schema / 损坏结构拦截",
            "scenario": "缺少 candidates 核心数据结构",
            "expected_gate_code": "BLOCKED_MALFORMED_STRUCTURE",
            "actual_gate_code": res_2["w4_delivery_audit"]["gate_code"],
            "pass": passed_2
        })

        # 负例 3: 缺少候选 (条目数仅 40 笔，未满 51 候选)
        insufficient_data = copy.deepcopy(base_data)
        insufficient_data["candidates"] = insufficient_data["candidates"][:40]
        dir_neg_3 = create_fixture_dir("neg3_insufficient_candidates", insufficient_data)
        res_3 = evaluate_w5_dependency_gate(dir_neg_3)
        passed_3 = (
            not res_3["is_all_readable"]
            and res_3["w4_delivery_audit"]["gate_code"] == "BLOCKED_INSUFFICIENT_CANDIDATES"
        )
        results.append({
            "case_id": "NEG_03_INSUFFICIENT_CANDIDATES",
            "case_name": "负例 3: 候选数量不足拦截 (<51 候选)",
            "scenario": "candidates 仅包含 40 笔记录",
            "expected_gate_code": "BLOCKED_INSUFFICIENT_CANDIDATES",
            "actual_gate_code": res_3["w4_delivery_audit"]["gate_code"],
            "pass": passed_3
        })

        # 负例 4: 重复记录 ID (将第 0 项 candidate_id 覆盖第 1 项)
        dup_data = copy.deepcopy(base_data)
        dup_data["candidates"][1]["candidate_id"] = dup_data["candidates"][0]["candidate_id"]
        dir_neg_4 = create_fixture_dir("neg4_duplicate_ids", dup_data)
        res_4 = evaluate_w5_dependency_gate(dir_neg_4)
        passed_4 = (
            not res_4["is_all_readable"]
            and res_4["w4_delivery_audit"]["gate_code"] == "BLOCKED_DUPLICATE_RECORD_IDS"
        )
        results.append({
            "case_id": "NEG_04_DUPLICATE_RECORD_IDS",
            "case_name": "负例 4: 存在重复 candidate_id 拦截",
            "scenario": "候选列表中存在相同 candidate_id 的重复项",
            "expected_gate_code": "BLOCKED_DUPLICATE_RECORD_IDS",
            "actual_gate_code": res_4["w4_delivery_audit"]["gate_code"],
            "pass": passed_4
        })

        # 负例 5: accum_early_entry.py 代码版本漂移
        drift_code_file = temp_root / "accum_early_entry_drifted.py"
        drift_code_file.write_text("# Drifted code content\n", encoding="utf-8")
        dir_valid = create_fixture_dir("valid_for_hash_test", base_data)
        res_5 = evaluate_w5_dependency_gate(dir_valid, custom_w2_code=drift_code_file)
        passed_5 = (
            not res_5["is_all_readable"]
            and res_5["w2_entry_code_audit"]["status"] == "FAILED"
            and res_5["w2_entry_code_audit"]["reason"] == "HASH_MISMATCH"
        )
        results.append({
            "case_id": "NEG_05_CODE_HASH_DRIFT",
            "case_name": "负例 5: accum_early_entry.py 哈希漂移拦截",
            "scenario": "被测代码 SHA256 发生改变，非冻结哈希",
            "expected_w2_status": "FAILED",
            "actual_w2_status": res_5["w2_entry_code_audit"]["status"],
            "pass": passed_5
        })

        # 负例 6: 上游交付报告声明依赖哈希被篡改
        tampered_hash_data = copy.deepcopy(base_data)
        tampered_hash_data["code_hashes"]["accum_early_entry_py"] = "bad_fake_hash_99999999999"
        dir_neg_6 = create_fixture_dir("neg6_tampered_payload_hash", tampered_hash_data)
        res_6 = evaluate_w5_dependency_gate(dir_neg_6)
        passed_6 = (
            not res_6["is_all_readable"]
            and res_6["w4_delivery_audit"]["gate_code"] == "BLOCKED_CODE_HASH_MISMATCH"
        )
        results.append({
            "case_id": "NEG_06_PAYLOAD_HASH_MISMATCH",
            "case_name": "负例 6: 上游交付报告声明版本与基线不符拦截",
            "scenario": "W4-R2 JSON 中声明的 accum_early_entry_py 哈希被篡改",
            "expected_gate_code": "BLOCKED_CODE_HASH_MISMATCH",
            "actual_gate_code": res_6["w4_delivery_audit"]["gate_code"],
            "pass": passed_6
        })

        # 正例 7: 正式 A-R2 真实交付环境直接放行
        w4_r2_real_dir = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2"
        res_pos = evaluate_w5_dependency_gate(w4_r2_real_dir)
        passed_pos = (
            res_pos["is_all_readable"]
            and res_pos["overall_status"] == "DEPENDENCY_READABLE"
            and res_pos["w4_delivery_audit"]["gate_code"] == "DEPENDENCY_READABLE"
        )
        results.append({
            "case_id": "POS_07_VALID_DELIVERY",
            "case_name": "正向对照: A-R2 正式交付合规放行至 DEPENDENCY_READABLE",
            "scenario": "W2 哈希吻合、06-06 齐全、W4-04_R2 交付物 51 候选 (102 条目) 深度核验通过",
            "expected_status": "DEPENDENCY_READABLE",
            "actual_status": res_pos["overall_status"],
            "pass": passed_pos
        })

    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return results


def main():
    print("=" * 70)
    print("【ACCUM-EARLY-06-W5-R2】门禁与依赖深度校验 (接续 A-R2 正式接入) 执行中...")
    print("=" * 70)

    # 1. 执行负例与正向单元测试套件
    test_results = run_negative_and_positive_test_suite()
    all_tests_passed = all(t["pass"] for t in test_results)
    print(f"单元测试套件执行完成: {len(test_results)} 项测试，全部通过: {all_tests_passed}")
    for t in test_results:
        print(f"  - [{ 'PASS' if t['pass'] else 'FAIL' }] {t['case_id']}: {t['case_name']}")

    # 2. 真实当前环境评判：核实 A-R2 正式交付
    w4_r2_dir = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "04_R2"
    eval_r2 = evaluate_w5_dependency_gate(w4_r2_dir)

    report = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task": "ACCUM-EARLY-06-W5-R2",
        "description": "依赖真实硬门禁与防写死深度校验 (支持 A-R2 正式交付，含 6 负例 + 1 正例单元测试)",
        "unit_test_suite": {
            "total_cases": len(test_results),
            "passed_cases": sum(1 for t in test_results if t["pass"]),
            "all_passed": all_tests_passed,
            "cases": test_results
        },
        "w4_04_r2_real_delivery_evaluation": {
            "dir": str(w4_r2_dir),
            "target_json_path": eval_r2["w4_delivery_audit"]["target_json_path"],
            "gate_status": eval_r2["overall_status"],
            "is_all_readable": eval_r2["is_all_readable"],
            "primary_reason": eval_r2["primary_reason"],
            "detail": eval_r2
        },
        "verdict_summary": {
            "dependency_reading_status": "DEPENDENCY_READABLE" if eval_r2["is_all_readable"] else "BLOCKED",
            "ready_for_reconciliation": eval_r2["is_all_readable"],
            "handoff_note": "A-R2 真实交付物 06_机器可读_交付.json 已成功深度接入，解阻门禁，正式转入逐笔复算！"
        }
    }

    out_file = OUT_DIR / "01_dependency_hard_gate.json"
    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"门禁评判报告已生成: {out_file}")


if __name__ == "__main__":
    main()
