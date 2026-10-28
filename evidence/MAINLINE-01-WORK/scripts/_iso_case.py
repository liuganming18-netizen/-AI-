#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隔离单例执行器：接收 ISO_CASE 环境变量（JSON），只调一次 `paper_engine.open_position`。

为什么要独立子进程：`live_store` 在 **import 时**就解析 `PAPER_LEDGER_DIR` / `LIVE_DB`，
同进程内无法为不同用例切换账本；且模块级缓存会让「重载前/后」互相污染。
每个用例一个干净进程，结果以单行 JSON 打到 stdout。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

BACKEND = Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
sys.path.insert(0, str(BACKEND))


def main() -> int:
    cfg = json.loads(os.environ["ISO_CASE"])
    out = {"case": cfg.get("name"), "ok": False}
    try:
        import paper_engine as PE
        out["resolved"] = {
            "realtime_fill_mode_env": PE.realtime_fill_mode(),
            "effective_fill_mode": PE.effective_fill_mode(cfg.get("origin", "test")),
            "ledger_dir": str(PE.L.get_ledger_dir()),
            "market_db": str(PE.L.MARKET_DB),
            "effective_floor_ms": PE.L.effective_floor_ms(),
            "paper_engine_file": str(Path(PE.__file__).resolve()),
        }
        act = cfg.get("action") or "open"
        if act == "open":
            r = PE.open_position(
                cfg.get("arm", "3a"), cfg["symbol"],
                signal_uid=cfg.get("uid"),
                direction=cfg.get("direction", "long"),
                atr=cfg.get("atr", 500.0),
                stop_price=cfg.get("stop_price"),
                now_ms=cfg.get("attempt_ms"),
                event_time=cfg.get("event_time"),
                available_at=cfg.get("available_at"),
                requested_at=cfg.get("requested_at"),
                signal_payload=cfg.get("signal_payload"),
                origin=cfg.get("origin", "test"),
                pending_id=cfg.get("pending_id"),
                epoch_ms=cfg.get("epoch_ms"),
                first_request_ms=cfg.get("first_request_ms"),
                stoploss_s=cfg.get("stoploss_s", 0.05),
                trail_d=cfg.get("trail_d", 0.05),
                config_fingerprint=cfg.get("config_fingerprint", "82dec362c182"),
                model_version=cfg.get("model_version", "FT-LOW4H-EXP-V2.2"),
                process_start_ms=cfg.get("process_start_ms"),
            )
            out["result"] = r
            out["ok"] = bool(r.get("ok"))
        elif act == "advance_pending":
            r = PE.advance_pending(T=cfg.get("attempt_ms"), log=lambda *a, **k: None,
                                   process_start_ms=cfg.get("process_start_ms"))
            out["result"] = r
            out["ok"] = True
        elif act == "dump":
            out["result"] = cfg.get("dump_fn") and None
            out["ok"] = True
        else:
            out["error"] = f"unknown action {act}"
    except Exception as exc:                                   # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()
    print("ISO_RESULT " + json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
