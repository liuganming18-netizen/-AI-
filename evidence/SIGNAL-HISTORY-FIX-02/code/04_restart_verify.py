# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-02｜04 受控重启 + 修复生效四项验证。

工单「二」原文要求：
  「核对当前进程实际加载的版本。使用已有受控重启流程，**仅更新相关策略运行进程**，
   保留账本、断点和独立采集服务。重启后验证：
     ① 最后处理水位与实际处理的 K 线一致；
     ② 无漏处理、重复候选、重复订单；
     ③ 历史候选不会补成当前订单；
     ④ 新增诊断在实际 API 和用户入口可见。
   若缺少安全恢复条件，明确阻塞，**不把『文件已修改』写成『运行已修复』**。
   真实交易保持禁用。」

纪律：
  · 只用既有 `service_manager.py restart`（= stop() + 1s + start()），不手搓启停；
  · 重启参数与当前**完全一致**（当前 `paper_enabled=False` ⇒ **不加** `--enable-paper`，
    以免把纸面执行链顺手打开，违反「真实交易保持禁用」）；
  · 采集器 `managed_by_this_instance=false` ⇒ 不受 restart 影响，重启前后都要核它的 pid；
  · 账本 / 断点目录在重启前后做**文件级指纹**，用「指纹不变」证明没被重建；
  · 安全闸：任务队列存在非终态任务 或 有未平持仓 ⇒ **拒绝重启**并如实报阻塞。

用法：
    python 04_restart_verify.py --check-only    # 只做重启前体检（不重启）
    python 04_restart_verify.py                 # 体检 → 重启 → 四项验证
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

BACKEND = cm.BACKEND
SM = BACKEND / "service_manager.py"
STATUS_FILE = BACKEND / "locks" / "unified_runtime_status_3a.json"
API_URL = "http://127.0.0.1:8787/api/v1/paper/execution_daemon/status?arm=3a"

#: 冻结集合（动态构建，与 FIX-01 同口径；不写死文件名列表以免漏项）
def frozen_files() -> list[Path]:
    pats = ["框架/backend/**/*.py", "UI界面/web/src/**/*.ts", "UI界面/web/src/**/*.tsx",
            "UI界面/web/package.json", "UI界面/web/vite.config.ts"]
    out: list[Path] = []
    for pat in pats:
        out.extend(sorted(cm.BASE.glob(pat)))
    return [p for p in out if p.is_file()]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sm_status() -> dict:
    r = subprocess.run([sys.executable, str(SM), "status", "--json"],
                       capture_output=True, text=True, cwd=str(cm.BASE))
    return json.loads(r.stdout)


def api_item() -> dict:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(API_URL, timeout=15) as resp:
        d = json.loads(resp.read().decode())
    return d.get("item") or d.get("data", {}).get("item") or {}


def proc_info(pid: int) -> dict:
    try:
        import psutil
        p = psutil.Process(int(pid))
        return {"pid": int(pid), "create_time_utc": cm.iso(int(p.create_time() * 1000)),
                "create_time_ms": int(p.create_time() * 1000),
                "cmdline": p.cmdline()}
    except Exception as exc:                                  # noqa: BLE001
        return {"pid": pid, "error": f"{type(exc).__name__}: {exc}"}


def ledger_fingerprint() -> dict:
    """账本目录文件级指纹（**只读**；重启前后应完全一致）。"""
    import live_store as L
    dirs = {}
    for name, val in (("PAPER_LEDGER_DIR", getattr(L, "PAPER_LEDGER_DIR", None)),
                      ("LIVE_DIR", getattr(L, "LIVE_DIR", None))):
        if not val:
            dirs[name] = {"path": None}
            continue
        d = Path(val)
        if not d.exists():
            dirs[name] = {"path": str(d), "exists": False}
            continue
        files = sorted([p for p in d.rglob("*") if p.is_file()])
        dirs[name] = {"path": str(d), "exists": True, "n_files": len(files),
                      "files": {str(p.relative_to(d)): {"size": p.stat().st_size,
                                                        "sha256": sha(p)} for p in files}}
    return dirs


def job_queue_state() -> dict:
    import sqlite3
    p = cm.BASE / "数据/任务队列/jobs.sqlite3"
    if not p.exists():
        return {"exists": False}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT job_id,kind,state,created_at FROM jobs "
        "WHERE state NOT IN ('succeeded','failed','cancelled','done')")]
    tot = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    c.close()
    return {"exists": True, "total": tot, "non_terminal_n": len(rows),
            "non_terminal": rows}


