# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜01 成交/退出链路的行情读取路径普查。

工单第 1 条要求「查全路径，不只修一个函数」，所以这里做两件事：

A **静态普查**：用语法树扫 `框架/backend/*.py`，把每一处
  · 裸 SQL 里出现 `timeframe='15m'` / `timeframe='4h'` 的位置，
  · 以及调用 `closed_15m / bar_at_or_after / bar_before / last_closed_15m / last_closed_4h` 的位置，
  全部列出来，并判定它属于哪一类：
    `合并派发内`（在四个取数函数里，已被合并门面覆盖）
    `绕过取数函数`（自己写 SQL 查 candles ⇒ 只能读热库）
    `取数函数调用`（自动继承合并能力）
  这样「改了哪几处、还剩哪几处」是清单，不是印象。

B **运行期取证**：在合并模式下跑一遍真实事件链，读 `MergedReader.passthrough`
  ——凡是没有走合并、直接落到热库的 SQL 都会被记账。如果清单里出现
  `FROM candles ... timeframe='15m'`，说明还有路径没接上（本单即为缺陷）。

输出：out/01_读取路径普查.json
"""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-03-B" / "out"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(HERE.parent))

import _harness as H                                          # noqa: E402

READ_FUNCS = {"closed_15m", "bar_at_or_after", "bar_before",
              "last_closed_15m", "last_closed_4h"}
#: 这五个函数内部自己写 SQL 是**正常**的（它们就是取数函数本身）
DISPATCH_OWNERS = {"closed_15m", "bar_at_or_after", "bar_before",
                   "last_closed_15m", "last_closed_4h", "latest_closed_detail"}

#: 绕过点的人工登记（**结论 + 具体修改点**，供协调；本单不改这些文件）
#: `on_fill_chain` 的判断依据是「这段 SQL 的结果会不会决定成交价或退出价」。
BYPASS_META = {
    "框架/backend/paper_loop.py::execute_cycle": {
        "on_fill_chain": True,
        "role": "15m 明细周期退出扫描（每轮取最近一根已收盘 15m 给 check_position_exit_on_bar）",
        "change": ("把 `con_mkt.execute(\"SELECT ... FROM candles ... timeframe='15m' ...\")`"
                   "（第 197、206 行两段）换成 `PE.latest_closed_detail(mkt, sym, min_t, now)`；"
                   "该函数本单已加进 paper_engine，语义与原 SQL 逐字相同，只是多一条合并分支。"),
    },
    "框架/backend/unified_runtime.py::execute_cycle": {
        "on_fill_chain": True,
        "role": "同上：15m 明细退出扫描（统一执行守护器每轮）",
        "change": ("把第 2369、2378 行两段 SQL 换成 `PE.latest_closed_detail(mkt, sym, min_t, now_ms)`；"
                   "其余不动。"),
    },
    "框架/backend/live_api.py::paper_advance_trailing_post": {
        "on_fill_chain": True,
        "role": "页面手工推进跟踪止损（会直接决定止损线）",
        "change": ("把第 1182、1188 行两段 SQL 换成 `PE.last_closed_4h(con_mkt, sym, upto_ms)`"
                   "（它已在四个取数函数里，自动继承合并能力）；"
                   "入参 `con_mkt` 由 `L.market_conn()` 改为 `PE.open_market()`。"),
    },
    "框架/backend/unified_runtime.py::idle_breakdown": {
        "on_fill_chain": False,
        "role": "诊断展示（水位/断流说明），不参与成交与退出定价",
        "change": "可不改；若要在隔离回放里也看到合并口径，再换成取数函数。",
    },
}


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def scan_file(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    # 每个函数定义的行区间
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))

    def owner_of(lineno):
        best = None
        for name, a, b in funcs:
            if a <= lineno <= b and (best is None or a >= best[1]):
                best = (name, a, b)
        return best[0] if best else "<module>"

    hits = []
    for node in ast.walk(tree):
        # ① 裸 SQL：任何字符串常量里带 timeframe='15m'/'4h'
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if "timeframe='15m'" in v or "timeframe='4h'" in v:
                ln = node.lineno
                owner = owner_of(ln)
                hits.append({
                    "kind": "sql_literal",
                    "file": str(path.relative_to(PROJECT)),
                    "line": ln,
                    "owner": owner,
                    "classified": ("合并派发内" if owner in DISPATCH_OWNERS else "绕过取数函数"),
                    "snippet": (lines[ln - 1].strip()[:120] if ln - 1 < len(lines) else v[:120]),
                })
        # ② 取数函数调用
        if isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name in READ_FUNCS:
                ln = node.lineno
                owner = owner_of(ln)
                if owner in DISPATCH_OWNERS:
                    continue                    # 定义处不算调用
                hits.append({
                    "kind": "read_func_call",
                    "file": str(path.relative_to(PROJECT)),
                    "line": ln,
                    "owner": owner,
                    "callee": name,
                    "classified": "取数函数调用",
                    "snippet": (lines[ln - 1].strip()[:120] if ln - 1 < len(lines) else ""),
                })
    return hits


def main() -> int:
    rep = {"probe": "01_读取路径普查", "generated_at_utc": iso(time.time() * 1000),
           "files_scanned": [], "static": [], "by_file": {}, "summary": {}, "runtime": {}}

    for p in sorted(BACKEND.glob("*.py")):
        rep["files_scanned"].append(p.name)
        rep["static"].extend(scan_file(p))

    for h in rep["static"]:
        rep["by_file"].setdefault(h["file"], []).append(
            {k: h[k] for k in ("line", "kind", "owner", "classified") if k in h}
            | ({"callee": h["callee"]} if "callee" in h else {}))

    # ── 分类汇总：哪些是「只能读热库」的真绕过点 ─────────────────────────────
    bypass = [h for h in rep["static"] if h["classified"] == "绕过取数函数"]
    # 同一函数里成组的 4 行 SQL 视为一处（candles + candle_versions 兜底）
    by_owner = {}
    for h in bypass:
        key = f"{h['file']}::{h['owner']}"
        by_owner.setdefault(key, {"file": h["file"], "owner": h["owner"],
                                  "lines": [], "kinds": set()})
        by_owner[key]["lines"].append(h["line"])
        by_owner[key]["kinds"].add(h["kind"])
    bypass_points = []
    for k, v in sorted(by_owner.items()):
        meta = BYPASS_META.get(k, {})
        bypass_points.append({
            "where": k, "lines": sorted(set(v["lines"])),
            "kinds": sorted(v["kinds"]),
            "covered_by_merge": False,
            "on_fill_chain": meta.get("on_fill_chain"),
            "role": meta.get("role", "未登记"),
            "coordination_change": meta.get("change"),
            "note": ("自己写 SQL 查 candles ⇒ 只读热库。属共享入口，本单按工单要求"
                     "只登记具体修改点、不擅自改动。"),
        })
    rep["summary"] = {
        "sql_literal_hits": sum(1 for h in rep["static"] if h["kind"] == "sql_literal"),
        "read_func_calls": sum(1 for h in rep["static"] if h["kind"] == "read_func_call"),
        "bypass_points": len(bypass_points),
        "bypass_detail": bypass_points,
        "conclusion": ("成交/退出链路上还有自写 SQL 的取数点，它们不受合并门面影响；"
                       "这些点在 `paper_loop.py` 与 `unified_runtime.py` 里，属共享入口，"
                       "本单按工单要求只报具体修改点、不擅自改动。"),
    }

    # ── 运行期：合并模式下跑真实事件，看有没有「静默回落热库」的 15m/4h SQL ────
    ev = H.EVENTS[0]
    try:
        w = H.run_job(H.event_job(ev, mode="merge"), "01_census_merge", mode="merge")
        prov = w.get("market_provenance") or {}
        p15 = (prov.get("15m") or {})
        p4h = (prov.get("4h") or {})
        pt_all = list(p15.get("passthrough_sql") or []) + list(p4h.get("passthrough_sql") or [])
        bad = [x for x in pt_all
               if "FROM candles" in x["sql"].upper() or "FROM CANDLE_VERSIONS" in x["sql"].upper()]
        rep["runtime"] = {
            "event": ev["key"], "symbol": ev["symbol"],
            "merge_enabled": (w.get("market_mode") or {}).get("merge_enabled"),
            "verdict": w.get("verdict"),
            "15m": {"reads": p15.get("reads"),
                    "returned_by_source": p15.get("returned_by_source"),
                    "returned_available_at_missing": p15.get("returned_available_at_missing"),
                    "unbounded_latest_calls": p15.get("unbounded_latest_calls"),
                    "passthrough_sql_count": p15.get("passthrough_sql_count"),
                    "cache_loads": p15.get("cache_loads")},
            "4h": {"reads": p4h.get("reads"),
                   "returned_by_source": p4h.get("returned_by_source"),
                   "returned_available_at_missing": p4h.get("returned_available_at_missing"),
                   "passthrough_sql_count": p4h.get("passthrough_sql_count")},
            "passthrough_samples": pt_all[:8],
            "passthrough_candle_sql": bad,
            "passthrough_candle_sql_count": len(bad),
            "judgement": ("通过：链路里没有 K 线 SQL 落到热库穿透（穿透只用于资金费等非 K 线表）"
                          if not bad else
                          "不通过：仍有 K 线 SQL 直接落热库，说明还有路径没接上"),
        }
    except Exception as exc:                                 # noqa: BLE001
        rep["runtime"] = {"error": f"{type(exc).__name__}: {exc}"}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "01_读取路径普查.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"bypass_points": bypass_points,
                      "runtime_judgement": rep["runtime"].get("judgement"),
                      "passthrough_candle_sql": rep["runtime"].get("passthrough_candle_sql_count")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
