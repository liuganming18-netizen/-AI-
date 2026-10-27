#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-1｜隔离路径解析纯函数验证（零进程、零污染）

目的：不启动任何子进程，只在 Python 内构造 ServiceConfig 并调用 resolve_paths()，
      确认「jobs_db 是否被强制隔离」「不隔离时的回落目标是什么」。

纪律：
  - 不启动 ServiceManager，不 spawn 任何进程
  - 不写任何生产目录
  - 所有结论落到 out/A1_resolve_paths.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import hashlib
import datetime
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT_ROOT / "框架"))

# 确保环境干净：若外部注入了隔离变量，本次验证会失真
ENV_KEYS = ["JOBS_DB", "ISOLATED_MODE", "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR",
            "LIVE_DB", "LIVE_DIR", "MARKET_DB", "SYS_PID_DIR", "SYS_LOG_DIR"]
env_snapshot = {k: os.environ.get(k) for k in ENV_KEYS}

import service_manager as SM  # noqa: E402

SRC = BACKEND / "service_manager.py"
src_bytes = SRC.read_bytes()

PROD_JOBS = PROJECT_ROOT / "数据" / "任务队列" / "jobs.sqlite3"
PROD_LEDGER = PROJECT_ROOT / "数据" / "paper"


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mtime_of(p: Path) -> str:
    return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def run_case(name: str, cfg_kwargs: dict, tmp: Path) -> dict:
    """构造 ServiceConfig 并只调 resolve_paths()，不启动任何进程。"""
    base = dict(
        port=64999,
        py=sys.executable,
        market_db=str(tmp / "market" / "m.sqlite3"),
        live_db=str(tmp / "live" / "l.sqlite3"),
        paper_ledger_dir=str(tmp / "paper_ledger"),
        paper_lock_dir=str(tmp / "locks"),
        pid_dir=tmp / "pids",
        log_dir=tmp / "logs",
    )
    base.update(cfg_kwargs)
    rec = {"case": name, "input_keys": sorted(cfg_kwargs.keys())}
    try:
        cfg = SM.ServiceConfig(**base)
        rec["is_isolated_before"] = cfg.is_isolated
        rec["jobs_db_before"] = cfg.jobs_db
        cfg.resolve_paths()
        rec["raised"] = None
        rec["is_isolated_after"] = cfg.is_isolated
        rec["jobs_db_after"] = cfg.jobs_db
        rec["jobs_db_resolved"] = str(Path(cfg.jobs_db).resolve()) if cfg.jobs_db else None
        rec["points_to_production_jobs"] = bool(
            cfg.jobs_db and Path(cfg.jobs_db).resolve() == PROD_JOBS.resolve()
        )
        rec["under_production_data_root"] = bool(
            cfg.jobs_db and (PROJECT_ROOT / "数据").resolve() in Path(cfg.jobs_db).resolve().parents
        )
        rec["verdict"] = "未阻断"
    except Exception as exc:  # noqa: BLE001
        rec["raised"] = f"{type(exc).__name__}: {exc}"
        rec["verdict"] = "已阻断"
    return rec


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="audit03_resolve_"))
    for d in ("market", "live", "paper_ledger", "locks", "pids", "logs"):
        (tmp / d).mkdir(parents=True, exist_ok=True)

    cases = []
    # 场景 1：隔离模式 + 完全不传 jobs_db（= 交付测试脚本 Test 2 的真实构造）
    cases.append(run_case("S1 隔离+无jobs_db(等价Test2构造)", {"is_isolated": True}, tmp))
    # 场景 2：隔离模式 + jobs_db 指向生产任务队列
    cases.append(run_case("S2 隔离+jobs_db=生产任务队列", {"is_isolated": True, "jobs_db": str(PROD_JOBS)}, tmp))
    # 场景 3：非隔离模式 + 无 jobs_db（日常启动路径）
    cases.append(run_case("S3 非隔离+无jobs_db", {"is_isolated": False}, tmp))
    # 场景 4：非隔离 + 环境变量 ISOLATED_MODE=1
    os.environ["ISOLATED_MODE"] = "1"
    cases.append(run_case("S4 非隔离入参+ISOLATED_MODE=1", {"is_isolated": False}, tmp))
    os.environ.pop("ISOLATED_MODE", None)
    # 场景 5：隔离模式 + jobs_db 指向 tmp（正确用法）
    cases.append(run_case("S5 隔离+jobs_db=tmp(正确用法)", {"is_isolated": True, "jobs_db": str(tmp / "jobs.sqlite3")}, tmp))

    # 交付测试脚本 Test 2 的构造是否真的能过？单独再确认一次（只调 resolve_paths）
    test2_src = (BACKEND / "tests" / "test_system_isolation_03.py").read_text(encoding="utf-8")
    has_jobs_db_in_test = "jobs_db" in test2_src

    out = {
        "工单": "SYSTEM-INDEPENDENT-AUDIT-03 / A-1",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "被检源码": str(SRC),
        "源码_sha256": sha256_of(SRC),
        "源码_sha256_前16": sha256_of(SRC)[:16],
        "源码_mtime": mtime_of(SRC),
        "源码字节数": len(src_bytes),
        "环境快照_检查前": env_snapshot,
        "交付哈希清单": {
            "version_hash.txt_生成时刻": "2026-10-23T14:36:30+08:00",
            "service_manager.py_在清单中的状态": "?? 未跟踪（无 SHA-256 记录）",
            "是否可按SHA绑定": False,
        },
        "测试脚本是否出现jobs_db字样": has_jobs_db_in_test,
        "生产任务队列": {
            "path": str(PROD_JOBS),
            "exists": PROD_JOBS.exists(),
            "mtime": mtime_of(PROD_JOBS) if PROD_JOBS.exists() else None,
            "size": PROD_JOBS.stat().st_size if PROD_JOBS.exists() else None,
        },
        "场景": cases,
        "临时目录": str(tmp),
    }
    outp = PROJECT_ROOT / "evidence" / "SYSTEM-INDEPENDENT-AUDIT-03" / "out" / "A1_resolve_paths.json"
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"源码 SHA-256 = {out['源码_sha256']}")
    print(f"源码 mtime   = {out['源码_mtime']}  ({out['源码字节数']} B)")
    print(f"测试脚本含 jobs_db 字样: {has_jobs_db_in_test}")
    print(f"生产 jobs.sqlite3: exists={out['生产任务队列']['exists']} mtime={out['生产任务队列']['mtime']}")
    print()
    for c in cases:
        print(f"[{c['verdict']}] {c['case']}")
        if c.get("raised"):
            print(f"        抛出: {c['raised'][:120]}")
        else:
            print(f"        jobs_db -> {c.get('jobs_db_resolved')}")
            print(f"        指向生产={c.get('points_to_production_jobs')} 在数据根下={c.get('under_production_data_root')}")
    print(f"\n落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
