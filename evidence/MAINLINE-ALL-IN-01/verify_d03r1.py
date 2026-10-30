#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D03-R1 五条契约隔离验证（真实交易 / 零交易 / 404 / 503 / 权益曲线）。

**隔离纪律**
  · 只在临时目录里跑：把生产账本 `数据/paper/paper_3a.sqlite3` **复制**一份过去，
    用 `PAPER_LEDGER_DIR` 指向副本；生产账本一个字节都不改。
  · 「零交易」样本 = 同一份副本再复制成 3b，并**在副本内**清空 `paper_realized`，
    使该臂「账户在、但没有平仓记录」。
  · 「503」样本 = 把 `PAPER_LEDGER_DIR` 指向一个空目录（四臂库都不存在）。
  · 不启动生产服务、不碰 `live_signal`、不写任何账本行。

用法：`/usr/bin/python3 evidence/MAINLINE-ALL-IN-01/verify_d03r1.py`
输出：`out/d03r1_contract_verify.json`（本脚本自己写，路径相对本文件）
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
SRC_LEDGER = ROOT / "数据" / "paper" / "paper_3a.sqlite3"

sys.path.insert(0, str(BACKEND))

results: list[dict] = []


def rec(name: str, ok: bool, detail) -> None:
    results.append({"case": name, "ok": bool(ok), "detail": detail})
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + json.dumps(detail,
          ensure_ascii=False)[:400])


