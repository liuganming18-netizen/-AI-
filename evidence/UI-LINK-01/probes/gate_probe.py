#!/usr/bin/env python3
"""UI-LINK-01 · 服务端新开仓总闸探针（隔离环境专用）

用途：对隔离后端做「开始 / 暂停」的真实切换，并把切换前后的**服务端状态**落成证据：
  - 控制文件原文（gate_file 的字节内容 + SHA-256）
  - 接口侧 `/api/v1/paper/status.item.order_processing.new_entry_gate`
  - 账本侧 `/api/v1/paper/accounts.quality.new_entry_gate`
  - 暂停时真正提交一次开仓，看服务端是否真的拒绝（不能只看前端按钮变灰）

只写隔离目录（/tmp/ui-link-01/），生产 8787 一律拒绝操作。

用法：
  python3 gate_probe.py show
  python3 gate_probe.py start
  python3 gate_probe.py pause
  python3 gate_probe.py try-open      # 暂停状态下真发一次开仓，验证被拒
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8799"
ISO = Path("/tmp/ui-link-01")
GATE_FILE = ISO / "live" / "paper_new_entry_gate.json"
PROD_GATE = Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend/live/paper_new_entry_gate.json")

# 本机有 HTTP_PROXY 但没有 no_proxy ⇒ 必须显式禁用代理，否则 127.0.0.1 会被发给代理
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    try:
        r = _op.open(BASE + path, timeout=30)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as e:
        return "ERR", str(e)


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        r = _op.open(req, timeout=30)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None
    except Exception as e:
        return "ERR", str(e)


def gate_file_state():
    if not GATE_FILE.exists():
        return {"exists": False, "path": str(GATE_FILE)}
    raw = GATE_FILE.read_bytes()
    try:
        body = json.loads(raw)
    except Exception:
        body = None
    return {"exists": True, "path": str(GATE_FILE), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "body": body}


def snapshot(tag):
    _, st = get("/api/v1/paper/status")
    _, ac = get("/api/v1/paper/accounts")
    proc = ((st.get("item") or {}).get("order_processing") or {}).get("new_entry_gate") if isinstance(st, dict) else None
    led = ((ac.get("quality") or {}).get("new_entry_gate")) if isinstance(ac, dict) else None
    return {
        "tag": tag,
        "gate_file": gate_file_state(),
        "status_side_gate": proc,
        "ledger_side_gate": led,
        "order_processing_status": ((st.get("item") or {}).get("order_processing") or {}).get("status") if isinstance(st, dict) else None,
    }


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "show").strip()
    if not str(GATE_FILE).startswith("/tmp/ui-link-01"):
        print(json.dumps({"error": "拒绝操作非隔离目录的总闸文件", "gate_file": str(GATE_FILE)}, ensure_ascii=False))
        return 2

    if action == "show":
        print(json.dumps(snapshot("show"), ensure_ascii=False, indent=1))
        return 0

    if action in ("start", "pause"):
        code, body = post("/api/v1/paper/toggle_gate",
                          {"action": action, "set_by": "ui-link-01-e2e",
                           "note": "UI-LINK-01 隔离环境端到端验证"})
        out = {"action": action, "http": code,
               "response_gate": ((body or {}).get("items") or [{}])[0] if isinstance(body, dict) else body,
               "response_missing_reason": (body or {}).get("missing_reason") if isinstance(body, dict) else None,
               "after": snapshot("after_" + action)}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if code == 200 else 1

    if action == "try-open":
        # 用种子信号真发一次开仓；暂停时服务端必须拒绝，且不得写挂单
        _, sig = get("/api/v1/signals?scope=current")
        items = (sig or {}).get("items") or []
        if not items:
            print(json.dumps({"error": "当前信号区为空，无法发起开仓"}, ensure_ascii=False))
            return 1
        s = items[0]
        code, body = post("/api/v1/paper/orders", {
            "action": "open", "arm": s.get("arm") or "3a",
            "symbol": s.get("symbol"), "direction": s.get("direction") or "long",
            "signal_uid": s.get("signal_id"),
        })
        _, ac = get("/api/v1/paper/accounts")
        out = {
            "http": code,
            "signal_uid": s.get("signal_id"),
            "symbol": s.get("symbol"),
            "quality": (body or {}).get("quality") if isinstance(body, dict) else None,
            "missing_reason": (body or {}).get("missing_reason") if isinstance(body, dict) else None,
            "items": (body or {}).get("items") if isinstance(body, dict) else None,
            "ledger_n_pending_after": ((ac.get("quality") or {}).get("n_pending_orders")) if isinstance(ac, dict) else None,
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(json.dumps({"error": "未知动作，可用：show/start/pause/try-open"}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
