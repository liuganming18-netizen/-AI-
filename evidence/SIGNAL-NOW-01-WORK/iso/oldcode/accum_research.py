# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜三个研究入口的配置与校验（只做接入，不做自动开仓）。

三条不可越的线
--------------
1. **未批准参数一律 pending**：本模块不提供任何「兜底默认值」。`validate()` 只要
   遇到 pending 参数就返回 `ready=False`，`can_open_position()` 恒为 `False`。
   想开仓必须先有人把 pending 改成 approved 并写明批准依据。
2. **不冒称策略编号**：`4a/4b/4c` 只是**避免与退役臂冲突的工程命名**，不是用户
   批准的策略编号。真实策略身份在 `strategy_id` 里写 `ACCUM-RESEARCH-v0`（研究
   代号），并标 `approved=False`。
3. **不覆盖退役账本**：`3a/3b/3c/3d` 属已退役旧策略，本模块拒绝为其建账。

复用了什么、没复用什么（防止「大箱体突破」被当成「局部提前入场」已实现）
----------------------------------------------------------------------
复用：15 分钟/1 小时/4 小时行情读取、事件状态机容器、成交模型、成本模型、账本结构。
**未复用**：`BOX → SWEEP(跌破箱体下沿) → RETEST → 突破上沿` 的触发条件。
三组方案研究的是「扫低收回后的回踩转强」，其局部压制位、回踩确认、卖压减弱基准
都还没有批准定义 ⇒ 只建入口，不宣称已实现。

成本模型冲突（不择一）
----------------------
· 策略包 `EventTrendConfig`：手续费 5bps、滑点 3bps
· 生效视图 `10_研究参数生效视图_v2_2_user5000_shared.json`：手续费 5bps、滑点 5bps
本模块**同时**返回两套，并在报告里并列，不替任何人挑一个。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

RESEARCH_ROOT = (Path(__file__).resolve().parents[2] / "数据" / "research"
                 / "ACCUM-EARLY-01")
LEDGER_DIR = RESEARCH_ROOT / "ledger"

RETIRED_ARMS = ("3a", "3b", "3c", "3d")

SCHEMES: Dict[str, dict] = {
    "4a": {
        "engineering_name": "4a",
        "label": "甲",
        "direction": "回踩后突破局部压制，一小时收盘确认",
        "strategy_id": "ACCUM-RESEARCH-v0",
        "approved": False,
        "confirm_rule": "1h_close_above_local_resistance",
    },
    "4b": {
        "engineering_name": "4b",
        "label": "乙",
        "direction": "突破后下一根一小时收盘仍守住同一局部压制位",
        "strategy_id": "ACCUM-RESEARCH-v0",
        "approved": False,
        "confirm_rule": "next_1h_close_holds_above_local_resistance",
    },
    "4c": {
        "engineering_name": "4c",
        "label": "丙",
        "direction": "突破后等待回抽，再由一小时收盘确认转强",
        "strategy_id": "ACCUM-RESEARCH-v0",
        "approved": False,
        "confirm_rule": "pullback_then_1h_close_turns_up",
    },
}

#: 待定参数：value 一律 None，status 一律 pending，**没有默认值**
PENDING_PARAMS: Dict[str, dict] = {
    "local_resistance_definition": {
        "status": "pending", "value": None,
        "question": "局部压制位怎么取（前 N 根高点？箱体内分位？）",
        "approved_basis": None, "blocks": "全部三组的入场触发",
    },
    "local_resistance_confirm": {
        "status": "pending", "value": None,
        "question": "局部压制位用收盘确认还是盘中触及",
        "approved_basis": None, "blocks": "全部三组",
    },
    "sweep_bars": {
        "status": "pending", "value": None,
        "question": "扫低收回要求单根完成还是多根完成",
        "approved_basis": "冻结规则为单根收盘回收（仅对原策略）",
        "blocks": "事件链起点",
    },
    "sell_pressure_baseline": {
        "status": "pending", "value": None,
        "question": "卖压减弱与主动买入放大的比较基准（V20？近 N 根？）",
        "approved_basis": None, "blocks": "甲/乙/丙的证据判定",
    },
    "sell_pressure_threshold": {
        "status": "pending", "value": None,
        "question": "卖压减弱与主动买入放大的阈值",
        "approved_basis": None, "blocks": "甲/乙/丙",
    },
    "observe_deadline_hours": {
        "status": "pending", "value": None,
        "question": "观察期限（小时）",
        "approved_basis": "原策略 336 小时，仅对原策略", "blocks": "事件生命周期",
    },
    "pullback_deadline_hours": {
        "status": "pending", "value": None,
        "question": "回抽等待期限（小时）",
        "approved_basis": None, "blocks": "丙",
    },
    "intrabar_break_cancel": {
        "status": "pending", "value": None,
        "question": "盘中跌破是否即时取消，还是只看收盘",
        "approved_basis": "原策略按收盘，仅对原策略", "blocks": "候选取消",
    },
    "entry_distance_limit": {
        "status": "pending", "value": None,
        "question": "入场距离与价格偏离上限",
        "approved_basis": "参与率上限 0.1% 属原策略", "blocks": "成交可行性",
    },
    "oi_necessity": {
        "status": "pending", "value": None,
        "question": "持仓量是必要条件还是辅助证据",
        "approved_basis": None, "blocks": "全部门槛",
    },
    "repeat_candidate_limit": {
        "status": "pending", "value": None,
        "question": "同事件重复候选与再次入场限制",
        "approved_basis": "原策略每标的每场所最多一个观察事件", "blocks": "候选数量",
    },
    "risk_budget_new_scheme": {
        "status": "pending", "value": None,
        "question": "新方案用哪套仓位与风险上限（3 仓还是 10 仓，见冲突）",
        "approved_basis": None,
        "blocks": "开仓（不阻塞数据、记录、隔离验证）",
    },
    "slippage_source": {
        "status": "pending", "value": None,
        "question": "滑点取 3bps（策略包）还是 5bps（生效视图）",
        "approved_basis": None, "blocks": "成本核算与盈亏口径",
    },
}

