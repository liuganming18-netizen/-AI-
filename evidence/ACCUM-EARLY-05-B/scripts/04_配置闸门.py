# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜04 配置闸门阻塞行为实测。

02 号脚本只证明「fixture 能过闸门」；本脚本反过来证明**闸门真能拦住**——
每一条阻塞都必须给出一条负向用例，且负向用例必须配一条正向对照，
否则「拦住」可能只是恒真（历史上踩过 `write_gate` 判据恒真的坑）。

九条负向 + 两条正向，全部只看 `load_config()` 的返回值，不看注释、不看自报：

  N1 配置不存在               → config_file_missing（missing == 49）
  N2 空白表单（49 项全空）    → config_fields_missing（missing == 48）
  N3 只填一半                 → config_fields_missing（missing == 25）
  N4 格式版本不对             → schema_version_mismatch
  N5 批准配置但未批准         → not_approved
  N6 fixture 但没开显式开关   → fixture_without_explicit_env
  N7 取值不在已实现集合       → config_value_unsupported
  N8 经济口径声明策略包 3bps  → economic_caliber_declared_mismatch
  N9 引擎已确定语义被改       → fixed_rules_mismatch

  P1 fixture + 显式开关       → open_allowed / fixture_chain_selfcheck
  P2 批准配置 + approved=true → open_allowed / approved_rules

另外三件事：
  · `try_open` 在阻塞配置下**绝不**调 `paper_engine.open_position`（计数器 == 0）；
  · `try_open` 在放行配置下**确实**调了（计数器 == 1）—— 这是上一条的正向对照；
  · 账本目录指向生产时，即使配置放行也必须 `isolation_violation`（计数器仍 0）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

GATE = C.RUNS / "04_gate"
CFG = GATE / "cfg"