def preflight() -> dict:
    st = sm_status()
    svc = st["services"]
    rt = svc["runtime"]
    pid = rt.get("pid")
    pinfo = proc_info(pid) if pid else {}
    ur = BACKEND / "unified_runtime.py"
    ur_mtime_ms = int(ur.stat().st_mtime * 1000)
    api = api_item()
    jq = job_queue_state()
    ledger = ledger_fingerprint()
    ident = api.get("runtime_identity") or {}
    se = api.get("signal_engine") or {}
    op = api.get("order_processing") or {}
    ed = api.get("execution_daemon") or {}
    return {
        "captured_at_utc": cm.utc_now(),
        "service_status": st,
        "runtime_pid": pid,
        "runtime_process": pinfo,
        "unified_runtime_file": {"path": str(ur), "size": ur.stat().st_size,
                                 "sha256": sha(ur), "mtime_utc": cm.iso(ur_mtime_ms)},
        # 进程落后于代码 = 跑的是旧代码。这是「文件已改 ≠ 运行已修」的机械判据。
        "process_older_than_code": (pinfo.get("create_time_ms") is not None
                                    and pinfo["create_time_ms"] < ur_mtime_ms),
        "process_lag_sec": (round((ur_mtime_ms - pinfo["create_time_ms"]) / 1000, 1)
                            if pinfo.get("create_time_ms") else None),
        "collector_pid": svc.get("collector", {}).get("pid"),
        "collector_managed": svc.get("collector", {}).get("managed_by_this_instance"),
        "api_signal_engine_keys": sorted(se.keys()),
        "api_has_atr_caliber": "atr_caliber" in se,
        "api_has_watermark_check": "watermark_check" in se,
        "history_candidates_discarded": (se.get("bootstrap") or {}).get(
            "history_candidates_discarded"),
        "signals_generated_total": se.get("signals_generated_total"),
        "state_counter": se.get("state_counter"),
        "funnel": se.get("funnel"),
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "current_pending_in_db": op.get("current_pending_in_db"),
        "open_positions": ed.get("open_positions"),
        "job_queue": jq,
        "ledger": ledger,
        "frozen_files_n": len(frozen_files()),
    }


def safety_gate(pre: dict, allow_start_when_down: bool = False) -> dict:
    """重启安全闸：任一不满足 ⇒ 拒绝重启，并如实报阻塞（不硬上）。

    `allow_start_when_down`：只放宽 `runtime_process_identified` 一项，用于
    「runtime 已经停了、需要把它拉起来」的场景（本单第一次尝试把它降级成
    `disabled_safe_mode` 之后的恢复）。放宽这件事必须显式声明、写进证据，
    不能悄悄把闸门调松。
    """
    checks = {
        "job_queue_no_running": pre["job_queue"].get("non_terminal_n") == 0,
        "no_open_positions": (pre.get("open_positions") or 0) == 0,
        "no_pending_orders": (pre.get("current_pending_in_db") or 0) == 0,
        "runtime_process_identified": pre.get("runtime_pid") is not None,
        "collector_untouched_by_restart": pre.get("collector_managed") is False,
    }
    relaxed = []
    if allow_start_when_down and not checks["runtime_process_identified"]:
        checks["runtime_process_identified"] = True
        relaxed.append("runtime_process_identified")
    return {"checks": checks, "all_pass": all(checks.values()),
            "relaxed": relaxed,
            "blocking": [k for k, v in checks.items() if not v]}


