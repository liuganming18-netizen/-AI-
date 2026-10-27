# -*- coding: utf-8 -*-
"""50｜新进程重跑：记录与身份恢复（生产库，只做幂等重放，不新增行）。

验证「进程重启后身份/记录恢复」的**存储侧**：
  1) 起一个**全新进程**再导入同一份反重力产物（不是同一进程内存缓存）
  2) 行数不变、`new=0`、`updated=289`
  3) `first_seen_at` 一条都不许变（首次出现时刻是事实，不可改写）
  4) 身份列（uid/strategy_key/strategy_version/config_hash/四时刻/bar_*）逐条一致
  5) `updated_at` 必须推进（证明这次真的写了，不是「什么都没做也算通过」）
  6) HTTP 再读一次，289 条仍在

**边界**：本单不重启生产后端进程（工单禁止中断健康服务）⇒ 「真实进程重启后
API 仍能读」这一点只能给到「新进程导入 + 独立 HTTP 读取」，进程级重启**未验证**。

输出：out/50_重启恢复.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_r1 as L  # noqa: E402

IMPORT = L.BACKEND / "research_signal_import.py"

IDENT_COLS = ("signal_uid", "source", "origin", "exchange", "market", "symbol", "timeframe",
              "strategy_key", "strategy_version", "config_hash", "data_version",
              "structural_kind", "direction", "event_time", "confirmed_at", "available_at",
              "bar_open_time", "bar_close_time", "display_state", "executable_state",
              "executable_reason", "payload_json")


def snapshot() -> dict:
    con = L.ro_conn(L.LIVE_DB)
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM live_signal ORDER BY signal_uid")]
    finally:
        con.close()
    ident = [{k: r.get(k) for k in IDENT_COLS} for r in rows]
    return {
        "n": len(rows),
        "ident_sha256": hashlib.sha256(
            json.dumps(ident, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest(),
        "first_seen": {r["signal_uid"]: r["first_seen_at"] for r in rows},
        "updated_at": {r["signal_uid"]: r["updated_at"] for r in rows},
        "ident": ident,
    }


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}
    before = snapshot()
    rep["重跑前"] = {"行数": before["n"], "身份哈希": before["ident_sha256"],
                  "first_seen_样本": dict(list(before["first_seen"].items())[:3])}

    # 全新进程（子进程 = 新解释器，无任何内存状态继承）
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{L.PROJECT / '框架'}:{L.BACKEND}:{L.PROJECT}"
    env.pop("LIVE_DB", None)
    r = L.run([L.PY, str(IMPORT), "--run-dir", str(L.RUN_B), "--require-ok", "--apply", "--json"],
              env=env)
    try:
        rep["重跑"] = json.loads(r["stdout"])
    except Exception:  # noqa: BLE001
        rep["重跑"] = {"parse_error": True, "stdout": r["stdout"][-3000:],
                      "stderr": r["stderr"][-3000:]}
    rep["重跑"]["rc"] = r["returncode"]
    rep["重跑"]["pid"] = "子进程（新解释器）"

    after = snapshot()
    rep["重跑后"] = {"行数": after["n"], "身份哈希": after["ident_sha256"],
                  "first_seen_样本": dict(list(after["first_seen"].items())[:3])}

    fs_changed = [k for k in before["first_seen"]
                  if before["first_seen"][k] != after["first_seen"].get(k)]
    upd_advanced = [k for k in before["updated_at"]
                    if after["updated_at"].get(k, 0) > before["updated_at"][k]]
    upd_same = [k for k in before["updated_at"]
                if after["updated_at"].get(k) == before["updated_at"][k]]
    rep["比对"] = {
        "first_seen 被改写的条数": len(fs_changed),
        "updated_at 推进的条数": len(upd_advanced),
        "updated_at 没推进的条数": len(upd_same),
        "身份列逐条一致": before["ident_sha256"] == after["ident_sha256"],
        "行数变化": after["n"] - before["n"],
    }

    # HTTP 再读
    import urllib.request
    with urllib.request.urlopen(
            L.API + "/api/v1/signals?scope=all&origin=historical_research&limit=1000",
            timeout=60) as resp:
        j = json.loads(resp.read().decode())
    items = j.get("items") or []
    rep["HTTP_重读"] = {"status": resp.status, "items_n": len(items),
                      "has_more": j.get("has_more"),
                      "uid集合一致": sorted(i["signal_id"] for i in items)
                      == sorted(before["first_seen"])}

    stats = rep["重跑"].get("stats") or {}
    checks = {
        "新进程导入 rc=0": rep["重跑"]["rc"] == 0,
        "新进程未新增行（new=0）": stats.get("new") == 0,
        "新进程全部按更新处理（updated=289）": stats.get("updated") == 289,
        "行数不变（289）": after["n"] == before["n"] == 289,
        "first_seen 一条都没被改写": len(fs_changed) == 0,
        "身份列逐条逐位一致（含 payload）": before["ident_sha256"] == after["ident_sha256"],
        "updated_at 全部推进（证明真写了）": len(upd_advanced) == 289 and not upd_same,
        "HTTP 重读仍是 289 条": len(items) == 289,
        "HTTP 重读的 uid 集合与库内一致":
            sorted(i["signal_id"] for i in items) == sorted(before["first_seen"]),
    }
    rep["判定"] = {"checks": checks, "pass_n": sum(1 for v in checks.values() if v),
                 "total_n": len(checks)}
    fail = [x for x, v in checks.items() if not v]
    rep["判定"]["结论"] = (
        "新进程重放幂等：不新增、不改写首次出现时刻与身份，只推进 updated_at；HTTP 仍读得到 289 条。"
        if not fail else f"存在未通过项：{fail}")
    rep["未验证"] = [
        "生产后端进程**未重启**（工单禁止中断健康服务）⇒ 「重启后 API 恢复」只在存储侧与新进程侧验证，进程级未验证",
        "重启后守护器能否自行恢复扫描（route_pending_orders 依赖运行中的守护循环）未验证",
    ]

    p = L.dump("50_重启恢复.json", rep)
    print("== 50 新进程重跑与恢复 ==")
    for x, v in checks.items():
        print(("  [通过] " if v else "  [不通过] ") + x)
    print("pass", rep["判定"]["pass_n"], "/", rep["判定"]["total_n"])
    print("->", p)
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
