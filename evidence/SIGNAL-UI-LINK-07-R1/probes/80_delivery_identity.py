#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-UI-LINK-07-R1 探针⑥：交付身份（要求 5）

给反重力独立复验用的三样东西：
  1. **修复后的版本标识** —— 每个改动文件的 sha256 + 字节数 + 关键行号，
     以及运行中进程的 PID / 启动时刻 / 命令行。别人跑同一条命令就能确认
     「跑的是不是这一版」，不用听我描述。
  2. **生产页面入口** —— 页面地址、当前生产口径（0 条 / 51 条明细）。
  3. **可复用的隔离验证入口** —— 每条命令都能原样复制执行，并说明它证什么。

同时留「没动过」的证据：采集器 PID、账本、门禁文件、冻结规则文件的 sha。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
NODE = "/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node"

# ── 本单改动 / 新增的文件（版本标识的核心）───────────────────────────────────
CODE = [
    ("UI界面/web/src/api/signalsState.ts", "新增：信号三态分类器（唯一实现）"),
    ("UI界面/web/src/api/useMarketData.ts", "改：pullSignals 按契约分类，不再把异常清成空列表"),
    ("UI界面/web/src/components/SignalListPanel.tsx", "改：三态渲染 + 故障框 + 卡片 data 属性"),
    ("UI界面/web/src/App.tsx", "改：把 signalsState 透传给信号面板"),
    ("框架/backend/adapter.py", "改：两条失败路径写对 quality.status / missing_reason"),
]

# ── 必须证明「没动过」的文件 ─────────────────────────────────────────────────
UNTOUCHED = [
    ("evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md", "冻结规则（不得改）"),
    ("框架/backend/live/paper_new_entry_gate.json", "生产入场门禁"),
    ("框架/backend/live/live_signals.sqlite3", "生产实时信号库"),
    ("数据/paper/paper_3a.sqlite3", "3a 账本"),
    ("数据/paper/paper_3b.sqlite3", "3b 账本"),
    ("数据/paper/paper_3c.sqlite3", "3c 账本"),
    ("数据/paper/paper_3d.sqlite3", "3d 账本"),
]


