# -*- coding: utf-8 -*-
"""UI-LINK-01 · 构建与源码绑定清单（机械复算，禁止手抄哈希）。

产出 `responses/build_and_source_hashes.json`：
  · 工单点名的 5 个源文件 + 画布 HTML 的字节数与 SHA-256
  · 前端构建产物（`dist/assets/*`）的字节数与 SHA-256
  · `public/system-flow.html` 与 `dist/system-flow.html` 是否**逐字节一致**
  · 每个源文件的 mtime 与构建产物的 mtime，用来自证「构建晚于源码改动」

复现：
  python3 probes/build_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
WEB = ROOT / "UI界面" / "web"

SOURCES = [
    WEB / "public" / "system-flow.html",
    WEB / "src" / "App.tsx",
    WEB / "src" / "components" / "BacktestWorkbench.tsx",
    WEB / "src" / "api" / "useLiveTrading.ts",
    WEB / "src" / "api" / "liveTrading.ts",
    WEB / "src" / "components" / "PositionPanel.tsx",
    # 本工单额外修改（缺陷修复，见 README 第 1 节）：
    WEB / "src" / "components" / "backtest" / "MetricStrip.tsx",
]

DIST_FIXED = [WEB / "dist" / "system-flow.html"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entry(p: Path) -> dict:
    st = p.stat()
    return {
        "file": str(p.relative_to(ROOT)),
        "bytes": st.st_size,
        "sha256": sha256(p),
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main() -> int:
    out: dict = {"generated_at": datetime.now(timezone.utc)
                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "sources": [], "dist": [], "checks": []}

    missing = [str(p) for p in SOURCES if not p.exists()]
    if missing:
        print(json.dumps({"error": "源文件缺失", "missing": missing},
                         ensure_ascii=False, indent=1))
        return 1

    out["sources"] = [entry(p) for p in SOURCES]

    dist_assets = sorted((WEB / "dist" / "assets").glob("*"))
    out["dist"] = [entry(p) for p in DIST_FIXED + dist_assets if p.exists()]

    # 画布：public 与 dist 必须逐字节一致，否则「构建没把新画布带上」
    pub = WEB / "public" / "system-flow.html"
    dis = WEB / "dist" / "system-flow.html"
    a, b = sha256(pub), sha256(dis)
    out["checks"].append({
        "item": "public/system-flow.html == dist/system-flow.html",
        "public_sha256": a, "dist_sha256": b,
        "public_bytes": pub.stat().st_size, "dist_bytes": dis.stat().st_size,
        "pass": a == b,
    })

    # 构建晚于源码改动：dist js 的 mtime 必须 >= 每个源文件 mtime
    js = [p for p in dist_assets if p.suffix == ".js"]
    if js:
        newest_src = max(SOURCES, key=lambda p: p.stat().st_mtime)
        js_mtime = max(p.stat().st_mtime for p in js)
        out["checks"].append({
            "item": "构建产物晚于全部源码改动",
            "newest_source": str(newest_src.relative_to(ROOT)),
            "newest_source_mtime_utc": datetime
                .fromtimestamp(newest_src.stat().st_mtime, timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dist_js_mtime_utc": datetime
                .fromtimestamp(js_mtime, timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pass": js_mtime >= newest_src.stat().st_mtime,
        })

    out["all_pass"] = all(c["pass"] for c in out["checks"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
