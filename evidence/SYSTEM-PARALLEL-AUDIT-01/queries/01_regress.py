# -*- coding: utf-8 -*-
"""01 受影响回归（SYSTEM-PARALLEL-AUDIT-01 并行工作 1 的后半）。

工单原话
--------
「保存当前已通过风险定量的基线，准备受影响回归，避免后续重复全量审核。」
「审核阶段绑定代码、配置、测试数据及运行进程身份；发现版本变化，只作废受影响项。」

所以本脚本不重跑审核，只做一件事：
    拿 `00_基线.json` 当基准，重跑同一组输入，**逐字段**比对，
    然后按「哪些文件变了」把结论精确映射到「哪些项作废」。

判定分层（关键：不许一有变化就全盘作废）
----------------------------------------
    SHA 全同                     → 全部项保持有效，不必重跑语义（仍会复算一遍做交叉验证）
    SHA 变但定量矩阵全同         → 定量项仍有效（行为未变），但**身份已变**，需重新绑定版本
    SHA 变且某组定量变了         → 只作废「涉及该字段」的项
    `apply_order_rounding` 变    → 作废 B 关全部定量结论
    `live_store.INIT_EQUITY` 变  → 作废 A-1 结论与所有「绝对预算」数字
    `paper_engine` 的 equity() 变 → 作废 A-1 结论

用法
----
    python 01_regress.py            # 比对并打印
    python 01_regress.py --json     # 只输出 JSON 路径

产物：out/01_回归结果.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import sys

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
BASE_JSON = OUT / "00_基线.json"
BACKEND = BASE / "框架/backend"

# 文件 → 该文件变化时会作废哪些结论（「受影响项」映射表）
IMPACT_MAP = {
    "框架/backend/strategies/wx_execution.py": [
        "B关_止损唯一性", "B关_数量唯一性", "B关_风险比预算", "B关_全链路风险重算",
    ],
    "框架/backend/live_store.py": [
        "A-1_权益基准", "A-1_绝对预算10vs25", "C关_账本闭合",
    ],
    "框架/backend/paper_engine.py": [
        "A-1_权益基准", "A-1_绝对预算10vs25", "A-2_stop_price污染",
        "A关_参数身份贯穿", "C关_账本闭合", "C关_上移与退出",
    ],
    "框架/backend/strategy_contracts.py": [
        "A关_缺配置拒单", "A关_参数身份贯穿",
    ],
    "框架/backend/unified_runtime.py": [
        "B任务_统一入口启动整链", "B任务_进程状态", "B任务_隔离与停止范围",
    ],
    "框架/backend/live_api.py": [
        "A-2_stop_price污染", "A关_客户端不能覆盖冻结参数", "C任务_UI数据同源",
    ],
    "框架/backend/paper_loop.py": [
        "C关_自动成交", "B任务_统一入口启动整链",
    ],
    "框架/backend/api/routes.py": [
        "C任务_UI数据同源", "B任务_进程状态",
    ],
}

# 定量矩阵里逐个比较的字段：基线键名 → apply_order_rounding 返回键名
# （00_baseline.py 存盘时用了简称 entry/stop，返回体里是全名 entry_price/stop_price）
QTY_FIELDS = {
    "entry": "entry_price",
    "stop": "stop_price",
    "stop_dist": "stop_dist",
    "qty": "qty",
    "planned_risk": "planned_risk",
    "reject": "reject",
}


def sha_of(rel: str) -> str:
    return hashlib.sha256((BASE / rel).read_bytes()).hexdigest()


def recompute_matrix(base: dict) -> list[dict]:
    """用与 00_baseline.py 完全相同的输入重算定量矩阵。

    刻意**不 import** 00_baseline（避免循环），但输入必须逐字一致：
    从基线 JSON 里读 s / atr / equity 三元组，保证是同一组输入。
    """
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("PAPER_LEDGER_DIR", "/tmp/wx_regress/paper")
    os.environ.setdefault("PAPER_LOCK_DIR", "/tmp/wx_regress/locks")
    os.environ.setdefault("LIVE_DIR", "/tmp/wx_regress/live")
    for d in ("paper", "locks", "live"):
        pathlib.Path(f"/tmp/wx_regress/{d}").mkdir(parents=True, exist_ok=True)
    from strategies.wx_execution import apply_order_rounding

    frozen = base["冻结规则_已授权"]
    limits = {"quantity_step": 0.001, "price_step": 0.1, "min_quantity": 0.001,
              "min_notional": 5.0, "max_quantity": 1000.0}
    rows = []
    for x in base["定量矩阵"]:
        r = apply_order_rounding(
            raw_open=100.0, atr=x["atr"], limits=limits,
            risk_per_trade_fraction=frozen["risk_budget_per_trade"],
            equity=x["equity"], stop_atr_mult=3.0,
            slippage_each_side=frozen["slippage_bps"] / 10000.0,
            stoploss_s=x["s"], strategy_id="FT-LOW4H", direction="long")
        rows.append({"s": x["s"], "atr": x["atr"], "equity": x["equity"]}
                    | {base_key: r[ret_key] for base_key, ret_key in QTY_FIELDS.items()})
    return rows


def main() -> None:
    if not BASE_JSON.exists():
        print(f"缺少基线 {BASE_JSON}，先跑 00_baseline.py", file=sys.stderr)
        sys.exit(2)
    base = json.loads(BASE_JSON.read_text(encoding="utf-8"))

    # ---------- 1. 文件 SHA 比对 ----------
    sha_now = {}
    for rel in base["文件SHA"]:
        p = BASE / rel
        sha_now[rel] = {"sha256": sha_of(rel) if p.exists() else None,
                        "exists": p.exists()}
    changed = [rel for rel in sha_now
               if not sha_now[rel]["exists"]
               or sha_now[rel]["sha256"] != base["文件SHA"][rel]["sha256"]]
    unchanged = [rel for rel in sha_now if rel not in changed]

    # ---------- 2. 定量矩阵重算比对 ----------
    old = [{k: x[k] for k in QTY_FIELDS} | {"s": x["s"], "atr": x["atr"], "equity": x["equity"]}
           for x in base["定量矩阵"]]
    try:
        new = recompute_matrix(base)
        recompute_err = None
    except Exception as e:
        new = None
        recompute_err = f"{type(e).__name__}: {e}"

    diffs = []
    if new is not None:
        if len(new) != len(old):
            diffs.append({"type": "组数不符", "old": len(old), "new": len(new)})
        for o, n in zip(old, new):
            for f in QTY_FIELDS:
                ov, nv = o.get(f), n.get(f)
                if ov != nv:
                    diffs.append({"s": o["s"], "atr": o["atr"], "equity": o["equity"],
                                  "field": f, "old": ov, "new": nv})
    # ---------- 3. 常量比对 ----------
    const_drift = []
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    try:
        import importlib
        import live_store as L
        import paper_engine as PE
        importlib.reload(L)
        importlib.reload(PE)
        now_const = {
            "L.INIT_EQUITY": L.INIT_EQUITY,
            "FALLBACK.initial_equity_USDT": PE.FALLBACK.get("initial_equity_USDT"),
            "FALLBACK.risk_per_trade_equity_fraction":
                PE.FALLBACK.get("risk_per_trade_equity_fraction"),
            "FALLBACK.max_positions": PE.FALLBACK.get("max_positions"),
        }
        for k, v in now_const.items():
            if base["关键常量"].get(k) != v:
                const_drift.append({"key": k, "old": base["关键常量"].get(k), "new": v})
    except Exception as e:
        now_const = {"error": f"{type(e).__name__}: {e}"}
        const_drift.append({"key": "<import失败>", "old": None, "new": str(e)})

    # ---------- 4. 受影响项映射 ----------
    invalidated: dict[str, list[str]] = {}
    for rel in changed:
        for item in IMPACT_MAP.get(rel, ["<未登记影响面，需人工判定>"]):
            invalidated.setdefault(item, []).append(rel)

    # ---------- 5. 判定 ----------
    if not changed and not diffs and not const_drift:
        verdict = "全部有效：代码/配置身份未变，定量行为未变，常量未变"
    elif not diffs and not const_drift:
        verdict = "定量有效但身份已变：行为一致，需重新绑定版本号后再引用旧结论"
    else:
        verdict = "部分作废：见 受影响项"

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "基准文件": str(BASE_JSON.relative_to(BASE)),
        "基准生成时刻": base.get("生成时刻"),
        "判定": verdict,
        "文件": {
            "未变": unchanged,
            "已变": changed,
            "明细": {k: {"基准": base["文件SHA"][k]["sha256"][:16],
                         "当前": (v["sha256"] or "")[:16] if v["sha256"] else None}
                     for k, v in sha_now.items()},
        },
        "定量矩阵": {
            "组数": len(old),
            "重算成功": new is not None,
            "重算错误": recompute_err,
            "差异条数": len(diffs),
            "差异": diffs[:80],
            "结论": "行为未变" if (new is not None and not diffs) else "行为已变或无法重算",
        },
        "常量漂移": const_drift,
        "受影响项": invalidated,
        "不受影响项": [i for i in sorted({x for v in IMPACT_MAP.values() for x in v})
                       if i not in invalidated],
    }

    (OUT / "01_回归结果.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    print(f"基准时刻 {base.get('生成时刻')}")
    print(f"文件：未变 {len(unchanged)}，已变 {len(changed)}")
    for rel in changed:
        print(f"  CHANGED {rel}")
        print(f"          基准 {(base['文件SHA'][rel]['sha256'])[:16]}… "
              f"当前 {(sha_now[rel]['sha256'] or 'MISSING')[:16]}")
    print(f"\n定量矩阵 {len(old)} 组，重算{'成功' if new is not None else '失败'}"
          f"，差异 {len(diffs)} 条")
    if recompute_err:
        print(f"  错误：{recompute_err}")
    for d in diffs[:10]:
        print(f"  DIFF s={d.get('s')} atr={d.get('atr')} eq={d.get('equity')} "
              f"{d.get('field')}: {d.get('old')} → {d.get('new')}")
    print(f"\n常量漂移 {len(const_drift)} 条")
    for c in const_drift:
        print(f"  {c['key']}: {c['old']} → {c['new']}")
    print(f"\n判定：{verdict}")
    if invalidated:
        print("受影响项：")
        for item, files in invalidated.items():
            print(f"  - {item}  ← {files}")
    print("\n→ out/01_回归结果.json")


if __name__ == "__main__":
    main()
