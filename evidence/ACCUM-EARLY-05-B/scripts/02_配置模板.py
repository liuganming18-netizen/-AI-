# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜02 生成配置模板（空白表单 + fixture 自测配置）。

纪律：字段清单**从代码里的必填清单取**（`accum_early_entry.REQUIRED_PATHS`），不手抄。
  · `config/批准配置_空白表单_05.json`：全部 null，照着填；
  · `config/fixture_rules_05.json`：工程自测用，`kind=fixture`、`approved=false`，
    取值**逐项标注来源**（04-A 的推荐值），只证明链路行为，不是批准。

自检（任一不满足就报错退出，不静默通过）：
  1. fixture 覆盖的路径集合 == REQUIRED_PATHS（多一个少一个都算错）；
  2. 空白表单的路径集合 == REQUIRED_PATHS；
  3. fixture 里每个 IMPLEMENTED 白名单字段的取值都在白名单内；
  4. 两份文件用 `load_config()` 读回来：空白表单必须 `open_allowed=False` 且
     `missing` 数量 == 必填字段数；fixture 在设了环境开关时 `open_allowed=True`。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

import accum_early_entry as AE                                 # noqa: E402

#: fixture 的取值。**每一项都是「04-A 推荐值」或「工程上必须先有一个值」**，
#: 不是用户批准值。来源写在 `fixture_notes` 里，随文件一起落盘。
FIXTURE_VALUES = {
    "schema_version": AE.CONFIG_SCHEMA,
    "kind": "fixture",
    "approved": False,
    "approval_ref": "工程自测配置（未经用户批准）：只用于验证接线与链路行为",

    "shared.sweep_reference.mode": "prior_n_low",
    "shared.sweep_reference.n": 24,
    "shared.sweep_reference.must_be_above_box_low": True,
    "shared.sweep_recovery_scope.mode": "single_bar",
    "shared.sweep_recovery_scope.max_bars": 1,
    "shared.sweep_max_overshoot_atr": 0.5,
    "shared.sweep_min_recovery_atr": 0.1,

    "shared.local_resistance.mode": "swing_high_k3_lookback48",
    "shared.local_resistance.lookback_bars": 48,
    "shared.local_resistance.swing_k": 3,
    "shared.local_resistance.timeframe": "1h",
    "shared.local_resistance_confirm.mode": "1h_close",
    "shared.local_resistance_confirm.buffer_atr": 0.0,
    "shared.resistance_qualify.min_gap_atr": 0.5,

    "shared.sell_pressure.baseline": "v20_median",
    "shared.sell_pressure.n": 20,
    "shared.sell_pressure.buy_ratio_mult": 1.0,
    "shared.sell_pressure.require": True,
    "shared.sell_pressure.require_upper_half": True,
    "shared.sell_pressure.sell_pressure_mult": 0.8,

    "shared.observe_deadline_hours": 336,
    "shared.intrabar_break_cancel.mode": "close_only",
    "shared.intrabar_break_cancel.buffer_atr": 0.1,
    "shared.invalidate.close_below_l_bars": 2,
    "shared.invalidate.l_overshoot_atr": 0.5,
    "shared.armed_fail.bars": 2,
    "shared.armed_fail.buffer_atr": 0.1,
    "shared.confirm.require_bull_close": True,
    "shared.entry_gate.max_gap_up_atr": 0.5,
    "shared.entry_gate.entry_price_deviation_cap": 0.015,
    "shared.oi_role": "evidence_only",
    "shared.repeat_candidate_limit.per_event": 1,
    "shared.repeat_candidate_limit.cooldown_hours": 0,

    "schemes.4a.enabled": True,
    "schemes.4a.confirm": "close_above_resistance_first",
    "schemes.4b.enabled": True,
    "schemes.4b.confirm": "next_close_holds_above_resistance",
    "schemes.4c.enabled": True,
    "schemes.4c.confirm": "pullback_to_resistance_then_close_up",
    "schemes.4c.pullback.zone_atr": 0.25,
    "schemes.4c.pullback.deadline_hours": 48,

    "exit.gap_model": "stop_price",

    "conflict_resolution.slippage_source": "effective_view",
    "conflict_resolution.risk_config": "effective_view",
    "conflict_resolution.participation_cap": "effective_view",
}

FIXTURE_NOTES = {
    "为什么用 effective_view 作口径声明":
        "引擎生效视图实际是 5bps 滑点；声明 strategy_package（3bps）会与账本不符，"
        "本单不择一，只在 fixture 里指名一个能对上的口径。",
    "为什么 sell_pressure_mult=0.8": "与冻结规则 retest_vol_multiplier=0.8 同倍数（04-A 推荐值）。",
    "为什么 gap_model=stop_price": "引擎现行行为；另两档（bar_open / midpoint）属待决，只作敏感性。",
    "为什么 cooldown_hours=0": "repeat_candidate_limit 未批准 ⇒ 冷却不生效，候选会比正式规则更密。",
}


