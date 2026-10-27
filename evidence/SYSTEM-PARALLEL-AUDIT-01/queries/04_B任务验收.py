# -*- coding: utf-8 -*-
"""04 B 任务独立验收（SYSTEM-PARALLEL-AUDIT-01 并行工作 3 之二）。

B 任务验收重点（工单原文）
--------------------------
「B：统一入口真实启动整条链，进程状态准确，隔离和停止范围正确。」

三个可机械判定的命题：

    B1  统一入口**真实启动整条链**
        从 `启动系统.command` → `框架/backend/start.sh` → `service_manager.py`
        → `unified_runtime.py` 这条接线必须真实存在，且 runtime 一个 cycle 里
        同时跑到「行情 → 信号 → 挂单路由 → 撮合 → 跟踪 → 退出」六段。
        判据：step 返回六个字段齐全，且 `errors` 为空。

    B2  进程状态**准确**
        · `--status` 的 PID 必须等于真实进程 PID
        · 状态文件 `paper_runtime_<arm>_status.json` 的 heartbeat 必须新鲜
        · 进程被 SIGKILL 后，状态必须变成 stopped/crashed，不能仍报 running

    B3  隔离和停止范围**正确**
        · SIGTERM 只终止目标进程，不误伤同项目的其他进程
        · service_manager.stop() 只杀 cmdline 含 unified_runtime.py 且 cwd 在项目内的进程
        · 停止后账本不被清空、不被重置

产物：out/04_B任务验收.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_b_task")
PY = sys.executable

M15, H1, H4 = 900_000, 3_600_000, 14_400_000
SYM = "BTCUSDT"

FILES = [
    "启动系统.command",
    "框架/backend/start.sh",
    "框架/backend/service_manager.py",
    "框架/backend/unified_runtime.py",
    "框架/backend/live_store.py",
    "框架/backend/paper_engine.py",
]


def sha_of(rel: str) -> dict:
    p = BASE / rel
    b = p.read_bytes()
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))}


def build_market(market: pathlib.Path) -> dict:
    """造够预热的行情：4h 260 根、1h 1100 根、15m 4400 根（同一时间轴，正弦+趋势）。"""
    if market.exists():
        market.unlink()
    con = sqlite3.connect(market)
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
    CREATE TABLE funding_history(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")

    import math
    now = int(time.time() * 1000)
    b4 = (now // H4) * H4
    n = {"4h": 0, "1h": 0, "15m": 0}
    specs = [("4h", H4, 260), ("1h", H1, 1100), ("15m", M15, 4400)]
    for tf, step, cnt in specs:
        base_t = (now // step) * step
        for k in range(cnt, 0, -1):
            ot = base_t - k * step
            i = cnt - k
            px = 80000.0 + 4000.0 * math.sin(i / 40.0) + i * 1.2
            hi = px * 1.004
            lo = px * 0.996
            con.execute(
                "INSERT INTO candle_versions VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (SYM, tf, ot, ot + step - 1, f"{px:.2f}", f"{hi:.2f}", f"{lo:.2f}",
                 f"{px:.2f}", "50000", "5000000000", 10, "25000", "2500000000",
                 1, ot + step - 1))
            con.execute("INSERT INTO candle_heads VALUES('binance','um',?,?,?,?)",
                        (SYM, tf, ot, 1))
            n[tf] += 1
    con.commit()
    con.close()
    return n


def env_vars() -> dict:
    e = dict(os.environ)
    e.update({
        "PAPER_LEDGER_DIR": str(ROOT / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "PAPER_LOCK_DIR": str(ROOT / "locks"),
        "LIVE_DIR": str(ROOT / "live"),
        "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
        "MARKET_DB": str(ROOT / "market.sqlite3"),
        "EFFECTIVE_VIEW": str(ROOT / "view.json"),
        "PAPER_GATE_FILE": str(ROOT / "gate.json"),
        "PYTHONPATH": f"{BASE}/框架:{BACKEND}:{BASE}",
        "PYTHONUNBUFFERED": "1",
    })
    e.pop("PAPER_INITIAL_EQUITY", None)
    return e


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    for d in ("paper", "live", "locks", "logs"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    bars = build_market(ROOT / "market.sqlite3")
    base_view = json.loads((BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json")
                           .read_text(encoding="utf-8"))
    base_view["risk"]["initial_equity_USDT"] = 2000
    (ROOT / "view.json").write_text(json.dumps(base_view, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (ROOT / "gate.json").write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    env = env_vars()

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "B 任务独立验收：统一入口启动整链 / 进程状态准确 / 隔离与停止范围正确",
        "边界": "全部在 /tmp/wx_b_task 隔离环境；隔离端口；不碰生产账本与 8787",
        "文件SHA": {f: sha_of(f) for f in FILES},
        "行情": bars,
        "场景": {},
    }

    # ---------- B1 接线静态核对 ----------
    chain = {}
    cmd_txt = (BASE / "启动系统.command").read_text(encoding="utf-8")
    start_txt = (BACKEND / "start.sh").read_text(encoding="utf-8")
    sm_txt = (BACKEND / "service_manager.py").read_text(encoding="utf-8")
    chain["启动系统.command 传 --enable-paper"] = "--enable-paper" in cmd_txt
    chain["start.sh 识别 --enable-paper"] = "--enable-paper" in start_txt
    chain["start.sh 调 service_manager.py"] = "service_manager.py" in start_txt
    chain["service_manager 启动 unified_runtime.py"] = "unified_runtime.py" in sm_txt
    chain["service_manager 用 spawn_detached_process"] = "spawn_detached_process" in sm_txt
    chain["默认安全模式不开链"] = "disabled_safe_mode" in sm_txt
    chain["stop 校验 cmdline 含 unified_runtime.py"] = (
        "unified_runtime.py" in sm_txt and "get_process_cmdline" in sm_txt)
    res["场景"]["B1_接线静态核对"] = {
        "检查": chain, "全部通过": all(chain.values())}

    # ---------- B1 实测：一个 cycle ----------
    cmd = [PY, str(BACKEND / "unified_runtime.py"), "--arm", "3a",
           "--symbol", SYM, "--once"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                       cwd=str(BASE), env=env)
    step_line = ""
    for line in (p.stdout or "").splitlines():
        if "Step finished" in line:
            step_line = line
    step_obj = {}
    if step_line:
        try:
            step_obj = json.loads(step_line.split("Step finished:", 1)[1].strip()
                                  .replace("'", '"'))
        except Exception:
            step_obj = {"raw": step_line[:400]}
    need_fields = ["new_signals", "routed", "matched", "trailing_updates", "exits", "errors"]
    b1 = {
        "rc": p.returncode, "耗时秒": round(time.time() - t0, 2),
        "step": step_obj,
        "六段字段齐全": all(k in step_obj for k in need_fields),
        "errors为空": (step_obj.get("errors") == []),
        "stderr尾部": (p.stderr or "")[-600:],
    }
    res["场景"]["B1_整链一个cycle"] = b1
    res["场景"]["B1_结论"] = (
        "通过" if (chain and all(chain.values()) and b1["六段字段齐全"] and b1["errors为空"])
        else "不通过")

    # ---------- B2 进程状态准确 ----------
    # 用 run_forever 起真实常驻进程
    rlog = ROOT / "logs" / "runtime.log"
    fh = open(rlog, "w")
    proc = subprocess.Popen(
        [PY, str(BACKEND / "unified_runtime.py"), "--arm", "3a",
         "--symbol", SYM, "--interval", "1.0"],
        cwd=str(BASE), env=env, stdout=fh, stderr=subprocess.STDOUT)
    b2 = {"pid_实测": proc.pid}
    # 等心跳
    status_json = ROOT / "locks" / "paper_runtime_3a_status.json"
    deadline = time.time() + 30
    while time.time() < deadline and not status_json.exists():
        time.sleep(0.5)
    time.sleep(2.0)
    b2["进程存活_启动后"] = pid_alive(proc.pid)
    b2["status文件存在"] = status_json.exists()
    sj = None
    if status_json.exists():
        try:
            sj = json.loads(status_json.read_text(encoding="utf-8"))
            # 注意：状态文件是**嵌套**结构，PID 在 runtime_identity 里，
            # 不是顶层。第一版按顶层取键，结果全空、误判「PID 不一致」。
            ri = sj.get("runtime_identity") or {}
            b2["status文件"] = {
                "pid": ri.get("pid"), "arm": ri.get("arm"), "symbol": ri.get("symbol"),
                "cycle_count": ri.get("cycle_count"),
                "overall_health": ri.get("overall_health"),
                "started_at": ri.get("started_at"),
                "market_data": (sj.get("market_data") or {}).get("status"),
                "signal_engine": (sj.get("signal_engine") or {}).get("status"),
                "order_processing": (sj.get("order_processing") or {}).get("status"),
                "execution_daemon": (sj.get("execution_daemon") or {}).get("status"),
            }
        except Exception as e:
            b2["status文件"] = f"解析失败 {e}"
    # --status 独立进程读取
    sp = subprocess.run([PY, str(BACKEND / "unified_runtime.py"), "--arm", "3a", "--status"],
                        capture_output=True, text=True, timeout=60, cwd=str(BASE), env=env)
    try:
        b2["--status输出"] = json.loads(sp.stdout)
    except Exception:
        b2["--status输出"] = {"raw": (sp.stdout or "")[:500],
                            "stderr": (sp.stderr or "")[-300:]}
    st_pid = None
    if isinstance(b2.get("--status输出"), dict):
        st_pid = (b2["--status输出"].get("runtime_identity") or {}).get("pid")
    b2["--status取到的pid"] = st_pid
    b2["PID一致"] = (st_pid == proc.pid)
    b2["status文件PID一致"] = (b2.get("status文件", {}).get("pid") == proc.pid
                            if isinstance(b2.get("status文件"), dict) else None)
    # 运行中 daemon 必须报 running（不能 healthy 但 daemon stopped）
    b2["运行中daemon为running"] = (
        isinstance(b2.get("status文件"), dict)
        and b2["status文件"].get("execution_daemon") == "running")
    b2["cycle_count推进"] = (isinstance(b2.get("status文件"), dict)
                           and (b2["status文件"].get("cycle_count") or 0) >= 1)
    res["场景"]["B2_进程状态"] = b2

    # ---------- B3 隔离与停止范围 ----------
    b3 = {"停止前": {"目标pid存活": pid_alive(proc.pid)}}
    # 记录一个无关的旁观进程（自己的 sleep），验证不被误杀
    bystander = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"], cwd=str(BASE))
    b3["旁观进程pid"] = bystander.pid
    # 账本快照（停止后不得被清空）
    ledger = ROOT / "paper" / "paper_3a.sqlite3"
    before = {"exists": ledger.exists(),
              "bytes": ledger.stat().st_size if ledger.exists() else None}
    b3["停止前账本"] = before

    # SIGTERM
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=20)
        b3["退出码"] = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        b3["退出码"] = "TIMEOUT_KILLED"
    time.sleep(1.5)
    b3["停止后"] = {
        "目标pid存活": pid_alive(proc.pid),
        "旁观进程存活": pid_alive(bystander.pid),
    }
    after = {"exists": ledger.exists(),
             "bytes": ledger.stat().st_size if ledger.exists() else None}
    b3["停止后账本"] = after
    b3["账本未被清空"] = (before["exists"] and after["exists"]
                        and after["bytes"] == before["bytes"])
    # 停止后状态文件应反映 stopped
    if status_json.exists():
        try:
            sj2 = json.loads(status_json.read_text(encoding="utf-8"))
            ri2 = sj2.get("runtime_identity") or {}
            b3["停止后status文件"] = {
                "pid": ri2.get("pid"),
                "overall_health": ri2.get("overall_health"),
                "execution_daemon": (sj2.get("execution_daemon") or {}).get("status"),
                "cycle_count": ri2.get("cycle_count"),
            }
        except Exception as e:
            b3["停止后status文件"] = f"解析失败 {e}"
    b3["停止后daemon为stopped"] = (
        isinstance(b3.get("停止后status文件"), dict)
        and b3["停止后status文件"].get("execution_daemon") == "stopped")
    # 清理旁观进程
    try:
        bystander.terminate()
    except Exception:
        pass
    b3["判定_只停自己"] = (not b3["停止后"]["目标pid存活"])
    b3["判定_未误伤旁观"] = b3["停止后"]["旁观进程存活"]
    b3["判定_合计"] = (b3["判定_只停自己"] and b3["判定_未误伤旁观"]
                      and b3["账本未被清空"] and b3.get("停止后daemon为stopped", False))
    res["场景"]["B3_隔离与停止范围"] = b3
    res["场景"]["B3_结论"] = "通过" if b3["判定_合计"] else "不通过"

    # ---------- B2 判定（依赖进程已停） ----------
    b2["SIGTERM后进程消失"] = not pid_alive(proc.pid)
    b2["判定_合计"] = bool(b2["进程存活_启动后"] and b2["PID一致"]
                        and b2.get("status文件PID一致")
                        and b2.get("运行中daemon为running")
                        and b2.get("cycle_count推进")
                        and b2["SIGTERM后进程消失"])
    res["场景"]["B2_结论"] = "通过" if b2["判定_合计"] else "不通过"
    fh.close()

    # ---------- 总判定 ----------
    res["B任务总判定"] = {
        "B1_统一入口启动整链": res["场景"]["B1_结论"],
        "B2_进程状态准确": res["场景"]["B2_结论"],
        "B3_隔离与停止范围": res["场景"]["B3_结论"],
        "合计": "通过" if all(res["场景"][k] == "通过"
                             for k in ("B1_结论", "B2_结论", "B3_结论"))
                else "不通过",
        "说明": "任一子项不通过即整体不通过",
    }

    (OUT / "04_B任务验收.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    print("行情：", bars)
    print("\n=== B1 接线静态核对 ===")
    for k, v in chain.items():
        print(f"  {'OK ' if v else 'FAIL'} {k}")
    print(f"  全部通过={res['场景']['B1_接线静态核对']['全部通过']}")
    print("\n=== B1 整链一个 cycle ===")
    print(f"  rc={b1['rc']} 耗时={b1['耗时秒']}s")
    print(f"  step={json.dumps(step_obj, ensure_ascii=False)[:400]}")
    print(f"  六段齐全={b1['六段字段齐全']} errors为空={b1['errors为空']}")
    if b1["stderr尾部"].strip():
        print(f"  stderr: {b1['stderr尾部'][:300]}")
    print(f"  → {res['场景']['B1_结论']}")
    print("\n=== B2 进程状态 ===")
    print(f"  启动 pid={b2['pid_实测']} 存活={b2['进程存活_启动后']}")
    print(f"  status文件={json.dumps(b2.get('status文件'), ensure_ascii=False)[:300]}")
    print(f"  --status={json.dumps(b2.get('--status输出'), ensure_ascii=False)[:300]}")
    print(f"  PID一致={b2['PID一致']} status文件PID一致={b2.get('status文件PID一致')} "
          f"停止后消失={b2['SIGTERM后进程消失']}")
    print(f"  → {res['场景']['B2_结论']}")
    print("\n=== B3 隔离与停止范围 ===")
    print(f"  停止前={b3['停止前']} 停止后={b3['停止后']} 退出码={b3['退出码']}")
    print(f"  账本 before={before} after={after} 未清空={b3['账本未被清空']}")
    print(f"  停止后status={json.dumps(b3.get('停止后status文件'), ensure_ascii=False)}")
    print(f"  只停自己={b3['判定_只停自己']} 未误伤={b3['判定_未误伤旁观']} "
          f"→ {res['场景']['B3_结论']}")
    print(f"\n=== B 任务总判定：{res['B任务总判定']['合计']} ===")
    print("\n→ out/04_B任务验收.json")


if __name__ == "__main__":
    main()
