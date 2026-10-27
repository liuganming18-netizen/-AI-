# -*- coding: utf-8 -*-
"""50｜隔离环境跨进程恢复（写入进程关闭 → 独立新进程读回）。

场景（严格按工单第 4 条）
------------------------
1. 在 `isolated/recovery/` 造一本**只有表结构、没有研究行**的隔离信号库
   （从生产在线备份后只删研究行；生产库不动）；
2. **进程 A**（真实导入器 `research_signal_import.py --apply`）写入 289 条 → 进程退出；
3. **进程 B**（同一条命令重放）→ 必须 0 新增、289 更新、总行数不变、`first_seen_at` 不变；
4. **进程 C**（隔离 API 服务，独立新进程）→ HTTP 读回 289 条，uid 与身份字段与库内逐条相等；
5. 停 C，起**进程 D**（又一个新进程，pid 不同）→ 再读一遍，仍 289 条、字段不变；
6. 在 D 上开隔离前端（3101）→ 页面读回 289 行，uid 集合与 API 一致。

判据纪律
--------
· 「跨进程」= 写入进程已退出、读回由**新 pid** 完成；不用同进程重连冒充；
· `first_seen_at` 是「首次出现」事实，重放不得改写；`updated_at` 允许变（幂等更新）；
· 只比 `uid + 身份字段 + 四个时刻`，不比 `updated_at`（它不是身份）；
· 每个进程的 pid / 端口 / 库路径都留痕，端口占用用 `lsof` 复核。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isoapi  # noqa: E402
import lib09 as U  # noqa: E402

RUN_DIR = U.PROJECT / "evidence" / "ACCUM-EARLY-08-ANTIGRAVITY" / "runs" / "antigravity_08_arm_b"
REC = U.ISO / "recovery"
REC_DB = REC / "live_signals.sqlite3"
Q = "/api/v1/signals?scope=all&origin=historical_research&limit=1000"

# 身份字段：`live_signal` 列 + `payload_json` 关键项（**不含 updated_at**）
COLS = ("signal_uid", "source", "origin", "exchange", "market", "symbol", "timeframe",
        "strategy_key", "strategy_version", "config_hash", "data_version",
        "structural_kind", "direction", "event_time", "confirmed_at", "available_at",
        "bar_open_time", "bar_close_time", "detection_low", "detection_atr",
        "display_state", "executable_state", "executable_reason", "first_seen_at")
PAY_KEYS = ("uid", "run_id", "scheme", "strategy_id", "rule_version", "config_sha256",
            "config_kind", "approved", "engine_view_sha256", "role", "origin",
            "executable_state")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def snapshot(db: Path) -> dict:
    """按 uid 抓一份身份快照（只读）。"""
    con = U.ro_conn(db)
    try:
        out = {}
        for r in con.execute(
                "SELECT * FROM live_signal WHERE origin='historical_research' "
                "ORDER BY signal_uid"):
            d = dict(r)
            raw_payload = d.get("payload_json") or ""
            try:
                pl = json.loads(raw_payload)
            except ValueError:
                pl = {}
            ts = pl.get("timestamps") or {}
            out[d["signal_uid"]] = {
                "cols": {k: d.get(k) for k in COLS},
                "payload": {k: pl.get(k) for k in PAY_KEYS},
                "ts": {k: ts.get(k) for k in ("event_time_ms", "confirmed_at_ms",
                                              "available_at_ms", "bar_open_time_ms",
                                              "bar_close_time_ms", "available_at_status")},
                "payload_sha256": _sha(raw_payload),
                "updated_at": d.get("updated_at"),
            }
        return out
    finally:
        con.close()


def api_snapshot(items: list) -> dict:
    """把 API 条目折成与库内快照同构的结构（用于逐字段比）。"""
    out = {}
    for it in items:
        r = it.get("research") or {}
        out[it["signal_id"]] = {
            "cols": {
                "origin": it.get("origin"), "symbol": it.get("symbol"),
                "timeframe": it.get("timeframe"), "strategy_key": it.get("strategy_key"),
                "strategy_version": it.get("strategy_version"),
                "config_hash": it.get("config_hash"), "data_version": it.get("data_version"),
                "structural_kind": it.get("structural_kind"),
                "direction": it.get("direction"),
                "executable_state": it.get("executable_state"),
                "executable_reason": it.get("actionable_reason"),
                "display_state": it.get("display_state"),
            },
            "payload": {"run_id": r.get("run_id"), "scheme": r.get("scheme"),
                        "config_sha256": r.get("config_sha256"),
                        "config_kind": r.get("config_kind"), "approved": r.get("approved"),
                        "engine_view_sha256": r.get("engine_view_sha256"),
                        "role": it.get("role"), "origin": it.get("origin"),
                        "executable_state": it.get("executable_state")},
            "ts": {"available_at_status": r.get("available_at_status")},
        }
    return out


def import_once(tag: str) -> dict:
    """跑一次真实导入器（独立子进程，记录它自己的 pid）。"""
    env = U.iso_env({"LIVE_DB": str(REC_DB.resolve()), "LIVE_DIR": str(REC.resolve())})
    t0 = __import__("time").time()
    proc = subprocess.Popen(
        [U.PY, str(U.BACKEND / "research_signal_import.py"),
         "--run-dir", str(RUN_DIR), "--apply", "--require-ok", "--json"],
        cwd=str(U.PROJECT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=300)
    try:
        payload = json.loads(out)
    except ValueError:
        payload = {"raw_stdout_head": (out or "")[:800]}
    return {"tag": tag, "pid": proc.pid, "returncode": proc.returncode,
            "elapsed_s": round(__import__("time").time() - t0, 2),
            "stderr": (err or "")[-1200:], "result": payload}


def main() -> int:
    checks: dict = {}
    REC.mkdir(parents=True, exist_ok=True)
    U.OUT.mkdir(parents=True, exist_ok=True)

    # ── 1. 造「空研究行」的隔离库（只动 isolated/）──────────────────────
    U.backup_db(U.LIVE_DB, REC_DB)
    con = sqlite3.connect(str(REC_DB), timeout=15)
    try:
        src_rows = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        con.execute("DELETE FROM live_signal WHERE origin='historical_research'")
        con.commit()
        left = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    finally:
        con.close()
    checks["恢复库来自生产在线备份（起始总行数 = 生产行数）"] = src_rows == 289
    checks["恢复库起始行数 = 0（清空只发生在 isolated/recovery）"] = left == 0

    # ── 2. 进程 A：首次写入 ─────────────────────────────────────────────
    a = import_once("A_首次写入")
    checks["进程A 退出码 0"] = a["returncode"] == 0
    st_a = (a["result"] or {}).get("stats") or {}
    checks["进程A 新增 289"] = st_a.get("new") == 289
    checks["进程A 更新 0"] = st_a.get("updated") == 0
    checks["进程A 写后研究行 = 289"] = st_a.get("research_rows_after") == 289
    snap_a = snapshot(REC_DB)
    checks["A 之后库内快照 289 条"] = len(snap_a) == 289
    checks["A 的 pid 与本脚本 pid 不同"] = a["pid"] != __import__("os").getpid()

    # ── 3. 进程 B：重放（幂等）──────────────────────────────────────────
    b = import_once("B_重放")
    checks["进程B 退出码 0"] = b["returncode"] == 0
    st_b = (b["result"] or {}).get("stats") or {}
    checks["进程B 新增 0（不重复）"] = st_b.get("new") == 0
    checks["进程B 更新 289（幂等命中）"] = st_b.get("updated") == 289
    checks["进程B 写后研究行仍 289"] = st_b.get("research_rows_after") == 289
    checks["进程B 的 pid 与 A 不同"] = b["pid"] != a["pid"]
    snap_b = snapshot(REC_DB)
    checks["重放后 uid 集合不变"] = set(snap_b) == set(snap_a)
    checks["重放后 first_seen_at 全部不变"] = all(
        snap_b[u]["cols"]["first_seen_at"] == snap_a[u]["cols"]["first_seen_at"] for u in snap_a)
    checks["重放后 payload 逐条不变（289/289 哈希相同）"] = all(
        snap_b[u]["payload_sha256"] == snap_a[u]["payload_sha256"] for u in snap_a)
    checks["重放后身份字段逐条不变"] = all(
        snap_b[u]["cols"] == snap_a[u]["cols"] and snap_b[u]["payload"] == snap_a[u]["payload"]
        and snap_b[u]["ts"] == snap_a[u]["ts"] for u in snap_a)
    checks["updated_at 允许变（不是身份）"] = True

    # ── 4. 进程 C：独立新进程读回 ───────────────────────────────────────
    api_c = isoapi.IsoApi(live_db=REC_DB, paper_dir=REC / "paper", lock_dir=REC / "locks")
    start_c = api_c.start()
    checks["进程C 已就绪"] = bool(start_c.get("ok"))
    if not start_c.get("ok"):
        U.dump("50_跨进程恢复.json", {"A": a, "B": b, "C": start_c,
                                    "判定": U.summarise(checks, "", "读回进程未起来")})
        print(json.dumps(start_c, ensure_ascii=False, indent=2))
        return 1
    checks["进程C 的 LIVE_DB 指向 recovery 库"] = str(
        (start_c.get("ready") or {}).get("LIVE_DB", "")).endswith("isolated/recovery/live_signals.sqlite3")
    checks["进程C 的 pid 与 A/B 都不同"] = start_c["pid"] not in (a["pid"], b["pid"])
    got_c = api_c.get(Q)
    items_c = (got_c.get("json") or {}).get("items") or []
    checks["进程C 读回 289 条"] = len(items_c) == 289
    checks["进程C 读回 has_more=false"] = (got_c.get("json") or {}).get("has_more") is False
    asnap_c = api_snapshot(items_c)
    checks["进程C uid 集合 == 库内 uid 集合"] = set(asnap_c) == set(snap_b)
    diff_c = [u for u in asnap_c if any(
        asnap_c[u]["cols"][k] != snap_b[u]["cols"].get(k) for k in asnap_c[u]["cols"])]
    checks["进程C 身份字段（库列）逐条 == 库内"] = not diff_c
    diff_cp = [u for u in asnap_c if any(
        asnap_c[u]["payload"][k] != snap_b[u]["payload"].get(k) for k in asnap_c[u]["payload"])]
    checks["进程C 身份字段（payload）逐条 == 库内"] = not diff_cp
    diff_ct = [u for u in asnap_c if asnap_c[u]["ts"]["available_at_status"]
               != snap_b[u]["ts"].get("available_at_status")]
    checks["进程C 可得性状态逐条 == 库内"] = not diff_ct

    stop_c = api_c.stop()
    checks["进程C 已退出"] = stop_c.get("returncode") == 0
    checks["C 退出后端口 8899 无监听"] = "LISTEN" not in (api_c.listeners().get("stdout") or "")

    # ── 5. 进程 D：又一个新进程读回（真正的跨进程恢复）──────────────────
    api_d = isoapi.IsoApi(live_db=REC_DB, paper_dir=REC / "paper", lock_dir=REC / "locks")
    start_d = api_d.start()
    checks["进程D 已就绪（新进程）"] = bool(start_d.get("ok"))
    checks["进程D 的 pid 与 C 不同"] = start_d.get("pid") != start_c.get("pid")
    got_d = api_d.get(Q)
    items_d = (got_d.get("json") or {}).get("items") or []
    checks["进程D 读回 289 条"] = len(items_d) == 289
    asnap_d = api_snapshot(items_d)
    checks["进程D 读回的 uid/身份 == 进程C（逐条）"] = json.dumps(
        asnap_d, sort_keys=True, ensure_ascii=False) == json.dumps(
        asnap_c, sort_keys=True, ensure_ascii=False)
    checks["进程D 读回 == 库内快照（逐条）"] = all(
        all(asnap_d[u]["cols"][k] == snap_b[u]["cols"].get(k) for k in asnap_d[u]["cols"])
        for u in asnap_d) and set(asnap_d) == set(snap_b)

    # ── 6. 页面读回（在 D 上开隔离前端）─────────────────────────────────
    web = isoapi.IsoWeb()
    wstart = web.start()
    checks["隔离前端已就绪"] = bool(wstart.get("ok"))
    png = U.OUT / "50_页面_跨进程恢复.png"
    page = {}
    if wstart.get("ok"):
        r = U.run([U.NODE, str(Path(__file__).resolve().parent / "page09.mjs"),
                   wstart["url"], str(png), "--open-research", "--limit-rows=3"],
                  cwd=U.PROJECT, timeout=300)
        for line in (r["stdout"] or "").splitlines():
            if line.startswith("@@PAGE@@"):
                page = json.loads(line[len("@@PAGE@@"):])
                break
    checks["页面研究行 289 条"] = (page.get("research_rows_after_open") or 0) == 289
    page_uids = page.get("research_all_uids") or []
    checks["页面 uid 集合 == 进程D 读回集合"] = set(page_uids) == set(asnap_d)
    checks["页面研究行未混入当前区"] = (page.get("current_area_has_research") or 0) == 0
    wstop = web.stop()
    checks["隔离前端已停"] = bool(wstop.get("stopped"))

    stop_d = api_d.stop()
    checks["进程D 已退出"] = stop_d.get("returncode") == 0
    probe_c = (stop_c.get("probe") or {}).get("probe") or {}
    probe_d = (stop_d.get("probe") or {}).get("probe") or {}
    prod_paths = (str(U.LIVE_DB.resolve()), str(U.PAPER_DIR.resolve()),
                  str(U.MARKET_DB.resolve()))
    checks["进程C 对生产三库写语句 = 0"] = all(
        sum(n for k, n in (probe_c.get("写语句按库") or {}).items() if k.startswith(p)) == 0
        for p in prod_paths)
    checks["进程D 对生产三库写语句 = 0"] = all(
        sum(n for k, n in (probe_d.get("写语句按库") or {}).items() if k.startswith(p)) == 0
        for p in prod_paths)

    rep = {
        "生成时间": U.now_utc(),
        "恢复库": {"path": str(REC_DB), "来源": "生产信号库在线备份 + 删研究行",
                   "起始行数": left},
        "进程A_首次写入": a,
        "进程B_重放": b,
        "进程C_读回": {"启动": start_c, "查询": {"status": got_c.get("status"),
                                            "items": len(items_c),
                                            "has_more": (got_c.get("json") or {}).get("has_more")},
                   "停止": {k: v for k, v in stop_c.items() if k != "probe"},
                   "写探针": probe_c},
        "进程D_读回": {"启动": start_d, "查询": {"status": got_d.get("status"),
                                            "items": len(items_d),
                                            "has_more": (got_d.get("json") or {}).get("has_more")},
                   "停止": {k: v for k, v in stop_d.items() if k != "probe"},
                   "写探针": probe_d},
        "页面": {k: v for k, v in page.items() if k != "research_all_uids"},
        "页面uid数": len(page_uids),
        "逐条差异": {"C_列差异": diff_c[:10], "C_payload差异": diff_cp[:10],
                     "C_可得性差异": diff_ct[:10]},
        "快照摘要": {"条数": len(snap_a),
                     "A后_payload哈希唯一值数": len({v["payload_sha256"] for v in snap_a.values()}),
                     "B后与A后完全一致": all(
                         snap_b[u]["payload_sha256"] == snap_a[u]["payload_sha256"] for u in snap_a)},
        "判定": U.summarise(checks,
                          "跨进程恢复成立：写入进程退出后，新进程可读回同一批记录，uid 与身份不变、重放不重复",
                          "跨进程恢复未通过"),
    }
    p = U.dump("50_跨进程恢复.json", rep)
    U.print_checks("50 跨进程恢复", rep, p)
    return 0 if not rep["判定"]["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
