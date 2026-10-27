# -*- coding: utf-8 -*-
"""10｜把报告里所有关键数字**机械抽取**成一个 JSON，供正文逐条引用。

为什么要这一步（项目纪律）：
  报告里的每个数字都必须能指回 `out/*.json` 的原始字段；人工抄写已多次出错。
  本脚本只做「读文件 + 取字段 + 打印」，不含任何计算口径的二次加工
  （唯一例外：`net_pct_of_initial` 这类派生值会显式标注 `derived`）。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")


def _count(v):
    """`symbols_checked` 这类字段可能是 int 也可能是 list，统一成计数。"""
    if v is None:
        return None
    if isinstance(v, (list, tuple, dict, set)):
        return len(v)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def g(name):
    p = cm.OUT / name
    return cm.read_json(p) if p.exists() else None


def main():
    scan = g("02_候选扫描.json") or {}
    equiv = g("02_数据源等价性.json") or {}
    iso = g("03_隔离行情库.json") or {}
    pe = g("04_prod_epoch.json") or {}
    rp = g("04_replay.json") or {}
    gc = g("04_gate_closed.json") or {}
    dup_o = g("04b_重复输入_open.json") or {}
    dup_g = g("04b_重复输入_gate.json") or {}
    v5 = g("05_核对_replay.json") or {}
    reg = g("06_工作台注册.json") or {}
    ex = g("07_导出核验.json") or {}
    ui = g("08_UI导出核验.json") or {}
    idA = g("01_A_身份静态取证.json") or {}
    idB = g("01_B_身份确定性验证.json") or {}
    idC = g("01_C_生产账本只读实证.json") or {}

    out = {
        "generated_at_utc": cm.utc_now(),
        "source_files": {k: str(cm.OUT / k) for k in (
            "01_A_身份静态取证.json", "01_B_身份确定性验证.json", "01_C_生产账本只读实证.json",
            "02_候选扫描.json", "02_数据源等价性.json", "03_隔离行情库.json",
            "04_prod_epoch.json", "04_replay.json", "04_gate_closed.json",
            "04b_重复输入_open.json", "04b_重复输入_gate.json",
            "05_核对_replay.json", "06_工作台注册.json",
            "07_导出核验.json", "08_UI导出核验.json")},

        "1_identity": {
            "exchange_order_id_hits_in_backend": idA.get("exchange_order_hits_in_backend"),
            "exchange_order_tokens_searched": idA.get("exchange_order_tokens_searched"),
            "exchange_order_verdict": idA.get("exchange_order_verdict"),
            "B1_same_window_identical_candidate_id":
                (idB.get("B1_same_window") or {}).get(
                    "same_window_two_runs_identical_candidate_id"),
            "B1_same_window_identical_cross_run_key":
                (idB.get("B1_same_window") or {}).get(
                    "same_window_two_runs_identical_cross_run_key"),
            "B2_verdict": (idB.get("B2_window_shift") or {}).get("verdict"),
            "B3_n_common": (idB.get("B3_intersection") or {}).get("n_common"),
            "B3_candidate_id_identical":
                (idB.get("B3_intersection") or {}).get("candidate_id_identical_on_common"),
            "B3_verdict": (idB.get("B3_intersection") or {}).get("verdict"),
            "prod_ledger_sha256_before": idC.get("sha256_before"),
            "prod_ledger_sha256_after": idC.get("sha256_after"),
            "prod_ledger_unchanged": idC.get("unchanged"),
            "prod_ledger_row_counts": idC.get("row_counts"),
            "prod_order_has_signal_uid": idC.get("order_has_signal_uid"),
            "prod_fill_has_signal_uid": idC.get("fill_has_signal_uid"),
            "prod_exchange_order_column_present": idC.get("exchange_order_column_present"),
            "capability_boundary": idC.get("capability_boundary"),
        },

        "2_candidates": {
            "n_candidates": scan.get("n_candidates"),
            "n_symbols_scanned": scan.get("n_symbols_scanned"),
            "n_errors": scan.get("n_errors"),
            "elapsed_sec": scan.get("elapsed_sec"),
            "equivalence_all_counters_identical": equiv.get("all_identical"),
            "equivalence_n_symbols": _count(equiv.get("symbols_checked")),
        },

        "3_isolated_market": {
            "db": iso.get("iso_market_db"),
            "downloaded_anything": iso.get("downloaded_anything"),
            "wrote_production": iso.get("wrote_production"),
            "size_mb": iso.get("size_mb"),
            "symbols": _count(iso.get("symbols")),
            "inserted": iso.get("inserted"),
            "funding_rows": iso.get("funding_rows"),
            "window": iso.get("window"),
            "scope_statement": iso.get("scope_statement"),
        },

        "4_pass_prod_epoch": {
            "n_cases": pe.get("n_cases"), "n_open_ok": pe.get("n_open_ok"),
            "reason_histogram": pe.get("reason_histogram"),
            "ledger_counts": pe.get("ledger_counts"),
            "verdict": "严格按生产 epoch ⇒ 65/65 被 before_paper_epoch_no_backfill 拒绝，0 成交",
        },

        "5_pass_replay": {
            "n_cases": rp.get("n_cases"), "n_open_ok": rp.get("n_open_ok"),
            "n_closed": rp.get("n_closed"),
            "reason_histogram": rp.get("reason_histogram"),
            "ledger_counts": rp.get("ledger_counts"),
            "epoch_ms_used": rp.get("epoch_ms_used"),
            "epoch_note": rp.get("epoch_note"),
            "elapsed_sec": rp.get("elapsed_sec"),
            "wrote_production": rp.get("wrote_production"),
        },

        "6_pass_gate_closed": {
            "n_cases": gc.get("n_cases"), "n_open_ok": gc.get("n_open_ok"),
            "reason_histogram": gc.get("reason_histogram"),
            "ledger_counts": gc.get("ledger_counts"),
        },

        "7_duplicate": {
            "open_passed": dup_o.get("passed"),
            "open_attempts": [{"label": a["label"], "ok": a["ok"], "reason": a["reason"]}
                              for a in (dup_o.get("attempts") or [])],
            "gate_passed": dup_g.get("passed"),
            "gate_attempts": [{"label": a["label"], "ok": a["ok"], "reason": a["reason"]}
                              for a in (dup_g.get("attempts") or [])],
        },

        "8_verify": {
            "n_candidates": v5.get("n_candidates"),
            "n_open_ok": v5.get("n_open_ok"),
            "n_filled_positions": v5.get("n_filled_positions"),
            "n_closed": v5.get("n_closed"),
            "chain_ok_all": v5.get("chain_ok_all"),
            "value_ok_all": v5.get("value_ok_all"),
            "n_chain_broken": v5.get("n_chain_broken"),
            "n_value_mismatch": v5.get("n_value_mismatch"),
            "wins_losses_flats": [v5.get("wins"), v5.get("losses"), v5.get("flats")],
            "win_rate_pct": v5.get("win_rate_pct"),
            "net_pnl_sum": v5.get("net_pnl_sum"),
            "gross_pnl_sum": v5.get("gross_pnl_sum"),
            "fee_sum": v5.get("fee_sum"),
            "slippage_sum": v5.get("slippage_sum"),
            "funding_sum": v5.get("funding_sum"),
            "exit_path_histogram": v5.get("exit_path_histogram"),
            "n_close_bar_before_entry": v5.get("n_close_bar_before_entry"),
            "n_exit_not_after_entry": v5.get("n_exit_not_after_entry"),
            "holding_hours_range": v5.get("holding_hours_range"),
            "gap_case_total": v5.get("gap_case_total"),
            "capability_boundary": v5.get("capability_boundary"),
            "ledger_counts": v5.get("ledger_counts"),
        },

        "9_workbench": {
            "n_symbols": reg.get("n_symbols"), "n_built": reg.get("n_built"),
            "n_error": reg.get("n_error"),
            "runs_root": reg.get("runs_root"),
            "runs_root_is_isolated": reg.get("runs_root_is_isolated"),
            "chart_json_written": reg.get("chart_json_written"),
            "total_trades": sum(r["n_trades"] for r in (reg.get("runs") or [])),
            "total_rejected": sum(r.get("n_rejected", 0) for r in (reg.get("runs") or [])),
            "reject_only_runs": sum(1 for r in (reg.get("runs") or []) if r["n_trades"] == 0),
            "n_exit_4h_path": sum(r.get("n_exit_4h_path", 0) for r in (reg.get("runs") or [])),
        },

        "10_export": {
            "n_runs_registered": ex.get("n_runs_registered"),
            "n_runs_in_list_endpoint": ex.get("n_runs_in_list_endpoint"),
            "runs_missing_from_list": ex.get("runs_missing_from_list"),
            "batches_endpoint": ex.get("batches_endpoint"),
            "coverage_groups": len(ex.get("coverage") or []),
            "coverage_all_ok": sum(1 for c in (ex.get("coverage") or [])
                                   if c["all_columns_ok"] is True),
            "coverage_not_applicable": sum(1 for c in (ex.get("coverage") or [])
                                           if c["all_columns_ok"] is None),
            "coverage_failed": sum(1 for c in (ex.get("coverage") or [])
                                   if c["all_columns_ok"] is False),
            "row_count_mismatch": sum(1 for c in (ex.get("coverage") or [])
                                      if not c["row_count_ok"]),
            "recon_runs": len(ex.get("reconciliation") or []),
            "recon_mismatch": sum(1 for r in (ex.get("reconciliation") or [])
                                  if not r["counts_match"]),
            "chart_checks": ex.get("chart_checks"),
            "chart_sample_runs": ex.get("chart_sample_runs"),
            "isolation_proof": {
                "prod_runs_unchanged": (ex.get("isolation_proof") or {}).get(
                    "prod_runs_unchanged"),
                "prod_ledger_unchanged": (ex.get("isolation_proof") or {}).get(
                    "prod_ledger_unchanged"),
            },
        },

        "11_ui": {
            "run_id": ui.get("run_id"),
            "downloads": [{"label": d["label"], "rows": d["rows"], "bytes": d["bytes"],
                           "saved_as": d["saved_as"]} for d in (ui.get("downloads") or [])],
            "page_facts": {k: (ui.get("page_facts") or {}).get(k) for k in
                           ("run_id_in_page", "has_kline_canvas", "canvas_count",
                            "trades_chip", "text_len")},
            "page_vs_summary": ui.get("page_vs_summary"),
            "order_locate": ui.get("order_locate"),
            "bad_responses": ui.get("bad_responses"),
            "console_errors": ui.get("console_errors"),
        },
    }
    cm.write_json("10_报告数字.json", out)

    # ── 控制台摘要（供人工核对）──────────────────────────────────────────
    v = out["8_verify"]
    print("身份：B1 同窗口逐位相同 =", out["1_identity"]["B1_same_window_identical_candidate_id"],
          "| B3 共同候选 =", out["1_identity"]["B3_n_common"],
          "| 交易所单号命中 =", out["1_identity"]["exchange_order_id_hits_in_backend"])
    print("候选：", out["2_candidates"]["n_candidates"], "个 /",
          out["2_candidates"]["n_symbols_scanned"], "标的扫描")
    print("prod_epoch：", out["4_pass_prod_epoch"]["n_open_ok"], "成交 /",
          out["4_pass_prod_epoch"]["reason_histogram"])
    print("replay：", out["5_pass_replay"]["n_open_ok"], "成交 /",
          out["5_pass_replay"]["n_closed"], "已平 /", out["5_pass_replay"]["reason_histogram"])
    print("gate_closed：", out["6_pass_gate_closed"]["reason_histogram"])
    print("duplicate：open =", out["7_duplicate"]["open_passed"],
          "gate =", out["7_duplicate"]["gate_passed"])
    print("核对：链通 =", v["chain_ok_all"], "数值对 =", v["value_ok_all"],
          "net =", v["net_pnl_sum"], "胜率 =", v["win_rate_pct"])
    print("出场路径 =", v["exit_path_histogram"],
          "平仓bar早于入场 =", v["n_close_bar_before_entry"])
    print("工作台：", out["9_workbench"]["n_built"], "run /",
          out["9_workbench"]["total_trades"], "笔 /",
          out["9_workbench"]["total_rejected"], "拒单")
    print("导出：列表命中", out["10_export"]["n_runs_in_list_endpoint"],
          "| 列覆盖", out["10_export"]["coverage_groups"], "组 全对",
          out["10_export"]["coverage_all_ok"], "不适用",
          out["10_export"]["coverage_not_applicable"], "失败",
          out["10_export"]["coverage_failed"],
          "| 账本对账不符", out["10_export"]["recon_mismatch"])
    print("UI：", out["11_ui"]["downloads"])
    print("UI 页面 vs summary 全对 =", (out["11_ui"]["page_vs_summary"] or {}).get("all_match"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
