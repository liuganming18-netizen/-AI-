# -*- coding: utf-8 -*-
"""00 开工冻结：全量 SHA-256 快照 + 与 AUDIT-03 基准比对 + 运行进程/入口核验。

工单（SYSTEM-CLOSELOOP-AUDIT-04）第 7 项：「验收起止代码、配置及实际运行版本一致。」
第 1 项前提：「从运行入口启动后，系统自动完成挂单、成交、止损上移和退出。」

本脚本只读，不改任何业务文件。
产物：out/00_开工冻结.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
A03 = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-03/out"
PORT = 8787


def audited_files() -> list[pathlib.Path]:
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    for extra in ("UI界面/web/vite.config.ts", "UI界面/web/package.json",
                  "框架/backend/live/paper_new_entry_gate.json",
                  "框架/backend/start.sh", "框架/backend/collector.sh",
                  "启动系统.command"):
        p = BASE / extra
        if p.exists():
            fs.append(p)
    return sorted(set(fs))


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def listen_pid(port: int) -> int | None:
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
    except Exception:                                            # noqa: BLE001
        return None
    return None


def proc_info(pid: int) -> dict:
    try:
        import psutil
        pr = psutil.Process(pid)
        return {"可判定": True, "pid": pid,
                "启动时刻": dt.datetime.fromtimestamp(pr.create_time()).astimezone().isoformat(timespec="seconds"),
                "启动时刻_ms": int(pr.create_time() * 1000),
                "命令行": " ".join(pr.cmdline())[:300]}
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "pid": pid, "原因": str(exc)}


def entry_wiring() -> dict:
    """统一启动入口是否拉起「纸面执行守护」。"""
    targets = {
        "启动系统.command": BASE / "启动系统.command",
        "框架/backend/start.sh": BASE / "框架/backend/start.sh",
        "框架/backend/collector.sh": BASE / "框架/backend/collector.sh",
    }
    kws = ["paper_execution_worker", "paper_loop", "PaperExecutionDaemon", "paper_exec"]
    res = {}
    for name, p in targets.items():
        if not p.exists():
            res[name] = {"存在": False}
            continue
        s = p.read_text(encoding="utf-8", errors="ignore")
        res[name] = {"存在": True, "命中关键词": [k for k in kws if k in s]}
    # 全后端（排除自身与缓存）谁引用了 worker / 会启动它
    refs = []
    for p in (BASE / "框架/backend").rglob("*.py"):
        if "__pycache__" in p.parts or "_legacy_archive" in p.parts:
            continue
        try:
            s = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                        # noqa: BLE001
            continue
        for kw in ("paper_execution_worker", "PaperExecutionDaemon", "start_background("):
            if kw in s:
                refs.append({"文件": p.relative_to(BASE).as_posix(), "关键词": kw})
    # 当前有没有 worker 进程在跑
    running = []
    try:
        import psutil
        for pr in psutil.process_iter(["pid", "cmdline"]):
            cl = " ".join(pr.info.get("cmdline") or [])
            if "paper_execution_worker" in cl or "paper_loop" in cl:
                running.append({"pid": pr.info["pid"], "命令行": cl[:200]})
    except Exception as exc:                                     # noqa: BLE001
        running = [{"错误": str(exc)}]
    return {"启动脚本": res, "引用点": refs, "当前运行中的执行守护": running}


def main() -> None:
    now = dt.datetime.now().astimezone()
    base_file = A03 / "00b_全量指纹_新基准.json"
    prev = {}
    prev_meta = {}
    if base_file.exists():
        b = json.loads(base_file.read_text(encoding="utf-8"))
        # AUDIT-03 结构：{"采样时刻":..., "文件": {path: {sha256,...}}} 或直接 {path:{...}}
        if isinstance(b.get("文件"), dict):
            prev = b["文件"]
            prev_meta = {"来源": "AUDIT-03 out/00b_全量指纹_新基准.json",
                         "采样时刻": b.get("采样时刻") or b.get("生成时刻")}
        else:
            prev = {k: v for k, v in b.items() if isinstance(v, dict) and "sha256" in v}
            prev_meta = {"来源": "AUDIT-03 out/00b_全量指纹_新基准.json", "采样时刻": None}

    cur = {}
    for p in audited_files():
        st = p.stat()
        cur[p.relative_to(BASE).as_posix()] = {
            "sha256": sha(p), "字节": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "mtime_ms": int(st.st_mtime * 1000)}

    changed = []
    for k in sorted(set(prev) | set(cur)):
        a, b = prev.get(k), cur.get(k)
        if a is None:
            changed.append({"文件": k, "变化": "新增"})
        elif b is None:
            changed.append({"文件": k, "变化": "消失"})
        elif a.get("sha256") != b.get("sha256"):
            changed.append({"文件": k, "变化": "被改",
                            "AUDIT03_sha": (a.get("sha256") or "")[:12],
                            "当前_sha": b["sha256"][:12],
                            "字节": f'{a.get("字节")} → {b["字节"]}',
                            "mtime": b["mtime"]})

    pid = listen_pid(PORT)
    proc = proc_info(pid) if pid else {"可判定": False, "原因": "端口无监听"}
    latest_ms = max(v["mtime_ms"] for v in cur.values())
    latest_file = max(cur.items(), key=lambda kv: kv[1]["mtime_ms"])[0]

    res = {
        "生成时刻": now.isoformat(timespec="seconds"),
        "基准": prev_meta,
        "本次": {"文件数": len(cur), "最新mtime": max(v["mtime"] for v in cur.values()),
                 "最新mtime文件": latest_file},
        "变动": {"条数": len(changed), "明细": changed},
        "运行进程": proc,
        "进程是否最新": (proc.get("启动时刻_ms", 0) >= latest_ms) if proc.get("可判定") else None,
        "入口接线": entry_wiring(),
        "全量指纹": cur,
    }
    (OUT / "00_开工冻结.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[冻结] {res['生成时刻']}  文件 {len(cur)} 个  最新 {latest_file} @ {res['本次']['最新mtime']}")
    print(f"  与 AUDIT-03 基准（{prev_meta.get('采样时刻')}）比对：变动 {len(changed)}")
    for c in changed:
        print(f"    {c['文件']:60s} {c['变化']:4s} {c.get('AUDIT03_sha','')}→{c.get('当前_sha','')} {c.get('字节','')}")
    print(f"  8787 进程：{proc}")
    print(f"  进程是否最新：{res['进程是否最新']}")
    print("  入口接线：")
    for k, v in res["入口接线"]["启动脚本"].items():
        print(f"    {k}: {v}")
    print(f"    后端引用点：{res['入口接线']['引用点']}")
    print(f"    运行中的执行守护：{res['入口接线']['当前运行中的执行守护']}")


if __name__ == "__main__":
    main()