def main() -> int:
    if not SRC_LEDGER.is_file():
        print(f"生产账本不存在，无法做真实样本验证：{SRC_LEDGER}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="d03r1-")).resolve()
    led = tmp / "ledger"
    led.mkdir()

    import live_store as L                                       # noqa: E402

    # 四臂副本：3a/3c/3d 保持原样（真实交易样本）。
    #
    # 注意（本轮踩过的坑）：生产账本是 WAL 模式，`sqlite3.connect("file:...?mode=ro")`
    # 打开 WAL 库时需要 `-shm`；只复制主文件会让只读打开直接报
    # `unable to open database file`（实测复现）。所以副本一律转成 `journal_mode=DELETE`
    # ——**只改副本**，生产库不动，之后 `ro=True` 才能如实读到行。
    def _copy_and_normalize(src: Path, dst: Path) -> None:
        shutil.copy2(src, dst)
        con = sqlite3.connect(str(dst))
        try:
            con.execute("PRAGMA journal_mode=DELETE")
            con.commit()
        finally:
            con.close()

    for arm in ("3a", "3c", "3d"):
        _copy_and_normalize(SRC_LEDGER, led / f"paper_{arm}.sqlite3")

    # 3b = **零交易**样本：用官方隔离建档路径（`PAPER_LEDGER_ALLOW_INIT=1`）新建一个
    # 自洽的空账户，再复制进样本目录。
    #
    # 为什么不能「复制 3a 再删 paper_realized」：删行会让 `paper_account.cash` 与
    # `paper_realized` 求和不再自洽 ⇒ `verify_ledger_identity` 抛
    # `LedgerIdentityMismatch` ⇒ 变成 503（本轮实测踩到）。零交易必须由**自洽空账本**表达。
    prov = tmp / "prov"
    prov.mkdir()
    os.environ["PAPER_LEDGER_DIR"] = str(prov)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    con = L.paper_conn("3b", create=True)
    con.close()
    os.environ.pop("PAPER_LEDGER_ALLOW_INIT", None)
    _copy_and_normalize(prov / "paper_3b.sqlite3", led / "paper_3b.sqlite3")

    os.environ["PAPER_LEDGER_DIR"] = str(led)

    import live_api as LA                                        # noqa: E402

    # ── ① 交易 API：真实交易 ────────────────────────────────────────────────
    body, st = LA.paper_trades({"arm": ["3a"]})
    blk = (body.get("items") or [{}])[0]
    rec("① 交易API·真实交易(arm=3a)", st == 200 and blk.get("total", 0) > 0,
        {"status": st, "total": blk.get("total"),
         "closed_count": blk.get("closed_count"),
         "realized_pnl_sum": blk.get("realized_pnl_sum"),
         "first_trade_keys": sorted((blk.get("trades") or [{}])[0].keys())[:8],
         "no_trades": blk.get("no_trades")})

    # ── ② 零交易契约：200 + 空列表 + no_trades，不得 404/500 ────────────────
    body, st = LA.paper_trades({"arm": ["3b"]})
    blk = (body.get("items") or [{}])[0]
    rec("② 零交易契约(arm=3b)", st == 200 and blk.get("no_trades") is True
        and blk.get("total") == 0,
        {"status": st, "total": blk.get("total"), "no_trades": blk.get("no_trades"),
         "quality_status": (body.get("quality") or {}).get("status")})

    # ── ③ 404 契约：未知臂 ─────────────────────────────────────────────────
    body, st = LA.paper_trades({"arm": ["9z"]})
    rec("③a 404·未知臂", st == 404 and (body.get("quality") or {}).get("note") == "unknown_arm",
        {"status": st, "quality_note": (body.get("quality") or {}).get("note"),
         "missing_reason": body.get("missing_reason")})

    # ── ③ 404 契约：未知持仓号 ─────────────────────────────────────────────
    body, st = LA.paper_trades({"arm": ["3a"], "position_id": ["99999999"]})
    rec("③b 404·未知持仓号", st == 404,
        {"status": st, "quality_note": (body.get("quality") or {}).get("note"),
         "missing_reason": body.get("missing_reason")})

    # ── ⑤ 权益曲线 ─────────────────────────────────────────────────────────
    body, st = LA.paper_equity({"arm": ["3a"]})
    blk = (body.get("items") or [{}])[0]
    pts = blk.get("points") or []
    asc = all(pts[i]["ts"] <= pts[i + 1]["ts"] for i in range(len(pts) - 1)) if pts else None
    rec("⑤a 权益曲线(arm=3a)", st == 200 and len(pts) > 0 and asc is True,
        {"status": st, "points": len(pts), "ascending": asc,
         "initial_equity": blk.get("initial_equity"),
         "last": blk.get("last")})

    body, st = LA.paper_equity({"arm": ["3a"], "format": ["jsonl"]})
    lines = [ln for ln in (body.get("jsonl") or "").splitlines() if ln.strip()]
    parsed_ok = True
    try:
        for ln in lines:
            json.loads(ln)
    except Exception:                                            # noqa: BLE001
        parsed_ok = False
    rec("⑤b 权益 JSONL(arm=3a)", st == 200 and lines and parsed_ok,
        {"status": st, "lines": len(lines), "parse_ok": parsed_ok,
         "content_type": body.get("content_type"),
         "sample": lines[0] if lines else None})

    # ── ④ 503 契约：账本不可用 ─────────────────────────────────────────────
    # `ledger_path()` 用的是 `get_ledger_dir()`（**调用时**读环境变量），所以这里切目录
    # 是真生效的；但 `_ledger_source()` 的说明文字用的是**导入时**缓存的常量，
    # 于是 note 里会印出生产路径 —— 属**文案陈旧**，不影响状态码。故一并记录真实目录。
    empty = tmp / "empty_ledger"
    empty.mkdir()
    os.environ["PAPER_LEDGER_DIR"] = str(empty)
    real_dir = str(L.get_ledger_dir())
    real_db_exists = L.ledger_path("3a").exists()
    body, st = LA.paper_trades({"arm": ["3a"]})
    rec("④a 503·交易API账本缺失", st == 503 and real_db_exists is False,
        {"status": st, "actual_ledger_dir": real_dir, "db_exists": real_db_exists,
         "quality_note": (body.get("quality") or {}).get("note")})
    body, st = LA.paper_equity({})
    rec("④b 503·权益API账本缺失", st == 503 and real_db_exists is False,
        {"status": st, "actual_ledger_dir": real_dir, "db_exists": real_db_exists,
         "quality_note": (body.get("quality") or {}).get("note")})

    # ── 反例：状态码必须能区分「账户不存在」与「账户在但零成交」──────────
    os.environ["PAPER_LEDGER_DIR"] = str(led)
    b404, s404 = LA.paper_trades({"arm": ["9z"]})
    b200, s200 = LA.paper_trades({"arm": ["3b"]})
    rec("反例·404 与零交易不可混同", s404 == 404 and s200 == 200,
        {"unknown_arm_status": s404, "zero_trade_status": s200})

    out = HERE / "out"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": __import__("datetime").datetime.utcnow()
               .strftime("%Y-%m-%dT%H:%M:%SZ"),
               "isolation": {"ledger_dir": str(led), "source": str(SRC_LEDGER),
                             "production_ledger_touched": False},
               "cases": results,
               "passed": sum(1 for r in results if r["ok"]),
               "failed": sum(1 for r in results if not r["ok"]),
               "total": len(results)}
    (out / "d03r1_contract_verify.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总：{payload['passed']}/{payload['total']} 通过，"
          f"{payload['failed']} 失败")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
