#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LIVE-CHECK-09 探针②：运行版本 vs 07-R1 交付版本（要求 1）

要回答的问题
------------
「现在跑的这一份，是不是 07-R1 交付的那一份？」

三个层次，缺一不可
------------------
1. **交付指纹对得上**：磁盘上 5 个改动文件的 sha256 == 07-R1 `80_delivery_identity.json`
   里登记的 sha256。这一层证明「文件没被事后改过」。
2. **进程确实加载了这一份**：进程启动时刻 ≥ 每个文件的 mtime。
   这一层证明「跑的不是内存里的旧代码」。注意：Python 在 import 时就把源码编译进内存，
   所以「文件后来被改、进程还在跑旧代码」是真实存在的坑 —— 启动时刻与 mtime 的先后
   是唯一能自证的判据（本机 `ps` 被拒，只能靠 `psutil.Process.create_time()`）。
3. **三条路径的实现文件没变**：信号生成（`live_signal_engine.py`）、
   入库（`unified_runtime.py` 的 `persist_candidates`）、
   接口（`live_api.py` + `adapter.py`）四个文件的 sha256 与 07-R1 记录时一致，
   且进程启动时刻晚于它们的 mtime。

为什么还要第 3 层
----------------
07-R1 的 `80_delivery_identity.json` 只登记了 5 个「本单改动」文件，
没有登记 `live_signal_engine.py` / `unified_runtime.py` / `live_api.py`。
本单要求「确认真实生产使用相同信号生成、入库和接口路径」，
所以必须把这三个也纳入指纹，否则「路径相同」只能靠嘴说。
本探针会把这四个文件的当前 sha 落盘，作为**本单的基线**，供后续单子比对。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"
PREV = ROOT / "evidence/SIGNAL-UI-LINK-07-R1/out/80_delivery_identity.json"

# 07-R1 登记的 5 个「本单改动」文件，按加载方式分成两组
# —— 这个区分是本单踩过的坑：前端文件由 vite dev server 通过 HMR 加载，
#    **根本不进 Python 进程**，拿它和 API 进程的启动时刻比会得出假失败。
DELIVERED_BACKEND = [
    "框架/backend/adapter.py",
]
DELIVERED_FRONTEND = [
    "UI界面/web/src/api/signalsState.ts",
    "UI界面/web/src/api/useMarketData.ts",
    "UI界面/web/src/components/SignalListPanel.tsx",
    "UI界面/web/src/App.tsx",
]
DELIVERED = DELIVERED_BACKEND + DELIVERED_FRONTEND

# 三条路径的实现文件（本单新纳入基线）
PATHS = {
    "信号生成": "框架/backend/live_signal_engine.py",
    "入库(持久化)": "框架/backend/unified_runtime.py",
    "接口(实时层)": "框架/backend/live_api.py",
    "接口(合并层)": "框架/backend/adapter.py",
}

# 不该被本单改动的关键文件
GUARD = [
    "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
    "框架/backend/live/paper_new_entry_gate.json",
    "框架/backend/live_store.py",
]


def sha(p: Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def mtime_utc(p: Path):
    if not p.exists():
        return None
    return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def listen_pid(port: int):
    import subprocess
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True, timeout=15)
        out = r.stdout.strip().splitlines()
        return int(out[0]) if out else None
    except Exception:                                        # noqa: BLE001
        return None


