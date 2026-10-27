# -*- coding: utf-8 -*-
"""20｜隔离演练：反重力研究信号 → live_signal → 现有 API（全在隔离副本上）。

顺序：
  1) 建隔离副本（生产信号库 + 四本账本，SQLite 在线备份）
  2) dry-run（不写）→ apply 第一次 → apply 第二次（新进程，验幂等）
  3) 子进程核验：字段契约 / 研究范围可读 / 当前区不可见 / 闸门扫不到 / 正向对照
  4) 生产行情库与四本账本前后指纹必须逐位不变

输出：out/20_隔离演练.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

IMPORT = L.BACKEND / "research_signal_import.py"
WORKER = Path(__file__).resolve().parent / "20b_隔离核验_worker.py"

PROD_FILES = {
    "生产信号库": L.LIVE_DB,
    "纸面账本_3a": L.PAPER_DIR / "paper_3a.sqlite3",
    "纸面账本_3b": L.PAPER_DIR / "paper_3b.sqlite3",
    "纸面账本_3c": L.PAPER_DIR / "paper_3c.sqlite3",
    "纸面账本_3d": L.PAPER_DIR / "paper_3d.sqlite3",
    "行情库": L.MARKET_DB,
}


def research_stats(db: Path) -> dict:
    con = L.ro_conn(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM live_signal WHERE origin='historical_research'").fetchone()[0]
        tot = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        first = {r["signal_uid"]: r["first_seen_at"] for r in con.execute(
            "SELECT signal_uid, first_seen_at FROM live_signal ORDER BY signal_uid LIMIT 5")}
        upd = {r["signal_uid"]: r["updated_at"] for r in con.execute(
            "SELECT signal_uid, updated_at FROM live_signal ORDER BY signal_uid LIMIT 5")}
        return {"research": n, "total": tot, "first_seen": first, "updated_at": upd}
    finally:
        con.close()


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    L.reset_iso()

    before = {k: L.fingerprint(p) for k, p in PROD_FILES.items()}
    rep["生产文件_前"] = before

    # 1) 隔离副本
    iso_live = L.ISO / "live" / "live_signals.sqlite3"
    rep["隔离副本"] = {"live": L.backup_db(L.LIVE_DB, iso_live)}
    for arm in ("3a", "3b", "3c", "3d"):
        rep["隔离副本"][f"paper_{arm}"] = L.backup_db(
            L.PAPER_DIR / f"paper_{arm}.sqlite3", L.ISO / "paper" / f"paper_{arm}.sqlite3")

    env = L.iso_env()
    run_dir = str(L.RUN_B)

    # 2) dry-run → apply ×2
    dry = L.run([L.PY, str(IMPORT), "--run-dir", run_dir, "--require-ok", "--json"], env=env)
    try:
        rep["dry_run"] = json.loads(dry["stdout"])
    except Exception:  # noqa: BLE001
        rep["dry_run"] = {"parse_error": True, "stdout": dry["stdout"][-2000:],
                          "stderr": dry["stderr"][-2000:]}
    rep["dry_run"]["rc"] = dry["returncode"]

    a1 = L.run([L.PY, str(IMPORT), "--run-dir", run_dir, "--require-ok", "--apply", "--json"], env=env)
    try:
        rep["apply_1"] = json.loads(a1["stdout"])
    except Exception:  # noqa: BLE001
        rep["apply_1"] = {"parse_error": True, "stdout": a1["stdout"][-3000:],
                          "stderr": a1["stderr"][-3000:]}
    rep["apply_1"]["rc"] = a1["returncode"]
    s1 = research_stats(iso_live)
    rep["apply_1"]["库内状态"] = s1

    a2 = L.run([L.PY, str(IMPORT), "--run-dir", run_dir, "--require-ok", "--apply", "--json"], env=env)
    try:
        rep["apply_2"] = json.loads(a2["stdout"])
    except Exception:  # noqa: BLE001
        rep["apply_2"] = {"parse_error": True, "stdout": a2["stdout"][-3000:],
                          "stderr": a2["stderr"][-3000:]}
    rep["apply_2"]["rc"] = a2["returncode"]
    s2 = research_stats(iso_live)
    rep["apply_2"]["库内状态"] = s2

    rep["幂等"] = {
        "第一次 new": (rep["apply_1"].get("stats") or {}).get("new"),
        "第一次 updated": (rep["apply_1"].get("stats") or {}).get("updated"),
        "第二次 new": (rep["apply_2"].get("stats") or {}).get("new"),
        "第二次 updated": (rep["apply_2"].get("stats") or {}).get("updated"),
        "两次后行数相同": s1["total"] == s2["total"] and s1["research"] == s2["research"],
        "first_seen 未被改写": s1["first_seen"] == s2["first_seen"],
        "updated_at 已推进（第二次写）": s1["updated_at"] != s2["updated_at"],
    }

    # 3) 子进程核验（字段/API/闸门/正向对照）
    w = L.run([L.PY, str(WORKER)], env=env, timeout=300)
    parsed = None
    for line in w["stdout"].splitlines():
        if line.startswith("@@RESULT@@"):
            parsed = json.loads(line[len("@@RESULT@@"):])
    rep["核验worker"] = {"rc": w["returncode"], "elapsed_s": w["elapsed_s"],
                       "stderr_tail": w["stderr"][-2000:]}
    if parsed is None:
        rep["核验worker"]["parse_error"] = True
        rep["核验worker"]["stdout_tail"] = w["stdout"][-3000:]
    else:
        rep["核验"] = parsed

    # 4) 生产零写入
    after = {k: L.fingerprint(p) for k, p in PROD_FILES.items()}
    rep["生产文件_后"] = after
    same = {k: L.same_file(before[k], after[k]) for k in before}
    rep["生产零写入"] = {"逐文件": same, "全部未变": all(v["same"] for v in same.values())}

    # 5) 判定
    k = rep.get("核验") or {}
    api = k.get("API") or {}
    scan = k.get("路由扫描") or {}
    ctrl = k.get("正向对照") or {}
    agg = k.get("逐项统计") or {}
    n = agg.get("n") or 0
    # 前置状态可能是空库（0 行）也可能是已导入过（289 行）——判据按**相对增量**写，
    # 不写死 0，否则这份证据只能在特定前置状态下复跑。
    dstat = rep["dry_run"].get("stats") or {}
    n0 = dstat.get("rows_before")
    r0 = dstat.get("existing_research_rows")
    a1 = rep["apply_1"].get("stats") or {}
    checks = {
        "dry-run 未写库（行数不变）": n0 is not None and dstat.get("rows_after") == n0,
        "apply 后研究行恰好 289（不是累加）": agg.get("origin_ok") == n == 289,
        "第一次 apply 的净增 = 289 − 原有研究行":
            (n0 is not None and r0 is not None
             and a1.get("rows_after") == n0 + (289 - r0)),
        "第二次导入 new=0 / updated=289":
            rep["幂等"]["第二次 new"] == 0 and rep["幂等"]["第二次 updated"] == 289,
        "两次后行数不变": rep["幂等"]["两次后行数相同"] is True,
        "first_seen 未被改写": rep["幂等"]["first_seen 未被改写"] is True,
        "289 行全为 historical_research": agg.get("origin_ok") == n == 289,
        "289 行全为 historical_not_executable": agg.get("exec_ok") == n,
        "289 行 available_at 全为空（未替代）": agg.get("avail_null") == n,
        "289 行 available_at 标 unproven": agg.get("unproven_ok") == n,
        "289 行 config_hash 已填": agg.get("cfg_ok") == n,
        "289 行 data_version 为空（未编造）": agg.get("dv_null") == n,
        "289 行 role=research_only": agg.get("role_ok") == n,
        "289 行 approved=false": agg.get("approved_false") == n,
        "289 行 run_id 正确": agg.get("run_ok") == n,
        "bar_open_time 与 event_time 不同（不互相替代）": agg.get("bar_open_distinct") == n,
        "bar_open_time 是 1h 确认 bar 开盘（open<close，跨度 3,599,999ms）": agg.get("bar_order_ok") == n,
        "15m 入场 bar 开盘晚于确认 bar 收盘（执行时点单列）": agg.get("entry_after_close") == n,
        "研究范围 API 读到 289": api.get("研究范围(scope=all&origin=historical_research)", {}).get("total") == 289,
        "当前交易区 API 仍为 0": api.get("当前交易区(scope=current)", {}).get("total") == 0,
        "研究信号未混入当前区": api.get("研究信号是否混入当前区") is False,
        "闸门扫描 origin=historical_research 为 0": scan.get("origin=historical_research") == 0,
        "闸门扫描 origin=None 也为 0": scan.get("origin=None（最坏情况，无来源过滤）") == 0,
        "正向对照能扫到合成 enterable 行": (ctrl.get("合成 enterable+live 行被扫到（无过滤）") or 0) == 1,
        "正向对照删除后归零": ctrl.get("删除后回到 0") == 0,
        "生产行情库与四本账本零写入": rep["生产零写入"]["全部未变"] is True,
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = ("隔离演练通过：289 条研究信号可幂等接入、可按研究范围读取、"
                       "当前交易区不可见、路由扫描扫不到、生产零写入。"
                       if not fail else f"存在未通过项：{fail}")

    p = L.dump("20_隔离演练.json", rep)
    print("== 20 隔离演练 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