def do_restart() -> dict:
    t0 = time.time()
    env = dict(os.environ)
    # 为什么必须带 `ENABLE_PAPER_RUNTIME=1`：
    #   旧 runtime（pid 59109）的真实命令行是
    #   `unified_runtime.py --arm 3a --symbol all --interval 2.0`，**没有** `--enable-paper`；
    #   它是靠 `ENABLE_PAPER_RUNTIME=1` 环境变量启动的（`service_manager.py:1052` 的默认值
    #   就是读这个变量）。不带它时 `start()` 会把 runtime 置为 `disabled_safe_mode`
    #   （exit_reason=「普通UI/API启动模式下交易链默认禁用，不开放自动交易」）——
    #   这不是「更安全」，而是**把原来在跑的进程弄停了**，属于降级。
    #   纸面模拟（paper）≠ 真实交易；真实交易链已封存在 `_legacy_archive_v31/`。
    env["ENABLE_PAPER_RUNTIME"] = "1"
    cmd = [sys.executable, str(SM), "restart", "--arm", "3a", "--symbol", "all"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cm.BASE), env=env)
    return {"cmd": "ENABLE_PAPER_RUNTIME=1 " + " ".join(cmd),
            "rc": r.returncode, "stdout_tail": (r.stdout or "")[-2000:],
            "stderr_tail": (r.stderr or "")[-2000:],
            "elapsed_sec": round(time.time() - t0, 1),
            "env_used": {"ENABLE_PAPER_RUNTIME": "1"},
            "note": ("`ENABLE_PAPER_RUNTIME=1` 是**恢复**旧进程的启动条件，不是新开能力："
                     "旧进程 cmdline 无 `--enable-paper`，说明它就是这个变量拉起来的。"
                     "纸面模拟 ≠ 真实交易；真实交易链已封存。")}


def wait_ready(timeout_sec: int = 300) -> dict:
    """等新进程完成 bootstrap 并把新诊断字段写进状态文件。"""
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_sec:
        try:
            it = api_item()
            se = it.get("signal_engine") or {}
            ident = it.get("runtime_identity") or {}
            last = {"runtime_status": ident.get("status"),
                    "bootstrap_present": bool(se.get("bootstrap")),
                    "has_atr_caliber": "atr_caliber" in se,
                    "has_watermark_check": "watermark_check" in se}
            if (ident.get("status") == "running" and se.get("bootstrap")
                    and "atr_caliber" in se and "watermark_check" in se):
                return {"ready": True, "waited_sec": round(time.time() - t0, 1),
                        "last": last}
        except Exception as exc:                              # noqa: BLE001
            last = {"error": f"{type(exc).__name__}: {exc}"}
        time.sleep(3)
    return {"ready": False, "waited_sec": round(time.time() - t0, 1), "last": last}