#: 成本模型两套并列，不择一
COST_MODELS = {
    "strategy_package": {
        "source": "策略/WyckoffMaster/event_trend_v0_1/config.py :: EventTrendConfig",
        "taker_fee_bps": 5.0, "slippage_bps": 3.0,
    },
    "effective_view": {
        "source": "数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/"
                  "5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json",
        "taker_fee_bps": 5.0, "slippage_bps": 5.0,
    },
}

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS research_account (
    scheme TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    approved INTEGER NOT NULL,
    created_ms INTEGER NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS research_order_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,           -- blocked / would_open / would_close
    reason TEXT NOT NULL,
    at_ms INTEGER NOT NULL,
    payload_json TEXT
);
CREATE TABLE IF NOT EXISTS research_fill (
    fill_id TEXT PRIMARY KEY,
    scheme TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL, price REAL,
    fee_bps REAL, slip_bps REAL,
    at_ms INTEGER NOT NULL,
    cost_model TEXT NOT NULL,     -- strategy_package / effective_view
    origin TEXT NOT NULL          -- replay / isolated_sample / live_recovery
);
"""


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def ledger_path(scheme: str) -> Path:
    if scheme in RETIRED_ARMS:
        raise ValueError(
            f"拒绝为退役臂 {scheme} 建账：3a–3d 属已退役旧策略，"
            f"写入会覆盖历史口径")
    if scheme not in SCHEMES:
        raise ValueError(f"未知研究入口 {scheme}；只允许 {sorted(SCHEMES)}")
    return LEDGER_DIR / f"paper_{scheme}.sqlite3"


def open_ledger(scheme: str) -> sqlite3.Connection:
    p = ledger_path(scheme)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.executescript(LEDGER_DDL)
    con.execute(
        "INSERT OR IGNORE INTO research_account(scheme, strategy_id, approved,"
        " created_ms, note) VALUES(?,?,?,?,?)",
        (scheme, SCHEMES[scheme]["strategy_id"], 0, _now_ms(),
         "工程命名，非批准策略编号；未获批准前不得开仓"))
    con.commit()
    return con


def pending_params() -> List[str]:
    return [k for k, v in PENDING_PARAMS.items() if v["status"] == "pending"]


def validate(scheme: str) -> dict:
    """配置校验。pending 未清空 ⇒ ready=False，且给出逐条阻塞原因。"""
    if scheme not in SCHEMES:
        raise ValueError(f"未知研究入口 {scheme}")
    pend = pending_params()
    blocked = [{"param": k, "question": PENDING_PARAMS[k]["question"],
                "approved_basis": PENDING_PARAMS[k]["approved_basis"],
                "blocks": PENDING_PARAMS[k]["blocks"]} for k in pend]
    return {
        "scheme": scheme,
        "label": SCHEMES[scheme]["label"],
        "direction": SCHEMES[scheme]["direction"],
        "strategy_id": SCHEMES[scheme]["strategy_id"],
        "approved": False,
        "ready": len(pend) == 0,
        "pending_count": len(pend),
        "blocked_by": blocked,
        "cost_models": COST_MODELS,
        "cost_model_conflict": True,
        "reuse": {
            "reused": ["行情读取（15m/1h/4h）", "事件状态机容器", "成交模型",
                       "成本模型（两套并列）", "账本结构"],
            "not_reused": ["BOX→SWEEP(跌破下沿)→RETEST→突破上沿 的触发条件"],
            "note": "局部压制位、回踩确认、卖压基准均未批准 ⇒ 未实现，不得宣称已实现",
        },
        "auto_entry_allowed": False,
        "auto_entry_reason": "存在未批准参数 ⇒ 禁止用默认值开仓",
    }


def can_open_position(scheme: str) -> dict:
    """恒 False：只要还有 pending 参数就不许开仓。"""
    pend = pending_params()
    if pend:
        return {"allowed": False,
                "reason": "pending_params_present",
                "pending": pend,
                "note": "数据、记录、隔离验证不受影响；只阻塞开仓"}
    return {"allowed": bool(SCHEMES[scheme]["approved"]),
            "reason": "approved" if SCHEMES[scheme]["approved"] else "scheme_not_approved"}


def log_blocked(scheme: str, symbol: str, kind: str, reason: str,
                payload=None) -> None:
    import json
    con = open_ledger(scheme)
    try:
        con.execute(
            """INSERT INTO research_order_log(scheme, symbol, kind, reason,
                   at_ms, payload_json) VALUES(?,?,?,?,?,?)""",
            (scheme, symbol, kind, reason, _now_ms(),
             json.dumps(payload or {}, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def isolation_report() -> dict:
    """三组隔离证据：账本路径、是否同一文件、是否触碰退役账本。"""
    out = {"ledgers": {}, "all_distinct": True, "touches_retired": False,
           "research_root": str(RESEARCH_ROOT)}
    seen = set()
    for s in SCHEMES:
        p = ledger_path(s).resolve()
        out["ledgers"][s] = {"path": str(p), "exists": p.exists(),
                             "size": (p.stat().st_size if p.exists() else 0)}
        if p in seen:
            out["all_distinct"] = False
        seen.add(p)
        if f"paper_{s}" in ("paper_3a", "paper_3b", "paper_3c", "paper_3d"):
            out["touches_retired"] = True
    return out
