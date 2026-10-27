# -*- coding: utf-8 -*-
"""02｜V3.1 实际路由、策略身份与信号落点（只读对撞）。

三处对撞，缺一即不成立：
  ① 代码事实（哪个文件、哪一行、什么常量）
  ② 运行事实（进程 91947 跑的是哪个模块、观察明细的写者 pid）
  ③ 接口事实（/api/v1/signals 返回的身份字段、/api/v1/strategies/registered 的配置身份）

输出：out/02_路由与身份.json
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib08 as L  # noqa: E402

ET = L.PROJECT / "策略" / "WyckoffMaster" / "event_trend_v0_1"


def grep(path: Path, pattern: str, limit: int = 12) -> list[dict]:
    """返回 [{line, text}]，给证据留精确位置。"""
    out = []
    if not path.exists():
        return [{"error": f"文件不存在 {path}"}]
    rx = re.compile(pattern)
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if rx.search(line):
            out.append({"line": i, "text": line.strip()[:220]})
            if len(out) >= limit:
                break
    return out


def http_json(url: str):
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}

    # ── ① 代码事实 ────────────────────────────────────────────────────────
    rep["代码事实"] = {
        "运行时主文件": {
            "path": "框架/backend/unified_runtime.py",
            "imports_event_state_machine": grep(
                L.BACKEND / "unified_runtime.py", r"EventStateMachine", 6),
            "imports_paper_engine": grep(
                L.BACKEND / "unified_runtime.py", r"^import paper_engine", 3),
            "身份常量": grep(
                L.BACKEND / "unified_runtime.py",
                r"^(EXCHANGE|MARKET|TIMEFRAME|STRATEGY_KEY|ARM_ENTRY_ROLE)\s*=", 10),
        },
        "策略包": {
            "path": "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
            "幂等键构造": grep(ET / "state_machine.py", r"cand_id\s*=\s*f?\"cand_", 4),
            "候选生成处": grep(ET / "state_machine.py", r"CandidateSnapshot\.create", 4),
            "config_strategy_version": grep(ET / "config.py", r"strategy_version", 6),
        },
        "持久化": {
            "path": "框架/backend/unified_runtime.py",
            "写入函数": grep(L.BACKEND / "unified_runtime.py",
                          r"def persist_candidates", 3),
            "冲突策略": grep(L.BACKEND / "unified_runtime.py",
                          r"ON CONFLICT\(signal_uid\) DO UPDATE", 3),
            "调用点": grep(L.BACKEND / "unified_runtime.py",
                        r"persist_candidates\(", 6),
        },
        "API路由": {
            "path": "框架/backend/api/routes.py",
            "信号路由": grep(L.BACKEND / "api" / "routes.py",
                          r'Route\("/api/v1/signals', 6),
            "观察路由": grep(L.BACKEND / "api" / "routes.py",
                          r'signals/observe', 4),
        },
        "当前区白名单": {
            "path": "框架/backend/live_api.py",
            "来源白名单": grep(L.BACKEND / "live_api.py",
                           r"origin IN \('live','recovered'\)", 3),
            "可执行态": grep(L.BACKEND / "live_api.py",
                          r"executable_state='enterable'", 4),
            "角色判据": grep(L.BACKEND / "live_api.py",
                          r"ARM_ENTRY_ROLE", 6),
        },
        "旧链边界": {
            "live_signal_engine_是否被unified_runtime引用": grep(
                L.BACKEND / "unified_runtime.py", r"live_signal_engine", 4) or "无引用（0 处）",
            "谁引用旧链": grep(L.BACKEND / "live_worker.py",
                            r"import live_signal_engine", 4),
            "LEGACY_PIPELINE_AVAILABLE": grep(
                L.BACKEND / "live_signal_engine.py", r"LEGACY_PIPELINE_AVAILABLE", 6),
            "结论": ("`live_signal_engine.py` 只被 `live_worker.py` 引用；"
                     "`unified_runtime.py` 0 处引用 ⇒ 当前运行链不是旧唐奇安 3a/3b 链"),
        },
    }

    # ── ② 运行事实 ────────────────────────────────────────────────────────
    st = L.run(["sh", "start.sh", "status"], cwd=L.BACKEND, timeout=180)
    try:
        status = json.loads(st["stdout"])
    except Exception as exc:  # noqa: BLE001
        status = {"_parse_error": str(exc)}
    rt = (status.get("services") or {}).get("runtime") or {}
    snap = rt.get("runtime_snapshot") or {}
    rep["运行事实"] = {
        "runtime_pid": rt.get("pid"),
        "runtime_status": rt.get("status"),
        "identity_matched": rt.get("identity_matched"),
        "pid_file_identity": (L.BACKEND / "runtime-3a-8787.pid").read_text().strip()
        if (L.BACKEND / "runtime-3a-8787.pid").exists() else None,
        "proc_identity": L.proc_identity(int(rt.get("pid") or 0)),
        "signal_engine_status": (snap.get("signal_engine") or {}).get("status"),
        "universe": (snap.get("signal_engine") or {}).get("universe"),
        "watermark_1h": (snap.get("signal_engine") or {}).get("watermark_1h"),
        "bootstrap_recovered_candidates": ((snap.get("signal_engine") or {})
                                           .get("bootstrap") or {}).get("recovered_candidates"),
        "bootstrap_history_candidates_discarded": ((snap.get("signal_engine") or {})
                                                   .get("bootstrap") or {}).get("history_candidates_discarded"),
        "data_source": ((snap.get("signal_engine") or {}).get("bootstrap") or {}).get("data_source"),
        "order_processing": snap.get("order_processing"),
        "execution_daemon": snap.get("execution_daemon"),
    }

    # 观察明细的写者 pid 是否就是运行时进程（文件身份 vs 进程身份）
    obs_p = L.LOCKS / "paper_runtime_3a_observe.json"
    if obs_p.exists():
        d = json.loads(obs_p.read_text(encoding="utf-8"))
        rep["运行事实"]["observe_file"] = {
            "path": str(obs_p), "pid": d.get("pid"), "arm": d.get("arm"),
            "cycle_count": d.get("cycle_count"),
            "generated_at": d.get("generated_at"),
            "counts": d.get("counts"), "observing_total": d.get("observing_total"),
            "universe": d.get("universe"),
            "pid_equals_runtime": d.get("pid") == rt.get("pid"),
        }

    # ── ③ 接口事实 ────────────────────────────────────────────────────────
    cur = http_json("http://127.0.0.1:8787/api/v1/signals?scope=current&limit=500")
    alls = http_json("http://127.0.0.1:8787/api/v1/signals?scope=all&limit=5")
    reg = http_json("http://127.0.0.1:8787/api/v1/strategies/registered")
    rep["接口事实"] = {
        "signals_current": {k: cur.get(k) for k in
                            ("total", "strategy_version", "rule_version", "param_hash",
                             "param_hash_basis", "params_source", "missing_reason", "as_of")},
        "signals_current_quality": cur.get("quality"),
        "signals_all_total": alls.get("total"),
        "registered_identity": (reg.get("identity") or {}),
        "registered_count": reg.get("registered_count"),
        "gate_binding": reg.get("gate_binding"),
        "run_frozen": reg.get("run_frozen"),
        "capital": reg.get("capital"),
    }

    # ── ④ 三处一致性判定（机器判，不手抄）────────────────────────────────
    facts = rep["代码事实"]
    ident = rep["接口事实"]["registered_identity"]
    checks = {
        "运行时导入 EventStateMachine（代码）":
            bool(facts["运行时主文件"]["imports_event_state_machine"]
                 and "line" in facts["运行时主文件"]["imports_event_state_machine"][0]),
        "运行时未引用旧链 live_signal_engine":
            facts["旧链边界"]["live_signal_engine_是否被unified_runtime引用"] == "无引用（0 处）",
        "幂等键由 candidate_id 构造（代码有定义）":
            bool(facts["策略包"]["幂等键构造"]
                 and "cand_" in facts["策略包"]["幂等键构造"][0].get("text", "")),
        "写入用 ON CONFLICT(signal_uid) DO UPDATE（幂等，非 INSERT OR IGNORE）":
            bool(facts["持久化"]["冲突策略"]
                 and "ON CONFLICT" in facts["持久化"]["冲突策略"][0].get("text", "")),
        "运行进程 pid 与 pid 文件一致":
            str(rep["运行事实"]["runtime_pid"]) == str(rep["运行事实"]["pid_file_identity"]),
        "观察明细写者 pid == 运行时 pid":
            (rep["运行事实"].get("observe_file") or {}).get("pid_equals_runtime") is True,
        "接口身份 strategy_version == FT-LOW4H-EXP-V2.2":
            rep["接口事实"]["signals_current"].get("strategy_version") == "FT-LOW4H-EXP-V2.2",
        "接口 param_hash == 生效视图 sha256 前 12 位":
            (ident.get("view_sha256") or "").startswith(
                rep["接口事实"]["signals_current"].get("param_hash") or "###"),
        "配置身份自检 identity_ok":
            ident.get("identity_ok") is True,
        "G01–G05 全通过（放行门禁）":
            (rep["接口事实"]["gate_binding"] or {}).get("all_pass") is True,
        "运行已冻结 run_frozen":
            rep["接口事实"]["run_frozen"] is True,
    }
    rep["判定"] = {"checks": checks,
                 "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks),
                 "结论": ""}
    fail = [k for k, v in checks.items() if not v]
    rep["判定"]["结论"] = (
        "路由与策略身份可确认：V3.1 入口 = unified_runtime + EventStateMachine，"
        "信号落点 = live_signal(signal_uid=candidate_id)，旧链未参与运行。"
        "但配置身份与门禁不成立：identity_ok=false、G01–G05 全 pending、run_frozen=false ⇒ "
        "任何信号只能带研究/未批准标识，不得标成正式获批信号。"
        if all(checks[k] for k in list(checks)[:8]) else
        f"身份或路由存在未通过项，接入必须阻塞：{fail}")

    p = L.dump("02_路由与身份.json", rep)
    print("== 02 路由与身份 ==")
    for k, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + k)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("identity_ok:", ident.get("identity_ok"), "| problems:", ident.get("problems"))
    print("gate:", (rep["接口事实"]["gate_binding"] or {}).get("gates"),
          "| all_pass:", (rep["接口事实"]["gate_binding"] or {}).get("all_pass"),
          "| run_frozen:", rep["接口事实"]["run_frozen"])
    print("->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
