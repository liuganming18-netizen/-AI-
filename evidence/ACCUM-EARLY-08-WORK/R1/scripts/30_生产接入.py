# -*- coding: utf-8 -*-
"""30｜生产接入：把反重力 arm_b 的 289 条研究信号写进**生产信号库**。

写什么：`框架/backend/live/live_signals.sqlite3` 的 `live_signal`（289 行，
        `origin='historical_research'` / `executable_state='historical_not_executable'`）。
不写什么：行情库（13.9GB）、四本纸面账本、任何交易表 —— 前后指纹必须逐位不变。

写前先做一份一致快照（SQLite 在线备份）到 `R1/isolated/pre_import/`，
回滚方式见 `R1/README.md`（精确 DELETE 289 行，不动 live 行）。

输出：out/30_生产接入.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

IMPORT = L.BACKEND / "research_signal_import.py"
BACKUP_DIR = L.ISO / "pre_import"

WATCH = {
    "行情库": L.MARKET_DB,
    "纸面账本_3a": L.PAPER_DIR / "paper_3a.sqlite3",
    "纸面账本_3b": L.PAPER_DIR / "paper_3b.sqlite3",
    "纸面账本_3c": L.PAPER_DIR / "paper_3c.sqlite3",
    "纸面账本_3d": L.PAPER_DIR / "paper_3d.sqlite3",
}


def backend_pid() -> dict:
    """后端进程身份（不重启、不 kill；只读取证）。"""
    p = L.BACKEND / "backend.pid"
    pid = p.read_text().strip() if p.exists() else None
    rec = {"pid_file": str(p), "pid": pid, "alive": False, "lsof_ok": None}
    if pid:
        try:
            os.kill(int(pid), 0)
            rec["alive"] = True
        except Exception as exc:  # noqa: BLE001
            rec["alive"] = f"{type(exc).__name__}"
        try:
            cp = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True, timeout=20)
            rec["lsof_ok"] = cp.returncode == 0
            rec["holds_live_db"] = "live_signals.sqlite3" in (cp.stdout or "")
        except Exception as exc:  # noqa: BLE001
            rec["lsof_ok"] = f"{type(exc).__name__}"
    return rec


def db_state(db: Path) -> dict:
    con = L.ro_conn(db)
    try:
        return {
            "rows_total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
            "by_origin": {r[0]: r[1] for r in con.execute(
                "SELECT origin, COUNT(*) FROM live_signal GROUP BY origin")},
            "by_exec": {r[0]: r[1] for r in con.execute(
                "SELECT executable_state, COUNT(*) FROM live_signal GROUP BY executable_state")},
            "by_scheme": {r[0]: r[1] for r in con.execute(
                "SELECT strategy_key, COUNT(*) FROM live_signal "
                "WHERE origin='historical_research' GROUP BY strategy_key ORDER BY strategy_key")},
            "current_area_rows": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered') "
                "AND executable_state='enterable'").fetchone()[0],
        }
    finally:
        con.close()


def sample_row(db: Path) -> dict:
    con = L.ro_conn(db)
    try:
        r = con.execute("SELECT * FROM live_signal WHERE origin='historical_research' "
                        "ORDER BY signal_uid LIMIT 1").fetchone()
        if not r:
            return {}
        d = dict(r)
        pl = json.loads(d["payload_json"])
        return {
            "signal_uid": d["signal_uid"], "symbol": d["symbol"], "timeframe": d["timeframe"],
            "origin": d["origin"], "executable_state": d["executable_state"],
            "strategy_key": d["strategy_key"], "strategy_version": d["strategy_version"],
            "config_hash": d["config_hash"], "data_version": d["data_version"],
            "event_time": d["event_time"], "confirmed_at": d["confirmed_at"],
            "available_at": d["available_at"], "bar_open_time": d["bar_open_time"],
            "bar_close_time": d["bar_close_time"],
            "payload_role": pl.get("role"), "payload_run_id": pl.get("run_id"),
            "payload_approved": pl.get("approved"),
            "available_at_status": (pl.get("timestamps") or {}).get("available_at_status"),
            "entry_bar_open_ms": (pl.get("timestamps") or {}).get("entry_bar_open_ms"),
        }
    finally:
        con.close()


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    rep["后端进程_前"] = backend_pid()
    rep["生产信号库_前"] = {"指纹": L.fingerprint(L.LIVE_DB), "状态": db_state(L.LIVE_DB)}
    before_watch = {k: L.fingerprint(p) for k, p in WATCH.items()}

    # 1) 写前快照（可回滚）。**已存在就不覆盖**：写前快照只在第一次接入时才有意义，
    #    复跑时再拷一份「已经写过 289 行」的库冒充写前状态，是造假。
    bak = BACKUP_DIR / "live_signals_before_import.sqlite3"
    if bak.exists():
        fp = L.fingerprint(bak)
        rep["写前快照"] = {"kept_existing": True, "dst": str(bak), "bytes": fp.get("size"),
                        "sha256": fp.get("sha256"),
                        "note": "已存在，未覆盖（覆盖会把「已写过」的库当写前状态）"}
    else:
        rep["写前快照"] = L.backup_db(L.LIVE_DB, bak)
        rep["写前快照"]["sha256"] = L.fingerprint(bak)["sha256"]
    rep["回滚方式"] = {
        "首选（精确删除本单写入的研究行，不动 live 行）":
            "DELETE FROM live_signal WHERE origin='historical_research' "
            "AND json_extract(payload_json,'$.source')='antigravity_08_backtest';",
        "兜底（整库还原，会丢掉快照之后的 live 行，需先确认无新 live 信号）":
            f"cp {bak} {L.LIVE_DB}",
    }

    # 2) 真写生产库（不设 LIVE_DB 覆盖 ⇒ 默认即生产）
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{L.PROJECT / '框架'}:{L.BACKEND}:{L.PROJECT}"
    env.pop("LIVE_DB", None)
    manifest_out = L.OUT / "manifest_antigravity_08_arm_b.json"
    run = L.run([L.PY, str(IMPORT), "--run-dir", str(L.RUN_B), "--require-ok",
                 "--apply", "--json", "--manifest-out", str(manifest_out)], env=env)
    try:
        rep["导入"] = json.loads(run["stdout"])
    except Exception:  # noqa: BLE001
        rep["导入"] = {"parse_error": True, "stdout": run["stdout"][-3000:],
                       "stderr": run["stderr"][-3000:]}
    rep["导入"]["rc"] = run["returncode"]
    rep["导入"]["elapsed_s"] = run["elapsed_s"]
    rep["manifest_path"] = str(manifest_out)
    rep["manifest_sha256"] = L.fingerprint(manifest_out).get("sha256") if manifest_out.exists() else None

    # 3) 读回
    rep["生产信号库_后"] = {"指纹": L.fingerprint(L.LIVE_DB), "状态": db_state(L.LIVE_DB),
                          "单条样本": sample_row(L.LIVE_DB)}
    after_watch = {k: L.fingerprint(p) for k, p in WATCH.items()}
    rep["后端进程_后"] = backend_pid()

    same = {k: L.same_file(before_watch[k], after_watch[k]) for k in before_watch}
    rep["非目标库零写入"] = {"逐文件": same, "全部未变": all(v["same"] for v in same.values())}

    # 4) 判定
    st0 = rep["生产信号库_前"]["状态"]
    st1 = rep["生产信号库_后"]["状态"]
    stats = rep["导入"].get("stats") or {}
    # 判据按**相对增量**写：生产库可能是空库（首次接入）也可能已有 289 行（复跑），
    # 写死「写前必须 0 行」会让这份证据只能跑一次。
    pre_research = (st0["by_origin"] or {}).get("historical_research", 0)
    expect_new = 289 - pre_research
    checks = {
        "导入进程 rc=0": rep["导入"]["rc"] == 0,
        "写前库内行数与导入器自报一致": stats.get("rows_before") == st0["rows_total"],
        "净增 = 289 − 写前已有研究行": stats.get("new") == expect_new,
        "写后总行数 = 写前行数 + 净增": st1["rows_total"] == st0["rows_total"] + expect_new,
        "写后研究行恰好 289（不是累加）": st1["by_origin"] == {"historical_research": 289},
        "写后全部 executable_state=historical_not_executable":
            st1["by_exec"] == {"historical_not_executable": 289},
        "三方案 105/87/97": st1["by_scheme"] == {"ACCUM-RESEARCH-v0::4a": 105,
                                              "ACCUM-RESEARCH-v0::4b": 87,
                                              "ACCUM-RESEARCH-v0::4c": 97},
        "当前交易区仍为 0 行": st1["current_area_rows"] == 0,
        "样本 available_at 为空": rep["生产信号库_后"]["单条样本"].get("available_at") is None,
        "样本 available_at 标 unproven":
            rep["生产信号库_后"]["单条样本"].get("available_at_status") == "unproven",
        "样本 bar_open < bar_close（列语义正确）":
            (rep["生产信号库_后"]["单条样本"].get("bar_open_time") or 0)
            < (rep["生产信号库_后"]["单条样本"].get("bar_close_time") or 0),
        "行情库与四本账本零写入": rep["非目标库零写入"]["全部未变"] is True,
        "后端进程未重启（pid 未变且存活）":
            rep["后端进程_前"]["pid"] == rep["后端进程_后"]["pid"]
            and rep["后端进程_后"]["alive"] is True,
        "写前快照已生成": Path(rep["写前快照"]["dst"]).exists() and rep["写前快照"]["bytes"] > 0,
        "manifest 已落盘": rep["manifest_sha256"] is not None,
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = ("生产接入完成：289 条研究信号已入库，全部标不可执行；"
                       "行情库与账本零写入；后端未重启。"
                       if not fail else f"存在未通过项：{fail}")

    p = L.dump("30_生产接入.json", rep)
    # 历次运行留痕：本脚本是幂等的，复跑会覆盖 `30_生产接入.json`。
    # 第一次「0 行 → 289 行」的记录一旦被覆盖就再也拿不回来（本单已经踩过一次），
    # 所以每次运行都往历次文件里追加一行摘要。
    hist_p = L.OUT / "30_生产接入_历次.json"
    hist = json.loads(hist_p.read_text(encoding="utf-8")) if hist_p.exists() else []
    hist.append({
        "时刻": rep["生成时刻"],
        "写前行数": stats.get("rows_before"), "写前研究行": stats.get("existing_research_rows"),
        "净增": stats.get("new"), "更新": stats.get("updated"),
        "写后总行数": stats.get("rows_after"),
        "写前快照sha256": (rep.get("写前快照") or {}).get("sha256"),
        "快照是否复用": (rep.get("写前快照") or {}).get("kept_existing") is True,
        "pass": f"{rep['判定']['pass_n']}/{rep['判定']['total_n']}",
    })
    hist_p.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    print("历次运行:", hist_p, f"（共 {len(hist)} 次）")
    print("== 30 生产接入 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
