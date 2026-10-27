# -*- coding: utf-8 -*-
"""05 C 任务独立验收（SYSTEM-PARALLEL-AUDIT-01 并行工作 3 之三）。

C 任务验收重点（工单原文）
--------------------------
「C：原操作清单完整，UI数据与同响应、源账本一致，示例数据未混成真实证据。」

三个可机械判定的命题：

    C1  原操作清单**完整**
        上一轮声明的「16 视图 / 全页面操作覆盖矩阵」必须能被复核：
        每个操作项要有对应的截图或响应证据，且截图不能重复。

    C2  UI 数据与**同响应、源账本**一致
        · 接口返回的账户/持仓/订单必须能与源账本 sqlite 直读对上
        · 接口在依赖缺失时必须**如实降级**，不能 500，也不能报假 OK

    C3  示例数据**未混成真实证据**
        账本里的测试/示例记录（`sig_ui_test_*` 等）不得被当作真实运行成绩展示。

本脚本**不再启动 HTTP 服务**。原因：`service_manager.py` 的
`ServiceConfig.resolve_paths()` 把 `market_db` / `paper_ledger_dir` /
`paper_lock_dir` 硬编码为生产路径，且 `shared_env[...] = str(self.cfg.xxx)`
会把调用方传入的环境变量**覆盖**掉。实测启动一次会让 Worker 往
**生产行情库** `数据/市场数据/db/market_v2.sqlite3` 写 tail_sync（mtime 已从
13:47 被改动）。为不再产生跨环境写入，改为**进程内直接调处理函数**取证。

产物：out/05_C任务验收.json
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"
UI01 = BASE / "evidence/SYSTEM-UI-01"
PY = sys.executable

FILES = [
    "框架/backend/live_api.py",
    "框架/backend/api/routes.py",
    "框架/backend/service_manager.py",
    "框架/backend/live_store.py",
    "框架/backend/adapter.py",
]


def sha_of(rel: str) -> dict:
    p = BASE / rel
    b = p.read_bytes()
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))}


def read_csv(p: pathlib.Path) -> tuple[list[str], list[dict]]:
    txt = p.read_bytes().decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(txt.splitlines()))
    return (list(rows[0].keys()) if rows else []), rows


# ══════════════════════════════════════════════════════════════════════
# 进程内探测：用子进程保证 env 在 import 前生效
# ══════════════════════════════════════════════════════════════════════
PROBE = r'''
import sys, json, os, traceback, math
sys.path.insert(0, r"{backend}")
out = {{"LIVE_DIR": None, "LIVE_DB": None, "结果": {{}}}}
try:
    import live_store as L
    out["LIVE_DIR"] = str(L.LIVE_DIR)
    out["LIVE_DB"] = str(L.LIVE_DB)
except Exception as e:
    out["live_store_err"] = f"{{type(e).__name__}}: {{e}}"
try:
    import live_api as LA
except Exception as e:
    out["live_api_err"] = f"{{type(e).__name__}}: {{e}}"
    print(json.dumps(out, ensure_ascii=False)); raise SystemExit(0)
for fn in ("paper_accounts", "paper_identity_get", "paper_positions", "paper_orders"):
    f = getattr(LA, fn, None)
    if f is None:
        out["结果"][fn] = {{"存在": False}}
        continue
    try:
        r = f()
        s = json.dumps(r, ensure_ascii=False)
        nan = []
        def walk(o, path=""):
            if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
                nan.append(path)
            elif isinstance(o, dict):
                for k, v in o.items(): walk(v, path + "." + str(k))
            elif isinstance(o, (list, tuple)):
                for i, v in enumerate(o): walk(v, path + "[" + str(i) + "]")
        walk(r)
        out["结果"][fn] = {{"ok": True, "bytes": len(s),
                           "非有限浮点": nan,
                           "missing_reason": (r.get("missing_reason")
                                              if isinstance(r, dict) else None),
                           "quality": (r.get("quality") if isinstance(r, dict) else None)}}
    except Exception as e:
        out["结果"][fn] = {{"ok": False, "exc": f"{{type(e).__name__}}: {{e}}"}}
print(json.dumps(out, ensure_ascii=False))
'''


def probe(live_dir: str, paper_dir: str, market_db: str) -> dict:
    env = dict(os.environ)
    env.update({"LIVE_DIR": live_dir, "PAPER_LEDGER_DIR": paper_dir,
                "MARKET_DB": market_db,
                "PYTHONPATH": f"{BASE}/框架:{BACKEND}:{BASE}"})
    code = PROBE.format(backend=str(BACKEND))
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       timeout=180, cwd=str(BASE), env=env)
    if p.returncode != 0:
        return {"probe_error": f"rc={p.returncode}", "stderr": (p.stderr or "")[-800:]}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"probe_error": "解析失败", "stdout": (p.stdout or "")[-800:]}


def main() -> None:
    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "C 任务独立验收：原操作清单完整 / UI数据与同响应源账本一致 / 示例数据未混成真实证据",
        "边界": "不启动 HTTP 服务（service_manager 无法做数据路径隔离，实测会写生产行情库）；"
                "改用进程内直接调处理函数",
        "文件SHA": {f: sha_of(f) for f in FILES},
    }

    # ══════════════ C1 原操作清单完整 ══════════════
    c1 = {"检查": []}
    p_matrix = UI01 / "01_全页面与16视图操作覆盖矩阵.csv"
    if p_matrix.exists():
        hdr, rows = read_csv(p_matrix)
        c1["矩阵文件"] = {"name": p_matrix.name, "sha256": sha_of(
            str(p_matrix.relative_to(BASE)))["sha256"], "表头": hdr, "行数": len(rows)}
        # 统计声称的视图数与操作项数
        n_views = len(rows)
        ops = [r.get(hdr[2], "") if len(hdr) > 2 else "" for r in rows]
        c1["声称视图数"] = n_views
        c1["非空操作项数"] = sum(1 for o in ops if o and o.strip() not in ("", "-", "—"))
        c1["行样例"] = rows[:4]
    else:
        c1["矩阵文件"] = {"存在": False}

    # 截图去重（与 02 号报告同法，独立重算）
    pngs = sorted(UI01.glob("*.png"))
    shas = {}
    for p in pngs:
        s = hashlib.sha256(p.read_bytes()).hexdigest()
        shas.setdefault(s, []).append(p.name)
    c1["截图总数"] = len(pngs)
    c1["唯一截图数"] = len(shas)
    c1["重复组"] = [{"sha256": k[:12], "files": v, "count": len(v)}
                    for k, v in shas.items() if len(v) > 1]
    c1["判定_截图支撑声称视图数"] = len(shas) >= c1.get("声称视图数", 0)
    c1["判定_合计"] = c1["判定_截图支撑声称视图数"]
    res["C1_原操作清单"] = c1
    res["C1_结论"] = "通过" if c1["判定_合计"] else "不通过"

    # ══════════════ C2 UI 数据与源账本一致 ══════════════
    c2 = {}
    prod_paper = str(BASE / "数据/paper")
    prod_live = str(BACKEND / "live")
    prod_market = str(BASE / "数据/市场数据/db/market_v2.sqlite3")
    empty_live = "/tmp/wx_live_empty"

    # 2a 正常环境：接口能否返回，且与账本直读对得上
    a = probe(prod_live, prod_paper, prod_market)
    c2["正常环境"] = a
    # 与源账本直读比对
    con = sqlite3.connect(f"file:{prod_paper}/paper_3a.sqlite3?mode=ro", uri=True)
    try:
        cash = con.execute("SELECT cash FROM paper_state LIMIT 1").fetchone()
        npos = con.execute("SELECT COUNT(*) FROM paper_position "
                           "WHERE status='open' AND COALESCE(voided,0)=0").fetchone()[0]
        npend = con.execute("SELECT COUNT(*) FROM paper_pending "
                            "WHERE status='pending'").fetchone()[0]
        c2["源账本直读"] = {"paper_state.cash": cash[0] if cash else None,
                          "open_positions": npos, "pending_orders": npend}
    finally:
        con.close()

    # 2b 缺 live 库：必须降级，不能 500
    b = probe(empty_live, prod_paper, prod_market)
    c2["缺live库"] = b
    rb = (b.get("结果") or {}).get("paper_accounts") or {}
    c2["缺live库_是否降级"] = bool(rb.get("ok")) or ("exc" in rb and "OperationalError" not in
                                              str(rb.get("exc", "")))
    c2["缺live库_paper_accounts抛错"] = rb.get("exc")
    c2["判定_缺依赖不硬崩"] = bool(rb.get("ok"))

    # 2c service_manager 是否支持数据路径隔离
    sm = (BACKEND / "service_manager.py").read_text(encoding="utf-8")
    c2["service_manager_读环境变量MARKET_DB"] = (
        'os.environ.get("MARKET_DB"' in sm or "os.environ['MARKET_DB']" in sm)
    c2["service_manager_读环境变量PAPER_LEDGER_DIR"] = (
        'os.environ.get("PAPER_LEDGER_DIR"' in sm
        or "os.environ['PAPER_LEDGER_DIR']" in sm)
    c2["service_manager_覆盖shared_env"] = 'shared_env["MARKET_DB"]' in sm
    c2["判定_可数据路径隔离"] = (c2["service_manager_读环境变量MARKET_DB"]
                              and c2["service_manager_读环境变量PAPER_LEDGER_DIR"])
    # 实测证据：我 13:47 起服务后生产行情库 mtime 变了
    mv2 = BASE / "数据/市场数据/db/market_v2.sqlite3"
    c2["生产行情库mtime"] = (time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(mv2.stat().st_mtime))
                           if mv2.exists() else None)
    c2["生产账本mtime"] = {
        p.name: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        for p in sorted((BASE / "数据/paper").glob("*.sqlite3"))}

    # 2d service_manager stop 能否停掉自己启动的进程（实测记录）
    c2["stop实测"] = {
        "时刻": "2026-10-23 13:48",
        "输出": "[!] 拒绝停止未知 worker 进程：pid=19809, cmd='' / "
                "[!] 拒绝停止非本项目 API 进程：pid=19806, cmd=''",
        "现象": "自己刚启动的两个进程都停不掉，原因是取不到 cmdline（cmd=''）",
        "处置": "改用 os.kill(SIGTERM) 手工停止，两进程均正常退出，18787 端口释放",
    }
    c2["判定_合计"] = (c2["判定_缺依赖不硬崩"] and c2["判定_可数据路径隔离"]
                      and bool(rb.get("ok")))
    res["C2_UI与源账本一致"] = c2
    res["C2_结论"] = "不通过" if not c2["判定_合计"] else "通过"

    # ══════════════ C3 示例数据未混成真实证据 ══════════════
    c3 = {"检查": []}
    test_prefixes = ("sig_ui_test_", "sig_test_", "sig_closeloop_", "sig_a1_",
                     "sig_a2_", "sig_a3_", "sig_probe")
    hits = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = BASE / f"数据/paper/paper_{arm}.sqlite3"
        if not p.exists():
            continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tabs:
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
                if "signal_uid" not in cols:
                    continue
                for pre in test_prefixes:
                    n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE signal_uid LIKE ?",
                                    (f"{pre}%",)).fetchone()[0]
                    if n:
                        hits.setdefault(pre, []).append(f"{p.name}:{t}={n}")
        finally:
            con.close()
    c3["测试前缀命中"] = hits
    c3["账本内含测试记录"] = bool(hits)
    # 关键判定不是「有没有测试记录」，而是「测试记录是否被当作正式成绩」。
    # 判据：voided=0 的净盈亏是否为 0，且 API 返回的权益是否等于初始本金。
    per_arm = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = BASE / f"数据/paper/paper_{arm}.sqlite3"
        if not p.exists():
            continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            d = {}
            if "paper_realized" in tabs:
                d["realized_全部"] = con.execute(
                    "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized").fetchone()[0]
                d["realized_voided0"] = con.execute(
                    "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized "
                    "WHERE COALESCE(voided,0)=0").fetchone()[0]
                d["realized_voided1"] = con.execute(
                    "SELECT COALESCE(SUM(net_pnl),0) FROM paper_realized "
                    "WHERE COALESCE(voided,0)=1").fetchone()[0]
                d["void_reason取值"] = [r[0] for r in con.execute(
                    "SELECT DISTINCT void_reason FROM paper_realized "
                    "WHERE COALESCE(voided,0)=1")]
            if "paper_state" in tabs:
                r = con.execute("SELECT cash FROM paper_state LIMIT 1").fetchone()
                d["paper_state.cash"] = r[0] if r else None
            per_arm[arm] = d
        finally:
            con.close()
    c3["各臂明细"] = per_arm
    # 独立取 API 侧的权益（进程内，只读）
    code = (
        "import sys,json;sys.path.insert(0,r'%s');"
        "import live_api as LA;r=LA.paper_accounts();"
        "print(json.dumps([{k:it.get(k) for k in "
        "('arm','cash','equity','initial_equity','positions')} "
        "for it in (r.get('items') or [])],ensure_ascii=False))" % str(BACKEND)
    )
    env = dict(os.environ)
    env.update({"LIVE_DIR": prod_live, "PAPER_LEDGER_DIR": prod_paper,
                "MARKET_DB": prod_market,
                "PYTHONPATH": f"{BASE}/框架:{BACKEND}:{BASE}"})
    sp = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                        timeout=120, cwd=str(BASE), env=env)
    try:
        c3["API权益"] = json.loads(sp.stdout.strip().splitlines()[-1])
    except Exception:
        c3["API权益"] = {"raw": (sp.stdout or "")[:300],
                       "stderr": (sp.stderr or "")[-300:]}
    # 判定：voided0 全为 0 ⇒ 测试记录未计入正式成绩
    c3["判定_测试记录未计入"] = all(
        abs(float(v.get("realized_voided0") or 0.0)) < 1e-9
        for v in per_arm.values()) if per_arm else None
    # 判定：API 的 cash 必须等于「初始本金 + voided0 净盈亏」，不能把作废亏损算进去
    api_ok = []
    for it in (c3["API权益"] if isinstance(c3["API权益"], list) else []):
        arm = it.get("arm")
        d = per_arm.get(arm) or {}
        expect = (float(it.get("initial_equity") or 0.0)
                  + float(d.get("realized_voided0") or 0.0))
        api_ok.append(abs(float(it.get("cash") or 0.0) - expect) < 1e-6)
    c3["判定_API排除作废亏损"] = all(api_ok) if api_ok else None
    # 观察项：paper_state.cash 与 account_view 口径是否不一致
    c3["观察_两套cash口径"] = {
        arm: {"paper_state.cash": v.get("paper_state.cash"),
              "realized_voided0": v.get("realized_voided0")}
        for arm, v in per_arm.items()}
    c3["判定_合计"] = bool(c3["判定_测试记录未计入"] and c3["判定_API排除作废亏损"])
    res["C3_示例数据"] = c3
    res["C3_结论"] = "通过" if c3["判定_合计"] else "不通过"

    # ══════════════ 总判定 ══════════════
    res["C任务总判定"] = {
        "C1_原操作清单完整": res["C1_结论"],
        "C2_UI数据与源账本一致": res["C2_结论"],
        "C3_示例数据未混成真实证据": res["C3_结论"],
        "合计": "通过" if all(res[k] == "通过"
                             for k in ("C1_结论", "C2_结论", "C3_结论"))
                else "不通过",
        "说明": "任一子项不通过即整体不通过；不得把局部通过升级为系统通过",
    }

    (OUT / "05_C任务验收.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    print("=== C1 原操作清单 ===")
    print(f"  矩阵行数={c1.get('声称视图数')} 非空操作项={c1.get('非空操作项数')}")
    print(f"  截图 {c1['截图总数']} 张，唯一 {c1['唯一截图数']} 个")
    for g in c1["重复组"]:
        print(f"    {g['sha256']}… × {g['count']}  {g['files']}")
    print(f"  → {res['C1_结论']}")
    print("\n=== C2 UI 与源账本 ===")
    print(f"  源账本直读: {c2.get('源账本直读')}")
    print(f"  正常环境接口: " + json.dumps(
        {k: (v.get('ok'), v.get('exc')) for k, v in
         ((a.get('结果') or {}).items())}, ensure_ascii=False)[:400])
    print(f"  缺live库 paper_accounts: {rb.get('ok')} / {rb.get('exc')}")
    print(f"  service_manager 读 MARKET_DB={c2['service_manager_读环境变量MARKET_DB']} "
          f"读 PAPER_LEDGER_DIR={c2['service_manager_读环境变量PAPER_LEDGER_DIR']} "
          f"覆盖shared_env={c2['service_manager_覆盖shared_env']}")
    print(f"  生产行情库 mtime={c2['生产行情库mtime']}（我 13:47 起服务时被改）")
    print(f"  → {res['C2_结论']}")
    print("\n=== C3 示例数据 ===")
    for k, v in hits.items():
        print(f"  测试前缀 {k}: {v}")
    for arm, d in (c3.get("各臂明细") or {}).items():
        print(f"  {arm}: realized_全部={d.get('realized_全部')} "
              f"voided0={d.get('realized_voided0')} voided1={d.get('realized_voided1')} "
              f"paper_state.cash={d.get('paper_state.cash')}")
    print(f"  API权益={json.dumps(c3.get('API权益'), ensure_ascii=False)[:300]}")
    print(f"  测试记录未计入={c3.get('判定_测试记录未计入')} "
          f"API排除作废亏损={c3.get('判定_API排除作废亏损')} → {res['C3_结论']}")
    print(f"\n=== C 任务总判定：{res['C任务总判定']['合计']} ===")
    print("\n→ out/05_C任务验收.json")


if __name__ == "__main__":
    main()
