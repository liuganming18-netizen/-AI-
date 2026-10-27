# -*- coding: utf-8 -*-
"""ACCEPT-04 / A：SYSTEM-ISOLATION-04 独立复验（只读）

做三件事，全部只读业务代码、只在 evidence/ACCEPT-04/ 落盘：
1) 版本核对：把交付声称的 6 个源码 SHA-256 与磁盘现状逐条重算比对，并记录 mtime。
2) 隔离纯函数矩阵：直接调 ServiceConfig.resolve_paths()（零进程、零写库），
   覆盖 jobs_db 必填 / 生产路径拒绝 / 符号链接绕行 / 冷层 MARKET_ARCHIVE 缺省。
3) 冷层回落实证：隔离模式 7 项齐全时 market_archive 是否为 None，以及子进程
   在 MARKET_ARCHIVE 未设置时 project_paths 会解析到哪个真实目录（是否存在）。

禁止：不启动任何服务进程、不写任何业务库、不修改业务代码。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # evidence/ACCEPT-04/queries -> 工程根
OUT = ROOT / "evidence" / "ACCEPT-04" / "out"
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
os.environ["DATA_HUB_PROJECT"] = str(ROOT)

RESULT = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "root": str(ROOT), "sections": {}}

# 清掉可能干扰场景判定的环境变量（记录原值以便如实说明）
_ENV_KEYS = ["JOBS_DB", "MARKET_DB", "MARKET_ARCHIVE", "LIVE_DB", "LIVE_DIR",
             "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "SYS_PID_DIR", "SYS_LOG_DIR",
             "ISOLATED_MODE", "EFFECTIVE_VIEW"]
_ENV_SEEN = {k: os.environ.get(k) for k in _ENV_KEYS if os.environ.get(k)}
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 1. 版本核对 ───────────────────────────────────────────────────────────────
EV = ROOT / "evidence" / "SYSTEM-ISOLATION-04"
ev_json = json.loads((EV / "path_isolation_evidence.json").read_text(encoding="utf-8"))
claimed = ev_json["execution_metadata"]["source_sha256"]

ver = []
for rel, want in claimed.items():
    p = ROOT / rel
    if not p.exists():
        ver.append({"file": rel, "claimed": want, "actual": None, "match": False,
                    "note": "MISSING"})
        continue
    got = sha256(p)
    ver.append({
        "file": rel, "claimed": want, "actual": got, "match": got == want,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)),
        "size": p.stat().st_size,
    })
RESULT["sections"]["version"] = {
    "evidence_dir": str(EV),
    "delivery_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime((EV / "README.md").stat().st_mtime)),
    "run_id_claimed": ev_json.get("run_id"),
    "items": ver,
    "all_match": all(v["match"] for v in ver),
    "post_delivery_edits": [v["file"] for v in ver
                            if v.get("mtime") and v["mtime"] >
                            time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime((EV / "README.md").stat().st_mtime))],
}

# ── 2. 隔离纯函数矩阵 ─────────────────────────────────────────────────────────
import service_manager as sm  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="accept04_iso_"))
paths = {
    "market_db": str(TMP / "market" / "market.sqlite3"),
    "live_db": str(TMP / "live" / "live.sqlite3"),
    "paper_ledger_dir": str(TMP / "paper"),
    "paper_lock_dir": str(TMP / "locks"),
    "jobs_db": str(TMP / "jobs" / "jobs.sqlite3"),
    "pid_dir": str(TMP / "pids"),
    "log_dir": str(TMP / "logs"),
}
for k in ("market", "live", "paper", "locks", "jobs"):
    (TMP / k).mkdir(parents=True, exist_ok=True)

PROD = {
    "jobs_db": str(ROOT / "数据" / "任务队列" / "jobs.sqlite3"),
    "market_db": str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
    "paper_ledger_dir": str(ROOT / "数据" / "paper"),
    "market_archive": str(ROOT / "数据" / "市场数据" / "parquet"),
}


def make(**over):
    kw = dict(paths)
    kw.update(over)
    cfg = sm.ServiceConfig(is_isolated=True, **kw)
    cfg.resolve_paths()
    return cfg


def run_case(name, fn):
    rec = {"case": name}
    try:
        cfg = fn()
        rec["result"] = "OK"
        rec["market_archive_after"] = cfg.market_archive
        rec["jobs_db_after"] = cfg.jobs_db
    except Exception as exc:                                    # noqa: BLE001
        rec["result"] = f"{type(exc).__name__}: {exc}"
        rec["rejected"] = isinstance(exc, RuntimeError)
    return rec


cases = []
cases.append(run_case("S1_隔离模式缺 jobs_db", lambda: make(jobs_db=None)))
cases.append(run_case("S2_隔离模式 jobs_db=生产任务队列", lambda: make(jobs_db=PROD["jobs_db"])))
cases.append(run_case("S3_隔离模式 market_db=生产热库", lambda: make(market_db=PROD["market_db"])))
cases.append(run_case("S4_隔离模式 paper_ledger=生产账本",
                      lambda: make(paper_ledger_dir=PROD["paper_ledger_dir"])))

# 符号链接别名绕行
sym = TMP / "alias_jobs.sqlite3"
try:
    if sym.exists() or sym.is_symlink():
        sym.unlink()
    os.symlink(PROD["jobs_db"], sym)
    cases.append(run_case("S5_隔离模式 jobs_db=符号链接→生产", lambda: make(jobs_db=str(sym))))
except OSError as exc:
    cases.append({"case": "S5_隔离模式 jobs_db=符号链接→生产", "result": f"SKIP: {exc}"})

# 相对路径绕行
rel = "数据/任务队列/jobs.sqlite3"
cases.append(run_case("S6_隔离模式 jobs_db=相对路径绕行", lambda: make(jobs_db=rel)))

# 关键：隔离模式 7 项齐全但**不传** market_archive（交付实测配置就是这样）
full = run_case("S7_隔离模式 7 项齐全_未传 market_archive", lambda: make())
cases.append(full)

# 冷层显式指向生产时应被拦截
cases.append(run_case("S8_隔离模式 market_archive=生产 parquet",
                      lambda: make(market_archive=PROD["market_archive"])))

# 非隔离模式缺 jobs_db → 回落生产
def non_iso():
    cfg = sm.ServiceConfig(is_isolated=False)
    old = os.environ.pop("JOBS_DB", None)
    try:
        cfg.resolve_paths()
    finally:
        if old is not None:
            os.environ["JOBS_DB"] = old
    return cfg

cases.append(run_case("S9_非隔离模式缺 jobs_db", non_iso))

RESULT["sections"]["isolation_matrix"] = {
    "tmp_dir": str(TMP),
    "cases": cases,
    "S7_market_archive_is_none": full.get("market_archive_after", "N/A") is None,
}

# ── 3. 冷层回落实证（子进程视角）─────────────────────────────────────────────
# 模拟 service_manager 生成的 shared_env：隔离模式不设 MARKET_ARCHIVE
child_env = {"DATA_HUB_PROJECT": str(ROOT), "MARKET_DB": paths["market_db"],
             "LIVE_DB": paths["live_db"], "PAPER_LEDGER_DIR": paths["paper_ledger_dir"]}
env_keys = [k for k in child_env if "ARCHIVE" in k]
# 用子进程重新 import project_paths，确保不受本进程已加载模块影响
code = (
    "import json,sys;"
    "sys.path.insert(0,r'%s');"
    "import project_paths as pp;"
    "print(json.dumps({'MARKET_ARCHIVE':str(pp.MARKET_ARCHIVE)}))" % BACKEND
)
import subprocess  # noqa: E402
clean_env = {k: v for k, v in os.environ.items()
             if k not in ("MARKET_ARCHIVE",)}
clean_env.update(child_env)
clean_env.pop("MARKET_ARCHIVE", None)
out = subprocess.run([sys.executable, "-c", code], env=clean_env,
                     capture_output=True, text=True, timeout=60)
try:
    child_archive = json.loads(out.stdout.strip().splitlines()[-1])["MARKET_ARCHIVE"]
except Exception:                                                # noqa: BLE001
    child_archive = f"PARSE_FAIL: {out.stdout[-200:]} / {out.stderr[-300:]}"

prod_parquet = ROOT / "数据" / "市场数据" / "parquet"
RESULT["sections"]["cold_layer"] = {
    "isolated_required_items": ["market_db", "live_db", "paper_ledger_dir",
                                "paper_lock_dir", "jobs_db", "pid_dir", "log_dir"],
    "market_archive_in_required": False,
    "child_env_has_market_archive": any("ARCHIVE" in k for k in child_env),
    "child_resolved_MARKET_ARCHIVE": child_archive,
    "production_parquet_exists": prod_parquet.is_dir(),
    "production_parquet_is_same": Path(child_archive).resolve() == prod_parquet.resolve()
    if child_archive and not str(child_archive).startswith("PARSE_FAIL") else None,
    "production_parquet_files": (sum(1 for _ in prod_parquet.rglob("*.parquet"))
                                 if prod_parquet.is_dir() else 0),
}

# ── 4. 收尾 ──────────────────────────────────────────────────────────────────
shutil.rmtree(TMP, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "q01_A_版本与隔离矩阵.json").write_text(
    json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps(RESULT["sections"]["version"]["all_match"]))
for c in cases:
    print(f"{c['case']:<42} {c['result'][:110]}")
print("S7 market_archive:", full.get("market_archive_after"))
print("child MARKET_ARCHIVE:", child_archive, "prod exists:", prod_parquet.is_dir(),
      "same:", RESULT["sections"]["cold_layer"]["production_parquet_is_same"])
