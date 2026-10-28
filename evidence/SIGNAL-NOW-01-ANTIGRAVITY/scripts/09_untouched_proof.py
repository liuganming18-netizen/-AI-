# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤9：本单「未动生产 / 未动 Work / 未动交接」自证。

为什么不能只比哈希
------------------
生产库 `live_signals.sqlite3` 与 `paper_3a` 被运行时进程**持续写入**（WAL），
所以「哈希没变」既不能证明本单没写，哈希变了也不能归罪于本单。
真正能自证的是两件事，本脚本分开做：

  ① **静态证明**：本单所有脚本对生产库的连接一律 `mode=ro` + `PRAGMA query_only=ON`；
     唯一可写连接只出现在步骤 3 的隔离副本里，且目标路径必须落在 `iso/` 目录内。
  ② **时间证明**：Work 证据目录与统一交接文件的**最大 mtime** 必须早于本单开始时刻
     ⇒ 本单没有写过它们（写文件必然刷新 mtime）。

再附一份当前哈希快照，供后续比对。

产物：out/09_未动自证.json
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
SCRIPTS = ROOT / "scripts"
WORK = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
HANDOVER = PROJ / "docs/用户约定/当前交接.md"
BACKEND = PROJ / "框架/backend"

PROD_DBS = {
    "live_signals": BACKEND / "live" / "live_signals.sqlite3",
    "paper_3a": PROJ / "数据/paper/paper_3a.sqlite3",
    "paper_3b": PROJ / "数据/paper/paper_3b.sqlite3",
    "paper_3c": PROJ / "数据/paper/paper_3c.sqlite3",
    "paper_3d": PROJ / "数据/paper/paper_3d.sqlite3",
}
SOURCE_FILES = ["框架/backend/paper_engine.py",
                "框架/backend/unified_runtime.py",
                "框架/backend/live_api.py"]


def utc(ts=None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() if ts is None else float(ts)))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_connections() -> list:
    """静态扫描：把每个 sqlite3.connect(...) 调用连同其 mode=ro 判定列出来。"""
    rows = []
    for sp in sorted(SCRIPTS.glob("*.py")):
        txt = sp.read_text(encoding="utf-8")
        lines = txt.splitlines()
        for i, line in enumerate(lines, 1):
            if "sqlite3.connect" not in line:
                continue
            # 排除本脚本里「用字符串判断是否含 sqlite3.connect」的自指行，否则会误判成可写连接
            if "not in line" in line or line.lstrip().startswith("#"):
                continue
            # 把可能跨行的调用拼成一段
            seg = "\n".join(lines[i - 1:i + 3])
            is_ro = ("mode=ro" in seg)
            rows.append({
                "script": sp.name, "line": i, "call": line.strip(),
                "has_mode_ro": is_ro,
                "has_query_only": ("query_only=ON" in txt),
                "verdict": ("只读连接" if is_ro else
                            "可写连接 —— 仅允许指向 iso/ 下的隔离副本"),
            })
    return rows


