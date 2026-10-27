# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 00：依据取证 + 修前身份 + 静态调用点 + 修前调用轨迹

本脚本回答三个问题，全部用「读文件 / 解析 AST / 跑状态机」得出，不抄结论：

  Q1 承接判定的时点依据在哪？——逐条给出文件+行号+原文，并**当场读回该行校验**。
     06-01（窗口一）未交付 ⇒ 本窗直取原文；发现矛盾的项单独标 `blocked_item`。
  Q2 修前 `_absorption` 被谁调用？——AST 找调用点 + 所属函数，再与运行时轨迹对撞。
  Q3 修前状态机实际读的是哪一根 bar？——包装 `_absorption`，记录 `i` 与
     `bar_close_ms`，跑完整 `SchemeObserver.on_bar` 序列。

产物：out/00_basis.json、out/00_pre_fix_trace.json
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402

AEE_PATH = W.BACKEND / "accum_early_entry.py"

# ══════════════════════════════════════════════════════════════════════════
# Q1 依据：每条都给 (文件, 行号, 必须命中的原文片段)
# ══════════════════════════════════════════════════════════════════════════
BASIS = [
    {"id": "B1", "claim": "承接判定的判断时点 = 扫低根 1h 收盘",
     "file": "evidence/ACCUM-EARLY-04-A/README.md", "line": 53,
     "must_contain": "扫低根 1h 收盘",
     "kind": "方案文本（04-A 规则接口的可得时点表）"},
    {"id": "B2", "claim": "承接基准窗口 = 扫低根之前 20 根 1h 的中位数",
     "file": "evidence/ACCUM-EARLY-04-A/02_规则接口.md", "line": 134,
     "must_contain": "扫低根之前 20 根 1h 的中位数",
     "kind": "方案文本（参数表 absorption_baseline_bars）"},
    {"id": "B3", "claim": "承接是硬门槛（不满足不进 ARMED）",
     "file": "evidence/ACCUM-EARLY-04-A/03_决策表.md", "line": 28,
     "must_contain": "承接条件是**硬门槛**",
     "kind": "方案文本（决策表第 6 项）"},
    {"id": "B4", "claim": "参考实现在**扫低成立的那一根**就调用 absorption 并把不通过记为"
                          " SWEEP_REJECTED（不进 SWEEP）",
     "file": "evidence/ACCUM-EARLY-04-A/rules/accum_early_rules.py", "line": 1065,
     "must_contain": "ab = absorption(bars_1h, i, p)",
     "kind": "方案文本（04-A 参考实现）"},
    {"id": "B5", "claim": "扫低与收回必须在同一根 1h 内完成（single_bar）",
     "file": "evidence/ACCUM-EARLY-04-A/02_规则接口.md", "line": 128,
     "must_contain": "扫低与收回必须在**同一根** 1h 内完成",
     "kind": "方案文本（参数表 sweep_recovery_scope，未批准）"},
    {"id": "B6", "claim": "sell_pressure.require 仍是「建议未批准」",
     "file": "evidence/ACCUM-EARLY-05-A-R1/03_完整方案参数附件表.md", "line": 48,
     "must_contain": "shared.sell_pressure.require",
     "kind": "批准状态表"},
    {"id": "B7", "claim": "局部压制位也定义为「扫低根之前 48 根 1h」"
                          "（本单不修，只交证据）",
     "file": "evidence/ACCUM-EARLY-04-A/README.md", "line": 52,
     "must_contain": "扫低根之前 48 根 1h",
     "kind": "方案文本（可得时点表）"},
]


def check_basis() -> list:
    rows = []
    for b in BASIS:
        p = W.PROJECT / b["file"]
        lines = p.read_text(encoding="utf-8").splitlines()
        got = lines[b["line"] - 1] if 0 < b["line"] <= len(lines) else ""
        rows.append({**b, "line_text": got.strip()[:400],
                     "verified": b["must_contain"] in got})
    return rows


