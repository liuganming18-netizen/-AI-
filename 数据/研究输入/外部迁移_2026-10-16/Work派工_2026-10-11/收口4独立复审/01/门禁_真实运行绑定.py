#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收口4 · 门禁**真实运行绑定**核验（bindings 必须按实际产物实算，而非仅 64-hex 格式）。

合同（33 号 item 3）要求：
  · bindings 不仅 64-hex 格式，还须按**实际文件 / 冻结配置 / 数据快照 / 引擎入口实算**；
  · Codex 接受记录的 `path+sha256+scope` 须与该门禁证据**及具体 run** 绑定相符；
  · **禁止**引用「存在的别的接受记录」给任意文件/旧 run 背书；
  · 隔离测试入口须**显式模式**，且**不得**成为正常研究的环境覆盖旁路；
  · 当前研究门禁保持 **pending**，Work **不**自写 Codex G01–G05 接受记录。

本模块只做**只读实算**与判定，不写生产、不推断、不自写 PASS。真实绑定实算值由
`compute_real_bindings()` 从实际产物计算；`verify_bindings()` 逐维比对声明值 vs 实算值；
`verify_run_binding()` 校验**具体 run** 与台账一致。任一不符 ⇒ fail closed。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

BACKEND = Path("/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/后端实现")
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.dont_write_bytecode = True

from strategies import wx_config, wx_gate, wx_donchian_wyckoff as wx  # noqa: E402

# 真实绑定实算所用的**实际产物**（只读）。
CODE_SOURCE_FILES = [
    "strategies/wx_config.py", "strategies/wx_donchian_wyckoff.py",
    "strategies/wx_engine.py", "strategies/wx_execution.py",
    "strategies/wx_gate.py", "strategies/wx_ledger.py", "strategy_runner.py",
]
DATA_SNAPSHOT = (ROOT / "最新唐奇安威科夫接通与收尾_2026-10-11/数据终验与输入试点2/"
                 "02_pilot_export_readin.json")
