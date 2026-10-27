#!/usr/bin/env python3
"""接口快照与比对工具（架构重构护栏）。

用途
----
1) 采集：把一组**只读 GET** 接口的真实响应抓成本地快照。
2) 比对：改造后再抓一次，逐字段比较，证明「行为零变化」。

为什么不能只比 JSON 字符串
--------------------------
响应里有时间戳、耗时、实时价这类天然会变的字段。直接字符串比对必然误报。
本工具用**两次连拍**自动识别「本来就一直在变」的路径：
    t0 抓一次 → t1 抓一次（间隔约 2 秒）→ 两边不同的路径记为易变路径
比对时把这些路径（含数组下标通配）整体忽略，其余字段**逐值严格相等**才算通过。

用法
----
    PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
    $PY tools/api_snapshot.py capture --out evidence/基线_xxx
    $PY tools/api_snapshot.py diff --base evidence/基线_xxx --now evidence/改造后_xxx

退出码：0 = 无差异；1 = 有差异；2 = 采集/读取失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("SNAPSHOT_BASE_URL", "http://127.0.0.1:8787")
TIMEOUT = float(os.environ.get("SNAPSHOT_TIMEOUT", "60"))

# 只读端点清单。参数固定，保证可重复。
# 不含任何会写库/下单/建任务的接口。
ENDPOINTS: list[tuple[str, str]] = [    ("health", "/api/health"),
    ("v1_health", "/api/v1/health"),
    ("v1_overview", "/api/v1/overview"),
    ("v1_markets", "/api/v1/markets"),
    ("v1_pool", "/api/v1/pool"),
    ("v1_pool_status", "/api/v1/pool/status"),
    ("v1_strategies", "/api/v1/strategies"),
    ("v1_strategies_registered", "/api/v1/strategies/registered"),
    ("v1_strategy", "/api/v1/strategy"),
    ("v1_signals", "/api/v1/signals?limit=20"),
    ("v1_signals_all", "/api/v1/signals?limit=20&scope=all"),
    ("v1_live_health", "/api/v1/live/health"),
    ("v1_accounts", "/api/v1/accounts"),
    ("v1_paper_accounts", "/api/v1/paper/accounts"),
    ("v1_paper_positions", "/api/v1/paper/positions"),
    ("v1_paper_orders", "/api/v1/paper/orders?limit=20"),
    ("v1_paper_funding_audit", "/api/v1/paper/funding-audit"),
    ("v1_paper_legacy_audit", "/api/v1/paper/legacy-audit"),
    ("v1_backtests", "/api/v1/backtests?limit=20"),
    ("v1_data_status", "/api/v1/data/status"),
    ("v1_data_coverage", "/api/v1/data/coverage"),
    ("v1_data_import_plan", "/api/v1/data/import-plan"),
    ("v1_data_oi", "/api/v1/data/oi?limit=20"),
    ("v1_data_funding", "/api/v1/data/funding?limit=20"),
    ("v1_data_smart_money", "/api/v1/data/smart-money?limit=20"),
    ("v1_data_all_in_one", "/api/v1/data/all-in-one?limit=20"),
    ("v1_data_feeds", "/api/v1/data/feeds"),
    ("v1_data_pool_scope", "/api/v1/data/pool-scope"),
    ("v1_data_field_contract", "/api/v1/data/field-contract"),
    ("v1_data_whitelist", "/api/v1/data/whitelist"),
    ("v1_market_one", "/api/v1/markets/binance/BTCUSDT"),
    ("v1_candles_1h", "/api/v1/markets/binance/BTCUSDT/candles?timeframe=1h&limit=50"),
    ("v1_candles_1d", "/api/v1/markets/binance/BTCUSDT/candles?timeframe=1d&limit=50"),
    ("v1_market_oi", "/api/v1/markets/binance/BTCUSDT/oi?limit=20"),
    ("v1_market_funding", "/api/v1/markets/binance/BTCUSDT/funding?limit=20"),
    ("v1_rule_snapshots", "/api/v1/markets/binance/BTCUSDT/rule-snapshots"),
    ("v1_public_extras", "/api/v1/markets/binance/BTCUSDT/public-extras"),
    ("v1_market_metrics", "/api/v1/markets/binance/BTCUSDT/metrics?timeframe=1h&limit=20"),
    ("v1_reviews", "/api/v1/reviews?limit=20"),
    ("v1_unknown", "/api/v1/definitely-not-a-route"),
]

# 这些端点的**取值**会随行情、采集、外部批量任务而变（新 run、新 K 线、实时价）。
# 对它们只校验「结构与类型不变」；取值变化单独列出、不计为失败。
# 其余端点属于契约/配置类，取值必须严格一致。
STRUCT_ONLY = {
    "v1_overview", "v1_markets", "v1_pool", "v1_pool_status",
    "v1_signals", "v1_signals_all", "v1_live_health", "v1_accounts",
    "v1_paper_accounts", "v1_paper_positions", "v1_paper_orders",
    "v1_paper_funding_audit", "v1_paper_legacy_audit",
    "v1_backtests", "v1_reviews",
    "v1_data_status", "v1_data_coverage", "v1_data_import_plan",
    "v1_data_oi", "v1_data_funding", "v1_data_smart_money",
    "v1_data_all_in_one", "v1_data_feeds",
    "v1_market_one", "v1_candles_1h", "v1_candles_1d",
    "v1_market_oi", "v1_market_funding", "v1_public_extras", "v1_market_metrics",
}


def _get(path: str) -> tuple[int, bytes, float]:
    url = BASE_URL.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    # 本机回环**必须**绕过系统代理：否则 http_proxy 会把请求转给代理，
    # 代理再回连 127.0.0.1 会得到 RemoteDisconnected（实测）。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            return resp.status, body, (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, body, (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"请求失败 {path}: {type(exc).__name__}: {exc}") from exc


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"__non_json__": body.decode("utf-8", "replace")}


def _walk(node: Any, prefix: str = "") -> dict[str, Any]:
    """把 JSON 展开成 {路径: 值}，数组下标统一写成 *。"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        if not node:
            out[prefix or "/"] = {}
        for key, val in node.items():
            out.update(_walk(val, f"{prefix}/{key}"))
    elif isinstance(node, list):
        if not node:
            out[prefix or "/"] = []
        for item in node:
            out.update(_walk(item, f"{prefix}/*"))
    else:
        out[prefix or "/"] = node
    return out