# ══════════════════════════════════════════════════════════════════════════
# Q2 静态调用点：AST 找 `_absorption` 的定义与全部调用点所属函数
# ══════════════════════════════════════════════════════════════════════════
def ast_call_sites(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in
            ("_absorption", "_sweep_check", "_confirm_actions", "on_bar", "_resistance")]

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []
            self.sites = []

        def visit_FunctionDef(self, node):                       # noqa: N802
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):                              # noqa: N802
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name in ("_absorption", "_resistance", "_sweep_check"):
                self.sites.append({
                    "callee": name, "line": node.lineno,
                    "enclosing": list(self.stack),
                    "arg_i_repr": (ast.unparse(node.args[1]) if len(node.args) > 1 else None),
                    "n_args": len(node.args),
                })
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    return {"definitions": {d.name: d.lineno for d in defs}, "call_sites": v.sites}


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    # 防篡改：本脚本只在**修前代码**上有意义。产物已存在且记录的修前 sha 与当前不符
    # ⇒ 说明代码已改，拒绝覆盖（否则「修前轨迹」会被修后结果悄悄替换）。
    prev = W.load_out("00_basis_and_pre_fix_trace.json")
    cur_sha = W.sha256_file(AEE_PATH)["sha256"]
    if prev and prev.get("code_before_fix", {}).get("sha256") != cur_sha:
        print(json.dumps({"skipped": True,
                          "why": "当前代码已不是修前版本，保留既有修前轨迹，未覆盖",
                          "recorded_pre_fix_sha256": prev["code_before_fix"]["sha256"],
                          "current_sha256": cur_sha}, ensure_ascii=False))
        return 0
    out = {"stats_at_utc": W.iso(int(__import__("time").time() * 1000)),
           "code_before_fix": W.sha256_file(AEE_PATH),
           "code_mtime_utc": W.iso(int(AEE_PATH.stat().st_mtime * 1000)),
           "version": W.AEE.VERSION, "config_schema": W.AEE.CONFIG_SCHEMA,
           "rule_version": W.AEE.RULE_VERSION}

    # 06-01 是否已交付（决定依据是「引用 06-01」还是「本窗直取原文」）
    w1 = W.PROJECT / "evidence" / "ACCUM-EARLY-06-W1"
    out["upstream_06_01"] = {
        "dir": str(w1), "delivered": w1.exists(),
        "note": ("06-01（窗口一：规则来源矩阵）未交付 ⇒ 本单依据改为**直取原始文档**"
                 "并逐条读回校验；不引用不存在的上游产物。")}

    # ── Q1 ────────────────────────────────────────────────────────────────
    basis = check_basis()
    out["basis"] = basis
    out["basis_all_verified"] = all(b["verified"] for b in basis)
    # 冲突项判定：single_bar 未批准 + 多根口径无原文 ⇒ 只对「多根」这一情形报未验证
    out["blocked_items"] = [{
        "item": "sweep_recovery_scope = multi_bar 时的「扫低根」",
        "why": ("B5 只规定了 single_bar（同根完成）；多根完成时「扫低根」取最低价那根"
                "还是收回那根，原文没有规定，且该档未批准 ⇒ 不猜。"),
        "blocked_scope": "仅该项；single_bar 路径不受影响"}]
    out["no_conflict_items"] = [
        "承接时点=扫低根 1h 收盘（B1/B4 一致）",
        "基准窗口=扫低根之前 20 根（B2 唯一口径）",
        "承接是硬门槛（B3/B6 一致：门槛存在但倍率未批准）"]

    # ── Q2 ────────────────────────────────────────────────────────────────
    sites = ast_call_sites(AEE_PATH)
    out["static"] = sites
    by_callee = {}
    for s in sites["call_sites"]:
        by_callee.setdefault(s["callee"], []).append(s)
    out["static_summary"] = {
        "absorption_enclosing": [s["enclosing"] for s in by_callee.get("_absorption", [])],
        "absorption_in_sweep_check": any("_sweep_check" in s["enclosing"]
                                         for s in by_callee.get("_absorption", [])),
        "absorption_in_confirm_actions": any("_confirm_actions" in s["enclosing"]
                                             for s in by_callee.get("_absorption", [])),
        "absorption_in_on_bar": any("on_bar" in s["enclosing"]
                                    for s in by_callee.get("_absorption", [])),
        "absorption_arg_i": [s["arg_i_repr"] for s in by_callee.get("_absorption", [])],
    }

    # ── Q3 运行时轨迹（修前）───────────────────────────────────────────────
    cfg = W.load_fixture_config()
    out["config"] = {k: cfg.get(k) for k in
                     ("path", "kind", "ready", "open_allowed", "open_scope",
                      "config_sha256", "blocked_by")}
    runs = {}
    for name, spec in W.CASES.items():
        bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"])
        r = W.run_observer(name, bars, cfg, tag="00_" + name[:1])
        runs[name] = {
            "n_candidates": r["n_candidates"], "final_state": r["final_state"],
            "closed_reason": r["closed_reason"],
            "absorption_calls": [c for c in r["trace"] if c["fn"] == "absorption"],
            "sweep_bar_i": W.SWEEP_I, "confirm_bar_i": W.CONFIRM_I,
            "sweep_bar_close_utc": W.iso(bars[W.SWEEP_I]["close_time"]),
            "confirm_bar_close_utc": W.iso(bars[W.CONFIRM_I]["close_time"]),
            "transitions": [{"to": t["to_state"], "at": W.iso(t["at_ms"]),
                             "reason": t["reason"]} for t in r["transitions"]],
        }
    out["runtime_before_fix"] = runs

    # 修前「读的是哪一根」的结论必须由轨迹推出，不靠注释
    reads = {n: sorted({c["i"] for c in v["absorption_calls"]})
             for n, v in runs.items()}
    out["before_fix_reads_bar_index"] = reads
    out["before_fix_reads_sweep_bar"] = {n: (W.SWEEP_I in idx) for n, idx in reads.items()}
    out["before_fix_reads_confirm_bar"] = {n: (W.CONFIRM_I in idx) for n, idx in reads.items()}

    W.dump("00_basis_and_pre_fix_trace.json", out)

    print("basis_verified =", out["basis_all_verified"])
    print("static:", json.dumps(out["static_summary"], ensure_ascii=False))
    print("before_fix_reads_bar_index =", json.dumps(reads, ensure_ascii=False))
    print("candidates_before_fix =", json.dumps(
        {n: v["n_candidates"] for n, v in runs.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
