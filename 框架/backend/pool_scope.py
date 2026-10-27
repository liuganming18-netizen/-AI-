# -*- coding: utf-8 -*-
"""币池范围解析（唯一事实源）· `scope_crypto_v1`

背景（SIGNAL-LINK-01 核实）
──────────────────────────
本模块原实现**在工程内已不存在**（`框架/backend/logs/_pool_scope.py` 与旧工作区
`取消成交额硬门槛与375新准入37/` 均已无副本），但仍有 12 个模块按名字引用它：

  · `live_pool.py` 顶部 `import _pool_scope as PS` 失败 ⇒ 静默退化到内置
    `_FallbackPoolScope` ⇒ `scope_symbols()` 返回**冻结 375 名单**、
    `pool_source='fallback_frozen_375'`、并带一条 warning；
  · `unified_runtime._default_universe()` 于是不走 `scope_crypto_v1`，改走
    行情库 `candle_heads` 兜底（本次实测同样是 526 个，且与范围文件**逐币相同**）。

也就是说：**标的集合恰好没错，但「池口径」被降级成了兜底路径**，报告与接口里
`pool_source` 会写成 `fallback_frozen_375`，`live_signal_engine` 那条链会按 375
做准入/排名。这属于「身份失真」缺陷，不是「数据缺失」缺陷。

本模块只做一件事：把 `数据/contracts/旧标的池/scope_crypto.json`
（`scope_version=crypto_only_v1`，`binance_items` 526 条，已排除指数
`BTCDOMUSDT`/`ALLUSDT`）如实解析出来，**不新增任何筛选**。

约定（与既有测试一致）
──────────────────────
  · 路径优先取环境变量 `POOL_SCOPE_JSON`；未设则用工程内正式位置；
  · 文件缺失 / 不可解析 ⇒ 返回空列表（**不是**伪造名单），供隔离测试「不过滤」；
  · 本模块**只读**，不写任何文件、不联网。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# 本文件位于 `框架/backend/`，工程根 = parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCOPE_JSON = Path(os.environ.get("POOL_SCOPE_JSON")
                  or (PROJECT_ROOT / "数据" / "contracts" / "旧标的池" / "scope_crypto.json"))

SCOPE_VERSION_EXPECTED = "crypto_only_v1"
EXCLUDED_INDICES = ("BTCDOMUSDT", "ALLUSDT")

_cache: dict = {"path": None, "mtime": None, "data": None}


def _load() -> dict:
    """读并缓存范围文件（按 mtime 失效，避免每次调用都解析 220KB）。"""
    p = Path(SCOPE_JSON)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _cache.update({"path": str(p), "mtime": None, "data": None})
        return {}
    if _cache["data"] is not None and _cache["path"] == str(p) and _cache["mtime"] == mtime:
        return _cache["data"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    _cache.update({"path": str(p), "mtime": mtime, "data": data})
    return data


def binance_scope_symbols() -> list:
    """币安 USD-M 加密币范围（`crypto_only_v1`，当前 526 个）。

    文件缺失/不可解析 ⇒ 返回 `[]`（调用方据此判「不过滤」，**不得**凭空造名单）。
    """
    d = _load()
    if not isinstance(d, dict):
        return []
    items = d.get("binance_items")
    out = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("symbol"):
                sym = str(it["symbol"])
                if sym not in EXCLUDED_INDICES:
                    out.append(sym)
    if not out:                                   # 兼容旧结构（只有 symbols 列表）
        raw = d.get("symbols") or d.get("binance") or []
        if isinstance(raw, list):
            out = [str(s) for s in raw if s and str(s) not in EXCLUDED_INDICES]
    return out


def binance_items() -> list:
    """币安条目的完整记录（含 `onboard_time`/`status`/`underlying_type`）。"""
    d = _load()
    items = d.get("binance_items") if isinstance(d, dict) else None
    return list(items) if isinstance(items, list) else []


def okx_symbols() -> list:
    d = _load()
    raw = d.get("okx_symbols") if isinstance(d, dict) else None
    return [str(s) for s in raw] if isinstance(raw, list) else []


def scope_meta() -> dict:
    """口径元信息（供接口/报告照抄，不加工）。"""
    d = _load()
    if not isinstance(d, dict) or not d:
        return {"pool_source": "scope_crypto_v1", "available": False,
                "scope_json": str(SCOPE_JSON),
                "note": "范围文件缺失或不可解析 ⇒ 不过滤（隔离测试用）"}
    syms = binance_scope_symbols()
    return {
        "pool_source": "scope_crypto_v1",
        "available": True,
        "scope_json": str(SCOPE_JSON),
        "scope_version": d.get("scope_version"),
        "scope_version_expected": SCOPE_VERSION_EXPECTED,
        "generated_utc": d.get("generated_utc"),
        "counts": d.get("counts"),
        "n": len(syms),
        "excluded_indices": list(EXCLUDED_INDICES),
        "sha256": _sha256_file(Path(SCOPE_JSON)),
    }


def _sha256_file(p: Path):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
