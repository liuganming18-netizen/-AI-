# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜观察事件与候选的持久化（研究库，与生产库物理隔离）。

为什么要单独一个库
------------------
生产 `框架/backend/live/live_signals.sqlite3` 里的 `live_signal` 是**原策略**的
信号账，任何研究写入都会污染原策略统计。本模块把观察事件、候选、状态迁移、
数据缺口全部写进**独立研究库**
`数据/research/ACCUM-EARLY-01/research_observe.sqlite3`，
不建外键到生产库、不读生产库。

三条硬规则（用 SQLite 触发器强制，不靠调用方自觉）
--------------------------------------------------
1. **初始锚点冻结**：`old_low` / `sweep_low` / `vwap_anchor_ms` /
   `vwap_anchor_source` / `structure_seen_ms` / `expires_at_ms` 建档后不可改。
   任何 UPDATE 触碰这些列 → 触发器 `ABORT`。
2. **候选不覆盖**：候选表只允许 INSERT；同一事件再次产生候选时 `seq_in_event+1`。
   修改候选状态只允许改 `state` / `cancelled_ms` / `cancel_reason`，其余列冻结。
3. **观察期限不重置**：重复扫描命中同一 `(scheme, symbol, box_version_id)` 时
   返回既有 `event_id`，**不更新** `expires_at_ms`；重复扫描也不产生新事件。

状态口径（数据不足 ≠ 形态失败）
------------------------------
`state` 取值：
  BOX / SWEEP / RETEST / CANDIDATE   —— 与现有状态机同名，直接映射
  DATA_INSUFFICIENT                 —— 研究侧独立状态，**不映射**为 INVALIDATED
  INVALIDATED / EXPIRED / CLOSED    —— 与现有状态机同名
映射说明：`DATA_INSUFFICIENT` 是**新增**的研究侧状态，生产状态机里没有对应项；
若将来要合流，需要生产侧新增同名状态，属于改冻结包，未获批准前只在本库存在。

对外接口
--------
    st = ObserveStore(path=None)          # 默认研究库路径
    ev = st.create_event(...)              # 幂等，返回既有或新建
    cd = st.add_candidate(event_id, ...)   # 只新增，不覆盖
    st.transition(event_id, from_, to_, reason, ...)
    st.close_event(event_id, state, reason)
    st.add_data_gap(event_id, field, from_ms, to_ms, reason)
    st.dump_event(event_id)                # 逐事件证据链
    st.assert_isolated()                   # 断言没有指向生产库
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

RESEARCH_DB = (Path(__file__).resolve().parents[2] / "数据" / "research"
               / "ACCUM-EARLY-01" / "research_observe.sqlite3")

PRODUCTION_DBS = (
    Path(__file__).resolve().parents[2] / "框架" / "backend" / "live" / "live_signals.sqlite3",
    Path(__file__).resolve().parents[2] / "数据" / "paper" / "paper_3a.sqlite3",
)

STATES = ("BOX", "SWEEP", "RETEST", "CANDIDATE", "DATA_INSUFFICIENT",
          "INVALIDATED", "EXPIRED", "CLOSED")

FROZEN_EVENT_COLUMNS = ("old_low", "sweep_low", "vwap_anchor_ms",
                        "vwap_anchor_source", "structure_seen_ms",
                        "expires_at_ms", "box_version_id")