def _set(doc: dict, dotted: str, value) -> None:
    cur = doc
    parts = dotted.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            raise SystemExit(f"路径冲突：{dotted}")
    cur[parts[-1]] = value


def build(values: dict | None) -> dict:
    doc: dict = {}
    for path in AE.REQUIRED_PATHS:
        _set(doc, path, None if values is None else values[path])
    # 格式版本不是「参数」，是文件身份 ⇒ 空白表单也写上，免得读回来先报格式不符
    doc["schema_version"] = AE.CONFIG_SCHEMA
    return doc


def _paths_of(doc: dict) -> set:
    out = set()

    def walk(node, prefix=""):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(v, p)
            else:
                out.add(p)
    walk(doc)
    return out


def main() -> int:
    C.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    checks = []

    # ① fixture 取值覆盖必须与必填清单完全一致
    extra = set(FIXTURE_VALUES) - set(AE.REQUIRED_PATHS)
    lack = set(AE.REQUIRED_PATHS) - set(FIXTURE_VALUES)
    checks.append({"check": "fixture 取值集合 == 必填清单", "ok": not extra and not lack,
                   "extra": sorted(extra), "missing": sorted(lack)})

    blank = build(None)
    fx = build(FIXTURE_VALUES)
    checks.append({"check": "空白表单路径集合 == 必填清单",
                   "ok": _paths_of(blank) == set(AE.REQUIRED_PATHS),
                   "n": len(_paths_of(blank))})
    checks.append({"check": "fixture 路径集合 == 必填清单",
                   "ok": _paths_of(fx) == set(AE.REQUIRED_PATHS),
                   "n": len(_paths_of(fx))})

    # ③ 白名单字段取值必须在已实现集合内
    bad = []
    for key, allowed in AE.IMPLEMENTED.items():
        v = AE._dig(fx, key)
        if v is AE._MISSING or v is None:
            continue
        if v not in allowed:
            bad.append({"field": key, "value": v, "allowed": sorted(allowed)})
    checks.append({"check": "fixture 白名单字段取值合法", "ok": not bad, "bad": bad})

    C.BLANK.write_text(json.dumps(blank, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = dict(fx)
    payload["_fixture_notes"] = FIXTURE_NOTES
    C.FIXTURE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # ④ 读回来验闸门行为
    os.environ.pop(AE.FIXTURE_ENV, None)
    b = AE.load_config(C.BLANK)
    n_blank_missing = len(AE.REQUIRED_PATHS) - 1        # schema_version 已填（格式身份）
    checks.append({"check": "空白表单 ⇒ 阻塞且缺字段数 == 必填字段数-1（格式版本已填）",
                   "ok": (b["open_allowed"] is False
                          and len(b["missing"]) == n_blank_missing),
                   "missing": len(b["missing"]),
                   "expected_missing": n_blank_missing,
                   "required": len(AE.REQUIRED_PATHS),
                   "blocked_by": b["blocked_by"]})

    f_off = AE.load_config(C.FIXTURE)
    checks.append({"check": "fixture 未开环境开关 ⇒ 阻塞（fixture_without_explicit_env）",
                   "ok": (f_off["open_allowed"] is False
                          and "fixture_without_explicit_env" in f_off["blocked_by"]),
                   "blocked_by": f_off["blocked_by"], "missing": f_off["missing"],
                   "unsupported": f_off["unsupported"]})

    os.environ[AE.FIXTURE_ENV] = "1"
    f_on = AE.load_config(C.FIXTURE)
    os.environ.pop(AE.FIXTURE_ENV, None)
    checks.append({"check": "fixture 开了环境开关 ⇒ 允许开仓（fixture_chain_selfcheck）",
                   "ok": f_on["open_allowed"] is True and f_on.get("open_scope") == "fixture_chain_selfcheck",
                   "blocked_by": f_on["blocked_by"], "missing": f_on["missing"],
                   "unsupported": f_on["unsupported"],
                   "fixed_rules_ok": (f_on.get("fixed_rules") or {}).get("ok")})

    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "required_fields": len(AE.REQUIRED_PATHS),
           "schema": AE.CONFIG_SCHEMA,
           "files": {"blank": str(C.BLANK), "fixture": str(C.FIXTURE)},
           "checks": checks,
           "all_ok": all(c["ok"] for c in checks)}
    C.dump("02_配置模板.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
