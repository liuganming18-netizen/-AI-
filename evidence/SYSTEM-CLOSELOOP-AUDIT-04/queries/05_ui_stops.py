# -*- coding: utf-8 -*-
"""05 第一关⑥：止损轨迹正式路由 + 页面与账本一致 + 接口失败时页面如实报错。

拓扑：playwright ──▶ vite:3001 ──proxy──▶ adapter:8799（账本 /tmp/wx_audit04）
生产 8787 / 数据/paper 全程不参与。

产物：out/05_止损轨迹.json（含 05_ui_stops.json 的摘要与对账结论）
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04"
OUT = EVID / "out"
SHOTS = EVID / "shots"
BACKEND = BASE / "框架/backend"
WEB = BASE / "UI界面/web"
ROOT = pathlib.Path("/tmp/wx_audit04")
PAPER = ROOT / "paper"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
NODE = "/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
AD_PORT = 8799
WEB_PORT = 3001
ARM = "3a"
SYM = "BTCUSDT"
H4 = 14_400_000

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER), "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"), "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET), "PAPER_GATE_FILE": str(GATE),
}


def port_open(p: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def prep_ledger() -> int:
    con = sqlite3.connect(PAPER / f"paper_{ARM}.sqlite3")
    con.row_factory = sqlite3.Row
    now = int(time.time() * 1000)
    con.execute("DELETE FROM paper_position")
    con.execute("DELETE FROM paper_stop_history")
    con.execute("DELETE FROM paper_order")
    con.execute("DELETE FROM paper_fill")
    con.execute("DELETE FROM paper_pending")
    con.execute(
        "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,"
        "signal_uid,arm,stop_price,atr_at_entry,entry_fee,model_version,origin,epoch_ms) "
        "VALUES(?,?,?,?,?,'open',?,?,?,?,?,'fixture','fixture',?)",
        (f"paper43_{ARM}", SYM, 10.0, 100.0, now - 8 * H4, "ui_stops_04", ARM,
         92.1, 1.0, 0.05, 1791939600000))
    pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO paper_stop_history(account_id,position_id,old_stop,new_stop,reason,"
                "ref_bar_open_ms,available_at,decision_at,effective_at,created_at) "
                "VALUES(?,?,NULL,?,'initial_stop',?,?,?,?,?)",
                (f"paper43_{ARM}", pid, 85.0, now - 8 * H4, now - 8 * H4, now - 8 * H4,
                 now - 8 * H4, now))
    con.execute("INSERT INTO paper_stop_history(account_id,position_id,old_stop,new_stop,reason,"
                "ref_bar_open_ms,available_at,decision_at,effective_at,created_at) "
                "VALUES(?,?,?,?,'ft_low4h_trail',?,?,?,?,?)",
                (f"paper43_{ARM}", pid, 85.0, 92.1, now - 2 * H4, now - H4, now - H4,
                 now - H4, now))
    con.commit()
    con.close()
    return int(pid)


def ledger_rows(pid: int) -> list[dict]:
    con = sqlite3.connect(PAPER / f"paper_{ARM}.sqlite3")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,old_stop,new_stop,reason,effective_at FROM paper_stop_history "
        "WHERE position_id=? ORDER BY id", (pid,))]
    con.close()
    return rows


def main() -> None:
    for k, v in ENV.items():
        os.environ[k] = v
    pid = prep_ledger()
    rows = ledger_rows(pid)
    res: dict = {"生成时刻": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "position_id": pid,
                 "账本轨迹": rows}

    ad_log = open(OUT / "05_adapter.log", "wb")
    ad = subprocess.Popen([PY, str(BACKEND / "adapter.py")], cwd=str(BACKEND),
                          env={**os.environ, "PORT": str(AD_PORT), "PYTHONPATH": str(BACKEND)},
                          stdout=ad_log, stderr=subprocess.STDOUT, start_new_session=True)
    vite_log = open(OUT / "05_vite.log", "wb")
    vite = None
    try:
        ok = False
        for _ in range(200):
            if port_open(AD_PORT):
                ok = True
                break
            time.sleep(0.5)
        res["隔离后端就绪"] = ok
        if not ok:
            raise SystemExit("8799 未就绪，见 out/05_adapter.log")

        vite = subprocess.Popen([NODE, str(WEB / "node_modules/vite/bin/vite.js"),
                                 "--port", str(WEB_PORT), "--host", "127.0.0.1"],
                                cwd=str(WEB),
                                env={**os.environ, "API_PROXY_TARGET": f"http://127.0.0.1:{AD_PORT}",
                                     "VITE_CACHE_DIR": "/tmp/wx_vite_cache04", "DISABLE_HMR": "true"},
                                stdout=vite_log, stderr=subprocess.STDOUT, start_new_session=True)
        ok2 = False
        for _ in range(240):
            if port_open(WEB_PORT):
                ok2 = True
                break
            time.sleep(0.5)
        res["前端就绪"] = ok2
        if not ok2:
            raise SystemExit("3001 未就绪，见 out/05_vite.log")

        cp = subprocess.run([NODE, str(EVID / "queries/05_ui_stops.js")],
                            env={**os.environ, "NODE_PATH": str(WEB / "node_modules"),
                                 "AUDIT_BASE": f"http://127.0.0.1:{WEB_PORT}/"},
                            capture_output=True, text=True, timeout=900)
        res["浏览器返回码"] = cp.returncode
        res["浏览器stdout尾部"] = (cp.stdout or "").strip()[-1500:]
        if cp.returncode != 0:
            res["浏览器stderr尾部"] = (cp.stderr or "").strip()[-1500:]

        js = json.loads((OUT / "05_ui_stops.json").read_text(encoding="utf-8"))
        res["浏览器"] = {k: {kk: vv for kk, vv in v.items() if kk in ("click", "requests", "trail", "sha")}
                     for k, v in js["steps"].items()}
        res["截图去重"] = js.get("dupShots")

        # 页面与账本一致：账本每一条 (old→new) 都要在渲染文本里出现
        ok_text = (js["steps"]["ok"]["trail"] or {}).get("boxText") or ""
        checks = []
        for r in rows:
            old = r["old_stop"]
            new = r["new_stop"]
            pair = f"{old:g} → {new:g}" if old is not None else f"{new:g}"
            checks.append({"轨迹行": r["id"], "账本": pair,
                           "页面出现": (pair in ok_text) or (f"{new:g}" in ok_text and
                                                        (old is None or f"{old:g}" in ok_text))})
        res["页面与账本逐行核对"] = checks
        res["页面与账本一致"] = all(c["页面出现"] for c in checks) and len(rows) > 0

        err500 = (js["steps"]["err500"]["trail"] or {}).get("boxText") or ""
        abort = (js["steps"]["abort"]["trail"] or {}).get("boxText") or ""
        res["接口500时页面文案"] = err500[:400]
        res["网络中断时页面文案"] = abort[:400]
        res["500如实报错"] = ("⚠" in err500 and "暂无上移记录" not in err500
                          and ("失败" in err500 or "error" in err500.lower() or "500" in err500))
        res["中断如实报错"] = ("⚠" in abort and "暂无上移记录" not in abort)

        res["判定"] = {
            "⑥止损轨迹路由可访问": ("通过" if any(r["status"] == 200
                                        for r in js["steps"]["ok"]["requests"]) else "不通过"),
            "⑥页面与账本一致": "通过" if res["页面与账本一致"] else "不通过",
            "⑥接口失败如实报错": ("通过" if (res["500如实报错"] and res["中断如实报错"]) else "不通过"),
        }
    finally:
        if vite:
            vite.terminate()
        ad.terminate()
        for p in (vite, ad):
            if p is None:
                continue
            try:
                p.wait(timeout=5)
            except Exception:                                # noqa: BLE001
                p.kill()
        ad_log.close()
        vite_log.close()

    (OUT / "05_止损轨迹.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    print(json.dumps({k: res.get(k) for k in
                      ("隔离后端就绪", "前端就绪", "浏览器返回码", "页面与账本一致",
                       "500如实报错", "中断如实报错", "判定", "截图去重")},
                     ensure_ascii=False, default=str))
    print("500 文案:", res.get("接口500时页面文案", "")[:300])
    print("中断 文案:", res.get("网络中断时页面文案", "")[:300])


if __name__ == "__main__":
    main()
