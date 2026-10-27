# -*- coding: utf-8 -*-
"""ACCUM-EARLY-08-WORK/R1｜反重力隔离回测「研究信号」接入。

职责边界（**只做一件事**）
--------------------------
把 `evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/<run_id>/raw.json` 里的候选信号，
按**既有** `live_signal` 契约写成 `origin='historical_research'` +
`executable_state='historical_not_executable'` 的研究信号行。

- **不读行情库、不写行情库**（本模块完全不 import market 相关模块）。
- **不写任何交易账本**（不 import `paper_engine`；不调用 `open_position`）。
- **不改任何既有文件**（新增模块；`live_store` 只用作连接与 schema 的唯一来源）。
- **不新造来源取值**：`historical_research` / `historical_not_executable` 都是
  `live_signal_engine.import_historical_research()` 已在用的既有取值。

为什么落 `historical_research` 而不是 `replay`
---------------------------------------------
`replay` 会被 `unified_runtime.run_replay()` 以 `route_pending_orders(origin="replay")`
扫进路由（真去开纸面仓）；`historical_research` **没有任何生产调用方**会把它送进路由
（生产只有 `origin="live"` / `"recovered"`）。选它 = 从路由层面就不存在误开仓路径，
比「靠闸门拦住」更硬。

幂等语义
--------
`signal_uid` 直接取反重力产物里的确定性键（`accum05_<scheme>_<symbol>_<confirm_ms>`），
不重写、不加前缀。冲突时**只更新** `payload_json` / `executable_reason` / `updated_at`；
`first_seen_at`、四个时刻、价格事实**永不改写**（与 `persist_candidates` 同纪律）。

用法
----
    python research_signal_import.py --run-dir <...>/runs/antigravity_08_arm_b
    python research_signal_import.py --run-dir ... --apply        # 真写（默认 dry-run）
    python research_signal_import.py --run-dir ... --apply --json

`LIVE_DB` 环境变量决定写哪本库（缺省 = 生产 `框架/backend/live/live_signals.sqlite3`）。
隔离演练请显式设置 `LIVE_DB=<隔离副本>`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1] if len(HERE.parents) >= 2 else HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_store as L  # noqa: E402

MANIFEST_VERSION = "accum08-research-signal/v1"

# 来源标签：**既有取值**（`live_signal_engine.py:23` 的语义），不新造。
ORIGIN_RESEARCH = "historical_research"
# 可执行态：**既有取值**（`live_api.py:173-183` 的六态映射里有它），恒不可执行。
EXEC_NOT_EXECUTABLE = "historical_not_executable"
# 来源系统（`live_signal.source` 是 TEXT NOT NULL，用可识别的系统名，不用 `live`）
SOURCE = "antigravity_08_backtest"
# 事件角色：当前交易信号区判据是 `enterable_arm_entry`；研究行**显式**用另一个值，
# 使「即使来源/可执行态被人改错」也进不了当前区（三重隔离的第三重）。
ROLE_RESEARCH = "research_only"

# 确认 bar 长度（1h）。与 `live_signal_engine.py:232` 的 `close_ms - H + 1` 同一口径。
CONFIRM_BAR_MS = 3_600_000

EXEC_REASON = ("approved=false｜研究/未批准配置（反重力 fixture 配置，G01–G05 全 pending）"
               "⇒ 只展示、不可执行、不进入当前交易区")

# 反重力 README §三 的 17 项数据通道，按「是否进入决策链」折叠成两个列表。
# 只在**运行级** manifest 里写一份（每条信号共用），不重复进 payload。
INPUT_COVERAGE = {
    "participating": [
        "4h K线（箱体/阻力/吸收阳线/4h 跟踪止损）",
        "1h K线（Spring/Breakout/Retest 状态机）",
        "15m K线（撮合价/盘中止损/跳空判定）",
        "合约交易规范（手数/步进/最小名义）",
        "前15m成交额（1% 参与率上限）",
        "资金费结算（8h，缺口段由 06-06 补齐）",
    ],
    "not_participating": [
        "持仓量 OI（未订阅）",
        "多空持仓比（未读取）",
        "CVD 主动买卖量（只读 volume，未读 taker_buy_volume）",
        "聪明钱/大户持仓（无历史时序，不可补）",
        "实时深度盘口（回测不适用，用滑点替代）",
        "流通市值 / 板块字典 / OI-市值比 / 5m·30m·8h·12h·1d K线（未参与）",
    ],
    "source": "evidence/ACCUM-EARLY-08-ANTIGRAVITY/README.md §三（17 项逐项审计）",
}

GAPS = [
    "available_at 无证据：反重力产物未记录 OI/交易所发布延迟 ⇒ 标 unproven，不用 confirmed_at 代替",
    "data_version 未提供：反重力只给窗口，未给数据内容哈希 ⇒ 落 null，不编造",
    "signal_uid 不含 run_id：两臂候选 uid 完全相同 ⇒ 只导入一个臂（arm_b, run_status=OK）",
    "config 为 fixture（approved=false, kind=fixture）：只能作研究，不得当批准配置",
    "G01–G05 全 pending：正式回测门禁未过",
]


# ─────────────────────────────────────────────────────────────────────────────
# 读取反重力产物
# ─────────────────────────────────────────────────────────────────────────────
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_run(run_dir: Path) -> Dict[str, Any]:
    """读一个 run 目录：raw.json（候选）+ 运行身份 json（代码哈希/配置哈希）。

    运行身份文件按 `out_arm_*/运行身份_<run_id>.json` 或 `run_dir/run_status.json` 兜底。
    缺任何一项都**不猜**：缺失项如实记 null 并进 `gaps`。
    """
    run_dir = Path(run_dir).resolve()
    raw_p = run_dir / "raw.json"
    if not raw_p.exists():
        raise FileNotFoundError(f"raw.json 不存在：{raw_p}")
    raw = json.loads(raw_p.read_text(encoding="utf-8"))
    run_id = raw.get("run_id") or json.loads(
        (run_dir / "run_status.json").read_text(encoding="utf-8")).get("run_id")

    ident = None
    for cand in (run_dir.parents[1] / "out_arm_a" / f"运行身份_{run_id}.json",
                 run_dir.parents[1] / "out_arm_b" / f"运行身份_{run_id}.json",
                 run_dir / f"运行身份_{run_id}.json"):
        if cand.exists():
            ident = json.loads(cand.read_text(encoding="utf-8"))
            break

    status_p = run_dir / "run_status.json"
    run_status = (json.loads(status_p.read_text(encoding="utf-8"))
                  if status_p.exists() else {"status": raw.get("run_status")})
    return {"run_dir": run_dir, "raw": raw, "raw_sha256": _sha256(raw_p),
            "identity": ident, "run_status": run_status, "run_id": run_id}


def iter_candidates(raw: Dict[str, Any]):
    """逐条产出 (symbol, candidate)。不复制、不改写原始字段。"""
    for s in raw.get("per_symbol") or []:
        sym = s.get("symbol")
        for c in s.get("candidates") or []:
            yield sym, c


# ─────────────────────────────────────────────────────────────────────────────
# 组装行
# ─────────────────────────────────────────────────────────────────────────────
def build_rows(loaded: Dict[str, Any], *, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    raw, ident = loaded["raw"], loaded["identity"] or {}
    cfg = raw.get("config") or {}
    eng = cfg.get("engine") or {}
    entry_ident = ident.get("entry_identity") or {}
    schemes = entry_ident.get("schemes") or {}
    window = raw.get("window") or {}
    code_hashes = ident.get("code_before") or {}
    code_drift = ident.get("code_drift")

    now = int(now_ms or time.time() * 1000)
    rows: List[Dict[str, Any]] = []

    for sym, c in iter_candidates(raw):
        scheme = c.get("scheme")
        sch = schemes.get(scheme) or {}
        uid = c.get("signal_uid")
        confirm_ms = c.get("confirm_close_ms")
        entry_open_ms = c.get("entry_bar_open_ms")
        if not uid or confirm_ms is None:
            # 缺确定性键或确认时刻 ⇒ 这条不导入（不编造 uid）
            continue
        # 确认 bar 开盘：沿用 live_signal_engine.py:232 的算法（close - H + 1），
        # 使 `bar_open_time` 列语义与既有链路逐字一致（不得填 15m 入场 bar 开盘）。
        confirm_open_ms = int(confirm_ms) - CONFIRM_BAR_MS + 1

        payload = {
            "manifest_version": MANIFEST_VERSION,
            # ── 身份（与反重力共用字段）────────────────────────────────────
            "uid": uid,
            "run_id": loaded["run_id"],
            "scheme": scheme,
            "strategy_id": sch.get("strategy_id"),
            "rule_version": sch.get("rule_version"),
            "strategy_label": sch.get("label"),
            "entry_version": entry_ident.get("entry_version"),
            "exit_rules": ident.get("exit_rules") or (raw.get("identity") or {}).get("exit_rules"),
            "config_sha256": cfg.get("config_sha256") or (ident.get("config") or {}).get("sha256"),
            "config_path": (ident.get("config") or {}).get("path"),
            "config_kind": cfg.get("kind"),
            "config_approved": bool(cfg.get("approved")),
            "config_approval_ref": cfg.get("approval_ref"),
            "engine_view_sha256": eng.get("view_sha256"),
            "engine_strategy_version": eng.get("strategy_version"),
            "code_hashes": code_hashes,
            "code_drift": code_drift,
            # ── 批准状态（当前只能 false）─────────────────────────────────
            "approved": False,
            "approval_blockers": ["G01–G05 全 pending（run_frozen=false）",
                                  "config approved=false / kind=fixture"],
            # ── 来源与角色（三重隔离）─────────────────────────────────────
            "source": SOURCE,
            "origin": ORIGIN_RESEARCH,
            "origin_semantics": "研究导入：只展示、恒不可执行、不进入当前交易区",
            "role": ROLE_RESEARCH,
            "arm": scheme,
            "executable_state": EXEC_NOT_EXECUTABLE,
            # ── 四个时刻（互不替代）───────────────────────────────────────
            "timestamps": {
                "event_time_ms": confirm_ms,
                "confirmed_at_ms": confirm_ms,
                "available_at_ms": None,
                "bar_open_time_ms": confirm_open_ms,
                "bar_close_time_ms": confirm_ms,
                "available_at_status": "unproven",
                "available_at_reason": ("反重力产物未提供 OI/交易所发布延迟证据；"
                                        "不得用 confirmed_at 代替可得时刻"),
                # 列语义沿用 live_signal_engine.py:232：bar_open_time = 确认 bar 开盘
                "bar_open_time_meaning": "1h 确认 bar 开盘（列语义与既有链路一致）",
                "bar_close_time_meaning": "1h 确认 bar 收盘（事件确认时刻）",
                # 执行最早时点单列一个字段，**不占用** bar_open_time（否则 open>close 语义倒挂）
                "entry_bar_open_ms": entry_open_ms,
                "entry_bar_open_meaning": "15m 入场 bar 开盘（回测撮合最早可执行时点，非信号 bar 开盘）",
            },
            # ── 数据版本与窗口 ───────────────────────────────────────────
            "data_version": None,
            "data_version_note": ("反重力未提供数据内容哈希；仅给窗口 "
                                  f"{window.get('start_utc')}→{window.get('end_utc')}"),
            "window": window,
            "pool_n_symbols": raw.get("n_symbols"),
            "flags": raw.get("flags"),
            # ── 缺口 ────────────────────────────────────────────────────
            "gaps": GAPS,
            # ── 原始候选（原样保留，便于逐条对账）────────────────────────
            "raw_candidate": c,
            "source_file": str(loaded["run_dir"] / "raw.json"),
            "source_file_sha256": loaded["raw_sha256"],
        }

        rows.append({
            "signal_uid": uid,
            "source": SOURCE,
            "origin": ORIGIN_RESEARCH,
            "exchange": "binance",
            "market": "um",
            "symbol": sym,
            "timeframe": "1h",
            "strategy_key": sch.get("strategy_id"),
            "strategy_version": sch.get("rule_version"),
            "config_hash": payload["config_sha256"],
            "data_version": None,
            "structural_kind": "accum_early_entry",
            "direction": "long",
            "event_time": int(confirm_ms),
            "confirmed_at": int(confirm_ms),
            # **不替代**：无证据就留空，让 API/页面如实显示「不可得」
            "available_at": None,
            "bar_open_time": int(confirm_open_ms),
            "bar_close_time": int(confirm_ms),
            "detection_low": None,
            "detection_atr": None,
            "display_state": "displayable",
            "executable_state": EXEC_NOT_EXECUTABLE,
            "executable_reason": EXEC_REASON,
            "admission": None,
            "rank": None,
            "daily_list_version": None,
            "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
            "first_seen_at": now,
            "updated_at": now,
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 写入（幂等）
# ─────────────────────────────────────────────────────────────────────────────
_INSERT_SQL = (
    "INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
    "strategy_key,strategy_version,config_hash,data_version,structural_kind,direction,"
    "event_time,confirmed_at,available_at,bar_open_time,bar_close_time,detection_low,"
    "detection_atr,display_state,executable_state,executable_reason,admission,rank,"
    "daily_list_version,payload_json,first_seen_at,updated_at) "
    "VALUES(" + ",".join("?" * 29) + ") "
    # 冲突时**只**补 payload / 原因 / 更新时间：事实与首次出现时刻永不改写
    "ON CONFLICT(signal_uid) DO UPDATE SET "
    "payload_json=excluded.payload_json, "
    "executable_reason=excluded.executable_reason, "
    "executable_state=excluded.executable_state, "
    "updated_at=excluded.updated_at"
)

_ORDER = ("signal_uid", "source", "origin", "exchange", "market", "symbol", "timeframe",
          "strategy_key", "strategy_version", "config_hash", "data_version",
          "structural_kind", "direction", "event_time", "confirmed_at", "available_at",
          "bar_open_time", "bar_close_time", "detection_low", "detection_atr",
          "display_state", "executable_state", "executable_reason", "admission", "rank",
          "daily_list_version", "payload_json", "first_seen_at", "updated_at")


def import_rows(rows: List[Dict[str, Any]], *, apply: bool = False) -> Dict[str, Any]:
    """写入 `live_signal`（`LIVE_DB` 决定目标库）。`apply=False` 时只做预检不写。"""
    db = Path(L.LIVE_DB)
    stats: Dict[str, Any] = {"target_db": str(db), "rows_in": len(rows), "apply": apply}

    if not db.exists():
        stats["error"] = "目标信号库不存在（不自动创建，避免凭空造库）"
        return stats

    con = sqlite3.connect(str(db), timeout=15.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=15000")
        before = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        stats["rows_before"] = before

        # 预检：哪些 uid 已存在（用于区分 new/updated，也用于 dry-run 报告）
        existing = {r[0] for r in con.execute(
            "SELECT signal_uid FROM live_signal WHERE origin=?", (ORIGIN_RESEARCH,))}
        stats["existing_research_rows"] = len(existing)
        stats["would_new"] = sum(1 for r in rows if r["signal_uid"] not in existing)
        stats["would_update"] = sum(1 for r in rows if r["signal_uid"] in existing)

        if not apply:
            stats["rows_after"] = before
            return stats

        new_n = upd_n = 0
        for r in rows:
            vals = tuple(r[k] for k in _ORDER)
            had = con.execute("SELECT 1 FROM live_signal WHERE signal_uid=?",
                              (r["signal_uid"],)).fetchone()
            con.execute(_INSERT_SQL, vals)
            if had:
                upd_n += 1
            else:
                new_n += 1
        con.commit()
        stats["new"] = new_n
        stats["updated"] = upd_n
        stats["rows_after"] = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        stats["research_rows_after"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin=?", (ORIGIN_RESEARCH,)).fetchone()[0]
        stats["by_scheme"] = {
            row["strategy_key"]: row["n"] for row in con.execute(
                "SELECT strategy_key, COUNT(*) n FROM live_signal WHERE origin=? "
                "GROUP BY strategy_key ORDER BY strategy_key", (ORIGIN_RESEARCH,))}
        stats["distinct_executable_state"] = [r[0] for r in con.execute(
            "SELECT DISTINCT executable_state FROM live_signal WHERE origin=?",
            (ORIGIN_RESEARCH,))]
        stats["current_area_rows"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered') "
            "AND executable_state='enterable'").fetchone()[0]
    finally:
        con.close()
    return stats


def run_manifest(loaded: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """运行级 manifest（与反重力共用字段的一份，供双方对账）。"""
    raw, ident = loaded["raw"], loaded["identity"] or {}
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": loaded["run_id"],
        "run_status": loaded["run_status"].get("status"),
        "raw_json_sha256": loaded["raw_sha256"],
        "config": {
            "path": (ident.get("config") or {}).get("path"),
            "sha256": (raw.get("config") or {}).get("config_sha256")
            or (ident.get("config") or {}).get("sha256"),
            "kind": (raw.get("config") or {}).get("kind"),
            "approved": (raw.get("config") or {}).get("approved"),
        },
        "engine": {"view_sha256": (raw.get("config") or {}).get("engine", {}).get("view_sha256"),
                   "strategy_version": (raw.get("config") or {}).get("engine", {}).get("strategy_version")},
        "code_hashes": ident.get("code_before"),
        "code_drift": ident.get("code_drift"),
        "approved": False,
        "origin": ORIGIN_RESEARCH,
        "executable_state": EXEC_NOT_EXECUTABLE,
        "window": raw.get("window"),
        "n_symbols": raw.get("n_symbols"),
        "candidates_by_scheme": raw.get("candidates"),
        "rows_built": len(rows),
        "input_coverage": INPUT_COVERAGE,
        "gaps": GAPS,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="反重力研究信号接入（默认 dry-run）")
    ap.add_argument("--run-dir", required=True, help="runs/<run_id> 目录")
    ap.add_argument("--apply", action="store_true", help="真写（默认只预检）")
    ap.add_argument("--require-ok", action="store_true",
                    help="run_status 不是 OK 就拒绝（推荐；FAILED 运行不得当信号源）")
    ap.add_argument("--manifest-out", default=None, help="运行级 manifest 落盘路径")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    a = ap.parse_args(argv)

    loaded = load_run(Path(a.run_dir))
    st = loaded["run_status"].get("status")
    if a.require_ok and st != "OK":
        out = {"refused": True, "run_id": loaded["run_id"], "run_status": st,
               "reason": "run_status 不是 OK，拒绝作为信号源"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    rows = build_rows(loaded)
    stats = import_rows(rows, apply=a.apply)
    manifest = run_manifest(loaded, rows)

    if a.manifest_out:
        Path(a.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.manifest_out).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    out = {"run_id": loaded["run_id"], "run_status": st, "rows_built": len(rows),
           "stats": stats, "manifest_head": {k: manifest[k] for k in
                                             ("manifest_version", "run_id", "approved",
                                              "origin", "executable_state", "rows_built")}}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"run_id={out['run_id']} status={st} rows={len(rows)} apply={a.apply}")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
