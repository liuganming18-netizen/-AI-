# -*- coding: utf-8 -*-
"""00 开工版本核对（T_start）—— 与 AUDIT-02 的 T1 快照比对。

工单：「开始与结束核对文件、配置和运行进程版本；变更时只作废受影响检查并重新执行。」
      「冻结回执若有格式缺项，可用独立快照补齐并记录。」

产物：out/00_开工快照.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-03"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
A02 = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02/out"
PORT = 8787


def audited_files() -> list[pathlib.Path]:
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    for extra in ("UI界面/web/vite.config.ts", "UI界面/web/package.json",
                  "框架/backend/live/paper_new_entry_gate.json"):
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


def backend_proc() -> dict:
    pid = None
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{PORT}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                pid = int(parts[1])
                break
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "原因": str(exc)}
    if pid is None:
        return {"可判定": False, "原因": "端口无监听"}
    try:
        import psutil
        pr = psutil.Process(pid)
        return {"可判定": True, "pid": pid,
                "启动时刻": dt.datetime.fromtimestamp(pr.create_time()).astimezone().isoformat(timespec="seconds"),
                "启动时刻_ms": int(pr.create_time() * 1000),
                "命令行": " ".join(pr.cmdline())[:260]}
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "pid": pid, "原因": str(exc)}


def main() -> None:
    now = dt.datetime.now().astimezone()
    t1 = json.loads((A02 / "00_版本快照_T1.json").read_text(encoding="utf-8"))
    prev = t1["文件"]

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
            changed.append({"文件": k, "变化": "本轮新增"})
        elif b is None:
            changed.append({"文件": k, "变化": "已消失"})
        elif a["sha256"] != b["sha256"]:
            changed.append({"文件": k, "变化": "被改",
                            "AUDIT02_sha": a["sha256"][:12], "当前_sha": b["sha256"][:12],
                            "字节": f'{a["字节"]} → {b["字节"]}',
                            "mtime": b["mtime"]})

    # 冻结回执（再次确认有没有补交）
    deliv = BASE / "evidence/SYSTEM-CLOSELOOP-02"
    receipt = deliv / "06_代码_配置_运行进程版本指纹及变更回执.md"
    rt = receipt.read_text(encoding="utf-8") if receipt.exists() else ""
    import re
    n64 = len(re.findall(r"\b[0-9a-fA-F]{64}\b", rt))
    n16 = len(re.findall(r"\b[0-9a-fA-F]{16}\b", rt))

    proc = backend_proc()
    latest_ms = max(v["mtime_ms"] for v in cur.values())

    res = {
        "生成时刻": now.isoformat(timespec="seconds"),
        "基准": {"来源": "AUDIT-02 out/00_版本快照_T1.json", "采样时刻": t1["采样时刻"],
                 "文件数": t1["文件数"]},
        "本次": {"文件数": len(cur), "最新mtime": max(v["mtime"] for v in cur.values())},
        "变动": {"条数": len(changed), "明细": changed},
        "冻结回执": {"文件": str(receipt.relative_to(BASE)), "存在": receipt.exists(),
                     "64位哈希数": n64, "16位短哈希数": n16,
                     "可核验": bool(n64 or n16),
                     "处理": ("格式缺项：无哈希 ⇒ 按工单允许，用本次独立快照补齐并记录，"
                              "基准 = 本文件记录的全量 SHA-256" if not (n64 or n16)
                              else "有哈希，需逐项核对")},
        "运行进程": proc,
        "进程是否最新": (proc.get("启动时刻_ms", 0) >= latest_ms) if proc.get("可判定") else None,
        "开工结论": ("代码与 AUDIT-02 基准完全一致 ⇒ 可直接沿用该基准" if not changed
                     else f"有 {len(changed)} 个文件变动 ⇒ 这些文件相关的检查必须以本次快照为准"),
        "全量指纹": cur,
    }
    (OUT / "00_开工快照.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[开工] {now.isoformat(timespec='seconds')}  文件 {len(cur)} 个")
    print(f"  与 AUDIT-02 T1（{t1['采样时刻']}）比对：变动 {len(changed)} 个")
    for c in changed:
        print(f"    {c['文件']}  {c['变化']}  {c.get('AUDIT02_sha','')} → {c.get('当前_sha','')}  {c.get('mtime','')}")
    print(f"  冻结回执：64位哈希 {n64} 个 / 16位 {n16} 个 ⇒ 可核验={bool(n64 or n16)}")
    print(f"  后端进程：{proc}")
    print(f"  进程是否最新：{res['进程是否最新']}")
    print(f"  结论：{res['开工结论']}")


if __name__ == "__main__":
    main()
