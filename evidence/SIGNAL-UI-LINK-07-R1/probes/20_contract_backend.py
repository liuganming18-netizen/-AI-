#!/usr/bin/env python3
"""20_contract_backend.py —— `/api/v1/signals` 状态契约的后端侧验证（SIGNAL-UI-LINK-07-R1）

为什么要有这一支：
    前端要区分「正常 0 条 / 业务异常 / HTTP 错误 / 连不上」，前提是**后端把话说清楚**。
    旧实现里两条失败路径（实时层不可用 / 实时层抛异常）都把 `quality.status` 留成 `ok`，
    只往 `note` 里塞一句话 ⇒ 前端按契约判 `status` 就会把它们当「正常空列表」。
    这是「接口异常被显示为空列表」的**上游根因**，必须单独验。

做法：在**本进程内**直接调用合并函数，分别构造四种情况（不改任何文件、不启服务）：
    A 正常          → status=ok、missing_reason 为空、note 非空（note 不得被当故障）
    B 实时层不可用  → status=not_connected、missing_reason 非空
    C 实时层抛异常  → status=error、missing_reason 非空
    D 下限缺失      → 由实时层给出 no_watermark，合并层必须原样透传

输出：out/20_contract_backend.json
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVID = Path(__file__).resolve().parents[1]
OUT = EVID / "out"
BE = ROOT / "框架" / "backend"
sys.path.insert(0, str(BE))

os.environ.setdefault("LIVE_DB", str(BE / "live" / "live_signals.sqlite3"))
os.environ.setdefault("MARKET_DB", str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"))
os.environ.setdefault("PAPER_LEDGER_DIR", str(ROOT / "数据" / "paper"))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

res = {"probe": "20_contract_backend",
       "generated_real_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "cases": {}, "steps": [], "ok": False}


def step(name, ok, detail):
    res["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("[OK] " if ok else "[FAIL] ") + name + (" — " + str(detail) if detail else ""))


def view(env):
    q = env.get("quality") or {}
    return {
        "quality_status": q.get("status"),
        "quality_note_nonempty": bool((q.get("note") or "").strip()),
        "missing_reason": env.get("missing_reason"),
        "total": env.get("total"),
    }


import adapter  # noqa: E402  （只导入，不启服务：main() 在 __main__ 下才跑）

PARAMS = {"scope": ["current"], "limit": ["5"]}

# ── A 正常 ────────────────────────────────────────────────────────────────
a = view(adapter._v1_signals_merged(dict(PARAMS)))
res["cases"]["A_normal"] = a
step("A 正常：status=ok", a["quality_status"] == "ok", a["quality_status"])
step("A 正常：missing_reason 为空", a["missing_reason"] is None, repr(a["missing_reason"]))
step("A 正常：note 非空（说明字段恒有值，不得被当故障）", a["quality_note_nonempty"],
     f"note_nonempty={a['quality_note_nonempty']}")

# ── B 实时层不可用（数据源未连接）─────────────────────────────────────────
_live_backup = adapter.live
adapter.live = None
try:
    b = view(adapter._v1_signals_merged(dict(PARAMS)))
finally:
    adapter.live = _live_backup
res["cases"]["B_not_connected"] = b
step("B 未连接：status=not_connected", b["quality_status"] == "not_connected", b["quality_status"])
step("B 未连接：missing_reason 非空", bool((b["missing_reason"] or "").strip()),
     b["missing_reason"])

# ── C 实时层抛异常 ────────────────────────────────────────────────────────
_fn_backup = adapter.live.live_signals


def _boom(*_a, **_k):
    raise RuntimeError("模拟读库失败（契约测试）")


adapter.live.live_signals = _boom
try:
    c = view(adapter._v1_signals_merged(dict(PARAMS)))
finally:
    adapter.live.live_signals = _fn_backup
res["cases"]["C_error"] = c
step("C 异常：status=error", c["quality_status"] == "error", c["quality_status"])
step("C 异常：missing_reason 非空", bool((c["missing_reason"] or "").strip()), c["missing_reason"])

# ── D 有效下限缺失（实时层给 no_watermark，合并层必须透传）────────────────
def _no_watermark(params):
    return {
        "items": [], "total": 0, "limit": 5, "has_more": False,
        "as_of": None,
        "quality": {"status": "no_watermark", "age_seconds": None, "scope": "current",
                    "live_start_ms": None, "paper_epoch_ms": None,
                    "effective_floor_ms": None,
                    "note": "下限缺失时如实空态：不用当前时间重置水位、不放行旧信号"},
        "missing_reason": "有效下限(paper epoch / live_start)缺失 ⇒ 当前有效信号无法判定",
    }


adapter.live.live_signals = _no_watermark
try:
    d = view(adapter._v1_signals_merged(dict(PARAMS)))
finally:
    adapter.live.live_signals = _fn_backup
res["cases"]["D_no_watermark"] = d
step("D 下限缺失：status 透传 no_watermark", d["quality_status"] == "no_watermark",
     d["quality_status"])
step("D 下限缺失：missing_reason 透传", bool((d["missing_reason"] or "").strip()),
     d["missing_reason"])

res["ok"] = all(s["ok"] for s in res["steps"])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "20_contract_backend.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n== 结果：" + ("全部通过" if res["ok"] else "存在失败项") + " ==")
print("写出：evidence/SIGNAL-UI-LINK-07-R1/out/20_contract_backend.json")
sys.exit(0 if res["ok"] else 1)
