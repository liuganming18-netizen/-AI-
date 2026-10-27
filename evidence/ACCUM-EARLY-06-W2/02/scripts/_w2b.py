# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜共用：原 7 事件同输入、双版本回放、逐事件取证

纪律
----
· **输入一字不改**：7 个事件的 (方案, 标的, 箱体 formation_ms)、两段行情范围、
  `max_boxes=2`、`warmup_days=60`、测试配置 `fixture_rules_05.json` 全部沿用 05-E / 05-E-R1；
  唯一「接口适配」是把修前代码作为独立模块加载（见 `load_pre_fix`），并把它的模块级
  `PROJECT` 指回真实工程根——**只改模块常量，不改任何规则/阈值/状态机**。
· **只写本窗**：观察库落在 `runs/`，账本目录显式指向 `runs/ledger`，不碰生产。
· **不宣称**：这里只证明「同一输入下两个版本的轨迹差异」，不是自然成交、不是策略绩效。
"""
from __future__ import annotations

import calendar
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
W2B = HERE.parents[1]                     # evidence/ACCUM-EARLY-06-W2/02
PROJECT = HERE.parents[4]
BACKEND = PROJECT / "框架" / "backend"
OUT = W2B / "out"
RUNS = W2B / "runs"
CONFIG_DIR = W2B / "config"

for _p in (str(PROJECT), str(BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

H = 3_600_000

#: 测试配置（05-B 发布、05-E-R1 使用的那一份，**逐字节相同**）
CONFIG_PATH = CONFIG_DIR / "fixture_rules_05.json"
CONFIG_SHA_EXPECT = "f322a940cea1e69d6ebe048f100b1e0b3b8234c1d42ef45a1f9b25bfd55152bd"

#: 修前代码（06-W2/01 逆向重建、sha 逐位等于修前）与修后代码
PRE_FIX_PATH = RUNS / "pre_fix_accum_early_entry.py"
PRE_FIX_SHA_EXPECT = "d96ad60eca61f7e53e273414196100ac21b33650a19480beafb0eb60c4d41a52"
POST_FIX_PATH = BACKEND / "accum_early_entry.py"
POST_FIX_SHA_EXPECT = "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017"

#: 05-E-R1 当时被验版本（只作第三方对照列，不重跑）
VERSION_05E_R1 = "f7056980e0cef0cac7be31ee76fee19357ce33b7704b37add8dc4de83816c8c6"

#: 两段行情范围（与 05-E-R1 脚本逐字相同）
RUNS_SPEC = [
    {"symbol": "SOLUSDT", "start": "2026-08-01T00:00:00Z", "end": "2026-09-05T00:00:00Z",
     "max_boxes": 2, "warmup_days": 60, "layer": "冷档（05-E 记为冷档区间）"},
    {"symbol": "AVAXUSDT", "start": "2026-09-12T00:00:00Z", "end": "2026-10-25T00:00:00Z",
     "max_boxes": 2, "warmup_days": 60, "layer": "热库（05-E 记为热库区间）"},
]

#: 05-E 原 7 事件（从 05-E/out/03_event_trace.json 读出后固化在这里，脚本会回读校验）
EVENT_IDS = [
    "ev_4a_binance_SOLUSDT_box_SOLUSDT_1785556799999_1785556799999",
    "ev_4b_binance_SOLUSDT_box_SOLUSDT_1785556799999_1785556799999",
    "ev_4c_binance_SOLUSDT_box_SOLUSDT_1785556799999_1785556799999",
    "ev_4a_binance_AVAXUSDT_box_AVAXUSDT_1789185599999_1789185599999",
    "ev_4b_binance_AVAXUSDT_box_AVAXUSDT_1789185599999_1789185599999",
    "ev_4c_binance_AVAXUSDT_box_AVAXUSDT_1789185599999_1789185599999",
    "ev_4a_binance_AVAXUSDT_box_AVAXUSDT_1789473599999_1789473599999",
]

_EV_RE = re.compile(r"^ev_(4[abc])_binance_(.+?)_(box_.+?_\d+)_(\d+)$")


def parse_event_id(eid: str) -> dict:
    m = _EV_RE.match(eid)
    if not m:
        raise ValueError(f"事件号格式不符：{eid}")
    return {"event_id": eid, "scheme": m.group(1), "symbol": m.group(2),
            "box_version_id": m.group(3), "formation_ms": int(m.group(4))}


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def ms_of(s: str) -> int:
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def sha256_file(p) -> dict:
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    h = hashlib.sha256()
    n = 0
    with p.open("rb") as fh:
        while True:
            b = fh.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
            n += len(b)
    return {"path": str(p), "exists": True, "bytes": p.stat().st_size,
            "sha256": h.hexdigest(), "hashed_bytes": n}


# ══════════════════════════════════════════════════════════════════════════
# 一、加载两个版本
# ══════════════════════════════════════════════════════════════════════════
def load_pre_fix():
    """把修前代码作为**独立模块**加载（接口适配，见 README §2）。

    适配只有三条，且都不碰规则：
      ① 模块名不同（`accum_early_entry_pre06`），避免覆盖在用的模块；
      ② 模块级 `PROJECT` / `RESEARCH_ROOT` / `DEFAULT_CONFIG_PATH` / `OBSERVE_DB`
         指回真实工程根（修前文件放在 `runs/` 下，`parents[2]` 解析不到工程根）；
      ③ 加载后立刻核 sha256 必须等于修前值，否则拒绝使用。
    """
    sha = sha256_file(PRE_FIX_PATH)["sha256"]
    if sha != PRE_FIX_SHA_EXPECT:
        raise SystemExit(f"修前代码身份不符：{sha} != {PRE_FIX_SHA_EXPECT}")
    spec = importlib.util.spec_from_file_location("accum_early_entry_pre06",
                                                  str(PRE_FIX_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.PROJECT = PROJECT
    mod.RESEARCH_ROOT = PROJECT / "数据" / "research" / "ACCUM-EARLY-05"
    mod.DEFAULT_CONFIG_PATH = mod.RESEARCH_ROOT / "approved_rules.json"
    mod.OBSERVE_DB = mod.RESEARCH_ROOT / "observe.sqlite3"
    return mod


def load_post_fix():
    import accum_early_entry as mod
    sha = sha256_file(POST_FIX_PATH)["sha256"]
    if sha != POST_FIX_SHA_EXPECT:
        raise SystemExit(f"修后代码身份不符：{sha} != {POST_FIX_SHA_EXPECT}")
    return mod


# ══════════════════════════════════════════════════════════════════════════
# 二、隔离环境
# ══════════════════════════════════════════════════════════════════════════
def isolated_env(tag: str) -> dict:
    """账本/实时库/闸门全部落在本窗 `runs/<tag>/`，行情源只读。"""
    d = RUNS / tag
    (d / "ledger").mkdir(parents=True, exist_ok=True)
    os.environ["PAPER_LEDGER_DIR"] = str(d / "ledger")
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["LIVE_DB"] = str(d / "live_signals.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(d / "gate.json")
    os.environ["WX_MARKET_MERGE"] = "1"
    os.environ["ACCUM05_CONFIG"] = str(CONFIG_PATH)
    os.environ["ACCUM05_FIXTURE_OK"] = "1"
    for k in ("ACCUM04_CONFIG", "ACCUM04_FIXTURE_OK"):
        os.environ.pop(k, None)
    (d / "gate.json").write_text(json.dumps({"allow_new_entries": False,
                                             "set_by": "06-W2/02 回放：不开仓"}),
                                 encoding="utf-8")
    return {"run_dir": str(d), "ledger_dir": str(d / "ledger"),
            "live_db": str(d / "live_signals.sqlite3"),
            "market_db": os.environ.get("MARKET_DB"), "fixture_env": "1"}


# ══════════════════════════════════════════════════════════════════════════
# 三、跑一个版本
# ══════════════════════════════════════════════════════════════════════════
def run_version(mod, tag: str) -> dict:
    env = isolated_env(tag)
    cfg = mod.load_config(CONFIG_PATH)
    db = RUNS / f"observe_{tag}.sqlite3"
    if db.exists():
        db.unlink()
    store = mod.open_store(db)
    feed = mod.DataFeed()
    scans = {}
    for spec in RUNS_SPEC:
        res = mod.scan_symbol(spec["symbol"], cfg, feed, store,
                              start_ms=ms_of(spec["start"]), end_ms=ms_of(spec["end"]),
                              max_boxes=spec["max_boxes"], open_candidates=False,
                              warmup_days=spec["warmup_days"])
        scans[spec["symbol"]] = {
            "symbol": spec["symbol"], "start": spec["start"], "end": spec["end"],
            "max_boxes": spec["max_boxes"], "warmup_days": spec["warmup_days"],
            "boxes_formed": res["boxes_formed"], "boxes_used": res["boxes_used"],
            "actions": res["actions"], "n_candidates": len(res["candidates"]),
            "cooldown_ms": res["cooldown_ms"], "skipped": res["skipped"],
            "bars": {k: v for k, v in res["bars"].items() if not k.startswith("available")},
            "candidates": [{k: c.get(k) for k in
                            ("scheme", "symbol", "confirm_close_ms", "status",
                             "status_reason", "resistance", "entry_bar_open_ms")}
                           for c in res["candidates"]],
            "events": res["events"],
        }

    events = {}
    for r in store.con.execute(
            "SELECT event_id, scheme, symbol, box_version_id, state, closed_reason, "
            "structure_seen_ms, expires_at_ms, old_low FROM observe_event"):
        d = dict(r)
        events[d["event_id"]] = d
    trans = {}
    for r in store.con.execute(
            "SELECT event_id, from_state, to_state, at_ms, reason, payload_json "
            "FROM observe_transition ORDER BY rowid"):
        d = dict(r)
        trans.setdefault(d["event_id"], []).append(d)
    cands = {}
    for r in store.con.execute(
            "SELECT candidate_id, event_id, scheme, confirm_close_ms, status, "
            "status_reason, conditions_json FROM accum05_candidate"):
        d = dict(r)
        cands.setdefault(d["event_id"], []).append(d)
    gaps = {}
    for r in store.con.execute(
            "SELECT event_id, field, reason, from_ms, to_ms FROM observe_data_gap"):
        d = dict(r)
        gaps.setdefault(d["event_id"], []).append(d)
    store.close()

    return {"tag": tag, "code": sha256_file(
                PRE_FIX_PATH if tag == "pre" else POST_FIX_PATH),
            "env": env, "config": {k: cfg.get(k) for k in
                                   ("path", "kind", "ready", "open_allowed",
                                    "open_scope", "config_sha256", "blocked_by")},
            "scans": scans, "events": events, "transitions": trans,
            "candidates": cands, "gaps": gaps}


# ══════════════════════════════════════════════════════════════════════════
# 四、逐事件取证
# ══════════════════════════════════════════════════════════════════════════
def event_record(run: dict, eid: str) -> dict:
    """把某个原事件在这一次运行里的命运取出来（找不到就是 NOT_FOUND，不编终态）。"""
    meta = parse_event_id(eid)
    ev = run["events"].get(eid)
    if not ev:
        # 同 (scheme, symbol, box) 但事件号不同 ⇒ 也算「未按原身份建档」
        alt = [e for e in run["events"].values()
               if e["scheme"] == meta["scheme"] and e["symbol"] == meta["symbol"]
               and e["box_version_id"] == meta["box_version_id"]]
        return {**meta, "found": False, "state": "NOT_FOUND",
                "closed_reason": None, "same_box_other_event_id": [a["event_id"] for a in alt],
                "transitions": [], "n_candidates": 0, "candidates": [],
                "sweep_absorption": None, "gaps": []}

    trs = run["transitions"].get(eid, [])
    cs = run["candidates"].get(eid, [])
    sweep_payload = None
    for t in trs:
        if t["to_state"] == "SWEEP":
            try:
                sweep_payload = json.loads(t["payload_json"] or "{}")
            except Exception:                                  # noqa: BLE001
                sweep_payload = None
            break
    return {
        **meta, "found": True, "state": ev["state"], "closed_reason": ev["closed_reason"],
        "old_low": ev["old_low"], "expires_at_ms": ev["expires_at_ms"],
        "transitions": [{"from": t["from_state"], "to": t["to_state"],
                         "at_ms": t["at_ms"], "time_utc": iso(t["at_ms"]),
                         "reason": t["reason"]} for t in trs],
        "n_candidates": len(cs),
        "candidates": [{"candidate_id": c["candidate_id"],
                        "confirm_close_ms": c["confirm_close_ms"],
                        "confirm_utc": iso(c["confirm_close_ms"]),
                        "status": c["status"], "status_reason": c["status_reason"],
                        "conditions": _conds(c["conditions_json"])} for c in cs],
        "sweep_absorption": (None if not sweep_payload else {
            "sweep_bar_i": sweep_payload.get("sweep_bar_i"),
            "absorption_bar_close_ms": sweep_payload.get("absorption_bar_close_ms"),
            "absorption_bar_close_utc": iso(sweep_payload.get("absorption_bar_close_ms")),
            "absorption_ok": (sweep_payload.get("absorption") or {}).get("ok"),
            "absorption_reason": ((sweep_payload.get("absorption") or {}).get("detail") or {}).get("reason"),
        }),
        "gaps": run["gaps"].get(eid, []),
    }


def _conds(js):
    try:
        rows = json.loads(js or "[]")
    except Exception:                                          # noqa: BLE001
        return {}
    return {c.get("name"): c.get("result") for c in rows if isinstance(c, dict)}


def not_found_reason(mod, run: dict, eid: str) -> dict:
    """解释「为什么这个箱体没建档」：箱体是否达标 + 同方案前序事件是否占位 + 箱体配额是否用尽。

    三者都查，避免把「未找到」写成「已失效」。
    """
    meta = parse_event_id(eid)
    spec = next(s for s in RUNS_SPEC if s["symbol"] == meta["symbol"])
    feed = mod.DataFeed()
    bars4 = feed.load("4h", meta["symbol"], ms_of(spec["start"]) - spec["warmup_days"] * 86_400_000,
                      ms_of(spec["end"]))
    boxes = mod.build_boxes(bars4)
    hit = [b for b in boxes if int(b["formation_ms"]) == meta["formation_ms"]]
    box_formed = bool(hit and hit[0]["formed"])
    box_row = ({"formed": hit[0]["formed"], "skip": hit[0].get("skip"),
                "width_atr": hit[0].get("width_atr"), "er": hit[0].get("er"),
                "atr": hit[0].get("atr"),
                "formation_utc": iso(hit[0].get("formation_ms"))} if hit else None)
    feed.close()

    # 同方案同标的前序事件：在 formation_ms 时刻是否还活着
    occupied = []
    for ev in run["events"].values():
        if ev["scheme"] != meta["scheme"] or ev["symbol"] != meta["symbol"]:
            continue
        trs = run["transitions"].get(ev["event_id"], [])
        last_ms = max([t["at_ms"] for t in trs] or [ev["structure_seen_ms"]])
        if ev["structure_seen_ms"] <= meta["formation_ms"] <= last_ms:
            occupied.append({"event_id": ev["event_id"], "state": ev["state"],
                             "last_transition_utc": iso(last_ms),
                             "structure_seen_utc": iso(ev["structure_seen_ms"])})

    scan = run["scans"].get(meta["symbol"], {})
    used, cap = scan.get("boxes_used"), scan.get("max_boxes")

    if not hit:
        verdict = "箱体不在 build_boxes 结果里（预热/窗口范围内未产出该 formation）"
    elif not box_formed:
        verdict = "箱体未达标（build_boxes 判 formed=false）"
    elif occupied:
        verdict = "箱体达标但被同方案前序事件占位（当根三个方案都不空闲 ⇒ 未 spawn）"
    elif used is not None and cap is not None and used >= cap:
        verdict = f"箱体达标、无占位，但 max_boxes={cap} 已用尽（boxes_used={used}）"
    else:
        verdict = "箱体达标、无占位、未达配额 ⇒ 需另查（不许当成已失效）"

    return {"box_row_exists": bool(hit), "box_formed": box_formed, "box_row": box_row,
            "occupied_by": occupied, "boxes_used": used, "max_boxes": cap,
            "verdict": verdict,
            "label_rule": "NOT_FOUND 只表示「本次运行没有按原身份建档」，不等于 INVALIDATED"}


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str),
                 encoding="utf-8")
    print(json.dumps({"written": str(p), "bytes": p.stat().st_size}, ensure_ascii=False))
    return p


def load_out(name: str):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
