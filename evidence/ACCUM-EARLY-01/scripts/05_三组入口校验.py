# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜05 三个研究入口的配置校验与账本隔离。

校验：pending 参数一律拒跑、两套成本模型并列不择一、三本账互不相同、
不得为退役臂建账、不把「大箱体突破」当成「局部提前入场」已实现。
输出：evidence/ACCUM-EARLY-01/out/05_三组入口校验.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main() -> int:
    import accum_research as A
    rep = {"probe": "05_三组入口校验", "generated_at_utc": iso(time.time() * 1000),
           "schemes": {}, "errors": []}

    for s in A.SCHEMES:
        rep["schemes"][s] = A.validate(s)
        rep["schemes"][s]["can_open_position"] = A.can_open_position(s)

    rep["cost_model_conflict"] = {
        "两套并列（不择一）": A.COST_MODELS,
        "冲突点": "滑点 3bps（策略包）vs 5bps（生效视图）；手续费两处一致 5bps",
        "处置": "报告并列两套；不替任何人挑一个；改口径需裁决",
        "执行链实际读的是": "生效视图（paper_engine.approved_params 的 _source）",
    }
    rep["isolation"] = A.isolation_report()
    try:
        A.ledger_path("3b")
        rep["retired_arm_blocked"] = False
    except ValueError as exc:
        rep["retired_arm_blocked"] = True
        rep["retired_arm_message"] = str(exc)

    rep["auto_entry"] = {
        "allowed": False,
        "reason": "pending_params_present",
        "pending_count": len(A.pending_params()),
        "说明": "未批准参数存在即禁止开仓；数据、记录、隔离验证不受影响",
    }
    rep["not_implemented"] = A.validate("4a")["reuse"]

    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "05_三组入口校验.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "ready": {s: rep["schemes"][s]["ready"] for s in A.SCHEMES},
        "pending": rep["auto_entry"]["pending_count"],
        "auto_entry": rep["auto_entry"]["allowed"],
        "retired_blocked": rep["retired_arm_blocked"],
        "ledgers_distinct": rep["isolation"]["all_distinct"],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
