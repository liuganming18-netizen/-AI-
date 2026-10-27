#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02_drift.py —— LINK-ACCEPT-01 收工复核（T1 vs T0）

工单第 1 条要求核对「源码哈希」，但本轮开审期间被审代码一直在变
（unified_runtime.py 在 19:04:39 → 19:15:53 → 19:16:07 连续被改，
基线自己声明的哈希在开审 2 分钟内即失效）。
所以必须给出 T0→T1 的逐文件变动，结论只对 T1 有效。

输出：out/T1_收工快照.json、out/T1_T0_变动.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "LINK-ACCEPT-01" / "out"
T0 = OUT / "T0_冻结快照.json"
OBJECTS = {"A": "PAPER-LIVE-01", "B": "UI-LINK-01"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mtime(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def pids_on(port: int) -> list[int]:
    try:
        out = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=15).stdout
        return sorted({int(x) for x in out.split() if x.strip().isdigit()})
    except Exception:  # noqa: BLE001
        return []


def proc_info(pid: int) -> dict:
    import psutil
    try:
        p = psutil.Process(pid)
        return {"pid": pid, "命令行": " ".join(p.cmdline())[:200],
                "启动时刻": dt.datetime.fromtimestamp(p.create_time()).strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:  # noqa: BLE001
        return {"pid": pid, "error": str(e)[:120]}


def delivery() -> dict:
    st = {}
    for k, name in OBJECTS.items():
        d = ROOT / "evidence" / name
        files = sorted(p for p in d.rglob("*") if p.is_file()) if d.is_dir() else []
        st[k] = {"存在": d.is_dir(), "文件数": len(files),
                 "文件": [str(p.relative_to(ROOT)) for p in files],
                 "最新mtime": max((mtime(p) for p in files), default=None)}
    return st


def main() -> None:
    t0 = json.loads(T0.read_text(encoding="utf-8"))
    fp0 = t0["全量指纹"]

    fp1, changed, missing = {}, [], []
    for rel, old in fp0.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        h = sha(p)
        fp1[rel] = {"sha256": h, "字节": p.stat().st_size, "mtime": mtime(p)}
        if h != old["sha256"]:
            changed.append({"文件": rel, "T0_sha": old["sha256"][:16] + "…",
                            "T1_sha": h[:16] + "…", "T0_mtime": old["mtime"],
                            "T1_mtime": mtime(p), "T0字节": old["字节"], "T1字节": p.stat().st_size})

    snap = {"生成时刻": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "被审文件数": len(fp1), "缺失": missing,
            "运行进程": [proc_info(pid) for pid in pids_on(8787)],
            "交付状态": delivery(), "全量指纹": fp1}
    (OUT / "T1_收工快照.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # 版本绑定复核：进程启动时刻 vs 代码 mtime
    bind = []
    for pr in snap["运行进程"]:
        if "启动时刻" not in pr:
            continue
        st = dt.datetime.strptime(pr["启动时刻"], "%Y-%m-%d %H:%M:%S")
        newer = [r for r, v in fp1.items()
                 if r.startswith("框架/backend") and r.endswith(".py")
                 and dt.datetime.strptime(v["mtime"], "%Y-%m-%d %H:%M:%S") > st]
        bind.append({"pid": pr["pid"], "启动时刻": pr["启动时刻"],
                     "启动后又被改的py": len(newer), "清单": newer})
    import psutil
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:  # noqa: BLE001
            continue
        if "unified_runtime.py" in cl:
            st = dt.datetime.fromtimestamp(p.create_time())
            newer = [r for r, v in fp1.items()
                     if r.startswith("框架/backend") and r.endswith(".py")
                     and dt.datetime.strptime(v["mtime"], "%Y-%m-%d %H:%M:%S") > st]
            bind.append({"pid": p.info["pid"], "启动时刻": st.strftime("%Y-%m-%d %H:%M:%S"),
                         "角色": "unified_runtime worker", "启动后又被改的py": len(newer), "清单": newer})

    drift = {"T0": t0["生成时刻"], "T1": snap["生成时刻"],
             "变动文件数": len(changed), "变动明细": changed,
             "缺失": missing, "版本绑定": bind,
             "交付状态变化": {"T0": t0["交付状态"], "T1": snap["交付状态"]}}
    (OUT / "T1_T0_变动.json").write_text(json.dumps(drift, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"T0={drift['T0']}  T1={drift['T1']}")
    print(f"被审 {len(fp0)} 文件，T0→T1 变动 {len(changed)} 个，缺失 {len(missing)}")
    for c in changed:
        print(f"  {c['文件']}  {c['T0_mtime']} → {c['T1_mtime']}  {c['T0字节']}→{c['T1字节']} 字节")
    for b in bind:
        print(f"  pid {b['pid']} 启动 {b['启动时刻']} 之后被改的 py: {b['启动后又被改的py']} 个 {b['清单'][:3]}")
    for k, v in drift["交付状态变化"]["T1"].items():
        print(f"  {k} 文件数 {v['文件数']} 最新 {v['最新mtime']}")


if __name__ == "__main__":
    main()
