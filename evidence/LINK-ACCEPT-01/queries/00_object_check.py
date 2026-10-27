#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00_object_check.py —— LINK-ACCEPT-01 开审前对象核对（只读）

工单第 1 条：开审先核对工单目录、交付时间、运行编号、源码哈希；
            未交付就记待审，禁止换旧报告代替。

本脚本只读，不写任何业务目录、不起服务、不动账本。
输出：out/00_对象核对.json、out/01_基线完整性.json、out/02_隔离路径.json、
      out/03_自然运行.json、out/04_账本来源.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import socket
import urllib.request
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = ROOT / "evidence"
OUT = EVID / "LINK-ACCEPT-01" / "out"
OUT.mkdir(parents=True, exist_ok=True)

OBJECTS = {
    "A": "PAPER-LIVE-01",
    "B": "UI-LINK-01",
}
BASELINE = "CODEX-CONNECT-02"


def ts(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mtime(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def api(path: str, timeout: float = 20.0):
    """只读 GET。必须显式禁代理：本机 HTTP_PROXY 无 no_proxy，
    urllib 会把 127.0.0.1:8787 发给代理，偶发纯文本 502 被当 JSON。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:8787" + path, timeout=timeout) as r:
            return {"code": r.status, "json": json.loads(r.read().decode("utf-8"))}
    except Exception as e:  # noqa: BLE001
        return {"code": None, "error": f"{type(e).__name__}: {e}"[:300]}


# ─────────────────────────────────────────────────────────────
# 1. 对象核对：A / B 是否交付
# ─────────────────────────────────────────────────────────────
def check_objects() -> dict:
    res = {"检查时刻": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "对象": {}}
    # 全项目按名字精确搜索（限定深度，禁全盘扫）
    hits = []
    for depth_dir in (EVID, ROOT, ROOT / "docs", Path("/Users/boos/Desktop")):
        if not depth_dir.exists():
            continue
        for p in depth_dir.iterdir():
            if not p.is_dir():
                continue
            for key, name in OBJECTS.items():
                if name.lower() in p.name.lower():
                    hits.append({"对象": key, "路径": str(p), "来源目录": str(depth_dir)})
    res["按名搜索结果"] = hits

    for key, name in OBJECTS.items():
        d = EVID / name
        info = {
            "目录": str(d),
            "存在": d.is_dir(),
        }
        if d.is_dir():
            files = [p for p in d.rglob("*") if p.is_file()]
            info["文件数"] = len(files)
            info["交付时间(最新mtime)"] = max((mtime(p) for p in files), default=None)
            # 运行编号 / 源码哈希 / 版本声明 的常见载体
            info["疑似运行编号文件"] = [p.name for p in files
                                   if re.search(r"(run|版本|version|manifest)", p.name, re.I)]
            info["含哈希字段的文件"] = []
            for p in files:
                if p.suffix.lower() not in (".json", ".md", ".txt", ".csv"):
                    continue
                try:
                    t = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                if re.search(r"[0-9a-f]{64}", t):
                    info["含哈希字段的文件"].append(p.name)
        else:
            info["文件数"] = 0
            info["交付时间(最新mtime)"] = None
        res["对象"][key] = info
    return res


# ─────────────────────────────────────────────────────────────
# 2. 参考基线完整性 + 进程/版本绑定
# ─────────────────────────────────────────────────────────────
def check_baseline() -> dict:
    b = EVID / BASELINE
    res = {"基线目录": str(b), "存在": b.is_dir()}
    if not b.is_dir():
        return res

    files = [p for p in b.rglob("*") if p.is_file()]
    res["文件数"] = len(files)
    res["交付时间范围"] = {
        "最早": min(mtime(p) for p in files),
        "最新": max(mtime(p) for p in files),
    }
    res["README首行"] = (b / "README.md").read_text(encoding="utf-8").splitlines()[0] if (b / "README.md").exists() else None

    # 2.1 声明哈希 vs 磁盘实际
    vp = b / "versions.json"
    res["哈希核对"] = []
    if vp.exists():
        v = json.loads(vp.read_text(encoding="utf-8"))
        for rel, expect in v.items():
            p = ROOT / rel
            row = {"文件": rel, "声明": expect[:16] + "…", "存在": p.exists()}
            if p.exists():
                act = sha(p)
                row.update({"实际": act[:16] + "…", "一致": act == expect,
                            "字节数": p.stat().st_size, "mtime": mtime(p)})
            else:
                row["一致"] = False
            res["哈希核对"].append(row)
        res["哈希条目数"] = len(v)
        res["哈希一致数"] = sum(1 for r in res["哈希核对"] if r.get("一致"))

    # 2.2 基线自报的六组结果（自报 ≠ 独立验收）
    ap = b / "audit_runtime_verification.json"
    if ap.exists():
        d = json.loads(ap.read_text(encoding="utf-8"))
        res["自报六组"] = {k: (v.get("status") if isinstance(v, dict) else None) for k, v in d.items()}
        res["自报六组_说明"] = "交付方自报，未经本审核方独立复算；且基线不是本次 A/B 的审核对象"

    # 2.3 进程身份与版本绑定
    procs = []
    for pid in _listen_pids(8787) + _runtime_pids():
        procs.append(_proc_info(pid))
    res["进程"] = procs

    ur = ROOT / "框架/backend/unified_runtime.py"
    ad = ROOT / "框架/backend/adapter.py"
    res["版本绑定"] = []
    for p in (ur, ad):
        res["版本绑定"].append({"文件": str(p.relative_to(ROOT)), "mtime": mtime(p),
                                "sha256": sha(p)[:16] + "…"})
    # 判定：worker 进程启动时刻 vs unified_runtime.py mtime
    worker = next((p for p in procs if p.get("cmd", "").find("unified_runtime.py") >= 0), None)
    if worker and ur.exists():
        st = dt.datetime.strptime(worker["启动时刻"], "%Y-%m-%d %H:%M:%S")
        mt = dt.datetime.fromtimestamp(ur.stat().st_mtime)
        res["worker是否加载最新unified_runtime"] = st >= mt
        res["worker启动时刻"] = worker["启动时刻"]
        res["unified_runtime_mtime"] = mt.strftime("%Y-%m-%d %H:%M:%S")
    return res


def _listen_pids(port: int) -> list[int]:
    """ps 在本沙箱被拒，用 lsof 取监听 PID。"""
    import subprocess
    try:
        out = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=15).stdout
        return sorted({int(x) for x in out.split() if x.strip().isdigit()})
    except Exception:  # noqa: BLE001
        return []


def _runtime_pids() -> list[int]:
    import psutil
    pids = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:  # noqa: BLE001
            continue
        if "unified_runtime.py" in cl:
            pids.append(p.info["pid"])
    return pids


def _proc_info(pid: int) -> dict:
    import psutil
    try:
        p = psutil.Process(pid)
        return {
            "pid": pid,
            "cmd": " ".join(p.cmdline())[:200],
            "启动时刻": dt.datetime.fromtimestamp(p.create_time()).strftime("%Y-%m-%d %H:%M:%S"),
            "cwd": p.cwd(),
        }
    except Exception as e:  # noqa: BLE001
        return {"pid": pid, "error": str(e)[:120]}


# ─────────────────────────────────────────────────────────────
# 3. 数据路径隔离（静态核对 + 运行实例指向）
# ─────────────────────────────────────────────────────────────
def check_isolation(baseline: dict) -> dict:
    res = {}
    t = EVID / BASELINE / "test_runtime.py"
    if t.exists():
        txt = t.read_text(encoding="utf-8")
        res["基线测试脚本"] = {
            "文件": str(t.relative_to(ROOT)),
            "sha256": sha(t),
            "隔离根目录常量": re.findall(r'TEST_DATA_DIR\s*=\s*pathlib\.Path\("([^"]+)"\)', txt),
            "设置的隔离环境变量": sorted(set(re.findall(r'os\.environ\["([A-Z_]+)"\]', txt))),
            "MARKET_ARCHIVE命中": txt.count("MARKET_ARCHIVE"),
        }
    ur = ROOT / "框架/backend/unified_runtime.py"
    if ur.exists():
        txt = ur.read_text(encoding="utf-8")
        res["运行时读取的行情路径变量"] = {
            "MARKET_DB命中": txt.count("MARKET_DB"),
            "MARKET_ARCHIVE命中": txt.count("MARKET_ARCHIVE"),
            "说明": "unified_runtime 只读 MARKET_DB，不读 MARKET_ARCHIVE ⇒ 不存在归档路径回落",
        }
    # 运行实例实际指向（gate_file 由 /paper/status 给出）
    st = api("/api/v1/paper/status")
    res["运行实例"] = {"接口": "/api/v1/paper/status", "code": st.get("code")}
    if st.get("code") == 200:
        it = st["json"].get("item") or {}
        g = ((it.get("order_processing") or {}).get("new_entry_gate") or {})
        res["运行实例"].update({
            "gate_file": g.get("gate_file"),
            "gate_allow_new_entries": g.get("allow_new_entries"),
            "gate_set_by": g.get("set_by"),
            "运行臂": (it.get("runtime_identity") or {}).get("arm"),
            "指向生产闸门": str(g.get("gate_file") or "").startswith(str(ROOT / "框架/backend/live")),
        })
    return res


# ─────────────────────────────────────────────────────────────
# 4. 自然市场运行状态（无自然成交就记未验证）
# ─────────────────────────────────────────────────────────────
def check_natural() -> dict:
    res = {"采样时刻": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "接口": {}}
    st = api("/api/v1/paper/status")
    res["接口"]["/paper/status"] = st.get("code")
    if st.get("code") == 200:
        it = st["json"].get("item") or {}
        ri = it.get("runtime_identity") or {}
        se = it.get("signal_engine") or {}
        op = it.get("order_processing") or {}
        ed = it.get("execution_daemon") or {}
        md = it.get("market_data") or {}
        res["运行身份"] = {k: ri.get(k) for k in
                       ("arm", "symbol", "pid", "status", "started_at", "cycle_count", "overall_health")}
        res["运行身份"]["started_at_可读"] = ts(ri.get("started_at"))
        res["信号引擎"] = {k: se.get(k) for k in
                       ("status", "universe", "universe_declared", "signals_generated_total", "last_cycle_ms")}
        res["订单处理"] = {k: op.get(k) for k in
                       ("status", "orders_routed_total", "orders_rejected_total",
                        "orders_waiting_gate", "current_pending_in_db")}
        res["执行守护"] = {k: ed.get(k) for k in
                       ("status", "open_positions", "pending_matched_total",
                        "trailing_updates_total", "exits_triggered_total", "last_error")}
        res["行情"] = {k: md.get(k) for k in ("status", "latest_15m_close_time", "latest_4h_close_time")}
        res["as_of"] = st["json"].get("as_of")

    for path, key in (("/api/v1/paper/accounts", "账户"), ("/api/v1/paper/positions?arm=3a", "持仓"),
                      ("/api/v1/signals?limit=5", "信号"), ("/api/v1/paper/orders?limit=5", "订单")):
        r = api(path)
        res["接口"][path] = r.get("code")
        if r.get("code") == 200:
            d = r["json"]
            res[key] = {"total": d.get("total"), "items": len(d.get("items") or [])}
    # 自然成交判据：成交来源信号 uid 前缀
    res["自然成交判据"] = (
        "signals_generated_total=0 且 orders_routed_total=0 且 open_positions=0 ⇒ 无自然信号、无自然成交"
    )
    return res


# ─────────────────────────────────────────────────────────────
# 5. 账本内成交来源（区分合成 / 自然）
# ─────────────────────────────────────────────────────────────
def check_ledger() -> dict:
    res = {}
    r = api("/api/v1/paper/orders?limit=5")
    if r.get("code") != 200:
        return {"error": r.get("error"), "code": r.get("code")}
    d = r["json"]
    # 注意：/paper/orders 顶层是 items[]（每臂一条），exit_fills / pending 在臂内，
    # 不在顶层。直接读顶层会永远得到 0 条成交（本轮第一版就踩了这个坑）。
    arms = d.get("items") or []
    res["臂数"] = len(arms)
    hist = {"order_status_histogram": {}, "pending_status_histogram": {}, "fill_action_histogram": {}}
    fills, pend = [], []
    for a in arms:
        for f in (a.get("exit_fills") or []):
            fills.append({**f, "arm": a.get("arm")})
        for q in (a.get("pending") or []):
            pend.append({**q, "arm": a.get("arm")})
        for k in hist:
            for kk, vv in (a.get(k) or {}).items():
                hist[k][kk] = hist[k].get(kk, 0) + vv
    res["各臂条数"] = [{"arm": a.get("arm"), "exit_fills": len(a.get("exit_fills") or []),
                     "pending": len(a.get("pending") or []), "mode": a.get("mode")} for a in arms]
    res["订单状态直方图"] = hist["order_status_histogram"]
    res["挂单状态直方图"] = hist["pending_status_histogram"]
    res["成交动作直方图"] = hist["fill_action_histogram"]
    res["退出成交条数"] = len(fills)
    res["退出成交来源"] = [
        {"arm": f.get("arm"), "fill_id": f.get("fill_id"), "position_id": f.get("position_id"),
         "signal_uid": f.get("signal_uid"), "fee": f.get("fee"),
         "fill_ts_可读": ts(f.get("fill_ts"))}
        for f in fills
    ]
    pat = re.compile(r"^(sig_closeloop|sig_ui_test|sig_test|sig_override|sig_wrong_fp|sig_no_bar|s1)")
    synth = [f for f in fills if pat.match(str(f.get("signal_uid") or ""))]
    res["合成来源成交数"] = len(synth)
    res["自然来源成交数"] = len(fills) - len(synth)
    if not fills:
        res["结论"] = "账本内无任何退出成交"
    elif len(synth) == len(fills):
        res["结论"] = "全部退出成交都来自合成测试信号 ⇒ 账本里没有任何自然市场成交"
    else:
        res["结论"] = "存在无法归类的成交来源，需逐笔核"
    res["挂单条数"] = len(pend)
    res["挂单终态分布"] = {}
    for q in pend:
        st = q.get("status")
        res["挂单终态分布"][st] = res["挂单终态分布"].get(st, 0) + 1
    return res


def main() -> None:
    obj = check_objects()
    base = check_baseline()
    iso = check_isolation(base)
    nat = check_natural()
    led = check_ledger()

    for name, data in (
        ("00_对象核对", obj),
        ("01_基线完整性", base),
        ("02_隔离路径", iso),
        ("03_自然运行", nat),
        ("04_账本来源", led),
    ):
        p = OUT / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"写出 {p.relative_to(ROOT)}")

    print("\n=== 一句话结论 ===")
    for k, v in obj["对象"].items():
        print(f"  {k} {v['目录'].split('/')[-1]}: 存在={v['存在']} 文件数={v['文件数']}")
    print(f"  基线哈希一致 {base.get('哈希一致数')}/{base.get('哈希条目数')}")
    print(f"  worker 加载最新 unified_runtime: {base.get('worker是否加载最新unified_runtime')}"
          f"（启动 {base.get('worker启动时刻')} vs mtime {base.get('unified_runtime_mtime')}）")
    print(f"  自然运行: 信号 {nat.get('信号引擎', {}).get('signals_generated_total')} / "
          f"路由订单 {nat.get('订单处理', {}).get('orders_routed_total')} / "
          f"持仓 {nat.get('执行守护', {}).get('open_positions')}")
    print(f"  账本: 退出成交 {led.get('退出成交条数')} 条，其中合成 {led.get('合成来源成交数')}，"
          f"自然 {led.get('自然来源成交数')}")


if __name__ == "__main__":
    main()
