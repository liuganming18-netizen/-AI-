#!/usr/bin/env python3
"""路由表与旧长链的**离线等价性**比对（架构重构第 1 期门禁）。

做法
----
不经过 HTTP，直接在同一进程里对同一路径分别调用：

    adapter._route_v1_legacy(path, params)   # 旧实现
    adapter.route_v1(path, params)           # 新路由表（默认）

两边返回的 `(body, status)` 逐字段比较。时间戳、耗时、年龄这类天然会变的
字段按**叶子键名**屏蔽（与 `api_snapshot.py` 同一套口径），其余必须严格相等。

为什么要有这一层
----------------
HTTP 快照比对证明「对外行为没变」；本脚本证明「内部分发也没变」。
两者都过，才算第 1 期完成。发现差异时直接打印路径与两侧取值，便于定位。

用法
----
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY tools/route_parity.py
退出码：0 = 全部一致；1 = 有差异；2 = 导入或执行失败。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

# 与快照工具同源的端点清单（参数固定、只读）
sys.path.insert(0, str(HERE))
from api_snapshot import ENDPOINTS  # noqa: E402

# 边界路径：未知子资源、空段、编码段、旧复盘契约等。
# 这些不在常规快照里，但最容易被新路由表「多匹配」或「少匹配」。
EDGE_PATHS: list[tuple[str, str]] = [
    ("edge_unknown_tail", "/api/v1/markets/binance/BTCUSDT/nope"),
    ("edge_paper_unknown", "/api/v1/paper/nope"),
    ("edge_backtest_one", "/api/v1/backtests/bt_20260101T000000Z_aaaaaaaa"),
    ("edge_backtest_sub", "/api/v1/backtests/bt_20260101T000000Z_aaaaaaaa/metrics"),
    ("edge_backtest_mode", "/api/v1/backtests?mode=history"),
    ("edge_reviews", "/api/v1/reviews"),
    ("edge_whitelist", "/api/v1/data/whitelist"),
    ("edge_backfill", "/api/v1/data/backfill"),
    ("edge_signal_missing", "/api/v1/signals/does-not-exist"),
    ("edge_strategy_missing", "/api/v1/strategies/does-not-exist"),
    ("edge_root", "/api/v1/"),
    ("edge_deep_unknown", "/api/v1/unknown/deep/path"),
]

VOLATILE_LEAVES = {
    "as_of", "age_seconds", "requested_at", "generated_at", "server_time",
    "updated_at", "fetched_at", "duration_ms", "elapsed_ms", "now",
}

# 说明文案里会内嵌「新鲜度 17531.5s」「17749s 前」这类随时间变化的数字。
# 本脚本只校验**分发是否等价**，因此把字符串里的数字统一归一化；
# 数值字段仍按数值严格比较（不受影响），对外内容的严格比对由 HTTP 快照负责。
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def _mask(node):
    if isinstance(node, dict):
        return {k: ("<易变>" if (k in VOLATILE_LEAVES or k.endswith("age_seconds"))
                    else _mask(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask(v) for v in node]
    if isinstance(node, str):
        return _DIGITS.sub("#", node)
    return node


def _canon(node) -> str:
    return json.dumps(_mask(node), ensure_ascii=False, sort_keys=True, default=str)


def main() -> int:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    import adapter  # noqa: E402

    # 强制构造路由表（不依赖 LEGACY_ROUTER 开关）
    os.environ["LEGACY_ROUTER"] = "0"
    adapter.LEGACY_ROUTER = False
    table_router = adapter._router()

    mismatches: list[str] = []
    checked = 0
    for name, path in ENDPOINTS + EDGE_PATHS:
        parsed = urlsplit(path)
        params = parse_qs(parsed.query, keep_blank_values=True)
        try:
            old_body, old_status = adapter._route_v1_legacy(parsed.path, params)
        except LookupError as exc:
            old_body, old_status = {"错误": str(exc)}, 404
        except Exception as exc:  # noqa: BLE001
            old_body, old_status = {"__exception__": f"{type(exc).__name__}: {exc}"}, 599

        try:
            new_body, new_status = table_router.resolve(parsed.path, params)
        except LookupError as exc:
            new_body, new_status = {"错误": str(exc)}, 404
        except Exception as exc:  # noqa: BLE001
            new_body, new_status = {"__exception__": f"{type(exc).__name__}: {exc}"}, 599

        checked += 1
        if old_status != new_status:
            mismatches.append(f"[状态码] {name} {path}: {old_status} -> {new_status}")
            continue
        a, b = _canon(old_body), _canon(new_body)
        if a != b:
            # 找第一个不同的字符位置，截取上下文
            pos = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            mismatches.append(
                f"[内容] {name} {path}\n    旧: …{a[max(0, pos-60):pos+120]}…\n"
                f"    新: …{b[max(0, pos-60):pos+120]}…")

    print(f"比对 {checked} 个路径，差异 {len(mismatches)} 处")
    for line in mismatches:
        print("  " + line)
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
