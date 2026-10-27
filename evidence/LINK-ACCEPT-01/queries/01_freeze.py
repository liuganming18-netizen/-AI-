#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01_freeze.py —— LINK-ACCEPT-01 开审冻结（T0）

被审代码是一个**还在变动**的目标（开审期间 unified_runtime.py 连续被改），
所以必须先钉一个快照时刻，之后所有结论只对这个快照有效。

被审文件集**动态构建**（不沿用上一轮清单）：
  框架/backend 下全部 *.py（排除 __pycache__）
  UI界面/web/src 下全部文件
  UI界面/web/package.json、vite.config.ts
  框架/backend/live/paper_new_entry_gate.json
  参考基线 evidence/CODEX-CONNECT-02/test_runtime.py
原因：第一版沿用了上一轮 148 个文件，事后发现它**漏了 unified_runtime.py**
（正是本轮开审期间被反复修改的那个文件），等于把最需要盯的文件放在冻结范围外。
故 T0a(148) 作废，改用 T0b(动态全集)。

输出：out/T0_冻结快照.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "LINK-ACCEPT-01" / "out"
OUT.mkdir(parents=True, exist_ok=True)
PREV = ROOT / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03" / "out" / "00_开工快照.json"

OBJECTS = {"A": "PAPER-LIVE-01", "B": "UI-LINK-01"}
BASELINE = "CODEX-CONNECT-02"


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
                "启动时刻": dt.datetime.fromtimestamp(p.create_time()).strftime("%Y-%m-%d %H:%M:%S"),
                "cwd": p.cwd()}
    except Exception as e:  # noqa: BLE001
        return {"pid": pid, "error": str(e)[:120]}


def delivery_state() -> dict:
    st = {}
    for k, name in OBJECTS.items():
        d = ROOT / "evidence" / name
        files = sorted(p for p in d.rglob("*") if p.is_file()) if d.is_dir() else []
        st[k] = {
            "目录": str(d.relative_to(ROOT)),
            "存在": d.is_dir(),
            "文件数": len(files),
            "文件": [str(p.relative_to(ROOT)) for p in files],
            "最新mtime": max((mtime(p) for p in files), default=None),
            "有报告": any(p.suffix == ".md" and ("报告" in p.name or p.name.lower().startswith("readme")) for p in files),
            "有哈希清单": any("hash" in p.name.lower() or "版本" in p.name or "versions" in p.name.lower() for p in files),
        }
    return st


def build_set() -> list[str]:
    rels: list[str] = []
    for p in sorted((ROOT / "框架" / "backend").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rels.append(str(p.relative_to(ROOT)))
    for p in sorted((ROOT / "UI界面" / "web" / "src").rglob("*")):
        if p.is_file():
            rels.append(str(p.relative_to(ROOT)))
    for extra in ("UI界面/web/package.json", "UI界面/web/vite.config.ts",
                  "框架/backend/live/paper_new_entry_gate.json",
                  f"evidence/{BASELINE}/test_runtime.py"):
        if (ROOT / extra).exists():
            rels.append(extra)
    return sorted(set(rels))


def main() -> None:
    rels = build_set()
    files = {}
    missing = []
    for rel in rels:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        files[rel] = {"sha256": sha(p), "字节": p.stat().st_size, "mtime": mtime(p)}

    snap = {
        "生成时刻": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "被审文件数": len(files),
        "集合构建": "backend全部py + web/src全部 + package.json + vite.config.ts + 闸门 + 基线测试脚本",
        "缺失文件": missing,
        "交付状态": delivery_state(),
        "运行进程": [proc_info(pid) for pid in pids_on(8787)],
        "基线哈希": _baseline_hashes(),
        "全量指纹": files,
    }
    p = OUT / "T0_冻结快照.json"
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"T0 = {snap['生成时刻']}  被审文件 {len(files)} 个，缺失 {len(missing)}")
    for k, v in snap["交付状态"].items():
        print(f"  {k} {v['目录']}: 存在={v['存在']} 文件数={v['文件数']} 有报告={v['有报告']} 最新={v['最新mtime']}")
    for pr in snap["运行进程"]:
        print(f"  8787 pid={pr.get('pid')} 启动={pr.get('启动时刻')}")
    for row in snap["基线哈希"]:
        print(f"  基线 {'一致' if row.get('一致') else '不一致'} {row['文件']} mtime={row.get('mtime')}")


def _baseline_hashes() -> list[dict]:
    vp = ROOT / "evidence" / BASELINE / "versions.json"
    if not vp.exists():
        return []
    out = []
    for rel, expect in json.loads(vp.read_text(encoding="utf-8")).items():
        p = ROOT / rel
        row = {"文件": rel, "声明": expect, "存在": p.exists()}
        if p.exists():
            act = sha(p)
            row.update({"实际": act, "一致": act == expect, "mtime": mtime(p), "字节": p.stat().st_size})
        else:
            row["一致"] = False
        out.append(row)
    return out


if __name__ == "__main__":
    main()