# ── 先把隔离环境摆好，再 import 生产模块（账本目录在 import 时就被读）────────
def _set_env(ledger_dir: Path, *, fixture_ok: bool) -> None:
    ledger_dir = Path(ledger_dir)
    (ledger_dir).mkdir(parents=True, exist_ok=True)
    for k in ("ACCUM04_CONFIG", "ACCUM04_FIXTURE_OK"):
        os.environ.pop(k, None)
    os.environ.update({
        "PAPER_LEDGER_DIR": str(ledger_dir),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(GATE / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(GATE / "gate.json"),
        "MARKET_DB": str(C.PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
        "WX_ARCHIVE_DIR": str(C.PROJECT / "数据" / "市场数据" / "parquet"),
        "WX_MARKET_MERGE": "1",
    })
    if fixture_ok:
        os.environ["ACCUM05_FIXTURE_OK"] = "1"
    else:
        os.environ.pop("ACCUM05_FIXTURE_OK", None)


_set_env(GATE / "paper", fixture_ok=True)
C.write_gate(GATE)

import accum_early_entry as AE                                  # noqa: E402
import paper_engine as PE                                       # noqa: E402


def _drop(doc: dict, path: str) -> None:
    """按点路径删掉一个叶子（父节点空了就一起删）。"""
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    cur.pop(parts[-1], None)


def _set(doc: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _write(name: str, doc: dict) -> Path:
    CFG.mkdir(parents=True, exist_ok=True)
    p = CFG / name
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _case(tag: str, why: str, path: Path | None, *, fixture_ok: bool,
          expect_block: str | None, expect_allowed: bool,
          expect_scope: str | None = None, extra: dict | None = None) -> dict:
    """跑一次 load_config，并断言它真的落在期望的档位上。"""
    _set_env(GATE / "paper", fixture_ok=fixture_ok)
    cfg = AE.load_config(str(path) if path else str(CFG / "__不存在__.json"))
    blocked = list(cfg.get("blocked_by") or [])
    row = {
        "case": tag, "why": why,
        "config_path": str(path) if path else None,
        "fixture_env": fixture_ok,
        "present": cfg.get("present"), "parse_ok": cfg.get("parse_ok"),
        "ready": cfg.get("ready"), "open_allowed": cfg.get("open_allowed"),
        "open_scope": cfg.get("open_scope"),
        "blocked_by": blocked,
        "missing_n": len(cfg.get("missing") or []),
        "unsupported_n": len(cfg.get("unsupported") or []),
        "declared_mismatch": (cfg.get("economic_caliber") or {}).get("declared_mismatch"),
        "fixed_rules_ok": (cfg.get("fixed_rules") or {}).get("ok"),
        "reason": cfg.get("reason"),
        "expect_block": expect_block, "expect_allowed": expect_allowed,
    }
    if extra:
        row.update(extra)
    # 判定：① 开关状态对得上；② 若要求阻塞，阻塞原因必须**真的出现在** blocked_by 里
    row["ok_allowed"] = (cfg.get("open_allowed") is expect_allowed)
    row["ok_block"] = (expect_block is None) or (expect_block in blocked)
    row["ok_scope"] = (expect_scope is None) or (cfg.get("open_scope") == expect_scope)
    row["ok"] = row["ok_allowed"] and row["ok_block"] and row["ok_scope"]
    return row


def main() -> int:
    # 观察库每次重开：`accum05_open_attempt` 是追加表，复用旧库会让「本次尝试记录」
    # 混进历史行。只删这 3 个文件（远低于环境安全删除守卫的阈值），不删目录。
    for n in ("observe.sqlite3", "observe.sqlite3-wal", "observe.sqlite3-shm"):
        f = GATE / n
        if f.exists():
            f.unlink()
    base = json.loads(C.FIXTURE.read_text(encoding="utf-8"))
    blank = json.loads(C.BLANK.read_text(encoding="utf-8"))
    cases = []

    # ── N1 配置不存在 ─────────────────────────────────────────────────────
    cases.append(_case("N1_配置不存在", "没有配置就必须阻塞，且不许用默认值补齐",
                       None, fixture_ok=True, expect_block="config_file_missing",
                       expect_allowed=False,
                       extra={"note": "本模块不提供任何默认值"}))

    # ── N2 空白表单 ───────────────────────────────────────────────────────
    cases.append(_case("N2_空白表单", "用户尚未填写任何批准值",
                       _write("n2_空白.json", blank), fixture_ok=True,
                       expect_block="config_fields_missing", expect_allowed=False,
                       extra={"note": "只有 schema_version 被填，其余 48 项全空"}))

    # ── N3 只填一半 ───────────────────────────────────────────────────────
    half = copy.deepcopy(base)
    for p in AE.REQUIRED_PATHS[24:]:
        _drop(half, p)
    cases.append(_case("N3_只填一半", "缺项不能被静默当成默认值",
                       _write("n3_半填.json", half), fixture_ok=True,
                       expect_block="config_fields_missing", expect_allowed=False,
                       extra={"note": "删掉后 25 项必填"}))

    # ── N4 格式版本不对 ───────────────────────────────────────────────────
    d4 = copy.deepcopy(base)
    d4["schema_version"] = "accum-early-04/v1"
    cases.append(_case("N4_格式版本错", "04 版字段语义与本版不同，不猜着迁移",
                       _write("n4_版本.json", d4), fixture_ok=True,
                       expect_block="schema_version_mismatch", expect_allowed=False))

    # ── N5 批准配置但 approved=false ──────────────────────────────────────
    d5 = copy.deepcopy(base)
    d5["kind"] = "approved"
    d5["approved"] = False
    cases.append(_case("N5_未批准", "kind=approved 但 approved=false ⇒ 仍不许开仓",
                       _write("n5_未批准.json", d5), fixture_ok=True,
                       expect_block="not_approved", expect_allowed=False))

    # ── N6 fixture 但没开显式开关 ─────────────────────────────────────────
    cases.append(_case("N6_未开开关", "fixture 配置必须靠环境变量显式放行，"
                                      "不能因为文件叫 fixture 就自动放行",
                       C.FIXTURE, fixture_ok=False,
                       expect_block="fixture_without_explicit_env", expect_allowed=False))

    # ── N7 取值不在已实现集合 ─────────────────────────────────────────────
    d7 = copy.deepcopy(base)
    d7["shared"]["intrabar_break_cancel"]["mode"] = "low_only"   # 白名单里没有
    cases.append(_case("N7_取值未实现", "未实现的规则分支不许静默降级成已实现分支",
                       _write("n7_未实现取值.json", d7), fixture_ok=True,
                       expect_block="config_value_unsupported", expect_allowed=False))

    # ── N8 经济口径声明冲突 ───────────────────────────────────────────────
    d8 = copy.deepcopy(base)
    d8["conflict_resolution"] = {"slippage_source": "strategy_package",
                                 "risk_config": "strategy_package",
                                 "participation_cap": "strategy_package"}
    cases.append(_case("N8_经济口径声明冲突",
                       "声明策略包（3bps/2000U/3仓/30%/0.1%）而引擎实际是"
                       "（5bps/5000U/10仓/10%/1%）⇒ 阻塞并列差异，不替人择一",
                       _write("n8_口径冲突.json", d8), fixture_ok=True,
                       expect_block="economic_caliber_declared_mismatch",
                       expect_allowed=False))

    # ── N9 引擎已确定语义被改（临时替换 approved_params，不落盘）──────────
    _orig = PE.approved_params
    _tampered = dict(_orig())
    _tampered["stoploss_s"] = 0.04                     # 已确定 5% 被改成 4%
    PE.approved_params = lambda: _tampered
    try:
        cases.append(_case("N9_已确定语义被改",
                           "把引擎 stoploss_s 临时改成 0.04 ⇒ 必须 fixed_rules_mismatch",
                           C.FIXTURE, fixture_ok=True,
                           expect_block="fixed_rules_mismatch", expect_allowed=False,
                           extra={"tampered": {"stoploss_s": 0.04}}))
    finally:
        PE.approved_params = _orig

    # ── P1 fixture + 显式开关 ─────────────────────────────────────────────
    cases.append(_case("P1_fixture放行", "与 N6 同一份文件，只把开关打开",
                       C.FIXTURE, fixture_ok=True, expect_block=None,
                       expect_allowed=True, expect_scope="fixture_chain_selfcheck"))

    # ── P2 批准配置 ───────────────────────────────────────────────────────
    d10 = copy.deepcopy(base)
    d10["kind"] = "approved"
    d10["approved"] = True
    d10["approval_ref"] = "自测用：证明「批准」这条路真的能通（不是真批准）"
    cases.append(_case("P2_批准放行", "kind=approved + approved=true ⇒ approved_rules",
                       _write("p2_批准.json", d10), fixture_ok=False,
                       expect_block=None, expect_allowed=True,
                       expect_scope="approved_rules"))

    # ── try_open：阻塞配置下不许调 paper_engine ───────────────────────────
    store = AE.open_store(GATE / "observe.sqlite3")
    calls = {"n": 0, "args": None}

    def _counting_open(*a, **kw):
        calls["n"] += 1
        calls["args"] = {"a": [str(x) for x in a], "kw": {k: str(v) for k, v in kw.items()}}
        return {"ok": True, "stub": True}

    _real_open = PE.open_position
    PE.open_position = _counting_open
    cand = {"candidate_id": 1, "scheme": "4a", "symbol": "SOLUSDT",
            "signal_uid": "accum05_4a_SOLUSDT_probe",
            "confirm_close_ms": 1786168799999, "earliest_entry_ms": 1786169099999,
            "entry_bar_open_ms": 1786169100000}
    open_probe = {}
    try:
        # ① 阻塞配置（N6：fixture 未开开关）
        _set_env(GATE / "paper", fixture_ok=False)
        cfg_blocked = AE.load_config(str(C.FIXTURE))
        calls["n"] = 0
        r1 = AE.try_open("4a", "SOLUSDT", cand, cfg_blocked, store)
        open_probe["blocked"] = {"reason": r1.get("reason"),
                                 "called_paper_engine": r1.get("called_paper_engine"),
                                 "paper_calls": calls["n"],
                                 "open_allowed": cfg_blocked.get("open_allowed")}
        # ② 放行配置（P1）
        _set_env(GATE / "paper", fixture_ok=True)
        cfg_ok = AE.load_config(str(C.FIXTURE))
        calls["n"] = 0
        r2 = AE.try_open("4a", "SOLUSDT", cand, cfg_ok, store)
        open_probe["allowed"] = {"reason": r2.get("reason"),
                                 "called_paper_engine": r2.get("called_paper_engine"),
                                 "paper_calls": calls["n"],
                                 "open_allowed": cfg_ok.get("open_allowed"),
                                 "isolation": r2.get("isolation")}
        # ③ 放行配置 + 账本目录指向生产 ⇒ 必须 isolation_violation，仍不许调
        _set_env(C.PROJECT / "数据" / "paper", fixture_ok=True)
        cfg_ok2 = AE.load_config(str(C.FIXTURE))
        calls["n"] = 0
        r3 = AE.try_open("4a", "SOLUSDT", cand, cfg_ok2, store)
        open_probe["prod_ledger_dir"] = {"reason": r3.get("reason"),
                                         "called_paper_engine": r3.get("called_paper_engine"),
                                         "paper_calls": calls["n"],
                                         "isolation": r3.get("isolation")}
    finally:
        PE.open_position = _real_open
        _set_env(GATE / "paper", fixture_ok=True)

    attempts = store.con.execute(
        "SELECT scheme, symbol, allowed, reason, config_kind FROM accum05_open_attempt "
        "ORDER BY id").fetchall()
    store.close()

    open_probe["attempts_written"] = [
        {"scheme": r[0], "symbol": r[1], "allowed": r[2], "reason": r[3], "kind": r[4]}
        for r in attempts]
    open_probe["ok"] = (
        open_probe["blocked"]["paper_calls"] == 0
        and open_probe["blocked"]["called_paper_engine"] is False
        and open_probe["allowed"]["paper_calls"] == 1          # 正向对照：确实调了
        and open_probe["allowed"]["called_paper_engine"] is True
        and open_probe["prod_ledger_dir"]["reason"] == "isolation_violation"
        and open_probe["prod_ledger_dir"]["paper_calls"] == 0)

    # ── 结构一致性：待决项映射 + 已确定语义（正常态）─────────────────────
    mapping = AE.mapping_check()
    fixed = AE.check_fixed_rules()

    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "why": ("证明配置闸门真能拦住：九条负向用例逐条落在期望的阻塞原因上，"
                   "并配两条正向对照；同时证明阻塞配置下 try_open 不会调用 "
                   "paper_engine（用调用计数器，不看返回值的自称）。"),
           "required_fields": len(AE.REQUIRED_PATHS),
           "cases": cases,
           "open_probe": open_probe,
           "mapping_check": mapping,
           "fixed_rules": {"ok": fixed["ok"], "rows": fixed["rows"],
                           "engine": fixed["engine"]},
           "summary": {
               "cases_n": len(cases),
               "cases_ok": sum(1 for c in cases if c["ok"]),
               "all_cases_ok": all(c["ok"] for c in cases),
               "open_probe_ok": open_probe["ok"],
               "mapping_ok": mapping["ok"],
               "fixed_rules_ok": fixed["ok"],
           }}
    out["summary"]["all_ok"] = (out["summary"]["all_cases_ok"]
                               and out["summary"]["open_probe_ok"]
                               and out["summary"]["mapping_ok"]
                               and out["summary"]["fixed_rules_ok"])

    C.dump("04_配置闸门.json", out)
    print(json.dumps({"summary": out["summary"],
                      "cases": [{"case": c["case"], "ok": c["ok"],
                                 "blocked_by": c["blocked_by"],
                                 "open_allowed": c["open_allowed"]} for c in cases]},
                     ensure_ascii=False, indent=1))
    return 0 if out["summary"]["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