def _volatile_paths(a: Any, b: Any) -> list[str]:
    fa, fb = _walk(a), _walk(b)
    return sorted(p for p in set(fa) | set(fb) if fa.get(p, "\0missing") != fb.get(p, "\0missing"))


# 显式易变键：两次连拍可能捕捉不到（例如只在写库后才变的字段）。
EXPLICIT_VOLATILE_KEYS = {
    "as_of", "age_seconds", "requested_at", "generated_at", "server_time",
    "updated_at", "fetched_at", "duration_ms", "elapsed_ms", "now",
}


def _mask(node: Any, volatile: set[str], prefix: str = "") -> Any:
    if isinstance(node, dict):
        return {k: _mask(v, volatile, f"{prefix}/{k}") for k, v in node.items()}
    if isinstance(node, list):
        return [_mask(v, volatile, f"{prefix}/*") for v in node]
    leaf = prefix.rsplit("/", 1)[-1]
    return "<易变>" if (prefix in volatile or leaf in EXPLICIT_VOLATILE_KEYS) else node


def _slug(name: str) -> str:
    return name


def _capture_once(out: Path) -> dict[str, Any]:
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"base_url": BASE_URL, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "endpoints": {}}
    for name, path in ENDPOINTS:
        status, body, ms = _get(path)
        parsed = _parse(body)
        (raw_dir / f"{_slug(name)}.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        index["endpoints"][name] = {
            "path": path,
            "status": status,
            "bytes": len(body),
            "sha256_raw": hashlib.sha256(body).hexdigest(),
            "duration_ms": round(ms, 1),
        }
        print(f"  {name:26s} {status}  {len(body):>9,}B  {ms:8.1f}ms", flush=True)
    return index


