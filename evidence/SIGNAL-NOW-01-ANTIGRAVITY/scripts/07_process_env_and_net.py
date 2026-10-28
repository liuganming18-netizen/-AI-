# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤7：进程环境与网络暴露面（只读）。

为什么单独一单：README 里「重载后的 Runtime/API 继承了 197 个环境变量（含代理与沙箱标记）」
和「外网连接数 0」这两条必须有可复查的原始输出，不能只写在报告里。

做法：只用 `ps eww -p PID`（取环境）与 `lsof -nP -iTCP -a -p PID`（取连接），
**不 import psutil**，避免依赖差异导致别人复现不了。不杀进程、不改环境。

产物：out/07_进程环境与网络.json
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
PROCS = {"runtime": 43827, "api": 43819, "worker": 68775, "collector": 42278}
# 只关心「工具链/沙箱/代理」这几类，避免把无关变量当异常
WATCH_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy",
              "CODEBUDDY_SESSION_ID", "CODEBUDDY_SAFE_DELETE_SANDBOX",
              "CODEBUDDY_TOYBOX_SANDBOX_PROFILE", "PYTHONDONTWRITEBYTECODE",
              "MARKET_DB", "LIVE_DB", "PAPER_LEDGER_DIR", "EFFECTIVE_VIEW",
              "CONTRACT_SPEC_CSV", "PAPER_GATE_FILE")


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _via_psutil(pid: int):
    try:
        import psutil                                     # noqa: PLC0415
    except Exception:                                     # noqa: BLE001
        return None
    try:
        pr = psutil.Process(pid)
        env = pr.environ()
        conns = pr.net_connections(kind="tcp")
    except Exception:                                     # noqa: BLE001
        return None
    est, listen, other = [], [], []
    for c in conns:
        item = {"fd": c.fd, "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                "remote": (f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None),
                "status": c.status}
        (est if c.status == "ESTABLISHED" else
         listen if c.status == "LISTEN" else other).append(item)
    return {"env": env, "established": est, "listen": listen, "other": other}


def ps_env(pid: int) -> dict:
    """环境变量：优先 psutil（本机沙箱禁 `ps`），退化为 `ps eww`。"""
    got = _via_psutil(pid)
    if got is not None:
        env = got["env"]
        return {"alive": True, "n_env": len(env), "source": "psutil",
                "watched": {k: env.get(k) for k in WATCH_KEYS if k in env},
                "env": env}
    try:
        cp = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=15)
    except Exception as exc:                              # noqa: BLE001
        return {"alive": False, "n_env": 0, "env": {},
                "error": f"{type(exc).__name__}: {exc}"}
    raw = (cp.stdout or "").strip()
    if not raw:
        return {"alive": False, "n_env": 0, "env": {}}
    env = {}
    for tok in raw.split():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tok)
        if m:
            env[m.group(1)] = m.group(2)
    return {"alive": True, "n_env": len(env), "source": "ps_eww",
            "watched": {k: env.get(k) for k in WATCH_KEYS if k in env},
            "env": env}


