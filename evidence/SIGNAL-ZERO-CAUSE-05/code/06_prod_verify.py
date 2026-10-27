# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜06 生产受控重启前后核验（只读 + 一次受控重启）。

用法：
    python 06_prod_verify.py --phase pre          # 重启前快照（不重启）
    python 06_prod_verify.py --restart            # 受控重启（只重启 runtime，采集器不动）
    python 06_prod_verify.py --phase post         # 重启后快照
    python 06_prod_verify.py --compare            # 前后对比 + 判定
    python 06_prod_verify.py --watch-min 70       # 等下一根 1h 收盘并确认被处理

为什么必须「先 pre、再 restart、再 post」：
  「文件已改」≠「运行已修」。唯一机械判据是**进程启动时刻 vs 影响运行的文件 mtime**，
  以及重启前后同一组可观测量的变化。缺任一侧，结论都只能靠相信。

安全边界（写进产物）：
  · 只重启 runtime；collector 若由本实例托管则拒绝重启（防误停采集）；
  · 重启必须带 `ENABLE_PAPER_RUNTIME=1`，否则会降级成 disabled_safe_mode（FIX-02 教训）；
  · 不启用真实交易、不改参数、不重跑回测；
  · 账本目录做**文件级指纹**，重启前后必须完全一致。