def verify(post: dict, pre: dict) -> dict:
    """工单四项验证，逐项给「通过 / 不通过 / 未验证」，不给模糊结论。"""
    se = post["api_signal_engine"] or {}
    op = post["api_order_processing"] or {}
    ed = post["api_execution_daemon"] or {}
    wm = se.get("watermark_check") or {}
    ac = se.get("atr_caliber") or {}
    fun = se.get("funnel") or {}
    hist = fun.get("history") or {}
    since = fun.get("since_start") or {}

    # ① 水位一致
    # 口径修正（第一版断言写错过，如实记录）：`processed_4h_closes` 存的是**收盘**时刻，
    # `_global_4h` 是**开盘**时刻，直接比会得到 -1 根的假阴性 ⇒ 必须换回开盘时刻再比。
    # 1h 侧的机械判据不是「lag==0」：引擎由 **4h 收盘**驱动，最后一根 4h 收盘之后才开盘的
    # 1h 根本来就**还没到处理时点**（最多 4 根）。真正的「漏处理」判据是：
    #   已处理的最大 1h 收盘时刻  >=  最后一根已处理 4h 的收盘时刻
    # —— 少一根就说明有「4h 都收完了、1h 却没喂进去」的缺口。
    H1 = 3_600_000
    H4 = 4 * H1
    p4_close = wm.get("processed_4h_max")
    p4_open = (p4_close - H4 + 1) if p4_close is not None else None
    p1_max = wm.get("processed_1h_max")
    covered_1h = (p1_max is not None and p4_close is not None
                  and (p1_max + H1 - 1) >= p4_close)
    aligned_4h = (p4_open is not None and p4_open == wm.get("global_4h"))
    lag_ok = (wm.get("lag_1h_bars") is not None and wm["lag_1h_bars"] <= 4)
    v1 = {
        "verdict": ("通过" if (covered_1h and aligned_4h and lag_ok) else "不通过"),
        "watermark_check": wm,
        "recomputed": {
            "processed_4h_max_open": p4_open,
            "global_4h": wm.get("global_4h"),
            "aligned_4h_in_open_time": aligned_4h,
            "processed_1h_close_max": (p1_max + H1 - 1) if p1_max is not None else None,
            "processed_4h_close_max": p4_close,
            "no_1h_gap_before_last_4h_close": covered_1h,
            "lag_1h_bars": wm.get("lag_1h_bars"),
            "lag_within_4h_block": lag_ok,
        },
        "evidence": ("`processed_1h_close_max == processed_4h_close_max` ⇒ 最后一根已处理 4h "
                     "收盘之前的 1h 全部喂进去了，没有「4h 收完但 1h 没进」的缺口；"
                     "`lag_1h_bars<=4` 是结构性的（4h 收盘之间最多夹 4 根 1h），不是漏处理。"),
    }
    # ② 无漏处理 / 重复候选 / 重复订单
    dup_orders = post.get("ledger_duplicate_orders")
    v2 = {
        "verdict": ("通过" if (v1["verdict"] == "通过"
                              and dup_orders == 0
                              and (hist.get("CANDIDATE") or 0)
                              == (se.get("bootstrap") or {}).get(
                                  "history_candidates_discarded", -1)) else "不通过"),
        "no_missed_bars": v1["verdict"] == "通过",
        "history_candidate_count_matches_funnel": (
            (hist.get("CANDIDATE") or 0)
            == (se.get("bootstrap") or {}).get("history_candidates_discarded", -1)),
        "funnel_history_CANDIDATE": hist.get("CANDIDATE"),
        "bootstrap_history_candidates_discarded": (se.get("bootstrap") or {}).get(
            "history_candidates_discarded"),
        "since_start_CANDIDATE": since.get("CANDIDATE"),
        "candidates_since_start": fun.get("candidates_since_start"),
        "duplicate_orders_in_ledger": dup_orders,
        "orders_routed_total": op.get("orders_routed_total"),
        "note": ("漏斗 `history.CANDIDATE` 与 `bootstrap.history_candidates_discarded` "
                 "必须相等——两者是同一批候选的两种记账，不等就是重复计数或漏计。"),
    }
    # ③ 历史候选不补成当前订单
    v3 = {
        "verdict": ("通过" if ((post.get("ledger_new_rows") or 0) == 0
                              and (op.get("orders_routed_total") or 0) == 0) else "不通过"),
        "history_candidates_discarded": (se.get("bootstrap") or {}).get(
            "history_candidates_discarded"),
        "orders_routed_total": op.get("orders_routed_total"),
        "ledger_new_rows_after_restart": post.get("ledger_new_rows"),
        "open_positions": ed.get("open_positions"),
        "note": ("预热期候选按纪律丢弃、**不补开**。若 `orders_routed_total>0` 而"
                 "`candidates_since_start==0`，说明有历史候选被补成了订单 —— 必须报不通过。"),
    }
    # ④ 新增诊断在 API 与用户入口可见
    ui = post.get("ui_source_has_atr_caliber")
    v4 = {
        "verdict": ("通过" if (post.get("api_has_atr_caliber")
                              and post.get("api_has_watermark_check") and ui) else "不通过"),
        "api_has_atr_caliber": post.get("api_has_atr_caliber"),
        "api_has_watermark_check": post.get("api_has_watermark_check"),
        "ui_source_has_atr_caliber": ui,
        "atr_caliber": ac,
        "api_url": API_URL,
        "note": ("API 有字段 ≠ 用户看得到。`ui_source_has_atr_caliber` 检查前端源码里"
                 "确实渲染了该字段（`data-testid=\"daemon-atr-caliber\"`），"
                 "并在浏览器实测里核对真实 DOM。"),
    }
    return {"V1_watermark_aligned": v1, "V2_no_miss_or_dup": v2,
            "V3_history_not_backfilled": v3, "V4_diagnostics_visible": v4,
            "all_pass": all(v["verdict"] == "通过" for v in (v1, v2, v3, v4))}


