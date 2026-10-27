# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜06 报告数字对撞（每个数字回原始字段复算）。

历史教训：报告里的数字靠人眼抄，抄错过校验和、抄错过 168/164 的口径。
所以本脚本做两件事，缺一不可：

  ① **复算**：每个数字都从原始来源重新算一遍——
     模块常量（`len(AE.REQUIRED_PATHS)`）、账本表（`paper_position` / `paper_order`）、
     观察库表（`accum05_candidate` / `accum05_exit` / `observe_data_gap`）、
     子进程连接探针日志。**不读 `out/*.json` 里的汇总值当答案**（那是被检查对象）。
  ② **对撞**：把复算值与本单 `README.md` 的「数字表」逐行比对，
     不一致就列出来并以非零码退出。

README 的数字表格式（一行一个数字，第三列是值）：
    | F01 | 必填字段数 | 49 |

首次运行时 README 还没有表，脚本会把复算结果全部打出来并写
`out/06_数字事实.json`，然后以退出码 2 提示「先补 README」。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

EVID = C.EVID
README = EVID / "README.md"
CHAIN_ROOT = C.RUNS / "03_chain"
ISO_ROOT = C.RUNS / "05_isolation"


def chain_dirs() -> dict:
    """作业名 → 运行目录。**从 03 的证据里取真实路径**，不硬编码目录名。

    03 每次跑都落在 `runs/03_chain/<运行号>/<作业名>` 下（不删历史），
    所以这里必须跟着证据里的 `run_dir` 走，否则读到的会是上一轮的结果。
    """
    r = C.load_out("03_全链验证.json") or {}
    return {x["name"]: Path(x["run_dir"]) for x in (r.get("runs") or [])}


def worker_json_path() -> Path:
    """05 的连接探针日志路径，同样从证据里取。"""
    o = C.load_out("05_隔离边界.json") or {}
    p = o.get("worker_json")
    if p and Path(p).exists():
        return Path(p)
    cands = sorted(ISO_ROOT.glob("*/worker_iso.json"), key=lambda x: x.stat().st_mtime)
    if not cands:
        raise SystemExit("找不到 05 的连接探针日志，请先跑 05_隔离边界.py")
    return cands[-1]


def _ro(p: Path):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return None if r is None else r[0]


def _positions(run_dir: Path, scheme: str) -> dict:
    p = Path(run_dir) / "paper" / f"paper_{scheme}.sqlite3"
    if not p.exists():
        return {"positions": 0, "closed": 0, "orders": 0, "fills": 0}
    con = _ro(p)
    try:
        return {
            "positions": _one(con, "SELECT COUNT(*) FROM paper_position") or 0,
            "closed": _one(con, "SELECT COUNT(*) FROM paper_position WHERE status='closed'") or 0,
            "orders": _one(con, "SELECT COUNT(*) FROM paper_order") or 0,
            "fills": _one(con, "SELECT COUNT(*) FROM paper_fill") or 0,
        }
    finally:
        con.close()


def _observe(run_dir: Path) -> dict:
    p = Path(run_dir) / "observe.sqlite3"
    if not p.exists():
        return {}
    con = _ro(p)
    try:
        return {
            "candidates": _one(con, "SELECT COUNT(*) FROM accum05_candidate") or 0,
            "exits": _one(con, "SELECT COUNT(*) FROM accum05_exit") or 0,
            "gaps": _one(con, "SELECT COUNT(*) FROM observe_data_gap") or 0,
            "transitions": _one(con, "SELECT COUNT(*) FROM observe_transition") or 0,
            "attempts": _one(con, "SELECT COUNT(*) FROM accum05_open_attempt") or 0,
        }
    finally:
        con.close()