ENGINE_EVIDENCE = (ROOT / "最新唐奇安威科夫接通与收尾_2026-10-11/接线路整改3/"
                   "复用真实调用证据_整改3.json")


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def _canon(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def compute_real_bindings(backend=BACKEND):
    """从**实际产物**实算七个身份维度（只读）。缺失 ⇒ 对应项为 None（fail closed）。"""
    out = {"code": None, "configs_18": None, "engine": None, "execution_cost": None,
           "capital": None, "data": None, "holdout": None, "provenance": {}}

    # code：逐文件实算 sha，再对 {相对名: sha} canonical 汇总
    files = {}
    for rel in CODE_SOURCE_FILES:
        fp = Path(backend) / rel
        if fp.exists():
            files[rel] = _sha(fp)
    if files:
        out["code"] = {"source_root": str(backend), "source_sha256": _canon(files),
                       "files": files}
        out["provenance"]["code"] = {rel: files[rel] for rel in sorted(files)}

    # configs_18：从生效视图**重算**18 配置生效身份（含执行覆盖）
    view, vsha = wx_config.load_view()
    if view is not None:
        eff = wx_config.effective_configs_check(view)
        out["configs_18"] = {"effective_sha256": eff["sha256"], "count": eff["count"],
                             "status": eff["status"]}
        out["provenance"]["view_sha256"] = vsha
        # execution_cost：从视图 execution 段实算（费率/滑点）
        ex = view.get("execution") or {}
        fee = ex.get("fee_each_side")
        slip = ex.get("slippage_each_side")
        out["execution_cost"] = {"fee_each_side": fee, "slippage_each_side": slip,
                                 "sha256": _canon({"fee_each_side": fee,
                                                   "slippage_each_side": slip})}
        # capital：从视图 capital_model 实算
        cm = view.get("capital_model") or {}
        out["capital"] = {"initial_equity_USDT":
                          cm.get("total_initial_equity_USDT_per_research_arm"),
                          "shared_across": "binance,okx",
                          "leverage": (view.get("risk") or {}).get("leverage", 1)}
        # holdout：从视图 sample 段实算（冻结样本切分/embargo/holdout 前置）
        out["holdout"] = {"frozen_sha256": _canon(view.get("sample") or {}),
                          "window": "DEV/VAL/FINAL split "
                                    + "/".join(str(x) for x in (view.get("sample") or {}).get("split", []))}

    # engine：从实际引擎证据实算（freqtrade 版本 + 官方 trade_model.py sha）+ 本地引擎入口
    if ENGINE_EVIDENCE.exists():
        ev = json.loads(ENGINE_EVIDENCE.read_text(encoding="utf-8"))
        eng_entry = Path(backend) / "strategies/wx_engine.py"
        out["engine"] = {"name": "freqtrade", "version": ev.get("freqtrade_version"),
                         "sha256": ev.get("trade_model_sha256"),
                         "entry_sha256": _sha(eng_entry) if eng_entry.exists() else None}
        out["provenance"]["engine_evidence_sha256"] = _sha(ENGINE_EVIDENCE)

    # data：从实际数据快照（试点 manifest）实算
    if DATA_SNAPSHOT.exists():
        d = json.loads(DATA_SNAPSHOT.read_text(encoding="utf-8"))
        out["data"] = {"snapshot_sha256": _sha(DATA_SNAPSHOT),
                       "as_of": d.get("cutoff"),
                       "scope": "pilot_" + str(len(d.get("samples_requested") or [])) + "coins"}
        out["provenance"]["data_snapshot"] = str(DATA_SNAPSHOT)
    return out


def verify_bindings(declared, real):
    """逐维比对**声明值**与**实算值**；仅比对门禁规格要求的具体字段。"""
    per = {}
    ok = True
    for key in wx_gate.REQUIRED_BINDINGS:
        dv = (declared or {}).get(key)
        rv = (real or {}).get(key)
        if not isinstance(dv, dict) or not isinstance(rv, dict):
            per[key] = {"ok": False, "why": "missing_declared_or_real"}
            ok = False
            continue
        spec = wx_gate.BINDING_SPEC.get(key, {})
        fields = tuple(spec.get("required", ())) + tuple((spec.get("equals") or {}).keys())
        diffs = {f: {"declared": dv.get(f), "real": rv.get(f)}
                 for f in fields if f in rv and dv.get(f) != rv.get(f)}
        per[key] = {"ok": not diffs, "diffs": diffs}
        ok = ok and not diffs
    return ok, per


def verify_run_binding(binding_doc, run):
    """校验**具体 run** 与台账绑定一致（禁止用别的 run/旧 run 套用）。"""
    b = binding_doc.get("bindings") or {}
    checks = {
        "code": (run.get("code_sha256") == (b.get("code") or {}).get("source_sha256")),
        "data": (run.get("data_snapshot_sha256") == (b.get("data") or {}).get("snapshot_sha256")),
        "engine": (run.get("engine_sha256") == (b.get("engine") or {}).get("sha256")),
        "configs_18": (run.get("configs_18_sha256")
                       == (b.get("configs_18") or {}).get("effective_sha256")),
        "holdout": (run.get("holdout_sha256") == (b.get("holdout") or {}).get("frozen_sha256")),
        "capital": (run.get("capital_USDT") == (b.get("capital") or {}).get("initial_equity_USDT")),
    }
    return all(checks.values()), checks


def gate(ledger_path, *, run=None, acceptance_path=None, acceptance_sha=None,
         real=None, isolated=False):
    """综合判定：wx_gate.binding 层 + 真实绑定实算 + 具体 run 绑定。

    `isolated=True` 表示**显式隔离模式**（测试用合成接受记录）；隔离模式下**永不**代表
    研究放行 —— 返回 `research_pass=False`。生产路径（isolated=False）使用冻结索引。
    """
    b = wx_gate.binding(ledger_path, acceptance_path=acceptance_path,
                        acceptance_sha=acceptance_sha)
    real = real if real is not None else compute_real_bindings()
    declared = {}
    try:
        declared = json.loads(Path(ledger_path).read_text(encoding="utf-8")).get("bindings") or {}
    except (OSError, ValueError):
        declared = {}
    real_ok, real_detail = verify_bindings(declared, real)
    run_ok, run_detail = (verify_run_binding({"bindings": declared}, run)
                          if run is not None else (None, {}))
    all_pass = bool(b.get("all_pass")) and real_ok and (run_ok is not False)
    research_pass = bool(all_pass and not isolated)
    return {"gate_all_pass": b.get("all_pass"), "real_bindings_ok": real_ok,
            "real_bindings_detail": real_detail, "run_binding_ok": run_ok,
            "run_binding_detail": run_detail, "isolated": isolated,
            "all_pass": all_pass, "research_pass": research_pass,
            "self_check": b.get("self_check"),
            "codex_acceptance": b.get("codex_acceptance"),
            "reason": b.get("reason")}


if __name__ == "__main__":
    rb = compute_real_bindings()
    print(json.dumps({k: (v if k != "provenance" else "...") for k, v in rb.items()},
                     ensure_ascii=False, indent=2, default=str))