DDL = """
CREATE TABLE IF NOT EXISTS observe_event (
    event_id            TEXT PRIMARY KEY,
    scheme              TEXT NOT NULL,          -- 研究方案：jia/yi/bing 或 4a/4b/4c
    symbol              TEXT NOT NULL,
    venue               TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    param_version       TEXT NOT NULL,
    box_version_id      TEXT NOT NULL,
    old_low             REAL,
    sweep_low           REAL,
    vwap_anchor_ms      INTEGER,
    vwap_anchor_source  TEXT,
    structure_seen_ms   INTEGER NOT NULL,       -- 结构实际出现时间（行情时间）
    confirmed_ms        INTEGER NOT NULL,       -- 系统确认时间
    expires_at_ms       INTEGER NOT NULL,       -- 观察期限，建档后冻结
    state               TEXT NOT NULL,
    closed_reason       TEXT,
    data_quality        TEXT,                   -- ok / partial / missing
    evidence_json       TEXT,
    counter_evidence_json TEXT,
    missing_evidence_json TEXT,
    created_ms          INTEGER NOT NULL,
    updated_ms          INTEGER NOT NULL,
    UNIQUE (scheme, symbol, box_version_id)
);

CREATE TABLE IF NOT EXISTS observe_candidate (
    candidate_id        TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL,
    scheme              TEXT NOT NULL,
    seq_in_event        INTEGER NOT NULL,
    created_ms          INTEGER NOT NULL,
    cancelled_ms        INTEGER,
    cancel_reason       TEXT,
    local_support_price REAL,
    entry_price_ref     REAL,
    state               TEXT NOT NULL,
    note                TEXT,
    UNIQUE (event_id, seq_in_event)
);

CREATE TABLE IF NOT EXISTS observe_transition (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT NOT NULL,
    candidate_id        TEXT,
    from_state          TEXT,
    to_state            TEXT NOT NULL,
    at_ms               INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    payload_json        TEXT
);

CREATE TABLE IF NOT EXISTS observe_data_gap (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT NOT NULL,
    field               TEXT NOT NULL,
    from_ms             INTEGER,
    to_ms               INTEGER,
    reason              TEXT NOT NULL,
    created_ms          INTEGER NOT NULL
);
"""

# 初始锚点冻结：任何 UPDATE 触碰冻结列即 ABORT
TRIGGERS = []
for _col in FROZEN_EVENT_COLUMNS:
    TRIGGERS.append(f"""
CREATE TRIGGER IF NOT EXISTS trg_freeze_{_col}
BEFORE UPDATE OF {_col} ON observe_event
FOR EACH ROW WHEN OLD.{_col} IS NOT NEW.{_col}
BEGIN
    SELECT RAISE(ABORT, 'frozen_anchor:{_col}');
END;
""")

TRIGGERS.append("""
CREATE TRIGGER IF NOT EXISTS trg_event_no_delete
BEFORE DELETE ON observe_event
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'observe_event_no_delete:保留失败与失效样本');
END;
""")

TRIGGERS.append("""
CREATE TRIGGER IF NOT EXISTS trg_candidate_no_delete
BEFORE DELETE ON observe_candidate
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'observe_candidate_no_delete:保留失效候选');
END;
""")

