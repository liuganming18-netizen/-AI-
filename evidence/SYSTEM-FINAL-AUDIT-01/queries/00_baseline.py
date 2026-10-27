# -*- coding: utf-8 -*-
"""00 基线冻结（SYSTEM-FINAL-AUDIT-01）：当前被审代码/配置 SHA-256 + 与上一轮 AUDIT-05 基准比对
+ 反重力「SYSTEM-CLOSELOOP-05 可核验冻结回执」8 个哈希独立核验 + 生产 4 臂账本零污染复核
+ 8787 运行进程身份 + 统一入口接线扫描。

只读脚本：不改任何业务文件、不写任何账本、不碰闸门。
产物：out/00_基线冻结.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

# 上一轮审核方自采基准（唯一可信的「上一版」）
A05 = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05/out/00_开工冻结.json"
# 反重力本轮交付的冻结回执（其哈希声明需被独立核验）
CLAIM_DOC = BASE / "evidence/SYSTEM-CLOSELOOP-05/06_可核验冻结回执.md"
PORT = 8787


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def audited_files() -> list[pathlib.Path]:
    fs: list[pathlib.Path] = []
    fs += [p for p in (BASE / "框架/backend").rglob("*.py")
           if "__pycache__" not in p.parts and "_legacy_archive" not in p.parts]
    fs += [p for p in (BASE / "UI界面/web/src").rglob("*")
           if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".json")]
    # 只收配置类 JSON：live/ 下的运行期诊断文件（pool_diagnostics.json 等）不计入，避免假漂移
    _gate = BASE / "框架/backend/live/paper_new_entry_gate.json"
    if _gate.exists():
        fs.append(_gate)
    fs += [p for p in (BASE / "数据/研究输入").rglob("*研究参数生效视图*.json") if p.is_file()]
    for extra in ("UI界面/web/vite.config.ts", "UI界面/web/package.json",
                  "框架/backend/start.sh", "框架/backend/collector.sh", "启动系统.command"):
        p = BASE / extra
        if p.exists():
            fs.append(p)
    return sorted(set(fs))


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


def claim_hashes() -> dict[str, str]:
    """从反重力的冻结回执里机械抽取「文件路径 → 声明哈希」。

    不手抄：正则抓 markdown 表格行，抓不到就返回空（宁可报「未声明」也不替它补）。
    """
    import re
    if not CLAIM_DOC.exists():
        return {}
    s = CLAIM_DOC.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for line in s.splitlines():
        m = re.search(r"`([^`]+\.(?:py|json|md|ts|tsx))`\s*\|\s*`([0-9a-f]{64})`", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def verify_claim(claim: dict[str, str]) -> dict:
    rows = []
    for rel, want in claim.items():
        p = BASE / rel
        if not p.exists():
            rows.append({"文件": rel, "声明": want[:12], "实际": None, "判定": "文件不存在"})
            continue
        got = sha(p)
        rows.append({"文件": rel, "声明": want[:12], "实际": got[:12],
                     "判定": "一致" if got == want else "不一致"})
    ok = sum(1 for r in rows if r["判定"] == "一致")
    return {"声明条数": len(rows), "一致": ok,
            "判定": "回执可核验" if rows and ok == len(rows) else
                    ("无可核验哈希" if not rows else "回执与实际不符"),
            "明细": rows}


def prod_ledgers() -> dict:
    rows = []
    for arm in ("3a", "3b", "3c", "3d"):
        p = BASE / f"数据/paper/paper_{arm}.sqlite3"
        if not p.exists():
            rows.append({"文件": p.name, "存在": False})
            continue
        st = p.stat()
        rows.append({"文件": p.name, "存在": True, "sha256": sha(p), "字节": st.st_size,
                     "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")})
    return {"四臂": rows}


def entry_wiring() -> dict:
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

    prev, prev_meta = {}, {}
    if A05.exists():
        b = json.loads(A05.read_text(encoding="utf-8"))
        prev = b.get("全量指纹") or {}
        prev_meta = {"来源": "AUDIT-05 out/00_开工冻结.json（审核方自采）",
                     "采样时刻": b.get("生成时刻"), "文件数": len(prev)}

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
                            "AUDIT05_sha": (a.get("sha256") or "")[:12],
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
        "审计集构成": "后端全部 py + 前端 src 全部 + 闸门 paper_new_entry_gate.json + 研究参数生效视图 + 启动脚本",
        "与AUDIT05基准比对": {"变动条数": len(changed), "明细": changed,
                        "结论": "字节级完全一致" if not changed else "存在差异"},
        "反重力冻结回执核验": verify_claim(claim_hashes()),
        "生产四臂账本": prod_ledgers(),
        "运行进程": proc,
        "进程是否最新": (proc.get("启动时刻_ms", 0) >= latest_ms) if proc.get("可判定") else None,
        "入口接线": entry_wiring(),
        "全量指纹": cur,
    }
    (OUT / "00_基线冻结.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[基线] {res['生成时刻']}  文件 {len(cur)} 个  最新 {latest_file} @ {res['本次']['最新mtime']}")
    print(f"  与 AUDIT-05 基准（{prev_meta.get('采样时刻')}，{prev_meta.get('文件数')} 文件）比对："
          f"变动 {len(changed)} 条 → {res['与AUDIT05基准比对']['结论']}")
    for c in changed:
        print(f"    {c['文件']:58s} {c['变化']:4s} {c.get('AUDIT05_sha','')}→{c.get('当前_sha','')} {c.get('字节','')}")
    vc = res["反重力冻结回执核验"]
    print(f"  反重力冻结回执：声明 {vc['声明条数']} 条，一致 {vc['一致']} 条 → {vc['判定']}")
    for r in vc["明细"]:
        print(f"    {r['判定']:6s} {r['文件']:58s} 声明{r['声明']} 实际{r['实际']}")
    print("  生产四臂账本：")
    for r in res["生产四臂账本"]["四臂"]:
        print(f"    {r['文件']:24s} {(r.get('sha256') or '(缺失)')[:16]} {r.get('mtime','')}")
    print(f"  8787 进程：{proc}")
    print(f"  进程是否最新：{res['进程是否最新']}")
    print("  入口接线：")
    for k, v in res["入口接线"]["启动脚本"].items():
        print(f"    {k}: {v}")
    print(f"    后端引用点：{res['入口接线']['引用点']}")
    print(f"    运行中的执行守护：{res['入口接线']['当前运行中的执行守护']}")


if __name__ == "__main__":
    main()
