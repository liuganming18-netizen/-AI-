# -*- coding: utf-8 -*-
"""兼容垫片：`_pool_scope` → `pool_scope`

为什么存在这个文件
──────────────────
`live_pool.py` 在导入期把 `框架/backend/logs/` 加进 `sys.path`，然后
`import _pool_scope as PS`。这个「日志目录里放模块」的历史布局在
SIGNAL-LINK-01 核查时发现**文件已丢失**（全盘无副本），导致 `live_pool`
静默退化到 `fallback_frozen_375`（375 个标的）——池口径被降级。

现改为：**实现放在正规位置** `框架/backend/pool_scope.py`，本文件只做转发。
这样即使 `logs/` 目录被日志清理误删，也只需重建这一个三行垫片，
真正的解析逻辑不会被日志清理带走。

接口与旧 `_pool_scope` 保持一致：`SCOPE_JSON` / `binance_scope_symbols()` /
`scope_meta()`。
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pool_scope import (            # noqa: E402,F401
    EXCLUDED_INDICES,
    SCOPE_JSON,
    SCOPE_VERSION_EXPECTED,
    binance_items,
    binance_scope_symbols,
    okx_symbols,
    scope_meta,
)

__all__ = [
    "SCOPE_JSON",
    "SCOPE_VERSION_EXPECTED",
    "EXCLUDED_INDICES",
    "binance_scope_symbols",
    "binance_items",
    "okx_symbols",
    "scope_meta",
]
