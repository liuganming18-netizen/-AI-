#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把生产登记库的当前状态原样导出（只读，不写生产）。

用途：FRAME-FIT-02 交付期间发现生产登记库被写入 8 条事件，来源无法逐条归因。
本脚本只做「拍照存档」，让独立复验能核对当时到底是什么样，不承担清理职责。

运行：
    /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python \
        evidence/FRAME-FIT-02/scripts/dump_production_registry_state.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]                      # 工程根
BACKEND = PROJECT / "框架" / "backend"
REG_DIR = BACKEND / "data" / "experiments"
OUT = PROJECT / "evidence" / "FRAME-FIT-02" / "logs" / "production_registry_state.txt"

sys.path.insert(0, str(BACKEND))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    lines: list[str] = []
    w = lines.append

    w("生产登记库状态快照（只读导出，本脚本不写生产）")
    w("=" * 78)
    w(f"导出时点 : {__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')}")
    w(f"登记库目录: {REG_DIR}")
    w(f"解释器   : {sys.executable}")
    w("")

    if not REG_DIR.exists():
        w("!! 登记库目录不存在")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 1

    for name in ("events.jsonl", "head.json"):
        p = REG_DIR / name
        if p.exists():
            w(f"{name}: {p.stat().st_size} 字节  sha256={sha256_file(p)}")
        else:
            w(f"{name}: 不存在")
    w("")

    ev = REG_DIR / "events.jsonl"
    raw = ev.read_text(encoding="utf-8").splitlines() if ev.exists() else []
    rows = [json.loads(x) for x in raw if x.strip()]
    w(f"事件总数: {len(rows)}")
    w("")

    w("逐条事件")
    w("-" * 78)
    for i, r in enumerate(rows):
        e = r.get("event") or {}
        w(f"[{i:02d}] seq={r.get('seq')}  type={e.get('type')}")
        w(f"     at   = {e.get('at')}")
        w(f"     by   = {e.get('by')}")
        w(f"     exp  = {e.get('experiment_id')}")
        for k in ("mode", "note", "run_id", "outcome", "design_identity", "run_identity"):
            if k in e:
                w(f"     {k:<5}= {e.get(k)}")
        if "sample_scope" in e:
            w(f"     scope= {json.dumps(e['sample_scope'], ensure_ascii=False)}")
        w(f"     hash = {r.get('hash')}")
        w(f"     prev = {r.get('prev')}")
        w("")

    w("链头文件 head.json")
    w("-" * 78)
    hp = REG_DIR / "head.json"
    w(hp.read_text(encoding="utf-8").strip() if hp.exists() else "不存在")
    w("")

    w("哈希链校验（用登记库自己的 verify，不改任何文件）")
    w("-" * 78)
    try:
        import experiment_registry as E
        reg = E.ExperimentRegistry(REG_DIR)
        v = reg.verify()
        w(json.dumps(v, ensure_ascii=False, indent=2, default=str))
        w("")
        st = reg.state()
        exps = st.get("experiments") or {}
        w(f"实验数: {len(exps)}")
        for eid, ex in exps.items():
            w(f"  {eid}  mode={ex.get('mode')}  frozen_at={ex.get('frozen_at')}  "
              f"runs={len(ex.get('runs') or [])}  trials={len(ex.get('trials') or [])}  "
              f"abandoned={ex.get('abandoned_at')}")
    except Exception as exc:                    # noqa: BLE001
        w(f"!! 校验失败: {type(exc).__name__}: {exc}")
    w("")

    w("归因说明")
    w("-" * 78)
    w("序号 00 时点 01:29:24，早于 FRAME-FIT-02 开始（02:40）⇒ 不是本单。")
    w("序号 01-08 时点 03:00:47~03:01:47，落在本单时段内，来源无法逐条归因。")
    w("本单交付的 tests/test_frame_fit_02.py 与 probe_launch_before_after.py 均已验证会把")
    w("FRAME_FIT_REGISTRY 重定向到临时目录；但本单在定稿前反复调过真实 create_run，")
    w("故按「待定」记录。清理需用户明确指示，且必须备份 + 重建哈希链，不能手删中间行。")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
