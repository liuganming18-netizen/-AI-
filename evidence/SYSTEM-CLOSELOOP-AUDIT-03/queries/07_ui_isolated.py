# -*- coding: utf-8 -*-
"""07 场景7（写操作部分）：隔离实例上的**核心操作**浏览器实测。

为什么要起第二套：生产 8787 的账本一个字节都不能动，而"开仓/平仓/闸门切换"
必须真的点下去才算实测。`vite.config.ts` 本来就留了口子（`API_PROXY_TARGET` /
`VITE_CACHE_DIR`，注释写明"目的是隔离起第二实例做页面/接口证据"）⇒ 用它。

拓扑：
  playwright ──▶ vite:3001（独立缓存目录）──proxy──▶ adapter:8799（env 全指 /tmp/wx_audit05）
                                                    └─▶ 隔离账本 / 合成行情
  生产 8787 / 数据/paper 全程不参与，前后 SHA 复核。

产物：out/07_UI核心操作.json / .md + shots/07_*.png
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
HERE = pathlib.Path(__file__).resolve().parent
BACKEND = PROJ / "框架" / "backend"
WEB = PROJ / "UI界面" / "web"
NODE = "/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"

ROOT = pathlib.Path("/tmp/wx_audit05")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
VITE_CACHE = "/tmp/wx_vite_cache"

AD_PORT = 8799
WEB_PORT = 3001
SYM = "BTCUSDT"
ARM = "3a"
M15 = 900_000
H1 = 3_600_000

PROD_PAPER = PROJ / "数据" / "paper"
PROD_GATE = BACKEND / "live" / "paper_new_entry_gate.json"
PROD_LIVE = BACKEND / "live" / "live_signals.sqlite3"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ITEMS: list[dict] = []
DETAIL: dict = {}


def chk(no, desc, expect, actual, ok, note=""):
    ITEMS.append({"编号": no, "检查项": desc, "预期": str(expect), "实际": str(actual),
                  "判定": "通过" if ok else "不通过", "备注": note})
    print(("  [OK ] " if ok else "  [FAIL] ") + f"{no} {desc} → {actual!r}"
          + (f"  ({note})" if note else ""))


def sec(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def sha(p: pathlib.Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def port_open(port, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def http(method, path, body=None, port=AD_PORT, timeout=40.0):
    url = f"http://127.0.0.1:{port}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:                                        # noqa: BLE001
            return e.code, {"raw": raw[:400]}


def prod_state():
    files = {}
    for p in sorted(PROD_PAPER.glob("*.sqlite3")):
        st = p.stat()
        files[p.name] = {"sha256": sha(p), "字节": st.st_size, "mtime_ms": int(st.st_mtime * 1000)}
    return {"账本": files, "闸门": sha(PROD_GATE), "信号库": sha(PROD_LIVE),
            "8787_pid": (lambda: next((int(l.split()[1]) for l in subprocess.run(
                ["lsof", "-nP", "-i:8787", "-sTCP:LISTEN"], capture_output=True, text=True
            ).stdout.splitlines()[1:] if l.split()[1].isdigit()), None))()}


# ── 隔离行情库 ───────────────────────────────────────────────────────────────
def build_market():
    if MARKET.exists():
        MARKET.unlink()
    con = sqlite3.connect(MARKET)
    con.executescript("""
    CREATE TABLE candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE funding_history (
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    con.commit()
    con.close()


def add_bar(symbol, tf, open_time, ohlc):
    span = {"15m": M15, "1h": H1, "4h": 4 * H1}[tf]
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (symbol, tf, open_time, close_time, f"{o:.4f}", f"{h:.4f}",
                     f"{l:.4f}", f"{c:.4f}", "1000", "100000", 10, "500", "50000", 1, close_time))
        con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                    (symbol, tf, open_time, 1))
        con.commit()
    finally:
        con.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = dt.datetime.now().astimezone()
    prod_before = prod_state()
    DETAIL["生产基线"] = prod_before
    print(f"  生产账本 {list(prod_before['账本'])}  8787 pid={prod_before['8787_pid']}")

    if ROOT.exists():
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True)
    LIVE_DIR.mkdir(parents=True)
    build_market()

    now_ms = int(time.time() * 1000)
    base = ((now_ms + M15 - 1) // M15) * M15
    GATE.write_text(json.dumps({"allow_new_entries": True, "reason": "audit07_isolated",
                                "set_by": "SYSTEM-CLOSELOOP-AUDIT-03", "since_ms": now_ms},
                               ensure_ascii=False), encoding="utf-8")
    for k in range(-16, 0):
        add_bar(SYM, "15m", base + k * M15, (100.0, 100.5, 99.5, 100.0))
    h1b = (base // H1) * H1
    for k in range(-8, 0):
        add_bar(SYM, "1h", h1b + k * H1, (100.0, 101.0, 99.0, 100.0))

    env = {"PAPER_LEDGER_DIR": str(PAPER), "PAPER_LEDGER_ALLOW_INIT": "1",
           "LIVE_DIR": str(LIVE_DIR), "LIVE_DB": str(LIVE_DB),
           "MARKET_DB": str(MARKET), "PAPER_GATE_FILE": str(GATE)}
    for k, v in env.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import live_store as L
    import paper_engine as PE
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()

    sec("A. 在隔离账本里造一笔已成交持仓（回放通道，引擎真实入口）")
    add_bar(SYM, "15m", base, (100.0, 100.5, 99.5, 100.0))
    op = PE.open_position(ARM, SYM, signal_uid="audit07_ui", direction="long",
                          atr=5.0, origin="replay", now_ms=base, requested_at=base,
                          event_time=base, available_at=base)
    add_bar(SYM, "15m", base + M15, (100.0, 100.5, 99.5, 100.2))
    adv = PE.advance_pending(T=base + 2 * M15, log=lambda *a, **k: None)
    con = L.paper_conn(ARM, ro=True)
    try:
        pos = dict(con.execute("SELECT * FROM paper_position WHERE account_id=? "
                               "ORDER BY position_id DESC LIMIT 1", (f"paper43_{ARM}",)).fetchone())
    finally:
        con.close()
    pid = int(pos["position_id"])
    print(f"  首轮={op.get('reason')} 成交={adv['filled'][:1]} 持仓#{pid} "
          f"status={pos['status']} entry={pos['entry_price']} stop={pos['stop_price']}")
    chk("A1", "隔离账本里有 1 笔 open 持仓供 UI 操作", "open", pos["status"], pos["status"] == "open")

    sec("B. 起隔离后端（8799）与第二套前端（3001，独立缓存目录）")
    ad_log = open("/tmp/wx_audit07_adapter.log", "wb")
    ad_env = {**os.environ, "PORT": str(AD_PORT), "PYTHONPATH": str(BACKEND)}
    ad = subprocess.Popen([sys.executable, str(BACKEND / "adapter.py")], cwd=str(BACKEND),
                          env=ad_env, stdout=ad_log, stderr=subprocess.STDOUT)
    ok = False
    for _ in range(200):
        if port_open(AD_PORT):
            ok = True
            break
        if ad.poll() is not None:
            break
        time.sleep(0.5)
    chk("B1", "隔离后端 8799 已监听", True, ok, ok)
    if not ok:
        ad.kill()
        return

    vite_log = open("/tmp/wx_audit07_vite.log", "wb")
    vite_env = {**os.environ, "API_PROXY_TARGET": f"http://127.0.0.1:{AD_PORT}",
                "VITE_CACHE_DIR": VITE_CACHE, "DISABLE_HMR": "true"}
    vite = subprocess.Popen([NODE, str(WEB / "node_modules" / "vite" / "bin" / "vite.js"),
                             "--port", str(WEB_PORT), "--host", "127.0.0.1"],
                            cwd=str(WEB), env=vite_env, stdout=vite_log, stderr=subprocess.STDOUT)
    ok2 = False
    for _ in range(240):
        if port_open(WEB_PORT):
            ok2 = True
            break
        if vite.poll() is not None:
            break
        time.sleep(0.5)
    chk("B2", "第二套前端 3001 已监听（代理指向 8799，不是 8787）", True, ok2, ok2,
        "" if ok2 else open("/tmp/wx_audit07_vite.log").read()[-400:])
    if not ok2:
        ad.kill(); vite.kill()
        return

    # 确认 3001 的 /api 真的打到 8799（而不是 8787）：比 health 的返回体
    st_iso, body_iso = http("GET", "/api/v1/health", port=AD_PORT)
    DETAIL["隔离后端health"] = {"status": st_iso, "body": body_iso}

    sec("C. 浏览器实测（playwright，真实点击）")
    node_env = {**os.environ, "NODE_PATH": str(WEB / "node_modules"),
                "AUDIT_BASE": f"http://127.0.0.1:{WEB_PORT}/"}
    cp = subprocess.run([NODE, str(HERE / "07_ui_write.js")], env=node_env,
                        capture_output=True, text=True, timeout=900)
    print("  node stdout:", (cp.stdout or "").strip()[-2500:])
    if cp.returncode != 0:
        print("  node stderr:", (cp.stderr or "").strip()[-1500:])
    chk("C1", "浏览器脚本跑通（含 5 个核心操作步骤）", "rc=0", f"rc={cp.returncode}",
        cp.returncode == 0,
        (cp.stderr or "").strip().splitlines()[-1][:200] if cp.returncode else "")
    js = json.loads((OUT / "07_ui_rw.json").read_text(encoding="utf-8")) \
        if (OUT / "07_ui_rw.json").exists() else {}
    DETAIL["浏览器"] = js

    if js:
        b = js["steps"]["baseline"]["state"]
        st = js["steps"]["stopTrail"]
        cl = js["steps"]["close"]
        ga = js["steps"]["gate"]
        ol = js["steps"]["openLong"]

        chk("C2", "工作台模拟实盘面板连上隔离账本", "connected=1 且 1 个持仓行",
            f"connected={b['connected']} 持仓行={b['positionRows']}",
            b["connected"] == "1" and b["positionRows"] >= 1)
        chk("C3", "面板显示账本止损价（不是前端推导）", "行文本含'账本止损价'",
            (b["rowTexts"][0] if b["rowTexts"] else "")[:160],
            any("账本止损价" in x for x in b["rowTexts"]))
        chk("C4", "点「止损轨迹」真的发出 /paper/stops/{id} 请求", "有 1 条 stops 请求",
            st["requests"], any("/stops/" in r["url"] for r in st["requests"]))
        code404 = [r for r in st["requests"] if "/stops/" in r["url"] and r["status"] == 404]
        chk("C5", "止损轨迹端点返回 200（UI 能取到轨迹）", "200",
            f"{len(code404)} 条 404", not code404,
            "默认路由表漏挂 ⇒ UI 拿不到轨迹（03 的 C5b 已从接口侧证明）")
        chk("C6", "404 时 UI 的降级文案不得伪装成'暂无记录'",
            "显示错误/不可用", (st["trailBoxText"] or "")[:200],
            not (st["trailBoxText"] and "暂无上移记录" in st["trailBoxText"]),
            "若显示'初始止损生效中，暂无上移记录'，用户无法区分'真没有'与'接口挂了'")
        chk("C7", "点「平仓」发出 POST /paper/orders", "有 POST 且 200",
            cl["requests"], any(r["method"] == "POST" and r["status"] == 200
                                for r in cl["requests"]))
        chk("C8", "平仓后 UI 持仓行归零", "0", cl["state"]["positionRows"],
            cl["state"]["positionRows"] == 0)
        chk("C9", "点闸门按钮发出 POST /paper/toggle_gate", "有 POST 且 200",
            ga["requests"], any(r["method"] == "POST" and "toggle_gate" in r["url"]
                                and r["status"] == 200 for r in ga["requests"]))
        chk("C10", "闸门按钮文案随服务端状态翻转", "与点击前不同",
            f"{ga['clickedLabel']} → {(ga['state']['gateBtn'] or {}).get('text')}",
            (ga["state"]["gateBtn"] or {}).get("text") != ga["clickedLabel"])
        chk("C11", "「按选中信号开多」在无选中信号时禁用并给出原因",
            "disabled=true 且有 title", f"disabled={ol['before'].get('disabled')} "
            f"title={str(ol['before'].get('title'))[:60]}", bool(ol["before"].get("disabled")))
        chk("C12", "5 个步骤截图哈希两两不同（不是同一张图冒充）", "0 组重复",
            js.get("dupShots"), not js.get("dupShots"))

    sec("D. 隔离账本闭合核对（UI 点击是否真的落到账本）")
    con = L.paper_conn(ARM, ro=True)
    try:
        p2 = dict(con.execute("SELECT * FROM paper_position WHERE position_id=?", (pid,)).fetchone() or {})
        stt = dict(con.execute("SELECT * FROM paper_state WHERE account_id=?",
                               (f"paper43_{ARM}",)).fetchone() or {})
        n_ord = con.execute("SELECT COUNT(*) FROM paper_order WHERE account_id=?",
                            (f"paper43_{ARM}",)).fetchone()[0]
        n_fill = con.execute("SELECT COUNT(*) FROM paper_fill WHERE account_id=?",
                             (f"paper43_{ARM}",)).fetchone()[0]
        n_real = con.execute("SELECT COUNT(*) FROM paper_realized WHERE account_id=?",
                             (f"paper43_{ARM}",)).fetchone()[0]
    finally:
        con.close()
    DETAIL["D_账本收尾"] = {"position": p2, "state": stt,
                          "n_order": n_ord, "n_fill": n_fill, "n_realized": n_real}
    print(f"  持仓 status={p2.get('status')} exit_reason={p2.get('exit_reason')} "
          f"exit_price={p2.get('exit_price')}")
    print(f"  现金={stt.get('cash')} 已实现={stt.get('realized_pnl')} "
          f"订单={n_ord} 成交={n_fill} 已实现行={n_real}")
    chk("D1", "UI 平仓后账本持仓转为 closed", "closed", p2.get("status"), p2.get("status") == "closed")
    chk("D2", "平仓落库：订单数 ≥2（开+平）、成交数 ≥2、已实现 1 行",
        "≥2/≥2/1", f"{n_ord}/{n_fill}/{n_real}", n_ord >= 2 and n_fill >= 2 and n_real == 1)
    chk("D3", "账户现金 = 初始权益 + 已实现（手续费不重复扣）",
        round(5000 + float(stt.get("realized_pnl") or 0), 6), round(float(stt.get("cash") or 0), 6),
        abs(float(stt.get("cash") or 0) - (5000 + float(stt.get("realized_pnl") or 0))) < 1e-6)
    st_gate, gate_body = http("GET", "/api/v1/paper/accounts", port=AD_PORT)
    gate_now = (gate_body.get("new_entry_gate") or {}) if isinstance(gate_body, dict) else {}
    gate_file = json.loads(GATE.read_text(encoding="utf-8"))
    DETAIL["D_闸门"] = {"status": st_gate, "accounts里的键": list(gate_body)[:12]
                        if isinstance(gate_body, dict) else None,
                        "new_entry_gate(接口)": gate_now, "闸门文件": gate_file}
    print(f"  隔离闸门文件 allow_new_entries={gate_file.get('allow_new_entries')} "
          f"reason={gate_file.get('reason')} set_by={gate_file.get('set_by')}")
    chk("D4", "UI 点闸门后隔离闸门文件真的被写成 allow_new_entries=False",
        "False", gate_file.get("allow_new_entries"),
        gate_file.get("allow_new_entries") is False,
        "UI 的 POST /paper/toggle_gate 落到服务端闸门文件（不是只改前端状态）")

    sec("E. 生产实例未受浏览器测试影响")
    prod_after = prod_state()
    DETAIL["生产复测"] = prod_after
    same = {k: (prod_before["账本"][k]["sha256"] == prod_after["账本"].get(k, {}).get("sha256"))
            for k in prod_before["账本"]}
    chk("E1", "生产账本 4 个库 SHA 前后一致", "全一致", same, all(same.values()))
    chk("E2", "生产闸门 SHA 前后一致", prod_before["闸门"], prod_after["闸门"],
        prod_before["闸门"] == prod_after["闸门"])
    chk("E3", "生产信号库 SHA 前后一致", prod_before["信号库"], prod_after["信号库"],
        prod_before["信号库"] == prod_after["信号库"])
    chk("E4", "生产 8787 实例 PID 未变（没被重启）", prod_before["8787_pid"],
        prod_after["8787_pid"], prod_before["8787_pid"] == prod_after["8787_pid"])

    sec("F. 收尾")
    for p, name in ((vite, "vite"), (ad, "adapter")):
        try:
            p.terminate()
            p.wait(timeout=10)
        except Exception:                                        # noqa: BLE001
            try:
                p.kill()
            except Exception:                                    # noqa: BLE001
                pass
    time.sleep(1)
    chk("F1", "隔离 8799 / 3001 已关闭，生产 8787 仍在", "8799=False 3001=False 8787=True",
        f"8799={port_open(AD_PORT)} 3001={port_open(WEB_PORT)} 8787={port_open(8787)}",
        (not port_open(AD_PORT)) and (not port_open(WEB_PORT)) and port_open(8787))

    ok_n = sum(1 for x in ITEMS if x["判定"] == "通过")
    ng_n = sum(1 for x in ITEMS if x["判定"] == "不通过")
    nv_n = sum(1 for x in ITEMS if x["判定"] == "未验证")
    print(f"\n合计 {len(ITEMS)} 条：通过 {ok_n}，不通过 {ng_n}，未验证 {nv_n}")
    for x in ITEMS:
        if x["判定"] != "通过":
            print(f"  不通过 {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")

    res = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "开始时刻": t0.isoformat(timespec="seconds"),
           "拓扑": {"浏览器": f"127.0.0.1:{WEB_PORT}", "前端代理": f"127.0.0.1:{AD_PORT}",
                    "隔离根目录": str(ROOT), "生产": "127.0.0.1:8787（全程未参与写操作）"},
           "明细": DETAIL, "断言": ITEMS,
           "汇总": {"总数": len(ITEMS), "通过": ok_n, "不通过": ng_n, "未验证": nv_n}}
    (OUT / "07_UI核心操作.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    md = ["# 07 场景7（写操作部分）：隔离实例上的核心操作浏览器实测", "",
          f"- 生成时刻：{res['生成时刻']}",
          f"- 拓扑：playwright → vite:{WEB_PORT} → adapter:{AD_PORT} → 隔离账本 `/tmp/wx_audit05`",
          "- 生产 8787 / `数据/paper` 全程未参与，前后 SHA 复核见 E 段", "",
          "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |", "|---|---|---|---|---|---|"]
    for x in ITEMS:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | "
                  f"{x['判定']} | {x['备注']} |")
    md += ["", f"**合计 {len(ITEMS)} 条：通过 {ok_n}，不通过 {ng_n}，未验证 {nv_n}**"]
    (OUT / "07_UI核心操作.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n已写出 {OUT/'07_UI核心操作.json'} / .md")


if __name__ == "__main__":
    main()
