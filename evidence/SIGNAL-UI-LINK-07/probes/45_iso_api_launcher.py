#!/usr/bin/env python3
"""45_iso_api_launcher.py —— 隔离 API 启动器（**只为页面级验收**，不改生产任何文件）

为什么需要它：
    `live_store.get_paper_epoch_ms()` 是 **fail-closed** 设计 ——
        return max(库里登记的 paper_epoch_ms, 冻结常量 PAPER_EPOCH_MS)
    即「只允许不降」。这是对的，不能为了测试去削弱它。
    但页面级验收需要「模拟启用时点」早于候选（本单 30 号探针的隔离时钟在
    2026-10-02，而生产冻结启用时点是 2026-10-14）⇒ 必须在本进程内把常量改小。

做法：
    在 import adapter 之前，把 `live_store.PAPER_EPOCH_MS` 这个**模块级全局**
    改成本次隔离验收的模拟启用时点。仅影响**本进程内存**；
    `live_store.py` / 配置 / 门禁文件在磁盘上一个字节都不动（有 sha 前后对比为证）。

用法：
    ISO_PAPER_EPOCH_MS=1790870400000 PORT=8799 python 45_iso_api_launcher.py
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

epoch = os.environ.get("ISO_PAPER_EPOCH_MS")
if not epoch:
    raise SystemExit("必须显式给出 ISO_PAPER_EPOCH_MS（本启动器只用于隔离验收）")

import live_store as L  # noqa: E402

_orig = int(L.PAPER_EPOCH_MS)
L.PAPER_EPOCH_MS = int(epoch)
print(f"[iso-launcher] PAPER_EPOCH_MS {_orig} -> {L.PAPER_EPOCH_MS}（仅本进程内存）", flush=True)
print(f"[iso-launcher] LIVE_DB={os.environ.get('LIVE_DB')}", flush=True)
print(f"[iso-launcher] PAPER_LEDGER_DIR={os.environ.get('PAPER_LEDGER_DIR')}", flush=True)

import adapter  # noqa: E402

adapter.main()
