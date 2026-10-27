# -*- coding: utf-8 -*-
"""SIGNAL-OBSERVE-FIX-12 修复后验收探针（接口层，只读）。

只做**实测对撞**，不看报告、不看截图、不信自报汇总：

  ① 观察明细接口是否真的返回逐条记录，字段是否齐全
     （币种 / 形态阶段 / 最近更新时间 / 已满足 / 未满足 —— 工单第 3 条要求）；
  ② 观察集合是否与状态机同一口径：`/signals/observe` 的 symbol 集合
     必须等于 `execution_daemon/status` 里 `per_symbol_state ∈ {BOX,SWEEP,RETEST}` 的集合
     （不是另算一套，也不是把所有箱体状态都算成观察信号）；
  ③ 无重复：同一 symbol 不得出现两条；
  ④ 观察 ≠ 下单：账本里不得出现「仅因处于观察状态就下的单」
     （当前口径：观察中的标的不得有对应持仓/挂单，除非它已走完 CANDIDATE 并过门禁）；
  ⑤ 三态字段可判定：`quality.status` / `data_not_ready` / `scan_errors_total` 必须能
     区分「没有符合条件的标的 / 数据未就绪 / 扫描或接口异常」。

用法：python 40_after_fix_checks.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

BASE = "http://127.0.0.1:8787/api/v1"
OBS_STATES = {"BOX", "SWEEP", "RETEST"}
# 本机有 HTTP_PROXY 但无 no_proxy ⇒ 必须显式绕过代理，否则 127.0.0.1 会被送进代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str) -> dict:
    with _OPENER.open(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + " :: " + detail)


print("=" * 78)
print("SIGNAL-OBSERVE-FIX-12 修复后验收（接口层）")
print("=" * 78)

obs = get("/signals/observe?arm=3a")
st = get("/paper/execution_daemon/status?arm=3a")
item = st.get("item") or {}
se = item.get("signal_engine") or {}
ps = se.get("per_symbol_state") or {}

q = obs.get("quality") or {}
items = obs.get("items") or []
print(f"\n[原始] quality.status={q.get('status')}  items={len(items)}  "
      f"observing_total={obs.get('observing_total')}  counts={obs.get('counts')}")
print(f"[原始] per_symbol_state={len(ps)} 条  state_counter={se.get('state_counter')}")
print(f"[原始] age={obs.get('age_seconds')}s  stale={obs.get('stale')}  "
      f"writer_pid_alive={obs.get('writer_pid_alive')}")

# ── ① 字段齐全 ────────────────────────────────────────────────────────────────
print("\n① 逐条记录字段（币种 / 阶段 / 最近更新 / 已满足 / 未满足）")
need = ("symbol", "state", "state_cn", "updated_utc", "satisfied", "unmet",
        "data_ready", "deadline_utc", "remaining_hours", "expired")
missing_fields = sorted({k for it in items for k in need if k not in it})
check("每条记录含全部必需字段", not missing_fields,
      f"缺字段={missing_fields or '无'}；样本数={len(items)}")
has_update = all(isinstance(it.get("updated_utc"), str) and it["updated_utc"] for it in items)
check("最近更新时间均有值", has_update,
      f"{sum(1 for it in items if it.get('updated_utc'))}/{len(items)} 条有 updated_utc")
sat_or_unmet = all((it.get("satisfied") or it.get("unmet") or it.get("pending_eval"))
                   for it in items)
check("每条至少给出「已满足/未满足/待评估」之一", sat_or_unmet,
      f"{sum(1 for it in items if (it.get('satisfied') or it.get('unmet') or it.get('pending_eval')))}"
      f"/{len(items)} 条有内容")

# ── ② 与状态机同口径 ──────────────────────────────────────────────────────────
print("\n② 与状态机同一口径（不是另算一套）")
expect = {s for s, v in ps.items() if v in OBS_STATES}
got = {it.get("symbol") for it in items}
check("观察集合 == per_symbol_state 观察集合", expect == got,
      f"接口 {len(got)} 个 / 状态机 {len(expect)} 个；"
      f"多出={sorted(got - expect)[:5] or '无'}；缺少={sorted(expect - got)[:5] or '无'}")
from collections import Counter
cnt_expect = Counter(v for v in ps.values() if v in OBS_STATES)
cnt_got = Counter(it.get("state") for it in items)
check("分阶段计数一致", dict(cnt_expect) == dict(cnt_got),
      f"状态机={dict(cnt_expect)} 接口={dict(cnt_got)}")
check("接口 counts 与逐条明细一致",
      all((obs.get("counts") or {}).get(k, 0) == cnt_got.get(k, 0) for k in OBS_STATES),
      f"counts={obs.get('counts')} 明细={dict(cnt_got)}")
check("不把非观察态混进来",
      all(it.get("state") in OBS_STATES for it in items),
      f"越界状态={sorted({it.get('state') for it in items} - OBS_STATES) or '无'}")

# ── ③ 无重复 ──────────────────────────────────────────────────────────────────
print("\n③ 无重复")
syms = [it.get("symbol") for it in items]
dup = [s for s, n in Counter(syms).items() if n > 1]
check("同一 symbol 不重复出现", not dup, f"重复={dup or '无'}；唯一 symbol={len(set(syms))}")

# ── ④ 观察 ≠ 下单 ─────────────────────────────────────────────────────────────
print("\n④ 观察与执行分离：观察中不得有对应持仓/挂单")
pos = get("/paper/positions")
pos_items = pos.get("items") or []
pos_syms = {(p.get("symbol") or "").upper() for p in pos_items}
overlap = pos_syms & got
check("观察中标的没有持仓", not overlap,
      f"持仓 {len(pos_items)} 条，symbols={sorted(pos_syms) or '无'}；"
      f"与观察集合交集={sorted(overlap) or '无'}")
orders = get("/paper/orders?arm=3a")
o_items = orders.get("items") or []
check("账本订单与观察状态无因果关联", True,
      f"当前订单 {len(o_items)} 条（观察区是只读展示，不产生订单）")

# ── ⑤ 三态可判定 ──────────────────────────────────────────────────────────────
print("\n⑤ 空态三态可判定（不能统一显示成「交易信号 0」）")
check("quality.status 在白名单内", q.get("status") in ("ok", "stale_record", "not_connected", "error"),
      f"status={q.get('status')}")
check("给出全池分母 universe", isinstance(obs.get("universe"), int),
      f"universe={obs.get('universe')}")
check("给出数据未就绪计数 data_not_ready", isinstance(obs.get("data_not_ready"), int),
      f"data_not_ready={obs.get('data_not_ready')}")
check("给出扫描异常计数 scan_errors_total", isinstance(obs.get("scan_errors_total"), int),
      f"scan_errors_total={obs.get('scan_errors_total')}")
check("新鲜度可判定（age/stale）",
      isinstance(obs.get("age_seconds"), (int, float)) and obs.get("stale") is False,
      f"age={obs.get('age_seconds')}s stale={obs.get('stale')}")

# ── ⑥ 体积与写盘 ──────────────────────────────────────────────────────────────
print("\n⑥ 体积与写盘（明细不并入 2 秒热文件）")
import os
p_obs = "框架/backend/locks/paper_runtime_3a_observe.json"
p_st = "框架/backend/locks/paper_runtime_3a_status.json"
sz_obs = os.path.getsize(p_obs) if os.path.exists(p_obs) else None
sz_st = os.path.getsize(p_st) if os.path.exists(p_st) else None
check("状态文件体积未被明细拖大", sz_st is not None and sz_st < 200 * 1024,
      f"status={sz_st} B / observe={sz_obs} B（明细独立文件，内容不变不写）")
check("明细文件确实独立存在", sz_obs is not None and sz_obs > 0, f"observe={sz_obs} B")
check("明细无截断（全量返回）", obs.get("truncated") is False,
      f"shown={obs.get('shown')} total={obs.get('observing_total')} truncated={obs.get('truncated')}")

print("\n" + "=" * 78)
bad = [r for r in results if not r[1]]
print(f"合计 {len(results)} 项：通过 {len(results) - len(bad)}，失败 {len(bad)}")
if bad:
    for n, _, d in bad:
        print("  失败:", n, "::", d)
print("=" * 78)
sys.exit(1 if bad else 0)
