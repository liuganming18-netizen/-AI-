# -*- coding: utf-8 -*-
"""06 第 7 项：验收前后版本一致；测试操作不影响日常实例。

判据（三条都必须为真）：
  V1 起止审计集（代码 + 配置）SHA-256 **零变动**；
  V2 8787 日常实例加载的是**最新**代码（进程启动时刻 >= 审计集最新 mtime）；
  V3 生产账本/闸门在审核窗口内**零写入**（我的探针只写 /tmp/wx_audit05）。

只读，不改任何业务文件。
产物：out/06_起止版本.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = HERE / "out"
START = OUT / "00_开工冻结.json"
PORT = 8787


def audited_files() -> list[pathlib.Path]:
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    # 只收「配置类」JSON；live/ 下的运行期诊断文件（pool_diagnostics.json 等）不计入，避免假漂移
    _gate = BASE / "框架/backend/live/paper_new_entry_gate.json"
    if _gate.exists():
        fs.append(_gate)
    fs += [p for p in (BASE / "数据/研究输入").rglob("*研究参数生效视图*.json") if p.is_file()]
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
                "命令行": " ".join(pr.cmdline())[:200]}
    except Exception as exc:                                     # noqa: BLE001
        return {"可判定": False, "pid": pid, "原因": str(exc)}


def main() -> None:
    now = dt.datetime.now().astimezone()
    start = json.loads(START.read_text(encoding="utf-8"))
    prev = start.get("全量指纹") or {}
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
                            "开工_sha": (a.get("sha256") or "")[:12],
                            "收工_sha": b["sha256"][:12],
                            "字节": f'{a.get("字节")} → {b["字节"]}',
                            "mtime": b["mtime"]})

    pid = listen_pid(PORT)
    proc = proc_info(pid) if pid else {"可判定": False, "原因": "端口无监听"}
    latest_ms = max(v["mtime_ms"] for v in cur.values())
    latest_file = max(cur.items(), key=lambda kv: kv[1]["mtime_ms"])[0]

    # 生产账本/闸门在审核窗口内的写入情况
    win_start = dt.datetime.fromisoformat(start["生成时刻"]).timestamp()
    prod = []
    for pat in ("数据/paper/*.sqlite3", "数据/paper/*.sqlite3-wal", "数据/paper/*.sqlite3-shm",
                "框架/backend/live/paper_new_entry_gate.json", "框架/backend/live/*.sqlite3*"):
        for p in BASE.glob(pat):
            mt = p.stat().st_mtime
            prod.append({"文件": p.relative_to(BASE).as_posix(),
                         "mtime": dt.datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds"),
                         "审核窗口内被写": mt >= win_start})
    prod_touched = [x for x in prod if x["审核窗口内被写"]]
    # -shm 是 SQLite 共享内存索引，任何进程「打开」库都会刷新它（不等于写入数据）。
    # 归因方法：在所有探针都停掉之后，静默观察 20 秒，看它是否自己继续变。
    import time as _t
    shm = BASE / "框架/backend/live/live_signals.sqlite3-shm"
    main_db = BASE / "框架/backend/live/live_signals.sqlite3"
    wal = BASE / "框架/backend/live/live_signals.sqlite3-wal"
    obs = {}
    if shm.exists():
        t1 = shm.stat().st_mtime
        m1 = main_db.stat().st_mtime if main_db.exists() else None
        w1 = wal.stat().st_mtime if wal.exists() else None
        _t.sleep(20)
        t2 = shm.stat().st_mtime
        m2 = main_db.stat().st_mtime if main_db.exists() else None
        w2 = wal.stat().st_mtime if wal.exists() else None
        obs = {"静默20秒内_shm是否变化": t2 != t1,
               "静默20秒内_主库是否变化": m2 != m1,
               "静默20秒内_WAL是否变化": w2 != w1,
               "结论": ("-shm 在探针全部停止后仍自行变化 ⇒ 由日常实例（8787）打开库造成，"
                    "不是本轮探针写入" if t2 != t1 else "-shm 静默期内未变化，无法归因")}
    data_writes = [x for x in prod_touched if not x["文件"].endswith("-shm")]

    # 我的隔离探针是否碰过生产 locks 目录
    locks = BASE / "框架/backend/locks"
    lock_files = []
    if locks.exists():
        for p in sorted(locks.glob("*")):
            mt = p.stat().st_mtime
            lock_files.append({"文件": p.name,
                               "mtime": dt.datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds"),
                               "审核窗口内被写": mt >= win_start})

    res = {
        "生成时刻": now.isoformat(timespec="seconds"),
        "审计集": {"文件数": len(cur), "开工文件数": len(prev),
                 "最新mtime": max(v["mtime"] for v in cur.values()), "最新mtime文件": latest_file},
        "V1_起止零变动": {"变动条数": len(changed), "明细": changed,
                    "判定": "通过" if not changed else "不通过"},
        "V2_日常实例最新": {"进程": proc,
                     "进程启动ms": proc.get("启动时刻_ms"),
                     "审计集最新mtime_ms": latest_ms,
                     "判定": ("通过" if (proc.get("可判定") and proc.get("启动时刻_ms", 0) >= latest_ms)
                            else "不通过")},
        "V3_生产未被写": {"明细": prod, "窗口内被写": prod_touched,
                    "主库/WAL/闸门 窗口内被写": data_writes,
                    "静默归因观察": obs,
                    "判定": "通过" if not data_writes else "不通过",
                    "说明": "只把「主库/WAL/闸门」计为数据写入；-shm 由任何打开库的进程刷新，需单独归因"},
        "生产locks目录": {"文件": lock_files,
                     "说明": "PAPER_LOCK_DIR 未设时 LOCK_DIR 指向代码目录；本轮探针已显式设置 PAPER_LOCK_DIR"},
        "全量指纹": cur,
    }
    (OUT / "06_起止版本.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[收工冻结] {res['生成时刻']}  文件 {len(cur)}（开工 {len(prev)}）")
    print(f"  V1 起止零变动: 变动 {len(changed)} → {res['V1_起止零变动']['判定']}")
    for c in changed:
        print(f"    {c['文件']:60s} {c['变化']:4s} {c.get('开工_sha','')}→{c.get('收工_sha','')} {c.get('字节','')}")
    print(f"  V2 日常实例最新: {res['V2_日常实例最新']['判定']}  进程={proc.get('启动时刻')}  最新文件={latest_file} @ {res['审计集']['最新mtime']}")
    print(f"  V3 生产未被写: {res['V3_生产未被写']['判定']}  数据写入 {len(data_writes)} 个 / -shm 触碰 {len(prod_touched)-len(data_writes)} 个")
    print(f"  静默归因: {obs}")
    for x in prod_touched:
        print(f"    !! {x['文件']} @ {x['mtime']}")
    print(f"  生产 locks: {[x['文件'] for x in lock_files]}")


if __name__ == "__main__":
    main()
