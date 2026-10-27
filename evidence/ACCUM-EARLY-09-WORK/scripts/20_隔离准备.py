# -*- coding: utf-8 -*-
"""20｜隔离环境准备（只读生产 → 生成 09/isolated 副本）。

做什么
------
1. 记录**生产**侧指纹（信号库 sha256、四本纸面账本 sha256、行情库 size+mtime）；
2. 用 SQLite **在线备份**（不是 `cp`）把生产信号库复制到 `isolated/live/live_signals.sqlite3`；
   四本纸面账本同样在线备份到 `isolated/paper/`；
3. 建 `isolated/locks/`（引擎锁目录）；
4. 对账：隔离副本的研究行数/总行数/账本行数必须与生产**逐位相同**，否则不算准备成功。

为什么用在线备份而不是 `cp`
--------------------------
生产库是 WAL 模式且**正被 pid 91940 持续写入**；`cp` 主库文件会丢掉 `-wal` 里已提交但
未 checkpoint 的事务，副本行数会少。`sqlite3.Connection.backup()` 走的是 SQLite 自己的
一致性快照语义，读到的是「某一刻的完整库」。

零写入纪律
----------
本脚本对生产库只开 `mode=ro` + `PRAGMA query_only=ON`；写入目标全部在 `isolated/` 下。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as U  # noqa: E402

ARMS = ("3a", "3b", "3c", "3d")


def counts(db: Path) -> dict:
    con = U.ro_conn(db)
    try:
        out = {"rows": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]}
        out["research"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'").fetchone()[0]
        out["current_area"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered') "
            "AND executable_state='enterable'").fetchone()[0]
        out["by_origin"] = {r[0]: r[1] for r in con.execute(
            "SELECT origin, COUNT(*) FROM live_signal GROUP BY origin ORDER BY origin")}
        return out
    finally:
        con.close()


def ledger_counts(db: Path) -> dict:
    con = U.ro_conn(db)
    try:
        out = {}
        for t in ("paper_account", "paper_position", "paper_order", "paper_trade", "paper_state"):
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out
    finally:
        con.close()


def main() -> int:
    checks: dict = {}
    U.ISO.mkdir(parents=True, exist_ok=True)
    iso_live = U.ISO / "live"
    iso_paper = U.ISO / "paper"
    iso_locks = U.ISO / "locks"
    for d in (iso_live, iso_paper, iso_locks):
        d.mkdir(parents=True, exist_ok=True)

    # ── 1. 生产侧指纹（写前基线）───────────────────────────────────────────
    prod_live_fp = U.fingerprint(U.LIVE_DB)
    prod_paper_fp = {a: U.fingerprint(U.PAPER_DIR / f"paper_{a}.sqlite3") for a in ARMS}
    market_fp = U.fingerprint(U.MARKET_DB)
    prod_counts = counts(U.LIVE_DB)
    prod_ledger = {a: ledger_counts(U.PAPER_DIR / f"paper_{a}.sqlite3") for a in ARMS}

    # ── 2. 在线备份 ───────────────────────────────────────────────────────
    iso_live_db = iso_live / "live_signals.sqlite3"
    live_backup = U.backup_db(U.LIVE_DB, iso_live_db)
    paper_backup = {}
    for a in ARMS:
        src = U.PAPER_DIR / f"paper_{a}.sqlite3"
        if not src.exists():
            paper_backup[a] = {"src": str(src), "skipped": "源不存在"}
            continue
        dst = iso_paper / f"paper_{a}.sqlite3"
        # 隔离目录里绝不允许出现 -wal/-shm 残片（会把别的实例的未提交页带进来）
        for suf in ("-wal", "-shm"):
            stale = Path(str(dst) + suf)
            if stale.exists():
                stale.unlink()
        paper_backup[a] = U.backup_db(src, dst)

    # ── 3. 对账：副本 == 生产 ─────────────────────────────────────────────
    iso_counts = counts(iso_live_db)
    iso_ledger = {a: ledger_counts(iso_paper / f"paper_{a}.sqlite3") for a in ARMS}
    iso_live_fp = U.fingerprint(iso_live_db)

    checks["隔离信号库已生成"] = iso_live_db.exists()
    checks["隔离信号库总行数 == 生产"] = iso_counts["rows"] == prod_counts["rows"]
    checks["隔离信号库研究行数 == 生产"] = iso_counts["research"] == prod_counts["research"]
    checks["隔离信号库来源分布 == 生产"] = iso_counts["by_origin"] == prod_counts["by_origin"]
    checks["隔离信号库研究行数 == 289"] = iso_counts["research"] == 289
    checks["四本纸面账本均已备份"] = all(
        (iso_paper / f"paper_{a}.sqlite3").exists() for a in ARMS)
    checks["隔离账本行数 == 生产"] = iso_ledger == prod_ledger
    checks["隔离锁目录已建"] = iso_locks.is_dir()
    checks["生产信号库未被改动（sha256 前后一致）"] = U.same_file(
        prod_live_fp, U.fingerprint(U.LIVE_DB))["same"]
    checks["生产行情库未被改动（size+mtime）"] = U.same_file(
        market_fp, U.fingerprint(U.MARKET_DB))["same"]

    rep = {
        "生成时间": U.now_utc(),
        "生产侧": {
            "LIVE_DB": prod_live_fp,
            "PAPER_DIR": str(U.PAPER_DIR),
            "纸面账本": prod_paper_fp,
            "MARKET_DB": market_fp,
            "信号库计数": prod_counts,
            "账本计数": prod_ledger,
        },
        "隔离侧": {
            "root": str(U.ISO),
            "LIVE_DB": str(iso_live_db),
            "LIVE_DB_指纹": iso_live_fp,
            "PAPER_LEDGER_DIR": str(iso_paper),
            "PAPER_LOCK_DIR": str(iso_locks),
            "信号库计数": iso_counts,
            "账本计数": iso_ledger,
            "备份明细": {"live": live_backup, "paper": paper_backup},
        },
        "判定": U.summarise(checks, "隔离环境准备完成（副本与生产逐位一致）",
                          "隔离环境准备未通过"),
    }
    p = U.dump("20_隔离准备.json", rep)
    U.print_checks("20 隔离准备", rep, p)
    print(json.dumps({"isolated_live_db": str(iso_live_db),
                      "research": iso_counts["research"],
                      "rows": iso_counts["rows"]}, ensure_ascii=False))
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
