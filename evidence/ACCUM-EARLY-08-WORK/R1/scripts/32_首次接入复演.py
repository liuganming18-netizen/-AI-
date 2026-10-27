# -*- coding: utf-8 -*-
"""32｜首次接入复演（在「写前状态」的副本上重放一次 0 → 289）。

为什么需要它：`30_生产接入.py` 是幂等脚本，复跑会把「写前行数 / 净增」更新成复跑时的值。
本单第一次接入（0 行 → 289 行）的记录**已被自己的复跑覆盖**（脚本缺陷，已改为追加历次文件）。
为了不靠「我记得当时是 0」这种话，这里用**写前状态的副本**（`isolated/pre_import/` 的
0 行重建件）把首次接入完整复演一遍，得到可复算的 0 → 289。

边界：这是**复演**，不是生产库的首次接入记录本身。
生产库当前状态（289 行）由 `30`/`40`/`50`/`70` 直接取证。

输出：out/32_首次接入复演.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

IMPORT = L.BACKEND / "research_signal_import.py"
WORK = L.ISO / "first_import"


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc(), "性质": "复演（副本上重放首次接入）"}
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "live").mkdir(parents=True)
    (WORK / "paper").mkdir(parents=True)
    (WORK / "locks").mkdir(parents=True)

    src = L.ISO / "pre_import" / "live_signals_before_import.sqlite3"
    if not src.exists():
        rep["error"] = f"写前状态副本不存在：{src}（先跑 31_写前快照重建.py）"
        L.dump("32_首次接入复演.json", rep)
        print(rep["error"])
        return 2

    dst = WORK / "live" / "live_signals.sqlite3"
    rep["起点"] = L.backup_db(src, dst)
    rep["起点行数"] = _count(dst)

    env = L.iso_env()
    env.update({
        "LIVE_DB": str(dst.resolve()),
        "LIVE_DIR": str((WORK / "live").resolve()),
        "PAPER_LEDGER_DIR": str((WORK / "paper").resolve()),
        "PAPER_LOCK_DIR": str((WORK / "locks").resolve()),
    })
    r = L.run([L.PY, str(IMPORT), "--run-dir", str(L.RUN_B), "--require-ok", "--apply", "--json"],
              env=env)
    try:
        rep["复演"] = json.loads(r["stdout"])
    except Exception:  # noqa: BLE001
        rep["复演"] = {"parse_error": True, "stdout": r["stdout"][-3000:],
                     "stderr": r["stderr"][-3000:]}
    rep["复演"]["rc"] = r["returncode"]
    rep["终点行数"] = _count(dst)
    st = rep["复演"].get("stats") or {}

    checks = {
        "起点是写前状态（0 行）": rep["起点行数"] == 0,
        "复演进程 rc=0": rep["复演"]["rc"] == 0,
        "首次接入净增 289": st.get("new") == 289,
        "更新 0 条（库里原本没有）": st.get("updated") == 0,
        "终点 289 行": rep["终点行数"] == 289,
        "复演后当前交易区 0 行": st.get("current_area_rows") == 0,
        "复演只写这一份副本（LIVE_DB 指向副本）":
            str(dst.resolve()) in str(st.get("target_db")),
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = ("首次接入在写前状态副本上复演成功：0 行 → 289 行，净增 289、更新 0。"
                       if not fail else f"存在未通过项：{fail}")

    p = L.dump("32_首次接入复演.json", rep)
    print("== 32 首次接入复演 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


def _count(db: Path) -> int:
    con = L.ro_conn(db)
    try:
        return con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
