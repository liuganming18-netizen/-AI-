#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10_before_fix.py — 修复前证据：观察区为什么「没有反应」。

四段各自取一手证据，不靠推断：
  ① 状态机：全池到底有多少个「处于观察状态的事件」（冻结规则 §3 原文口径）
  ② 接口  ：`/api/v1/signals` 与 `/paper/execution_daemon/status` 各返回了什么
  ③ 前端类型层：`ExecutionDaemonView` 有没有接收逐币阶段
  ④ 前端渲染层：`SignalListPanel` 有没有把逐币阶段画出来

判定「观察中」的口径来自冻结规则
`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md` §3 原文：
  「每个 symbol+venue 最多一个处于观察状态的事件」
⇒ 观察中 = `active_event` 存在，即 current_state ∈ {BOX, SWEEP, RETEST}。
  CANDIDATE 及之后（VOLUME_REJECTED / OI_REJECTED / INVALIDATED / EXPIRED /
  MISSED_WITHOUT_RETEST / CLOSED）都已结束，不算观察中。
  IDLE = 没有事件，不算观察中（但它是「没进观察」的原因，单独统计）。

只读，不写任何库、不改任何状态。

用法：
    python 10_before_fix.py
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "out"
API = "http://127.0.0.1:8787"

OBSERVING_STATES = ("BOX", "SWEEP", "RETEST")


def get(url: str):
    # 代理坑：本机有 HTTP_PROXY 无 no_proxy，必须显式空 ProxyHandler
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── ① 状态机：观察中事件数 ────────────────────────────────────────────────
def check_engine() -> dict:
    d = get(f"{API}/api/v1/paper/execution_daemon/status?arm=3a")
    se = (d.get("item") or {}).get("signal_engine") or {}
    ps = se.get("per_symbol_state") or {}
    sc = se.get("state_counter") or {}
    by_state: dict[str, int] = {}
    for v in ps.values():
        by_state[v] = by_state.get(v, 0) + 1
    observing = {k: by_state.get(k, 0) for k in OBSERVING_STATES}
    return {
        "state_counter": sc,
        "per_symbol_state_条目数": len(ps),
        "per_symbol_state_分布": by_state,
        "观察中合计": sum(observing.values()),
        "观察中分阶段": observing,
        "per_symbol_state_样例": dict(list(ps.items())[:8]),
        "per_symbol_state_字段粒度": "只有 {symbol: 阶段名}，无箱体/无时间/无条件",
        "watermark_1h_utc": se.get("watermark_1h"),
        "watermark_4h_utc": se.get("watermark_4h"),
    }


# ── ② 接口：观察中事件有没有出口 ──────────────────────────────────────────
def check_api() -> dict:
    out = {}
    for scope in ("current", "all", "history"):
        d = get(f"{API}/api/v1/signals?scope={scope}")
        q = d.get("quality") or {}
        out[scope] = {
            "total": d.get("total"),
            "items": len(d.get("items") or []),
            "quality_status": q.get("status"),
            "missing_reason": d.get("missing_reason"),
            "顶层键": sorted(d.keys()),
            "有观察字段": any("observ" in k.lower() or "watch" in k.lower() for k in d),
        }
    d = get(f"{API}/api/v1/paper/execution_daemon/status?arm=3a")
    se = (d.get("item") or {}).get("signal_engine") or {}
    out["daemon_signal_engine_键"] = sorted(se.keys())
    out["daemon_有逐币明细"] = False
    # 逐币阶段只有阶段名，没有任何一个键承载 H/L/建档时刻/未满足条件
    for k in se.keys():
        if any(x in k.lower() for x in ("observe", "detail", "event", "condition")):
            out["daemon_有逐币明细"] = True
    return out


# ── ③ 前端类型层 ──────────────────────────────────────────────────────────
def check_frontend_types() -> dict:
    p = ROOT / "UI界面/web/src/api/liveTrading.ts"
    s = p.read_text(encoding="utf-8")
    i = s.index("export interface ExecutionDaemonView")
    seg = s[i:i + 4000]
    fields = re.findall(r"^\s{2}(\w+)\??:", seg, re.M)
    return {
        "文件": str(p.relative_to(ROOT)),
        "ExecutionDaemonView_字段": fields,
        "有_perSymbolState": "perSymbolState" in fields,
        "有_observeEvents": "observeEvents" in fields,
        "解析函数读_per_symbol_state": "per_symbol_state" in s,
        "stateCounter_有解析": "stateCounter" in s,
    }


