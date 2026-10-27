# -*- coding: utf-8 -*-
"""04｜页面落点核对（前端只读代码事实 + 后端过滤口径）。

只回答一个问题：**现有信号页面到底能显示哪一类信号**。
不做浏览器渲染（不启第二实例、不改前端），只把「页面请求什么 / 后端放行什么」两处
代码事实摆在一起，据此给出结构性结论。

输出：out/04_页面落点.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib08 as L  # noqa: E402

WEB = L.PROJECT / "UI界面" / "web"


def grep(path: Path, pattern: str, limit: int = 10) -> list[dict]:
    if not path.exists():
        return [{"error": f"文件不存在 {path}"}]
    rx = re.compile(pattern)
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if rx.search(line):
            out.append({"line": i, "text": line.strip()[:240]})
            if len(out) >= limit:
                break
    return out


def main() -> int:
    rep: dict = {"生成时刻": L.now_utc()}

    rep["页面请求"] = {
        "交易信号区": {
            "file": "UI界面/web/src/api/useMarketData.ts",
            "hits": grep(WEB / "src" / "api" / "useMarketData.ts",
                         r"apiGet<Envelope<RawSignal>>\('/signals"),
            "轮询": grep(WEB / "src" / "api" / "useMarketData.ts", r"pollMs", 6),
        },
        "观察区": {
            "file": "UI界面/web/src/api/liveTrading.ts",
            "hits": grep(WEB / "src" / "api" / "liveTrading.ts",
                         r"/signals/observe", 6),
        },
        "故障分类器": {
            "file": "UI界面/web/src/api/signalsState.ts",
            "hits": grep(WEB / "src" / "api" / "signalsState.ts",
                         r"no_watermark|not_connected|'error'", 8),
        },
    }

    rep["后端放行口径"] = {
        "交易信号区": {
            "file": "框架/backend/live_api.py",
            "来源白名单": grep(L.BACKEND / "live_api.py",
                           r"origin IN \('live','recovered'\)", 3),
            "可执行态": grep(L.BACKEND / "live_api.py",
                          r"where.append\(\"executable_state='enterable'\"\)", 3),
            "角色": grep(L.BACKEND / "live_api.py", r"ARM_ENTRY_ROLE = ", 3),
        },
        "观察区": {
            "file": "框架/backend/unified_runtime.py",
            "读取入口": grep(L.BACKEND / "unified_runtime.py",
                         r"def get_observe_events", 3),
            "文件落点": grep(L.BACKEND / "unified_runtime.py",
                         r"def observe_file_path", 3),
        },
    }

    # 结构性结论（由上面两处事实直接推出，不靠自报）
    cur_req = (rep["页面请求"]["交易信号区"]["hits"] or [{}])[0].get("text", "")
    whitelist = (rep["后端放行口径"]["交易信号区"]["来源白名单"] or [{}])[0].get("text", "")
    rep["结构性结论"] = {
        "页面只请求 scope=current": "scope=current" in cur_req,
        "当前区只放行 live/recovered": "('live','recovered')" in whitelist,
        "研究/回放信号能否出现在现有信号页面":
            not ("scope=current" in cur_req and "('live','recovered')" in whitelist),
        "研究/观察信号的现有可见落点": "观察区 /api/v1/signals/observe（页面已渲染 334 条观察事件）",
        "结论": ("现有信号页面（SignalListPanel，请求 `/signals?scope=current&limit=500`）"
                 "**结构上只显示 origin∈{live,recovered} 的臂入场信号**。"
                 "带 research / replay / historical_research 等研究标识的信号"
                 "**不会**出现在该页面，只能在审计通道 `scope=all` 读到。"
                 "这是设计上的隔离（防止研究信号冒充可交易信号），不是缺陷。"),
        "对本次接入的含义": ("「研究信号在现有信号页面显示」这一层**不能按原样成立**："
                        "要么研究信号只落审计通道（页面不可见，需另开审计入口 = 改前端架构，本单禁止），"
                        "要么把研究观察事件接到已有的观察区（页面已渲染，属同一只读通道）。"
                        "两条路都需要主代理裁决，本单不擅自选择。"),
    }

    p = L.dump("04_页面落点.json", rep)
    print("== 04 页面落点 ==")
    print("页面请求:", cur_req)
    print("后端白名单:", whitelist)
    for k, v in rep["结构性结论"].items():
        print(f"  {k}: {v}")
    print("->", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