def sha(p: Path):
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def line_count(p: Path):
    try:
        return len(p.read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:                       # noqa: BLE001
        return None


def proc_info(pid: int):
    try:
        import psutil
    except ImportError:
        return {"pid": pid, "error": "psutil 不可用"}
    try:
        p = psutil.Process(pid)
        return {
            "pid": pid,
            "started_at_utc": dt.datetime.fromtimestamp(
                p.create_time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cwd": p.cwd(),
            "cmdline": " ".join(p.cmdline()),
            "environ_isolated_keys": {
                k: p.environ().get(k)
                for k in ("LIVE_DB", "PAPER_LEDGER_DIR", "PAPER_LOCK_DIR", "MARKET_DB")
                if k in p.environ()
            },
        }
    except Exception as exc:                # noqa: BLE001
        return {"pid": pid, "error": str(exc)}


def listen_pid(port: int):
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True, timeout=15)
        out = r.stdout.strip().splitlines()
        return int(out[0]) if out else None
    except Exception:                       # noqa: BLE001
        return None


def main():
    out = {
        "probe": "SIGNAL-UI-LINK-07-R1/80_delivery_identity",
        "generated_real_utc": dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "code_fingerprint": {},
        "untouched_fingerprint": {},
        "services": {},
        "entries": {},
    }

    for rel, why in CODE:
        p = ROOT / rel
        out["code_fingerprint"][rel] = {
            "why": why,
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else None,
            "lines": line_count(p) if p.exists() else None,
            "sha256": sha(p),
        }

    for rel, why in UNTOUCHED:
        p = ROOT / rel
        out["untouched_fingerprint"][rel] = {
            "why": why,
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else None,
            "sha256": sha(p),
        }

    # ── 服务身份 ─────────────────────────────────────────────────────────────
    for name, port in (("api_backend", 8787), ("frontend_dev", 3000)):
        pid = listen_pid(port)
        out["services"][name] = {
            "port": port,
            "pid": pid,
            **(proc_info(pid) if pid else {"error": "端口无监听"}),
        }

    # 采集器：找命令行里带 collector 的进程（只读身份，不动它）
    collectors = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cl = " ".join(p.info.get("cmdline") or [])
                if ("collector" in cl or "采集" in cl) and "python" in (p.info.get("name") or ""):
                    collectors.append({
                        "pid": p.info["pid"],
                        "started_at_utc": dt.datetime.fromtimestamp(
                            p.info["create_time"], dt.timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "cmdline": cl[:200],
                    })
            except Exception:               # noqa: BLE001
                continue
    except ImportError:
        pass
    out["services"]["collectors"] = collectors

    # ── 入口清单 ─────────────────────────────────────────────────────────────
    out["entries"] = {
        "生产页面": {
            "url": "http://127.0.0.1:3000/",
            "current_state": "当前可执行信号 0 张（正常空列表）+ 空态诊断 9 行 + 历史明细 51 条",
            "证据": ["out/70_prod_page.json", "out/70_prod_page_discarded.png"],
        },
        "生产接口": {
            "signals_current": "http://127.0.0.1:8787/api/v1/signals?scope=current&limit=500",
            "signals_all": "http://127.0.0.1:8787/api/v1/signals?scope=all&limit=500",
            "paper_status": "http://127.0.0.1:8787/api/v1/paper/status",
            "note": "正常态：quality.status=ok、missing_reason=null、quality.note 非空（口径说明，不是故障）",
        },
        "隔离验证入口": [
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{PY} 20_contract_backend.py",
                "proves": "接口契约四种情况：正常 ok / 未连接 not_connected / 读库异常 error / 下限缺失 no_watermark",
                "out": "out/20_contract_backend.json",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{PY} 40_two_paths.py --mode live",
                "proves": "新产生候选：正式引擎 step_once(2026-10-02T15:00Z) 现场产生并落库（origin=live）",
                "out": "out/40_two_paths_live.json",
                "cost": "约 100 秒（要回放 2336 小时真实行情重建状态）",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{PY} 40_two_paths.py --mode recovered",
                "proves": "重启恢复候选：bootstrap 尾部补齐 + _route_recovered_candidates 落库（origin=recovered）",
                "out": "out/40_two_paths_recovered.json",
                "cost": "约 100 秒",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{NODE} 30_fault_recovery.mjs",
                "proves": "五态 + 恢复：加载中 / 正常空 / 业务异常200 / HTTP503 / 网络失败 / 恢复",
                "out": "out/30_fault_recovery.json",
                "cost": "约 50 秒",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{NODE} 50_two_paths_page.mjs --mode live",
                "proves": "新产生候选的页面级验收（库/接口/页面按同一 ID 对四项字段）",
                "out": "out/50_two_paths_page_live.json",
                "cost": "约 20 秒；需要 3000 端口前端在跑",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{NODE} 50_two_paths_page.mjs --mode recovered",
                "proves": "重启恢复候选的页面级验收",
                "out": "out/50_two_paths_page_recovered.json",
                "cost": "约 20 秒",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{PY} 60_history_reconcile.py",
                "proves": "历史明细 51 条：数量一致 / ID 时间原因可查 / 不进当前列表 / 账本无过期补单",
                "out": "out/60_history_reconcile.json",
            },
            {
                "cmd": f"cd {ROOT}/evidence/SIGNAL-UI-LINK-07-R1/probes && "
                       f"{NODE} 70_prod_page.mjs",
                "proves": "生产页面：0 张卡 + 9 行诊断 + 51 条明细可展开，刷新轮询稳定",
                "out": "out/70_prod_page.json",
            },
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "80_delivery_identity.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== 改动文件指纹 ==")
    for rel, v in out["code_fingerprint"].items():
        print(f"  {rel}\n    {v['bytes']}B / {v['lines']}行 / sha {str(v['sha256'])[:16]}…")
    print("== 未动文件 ==")
    for rel, v in out["untouched_fingerprint"].items():
        print(f"  {rel}\n    {v['bytes']}B / sha {str(v['sha256'])[:16]}…")
    print("== 服务 ==")
    for name in ("api_backend", "frontend_dev"):
        s = out["services"][name]
        print(f"  {name}: pid={s.get('pid')} 启动={s.get('started_at_utc')}")
    print(f"  采集器 {len(collectors)} 个（未动）")
    print(f"\n写出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