def facts() -> dict:
    import accum_early_entry as AE

    f = {}
    # ── 结构常量 ──────────────────────────────────────────────────────────
    f["F01_必填字段数"] = len(AE.REQUIRED_PATHS)
    f["F02_待决规则项数"] = len(AE.PENDING_TO_CONFIG)
    f["F03_已实现取值字段数"] = len(AE.IMPLEMENTED)
    f["F04_已确定语义项数"] = len(AE.check_fixed_rules()["rows"])

    # ── 配置闸门（读 04 的原始用例表，不读 summary）────────────────────────
    g = C.load_out("04_配置闸门.json") or {}
    cases = g.get("cases") or []
    f["F05_闸门用例数"] = len(cases)
    f["F06_闸门通过用例数"] = sum(1 for c in cases if c.get("ok"))
    f["F07_负向用例数"] = sum(1 for c in cases if str(c.get("case", "")).startswith("N"))
    f["F08_正向用例数"] = sum(1 for c in cases if str(c.get("case", "")).startswith("P"))
    op = g.get("open_probe") or {}
    f["F09_阻塞时调用引擎次数"] = (op.get("blocked") or {}).get("paper_calls")
    f["F10_放行时调用引擎次数"] = (op.get("allowed") or {}).get("paper_calls")

    # ── 链路（账本原始表）──────────────────────────────────────────────────
    dirs = chain_dirs()
    for need in ("run_cold", "run_hot", "run_legacy"):
        if need not in dirs:
            raise SystemExit(f"03 的证据里没有 {need}，请先跑 03_全链验证.py")
    cold = {s: _positions(dirs["run_cold"], s) for s in ("4a", "4b", "4c")}
    hot = {s: _positions(dirs["run_hot"], s) for s in ("4a", "4b", "4c")}
    leg = {s: _positions(dirs["run_legacy"], s) for s in ("4a", "4b", "4c")}
    f["F11_冷窗口持仓笔数"] = sum(v["positions"] for v in cold.values())
    f["F12_冷窗口平仓笔数"] = sum(v["closed"] for v in cold.values())
    f["F13_冷窗口委托笔数"] = sum(v["orders"] for v in cold.values())
    f["F14_冷窗口成交笔数"] = sum(v["fills"] for v in cold.values())
    f["F15_热窗口持仓笔数"] = sum(v["positions"] for v in hot.values())
    f["F16_热窗口平仓笔数"] = sum(v["closed"] for v in hot.values())
    f["F17_对照臂持仓笔数"] = sum(v["positions"] for v in leg.values())
    f["F18_对照臂平仓笔数"] = sum(v["closed"] for v in leg.values())

    oc = _observe(dirs["run_cold"])
    oh = _observe(dirs["run_hot"])
    ol = _observe(dirs["run_legacy"])
    f["F19_冷窗口候选数"] = oc.get("candidates")
    f["F20_冷窗口退出记录数"] = oc.get("exits")
    f["F21_冷窗口数据缺口数"] = oc.get("gaps")
    f["F22_热窗口候选数"] = oh.get("candidates")
    f["F23_热窗口数据缺口数"] = oh.get("gaps")
    f["F24_对照臂退出记录数"] = ol.get("exits")

    # 冷窗口退出明细（逐字段回原始库）
    p = dirs["run_cold"] / "observe.sqlite3"
    con = _ro(p)
    try:
        row = con.execute(
            "SELECT exit_ts, exit_class, stop_used, fill_price, trail_updates, "
            "trail_effective_mode, exit_rules FROM accum05_exit ORDER BY id LIMIT 1").fetchone()
        det = con.execute(
            "SELECT detail_json FROM accum05_exit ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        con.close()
    d = json.loads(det or "{}")
    ups = d.get("updates") or []
    M15 = C.M15
    f["F25_冷窗口首笔退出时刻"] = row[0]
    f["F26_冷窗口首笔退出止损价"] = row[2]
    f["F27_冷窗口首笔退出成交价"] = row[3]
    f["F28_冷窗口首笔跟踪上移次数"] = row[4]
    f["F29_冷窗口首笔生效模式"] = row[5]
    f["F30_冷窗口首笔退出规则版本"] = row[6]
    f["F31_生效时刻符合公式条数"] = sum(
        1 for u in ups if int(u["effective_at"]) == (int(u["bar4h_close_ms"]) // M15 + 1) * M15)
    f["F32_上移明细总条数"] = len(ups)

    # 对照臂：生效时刻 == 4h 收盘（旧语义）的条数
    p2 = dirs["run_legacy"] / "observe.sqlite3"
    con = _ro(p2)
    try:
        det2 = con.execute(
            "SELECT detail_json FROM accum05_exit ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        con.close()
    ups2 = (json.loads(det2 or "{}").get("updates") or [])
    f["F33_对照臂旧语义条数"] = sum(
        1 for u in ups2 if int(u["effective_at"]) == int(u["bar4h_close_ms"]))
    f["F34_对照臂上移明细条数"] = len(ups2)

    # ── 重启 ──────────────────────────────────────────────────────────────
    r = C.load_out("03_全链验证.json") or {}
    res = r.get("resume") or {}
    f["F35_重启前委托数"] = res.get("orders_before")
    f["F36_重启后委托数"] = res.get("orders_after")
    f["F37_重启前成交数"] = res.get("fills_before")
    f["F38_重启后成交数"] = res.get("fills_after")

    # ── 隔离（读子进程探针原始日志）────────────────────────────────────────
    w = json.loads(worker_json_path().read_text(encoding="utf-8"))
    log = (w.get("conn") or {}).get("log") or []
    f["F39_隔离进程连接次数"] = len(log)
    f["F40_生产行情库只读连接次数"] = sum(
        1 for e in log if "market_v2.sqlite3" in e["db"] and e["readonly"])
    f["F41_生产账本可写连接次数"] = sum(
        1 for e in log if "/数据/paper/" in e["db"] and not e["readonly"])
    f["F42_隔离库可写连接次数"] = sum(
        1 for e in log if "/05_isolation/" in e["db"] and not e["readonly"])

    iso = C.load_out("05_隔离边界.json") or {}
    f["F43_闸门越界拒绝"] = (iso.get("x2_gate_boundary") or {}).get("negative", {}).get("raised")
    f["F44_生产四本账本未变"] = all(
        ((iso.get("x5_prod_fingerprint") or {}).get("ledgers_unchanged") or {}).values()) \
        if iso.get("x5_prod_fingerprint") else None
    f["F45_生产目录研究账本数"] = (
        (iso.get("x6_ledger_location") or {}).get("production_dir_research_ledgers") or {}).get("count")

    # ── 经济口径三维度独立阻塞（07 号）────────────────────────────────────
    e7 = C.load_out("07_经济口径一致性校验.json") or {}
    res7 = e7.get("results") or {}
    f["F48_经济口径用例数"] = len(res7)
    f["F49_经济口径通过用例数"] = sum(1 for v in res7.values() if v.get("pass"))

    # ── 退出差异核查（01 号）──────────────────────────────────────────────
    e1 = C.load_out("01_退出差异影响.json") or {}
    f["F46_退出差异核查样本数"] = len(e1.get("samples") or [])
    f["F47_两臂退出时刻差异笔数"] = sum(
        1 for s in (e1.get("samples") or []) if (s.get("exit_ts_diff") or 0) != 0)
    return f


def parse_readme_numbers(text: str) -> dict:
    """从 README 的「数字表」里取 `| Fxx | 说明 | 值 |`。"""
    out = {}
    for m in re.finditer(r"^\|\s*(F\d{2})\s*\|[^|]*\|\s*([^|]*?)\s*\|", text, re.M):
        key, val = m.group(1), m.group(2).strip()
        out[key] = val
    return out


def _norm(v) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def main() -> int:
    f = facts()
    C.dump("06_数字事实.json", {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
                                "facts": f})
    if not README.exists():
        print(json.dumps({"readme": "缺失，先写 README 的数字表",
                          "facts": f}, ensure_ascii=False, indent=1))
        return 2

    declared = parse_readme_numbers(README.read_text(encoding="utf-8"))
    rows, bad = [], []
    for k, v in f.items():
        key = k.split("_", 1)[0]
        want = _norm(v)
        got = declared.get(key)
        row = {"id": key, "name": k.split("_", 1)[1], "recomputed": want,
               "readme": got, "ok": (got == want)}
        rows.append(row)
        if not row["ok"]:
            bad.append(row)
    missing_in_readme = [k.split("_", 1)[0] for k in f if k.split("_", 1)[0] not in declared]
    extra_in_readme = sorted(set(declared) - {k.split("_", 1)[0] for k in f})

    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "why": "报告数字不手抄：每个数字回原始表/模块常量复算，再与 README 数字表双向对撞",
           "readme": str(README),
           "rows": rows,
           "mismatch": bad,
           "missing_in_readme": missing_in_readme,
           "extra_in_readme": extra_in_readme,
           "summary": {"facts_n": len(f), "matched": len(rows) - len(bad),
                       "mismatch_n": len(bad),
                       "missing_n": len(missing_in_readme),
                       "extra_n": len(extra_in_readme),
                       "all_ok": (not bad and not missing_in_readme and not extra_in_readme)}}
    C.dump("06_报告对撞.json", out)
    print(json.dumps({"summary": out["summary"],
                      "mismatch": bad[:10],
                      "missing_in_readme": missing_in_readme,
                      "extra_in_readme": extra_in_readme}, ensure_ascii=False, indent=1))
    return 0 if out["summary"]["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