def cmd_capture(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("[1/3] 第一次采集", flush=True)
    t0_dir = out / "_probe_t0"
    idx0 = _capture_once(t0_dir)
    time.sleep(2.0)
    print("[2/3] 第二次采集（用于识别天然易变字段）", flush=True)
    t1_dir = out / "_probe_t1"
    idx1 = _capture_once(t1_dir)

    volatile: set[str] = set()
    for name, _ in ENDPOINTS:
        a = json.loads((t0_dir / "raw" / f"{name}.json").read_text(encoding="utf-8"))
        b = json.loads((t1_dir / "raw" / f"{name}.json").read_text(encoding="utf-8"))
        for p in _volatile_paths(a, b):
            volatile.add(p)

    # 显式易变键（两次连拍可能捕捉不到，例如只在写库后才变的字段）
    # 这些键按「叶子键名」整体屏蔽，见 `_mask`。

    print(f"[3/3] 生成基线：易变路径 {len(volatile)} 条", flush=True)
    norm_dir = out / "norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    for name, _ in ENDPOINTS:
        raw = json.loads((t1_dir / "raw" / f"{name}.json").read_text(encoding="utf-8"))
        masked = _mask(raw, volatile)
        (norm_dir / f"{name}.json").write_text(
            json.dumps(masked, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    (out / "volatile_paths.json").write_text(
        json.dumps(sorted(volatile), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.json").write_text(
        json.dumps(idx1, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    import shutil
    shutil.rmtree(t0_dir, ignore_errors=True)
    shutil.rmtree(t1_dir, ignore_errors=True)
    print(f"完成：{out}", flush=True)
    return 0


# 这些计数走**非阻塞重统计**：进程刚启动时后台线程尚未算完，字段如实返回 null
# （不填 0 冒充）。因此「有值 ↔ null」不算行为变化，只记漂移。
ALLOW_NULL_KEYS = {"external_calls", "external_calls_ok",
                   "external_calls_failed", "source_files"}


def _null_allowed(path: str, a: Any, b: Any) -> bool:
    leaf = path.rsplit("/", 1)[-1]
    return leaf in ALLOW_NULL_KEYS and (a is None or b is None)


def _shape(node: Any, prefix: str = "") -> dict[str, str]:
    """把 JSON 压成 {路径: 类型}，数组下标统一写成 *。用于结构比对。"""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        if not node:
            out[prefix or "/"] = "dict"
        for key, val in node.items():
            out.update(_shape(val, f"{prefix}/{key}"))
    elif isinstance(node, list):
        if not node:
            out[prefix or "/"] = "list"
        for item in node:
            out.update(_shape(item, f"{prefix}/*"))
    else:
        out[prefix or "/"] = type(node).__name__
    return out


def cmd_diff(args: argparse.Namespace) -> int:
    base = Path(args.base)
    now = Path(args.now)
    volatile = set(json.loads((base / "volatile_paths.json").read_text(encoding="utf-8")))
    base_idx = json.loads((base / "index.json").read_text(encoding="utf-8"))
    now_idx = json.loads((now / "index.json").read_text(encoding="utf-8"))

    problems: list[str] = []
    drifts: list[str] = []
    for name, meta in base_idx["endpoints"].items():
        cur = now_idx["endpoints"].get(name)
        if cur is None:
            problems.append(f"[缺失] {name}")
            continue
        if cur["status"] != meta["status"]:
            problems.append(f"[状态码变化] {name}: {meta['status']} -> {cur['status']}")
        a = json.loads((base / "norm" / f"{name}.json").read_text(encoding="utf-8"))
        b = json.loads((now / "norm" / f"{name}.json").read_text(encoding="utf-8"))
        b = _mask(b, volatile)

        # ① 结构：路径与类型必须完全一致（任何端点都查）
        sa, sb = _shape(a), _shape(b)
        fa0, fb0 = _walk(a), _walk(b)
        for p in sorted(set(sa) | set(sb)):
            if sa.get(p) != sb.get(p):
                if _null_allowed(p, fa0.get(p), fb0.get(p)):
                    drifts.append(f"{name} {p}: {sa.get(p)} -> {sb.get(p)}（统计尚未就绪）")
                    continue
                problems.append(f"[结构变化] {name} {p}: {sa.get(p, '<无>')} -> {sb.get(p, '<无>')}")

        # ② 取值：契约类端点严格比对；数据类端点只登记漂移
        fa, fb = fa0, fb0
        diffs = [p for p in sorted(set(fa) | set(fb))
                 if fa.get(p, "\0missing") != fb.get(p, "\0missing")
                 and not _null_allowed(p, fa.get(p), fb.get(p))]
        target = drifts if name in STRUCT_ONLY else problems
        for p in diffs[: args.max_per_endpoint]:
            target.append(f"{name} {p}: {str(fa.get(p, '<无>'))[:70]} -> {str(fb.get(p, '<无>'))[:70]}")
        if len(diffs) > args.max_per_endpoint:
            target.append(f"{name} 另有 {len(diffs) - args.max_per_endpoint} 处未列出")

    if drifts:
        print(f"数据漂移 {len(drifts)} 处（数据类端点，不计为失败）：")
        for line in drifts:
            print("  · " + line)
    if problems:
        print(f"发现 {len(problems)} 处**行为差异**：")
        for line in problems:
            print("  " + line)
        return 1
    print(f"通过：{len(base_idx['endpoints'])} 个端点结构与状态码全部一致，"
          f"契约类端点取值零差异")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="接口快照与比对")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("capture", help="采集基线")
    p_cap.add_argument("--out", required=True)
    p_cap.set_defaults(func=cmd_capture)

    p_diff = sub.add_parser("diff", help="比对两次快照")
    p_diff.add_argument("--base", required=True)
    p_diff.add_argument("--now", required=True)
    p_diff.add_argument("--max-per-endpoint", type=int, default=5)
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
