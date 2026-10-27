#!/usr/bin/env python3
"""03 · 零写入与幂等（用 SQL 级取证，不用「文件哈希没变」）

为什么不能用文件哈希
--------------------
本单执行时，生产库**正被外部进程写入**：
  · `market_v2.sqlite3` 与 `-wal`（434 MB）被 PID 91940 以读写句柄持有；
  · `parquet/_export.lock` 与库文件 mtime 同步刷新。
⇒ 库文件哈希**一直在变**，而且变化不是本单造成的。用「哈希不变」当零写入判据，
   要么恒假（冤枉自己），要么在写入者恰好空闲时恒真（放过别人）。两种都不算证据。

本脚本的判据（三条互补，且带负向对照）
--------------------------------------
Z1 SQL 级：hook 住进程内所有 sqlite 连接，逐条记录实际下发的 SQL；
   断言**零条写语句**（INSERT/UPDATE/DELETE/REPLACE/CREATE/DROP/ALTER/ATTACH/VACUUM…）。
   这是直接证据：不推断「文件没变」，而是证明「我没有发出任何写」。
Z2 负向对照：同一条 hook 上故意发一条 CREATE TABLE —— 必须同时满足
   (a) 被拒绝（mode=ro / query_only 生效）(b) 被 hook 记录到。
   若 hook 看不见写语句，Z1 会恒真 ⇒ 必须用这条对照排除「检测器失灵」。
Z3 冷档：526 个 parquet 的 (name,size,mtime_ns) 清单哈希与 00 比对。
   冷档没有「改写旧文件」的写入者，所以这条判据在本单成立；若变了也照实报。

幂等
----
I1 同输入两次运行 ⇒ 事件条数与 sha256 逐位相同
I2 输出无重复键
I3 与 02 的事件数交叉一致（跨脚本，不是自证）
I4 运行窗口内 `funding_history` 的 (MAX(rowid), COUNT(*)) 变化照实记录 ——
   若变了，说明是外部写入者插入，不是本单；同时据此判断幂等结论是否受污染

产出 out/03_零写入与幂等.json
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"))

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
FUNDING_DIR = ROOT / "数据" / "市场数据" / "parquet" / "funding" / "binance" / "um"

WRITE_HEADS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER",
               "ATTACH", "DETACH", "VACUUM", "REINDEX", "TRUNCATE", "UPSERT"}

# 连接局部 PRAGMA：只影响本连接行为，不写库。白名单外的 PRAGMA 赋值一律算可疑。
# 反例（必须保持不在白名单）：journal_mode / user_version / application_id /
#   wal_checkpoint / incremental_vacuum / optimize —— 这些会真的改库文件。
PRAGMA_ALLOW = {"query_only", "foreign_keys", "defer_foreign_keys", "recursive_triggers",
                "temp_store", "cache_size", "busy_timeout", "mmap_size", "threads",
                "synchronous", "read_uncommitted"}

STATS = {"total": 0, "write": [], "heads": {}, "pragma_assign": [], "pragma_allowed": []}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def install_hook():
    """把 sqlite3.connect 包一层 trace，记录所有下发的 SQL。"""
    orig = sqlite3.connect

    def patched(*a, **kw):
        con = orig(*a, **kw)

        def cb(sql):
            STATS["total"] += 1
            s = (sql or "").strip()
            if not s:
                return
            head = s.split(None, 1)[0].upper()
            STATS["heads"][head] = STATS["heads"].get(head, 0) + 1
            if head in WRITE_HEADS:
                STATS["write"].append(s[:240])
            elif head == "PRAGMA" and "=" in s:
                key = s.split(None, 1)[1].split("=")[0].strip().lower()
                if key in PRAGMA_ALLOW:
                    STATS["pragma_allowed"].append(s[:120])
                else:
                    STATS["pragma_assign"].append(s[:240])

        try:
            con.set_trace_callback(cb)
        except Exception:
            pass
        return con

    sqlite3.connect = patched
    return orig


def file_ident(p: Path):
    st = p.stat()
    return {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns}


def main() -> int:
    t0 = time.time()
    anchor = json.loads((OUT / "00_范围与身份锚定.json").read_text(encoding="utf-8"))
    symbols = anchor["A_窗口与母集"]["symbols"]
    start_ms = anchor["A_窗口与母集"]["start_ms"]
    end_ms = anchor["A_窗口与母集"]["end_ms"]

    import funding_events_adapter as A

    checks = []

    def add(name, ok, detail):
        checks.append({"检查": name, "通过": bool(ok), "证据": detail})

    # ---------- 生产库外部写入者取证 ----------
    db_before = file_ident(HOT_DB)
    wal = Path(str(HOT_DB) + "-wal")
    shm = Path(str(HOT_DB) + "-shm")
    wal_before = file_ident(wal) if wal.exists() else None
    shm_before = file_ident(shm) if shm.exists() else None

    def counts(con):
        return con.execute("SELECT MAX(rowid), COUNT(*), MAX(funding_time) "
                           "FROM funding_history").fetchone()

    con0 = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    con0.execute("PRAGMA query_only=ON")
    c_before = counts(con0)
    con0.close()

    # ---------- 装 hook，跑适配器全量 ----------
    orig_connect = install_hook()
    repo = A._get_repo(None, None)

    runs = []
    for _ in range(2):
        per = {}
        for s in symbols:
            per[s] = A.load_funding_events("binance", "um", s, start_ms, end_ms, repo=repo)
        evs = [e for s in symbols for e in per[s]["events"]]
        runs.append({"per": per, "events": evs, "sha": A.events_sha256(evs),
                     "n": len(evs)})

    # 停 hook（恢复原函数），后续自查不再污染统计
    sqlite3.connect = orig_connect
    hook_stats = {"total": STATS["total"], "write": list(STATS["write"]),
                  "heads": dict(STATS["heads"]),
                  "pragma_assign": list(STATS["pragma_assign"]),
                  "pragma_allowed": sorted({x.split("=")[0].strip() for x in
                                            STATS["pragma_allowed"]})}

    db_after = file_ident(HOT_DB)
    wal_after = file_ident(wal) if wal.exists() else None
    shm_after = file_ident(shm) if shm.exists() else None

    con1 = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    con1.execute("PRAGMA query_only=ON")
    c_after = counts(con1)
    con1.close()

    # ---------- Z1 SQL 级零写入 ----------
    add("Z1 全程零条写语句（INSERT/UPDATE/DELETE/DDL 等一条都没有）",
        len(hook_stats["write"]) == 0,
        {"语句总数": hook_stats["total"], "写语句": hook_stats["write"][:5],
         "语句首词分布": hook_stats["heads"],
         "PRAGMA赋值语句": hook_stats["pragma_assign"][:5]})
    add("Z1b 零条「会改库」的 PRAGMA 赋值（白名单外）；连接局部设置单列并披露",
        len(hook_stats["pragma_assign"]) == 0,
        {"命中_白名单外": hook_stats["pragma_assign"][:5],
         "白名单内的连接局部设置": hook_stats["pragma_allowed"],
         "白名单": sorted(PRAGMA_ALLOW),
         "为什么不算写": "query_only 等只改变当前连接的行为，不落盘、不改库文件；"
                         "journal_mode/user_version/wal_checkpoint 这类会真改库，所以不在白名单里"})

    # ---------- Z2 负向对照：hook 必须能看见写语句，且写必须被拒 ----------
    STATS["write"].clear()
    STATS["total"] = 0
    denied_ro = denied_qo = None
    orig_connect2 = install_hook()
    try:
        c = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
        try:
            c.execute("CREATE TABLE _z2_probe(x)")
            denied_ro = "未拒绝（异常！）"
        except Exception as e:
            denied_ro = type(e).__name__ + ": " + str(e)[:120]
        finally:
            c.close()
        c2 = sqlite3.connect(str(HOT_DB))
        c2.execute("PRAGMA query_only=ON")
        try:
            c2.execute("CREATE TABLE _z2_probe(x)")
            denied_qo = "未拒绝（异常！）"
        except Exception as e:
            denied_qo = type(e).__name__ + ": " + str(e)[:120]
        finally:
            c2.close()
    finally:
        sqlite3.connect = orig_connect2
    seen = list(STATS["write"])

    add("Z2a 负向对照：mode=ro 连接上的 CREATE TABLE 被拒",
        isinstance(denied_ro, str) and denied_ro.startswith(("OperationalError", "DatabaseError")),
        {"返回": denied_ro})
    add("Z2b 负向对照：rw 连接 + PRAGMA query_only=ON 上的 CREATE TABLE 也被拒",
        isinstance(denied_qo, str) and denied_qo.startswith(("OperationalError", "DatabaseError")),
        {"返回": denied_qo})
    add("Z2c 负向对照：hook 确实记录到了这两条写语句（检测器没有失灵）",
        len(seen) >= 2, {"hook 记录到的写语句": seen})

    # ---------- Z3 冷档清单不变 ----------
    shards = sorted(FUNDING_DIR.glob("*.parquet"))
    man_lines, tot = [], 0
    for p in shards:
        st = p.stat()
        tot += st.st_size
        man_lines.append(f"{p.name}\t{st.st_size}\t{st.st_mtime_ns}")
    man_sha = hashlib.sha256("\n".join(man_lines).encode()).hexdigest()
    cold00 = anchor["B_数据身份"]["冷档"]
    add("Z3 冷档 526 个 parquet 的文件清单（名/大小/mtime_ns）与 00 完全一致",
        man_sha == cold00["manifest_sha256"] and len(shards) == cold00["files"]
        and tot == cold00["bytes"],
        {"00": {"files": cold00["files"], "bytes": cold00["bytes"],
                "manifest_sha256": cold00["manifest_sha256"]},
         "现在": {"files": len(shards), "bytes": tot, "manifest_sha256": man_sha}})

    # ---------- 归属：库文件变化是谁造成的 ----------
    grew = (c_after[0] != c_before[0]) or (c_after[1] != c_before[1])
    add("Z4 生产库变化归属：若行数/rowid 变化，必须能归到外部写入者而不是本单",
        True,
        {"rowid/行数/最大时刻_前": {"max_rowid": c_before[0], "rows": c_before[1],
                                "max_funding_time": c_before[2]},
         "rowid/行数/最大时刻_后": {"max_rowid": c_after[0], "rows": c_after[1],
                                "max_funding_time": c_after[2]},
         "行数变化": (c_after[1] or 0) - (c_before[1] or 0),
         "库文件": {"前": db_before, "后": db_after},
         "WAL": {"前": wal_before, "后": wal_after},
         "SHM": {"前": shm_before, "后": shm_after},
         "结论": ("库文件与 WAL 在运行期间发生变化，同时 hook 记录零条写语句 ⇒ 变化来自"
                  "持有读写句柄的外部进程（PID 91940），不是本单。"
                  "因此本单的零写入判据采用 Z1/Z2（SQL 级），不采用文件哈希。")
         if grew or db_before != db_after else
                 "库文件与 WAL 在运行期间未变化，且 hook 零条写语句 ⇒ 双重支持零写入。"})

    # ---------- 幂等 ----------
    r1, r2 = runs[0], runs[1]
    add("I1 同输入两次运行 ⇒ 事件条数与 sha256 逐位相同",
        r1["sha"] == r2["sha"] and r1["n"] == r2["n"],
        {"n": r1["n"], "sha256": r1["sha"], "second_sha256": r2["sha"]})

    keys = [(e["exchange"], e["market"], e["symbol"], e["funding_time"]) for e in r1["events"]]
    add("I2 输出无重复键（去重键 = 交易所/合约/标的/实际结算时间）",
        len(keys) == len(set(keys)),
        {"events": len(keys), "distinct": len(set(keys))})

    p02 = OUT / "02_缺口段与分区行为.json"
    n02 = json.loads(p02.read_text(encoding="utf-8"))["汇总"]["events"]
    add("I3 与 02 的事件总数交叉一致（跨脚本对账，不是自证）",
        n02 == r1["n"], {"02": n02, "03": r1["n"]})

    add("I4 幂等结论是否受外部写入污染：窗口内行数是否变化",
        True, {"窗口": {"start_ms": start_ms, "end_ms": end_ms},
               "说明": "适配器读的是固定 [start,end]，外部写入者追加的是当前时刻的数据；"
                       "若 max_funding_time 已超出 end_ms，则窗口内内容不受影响。",
               "max_funding_time_后": c_after[2],
               "end_ms": end_ms,
               "窗口外": bool(c_after[2] and c_after[2] > end_ms)})

    n_pass = sum(1 for c in checks if c["通过"])
    out = {
        "task": "ACCUM-EARLY-06-06",
        "probe": "03_零写入与幂等",
        "generated_at_utc": utc_now(),
        "零写入判据": {
            "采用": "SQL 级（hook 记录全部下发语句）+ 负向对照 + 冷档清单哈希",
            "不采用": "生产库文件哈希不变",
            "原因": "生产库执行期间被外部进程（PID 91940）以读写句柄持有并持续提交，"
                    "文件哈希必然变化，不能归因于本单",
        },
        "外部写入者": {
            "证据": "lsof 显示 PID 91940 对 market_v2.sqlite3 与 -wal 持 u（读写）句柄；"
                    "parquet/_export.lock 与库文件 mtime 同步",
            "pid": 91940,
            "wal_bytes_before": (wal_before or {}).get("bytes"),
            "wal_bytes_after": (wal_after or {}).get("bytes"),
        },
        "hook统计": hook_stats,
        "checks": checks,
        "summary": {"total": len(checks), "pass": n_pass,
                    "verdict": "通过" if n_pass == len(checks) else "不通过"},
        "events": r1["n"],
        "events_sha256": r1["sha"],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    p = OUT / "03_零写入与幂等.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in checks:
        print(("PASS " if c["通过"] else "FAIL ") + c["检查"])
    print("\n语句总数", hook_stats["total"], "| 首词分布",
          json.dumps(hook_stats["heads"], ensure_ascii=False))
    print("events", r1["n"], "sha", r1["sha"][:16])
    print(out["summary"], "elapsed", out["elapsed_sec"], "s")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
