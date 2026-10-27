# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜11 全池 + 绝对锚点 的重启一致性（R1 工单第 1 条的核心未完成项）。

**为什么需要这个脚本**：06 的 `03_restart_consistency.py` 已经支持 `--anchor-hours`，
但只在 **7 个标的**上跑过（`out/03_重启一致性_D2880_anchor2880.json`：0 差异、候选 3/3）。
工单明确「固定历史起点的 7 币测试通过，不能替代生产滚动窗口验证」⇒ 必须上 **全池 526**。

**本脚本不重写任何逻辑**：直接 `importlib` 载入 06 的 `03_restart_consistency.py`，
只把它的输出目录从 `06/out` 改到 `R1/out`，然后原样调用它的 `main()`。
这样「被测代码 = 06 已验证的代码」，不引入第二套实现。

**锚点从哪来（工单硬要求：不能每次启动重新算一个"固定起点"）**：
本脚本**不自己算锚点**。它先把 `LIVE_DB` 指到隔离库，再调用生产的
`live_store.ensure_state_anchor_ms()` —— 也就是生产 `run_forever()` 用的同一个函数 ——
由它**落定并读回**。第一次落定、第二次原样读回，因此「锚点是持久化的」这件事
在同一个脚本里就被证明了（详见 `10_anchor_semantics.py` 的专项验证）。

用法：
  python code/11_full_pool_anchor_restart.py [--upto 2026-10-25T04:59:59Z]
                                            [--anchor-hours 2880] [--symbols A,B]
产出：out/11_全池重启一致性_*.json / out/11_逐币重启对照_*.csv
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
OUT.mkdir(parents=True, exist_ok=True)

# 复用 06 的公共件（含 prod_session / load_hist / snapshot / diff_key）
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
# **关键**：只改输出目录，其余行为完全不变
cm.OUT = OUT

H1 = cm.H1


def main() -> int:
    argv = sys.argv[1:]
    upto_s = "2026-10-25T04:59:59Z"
    anchor_h = 2880
    depth = 2880
    syms = None
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto_s = argv[i + 1]
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth = int(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms = argv[i + 1]

    upto = cm.parse_utc_ms(upto_s)

    # ── 锚点：由生产函数在隔离库里落定并读回（不自己算）────────────────────
    iso_live = R1 / "out" / "11_iso_live"
    iso_live.mkdir(parents=True, exist_ok=True)
    os.environ["LIVE_DB"] = str(iso_live / "live.sqlite3")
    os.environ["no_proxy"] = "*"
    import live_store as L                      # 必须在设置 LIVE_DB 之后 import

    replay_ms = max(int(cm.warmup_ms()), depth * H1)
    anchor1 = L.ensure_state_anchor_ms(replay_ms, upto)
    anchor2 = L.ensure_state_anchor_ms(replay_ms, upto + 7 * H1)   # 晚 7h 再调
    print(f"[anchor] 第一次落定 = {cm.utc(anchor1)}")
    print(f"[anchor] 晚 7h 再调   = {cm.utc(anchor2)}  "
          f"（相同={anchor1 == anchor2}，即不随后移）")
    if anchor1 != anchor2:
        print("[FATAL] 锚点会随启动时刻后移 —— 这不是固定锚点，停止。")
        return 2

    # ── 交给 06 的 03 脚本原样执行 ────────────────────────────────────────
    mod = importlib.import_module("03_restart_consistency")
    mod.cm = cm                                 # 让它的 dump 走 R1/out
    sys.argv = ["03_restart_consistency.py",
                "--upto", upto_s,
                "--depth", str(depth),
                "--anchor-hours", str(anchor_h)]
    if syms:
        sys.argv += ["--symbols", syms]
    rc = mod.main()

    # ── 改名，避免与 06 的证据同名混淆 ────────────────────────────────────
    tag = f"D{depth}_anchor{anchor_h}" + ("_fullpool" if not syms else "_sub")
    ren = {
        f"03_重启一致性_{tag}.json": f"11_全池重启一致性_{tag}.json",
        f"03_逐币重启对照_{tag}.csv": f"11_逐币重启对照_{tag}.csv",
    }
    for a, b in ren.items():
        p = OUT / a
        if p.exists():
            shutil.move(str(p), str(OUT / b))
            print(f"[rename] {a} → {b}")

    # ── 给结果补一段「锚点来源」说明（不覆盖 06 原字段）──────────────────
    import json
    jp = OUT / f"11_全池重启一致性_{tag}.json"
    if jp.exists():
        d = json.loads(jp.read_text(encoding="utf-8"))
        d["r1_anchor_provenance"] = {
            "source": "live_store.ensure_state_anchor_ms（生产 run_forever 同一函数）",
            "iso_live_db": str(iso_live / "live.sqlite3"),
            "anchor_utc_first": cm.utc(anchor1),
            "anchor_utc_second_call_plus7h": cm.utc(anchor2),
            "anchor_stable": anchor1 == anchor2,
            "note": ("锚点由生产函数落定并读回，脚本不自行计算；"
                     "第二次调用（晚 7 小时）返回同一值 ⇒ 不随后移。"),
        }
        jp.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str),
                      encoding="utf-8")
        print(f"[out] 已补锚点来源字段：{jp.name}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