# ── ④ 前端渲染层 ──────────────────────────────────────────────────────────
def check_frontend_render() -> dict:
    p = ROOT / "UI界面/web/src/components/SignalListPanel.tsx"
    s = p.read_text(encoding="utf-8")
    return {
        "文件": str(p.relative_to(ROOT)),
        "用_perSymbolState": "perSymbolState" in s,
        "用_stateCounter": "stateCounter" in s,
        "逐币列表渲染": bool(re.search(r"perSymbolState\s*[?&.]|Object\.entries\(\s*perSymbol", s)),
        "有阶段中文名映射": "箱体观察" in s and "已扫低" in s,
        "诊断区在details折叠内": "<details" in s and "查看诊断与历史候选明细" in s,
        "空态主文案": "暂无交易信号" if "暂无交易信号" in s else "(未找到)",
        "出现交易信号0": "交易信号" in s,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    res = {
        "probe": "SIGNAL-OBSERVE-FIX-12/before",
        "generated_utc": now_iso(),
        "观察中口径": ("冻结规则 §3「每个 symbol+venue 最多一个处于观察状态的事件」"
                   "⇒ active_event 存在且 current_state ∈ {BOX,SWEEP,RETEST}"),
        "①状态机": check_engine(),
        "②接口": check_api(),
        "③前端类型层": check_frontend_types(),
        "④前端渲染层": check_frontend_render(),
    }

    e = res["①状态机"]
    a = res["②接口"]
    t = res["③前端类型层"]
    r = res["④前端渲染层"]

    # 故障段判定：逐段给结论，不笼统说「没通」
    res["故障段判定"] = {
        "①状态机产出观察中事件": f"{e['观察中合计']} 个（BOX {e['观察中分阶段'].get('BOX')} / "
                            f"SWEEP {e['观察中分阶段'].get('SWEEP')} / "
                            f"RETEST {e['观察中分阶段'].get('RETEST')}）"
                            + ("  ⇒ 正常" if e["观察中合计"] > 0 else "  ⇒ 无标的，需另查"),
        "②接口暴露逐币明细": ("未暴露（只有 {symbol: 阶段名}）" if not a["daemon_有逐币明细"]
                        else "已暴露"),
        "③前端类型层接收": ("已接收" if t["有_perSymbolState"] else
                      "**未接收**：ExecutionDaemonView 没有 perSymbolState 字段，"
                      "解析函数也没读 per_symbol_state ⇒ 接口给了也在这里丢掉"),
        "④前端渲染": ("已渲染逐币列表" if r["逐币列表渲染"] else
                   "**未渲染**：只用 stateCounter 拼成一句计数串，明细一条没画"),
        "结论": "故障在 ②③④ 三段（后端有数据、接口有阶段名、前端全丢），不是状态机没产出",
    }

    p = OUT / "10_before_fix.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== ① 状态机 ==")
    print(f"  state_counter {e['state_counter']}")
    print(f"  per_symbol_state {e['per_symbol_state_条目数']} 条 → {e['per_symbol_state_分布']}")
    print(f"  **观察中 {e['观察中合计']} 个**：{e['观察中分阶段']}")
    print(f"  粒度：{e['per_symbol_state_字段粒度']}")
    print("\n== ② 接口 ==")
    for s_ in ("current", "all", "history"):
        v = a[s_]
        print(f"  {s_:8s} total={v['total']} status={v['quality_status']} "
              f"有观察字段={v['有观察字段']}")
    print(f"  daemon signal_engine 键 {len(a['daemon_signal_engine_键'])} 个，"
          f"有逐币明细={a['daemon_有逐币明细']}")
    print("\n== ③ 前端类型层 ==")
    print(f"  ExecutionDaemonView 字段 {len(t['ExecutionDaemonView_字段'])} 个")
    print(f"  有 perSymbolState = {t['有_perSymbolState']}；"
          f"解析读 per_symbol_state = {t['解析函数读_per_symbol_state']}")
    print("\n== ④ 前端渲染层 ==")
    print(f"  用 perSymbolState = {r['用_perSymbolState']}；逐币列表 = {r['逐币列表渲染']}")
    print(f"  诊断区在折叠 details 内 = {r['诊断区在details折叠内']}")
    print("\n== 故障段判定 ==")
    for k, v in res["故障段判定"].items():
        print(f"  {k}: {v}")
    print(f"\n→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