TRIGGERS.append("""
CREATE TRIGGER IF NOT EXISTS trg_candidate_frozen
BEFORE UPDATE OF event_id, scheme, seq_in_event, created_ms,
                 local_support_price, entry_price_ref ON observe_candidate
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'candidate_frozen:候选只允许改状态与取消信息');
END;
""")


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class ObserveStore:
    def __init__(self, path=None):
        self.path = Path(path or RESEARCH_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(DDL)
        for t in TRIGGERS:
            self.con.execute(t)
        self.con.commit()

    # ── 隔离断言 ────────────────────────────────────────────────────────
    def assert_isolated(self) -> dict:
        p = self.path.resolve()
        for prod in PRODUCTION_DBS:
            if p == prod.resolve():
                raise RuntimeError(f"研究库不得指向生产库：{p}")
        return {"research_db": str(p),
                "production_dbs_checked": [str(x) for x in PRODUCTION_DBS],
                "isolated": True}

    # ── 事件 ────────────────────────────────────────────────────────────
    def create_event(self, *, scheme: str, symbol: str, venue: str,
                     strategy_version: str, param_version: str,
                     box_version_id: str, structure_seen_ms: int,
                     confirmed_ms: int, expires_at_ms: int,
                     old_low=None, sweep_low=None, vwap_anchor_ms=None,
                     vwap_anchor_source=None, state: str = "BOX",
                     evidence=None, counter_evidence=None,
                     missing_evidence=None, data_quality: str = "ok",
                     now_ms=None) -> dict:
        """幂等建档：同 (scheme, symbol, box_version_id) 已存在 ⇒ 返回既有，不改期限。"""
        now = int(now_ms or _now_ms())
        cur = self.con.execute(
            """SELECT event_id, expires_at_ms, state FROM observe_event
               WHERE scheme=? AND symbol=? AND box_version_id=?""",
            (scheme, symbol, box_version_id)).fetchone()
        if cur:
            return {"event_id": cur["event_id"], "created": False,
                    "expires_at_ms": int(cur["expires_at_ms"]),
                    "state": cur["state"],
                    "note": "重复扫描命中既有事件，观察期限未被重置"}
        if state not in STATES:
            raise ValueError(f"未知状态 {state}")
        event_id = f"ev_{scheme}_{venue}_{symbol}_{box_version_id}_{structure_seen_ms}"
        self.con.execute(
            """INSERT INTO observe_event(
                   event_id, scheme, symbol, venue, strategy_version, param_version,
                   box_version_id, old_low, sweep_low, vwap_anchor_ms,
                   vwap_anchor_source, structure_seen_ms, confirmed_ms,
                   expires_at_ms, state, closed_reason, data_quality,
                   evidence_json, counter_evidence_json, missing_evidence_json,
                   created_ms, updated_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?)""",
            (event_id, scheme, symbol, venue, strategy_version, param_version,
             box_version_id, old_low, sweep_low, vwap_anchor_ms,
             vwap_anchor_source, int(structure_seen_ms), int(confirmed_ms),
             int(expires_at_ms), state, data_quality,
             json.dumps(evidence or {}, ensure_ascii=False),
             json.dumps(counter_evidence or {}, ensure_ascii=False),
             json.dumps(missing_evidence or {}, ensure_ascii=False), now, now))
        self.con.execute(
            """INSERT INTO observe_transition(event_id, candidate_id, from_state,
                   to_state, at_ms, reason, payload_json)
               VALUES(?,NULL,NULL,?,?,?,?)""",
            (event_id, state, int(confirmed_ms), "事件建档（初始锚点冻结）",
             json.dumps({"data_quality": data_quality}, ensure_ascii=False)))
        self.con.commit()
        return {"event_id": event_id, "created": True,
                "expires_at_ms": int(expires_at_ms), "state": state}

    def transition(self, event_id: str, from_state: str, to_state: str,
                   reason: str, at_ms: int, candidate_id=None, payload=None):
        if to_state not in STATES:
            raise ValueError(f"未知状态 {to_state}")
        if not reason:
            raise ValueError("状态迁移必须写原因")
        self.con.execute(
            """UPDATE observe_event SET state=?, updated_ms=? WHERE event_id=?""",
            (to_state, int(at_ms), event_id))
        self.con.execute(
            """INSERT INTO observe_transition(event_id, candidate_id, from_state,
                   to_state, at_ms, reason, payload_json) VALUES(?,?,?,?,?,?,?)""",
            (event_id, candidate_id, from_state, to_state, int(at_ms), reason,
             json.dumps(payload or {}, ensure_ascii=False)))
        self.con.commit()
        return {"ok": True, "to_state": to_state}

    def close_event(self, event_id: str, state: str, reason: str, at_ms: int):
        """终结事件。数据不足必须用 DATA_INSUFFICIENT，不得写 INVALIDATED。"""
        if state not in ("INVALIDATED", "EXPIRED", "CLOSED", "DATA_INSUFFICIENT"):
            raise ValueError(f"不是终结状态：{state}")
        if not reason:
            raise ValueError("终结必须写原因")
        row = self.con.execute("SELECT state FROM observe_event WHERE event_id=?",
                               (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        self.con.execute(
            """UPDATE observe_event SET state=?, closed_reason=?, updated_ms=?
               WHERE event_id=?""", (state, reason, int(at_ms), event_id))
        self.con.execute(
            """INSERT INTO observe_transition(event_id, candidate_id, from_state,
                   to_state, at_ms, reason, payload_json) VALUES(?,NULL,?,?,?,?,NULL)""",
            (event_id, row["state"], state, int(at_ms), reason))
        self.con.commit()
        return {"ok": True, "state": state, "closed_reason": reason}

    # ── 候选 ────────────────────────────────────────────────────────────
    def add_candidate(self, event_id: str, *, scheme: str, created_ms: int,
                      local_support_price=None, entry_price_ref=None,
                      state: str = "WAIT_ENTRY", note=None) -> dict:
        row = self.con.execute(
            "SELECT COALESCE(MAX(seq_in_event),0) AS m FROM observe_candidate WHERE event_id=?",
            (event_id,)).fetchone()
        seq = int(row["m"]) + 1
        cand_id = f"cd_{event_id}_s{seq}"
        self.con.execute(
            """INSERT INTO observe_candidate(candidate_id, event_id, scheme,
                   seq_in_event, created_ms, cancelled_ms, cancel_reason,
                   local_support_price, entry_price_ref, state, note)
               VALUES(?,?,?,?,?,NULL,NULL,?,?,?,?)""",
            (cand_id, event_id, scheme, seq, int(created_ms),
             local_support_price, entry_price_ref, state, note))
        self.con.execute(
            """INSERT INTO observe_transition(event_id, candidate_id, from_state,
                   to_state, at_ms, reason, payload_json)
               VALUES(?,?,NULL,?,?,?,?)""",
            (event_id, cand_id, state, int(created_ms), "候选生成",
             json.dumps({"seq_in_event": seq,
                         "local_support_price": local_support_price},
                        ensure_ascii=False)))
        self.con.commit()
        return {"candidate_id": cand_id, "seq_in_event": seq}

    def cancel_candidate(self, candidate_id: str, reason: str, at_ms: int):
        if not reason:
            raise ValueError("取消必须写原因")
        row = self.con.execute(
            "SELECT event_id, state FROM observe_candidate WHERE candidate_id=?",
            (candidate_id,)).fetchone()
        if not row:
            raise KeyError(candidate_id)
        self.con.execute(
            """UPDATE observe_candidate SET state='CANCELLED', cancelled_ms=?,
                   cancel_reason=? WHERE candidate_id=?""",
            (int(at_ms), reason, candidate_id))
        self.con.execute(
            """INSERT INTO observe_transition(event_id, candidate_id, from_state,
                   to_state, at_ms, reason, payload_json)
               VALUES(?,?,?,?,?,?,NULL)""",
            (row["event_id"], candidate_id, row["state"], "CANCELLED",
             int(at_ms), reason))
        self.con.commit()
        return {"ok": True}

    # ── 数据缺口 ────────────────────────────────────────────────────────
    def add_data_gap(self, event_id: str, field: str, reason: str,
                     from_ms=None, to_ms=None, now_ms=None):
        self.con.execute(
            """INSERT INTO observe_data_gap(event_id, field, from_ms, to_ms,
                   reason, created_ms) VALUES(?,?,?,?,?,?)""",
            (event_id, field, from_ms, to_ms, reason, int(now_ms or _now_ms())))
        self.con.commit()
        return {"ok": True}

    # ── 逐事件证据链 ────────────────────────────────────────────────────
    def dump_event(self, event_id: str) -> dict:
        ev = self.con.execute("SELECT * FROM observe_event WHERE event_id=?",
                              (event_id,)).fetchone()
        if not ev:
            raise KeyError(event_id)
        cands = self.con.execute(
            """SELECT * FROM observe_candidate WHERE event_id=?
               ORDER BY seq_in_event""", (event_id,)).fetchall()
        trans = self.con.execute(
            """SELECT * FROM observe_transition WHERE event_id=? ORDER BY at_ms, id""",
            (event_id,)).fetchall()
        gaps = self.con.execute(
            "SELECT * FROM observe_data_gap WHERE event_id=? ORDER BY id",
            (event_id,)).fetchall()
        e = dict(ev)
        e["structure_seen_utc"] = _iso(e["structure_seen_ms"])
        e["confirmed_utc"] = _iso(e["confirmed_ms"])
        e["expires_at_utc"] = _iso(e["expires_at_ms"])
        return {
            "event": e,
            "candidates": [dict(r) for r in cands],
            "transitions": [{**dict(r), "at_utc": _iso(r["at_ms"])} for r in trans],
            "data_gaps": [dict(r) for r in gaps],
        }

    def close(self):
        self.con.close()
