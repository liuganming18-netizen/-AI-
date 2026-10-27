# -*- coding: utf-8 -*-
"""60｜下单防护「五层闸门」单因素矩阵（隔离沙箱，跑真实代码）。

工单要求（原话）
----------------
「对五层条件逐层执行单因素移除（每次只移除/破坏一层，其余保持有效），另做仅保留单一层、
全层有效、全层失效等对照；**不得用 C1→C4 逐步累计改坏的结果声称每一层单独有效**。」

本脚本的做法
------------
矩阵在**独立沙箱** `isolated/gate/` 里跑（库、账本、闸门文件全部是副本），
由 `60b_闸门矩阵_worker.py` 在**新进程**里调用生产同一份
`SignalPersistenceAndRouter._scan_actionable()` 与 `route_pending_orders()`；
每一格只改一个维度，互不累计。

同时证明
--------
· 生产信号库 / 生产账本 / 生产行情库的写语句 = 0（进程内 SQL 探针）；
· 生产闸门文件 `paper_new_entry_gate.json` 未被改动（sha256 前后一致）；
· 生产后端 8787 未重启（pid 不变）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib09 as U  # noqa: E402

GATE_ROOT = U.ISO / "gate"
WORKER = Path(__file__).resolve().parent / "60b_闸门矩阵_worker.py"
ARMS = ("3a", "3b", "3c", "3d")
PROD_GATE = U.BACKEND / "live" / "paper_new_entry_gate.json"


def prepare() -> dict:
    """造沙箱：信号库 + 四臂账本 + 闸门文件（全部副本）。"""
    live_dir = GATE_ROOT / "live"
    paper_dir = GATE_ROOT / "paper"
    lock_dir = GATE_ROOT / "locks"
    for d in (live_dir, paper_dir, lock_dir):
        d.mkdir(parents=True, exist_ok=True)
    live_db = live_dir / "live_signals.sqlite3"
    U.backup_db(U.LIVE_DB, live_db)
    paper = {}
    for a in ARMS:
        src = U.PAPER_DIR / f"paper_{a}.sqlite3"
        if not src.exists():
            paper[a] = {"skipped": "源不存在"}
            continue
        dst = paper_dir / f"paper_{a}.sqlite3"
        for suf in ("-wal", "-shm"):
            p = Path(str(dst) + suf)
            if p.exists():
                p.unlink()
        paper[a] = U.backup_db(src, dst)
    gate_src = PROD_GATE
    gate_dst = GATE_ROOT / "paper_new_entry_gate.json"
    if gate_src.exists():
        gate_dst.write_text(gate_src.read_text(encoding="utf-8"), encoding="utf-8")
    return {"live_db": live_db, "paper_dir": paper_dir, "lock_dir": lock_dir,
            "gate_file": gate_dst, "paper": paper,
            "gate_src_sha256": U.sha256_file(gate_src) if gate_src.exists() else None,
            "gate_dst_sha256": U.sha256_file(gate_dst) if gate_dst.exists() else None}


def main() -> int:
    checks: dict = {}
    prod_live_fp = U.fingerprint(U.LIVE_DB)
    prod_paper_fp = {a: U.fingerprint(U.PAPER_DIR / f"paper_{a}.sqlite3") for a in ARMS}
    prod_gate_fp = U.fingerprint(PROD_GATE)
    prod_8787 = U.run(["lsof", "-nP", "-iTCP:8787", "-sTCP:LISTEN"], timeout=20)
    prod_pid_before = (prod_8787["stdout"].split() or [None])[1] if prod_8787["stdout"] else None

    sb = prepare()
    checks["沙箱信号库来自生产在线备份"] = sb["live_db"].exists()
    checks["沙箱闸门文件与生产逐字节一致"] = sb["gate_src_sha256"] == sb["gate_dst_sha256"]

    env = U.iso_env({
        "LIVE_DB": str(sb["live_db"].resolve()),
        "LIVE_DIR": str(sb["live_db"].parent.resolve()),
        "PAPER_LEDGER_DIR": str(sb["paper_dir"].resolve()),
        "PAPER_LOCK_DIR": str(sb["lock_dir"].resolve()),
        "PAPER_GATE_FILE": str(sb["gate_file"].resolve()),
        "MARKET_DB": str(U.MARKET_DB.resolve()),
    })
    r = U.run([U.PY, str(WORKER)], cwd=U.PROJECT, env=env, timeout=900)
    mat = None
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("@@MATRIX@@"):
            mat = json.loads(line[len("@@MATRIX@@"):])
            break
    checks["矩阵工作进程产出结果"] = mat is not None
    if mat is None:
        U.dump("60_闸门矩阵.json", {"worker": r,
                                  "判定": U.summarise(checks, "", "矩阵工作进程未产出结果")})
        print((r["stdout"] or "")[-3000:])
        print((r["stderr"] or "")[-3000:])
        return 1

    cells = {c["key"]: c for c in mat["cells"]}
    checks["矩阵共 14 格"] = len(cells) == 14
    checks["无单元格异常"] = not mat.get("errors")
    checks["沙箱闸门为开启（否则观察不到挂单/开仓）"] = bool(
        (mat.get("GATE") or {}).get("allow_new_entries"))
    checks["沙箱库 = isolated/gate 下的副本"] = str(mat.get("LIVE_DB", "")).startswith(
        str(GATE_ROOT.resolve()))

    # ── 单因素移除：只让一层变通过 ⇒ 每格都必须仍被挡住 ─────────────────
    single_removal = ["B", "C", "D", "E", "F", "G"]
    for k in single_removal:
        c = cells.get(k) or {}
        checks[f"单因素移除 {k}：未被扫描到"] = c.get("被扫描到") is False
    # ── 仅保留单一层：其余全通过，只让一层不通过 ⇒ 每格都必须仍被挡住 ──
    keep_one = ["H", "I", "J", "K", "M"]
    for k in keep_one:
        c = cells.get(k) or {}
        checks[f"仅保留单层 {k}：未被扫描到"] = c.get("被扫描到") is False
    # ── 对照 ───────────────────────────────────────────────────────────
    checks["基准 A（原始研究行）：未被扫描到"] = (cells.get("A") or {}).get("被扫描到") is False
    checks["全层失效 L：被扫描到（装置有效）"] = (cells.get("L") or {}).get("被扫描到") is True
    checks["全层失效 N（调用方传 origin=live）：被扫描到"] = (
        cells.get("N") or {}).get("被扫描到") is True
    checks["全层失效 L：登记为挂单 = 1"] = (cells.get("L") or {}).get("登记为挂单") is True
    checks["全层失效 N：登记为挂单 = 1"] = (cells.get("N") or {}).get("登记为挂单") is True
    # 「生成模拟开仓」单独记录，不作为通过条件（旧反例只有挂单、没有持仓）
    pos_l = (cells.get("L") or {}).get("生成模拟开仓")
    checks["全层失效 L：模拟开仓状态已如实记录"] = pos_l is not None

    # ── 零写入与生产不变 ────────────────────────────────────────────────
    probe = mat.get("probe") or {}
    writes = probe.get("写语句按库") or {}
    prod_paths = (str(U.LIVE_DB.resolve()), str(U.PAPER_DIR.resolve()),
                  str(U.MARKET_DB.resolve()))
    checks["对生产信号库/账本/行情库写语句 = 0"] = all(
        sum(n for k, n in writes.items() if k.startswith(p)) == 0 for p in prod_paths)
    checks["写语句只落在 isolated/gate 下"] = all(
        k.startswith(str(GATE_ROOT.resolve())) for k in writes)
    checks["生产信号库未被改动（sha256）"] = U.same_file(prod_live_fp, U.fingerprint(U.LIVE_DB))["same"]
    checks["生产四本账本未被改动（sha256）"] = all(
        U.same_file(prod_paper_fp[a], U.fingerprint(U.PAPER_DIR / f"paper_{a}.sqlite3"))["same"]
        for a in ARMS)
    checks["生产闸门文件未被改动（sha256）"] = U.same_file(
        prod_gate_fp, U.fingerprint(PROD_GATE))["same"]
    prod_8787_after = U.run(["lsof", "-nP", "-iTCP:8787", "-sTCP:LISTEN"], timeout=20)
    prod_pid_after = (prod_8787_after["stdout"].split() or [None])[1] if prod_8787_after["stdout"] else None
    checks["生产后端 8787 未重启（pid 不变）"] = prod_pid_before == prod_pid_after and bool(prod_pid_before)

    table = []
    for k in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N"):
        c = cells.get(k) or {}
        led = c.get("ledger") or {}
        table.append({
            "格": k, "说明": c.get("label"), "调用方origin": c.get("caller_origin"),
            "被扫描到": c.get("被扫描到"),
            "登记为挂单": c.get("登记为挂单"),
            "生成模拟开仓": c.get("生成模拟开仓"),
            "paper_pending": led.get("paper_pending"),
            "paper_position": led.get("paper_position"),
            "paper_order": led.get("paper_order"),
            "paper_blocked": led.get("paper_blocked"),
            "paper_processed": led.get("paper_processed"),
            "reject_reasons": led.get("paper_order_reject_reasons"),
            "终态拒单明细": led.get("paper_processed_detail"),
            "route_stats": c.get("route_stats"),
            "改动": c.get("改动"),
        })

    rep = {
        "生成时间": U.now_utc(),
        "沙箱": {"root": str(GATE_ROOT), "live_db": str(sb["live_db"]),
                 "paper_dir": str(sb["paper_dir"]),
                 "gate_file": str(sb["gate_file"]),
                 "闸门内容": mat.get("GATE"),
                 "生产闸门sha256": sb["gate_src_sha256"],
                 "沙箱闸门sha256": sb["gate_dst_sha256"]},
        "矩阵": table,
        "工作进程": {"returncode": r["returncode"], "elapsed_s": r["elapsed_s"],
                     "stderr": (r["stderr"] or "")[-1500:]},
        "写探针": probe,
        "生产不变": {"信号库": prod_live_fp, "8787_pid_前": prod_pid_before,
                     "8787_pid_后": prod_pid_after},
        "判定": U.summarise(checks,
                          "五层闸门单因素矩阵通过：任一层单独有效即可挡住研究行；全层失效才可被扫描",
                          "五层闸门单因素矩阵未通过"),
    }
    p = U.dump("60_闸门矩阵.json", rep)
    U.print_checks("60 单因素闸门矩阵", rep, p)
    print("| 格 | 被扫描到 | 登记挂单 | 模拟开仓 |")
    for row in table:
        print(f"| {row['格']} | {row['被扫描到']} | {row['登记为挂单']} | {row['生成模拟开仓']} |")
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
