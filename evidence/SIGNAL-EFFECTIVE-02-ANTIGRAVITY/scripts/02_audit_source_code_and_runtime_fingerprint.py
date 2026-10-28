#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIGNAL-EFFECTIVE-02 独立复核脚本 2
源码差异哈希与当前运行进程指纹核查
执行方：反重力（只读、无副作用）
"""

import os
import json
import hashlib
import subprocess

DESKTOP_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
BACKEND_DIR = os.path.join(DESKTOP_DIR, "框架/backend")

FILES_TO_CHECK = [
    "live_api.py",
    "live_worker.py",
    "paper_engine.py",
    "unified_runtime.py",
    "wyckoff_factor_resonance.py",
]

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../out"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT_JSON_PATH = os.path.join(OUT_DIR, "source_code_and_process_fingerprint.json")

def file_hash(path):
    if not os.path.exists(path):
        return "NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def get_process_info():
    cmd = "ps aux | grep -E 'live_api|unified_runtime|live_worker|backtest_worker|realtime_collector' | grep -v grep"
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    procs = []
    for line in res.stdout.strip().splitlines():
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({
                "user": parts[0],
                "pid": int(parts[1]),
                "cpu": parts[2],
                "mem": parts[3],
                "started": parts[8],
                "command": parts[10]
            })
    return procs

def main():
    print("[*] 正在扫描源码哈希与运行进程指纹...")
    file_hashes = {}
    for f in FILES_TO_CHECK:
        full_path = os.path.join(BACKEND_DIR, f)
        file_hashes[f] = {
            "path": full_path,
            "sha256": file_hash(full_path),
            "size_bytes": os.path.getsize(full_path) if os.path.exists(full_path) else None
        }

    # 读取 lock 状态文件
    lock_files = {}
    lock_dir = os.path.join(BACKEND_DIR, "locks")
    if os.path.exists(lock_dir):
        for lf in os.listdir(lock_dir):
            if lf.endswith(".json"):
                lp = os.path.join(lock_dir, lf)
                try:
                    with open(lp, "r", encoding="utf-8") as fp:
                        lock_files[lf] = json.load(fp)
                except Exception as e:
                    lock_files[lf] = {"error": str(e)}

    # 读取 service_meta
    meta_path = os.path.join(BACKEND_DIR, "service_meta_8787.json")
    meta_data = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fp:
                meta_data = json.load(fp)
        except Exception as e:
            meta_data = {"error": str(e)}

    output = {
        "timestamp_scan": subprocess.check_output("date -u +'%Y-%m-%dT%H:%M:%SZ'", shell=True).decode().strip(),
        "source_code_hashes": file_hashes,
        "running_processes": get_process_info(),
        "service_meta_8787": meta_data,
        "locks_state": lock_files
    }

    with open(OUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[+] 源码与运行进程指纹已落盘: {OUT_JSON_PATH} (SHA256: {file_hash(OUT_JSON_PATH)})")

if __name__ == "__main__":
    main()
