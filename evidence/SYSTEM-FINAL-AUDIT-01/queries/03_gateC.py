# -*- coding: utf-8 -*-
"""03 C 关（自动运行关）驱动：两套配置各起一个独立子进程跑完整序列，再汇总。

为什么用子进程：`PAPER_LEDGER_DIR` 在 `live_store` 里是模块级常量，同进程切换会串账本。
两套配置用不同标的（BTCUSDT / ETHUSDT）与不同账本目录，互不干扰。

产物：out/03_C关自动运行.json
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
ROOT = pathlib.Path("/tmp/wx_final01")
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"

CASES = [
    {"tag": "A", "view": BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json",
     "symbol": "BTCUSDT", "paper": ROOT / "paper_A", "期望s": 0.05, "期望d": 0.05},
    {"tag": "B", "view": BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp2_config.json",
     "symbol": "ETHUSDT", "paper": ROOT / "paper_B", "期望s": 0.03, "期望d": 0.08},
]


def main() -> None:
    summary: dict = {"说明": "C 关：两套配置各跑一遍 信号→挂单→自动成交→上移→重启→退出→幂等→运行身份",
                     "统一入口核对": None, "用例": []}

    # 统一入口是否拉起守护（C 关第一句「从统一入口与真实信号生成开始验收」）
    hits = {}
    for name in ("启动系统.command", "框架/backend/start.sh", "框架/backend/collector.sh"):
        p = BASE / name
        s = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
        hits[name] = [k for k in ("paper_execution_worker", "paper_loop",
                                  "PaperExecutionDaemon", "paper_exec") if k in s]
    summary["统一入口核对"] = {"启动脚本命中": hits,
                          "结论": "不通过" if all(v == [] for v in hits.values()) else "通过"}

    for c in CASES:
        print(f"\n===== 用例 {c['tag']}：{c['view'].name} / {c['symbol']} =====", flush=True)
        r = subprocess.run([PY, str(HERE / "queries/03_gateC_run.py"),
                            "--view", str(c["view"]), "--tag", c["tag"],
                            "--paper", str(c["paper"]), "--symbol", c["symbol"]],
                           capture_output=True, text=True, timeout=600)
        print(r.stdout[-4000:], flush=True)
        if r.returncode != 0:
            print(r.stderr[-3000:], flush=True)
        f = OUT / f"03_{c['tag']}.json"
        if not f.exists():
            summary["用例"].append({"标签": c["tag"], "错误": f"未产出 {f.name}", "stderr": r.stderr[-800:]})
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        summary["用例"].append({
            "标签": c["tag"], "配置": c["view"].name, "标的": c["symbol"],
            "生效参数": d.get("生效参数"),
            "挂单": d.get("1_挂单", {}).get("挂单行"),
            "成交判定": d.get("2_判定"),
            "成交": [{"entry": p.get("entry_price"), "stop": p.get("stop_price"), "qty": p.get("qty"),
                      "s": p.get("stoploss_s"), "d": p.get("trail_d"),
                      "fp": (p.get("config_fingerprint") or "")[:12]}
                     for p in (d.get("2_成交", {}).get("持仓") or [])],
            "上移": d.get("3_上移", {}).get("轨迹"),
            "上移判定": d.get("3_上移", {}).get("判定"),
            "重启": d.get("3_上移", {}).get("重启"),
            "退出": d.get("4_退出"),
            "幂等": d.get("5_幂等"),
            "运行身份": d.get("6_运行身份"),
            "风险重算": d.get("7_风险重算"),
        })

    (OUT / "03_C关自动运行.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    print("\n===== 汇总 =====")
    print(f"统一入口：{json.dumps(summary['统一入口核对'], ensure_ascii=False)}")
    for u in summary["用例"]:
        print(f"\n[{u['标签']}] {u.get('配置')} / {u.get('标的')}")
        if "错误" in u:
            print("  错误:", u["错误"]); continue
        print(f"  生效参数: {json.dumps(u['生效参数'], ensure_ascii=False)}")
        print(f"  成交: {u['成交']}  判定={u['成交判定']}")
        print(f"  上移: {u['上移']}  判定={u['上移判定']}")
        print(f"  重启: {json.dumps(u['重启'], ensure_ascii=False)}")
        print(f"  退出: {json.dumps(u['退出'], ensure_ascii=False)}")
        print(f"  幂等: {json.dumps(u['幂等'], ensure_ascii=False)}")
        print(f"  运行身份: {json.dumps(u['运行身份'], ensure_ascii=False)[:300]}")
        print(f"  风险重算: {json.dumps(u['风险重算'], ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
