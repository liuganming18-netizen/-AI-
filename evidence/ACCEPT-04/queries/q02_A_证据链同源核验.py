# -*- coding: utf-8 -*-
"""ACCEPT-04 / A：同一次运行证据链核验（只读）

工单第 3 条要求「同一次运行的硬证据闭环」。本脚本只做交叉比对，不启动进程：
把 path_isolation_evidence.json 里的运行编号 / PID / 端口 / 隔离临时目录，
与 test_commands_and_outputs.log、README、全部交付文件逐一对照；
并核查该 JSON 是否由任何**已交付脚本**产出。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EV = ROOT / "evidence" / "SYSTEM-ISOLATION-04"
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"

j = json.loads((EV / "path_isolation_evidence.json").read_text(encoding="utf-8"))
log = (EV / "test_commands_and_outputs.log").read_text(encoding="utf-8", errors="replace")
readme = (EV / "README.md").read_text(encoding="utf-8", errors="replace")
ver = (EV / "version_hash.txt").read_text(encoding="utf-8", errors="replace")
mod = (EV / "modified_files.txt").read_text(encoding="utf-8", errors="replace")

api_pid = j["subprocesses"]["api"]["pid"]
wk_pid = j["subprocesses"]["worker"]["pid"]
port = j["port"]
run_id = j["run_id"]
tmpdir = j["isolated_test_dir"]
tmpname = Path(tmpdir).name

# 日志里出现的所有 PID（形如 pid=NNNN / "PID=NNNN"）
log_pids = sorted({int(x) for x in re.findall(r"[Pp][Ii][Dd][=:]\s*(\d{4,6})", log)})
log_ports = sorted({int(x) for x in re.findall(r"端口[:：]\s*(\d{4,5})", log)})
json_ports = [port]

delivered = sorted(p for p in EV.iterdir() if p.is_file())
gen_hits = {}
for p in delivered:
    try:
        s = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:                                            # noqa: BLE001
        continue
    gen_hits[p.name] = {
        "suffix": p.suffix,
        "mentions_run_id": run_id in s,
        "writes_evidence_json": "path_isolation_evidence.json" in s,
        "json_dump": "json.dump" in s,
        "mentions_pid_api": str(api_pid) in s,
    }

report = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "evidence_dir": str(EV),
    "run_id": run_id,
    "json_claims": {
        "api_pid": api_pid, "worker_pid": wk_pid, "port": port,
        "isolated_test_dir": tmpdir,
        "start_utc": j["execution_metadata"]["start_time_utc"],
        "end_utc": j["execution_metadata"]["end_time_utc"],
        "duration_s": j["execution_metadata"]["duration_seconds"],
    },
    "log_observed": {
        "pids": log_pids, "ports": log_ports,
        "run_id_hits": log.count(run_id),
        "api_pid_hits": log.count(str(api_pid)),
        "worker_pid_hits": log.count(str(wk_pid)),
        "port_hits": log.count(str(port)),
        "tmpdir_name_hits": log.count(tmpname),
        "test_durations": re.findall(r"Ran \d+ tests? in [\d.]+s", log),
    },
    "readme_claims": {
        "mentions_api_pid": str(api_pid) in readme,
        "mentions_worker_pid": str(wk_pid) in readme,
        "mentions_log_pid_28000": "28000" in readme,
        "mentions_run_id": run_id in readme,
    },
    "delivered_files": gen_hits,
    "version_hash_sha256_count": len(re.findall(r"\b[0-9a-f]{64}\b", ver)),
    "modified_files_mentions_generator": {
        "path_isolation_evidence": mod.count("path_isolation_evidence"),
        "run_id": mod.count(run_id),
        "audit_iso04": mod.count("audit_iso04"),
    },
}

# 判定：三项必须同时成立才算「同一次运行」
same_run = (log.count(run_id) > 0
            and log.count(str(api_pid)) > 0
            and log.count(str(wk_pid)) > 0
            and log.count(str(port)) > 0)
report["same_run_verdict"] = {
    "run_id_in_log": log.count(run_id) > 0,
    "api_pid_in_log": log.count(str(api_pid)) > 0,
    "worker_pid_in_log": log.count(str(wk_pid)) > 0,
    "port_in_log": log.count(str(port)) > 0,
    "tmpdir_in_log": log.count(tmpname) > 0,
    "same_run_established": same_run,
}
report["generator_delivered"] = any(
    v["suffix"] == ".py" and v["writes_evidence_json"] and v["json_dump"]
    for v in gen_hits.values())
report["delivered_py_files"] = [n for n, v in gen_hits.items() if v["suffix"] == ".py"]

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q02_A_证据链同源核验.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("JSON claims :", report["json_claims"])
print("log PIDs    :", log_pids)
print("log ports   :", log_ports)
print("run_id hits :", log.count(run_id), "| api pid hits:", log.count(str(api_pid)),
      "| worker pid hits:", log.count(str(wk_pid)), "| port hits:", log.count(str(port)))
print("tmpdir hits :", log.count(tmpname))
print("generator delivered:", report["generator_delivered"])
print("same_run_established:", same_run)
print("readme mentions 28000:", report["readme_claims"]["mentions_log_pid_28000"])
