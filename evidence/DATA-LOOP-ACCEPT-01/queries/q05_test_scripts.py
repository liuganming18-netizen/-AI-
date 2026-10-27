# -*- coding: utf-8 -*-
"""q05｜测试脚本是否「直接推进交易」的静态取证（只读）。

判据（工单第 3 项）：测试脚本只能驱动运行时/API，不得绕过运行时直接调用撮合与账本写入。
被检查的写入口（来自 paper_engine 的公开函数，逐项在源码里核对）：
  place_order / open_position / close_position / match_pending / execute_pending
  / apply_fill / _write_fill / insert_fill / append_equity / save_state
命中方式：扫描 tests/ 与 evidence/ 下 *.py，统计对 paper_engine（或 PE）这些符号的调用，
并区分「调用 PE 的只读函数」（如 approved_params / new_entry_gate / account_view）与「写函数」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"

WRITE_SYMBOLS = [
    "place_order", "open_position", "close_position", "match_pending", "execute_pending",
    "apply_fill", "insert_fill", "append_equity", "save_state", "record_fill",
    "process_signal", "handle_signal", "submit_order", "do_open", "do_close",
]
READ_SYMBOLS = ["approved_params", "new_entry_gate", "account_view", "equity_snapshot"]

PAT = re.compile(r"(?:PE|paper_engine|pe)\.(" + "|".join(WRITE_SYMBOLS + READ_SYMBOLS) + r")\s*\(")


def scan(root: Path):
    hits = []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "paper_engine" not in text and "import PE" not in text and "as PE" not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = PAT.search(line)
            if m:
                kind = "WRITE" if m.group(1) in WRITE_SYMBOLS else "READ"
                hits.append({"file": str(p.relative_to(ROOT)), "line": i, "symbol": m.group(1),
                             "kind": kind, "text": line.strip()[:180]})
    return hits


def main():
    tests = scan(ROOT / "框架" / "backend" / "tests")
    evid = scan(ROOT / "evidence")
    # 只看与纸面/闭环相关的证据脚本，避免把历史无关批次算进来
    evid_rel = [h for h in evid if re.search(r"(?i)paper|loop|ui-link|accept|isolation|closeloop", h["file"])]
    out = {
        "write_symbols": WRITE_SYMBOLS,
        "tests_hits": tests,
        "evidence_hits": evid_rel,
        "tests_write_calls": [h for h in tests if h["kind"] == "WRITE"],
        "evidence_write_calls": [h for h in evid_rel if h["kind"] == "WRITE"],
        "summary": {
            "tests_files_with_hits": len({h["file"] for h in tests}),
            "tests_write_calls": sum(1 for h in tests if h["kind"] == "WRITE"),
            "evidence_files_with_hits": len({h["file"] for h in evid_rel}),
            "evidence_write_calls": sum(1 for h in evid_rel if h["kind"] == "WRITE"),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "q05_test_scripts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("--- WRITE 调用明细（tests）---")
    for h in out["tests_write_calls"]:
        print(f"{h['file']}:{h['line']} {h['symbol']}  {h['text'][:120]}")
    print("--- WRITE 调用明细（evidence）---")
    for h in out["evidence_write_calls"][:40]:
        print(f"{h['file']}:{h['line']} {h['symbol']}  {h['text'][:120]}")


if __name__ == "__main__":
    main()
