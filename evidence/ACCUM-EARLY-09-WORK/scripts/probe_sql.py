# -*- coding: utf-8 -*-
"""SQL 级写探针（通用版）：证明**本进程**对哪些库发过写语句。

为什么不用文件指纹：行情库由采集进程持续写入，文件 size/mtime 在几分钟窗口里必变；
只比文件会把「别人的写入」算成「我们写的」。本探针在进程内包 `sqlite3.connect`，
按**库路径**统计写语句条数，因此能区分「我们没写」与「文件变了」。

用法：在 import 业务模块**之前** `install()`；结束时 `report()`。

计数口径（2026-10-27 修正）
--------------------------
初版把「写语句总数」算成 `len(样本列表)`，而样本上限 200 ⇒ 一旦写超过 200 条，
**总数就被截断成 200**，看起来像「正好 200 条」，是错的计数。
现在改为：
  · `写语句总数` = 独立计数器，**不封顶**；
  · `写语句按库` / `写语句分类` = 独立字典，**不封顶**；
  · `写语句样本` 只保留前 200 条（仅供人看），并在报告里写明是否被截断。

分类：
  · write  = INSERT / UPDATE / DELETE / REPLACE / CREATE / DROP / ALTER / VACUUM
  · attach = ATTACH / DETACH（只读对账时 live_api 会 ATTACH 账本库，不算写）
  · 其余（SELECT / PRAGMA / BEGIN …）不计。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "VACUUM")
ATTACH_PREFIXES = ("ATTACH", "DETACH")
SAMPLE_CAP = 200

STATE: Dict[str, Any] = {
    "connections": {},
    "writes": [],          # 样本（上限 SAMPLE_CAP）
    "write_n": 0,          # 写语句总数（不封顶）
    "by_db": {},           # 每库写语句数（不封顶）
    "by_kind": {},         # 每类写语句数（不封顶）
    "non_idem_n": 0,       # 非「幂等建表」写语句数（不封顶）
    "non_idem": [],        # 非幂等写语句样本（上限 50）
    "attaches": [],
    "attach_n": 0,
    "statements": 0,
}
_orig_connect = sqlite3.connect
_installed = False


def _key(db: Any) -> str:
    return str(db) if db is not None else "<none>"


def install() -> None:
    global _installed
    if _installed:
        return

    def _patched(*a, **kw):
        con = _orig_connect(*a, **kw)
        db = a[0] if a else kw.get("database")
        k = _key(db)
        STATE["connections"][k] = STATE["connections"].get(k, 0) + 1

        def _cb(sql: str) -> None:
            STATE["statements"] += 1
            s = (sql or "").lstrip().upper()
            if s.startswith(WRITE_PREFIXES):
                STATE["write_n"] += 1
                STATE["by_db"][k] = STATE["by_db"].get(k, 0) + 1
                head = " ".join((sql or "").split()).upper().split()[:4]
                kind = " ".join(head)
                STATE["by_kind"][kind] = STATE["by_kind"].get(kind, 0) + 1
                if len(STATE["writes"]) < SAMPLE_CAP:
                    STATE["writes"].append({"db": k, "sql": (sql or "")[:200]})
                # 幂等建表 = 以 CREATE 开头 **且** 含 `IF NOT EXISTS`（看**整句**，不是前 4 词）
                if not (s.startswith("CREATE") and "IF NOT EXISTS" in s):
                    STATE["non_idem_n"] += 1
                    if len(STATE["non_idem"]) < 50:
                        STATE["non_idem"].append({"db": k, "sql": (sql or "")[:200]})
            elif s.startswith(ATTACH_PREFIXES):
                STATE["attach_n"] += 1
                if len(STATE["attaches"]) < 50:
                    STATE["attaches"].append({"db": k, "sql": (sql or "")[:200]})

        con.set_trace_callback(_cb)
        return con

    sqlite3.connect = _patched  # type: ignore[assignment]
    _installed = True


def by_db() -> Dict[str, int]:
    return dict(STATE["by_db"])


def classify() -> Dict[str, int]:
    return dict(STATE["by_kind"])


def report() -> Dict[str, Any]:
    cls = classify()
    n = STATE["write_n"]
    return {
        "连接过的库": STATE["connections"],
        "语句总数（含读）": STATE["statements"],
        "写语句总数": n,
        "写语句按库": by_db(),
        "写语句分类（前4词）": cls,
        "非幂等建表写语句数": STATE["non_idem_n"],
        "非幂等建表写语句样本": STATE["non_idem"][:10],
        "写语句是否全为幂等建表": n > 0 and STATE["non_idem_n"] == 0,
        "写语句样本": STATE["writes"][:10],
        "写语句样本是否被截断": n > len(STATE["writes"]),
        "ATTACH/DETACH 次数": STATE["attach_n"],
        "判定口径": "本进程内通过 sqlite3.connect 打开的连接全部挂了 trace；"
                    "写语句数为 0 ⇒ 本进程对该库未发过任何写语句。"
                    "总数/按库/分类/非幂等数均为独立计数（不封顶），样本列表只保留前 "
                    f"{SAMPLE_CAP} 条。`CREATE … IF NOT EXISTS` 属幂等建表 DDL，"
                    "与 INSERT/UPDATE/DELETE 业务写分开计。",
    }


def reset() -> None:
    STATE.update({"connections": {}, "writes": [], "write_n": 0, "by_db": {},
                  "by_kind": {}, "non_idem_n": 0, "non_idem": [], "attaches": [],
                  "attach_n": 0, "statements": 0})
