#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D03-R1 五条契约 **真实 HTTP 往返** 验证（不是函数级直调）。

做法：起一个**只绑 127.0.0.1 随机端口**的 `ThreadingHTTPServer`，挂生产同一套
`adapter.Handler`，账本目录用 `PAPER_LEDGER_DIR` 指向临时副本 —— 生产账本不参与、
不监听固定端口 8787（避免和正在跑的服务抢端口）。

验证项（与 `verify_d03r1.py` 函数级验证互为交叉）：
  · ① 交易 API      `GET /api/v1/paper/trades?arm=3a`        → 200 且有逐笔
  · ② 零交易        `GET /api/v1/paper/trades?arm=3b`        → 200 + no_trades
  · ③ 404           `?arm=9z` / `?arm=3a&position_id=99999999` → 404
  · ④ 503           账本目录切到空目录后再请求                  → 503
  · ⑤ 权益曲线      `GET /api/v1/paper/equity?arm=3a`（含 jsonl） → 200

输出：`out/d03r1_http_verify.json`
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
SRC_LEDGER = ROOT / "数据" / "paper" / "paper_3a.sqlite3"

sys.path.insert(0, str(BACKEND))
results: list[dict] = []


def rec(name: str, ok: bool, detail) -> None:
    results.append({"case": name, "ok": bool(ok), "detail": detail})
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: "
          + json.dumps(detail, ensure_ascii=False)[:300])


def _norm(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    con = sqlite3.connect(str(dst))
    try:
        con.execute("PRAGMA journal_mode=DELETE")   # WAL 库只读打开需要 -shm，副本转 DELETE
        con.commit()
    finally:
        con.close()


def main() -> int:
    if not SRC_LEDGER.is_file():
        print(f"生产账本不存在：{SRC_LEDGER}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="d03r1http-")).resolve()
    led = tmp / "ledger"
    led.mkdir()
    for arm in ("3a", "3c", "3d"):
        _norm(SRC_LEDGER, led / f"paper_{arm}.sqlite3")

    import live_store as L
    prov = tmp / "prov"
    prov.mkdir()
    os.environ["PAPER_LEDGER_DIR"] = str(prov)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    con = L.paper_conn("3b", create=True)
    con.close()
    os.environ.pop("PAPER_LEDGER_ALLOW_INIT", None)
    _norm(prov / "paper_3b.sqlite3", led / "paper_3b.sqlite3")
    os.environ["PAPER_LEDGER_DIR"] = str(led)

    import adapter
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"隔离服务已起：127.0.0.1:{port}（账本目录 {led}）")

    def get(path: str):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            c.request("GET", path)
            r = c.getresponse()
            raw = r.read()
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:                                    # noqa: BLE001
                body = {"_raw": raw[:200].decode("utf-8", "replace")}
            return r.status, body
        finally:
            c.close()

    st, body = get("/api/v1/paper/trades?arm=3a&_cb=1")
    blk = (body.get("items") or [{}])[0]
    rec("① 交易API·真实交易", st == 200 and (blk.get("total") or 0) > 0,
        {"http": st, "total": blk.get("total"), "closed_count": blk.get("closed_count"),
         "realized_pnl_sum": blk.get("realized_pnl_sum")})

    st, body = get("/api/v1/paper/trades?arm=3b&_cb=2")
    blk = (body.get("items") or [{}])[0]
    rec("② 零交易契约", st == 200 and blk.get("no_trades") is True,
        {"http": st, "total": blk.get("total"), "no_trades": blk.get("no_trades")})

    st, body = get("/api/v1/paper/trades?arm=9z&_cb=3")
    rec("③a 404·未知臂", st == 404,
        {"http": st, "note": (body.get("quality") or {}).get("note"),
         "missing_reason": body.get("missing_reason")})

    st, body = get("/api/v1/paper/trades?arm=3a&position_id=99999999&_cb=4")
    rec("③b 404·未知持仓号", st == 404,
        {"http": st, "note": (body.get("quality") or {}).get("note")})

    st, body = get("/api/v1/paper/equity?arm=3a&_cb=5")
    blk = (body.get("items") or [{}])[0]
    rec("⑤a 权益曲线", st == 200 and (blk.get("total") or 0) > 0,
        {"http": st, "points": blk.get("total"), "last": blk.get("last")})

    st, body = get("/api/v1/paper/equity?arm=3a&format=jsonl&_cb=6")
    lines = [x for x in (body.get("jsonl") or "").splitlines() if x.strip()]
    rec("⑤b 权益 JSONL", st == 200 and bool(lines),
        {"http": st, "lines": len(lines), "content_type": body.get("content_type")})

    # ④ 503：`ledger_path()` 调用时读环境变量 ⇒ 运行中切目录即生效
    empty = tmp / "empty_ledger"
    empty.mkdir()
    os.environ["PAPER_LEDGER_DIR"] = str(empty)
    st, body = get("/api/v1/paper/trades?arm=3a&_cb=7")
    rec("④a 503·交易API账本缺失", st == 503, {"http": st})
    st, body = get("/api/v1/paper/equity?arm=3a&_cb=8")
    rec("④b 503·权益API账本缺失", st == 503, {"http": st})

    srv.shutdown()
    out = HERE / "out"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": __import__("datetime").datetime.utcnow()
               .strftime("%Y-%m-%dT%H:%M:%SZ"),
               "transport": f"http://127.0.0.1:{port}（随机端口，仅回环）",
               "ledger_dir": str(led), "production_ledger_touched": False,
               "cases": results,
               "passed": sum(1 for r in results if r["ok"]),
               "failed": sum(1 for r in results if not r["ok"]),
               "total": len(results)}
    (out / "d03r1_http_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总：{payload['passed']}/{payload['total']} 通过，{payload['failed']} 失败")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
