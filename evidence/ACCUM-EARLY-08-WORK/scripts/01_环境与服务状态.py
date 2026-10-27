# -*- coding: utf-8 -*-
"""01｜现行环境与服务状态（只读）。

只做三件事：取状态、取身份、取边界。**不启动、不停止、不重启任何进程。**
输出：out/01_环境与服务状态.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib08 as L  # noqa: E402


def http_get(url: str, timeout: float = 20.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return {"ok": True, "code": r.status, "json": json.loads(raw)}
            except Exception:
                return {"ok": True, "code": r.status, "text": raw[:4000]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def ps_identity(pid: int) -> dict:
    """进程身份：只用 lsof（本机沙箱禁 exec `ps`）。"""
    return L.proc_identity(pid)


def _prune_status(raw: dict) -> dict:
    """把 start.sh status 的整份 JSON 裁剪成证据够用的形状。

    原始输出约 100~200KB（含 526 个标的的逐标的状态机状态、箱体、跳过原因）。
    证据只需要「身份 / 健康 / 计数 / 边界」，逐标的明细用计数代替，不整份搬。
    """
    out: dict = {}
    for k, v in raw.items():
        if k != "services":
            out[k] = v
    svcs = {}
    for name, s in (raw.get("services") or {}).items():
        s = dict(s or {})
        snap = s.pop("runtime_snapshot", None)
        if snap:
            pruned = {}
            for sk, sv in snap.items():
                if isinstance(sv, dict) and len(sv) > 30:
                    # 逐标的大表（symbol -> 状态 / 跳过原因）只留条数与样本，不整份搬
                    pruned[sk] = {
                        "_pruned": True, "_size": len(sv),
                        "_sample_keys": list(sv)[:5],
                        "_sample_value": next(iter(sv.values())) if sv else None,
                    }
                else:
                    pruned[sk] = sv
            s["runtime_snapshot_pruned"] = pruned
        svcs[name] = s
    out["services"] = svcs
    return out


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc(), "工程根": str(L.PROJECT)}

    # ── 1. 分支与提交（工作树脏是既有事实，git 不能当基线）──────────────────
    rep["git"] = {
        "branch": L.run(["git", "branch", "--show-current"], cwd=L.PROJECT)["stdout"].strip(),
        "head": L.run(["git", "log", "-1", "--format=%H %ad %s", "--date=iso"],
                      cwd=L.PROJECT)["stdout"].strip(),
        "status_short_head": L.run(["git", "status", "--short"], cwd=L.PROJECT)["stdout"][:2000],
        "说明": "本工程 git HEAD 长期落后工作树，改动一律按文件 mtime 与内容核对，不用 git status 当基线",
    }

    # ── 2. start.sh status（唯一启动/状态入口，只读动作）────────────────────
    st = L.run(["sh", "start.sh", "status"], cwd=L.BACKEND, timeout=180)
    rep["start_sh_status"] = {"returncode": st["returncode"], "elapsed_s": st["elapsed_s"],
                              "stdout_bytes": st.get("stdout_len"), "stderr": st["stderr"]}
    try:
        raw = json.loads(st["stdout"])
        rep["start_sh_status"]["json"] = _prune_status(raw)
    except Exception as exc:  # noqa: BLE001
        rep["start_sh_status"]["parse_error"] = f"{type(exc).__name__}: {exc}"
        rep["start_sh_status"]["raw_head"] = st["stdout"][:4000]

    # ── 3. 监听端口（只看本工程相关端口；是否只绑本机回环是安全边界）────────
    all_listen = L.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=30)["stdout"]
    keep = []
    for line in all_listen.splitlines():
        if any(f":{p}" in line for p in (8787, 3000)) or "COMMAND" in line:
            keep.append(line)
    rep["listeners"] = "\n".join(keep)
    rep["listeners_note"] = "只保留 8787（后端 API）与 3000（前端 dev）两行；其余为本机无关进程，不搬进证据"

    # ── 4. 进程身份（PID / 启动时刻 / 命令行 / 工作目录）────────────────────
    pids = {}
    for name, f in (("api", "backend.pid"), ("worker", "worker.pid"),
                    ("runtime", "runtime-3a-8787.pid")):
        p = L.BACKEND / f
        if p.exists():
            txt = p.read_text(encoding="ascii", errors="replace").strip()
            if txt.isdigit():
                pids[name] = int(txt)
    for name, f in (("api", "backend-8799.pid"), ("api_8899", "backend-8899.pid")):
        p = L.BACKEND / f
        if p.exists():
            pids[name] = int(p.read_text(encoding="ascii", errors="replace").strip() or 0)
    rep["pid_files"] = {k: ps_identity(v) for k, v in pids.items() if v}

    # 采集器：不在 pid 文件里，从监听/进程表按名字找
    rep["collector_probe"] = L.run(
        ["pgrep", "-fl", "realtime_collector"], timeout=15)["stdout"]

    # ── 5. 健康与接口（只读 GET）────────────────────────────────────────────
    rep["http"] = {
        "api_health": http_get("http://127.0.0.1:8787/api/health"),
        "v1_health": http_get("http://127.0.0.1:8787/api/v1/health"),
        "signals_current": http_get("http://127.0.0.1:8787/api/v1/signals?scope=current&limit=500"),
        "signals_all": http_get("http://127.0.0.1:8787/api/v1/signals?scope=all&limit=5"),
        "signals_observe": http_get("http://127.0.0.1:8787/api/v1/signals/observe"),
    }
    # 只留「有信息量」的部分，避免把 334 条明细整份搬进证据
    obs = rep["http"]["signals_observe"].get("json") or {}
    if obs:
        rep["http"]["signals_observe_summary"] = {
            "quality_status": (obs.get("quality") or {}).get("status"),
            "total": obs.get("total"), "shown": obs.get("shown"),
            "truncated": obs.get("truncated"), "counts": obs.get("counts"),
            "observing_total": obs.get("observing_total"),
            "data_not_ready": obs.get("data_not_ready"),
            "universe": obs.get("universe"),
            "generated_at": obs.get("generated_at"),
            "age_seconds": obs.get("age_seconds"),
            "writer_pid_alive": obs.get("writer_pid_alive"),
            "scan_errors_total": obs.get("scan_errors_total"),
            "stale": obs.get("stale"),
            "first_item": (obs.get("items") or [{}])[0].get("symbol") if obs.get("items") else None,
        }
        rep["http"]["signals_observe"] = {"ok": True, "code": 200,
                                          "note": "明细 334 条，已在 summary 里摘要，原文不搬"}
    for k in ("signals_current", "signals_all"):
        d = rep["http"][k].get("json") or {}
        if d:
            rep["http"][k] = {
                "ok": True, "code": 200,
                "total": d.get("total"), "items_n": len(d.get("items") or []),
                "quality": d.get("quality"),
                "strategy_version": d.get("strategy_version"),
                "rule_version": d.get("rule_version"),
                "param_hash": d.get("param_hash"),
                "params_source": d.get("params_source"),
                "missing_reason": d.get("missing_reason"),
            }

    # ── 6. 关键文件身份（运行时状态文件 / 观察明细 / 生产信号库）────────────
    rep["files"] = {
        "observe_json": L.file_fingerprint(L.LOCKS / "paper_runtime_3a_observe.json"),
        "status_json": L.file_fingerprint(L.LOCKS / "paper_runtime_3a_status.json"),
        "live_signals_db": L.file_fingerprint(L.LIVE_DB),
        "market_db": L.file_fingerprint(L.MARKET_DB),
    }

    # ── 7. 生产信号库只读结构（表 + 行数 + 幂等键是否唯一索引）──────────────
    con = L.ro_conn(L.LIVE_DB)
    try:
        tbls = [dict(r) for r in con.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view','index') "
            "ORDER BY type,name")]
        counts = {}
        for t in ("live_signal", "live_run", "live_account_snapshot"):
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                counts[t] = f"ERR {exc}"
        idx = [dict(r) for r in con.execute("PRAGMA index_list(live_signal)")]
        cols = [dict(r) for r in con.execute("PRAGMA table_info(live_signal)")]
        rep["live_db"] = {"path": str(L.LIVE_DB), "objects": tbls, "counts": counts,
                          "indexes": idx, "columns": cols}
    finally:
        con.close()

    # ── 8. 解释器与依赖（启动脚本默认解释器）────────────────────────────────
    rep["python"] = {"start_sh_default": L.PY,
                     "exists": Path(L.PY).exists(),
                     "version": L.run([L.PY, "-V"])["stdout"].strip()
                     or L.run([L.PY, "-V"])["stderr"].strip()}

    p = L.dump("01_环境与服务状态.json", rep)

    # 控制台摘要
    j = rep["start_sh_status"].get("json") or {}
    print("== 01 环境与服务状态 ==")
    print("branch:", rep["git"]["branch"], "| head:", rep["git"]["head"][:60])
    print("overall_health:", j.get("overall_health"))
    for k in ("api", "worker", "collector", "runtime"):
        s = (j.get("services") or {}).get(k) or {}
        print(f"  {k:10s} pid={s.get('pid')} status={s.get('status')} "
              f"identity_matched={s.get('identity_matched')} "
              f"health={((s.get('http_health') or {}).get('ok'))}")
    print("live_signal rows:", rep["live_db"]["counts"].get("live_signal"))
    print("observe:", rep["http"].get("signals_observe_summary"))
    print("signals_current total:", (rep["http"]["signals_current"] or {}).get("total"))
    print("->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