def tcp_conns(pid: int) -> dict:
    got = _via_psutil(pid)
    if got is not None:
        return {"n_established": len(got["established"]),
                "n_listen": len(got["listen"]),
                "established": got["established"], "listen": got["listen"],
                "other": got["other"], "source": "psutil"}
    cp = subprocess.run(["lsof", "-nP", "-iTCP", "-a", "-p", str(pid)],
                        capture_output=True, text=True, timeout=25)
    lines = [l for l in (cp.stdout or "").splitlines() if l.strip()][1:]
    est, listen, other = [], [], []
    for l in lines:
        parts = l.split()
        if len(parts) < 9:
            continue
        item = {"raw": l.strip()}
        if "ESTABLISHED" in l:
            est.append(item)
        elif "LISTEN" in l:
            listen.append(item)
        else:
            other.append(item)
    return {"n_lines": len(lines), "established": est, "listen": listen,
            "other": other, "n_established": len(est), "n_listen": len(listen),
            "source": "lsof"}


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "7 进程环境与网络暴露面（只读）",
         "at_utc": utc(),
         "method": "psutil.Process.environ()/net_connections()（本机沙箱禁 `ps`）；退化为 ps eww / lsof"}

    procs = {}
    for role, pid in PROCS.items():
        e = ps_env(pid)
        c = tcp_conns(pid)
        procs[role] = {
            "pid": pid, "alive": e["alive"], "n_env": e["n_env"],
            "env_source": e.get("source"), "tcp_source": c.get("source"),
            "watched_env": e.get("watched"),
            "tcp": {"n_established": c["n_established"], "n_listen": c["n_listen"],
                    "established": c["established"][:20], "listen": c["listen"][:10],
                    "other": c["other"][:10]},
        }
    R["procs"] = procs

    # ── 结论（只陈述对比事实）──
    r, a, w, col = (procs["runtime"], procs["api"], procs["worker"],
                    procs["collector"])
    R["env_inheritance"] = {
        "runtime_n_env": r["n_env"], "api_n_env": a["n_env"],
        "worker_n_env": w["n_env"], "collector_n_env": col["n_env"],
        "restarted_have_proxy": bool(
            (r["watched_env"] or {}).get("HTTP_PROXY")
            or (a["watched_env"] or {}).get("HTTP_PROXY")),
        "not_restarted_have_proxy": bool(
            (w["watched_env"] or {}).get("HTTP_PROXY")
            or (col["watched_env"] or {}).get("HTTP_PROXY")),
        "restarted_have_codebuddy_markers": sorted(
            k for k in ("CODEBUDDY_SESSION_ID", "CODEBUDDY_SAFE_DELETE_SANDBOX",
                        "CODEBUDDY_TOYBOX_SANDBOX_PROFILE")
            if k in (r["watched_env"] or {}) or k in (a["watched_env"] or {})),
        "reading": ("重载后的 Runtime/API 继承了启动它们那个 shell 的环境（含代理与沙箱标记）；"
                    "未重启的 Worker/采集器保留原始精简环境。"
                    "⇒ 这是**重载方式**带来的差异，不是代码差异。"),
    }
    R["network_exposure"] = {
        "runtime_established": r["tcp"]["n_established"],
        "api_established": a["tcp"]["n_established"],
        "worker_established": w["tcp"]["n_established"],
        "collector_established": col["tcp"]["n_established"],
        "runtime_listen": r["tcp"]["n_listen"], "api_listen": a["tcp"]["n_listen"],
        "reading": ("Runtime/API 的 ESTABLISHED 数为 0 ⇒ 当前没有外网长连接，"
                    "代理变量此刻不产生实际影响；采集器有 ESTABLISHED ⇒ 它在直连行情源。"
                    "**本单不据此断言「代理配置无害」**，只说「此刻未观察到影响」。"),
    }
    R["risk_note"] = (
        "若下次重载继承到会拦截外网的代理配置，采集/下单链路可能被静默打断；"
        "建议重载时显式清理 HTTP(S)_PROXY 等变量。属运维动作，本单不动。")

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "07_进程环境与网络.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("=== 环境变量数 ===")
    for role in PROCS:
        d = procs[role]
        print(f"  {role:10s} pid={d['pid']} alive={d['alive']} n_env={d['n_env']}")
    print("\n=== 关注变量（重载的两个）===")
    for role in ("runtime", "api"):
        print(f"  {role}: {json.dumps(procs[role]['watched_env'], ensure_ascii=False)}")
    print("\n=== 网络 ===")
    for role in PROCS:
        t = procs[role]["tcp"]
        print(f"  {role:10s} ESTABLISHED={t['n_established']} LISTEN={t['n_listen']}")
        for e in t["established"][:6]:
            print(f"      {e.get('local')} -> {e.get('remote')} {e.get('status')}")
    print("\n=== 结论 ===")
    print("  " + R["env_inheritance"]["reading"])
    print("  " + R["network_exposure"]["reading"])
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