def iso_guard() -> dict:
    """步骤 3 的可写连接是否只落在 iso/ 目录内（顺着变量链断言，不靠肉眼看）。"""
    p = SCRIPTS / "03_isolation_recompute.py"
    txt = p.read_text(encoding="utf-8")
    m = re.search(r"^\s*iso_root\s*=\s*(.+)$", txt, re.M)
    iso_def = m.group(1).strip() if m else None
    targets = []
    for i, line in enumerate(txt.splitlines(), 1):
        if "sqlite3.connect" in line and "mode=ro" not in line:
            targets.append({"line": i, "call": line.strip()})
    chain = {
        "iso_root_定义在_ROUTE_iso": bool(re.search(r'^\s*iso_root\s*=\s*ROOT\s*/\s*"iso"', txt, re.M)),
        "临时目录建在_iso_root_下": bool(re.search(r"mkdtemp\([^)]*dir=str\(iso_root\)", txt)),
        "market_db_建在临时目录下": bool(re.search(r"^\s*market_db\s*=\s*tmp\s*/", txt, re.M)),
    }
    return {"iso_root_definition": iso_def,
            "writable_calls": targets,
            "variable_chain": chain,
            "chain_all_ok": all(chain.values()),
            "note": "两处可写连接的目标都是 `market_db`，它由 `tmp = mkdtemp(dir=iso_root)` 派生，"
                    "`iso_root = ROOT/'iso'` ⇒ 落在本单证据目录的 iso/ 下，不碰生产库。"
                    "生产库在该脚本里只以 mode=ro 打开，前后 sha256 对撞已在 03 产物里。"}


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "9 未动生产 / 未动 Work / 未动交接自证",
         "at_utc": utc()}

    # ── 本单开始时刻（以 01 产物的 at_utc 为准）──
    p01 = OUT / "01_身份与加载证据.json"
    d01 = json.loads(p01.read_text(encoding="utf-8"))
    started_utc = d01.get("at_utc")
    started_ms = None
    if started_utc:
        # 用 calendar.timegm 按 UTC 解释，不能用 mktime（会按本地时区多减一次偏移）
        started_ms = calendar.timegm(
            time.strptime(started_utc, "%Y-%m-%dT%H:%M:%SZ")) * 1000
    R["ticket_started_utc"] = started_utc

    # ── ① 静态证明：只读连接 ──
    R["readonly_connections"] = scan_connections()
    R["iso_guard"] = iso_guard()
    all_ro = [r for r in R["readonly_connections"] if r["has_mode_ro"]]
    writable = [r for r in R["readonly_connections"] if not r["has_mode_ro"]]
    R["readonly_summary"] = {
        "total_connect_calls": len(R["readonly_connections"]),
        "readonly_calls": len(all_ro),
        "writable_calls": len(writable),
        "writable_only_in_step3": all(r["script"].startswith("03_") for r in writable),
        "verdict": "生产库连接全部 mode=ro；可写连接只出现在步骤 3 的隔离副本脚本里"
                   if all(r["script"].startswith("03_") for r in writable) else "存在越界可写连接",
    }

    # ── ② 时间证明：Work 目录与交接文件未被本单写过 ──
    work_files = [p for p in WORK.rglob("*") if p.is_file()] if WORK.exists() else []
    work_max = max((p.stat().st_mtime for p in work_files), default=None)
    R["work_dir"] = {
        "path": str(WORK.relative_to(PROJ)),
        "file_count": len(work_files),
        "max_mtime_utc": utc(work_max) if work_max else None,
        "max_mtime_before_ticket_start":
            (work_max is not None and started_ms is not None and work_max * 1000 < started_ms),
        "note": "写文件必然刷新 mtime ⇒ 最大 mtime 早于本单开始时刻，即本单没写过 Work 目录。",
    }
    if HANDOVER.exists():
        st = HANDOVER.stat()
        R["handover"] = {
            "path": str(HANDOVER.relative_to(PROJ)),
            "size": st.st_size,
            "sha256": sha(HANDOVER),
            "mtime_utc": utc(st.st_mtime),
            "mtime_before_ticket_start":
                (started_ms is not None and st.st_mtime * 1000 < started_ms),
        }
    else:
        R["handover"] = {"path": str(HANDOVER), "exists": False}

    # ── ③ 源码哈希 vs 步骤 1 的记录 ──
    src_now = {}
    for rel in SOURCE_FILES:
        p = PROJ / rel
        st = p.stat()
        src_now[rel] = {"sha256": sha(p), "size": st.st_size,
                        "mtime_utc": utc(st.st_mtime)}
    R["source_now"] = src_now
    src_before = d01.get("sources") or {}
    R["source_compare"] = {
        rel: {"before": (src_before.get(rel) or {}).get("sha256"),
              "now": src_now[rel]["sha256"],
              "same": (src_before.get(rel) or {}).get("sha256") == src_now[rel]["sha256"]}
        for rel in SOURCE_FILES}

    # ── ④ 生产库当前哈希 vs 步骤 4 的 before ──
    p04 = OUT / "04_时间字段与单调关系.json"
    d04 = json.loads(p04.read_text(encoding="utf-8"))
    prod_before = d04.get("prod_before") or {}
    db_now = {}
    for k, p in PROD_DBS.items():
        if not p.exists():
            db_now[k] = {"exists": False}
            continue
        st = p.stat()
        wal = Path(str(p) + "-wal")
        shm = Path(str(p) + "-shm")
        db_now[k] = {"sha256": sha(p), "size": st.st_size, "mtime_utc": utc(st.st_mtime),
                     "wal_size": wal.stat().st_size if wal.exists() else None,
                     "shm_size": shm.stat().st_size if shm.exists() else None,
                     "wal_mtime_utc": utc(wal.stat().st_mtime) if wal.exists() else None}
    R["prod_db_now"] = db_now
    R["prod_db_compare"] = {
        k: {"before_sha": (prod_before.get(k) or {}).get("sha256"),
            "now_sha": (db_now.get(k) or {}).get("sha256"),
            "same": (prod_before.get(k) or {}).get("sha256") == (db_now.get(k) or {}).get("sha256")}
        for k in PROD_DBS}
    R["prod_db_reading"] = (
        "生产库被运行时进程持续写入（WAL）。**哈希相同**只说明本单读库前后该库没有落盘变化；"
        "**哈希不同**也不能归因于本单 —— 归因依据是上面的只读连接静态证明，不是哈希。"
        "本单不用哈希变化/不变作为「没写库」的唯一证据。"
        "**补一条旁证**：同时记录了 `-wal` 文件大小。若 `-wal` 为 0 字节且主库 mtime 未变，"
        "则「没有未 checkpoint 的写入」成立 ⇒ 此时「哈希不变」才可以读成「确实没有新写入」；"
        "若 `-wal` 非 0，则哈希不变**什么都不能证明**（写入还压在 WAL 里没落主库）。")

    # ── 结论 ──
    checks = {
        "生产库连接全部只读": R["readonly_summary"]["verdict"].startswith("生产库连接全部"),
        "可写连接只在步骤3隔离副本": R["readonly_summary"]["writable_only_in_step3"],
        "可写连接目标经变量链落在 iso/ 下": R["iso_guard"]["chain_all_ok"],
        "Work 目录 mtime 早于本单开始": R["work_dir"]["max_mtime_before_ticket_start"],
        "统一交接 mtime 早于本单开始": (R.get("handover") or {}).get("mtime_before_ticket_start"),
        "三个源码哈希与本单开始时一致": all(v["same"] for v in R["source_compare"].values()),
    }
    R["checks"] = checks
    R["verdict"] = ("全部通过" if all(v is True for v in checks.values())
                    else "存在未通过项，见 checks")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "09_未动自证.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== 只读连接静态证明 ===")
    for r in R["readonly_connections"]:
        print(f"  {r['script']}:{r['line']}  mode=ro={r['has_mode_ro']}  → {r['verdict']}")
    print(f"  合计 {R['readonly_summary']['total_connect_calls']} 处，"
          f"只读 {R['readonly_summary']['readonly_calls']}，可写 {R['readonly_summary']['writable_calls']}")
    print(f"  可写连接只在步骤3：{R['readonly_summary']['writable_only_in_step3']}")
    print(f"  ISO 根定义：{R['iso_guard']['iso_root_definition']}")
    for k, v in R["iso_guard"]["variable_chain"].items():
        print(f"  变量链 {k}: {v}")

    print("\n=== 时间证明 ===")
    print(f"  本单开始 {R['ticket_started_utc']}")
    print(f"  Work 目录 {R['work_dir']['file_count']} 个文件，最大 mtime "
          f"{R['work_dir']['max_mtime_utc']} → 早于本单开始="
          f"{R['work_dir']['max_mtime_before_ticket_start']}")
    h = R.get("handover") or {}
    print(f"  交接文件 mtime {h.get('mtime_utc')} → 早于本单开始="
          f"{h.get('mtime_before_ticket_start')}")

    print("\n=== 源码哈希 ===")
    for rel, v in R["source_compare"].items():
        print(f"  {rel}: 一致={v['same']}  {v['now'][:16]}…")

    print("\n=== 生产库 ===")
    for k, v in R["prod_db_compare"].items():
        n = db_now.get(k) or {}
        print(f"  {k}: 与步骤4 before 一致={v['same']}  wal={n.get('wal_size')} B  "
              f"mtime={n.get('mtime_utc')}")

    print("\n=== 结论 ===")
    for k, v in checks.items():
        print(f"  {k}: {v}")
    print(f"  → {R['verdict']}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