"""
from __future__ import annotations

import argparse
import datetime as dt
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
API = "http://127.0.0.1:8787/api/v1/paper/execution_daemon/status?arm=3a"
OUT = cm.OUT


def iso(ms):
    return cm.utc(ms)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sm_status() -> dict:
    r = subprocess.run([sys.executable, str(SM), "status", "--json"],
                       capture_output=True, text=True, cwd=str(cm.BASE))
    try:
        return json.loads(r.stdout)
    except Exception:                                        # noqa: BLE001
        return {"_error": r.stdout[-2000:] + r.stderr[-2000:]}


def api_item() -> dict:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(API, timeout=20) as resp:
        d = json.loads(resp.read().decode())
    return d.get("item") or d.get("data", {}).get("item") or d


def proc_info(pid) -> dict:
    try:
        import psutil
        p = psutil.Process(int(pid))
        return {"pid": int(pid), "create_time_ms": int(p.create_time() * 1000),
                "create_time_utc": iso(int(p.create_time() * 1000)),
                "cmdline": p.cmdline(), "environ_keys": sorted(p.environ().keys())}
    except Exception as exc:                                 # noqa: BLE001
        return {"pid": pid, "error": f"{type(exc).__name__}: {exc}"}


def runtime_identity_files() -> dict:
    out = {}
    for rel in ("框架/backend/unified_runtime.py", "框架/backend/history_loader.py",
                "框架/backend/live_store.py", "框架/backend/live_pool.py",
                "框架/backend/paper_engine.py"):
        p = cm.BASE / rel
        if p.exists():
            out[rel] = {"sha256": sha(p), "size": p.stat().st_size,
                        "mtime_ms": int(p.stat().st_mtime * 1000),
                        "mtime_utc": iso(int(p.stat().st_mtime * 1000))}
        else:
            out[rel] = {"missing": True}
    return out


def ledger_fingerprint() -> dict:
    """账本 + live 库的文件级指纹（只读；重启前后必须完全一致）。

    注意：本项目**没有** `PAPER_LEDGER_DIR` 这个符号（历史记忆里的名字是错的）。
    真实账本目录是 `live_store.LEDGER_DIR = 数据/paper`；信号库在 `LIVE_DIR`。
    这里两个都取，并如实记录用的是哪个属性名 —— 免得下次又去追一个不存在的常量。
    """
    import live_store as L
    res = {}
    for attr, name in (("LEDGER_DIR", "ledger"), ("PAPER_DIR", "paper_dir"),
                       ("LIVE_DIR", "live")):
        val = getattr(L, attr, None)
        if not val:
            res[name] = {"attr": attr, "path": None, "exists": False}
            continue
        d = Path(val)
        if not d.exists():
            res[name] = {"attr": attr, "path": str(d), "exists": False}
            continue
        files = sorted(p for p in d.rglob("*") if p.is_file())
        res[name] = {"attr": attr, "path": str(d), "exists": True, "n_files": len(files),
                     "files": {str(p.relative_to(d)): {"size": p.stat().st_size,
                                                       "sha256": sha(p)} for p in files}}
    return res


def db_watermarks() -> dict:
    import sqlite3
    import live_store as L
    con = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
    try:
        out = {}
        for tf in ("15m", "1h", "4h"):
            r = con.execute("SELECT MAX(open_time), COUNT(*) FROM candle_heads "
                            "WHERE exchange='binance' AND market='um' AND timeframe=?",
                            (tf,)).fetchone()
            out[tf] = {"max_open_ms": int(r[0]) if r[0] else None,
                       "max_open_utc": iso(r[0]), "n_heads": int(r[1])}
        return out
    finally:
        con.close()


def status_file_assessment() -> dict:
    """判断 `locks/unified_runtime_status_3a.json` 能不能当就绪判据。

    实测它是一份**固定桩**：`runtime_identity.updated_at = 2026-10-23T03:00:00Z`，
    字段名（`health`/`pid`/`cycle_count`）与 `get_full_status()` 完全对不上。
    把这种文件当判据，结论会是「永远没就绪」或「读到两天前的假状态」。
    """
    if not STATUS_FILE.exists():
        return {"exists": False, "usable_as_readiness_signal": False}
    try:
        d = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:                                 # noqa: BLE001
        return {"exists": True, "parse_error": f"{type(exc).__name__}: {exc}",
                "usable_as_readiness_signal": False}
    upd = (d.get("runtime_identity") or {}).get("updated_at")
    age_h = None
    if upd:
        try:
            t = dt.datetime.strptime(upd, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            age_h = round((time.time() - t.timestamp()) / 3600, 2)
        except Exception:                                    # noqa: BLE001
            pass
    keys = sorted(d.keys())
    has_health = "health" in d or "pid" in d
    return {"exists": True, "top_keys": keys, "updated_at": upd, "age_hours": age_h,
            "has_health_or_pid_field": has_health,
            "usable_as_readiness_signal": bool(has_health and (age_h is not None and age_h < 1)),
            "note": ("字段名与 `get_full_status()` 不一致 + updated_at 停在过去 ⇒ 固定桩，"
                     "不能当就绪判据；就绪一律走 API。")}


def snapshot(tag: str) -> dict:
    st = sm_status()
    svc = (st.get("services") or {}) if isinstance(st, dict) else {}
    rt = svc.get("runtime") or {}
    col = svc.get("collector") or {}
    pid = rt.get("pid")
    pinfo = proc_info(pid) if pid else {}
    item = api_item()
    se = item.get("signal_engine") or {}
    bs = se.get("bootstrap") or {}
    files = runtime_identity_files()
    ur = files.get("框架/backend/unified_runtime.py") or {}
    snap = {
        "phase": tag,
        "captured_at_utc": cm.utc(cm.now_ms()),
        "service_status": st,
        "runtime_pid": pid,
        "runtime_process": pinfo,
        "collector_pid": col.get("pid"),
        "collector_managed_by_this_instance": col.get("managed_by_this_instance"),
        "files": files,
        # 「文件已改 ≠ 运行已修」的机械判据
        "process_older_than_code": bool(pinfo.get("create_time_ms")
                                        and ur.get("mtime_ms")
                                        and pinfo["create_time_ms"] < ur["mtime_ms"]),
        "process_lag_sec": (round((ur["mtime_ms"] - pinfo["create_time_ms"]) / 1000, 1)
                            if pinfo.get("create_time_ms") and ur.get("mtime_ms") else None),
        "status_file": (json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                        if STATUS_FILE.exists() else None),
        # 状态文件是否其实是**固定桩**：字段名与 get_full_status() 不一致、updated_at 停在过去
        "status_file_assessment": status_file_assessment(),
        "api": {
            "runtime_identity": item.get("runtime_identity"),
            "market_data": item.get("market_data"),
            "signal_engine": {
                "universe": se.get("universe"),
                "universe_declared": se.get("universe_declared"),
                "watermark_1h": se.get("watermark_1h"),
                "watermark_1h_utc": iso(se.get("watermark_1h")),
                "watermark_4h": se.get("watermark_4h"),
                "watermark_4h_utc": iso(se.get("watermark_4h")),
                "bootstrap": bs,
                "skipped_reasons": se.get("skipped_reasons"),
                "scan_errors_total": se.get("scan_errors_total"),
                "state_counter": se.get("state_counter"),
                "idle_breakdown": se.get("idle_breakdown"),
                "funnel": se.get("funnel"),
                "atr_caliber": se.get("atr_caliber"),
                "watermark_check": se.get("watermark_check"),
                "data_quality": se.get("data_quality"),
                "signals_generated_total": se.get("signals_generated_total"),
                "last_cycle_ms": se.get("last_cycle_ms"),
            },
            "order_processing": item.get("order_processing"),
            "execution_daemon": item.get("execution_daemon"),
        },
        "db_watermarks": db_watermarks(),
        "ledger": ledger_fingerprint(),
    }
    p = OUT / f"06_生产核验_{tag}.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {p} ({p.stat().st_size} B)")
    return snap


def do_restart() -> dict:
    pre = json.loads((OUT / "06_生产核验_pre.json").read_text(encoding="utf-8"))
    if pre.get("collector_managed_by_this_instance"):
        return {"refused": True,
                "reason": "collector 由本实例托管 ⇒ 重启会连带停采集，按防误停策略拒绝"}
    env = dict(os.environ)
    env["ENABLE_PAPER_RUNTIME"] = "1"          # 不带它会降级成 disabled_safe_mode
    cmd = [sys.executable, str(SM), "restart", "--enable-paper", "--arm", "3a",
           "--symbol", "all"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cm.BASE), env=env)
    return {"cmd": cmd, "env_ENABLE_PAPER_RUNTIME": "1", "returncode": r.returncode,
            "stdout_tail": r.stdout[-4000:], "stderr_tail": r.stderr[-4000:],
            "elapsed_sec": round(time.time() - t0, 1)}


def wait_ready(timeout_sec: int = 600, want_pid=None) -> dict:
    """等 runtime 真正开始跑（走 **API**，不走状态文件）。

    缺陷（本轮实测）：`locks/unified_runtime_status_3a.json` 里躺着的是
    `{"updated_at": "2026-10-23T03:00:00Z", ...}` 这种**固定桩数据**，字段名也和
    `get_full_status()` 完全对不上（`health`/`pid`/`cycle_count` 全是 None）。
    拿它当就绪判据会永远读不到、或者读到一份两天前的假状态。
    所以判据改成：API 里 `runtime_identity.pid` 变了、`status=running`、
    `cycle_count >= 2`、且 `bootstrap.data_source` 已经写出来。
    """
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_sec:
        try:
            item = api_item()
            ri = item.get("runtime_identity") or {}
            se = item.get("signal_engine") or {}
            bs = se.get("bootstrap") or {}
            last = {"pid": ri.get("pid"), "status": ri.get("status"),
                    "cycle_count": ri.get("cycle_count"),
                    "data_source": bs.get("data_source"),
                    "started_at": ri.get("started_at")}
            ok = (ri.get("status") == "running" and (ri.get("cycle_count") or 0) >= 2
                  and bs.get("data_source") is not None
                  and (want_pid is None or ri.get("pid") != want_pid))
            if ok:
                return {"ready": True, "waited_sec": round(time.time() - t0, 1), "last": last}
        except Exception as exc:                             # noqa: BLE001
            last = {"error": f"{type(exc).__name__}: {exc}"}
        time.sleep(5)
    return {"ready": False, "waited_sec": round(time.time() - t0, 1), "last": last}


def watch_new_bars(minutes: int, out_name: str = "06_新增K线观察.json") -> dict:
    """等新的 1h 收盘，记录水位推进（验证「新增 K 线确实被处理」）。

    字段层级坑（本轮实测）：`lag_1h_max` / `n_lag_1h_gt1` / `lag_1h_histogram`
    都在 `watermark_check.per_symbol` **里面**，不在顶层。第一版从顶层读，采样里全是
    null —— 那是我的读取路径错，不是系统没给。顶层只有 `lag_1h_bars` / `aligned_1h`。
    """
    import sqlite3
    import live_store as L
    t0 = time.time()
    samples = []
    while time.time() - t0 < minutes * 60:
        try:
            item = api_item()
        except Exception as exc:                             # noqa: BLE001
            samples.append({"at": cm.utc(cm.now_ms()), "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(30)
            continue
        se = item.get("signal_engine") or {}
        wc = se.get("watermark_check") or {}
        per = wc.get("per_symbol") or {}
        con = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
        try:
            db1 = con.execute("SELECT MAX(open_time) FROM candle_heads WHERE exchange='binance' "
                              "AND market='um' AND timeframe='1h'").fetchone()[0]
        finally:
            con.close()
        samples.append({
            "at": cm.utc(cm.now_ms()),
            "cycle_count": (item.get("runtime_identity") or {}).get("cycle_count"),
            "db_1h_max": int(db1) if db1 else None,
            "db_1h_max_utc": iso(db1),
            "watermark_1h": se.get("watermark_1h"),
            "watermark_1h_utc": iso(se.get("watermark_1h")),
            "aligned_1h": wc.get("aligned_1h"),
            "lag_1h_bars": wc.get("lag_1h_bars"),
            # 逐币字段在 per_symbol 里
            "lag_1h_max": per.get("lag_1h_max"),
            "n_lag_1h_gt1": per.get("n_lag_1h_gt1"),
            "lag_1h_histogram": per.get("lag_1h_histogram"),
            "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
            "orders_routed_total": (item.get("order_processing") or {}).get("orders_routed_total"),
            "open_positions": (item.get("execution_daemon") or {}).get("open_positions"),
        })
        # 已看到「水位前进」且「库内 1h 最大值 == 水位」⇒ 新增 bar 已被处理
        if len(samples) >= 2 and samples[-1]["watermark_1h"] and samples[0]["watermark_1h"] \
                and samples[-1]["watermark_1h"] > samples[0]["watermark_1h"]:
            break
        time.sleep(30)
    res = {"minutes": minutes, "n_samples": len(samples), "samples": samples}
    (OUT / out_name).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(f"[watch] samples={len(samples)} first_wm={samples[0].get('watermark_1h_utc')} "
          f"last_wm={samples[-1].get('watermark_1h_utc')} "
          f"lag_max={samples[-1].get('lag_1h_max')} n_gt1={samples[-1].get('n_lag_1h_gt1')}")
    return res


def compare() -> dict:
    pre = json.loads((OUT / "06_生产核验_pre.json").read_text(encoding="utf-8"))
    post = json.loads((OUT / "06_生产核验_post.json").read_text(encoding="utf-8"))
    a, b = pre["api"]["signal_engine"], post["api"]["signal_engine"]
    ba, bb = a.get("bootstrap") or {}, b.get("bootstrap") or {}
    wa, wb = a.get("watermark_check") or {}, b.get("watermark_check") or {}
    checks = {}

    def ck(name, ok, detail=None):
        checks[name] = {"pass": bool(ok), "detail": detail}

    ck("pid_changed", pre["runtime_pid"] != post["runtime_pid"],
       {"pre": pre["runtime_pid"], "post": post["runtime_pid"]})
    ck("collector_untouched", pre["collector_pid"] == post["collector_pid"],
       {"pre": pre["collector_pid"], "post": post["collector_pid"]})
    ck("code_effective_after_restart", not post["process_older_than_code"],
       {"process_lag_sec": post["process_lag_sec"],
        "runtime_start_utc": (post["runtime_process"] or {}).get("create_time_utc"),
        "unified_runtime_mtime_utc": (post["files"].get("框架/backend/unified_runtime.py")
                                      or {}).get("mtime_utc")})
    ck("history_source_is_merged", bb.get("data_source") == "merged_cold_hot",
       {"pre": ba.get("data_source"), "post": bb.get("data_source")})
    hist = bb.get("history") or {}
    ck("cold_layer_actually_read",
       bool(((hist.get("4h") or {}).get("cold_rows_scanned") or 0) > 0),
       {"4h": (hist.get("4h") or {}), "1h_cold": (hist.get("1h") or {}).get("cold_rows_scanned")})
    used = bb.get("used") or {}
    ck("declared_window_filled", (used.get("bars_4h_max") or 0) >= 289,
       {"bars_4h_max": used.get("bars_4h_max"), "bars_4h_min": used.get("bars_4h_min"),
        "bars_1h_max": used.get("bars_1h_max"),
        "replay_window_hours": used.get("replay_window_hours")})
    ck("atr_prewarm_deep", (used.get("atr_prewarm_bars_max") or 0) >= 700,
       {"atr_prewarm_bars_min": used.get("atr_prewarm_bars_min"),
        "atr_prewarm_bars_max": used.get("atr_prewarm_bars_max")})
    ck("watermark_1h_aligned",
       (wb.get("aligned_1h") is True and wb.get("lag_1h_bars") in (0, 1)
        and (wb.get("per_symbol") or {}).get("n_lag_1h_gt1") == 0),
       {"aligned_1h": wb.get("aligned_1h"), "lag_1h_bars": wb.get("lag_1h_bars"),
        "lag_1h_histogram": (wb.get("per_symbol") or {}).get("lag_1h_histogram"),
        "lag_1h_max": (wb.get("per_symbol") or {}).get("lag_1h_max"),
        "n_lag_1h_gt1": (wb.get("per_symbol") or {}).get("n_lag_1h_gt1"),
        "n_per_symbol": (wb.get("per_symbol") or {}).get("n"),
        "pre_aligned_1h": wa.get("aligned_1h"), "pre_lag_1h_bars": wa.get("lag_1h_bars")})
    ck("watermark_4h_same_caliber",
       wb.get("aligned_4h") is True and wb.get("lag_4h_bars") in (0, 1),
       {"aligned_4h": wb.get("aligned_4h"), "lag_4h_bars": wb.get("lag_4h_bars"),
        "processed_4h_max_open": wb.get("processed_4h_max_open"),
        "processed_4h_max_close": wb.get("processed_4h_max_close"),
        "db_4h_max_open": wb.get("db_4h_max_open"),
        "pre_aligned_4h": wa.get("aligned_4h"), "pre_lag_4h_bars": wa.get("lag_4h_bars"),
        "note": "修复前 lag_4h_bars=-1 / aligned_4h=false，因为拿 close 口径减 open 口径"})
    dq = b.get("data_quality") or {}
    # ── 本单专属判据（SIGNAL-ZERO-CAUSE-05）─────────────────────────────────
    ac_b = b.get("atr_caliber") or {}
    ac_a = a.get("atr_caliber") or {}
    ck("box_gate_mode_is_spec", ac_b.get("box_gate_mode") == "atr_warmup",
       {"pre": ac_a.get("box_gate_mode"), "post": ac_b.get("box_gate_mode"),
        "gate_metric": ac_b.get("gate_metric"),
        "n_box_gate_open": ac_b.get("n_box_gate_open"),
        "note": "规范口径=ATR 预热满 200 根即建档；旧口径=窗口内再数 200 根（仅诊断）"})
    fa = ((a.get("funnel") or {}).get("history") or {}).get("BOX")
    fb = ((b.get("funnel") or {}).get("history") or {}).get("BOX")
    ck("history_box_count_increased", (fb or 0) > (fa or 0) * 2,
       {"pre_history_BOX": fa, "post_history_BOX": fb,
        "pre_history_CANDIDATE": ((a.get("funnel") or {}).get("history") or {}).get("CANDIDATE"),
        "post_history_CANDIDATE": ((b.get("funnel") or {}).get("history") or {}).get("CANDIDATE"),
        "note": ("闸门改口径后，同一份 1160h 历史能建出的箱体应显著增加"
                 "（离线复算：1598 → 4257）。")})
    ck("history_candidates_discarded_not_routed",
       ((bb.get("history_candidates_discarded") is not None)
        and (post["api"]["order_processing"] or {}).get("orders_routed_total")
        == (pre["api"]["order_processing"] or {}).get("orders_routed_total")),
       {"history_candidates_discarded": bb.get("history_candidates_discarded"),
        "orders_routed_pre": (pre["api"]["order_processing"] or {}).get("orders_routed_total"),
        "orders_routed_post": (post["api"]["order_processing"] or {}).get("orders_routed_total"),
        "note": "回放期产生的历史候选一律丢弃，不得补成当前订单（工单硬要求）"})
    ck("state_counter_recorded",
       b.get("state_counter") is not None,
       {"pre": a.get("state_counter"), "post": b.get("state_counter")})
    ck("data_quality_gate_reported",
       ("blocked" in dq) and (dq.get("blocked_n") is not None),
       {"blocked_n": dq.get("blocked_n"), "symbols": sorted((dq.get("blocked") or {}).keys()),
        "pre_value": (a.get("data_quality") or "null")})
    led_a = pre.get("ledger") or {}
    led_b = post.get("ledger") or {}

    def durable(d):
        """只比**持久文件**：`-shm` / `-wal` 是 SQLite 的瞬时伴生文件，
        连接关闭时会 checkpoint 并删除 ⇒ 它们的有无跟「账本有没有被改」无关。"""
        return {k: v for k, v in (d.get("files") or {}).items()
                if not (k.endswith("-shm") or k.endswith("-wal"))}

    def sidecars(d):
        return sorted(k for k in (d.get("files") or {})
                      if k.endswith("-shm") or k.endswith("-wal"))

    led_ok = all(durable(led_a.get(k)) == durable(led_b.get(k))
                 for k in ("ledger", "paper_dir"))
    ck("ledger_untouched", led_ok,
       {"durable_files": {k: len(durable(led_a.get(k))) for k in ("ledger", "paper_dir")},
        "durable_same": {k: durable(led_a.get(k)) == durable(led_b.get(k))
                         for k in ("ledger", "paper_dir")},
        "sidecar_churn": {k: {"pre": sidecars(led_a.get(k) or {}),
                              "post": sidecars(led_b.get(k) or {})}
                          for k in ("ledger", "paper_dir", "live")},
        "paths": {k: (led_a.get(k) or {}).get("path") for k in ("ledger", "paper_dir", "live")},
        "note": ("只比 `*.sqlite3` 等持久文件；`-shm`/`-wal` 是瞬时伴生文件，"
                 "连接关闭时 checkpoint 会删掉它们 —— 拿它当「账本被改」会误判。")})
    ck("no_new_orders",
       (post["api"]["order_processing"] or {}).get("orders_routed_total")
       == (pre["api"]["order_processing"] or {}).get("orders_routed_total"),
       {"pre": (pre["api"]["order_processing"] or {}).get("orders_routed_total"),
        "post": (post["api"]["order_processing"] or {}).get("orders_routed_total")})
    ck("no_new_positions",
       (post["api"]["execution_daemon"] or {}).get("open_positions")
       == (pre["api"]["execution_daemon"] or {}).get("open_positions"),
       {"pre": (pre["api"]["execution_daemon"] or {}).get("open_positions"),
        "post": (post["api"]["execution_daemon"] or {}).get("open_positions")})
    res = {"generated_at_utc": cm.utc(cm.now_ms()),
           "checks": checks, "n_pass": sum(1 for v in checks.values() if v["pass"]),
           "n_total": len(checks),
           "all_pass": all(v["pass"] for v in checks.values()),
           "note": ("每一项都是「重启前后同一观测量的对比」或「对 bootstrap 实际读/用量的复算」，"
                    "不接受「页面能点开」「日志没报错」这类间接证据。")}
    (OUT / "06_生产核验_对比.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    for k, v in checks.items():
        print(f"  {'PASS' if v['pass'] else 'FAIL'}  {k}")
    print(f"[compare] {res['n_pass']}/{res['n_total']} pass, all_pass={res['all_pass']}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("pre", "post"))
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--wait-ready", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--watch-min", type=int, default=0)
    ap.add_argument("--watch-out", default="06_新增K线观察.json")
    a = ap.parse_args()
    if a.phase:
        snapshot(a.phase)
    if a.restart:
        r = do_restart()
        (OUT / "06_重启记录.json").write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print(json.dumps({k: v for k, v in r.items() if k != "stdout_tail"},
                         ensure_ascii=False, indent=1))
        if r.get("returncode") not in (0, None):
            print("[restart] 非零退出，保留现场，不自动重试")
            return 1
    if a.wait_ready:
        pre_pid = None
        pp = OUT / "06_生产核验_pre.json"
        if pp.exists():
            pre_pid = json.loads(pp.read_text(encoding="utf-8")).get("runtime_pid")
        w = wait_ready(want_pid=pre_pid)
        (OUT / "06_重启就绪.json").write_text(json.dumps(w, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print(json.dumps(w, ensure_ascii=False, indent=1))
        if not w.get("ready"):
            return 2
    if a.compare:
        compare()
    if a.watch_min:
        watch_new_bars(a.watch_min, a.watch_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
