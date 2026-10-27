# -*- coding: utf-8 -*-
"""03｜隔离与空链路验证（生产零写入 + 幂等 + 现有 API 返回）。

四层链路逐层取行为证据：
  ① 策略产出候选 → 本单**不碰自然信号**，改用明确标注的合成候选（不是自然产出）
  ② 幂等持久化   → 同一 signal_uid 写两次只一行；first_seen_at 不变、updated_at 推进
  ③ 现有 API 返回 → 调 route 表绑定的同一个处理函数 `adapter._v1_signals_merged`
  ④ 页面落点     → 页面只请求 scope=current，研究标识被当前区白名单挡掉（结构性结论）

隔离纪律：
  · 所有写入只落在 `evidence/ACCUM-EARLY-08-WORK/isolated/`；
  · 生产信号库与四本纸面账本在前后各取一次文件身份，必须逐位不变；
  · 零写入判据 = SQL 级写探针 + 负向对照（不用文件哈希单独作证）。

输出：out/03_隔离与空链路.json
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib08 as L  # noqa: E402

WORKER = Path(__file__).resolve().parent / "03b_隔离链路_worker.py"


def backup_db(src: Path, dst: Path) -> dict:
    """用 SQLite 在线备份 API 取一致快照（不直接 cp 一个正在写的 WAL 库）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15)
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()
    return {"src": str(src), "dst": str(dst), "bytes": dst.stat().st_size,
            "sha256": L.sha256_file(dst)}


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    iso = L.ISO
    if iso.exists():
        shutil.rmtree(iso)
    (iso / "live").mkdir(parents=True)
    (iso / "paper").mkdir(parents=True)
    (iso / "locks").mkdir(parents=True)
    (iso / "tmp").mkdir(parents=True)

    # ── 0. 生产文件身份（前）──────────────────────────────────────────────
    prod_files = {
        "生产信号库": L.LIVE_DB,
        "纸面账本_3a": L.PAPER_DIR / "paper_3a.sqlite3",
        "纸面账本_3b": L.PAPER_DIR / "paper_3b.sqlite3",
        "纸面账本_3c": L.PAPER_DIR / "paper_3c.sqlite3",
        "纸面账本_3d": L.PAPER_DIR / "paper_3d.sqlite3",
        "观察明细文件": L.LOCKS / "paper_runtime_3a_observe.json",
    }
    before = {k: L.file_fingerprint(p) for k, p in prod_files.items()}
    rep["生产文件身份_前"] = before

    # ── 1. 负向对照（探针必须能抓到写）─────────────────────────────────────
    rep["探针负向对照"] = L.negative_control(iso / "tmp")

    # ── 2. 建隔离副本（SQLite 在线备份，非文件拷贝）────────────────────────
    rep["隔离副本"] = {
        "live": backup_db(L.LIVE_DB, iso / "live" / "live_signals.sqlite3"),
    }
    for arm in ("3a", "3b", "3c", "3d"):
        rep["隔离副本"][f"paper_{arm}"] = backup_db(
            L.PAPER_DIR / f"paper_{arm}.sqlite3",
            iso / "paper" / f"paper_{arm}.sqlite3")

    # ── 3. 跑隔离 worker（干净环境变量，进程级隔离）────────────────────────
    env = dict(os.environ)
    env.update({
        "LIVE_DB": str((iso / "live" / "live_signals.sqlite3").resolve()),
        "LIVE_DIR": str((iso / "live").resolve()),
        "PAPER_LEDGER_DIR": str((iso / "paper").resolve()),
        "PAPER_LOCK_DIR": str((iso / "locks").resolve()),
        "MARKET_DB": str(L.MARKET_DB),          # 行情库只读，指向生产（不写入）
        "PYTHONPATH": f"{L.PROJECT / '框架'}:{L.BACKEND}:{L.PROJECT}",
        "PYTHONHASHSEED": "0",
    })
    env.pop("EFFECTIVE_VIEW", None)
    run = L.run([L.PY, str(WORKER)], cwd=L.BACKEND, env=env, timeout=300)
    rep["worker"] = {"returncode": run["returncode"], "elapsed_s": run["elapsed_s"],
                     "stderr_tail": run["stderr"][-3000:]}
    parsed = None
    for line in run["stdout"].splitlines():
        if line.startswith("@@RESULT@@"):
            parsed = json.loads(line[len("@@RESULT@@"):])
    if parsed is None:
        rep["worker"]["parse_error"] = "worker 未输出 @@RESULT@@"
        rep["worker"]["stdout_tail"] = run["stdout"][-4000:]
        L.dump("03_隔离与空链路.json", rep)
        print("!! worker 失败，见 out/03_隔离与空链路.json")
        return 1
    rep["隔离链路"] = parsed

    # ── 4. 生产文件身份（后）+ 逐位比对 ───────────────────────────────────
    after = {k: L.file_fingerprint(p) for k, p in prod_files.items()}
    rep["生产文件身份_后"] = after
    same = {}
    for k in before:
        b, a = before[k], after[k]
        same[k] = {
            "sha256_same": b.get("sha256") == a.get("sha256"),
            "size_same": b.get("size") == a.get("size"),
            "mtime_same": b.get("mtime_utc") == a.get("mtime_utc"),
        }
    rep["生产零写入判定"] = {
        "逐文件": same,
        "全部未变": all(v["sha256_same"] and v["size_same"] for v in same.values()),
        "注意": ("观察明细文件由运行中的运行时（pid 91947）持续重写，"
                 "其 mtime/sha 变化**属正常生产行为**，不计为本单写入；"
                 "本单只看信号库与四本账本"),
    }
    rep["生产零写入判定"]["全部未变"] = all(
        v["sha256_same"] and v["size_same"]
        for k, v in same.items() if k != "观察明细文件")

    # ── 5. 判定汇总（机器判，不手抄）──────────────────────────────────────
    il = rep["隔离链路"]
    idem = il.get("幂等核对") or {}
    api = il.get("API层") or {}
    iso_self = il.get("隔离自报") or {}
    checks = {
        "隔离成立：LIVE_DB 不指向生产": iso_self.get("LIVE_DB_is_production") is False,
        "隔离成立：账本目录不指向生产": iso_self.get("PAPER_LEDGER_DIR_is_production") is False,
        "探针负向对照抓到写（探针非恒真）": (rep["探针负向对照"] or {}).get("caught") is True,
        "同一 signal_uid 写两次只一行": idem.get("同一uid只一行") is True,
        "第二次写入记为 updated（非新增）": (idem.get("两次写入统计") or {}).get("second_updated") == 1
        and (idem.get("两次写入统计") or {}).get("second_new") == 0,
        "first_seen_at 未被改写": idem.get("first_seen_at_未被改写") is True,
        "updated_at 已推进": idem.get("updated_at_已推进") is True,
        "研究标识 origin 落库为 research": idem.get("research_origin") == "research",
        "payload 带事件角色 role": idem.get("payload_role") == "enterable_arm_entry",
        "审计通道 scope=all 能读到研究信号":
            (api.get("scope_all") or {}).get("research_uid_present") is True,
        "交易信号区 scope=current 读不到研究信号":
            (api.get("scope_current") or {}).get("research_uid_present") is False,
        "正向对照：origin=live 的合成信号能进当前区":
            (api.get("scope_current") or {}).get("live_uid_present") is True,
        "生产信号库行数未变": (il.get("生产库只读探测") or {}).get("rows_unchanged") is True,
        "生产库上 0 条写语句": len((il.get("生产库只读探测") or {}).get("write_like") or []) == 0,
        "生产信号库与四本账本逐位未变": rep["生产零写入判定"]["全部未变"] is True,
    }
    rep["判定"] = {"checks": checks,
                 "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [k for k, v in checks.items() if not v]
    rep["判定"]["结论"] = ("隔离空链路成立：候选 → 幂等持久化 → 现有 API（审计通道）三层打通；"
                       "研究标识被当前交易信号区白名单结构性挡掉，生产库与账本零写入。"
                       if not fail else f"存在未通过项，不得声称链路成立：{fail}")

    p = L.dump("03_隔离与空链路.json", rep)
    print("== 03 隔离与空链路 ==")
    for k, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + k)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("worker rc:", run["returncode"], "elapsed", run["elapsed_s"])
    print("->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
