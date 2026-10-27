# -*- coding: utf-8 -*-
"""行情库写探针：SQL 级证明「本进程对行情库一条写语句都没发过」。

为什么不能只看文件指纹：行情库由采集进程持续写入，文件大小/mtime 在几分钟的
窗口里**必然会变**，此时「文件变了」既可能是我们写的、也可能是采集写的，无法区分。
所以在**本进程内**包一层 `sqlite3.connect`，凡目标库是行情库的连接都挂
`set_trace_callback`，把 INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER 计数。

必须在 import 其它业务模块**之前**调用 `install()`。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

STATE: Dict[str, Any] = {"connections": 0, "write_stmts": 0, "samples": [],
                         "market_db_markers": ("market_v2.sqlite3",)}
_orig_connect = sqlite3.connect
_installed = False


def _is_market(db: Any) -> bool:
    s = db if isinstance(db, str) else str(db)
    return any(m in s for m in STATE["market_db_markers"])


def install() -> None:
    global _installed
    if _installed:
        return

    def _patched(*a, **kw):
        con = _orig_connect(*a, **kw)
        db = a[0] if a else kw.get("database")
        if _is_market(db):
            STATE["connections"] += 1

            def _cb(sql: str) -> None:
                s = (sql or "").lstrip().upper()
                if s.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE",
                                 "CREATE", "DROP", "ALTER", "VACUUM", "ATTACH")):
                    STATE["write_stmts"] += 1
                    if len(STATE["samples"]) < 10:
                        STATE["samples"].append((sql or "")[:160])

            con.set_trace_callback(_cb)
        return con

    sqlite3.connect = _patched  # type: ignore[assignment]
    _installed = True


def report() -> Dict[str, Any]:
    return {"行情库连接数": STATE["connections"],
            "行情库写语句数": STATE["write_stmts"],
            "写语句样本": STATE["samples"],
            "判定口径": "本进程内通过 sqlite3.connect 打开行情库的连接全部挂了 trace；"
                        "计数为 0 即「本进程未发过写语句」"}