def proc_start_utc(pid: int):
    try:
        import psutil
        return dt.datetime.fromtimestamp(
            psutil.Process(pid).create_time(), dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except Exception as exc:                                 # noqa: BLE001
        return f"error:{exc}"


def main():
    ap = argparse.ArgumentParser(
        description="核对「当前运行版本」是否等于 SIGNAL-UI-LINK-07-R1 交付版本")
    ap.add_argument("--out", default=str(OUT / "20_version_check.json"),
                    help="输出路径（默认 out/20_version_check.json）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论，不逐项打印")
    args = ap.parse_args()

    def say(msg):
        if not args.quiet:
            print(msg)

    out = {
        "probe": "SIGNAL-LIVE-CHECK-09/20_version_check",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "baseline_source": str(PREV.relative_to(ROOT)),
        "checks": [],
        "ok": False,
    }

    def chk(name, ok, detail):
        out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'[OK]' if ok else '[FAIL]'} {name} — {detail}")

    prev = json.loads(PREV.read_text(encoding="utf-8")) if PREV.exists() else None
    chk("找到 07-R1 交付指纹文件", prev is not None,
        str(PREV.relative_to(ROOT)) if prev else "缺失")

    # ── 第 1 层：交付指纹 ────────────────────────────────────────────────────
    delivered = {}
    for rel in DELIVERED:
        p = ROOT / rel
        cur = sha(p)
        old = ((prev or {}).get("code_fingerprint") or {}).get(rel, {}).get("sha256")
        same = (cur is not None and cur == old)
        delivered[rel] = {
            "sha256_now": cur, "sha256_delivered": old, "same": same,
            "bytes_now": p.stat().st_size if p.exists() else None,
            "mtime_utc": mtime_utc(p),
        }
        chk(f"① 交付文件未变：{rel}", same,
            f"now {str(cur)[:16]}… vs delivered {str(old)[:16]}…")
    out["delivered_fingerprint"] = delivered

    # ── 第 3 层：三条路径实现文件（本单新基线）───────────────────────────────
    paths = {}
    for label, rel in PATHS.items():
        p = ROOT / rel
        paths[label] = {
            "file": rel, "sha256": sha(p),
            "bytes": p.stat().st_size if p.exists() else None,
            "mtime_utc": mtime_utc(p),
        }
    out["path_fingerprint"] = paths
    print("\n== 三条路径实现文件（本单基线）==")
    for label, v in paths.items():
        print(f"  {label}: {v['file']}\n    sha {str(v['sha256'])[:16]}… "
              f"mtime {v['mtime_utc']}")

    # ── 第 2 层：进程加载的是不是这一份 ──────────────────────────────────────
    api_pid = listen_pid(8787)
    api_start = proc_start_utc(api_pid) if api_pid else None
    out["process"] = {"api_pid": api_pid, "api_started_utc": api_start}
    chk("② 找到 API 进程", api_pid is not None, f"pid={api_pid} 启动={api_start}")

    # 后端文件：进程启动必须晚于文件 mtime（否则跑的是旧代码）
    start_ms = None
    if api_pid:
        try:
            import psutil
            start_ms = int(psutil.Process(api_pid).create_time() * 1000)
        except Exception:                                    # noqa: BLE001
            start_ms = None
    out["process"]["api_started_ms"] = start_ms

    stale = []
    # 只拿**后端**文件比进程启动时刻。前端走 vite HMR，与 Python 进程无关。
    for rel in DELIVERED_BACKEND + list(PATHS.values()):
        p = ROOT / rel
        if not p.exists() or start_ms is None:
            continue
        mt = int(p.stat().st_mtime * 1000)
        if mt > start_ms:
            stale.append({"file": rel, "mtime_utc": mtime_utc(p),
                          "behind_process_by_sec": round((mt - start_ms) / 1000, 1)})
    chk("②b 没有**后端**文件比 API 进程更新（不存在「改了但没重启」）",
        not stale, f"落后文件 {len(stale)} 个" + (f"：{stale}" if stale else ""))
    out["stale_backend_files"] = stale

    # 前端：单独核。判据不是 mtime，而是「页面实际渲染出的是不是新逻辑」。
    # 这里只记录事实（dev server 启动时刻 + 文件 mtime），由 70 号页面探针给行为结论。
    fe = {}
    fe_pid = listen_pid(3000)
    fe_start = proc_start_utc(fe_pid) if fe_pid else None
    for rel in DELIVERED_FRONTEND:
        p = ROOT / rel
        fe[rel] = {"mtime_utc": mtime_utc(p),
                   "mtime_after_devserver": (
                       bool(fe_start) and p.exists()
                       and mtime_utc(p) > fe_start)}
    out["frontend"] = {"dev_server_pid": fe_pid, "dev_server_started_utc": fe_start,
                       "files": fe,
                       "note": ("前端由 vite dev server 热更新，文件 mtime 晚于 dev server "
                                "启动是**正常**的（HMR 就地替换模块，不需要重启）。"
                                "「新逻辑是否生效」由 70 号页面探针按实际渲染结果判定。")}
    print(f"\n== 前端（HMR，不按进程启动时刻判）==")
    print(f"  dev server pid={fe_pid} 启动={fe_start}")
    for rel, v in fe.items():
        print(f"  {rel}: mtime={v['mtime_utc']} 晚于devserver={v['mtime_after_devserver']}")

    # Runtime 进程（真正跑引擎的那个）也要核
    try:
        import psutil
        rt = None
        for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "unified_runtime" in cl and "python" in cl.lower():
                rt = {
                    "pid": p.info["pid"],
                    "started_at_utc": dt.datetime.fromtimestamp(
                        p.info["create_time"], dt.timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                    "started_at_ms": int(p.info["create_time"] * 1000),
                    "cmdline": cl[:200],
                }
                break
        out["process"]["runtime"] = rt
        chk("②c 找到 Runtime 进程", rt is not None,
            f"pid={rt['pid']} 启动={rt['started_at_utc']}" if rt else "未找到")
        if rt:
            stale_rt = []
            for rel in PATHS.values():
                p = ROOT / rel
                if p.exists() and int(p.stat().st_mtime * 1000) > rt["started_at_ms"]:
                    stale_rt.append(rel)
            chk("②d 没有后端文件比 Runtime 进程更新（引擎跑的是当前代码）",
                not stale_rt,
                f"落后文件 {len(stale_rt)} 个" + (f"：{stale_rt}" if stale_rt else ""))
            out["stale_runtime_files"] = stale_rt
    except ImportError:
        chk("②c 找到 Runtime 进程", False, "psutil 不可用")

    # ── 守卫文件 ─────────────────────────────────────────────────────────────
    guard = {}
    for rel in GUARD:
        p = ROOT / rel
        guard[rel] = {"sha256": sha(p), "mtime_utc": mtime_utc(p)}
    out["guard_fingerprint"] = guard
    prev_guard = (prev or {}).get("untouched_fingerprint") or {}
    for rel in GUARD:
        old = (prev_guard.get(rel) or {}).get("sha256")
        if old is None:
            continue
        chk(f"③ 守卫文件未变：{rel}", guard[rel]["sha256"] == old,
            f"now {str(guard[rel]['sha256'])[:16]}… vs 07-R1 {str(old)[:16]}…")

    out["ok"] = all(c["ok"] for c in out["checks"])
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    # ── 不覆盖：每次核对都绑定核对时刻 ──────────────────────────────────────
    # 这个项目反复强调「结论绑定时刻」。上一版把输出路径写死，传 --out 被静默忽略，
    # 结果一次核对把上一次的结果冲掉了 —— 事后无法回答「11:50 那次是什么结论」。
    # 现在：目标已存在就先按它自己的 generated_real_utc 归档，再写新文件。
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            stamp = (old.get("generated_real_utc") or "unknown").replace(":", "")
            arch = p.parent / f"{p.stem}_archived_{stamp}.json"
            if not arch.exists():
                arch.write_text(json.dumps(old, ensure_ascii=False, indent=1),
                                encoding="utf-8")
                say(f"旧结果已归档：{arch}")
        except Exception as exc:                             # noqa: BLE001
            say(f"[WARN] 旧结果无法归档（{exc}），仍然写新文件")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== 结果：{'全部通过' if out['ok'] else '存在失败项'} ==")
    print(f"核对时刻：{out['generated_real_utc']}")
    print(f"写出：{p}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
