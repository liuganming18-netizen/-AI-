# -*- coding: utf-8 -*-
"""UI-LINK-01 · 接口缺口复现（只读，**不发任何写请求**）。

把 README 第 4 节里 GAP-01 / GAP-02 / GAP-03 引用的每个数字变成可复算的输出，
避免「人工抄写」把字节数、行数写错。

用法：python3 gap_reverify.py [base]
  默认 base = http://127.0.0.1:8799（隔离后端；换 8787 可复验生产只读）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8799"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path: str, timeout: int = 300):
    s = time.time()
    try:
        r = _OP.open(BASE + path, timeout=timeout)
        raw = r.read()
        return {"path": path, "http": r.status, "bytes": len(raw),
                "ms": int((time.time() - s) * 1000), "body": json.loads(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        return {"path": path, "http": e.code, "bytes": len(raw),
                "ms": int((time.time() - s) * 1000), "body": body}
    except Exception as e:  # noqa: BLE001
        return {"path": path, "http": "ERR", "bytes": 0,
                "ms": int((time.time() - s) * 1000), "error": str(e)}


def main() -> int:
    out = {"base": BASE, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "gap01": [], "gap02": [], "gap03": {}}

    # GAP-01：非法 pos_id 应 400，实际 500
    for p in ("/api/v1/paper/stops/notanint?arm=3a",
              "/api/v1/paper/stops/999?arm=3a",
              "/api/v1/paper/stops/1?arm=3a"):
        r = get(p)
        out["gap01"].append({"path": p, "http": r["http"], "bytes": r["bytes"],
                             "total": (r.get("body") or {}).get("total")})

    # GAP-02：交易所段大小写敏感，写错不报错、静默变 missing
    for p in ("/api/v1/markets/binance/BTCUSDT/candles?interval=4h",
              "/api/v1/markets/BINANCE/BTCUSDT/candles?interval=4h"):
        r = get(p)
        b = r.get("body") or {}
        out["gap02"].append({"path": p, "http": r["http"], "bytes": r["bytes"],
                             "quality": (b.get("quality") or {}).get("status"),
                             "missing_reason": b.get("missing_reason")})

    # GAP-03：limit 不生效
    r = get("/api/v1/markets?limit=3")
    b = r.get("body") or {}
    out["gap03"] = {"path": "/api/v1/markets?limit=3", "http": r["http"],
                    "bytes": r["bytes"], "ms": r["ms"],
                    "items_returned": len(b.get("items") or []),
                    "limit_honored": len(b.get("items") or []) <= 3}

    out["verdict"] = {
        "gap01_bad_id_is_500": out["gap01"][0]["http"] == 500,
        "gap01_missing_is_404": out["gap01"][1]["http"] == 404,
        "gap02_uppercase_silently_missing":
            out["gap02"][1]["quality"] == "missing" and out["gap02"][1]["http"] == 200,
        "gap03_limit_ignored": not out["gap03"]["limit_honored"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