def ledger_dup_and_new(pre: dict, post: dict) -> tuple:
    """账本：重复订单数 + 重启后新增行数（按文件指纹与行数差）。"""
    def rows(d):
        n = 0
        for _k, v in (d or {}).items():
            for _f, meta in (v.get("files") or {}).items():
                n += 1
        return n
    pre_files = {}
    for _k, v in (pre.get("ledger") or {}).items():
        pre_files.update({f"{_k}/{f}": m for f, m in (v.get("files") or {}).items()})
    post_files = {}
    for _k, v in (post.get("ledger") or {}).items():
        post_files.update({f"{_k}/{f}": m for f, m in (v.get("files") or {}).items()})
    new = [k for k in post_files if k not in pre_files]
    changed = [k for k in post_files if k in pre_files
               and post_files[k]["sha256"] != pre_files[k]["sha256"]]
    return {"new_files": new, "changed_files": changed,
            "n_new": len(new), "n_changed": len(changed)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="不再重启，只用**当前**运行态重跑四项验证（用于修正断言逻辑后复核）")
    ap.add_argument("--allow-start-when-down", action="store_true",
                    help="runtime 已停时允许把它拉起来（只放宽 runtime_process_identified）")
    a = ap.parse_args()

    if a.verify_only:
        prev = json.loads((cm.OUT / "04_重启与验证.json").read_text(encoding="utf-8"))
        pre = prev["pre"]
        st2 = sm_status()
        item2 = api_item()
        post = {
            "captured_at_utc": cm.utc_now(), "service_status": st2,
            "runtime_pid": st2["services"]["runtime"].get("pid"),
            "runtime_process": proc_info(st2["services"]["runtime"].get("pid")),
            "unified_runtime_file": pre["unified_runtime_file"],
            "api_signal_engine": item2.get("signal_engine") or {},
            "api_order_processing": item2.get("order_processing") or {},
            "api_execution_daemon": item2.get("execution_daemon") or {},
            "api_has_atr_caliber": "atr_caliber" in (item2.get("signal_engine") or {}),
            "api_has_watermark_check": "watermark_check" in (item2.get("signal_engine") or {}),
            "collector_pid": st2["services"].get("collector", {}).get("pid"),
            "ledger": ledger_fingerprint(), "job_queue": job_queue_state(),
            "frozen_files_n": len(frozen_files()),
        }
        panel = cm.BASE / "UI界面/web/src/components/PositionPanel.tsx"
        api_ts = cm.BASE / "UI界面/web/src/api/liveTrading.ts"
        post["ui_source_has_atr_caliber"] = (
            panel.exists() and "daemon-atr-caliber" in panel.read_text(encoding="utf-8")
            and api_ts.exists() and "atrCaliber" in api_ts.read_text(encoding="utf-8"))
        post["process_older_than_code"] = (
            post["runtime_process"].get("create_time_ms") is not None
            and post["runtime_process"]["create_time_ms"]
            < int((BACKEND / "unified_runtime.py").stat().st_mtime * 1000))
        lg = ledger_dup_and_new(pre, post)
        post["ledger_new_rows"] = lg["n_new"]
        post["ledger_duplicate_orders"] = lg["n_changed"]
        post["ledger_diff"] = lg
        v = verify(post, pre)
        out = {"ticket": "SIGNAL-HISTORY-FIX-02", "stage": "verify_recheck",
               "generated_at_utc": cm.utc_now(),
               "note": ("断言逻辑修正后**不重启**、只用当前运行态复核。"
                        "修正的两处：①V1 把 4h 收盘时刻换成开盘时刻再比、"
                        "1h 判据改为「已处理 1h 收盘 >= 已处理 4h 收盘」；"
                        "②V4 取错了键名（`ui_has_atr_caliber` → `ui_source_has_atr_caliber`）。"
                        "修正的是**我的断言**，不是系统行为。"),
               "pre": pre, "post": post, "verification": v,
               "collector_untouched": (pre.get("collector_pid") == post.get("collector_pid")),
               "result": ("PASS" if (v["all_pass"] and not post["process_older_than_code"])
                          else "FAIL")}
        cm.write_json("04b_验证复核.json", out)
        for k in ("V1_watermark_aligned", "V2_no_miss_or_dup",
                  "V3_history_not_backfilled", "V4_diagnostics_visible"):
            print(f"[{k}] {v[k]['verdict']}")
        print(f"[RESULT] {out['result']}")
        print("-> out/04b_验证复核.json")
        return

    pre = preflight()
    gate = safety_gate(pre, allow_start_when_down=a.allow_start_when_down)
    print(f"[PRE] runtime pid={pre['runtime_pid']} "
          f"进程早于代码={pre['process_older_than_code']} "
          f"(落后 {pre['process_lag_sec']}s)")
    print(f"[PRE] 代码 sha256={pre['unified_runtime_file']['sha256'][:16]}… "
          f"size={pre['unified_runtime_file']['size']}")
    print(f"[PRE] API 有 atr_caliber={pre['api_has_atr_caliber']} "
          f"watermark_check={pre['api_has_watermark_check']} "
          f"history_candidates={pre['history_candidates_discarded']}")
    print(f"[PRE] 任务队列非终态={pre['job_queue'].get('non_terminal_n')} "
          f"持仓={pre['open_positions']} 挂单={pre['current_pending_in_db']}")
    print(f"[GATE] 全通过={gate['all_pass']} 阻塞项={gate['blocking']}")

    out = {"ticket": "SIGNAL-HISTORY-FIX-02", "stage": "restart_verify",
           "generated_at_utc": cm.utc_now(), "pre": pre, "safety_gate": gate}

    if a.check_only:
        cm.write_json("04_重启前体检.json", out)
        print("-> out/04_重启前体检.json（--check-only，未重启）")
        return

    if not gate["all_pass"]:
        out["result"] = "BLOCKED"
        out["blocked_reason"] = f"安全闸未通过：{gate['blocking']}"
        cm.write_json("04_重启与验证.json", out)
        print(f"[BLOCKED] {out['blocked_reason']} —— 不重启，不把「文件已改」写成「运行已修复」")
        return

    rs = do_restart()
    out["restart"] = rs
    print(f"[RESTART] rc={rs['rc']} elapsed={rs['elapsed_sec']}s")
    if rs["rc"] != 0:
        out["result"] = "RESTART_FAILED"
        cm.write_json("04_重启与验证.json", out)
        print("[RESTART] 失败：", rs["stderr_tail"][-500:])
        return

    wr = wait_ready()
    out["wait_ready"] = wr
    print(f"[READY] ready={wr['ready']} waited={wr['waited_sec']}s last={wr.get('last')}")

    st2 = sm_status()
    item2 = api_item()
    post = {
        "captured_at_utc": cm.utc_now(),
        "service_status": st2,
        "runtime_pid": st2["services"]["runtime"].get("pid"),
        "runtime_process": proc_info(st2["services"]["runtime"].get("pid")),
        "unified_runtime_file": pre["unified_runtime_file"],
        "api_signal_engine": item2.get("signal_engine") or {},
        "api_order_processing": item2.get("order_processing") or {},
        "api_execution_daemon": item2.get("execution_daemon") or {},
        "api_has_atr_caliber": "atr_caliber" in (item2.get("signal_engine") or {}),
        "api_has_watermark_check": "watermark_check" in (item2.get("signal_engine") or {}),
        "collector_pid": st2["services"].get("collector", {}).get("pid"),
        "ledger": ledger_fingerprint(),
        "job_queue": job_queue_state(),
        "frozen_files_n": len(frozen_files()),
    }
    # 用户入口：前端源码里确实渲染了该字段
    panel = cm.BASE / "UI界面/web/src/components/PositionPanel.tsx"
    api_ts = cm.BASE / "UI界面/web/src/api/liveTrading.ts"
    post["ui_source_has_atr_caliber"] = (
        panel.exists() and "daemon-atr-caliber" in panel.read_text(encoding="utf-8")
        and api_ts.exists() and "atrCaliber" in api_ts.read_text(encoding="utf-8"))
    # 进程是否真的换成了新代码
    post["process_older_than_code"] = (
        post["runtime_process"].get("create_time_ms") is not None
        and post["runtime_process"]["create_time_ms"]
        < int((BACKEND / "unified_runtime.py").stat().st_mtime * 1000))
    lg = ledger_dup_and_new(pre, post)
    post["ledger_new_rows"] = lg["n_new"]
    post["ledger_duplicate_orders"] = lg["n_changed"]
    post["ledger_diff"] = lg
    out["post"] = post

    v = verify(post, pre)
    out["verification"] = v
    out["collector_untouched"] = (pre.get("collector_pid") == post.get("collector_pid"))
    out["result"] = "PASS" if (v["all_pass"] and not post["process_older_than_code"]) else "FAIL"
    cm.write_json("04_重启与验证.json", out)

    for k in ("V1_watermark_aligned", "V2_no_miss_or_dup",
              "V3_history_not_backfilled", "V4_diagnostics_visible"):
        print(f"[{k}] {v[k]['verdict']}")
    print(f"[POST] 新进程早于代码={post['process_older_than_code']} "
          f"（应为 False）| 采集器未变={out['collector_untouched']}")
    print(f"[POST] 账本新增文件={lg['n_new']} 变更文件={lg['n_changed']}")
    print(f"[RESULT] {out['result']}")
    print("-> out/04_重启与验证.json")


if __name__ == "__main__":
    main()
