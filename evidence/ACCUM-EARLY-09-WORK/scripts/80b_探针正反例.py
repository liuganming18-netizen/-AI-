# -*- coding: utf-8 -*-
"""80b｜写探针的正反例（证明这把尺子不是瞎的）。

为什么要做
----------
「探针报告写语句 = 0」只有在**探针确实能抓到写**的前提下才有意义。
所以做两个对照：
  · 正例（write）：在临时库上建表 + 插入 + 更新 ⇒ 探针必须报出这几条写语句；
  · 反例（read） ：只读同一批库（`mode=ro`）⇒ 探针必须报 0 条写语句。
两个都成立，才能说「0 写 = 真的没写」，而不是「探针根本没接上」。

只在 `09/isolated/probe_ctl/` 下建临时库，不碰任何生产文件。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as U  # noqa: E402
import probe_sql  # noqa: E402

probe_sql.install()          # 早于任何 connect

ROOT = U.ISO / "probe_ctl"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "write"
    ROOT.mkdir(parents=True, exist_ok=True)
    db = ROOT / "control.sqlite3"
    if mode == "write":
        if db.exists():
            db.unlink()
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO t(v) VALUES('a')")
        con.execute("INSERT INTO t(v) VALUES('b')")
        con.execute("UPDATE t SET v='c' WHERE id=1")
        con.commit()
        con.close()
    else:
        if not db.exists():
            print(json.dumps({"error": "先跑 write 模式建库"}, ensure_ascii=False))
            return 2
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        rows = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        con.close()
        print("@@READ@@" + json.dumps({"rows": rows}, ensure_ascii=False))
    print("@@PROBE@@" + json.dumps({"mode": mode, "probe": probe_sql.report()},
                                   ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
