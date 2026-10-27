# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 02b · 把重放结果物化成独立 run 目录

工单要求「输出独立 run 目录，不覆盖原账本」。
本脚本不重跑任何回测，只把 02_progress.jsonl 里已有的重放结果按 run 拆成目录：

    evidence/ENTRY-AUDIT-03/runs/<generation>_<safe_id>/
        run.json         该 run 的规格资格 / 执行有效性 / 身份链 / 成本合计
        candidates.csv   该 run 的逐候选身份结果
        trades.csv       该 run 的逐笔成交（引擎原值）
        rejects.jsonl    该 run 的原始拒单事件

写入边界：只在 evidence/ENTRY-AUDIT-03/runs/ 下，绝不写 回测日志/ 或任何账本目录。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-03"
RUNS = OUT / "runs"
PROG = OUT / "02_progress.jsonl"


def safe(tid: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", tid)


def main() -> None:
    # ⚠ 不删目录：批量删除既危险也会被安全策略拦。
    #   任务集固定，直接按目录覆盖写即可；多余的旧目录只在 index 里标注，不动。
    RUNS.mkdir(parents=True, exist_ok=True)

    results = [json.loads(l) for l in PROG.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    for r in results:
        t = r["task"]
        d = RUNS / safe(t["task_id"])
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "audit": "ENTRY-AUDIT-03",
            "task_id": t["task_id"],
            "generation": t["generation"],
            "symbol": t["symbol"],
            "group": t.get("group"),
            "window_start": t["start"],
            "window_end": t["end"],
            "status": r["status"],
            "error": r.get("error", ""),
            "run": {k: v for k, v in (r.get("run") or {}).items() if k != "candidate_ms_list"},
            "candidate_ms_list": (r.get("run") or {}).get("candidate_ms_list"),
            "counts": {"candidates": len(r.get("candidates") or []),
                       "trades": len(r.get("trades") or []),
                       "rejects": len(r.get("rejects") or [])},
            "write_boundary": "本目录为审计独立产物；未写入 回测日志/ 或任何账本目录",
        }
        (d / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if r.get("candidates"):
            pd.DataFrame(r["candidates"]).to_csv(d / "candidates.csv", index=False,
                                                 encoding="utf-8-sig")
        if r.get("trades"):
            pd.DataFrame(r["trades"]).to_csv(d / "trades.csv", index=False, encoding="utf-8-sig")
        if r.get("rejects"):
            with open(d / "rejects.jsonl", "w", encoding="utf-8") as f:
                for e in r["rejects"]:
                    f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        n += 1

    index = {
        "audit": "ENTRY-AUDIT-03",
        "run_dirs_written": n,
        "root": "evidence/ENTRY-AUDIT-03/runs/",
        "tasks": [{"task_id": r["task"]["task_id"],
                   "dir": f"runs/{safe(r['task']['task_id'])}",
                   "status": r["status"],
                   "candidates": len(r.get("candidates") or []),
                   "trades": len(r.get("trades") or [])} for r in results],
    }
    # 目录里若还有不在本次任务集内的旧目录，如实列出（不删除）
    written = {safe(r["task"]["task_id"]) for r in results}
    extra = sorted(p.name for p in RUNS.iterdir()
                   if p.is_dir() and p.name not in written)
    index["orphan_dirs_not_written_this_run"] = extra
    (RUNS / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已物化 {n} 个独立 run 目录 → {RUNS}"
          + (f"（另有 {len(extra)} 个旧目录未动）" if extra else ""))


if __name__ == "__main__":
    main()
