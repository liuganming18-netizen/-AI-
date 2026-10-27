# -*- coding: utf-8 -*-
"""12 结束版本核对：把 148 个被审文件重新采一遍，与 21:58 的新基准逐字节比。

工单要求「开始与结束核对文件、配置和运行进程版本；变更时只作废受影响检查并重新执行」。
开工时比过一轮（00/00b：6 个文件被改、进程落后代码）；这里是**收尾再比一次**，
用来证明"验收期间没有边验边改"，以及"现在跑的进程是不是最新的"。

产物：out/12_结束版本核对.json / .md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03" / "out"


def sha(p: pathlib.Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pid_on(port):
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


def proc_start_ms(pid):
    # `ps -o lstart=` 在本机沙箱里被拒 ⇒ 用 psutil（已装）读真实启动时刻
    try:
        import psutil
        return int(psutil.Process(pid).create_time() * 1000)
    except Exception:                                            # noqa: BLE001
        pass
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=20)
        s = (r.stdout or "").strip()
        if not s:
            return None
        return int(dt.datetime.strptime(s, "%a %b %d %H:%M:%S %Y")
                   .astimezone().timestamp() * 1000)
    except Exception:                                            # noqa: BLE001
        return None


def main():
    base = json.loads((OUT / "00b_全量指纹_新基准.json").read_text(encoding="utf-8"))
    files = base["文件"]
    now = dt.datetime.now().astimezone()
    changed, missing = {}, []
    cur = {}
    for rel, old in files.items():
        p = PROJ / rel
        s = sha(p)
        if s is None:
            missing.append(rel)
            continue
        cur[rel] = {"sha256": s, "字节": p.stat().st_size,
                    "mtime_ms": int(p.stat().st_mtime * 1000)}
        if s != old["sha256"]:
            changed[rel] = {"旧": old["sha256"][:12], "新": s[:12],
                            "旧字节": old["字节"], "新字节": p.stat().st_size,
                            "旧mtime": old["mtime"],
                            "新mtime": dt.datetime.fromtimestamp(
                                p.stat().st_mtime).astimezone().isoformat(timespec="seconds")}

    pid = pid_on(8787)
    start = proc_start_ms(pid) if pid else None
    latest_mtime = max((v["mtime_ms"] for v in cur.values()), default=0)
    latest_file = max(cur.items(), key=lambda kv: kv[1]["mtime_ms"])[0] if cur else None

    res = {
        "采样时刻": now.isoformat(timespec="seconds"),
        "基准采样时刻": base.get("采样时刻"),
        "被审文件数": len(files),
        "重采成功": len(cur),
        "缺失": missing,
        "变动文件数": len(changed),
        "变动明细": changed,
        "8787_pid": pid,
        "8787启动时刻": (dt.datetime.fromtimestamp(start / 1000).astimezone()
                    .isoformat(timespec="seconds") if start else None),
        "最新文件mtime": (dt.datetime.fromtimestamp(latest_mtime / 1000).astimezone()
                     .isoformat(timespec="seconds")),
        "最新文件": latest_file,
        "进程是否加载最新代码": (bool(start) and start >= latest_mtime),
    }
    (OUT / "12_结束版本核对.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 12 结束版本核对", "",
          f"- 采样时刻：{res['采样时刻']}（基准 {res['基准采样时刻']}）",
          f"- 被审文件：{res['被审文件数']} 个，重采成功 {res['重采成功']} 个",
          f"- **变动文件数：{res['变动文件数']}**",
          f"- 8787 进程：PID {pid}，启动于 {res['8787启动时刻']}",
          f"- 最新被审文件 mtime：{res['最新文件mtime']}（`{latest_file}`）",
          f"- **运行进程是否加载最新代码：{res['进程是否加载最新代码']}**", ""]
    if changed:
        md += ["| 文件 | 旧 SHA12 | 新 SHA12 | 旧字节 | 新字节 | 旧 mtime | 新 mtime |",
               "|---|---|---|---|---|---|---|"]
        for k, v in changed.items():
            md.append(f"| `{k}` | {v['旧']} | {v['新']} | {v['旧字节']} | {v['新字节']} | "
                      f"{v['旧mtime']} | {v['新mtime']} |")
    else:
        md.append("验收期间（21:58 新基准 → 收尾）被审文件**零变动**。")
    (OUT / "12_结束版本核对.md").write_text("\n".join(md), encoding="utf-8")

    print(f"被审 {len(files)} 个，变动 {len(changed)} 个，缺失 {len(missing)} 个")
    for k, v in changed.items():
        print(f"  {k}  {v['旧']} → {v['新']}  mtime {v['旧mtime']} → {v['新mtime']}")
    print(f"8787 pid={pid} 启动={res['8787启动时刻']}  最新文件 mtime={res['最新文件mtime']}")
    print(f"进程加载最新代码：{res['进程是否加载最新代码']}")
    print(f"已写出 {OUT/'12_结束版本核对.json'} / .md")


if __name__ == "__main__":
    main()
