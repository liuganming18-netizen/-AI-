# -*- coding: utf-8 -*-
"""05 隔离与恢复 —— 场景6（重启恢复、幂等、测试闸门隔离、日常实例不受影响）。

前置：`01_env.py` → `02_causal.py` → `03_exit_engine.py`（本脚本要读它们留下的账本状态）。

判据刻意选"会变的东西"：
  · 生产账本 4 个 sqlite3 的 **mtime 必须早于本轮测试开始时刻** ⇒ 测试一个字节都没写进去；
  · 生产闸门 json 的 **SHA + 内容**前后一致；
  · 8787 生产实例 **PID 不变、健康检查仍通**；
  · 隔离侧"重启"= **另起一个 Python 进程**重新读库，状态与内存里的判断一致。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _iso  # noqa: E402

RES: list[dict] = []
DETAIL: dict = {}

PROD_PAPER = _iso.PROJ / "数据" / "paper"
PROD_GATE = _iso.BACKEND / "live" / "paper_new_entry_gate.json"
PROD_LIVE_DB = _iso.BACKEND / "live" / "live_signals.sqlite3"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chk(no: str, desc: str, expect, actual, ok: bool, note: str = "") -> None:
    RES.append({"编号": no, "检查项": desc, "预期": str(expect), "实际": str(actual),
                "判定": "通过" if ok else "不通过", "备注": note})
    print(f"  [{'OK ' if ok else 'FAIL'}] {no} {desc} → {actual!r}")


def sec(t: str) -> None:
    print("=" * 72)
    print(t)


def sha(p: pathlib.Path) -> str | None:
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def prod_state() -> dict:
    files = {}
    for p in sorted(PROD_PAPER.glob("*.sqlite3")):
        st = p.stat()
        files[p.name] = {"sha256": sha(p), "字节": st.st_size,
                         "mtime_ms": int(st.st_mtime * 1000),
                         "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")}
    gate = {"存在": PROD_GATE.exists(), "sha256": sha(PROD_GATE),
            "内容": (json.loads(PROD_GATE.read_text(encoding="utf-8"))
                     if PROD_GATE.exists() else None)}
    live = {"存在": PROD_LIVE_DB.exists(), "sha256": sha(PROD_LIVE_DB),
            "字节": PROD_LIVE_DB.stat().st_size if PROD_LIVE_DB.exists() else None}
    return {"账本": files, "闸门": gate, "信号库": live}


def http_health(port: int = 8787) -> dict:
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/api/v1/health", timeout=20) as r:
            return {"status": r.status, "ok": True}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ok": False}
    except Exception as e:                                       # noqa: BLE001
        return {"status": None, "ok": False, "error": str(e)}


def pid_on(port: int) -> int | None:
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
    except Exception:                                            # noqa: BLE001
        return None
    return None


CHILD = r'''
import json, sys, os
sys.path.insert(0, r"%s")
import live_store as L
import paper_engine as PE
out = {"epoch": L.get_paper_epoch_ms(), "arms": {}}
for arm in L.ARMS:
    try:
        snap = PE.snapshot(arm)
    except Exception as e:
        snap = {"错误": f"{type(e).__name__}: {e}"}
    rows = []
    for p in (snap.get("positions") or []):
        pid = int(p.get("position_id") or -1)
        rows.append({"position_id": pid, "symbol": p.get("symbol"),
                     "stop_price": p.get("stop_price"),
                     "stop_history": PE.get_stop_history(arm, pid)})
    out["arms"][arm] = {"positions": rows, "cash": snap.get("cash"),
                        "realized": snap.get("realized_pnl_total"),
                        "n_closed": snap.get("n_closed"), "equity": snap.get("equity")}
print(json.dumps(out, ensure_ascii=False, default=str))
'''


def child_read() -> dict:
    code = CHILD % str(_iso.BACKEND)
    env = {**os.environ,
           "PAPER_LEDGER_DIR": str(_iso.PAPER),
           "PAPER_LEDGER_ALLOW_INIT": "1",
           "LIVE_DIR": str(_iso.LIVE_DIR),
           "LIVE_DB": str(_iso.LIVE_DB),
           "MARKET_DB": str(_iso.MARKET),
           "PAPER_GATE_FILE": str(_iso.GATE),
           "PYTHONPATH": str(_iso.BACKEND)}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    try:
        return {"ok": True, "data": json.loads(p.stdout.strip().split("\n")[-1])}
    except Exception:                                            # noqa: BLE001
        return {"ok": False, "stdout": (p.stdout or "")[-1500:], "stderr": (p.stderr or "")[-1500:]}


def main() -> None:
    env = _iso.setup()
    base = env["合成行情"]["bar锚点base"]
    import live_store as L
    import paper_engine as PE

    t_start = int(time.time() * 1000)
    t_start_iso = dt.datetime.fromtimestamp(t_start / 1000).astimezone().isoformat(timespec="seconds")

    sec("A. 生产侧基线（测试开始前的只读快照）")
    before = prod_state()
    pid_before = pid_on(8787)
    health_before = http_health()
    DETAIL["A_生产基线"] = {"采样时刻": t_start_iso, "状态": before,
                        "8787_pid": pid_before, "health": health_before}
    print(f"  生产账本 {list(before['账本'])}")
    print(f"  生产闸门 sha={str(before['闸门']['sha256'])[:12]}  8787 pid={pid_before}  "
          f"health={health_before}")

    sec("B. 隔离环境隔离性（路径与闸门）")
    chk("B1", "账本目录指向 /tmp（不是生产的 数据/paper）",
        str(_iso.PAPER), str(L.LEDGER_DIR), str(L.LEDGER_DIR) == str(_iso.PAPER))
    chk("B2", "行情库指向 /tmp 合成库", str(_iso.MARKET), str(L.MARKET_DB),
        str(L.MARKET_DB) == str(_iso.MARKET))
    chk("B3", "信号库指向 /tmp", str(_iso.LIVE_DB), str(L.LIVE_DB),
        str(L.LIVE_DB) == str(_iso.LIVE_DB))
    chk("B4", "闸门文件指向 /tmp（不是生产 gate）", str(_iso.GATE.resolve()),
        str(PE.GATE_FILE), str(PE.GATE_FILE) == str(_iso.GATE.resolve()),
        "引擎对 GATE_FILE 做了 .resolve()（macOS 上 /tmp → /private/tmp），按解析后路径比")

    # 闸门隔离的**行为验证**：关掉隔离闸门，生产闸门不受影响
    prod_gate_before = PROD_GATE.read_text(encoding="utf-8")
    bak = _iso.GATE.read_text(encoding="utf-8")
    try:
        _iso.GATE.write_text(json.dumps({"allow_new_entries": False,
                                         "reason": "audit03_isolation_probe"},
                                        ensure_ascii=False), encoding="utf-8")
        gate_iso = PE.new_entry_gate()
        prod_after = PROD_GATE.read_text(encoding="utf-8")
    finally:
        _iso.GATE.write_text(bak, encoding="utf-8")
    chk("B5", "改隔离闸门 ⇒ 引擎只认隔离闸门（生产闸门文件内容不变）",
        "隔离闸门 allow=False 且生产闸门字节不变",
        f"隔离 allow={gate_iso['allow_new_entries']} 生产闸门不变={prod_after == prod_gate_before}",
        gate_iso["allow_new_entries"] is False and prod_after == prod_gate_before)

    sec("C. 重启恢复（另起进程重新读库）")
    con = L.paper_conn("3a", ro=True)
    try:
        pos_rows = [dict(x) for x in con.execute(
            "SELECT position_id,symbol,status,stop_price FROM paper_position ORDER BY position_id")]
        stt = dict(con.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone() or {})
        hist_all = {}
        for r in pos_rows:
            hist_all[r["position_id"]] = [dict(x) for x in con.execute(
                "SELECT reason,old_stop,new_stop,effective_at FROM paper_stop_history "
                "WHERE position_id=? ORDER BY effective_at", (r["position_id"],))]
    finally:
        con.close()
    epoch_mem = L.get_paper_epoch_ms()
    child = child_read()
    DETAIL["C_重启读回"] = {"父进程持仓": pos_rows, "父进程账户": stt,
                        "父进程止损历史": hist_all, "父进程epoch": epoch_mem,
                        "子进程": child}
    chk("C1", "新进程能读到隔离账本（重启后状态不丢）", True, child.get("ok"), bool(child.get("ok")),
        (child.get("stderr") or "")[:200] if not child.get("ok") else "")
    if child.get("ok"):
        d = child["data"]
        chk("C2", "重启后 paper epoch 与重启前一致（只允许不降）", epoch_mem, d.get("epoch"),
            int(d.get("epoch") or 0) == int(epoch_mem))
        a3a = (d.get("arms") or {}).get("3a") or {}
        child_pids = [x["position_id"] for x in (a3a.get("positions") or [])]
        parent_open = [r["position_id"] for r in pos_rows if r["status"] == "open"]
        chk("C3", "重启后未平仓持仓集合一致", parent_open, child_pids,
            sorted(child_pids) == sorted(parent_open),
            "父进程 open=%s 子进程=%s" % (parent_open, child_pids))
        # 容差口径：账本落盘与子进程 snapshot() 都只保留 6 位小数，
        # 父进程直读 paper_state 拿到的是未截断值（如 4983.2729810683），
        # 两边做差最大可达 5e-7 ⇒ 容差必须 >= 1e-6，用 1e-9 会把"同一个数"判成不一致。
        cash_p = float(stt.get("cash") or 0)
        cash_c = float(a3a.get("cash") or 0)
        real_p = float(stt.get("realized_pnl") or 0)
        real_c = float(a3a.get("realized") or 0)
        chk("C4", "重启后账户现金/已实现与重启前一致（容差 1e-6，因落盘只保留 6 位）",
            (round(cash_p, 6), round(real_p, 6)), (round(cash_c, 6), round(real_c, 6)),
            abs(cash_c - cash_p) < 1e-6 and abs(real_c - real_p) < 1e-6,
            f"原始差：现金 {cash_c - cash_p:+.3e}、已实现 {real_c - real_p:+.3e}")
    # 已平仓笔数也要一致（只看 open 集合会因为"全平了"而空对空通过）
    con = L.paper_conn("3a", ro=True)
    try:
        n_closed_parent = con.execute(
            "SELECT COUNT(*) FROM paper_realized WHERE account_id='paper43_3a'").fetchone()[0]
    finally:
        con.close()
    chk("C5", "重启后已平仓笔数与重启前一致", n_closed_parent,
        (a3a or {}).get("n_closed"),
        int((a3a or {}).get("n_closed") or -1) == int(n_closed_parent),
        "空对空不算通过：用已平仓笔数做非空校验")

    sec("D. 幂等（重启后仍然幂等）")
    # 自建一笔干净持仓（3b/DOGEUSDT），不用 03 里被"伪造 4H"改过止损的那笔
    SYM_D = "DOGEUSDT"
    _iso.seed_history(SYM_D, base)
    r_open = PE.open_position("3b", SYM_D, signal_uid="audit03_d1", direction="long", atr=5.0,
                              now_ms=base, requested_at=base,
                              event_time=base, available_at=base, origin="replay")
    _iso.add_bar(SYM_D, base, _iso.P_FLAT)
    _iso.add_bar(SYM_D, base + _iso.M15, _iso.P_FLAT)
    adv = PE.advance_pending(T=base + 2 * _iso.M15)
    con = L.paper_conn("3b", ro=True)
    try:
        row_d = con.execute("SELECT position_id,symbol,stop_price FROM paper_position "
                            "WHERE account_id='paper43_3b' AND symbol=? AND status='open'",
                            (SYM_D,)).fetchone()
        pid_d = int(row_d["position_id"]) if row_d else None
        n_hist_before = con.execute("SELECT COUNT(*) FROM paper_stop_history WHERE position_id=?",
                                    (pid_d if pid_d else -1,)).fetchone()[0]
        stop_before = float(row_d["stop_price"]) if row_d else None
    finally:
        con.close()
    DETAIL["D_自建持仓"] = {"open": r_open, "position_id": pid_d, "stop": stop_before,
                        "历史行数": n_hist_before,
                        "advance": {k: v for k, v in adv.items() if k != "filled"}}
    chk("D0", "自建一笔干净持仓用于幂等测试", "1 笔且止损已落", pid_d,
        pid_d is not None and stop_before is not None)

    if pid_d is None:
        for no, d in (("D1", "重复推进只上移一次"), ("D2", "止损历史不重复"),
                      ("D3", "重启后重复推进不重复"), ("D4", "历史行数不增"),
                      ("D5", "跨进程幂等拒单")):
            RES.append({"编号": no, "检查项": d, "预期": "—", "实际": "无待测持仓",
                        "判定": "未验证", "备注": "前置不可用"})
    else:
        h4 = (base // (4 * _iso.H1)) * (4 * _iso.H1)
        barA = {"open_time": h4 - 4 * _iso.H1, "close_time": h4 - 1,
                "open": 100.0, "high": 101.0, "low": 95.0, "close": 99.0,
                "available_at": h4 - 1}
        r1 = PE.advance_position_trailing("3b", pid_d, barA)
        r2 = PE.advance_position_trailing("3b", pid_d, barA)
        con = L.paper_conn("3b", ro=True)
        try:
            n_after = con.execute("SELECT COUNT(*) FROM paper_stop_history WHERE position_id=?",
                                  (pid_d,)).fetchone()[0]
            stop_after = con.execute("SELECT stop_price FROM paper_position WHERE position_id=?",
                                     (pid_d,)).fetchone()[0]
        finally:
            con.close()
        DETAIL["D_重复推进"] = {"第一次": r1, "第二次": r2,
                            "历史行数_前": n_hist_before, "历史行数_后": n_after,
                            "止损_前": stop_before, "止损_后": stop_after}
        chk("D1", "重复推进同一根 4H K 线 ⇒ 只上移一次", "第一次 True、第二次 False",
            f"{r1.get('stop_updated')} / {r2.get('stop_updated')}",
            bool(r1.get("stop_updated")) and not r2.get("stop_updated"))
        chk("D2", "止损历史只多一行", n_hist_before + 1, n_after,
            int(n_after) == int(n_hist_before) + 1)
        child2 = child_read()
        r3 = PE.advance_position_trailing("3b", pid_d, barA)
        con = L.paper_conn("3b", ro=True)
        try:
            n_final = con.execute("SELECT COUNT(*) FROM paper_stop_history WHERE position_id=?",
                                  (pid_d,)).fetchone()[0]
        finally:
            con.close()
        DETAIL["D_重启后重复推进"] = {"子进程ok": child2.get("ok"), "第三次": r3,
                              "历史行数": n_final}
        chk("D3", "重启后重复推进仍不重复记录", False, r3.get("stop_updated"),
            not r3.get("stop_updated"))
        chk("D4", "重启后历史行数仍为 +1（没有多记）", n_after, n_final,
            int(n_final) == int(n_after))
        dup = PE.open_position("3a", _iso.SYM, signal_uid="audit03_s1", direction="long", atr=5.0,
                               now_ms=base, requested_at=base,
                               event_time=base - _iso.M15, available_at=base, origin="replay")
        chk("D5", "重启后同一 signal_uid 仍拒单（幂等跨进程有效）",
            "duplicate_signal_uid", dup.get("reason"),
            dup.get("reason") == "duplicate_signal_uid")

    sec("E. 日常实例不受测试动作影响（测试结束后再测一次）")
    after = prod_state()
    pid_after = pid_on(8787)
    health_after = http_health()
    DETAIL["E_生产复测"] = {"状态": after, "8787_pid": pid_after, "health": health_after}
    chk("E1", "生产账本 4 个库的 SHA 前后一致", "全部一致",
        "一致" if all(before["账本"].get(k, {}).get("sha256") == v["sha256"]
                     for k, v in after["账本"].items()) else "有变化",
        all(before["账本"].get(k, {}).get("sha256") == v["sha256"]
            for k, v in after["账本"].items()))
    chk("E2", "生产账本文件 mtime 早于本轮测试开始时刻（一个字节都没写）",
        f"全部 < {t_start_iso}",
        max((v["mtime_ms"] for v in after["账本"].values()), default=0) < t_start,
        "最大 mtime = " + max((v["mtime"] for v in after["账本"].values()), default="无"))
    chk("E3", "生产闸门内容与 SHA 前后一致", "一致",
        before["闸门"]["sha256"] == after["闸门"]["sha256"],
        before["闸门"]["sha256"] == after["闸门"]["sha256"])
    chk("E4", "生产信号库 SHA 前后一致", "一致",
        before["信号库"]["sha256"] == after["信号库"]["sha256"],
        before["信号库"]["sha256"] == after["信号库"]["sha256"])
    chk("E5", "8787 生产实例 PID 未变（测试没有重启它）", pid_before, pid_after,
        pid_before == pid_after)
    chk("E6", "8787 健康检查仍通", "ok", health_after.get("status"),
        bool(health_after.get("ok")))

    out = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "测试开始时刻": t_start_iso,
        "明细": DETAIL,
        "断言": RES,
        "汇总": {"总数": len(RES),
                 "通过": sum(1 for x in RES if x["判定"] == "通过"),
                 "不通过": sum(1 for x in RES if x["判定"] == "不通过"),
                 "未验证": sum(1 for x in RES if x["判定"] == "未验证")},
    }
    _iso.dump("05_隔离与恢复.json", out)

    md = ["# 05 隔离与恢复（场景6）", "",
          f"- 生成时刻：{out['生成时刻']}", f"- 测试开始时刻：{t_start_iso}", "",
          "## A 生产基线", ""]
    md += [f"- 生产账本：{', '.join(before['账本'])}",
           f"- 生产闸门 SHA：`{before['闸门']['sha256']}`",
           f"- 8787 PID：{pid_before}，健康：{health_before}", "",
           "## E 生产复测", "",
           f"- 8787 PID：{pid_after}，健康：{health_after}",
           f"- 账本 mtime 最大：{max((v['mtime'] for v in after['账本'].values()), default='无')}", "",
           "## 断言清单", "", "| 编号 | 检查项 | 预期 | 实际 | 判定 | 备注 |",
           "|---|---|---|---|---|---|"]
    for x in RES:
        md.append(f"| {x['编号']} | {x['检查项']} | {x['预期']} | {x['实际']} | {x['判定']} | "
                  f"{x['备注']} |")
    md += ["", f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
               f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}"]
    (_iso.OUT / "05_隔离与恢复.md").write_text("\n".join(md), encoding="utf-8")

    sec(f"合计 {out['汇总']['总数']} 条：通过 {out['汇总']['通过']}，"
        f"不通过 {out['汇总']['不通过']}，未验证 {out['汇总']['未验证']}")
    for x in RES:
        if x["判定"] != "通过":
            print(f"  {x['判定']} {x['编号']} {x['检查项']}：预期 {x['预期']} 实际 {x['实际']}")


if __name__ == "__main__":
    main()
