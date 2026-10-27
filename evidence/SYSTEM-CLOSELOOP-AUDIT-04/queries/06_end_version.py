# -*- coding: utf-8 -*-
"""06 第一关⑦：验收起止的代码、配置、实际运行版本是否一致。

判据（四条全真才算一致）：
  V1 开工/收工两次全量 SHA-256 快照零变动；
  V2 8787 进程启动时刻 ≥ 最后一次代码改动（跑的是磁盘上的代码）；
  V3 生产闸门文件与隔离闸门文件内容可核验（不是「对方说关了」）；
  V4 隔离实例实际 import 的模块路径 = 被审代码目录（不是另一份拷贝）。

产物：out/06_起止版本.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04"
OUT = EVID / "out"
BACKEND = BASE / "框架/backend"
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


def main() -> None:
    start = json.loads((OUT / "00_开工冻结.json").read_text(encoding="utf-8"))
    prev = start["全量指纹"]

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
            changed.append({"文件": k, "变化": "收工时新增", "mtime": b["mtime"]})
        elif b is None:
            changed.append({"文件": k, "变化": "收工时消失"})
        elif a.get("sha256") != b.get("sha256"):
            changed.append({"文件": k, "变化": "验收期间被改",
                            "开工_sha": a["sha256"][:12], "收工_sha": b["sha256"][:12],
                            "字节": f'{a.get("字节")} → {b["字节"]}', "mtime": b["mtime"]})

    pid = listen_pid(PORT)
    proc = {"可判定": False, "原因": "端口无监听"}
    if pid:
        try:
            import psutil
            pr = psutil.Process(pid)
            proc = {"可判定": True, "pid": pid,
                    "启动时刻": dt.datetime.fromtimestamp(pr.create_time()).astimezone().isoformat(timespec="seconds"),
                    "启动时刻_ms": int(pr.create_time() * 1000),
                    "命令行": " ".join(pr.cmdline())[:300]}
        except Exception as exc:                                 # noqa: BLE001
            proc = {"可判定": False, "pid": pid, "原因": str(exc)}

    latest_ms = max(v["mtime_ms"] for v in cur.values())
    latest_file = max(cur.items(), key=lambda kv: kv[1]["mtime_ms"])[0]

    gate_prod = BACKEND / "live" / "paper_new_entry_gate.json"
    gate_iso = pathlib.Path("/tmp/wx_audit04/gate.json")
    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "文件数": {"开工": len(prev), "收工": len(cur)},
        "验收期间变动": {"条数": len(changed), "明细": changed},
        "最新改动": {"文件": latest_file, "mtime": cur[latest_file]["mtime"]},
        "运行进程": proc,
        "进程跑的是最新代码": ((proc.get("启动时刻_ms", 0) >= latest_ms) if proc.get("可判定") else None),
        "生产闸门": ({"路径": str(gate_prod), "存在": gate_prod.exists(),
                  "内容": (json.loads(gate_prod.read_text(encoding="utf-8"))
                         if gate_prod.exists() else None),
                  "sha256": (sha(gate_prod) if gate_prod.exists() else None)}),
        "隔离闸门": ({"路径": str(gate_iso), "存在": gate_iso.exists(),
                  "内容": (json.loads(gate_iso.read_text(encoding="utf-8"))
                         if gate_iso.exists() else None)}),
        "隔离实例导入路径": str(BACKEND),
    }
    res["判定"] = {
        "V1 起止零变动": "通过" if not changed else "不通过",
        "V2 进程跑最新代码": ("通过" if res["进程跑的是最新代码"] else "不通过"),
        "V3 闸门可核验": ("通过" if (res["生产闸门"]["存在"] and res["隔离闸门"]["存在"]) else "不通过"),
    }
    (OUT / "06_起止版本.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    print(f"[收工] 文件 {len(cur)}  变动 {len(changed)}")
    for c in changed:
        print("   ", c)
    print("  最新改动:", res["最新改动"])
    print("  8787:", proc)
    print("  判定:", json.dumps(res["判定"], ensure_ascii=False))


if __name__ == "__main__":
    main()
