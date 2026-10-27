# -*- coding: utf-8 -*-
"""00 冻结验收清单（先冻结、后验收；文件存在即拒绝覆盖）。

纪律：
  · 清单在**看任何验收结果之前**生成，之后不得增删条目；确需变更须写 `amendments` 并注明原因与时点。
  · 页面总数按**实际路由/菜单/功能入口**枚举（代码 + 真实浏览器结构勘察双重来源），
    再与反重力的页面清单交叉核对；对方清单缺失时显式记录，不假装核对过。
  · 每个操作都要写清"证据四件套"：截图 / DOM 断言 / 接口调用 / 账本落库。

输出：
  out/00_验收清单_冻结.json   机器可读（后续脚本按它逐条打勾）
  out/00_验收清单_冻结.md     人读版
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

FREEZE = OUT / "00_验收清单_冻结.json"
TZ = timezone(timedelta(hours=8))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ────────────────────────────────────────────── 被审版本指纹
AUDITED_FILES = [
    "框架/backend/adapter.py",
    "框架/backend/api/routes.py",
    "框架/backend/api/router.py",
    "框架/backend/live_api.py",
    "框架/backend/paper_engine.py",
    "框架/backend/live_store.py",
    "框架/backend/live_account.py",
    "框架/backend/live_worker.py",
    "框架/backend/live_signal_engine.py",
    "框架/backend/backtest_store.py",
    "框架/backend/backtest_api.py",
    "框架/backend/backtest_runner.py",
    "框架/backend/datacenter_service.py",
    "框架/backend/metrics_api.py",
    "框架/backend/smart_money.py",
    "UI界面/web/src/App.tsx",
    "UI界面/web/src/api/useLiveTrading.ts",
    "UI界面/web/src/api/liveTrading.ts",
    "UI界面/web/src/api/useMarketData.ts",
    "UI界面/web/src/api/client.ts",
    "UI界面/web/src/components/NavigationSidebar.tsx",
    "UI界面/web/src/components/SignalListPanel.tsx",
    "UI界面/web/src/components/PositionPanel.tsx",
    "UI界面/web/src/components/TokenTable.tsx",
    "UI界面/web/src/components/DataCenterView.tsx",
    "UI界面/web/src/components/StrategyManagementView.tsx",
    "UI界面/web/src/components/BacktestView.tsx",
    "UI界面/web/src/components/BacktestWorkbench.tsx",
    "UI界面/web/vite.config.ts",
]
fingerprint = {}
for rel in AUDITED_FILES:
    p = BASE / rel
    fingerprint[rel] = ({"sha256": sha(p), "字节": p.stat().st_size,
                         "mtime": datetime.fromtimestamp(p.stat().st_mtime, TZ).isoformat(timespec="seconds")}
                        if p.exists() else {"缺失": True})

# 运行态
def sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<失败 {exc}>"


pids = {}
for f in [".system/frontend.pid", "框架/backend/backend.pid", "框架/backend/worker.pid"]:
    p = BASE / f
    pids[f] = p.read_text(encoding="utf-8").strip() if p.exists() else None

RUNTIME = {
    "前端": {"地址": "http://127.0.0.1:3000/", "pid": pids.get(".system/frontend.pid"),
             "形态": "vite dev server（/api 同源代理 → 8787）"},
    "后端": {"地址": "http://127.0.0.1:8787/", "pid": pids.get("框架/backend/backend.pid"),
             "形态": "ThreadingHTTPServer 自定义路由（非 FastAPI，无 openapi.json）"},
    "worker": {"pid": pids.get("框架/backend/worker.pid")},
    "python": sh(f"{BASE}/evidence/FT-BASELINE-01/.venv/bin/python -V || python3 -V"),
    "node": sh("/Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node -v"),
    "chromium": "playwright chromium_headless_shell-1248（本机已缓存，可真实渲染）",
    "paper_ledger_dir": "数据/paper（PAPER_LEDGER_DIR 未覆盖时为默认值）",
}

# ────────────────────────────────────────────── 接口清单（从路由表机械提取）
routes_src = (BASE / "框架/backend/api/routes.py").read_text(encoding="utf-8")
GET_ROUTES = re.findall(r'Route\(\s*"([^"]+)"', routes_src)
POST_ROUTES = ["/api/v1/paper/orders", "/api/v1/paper/control", "/api/v1/paper/toggle_gate",
               "/api/v1/signals/clear", "/api/v1/backtests"]

# ────────────────────────────────────────────── 页面与视图（实际路由/菜单/入口）
PAGES = [
    {"id": "workbench", "名称": "交易工作台", "来源": "NavigationSidebar id=workbench / App.tsx currentNav",
     "视图": [{"id": "workbench.main", "名称": "主视窗（信号区 + 行情表 + 持仓区）"}]},
    {"id": "datacenter", "名称": "数据中心", "来源": "NavigationSidebar id=datacenter / DataCenterView.tsx",
     "视图": [
         {"id": "dc.dashboard", "名称": "全域数据名称与红绿灯看板主视窗"},
         {"id": "dc.pending", "名称": "待补数据与核销清单"},
         {"id": "dc.matrix", "名称": "全资产协同矩阵"},
         {"id": "dc.kline", "名称": "K线（8 周期）"},
         {"id": "dc.oi", "名称": "OI"},
         {"id": "dc.funding", "名称": "费率"},
         {"id": "dc.smartmoney", "名称": "聪明钱"},
         {"id": "dc.metrics", "名称": "指标"},
     ]},
    {"id": "strategy", "名称": "策略管理", "来源": "NavigationSidebar id=strategy / StrategyManagementView.tsx",
     "视图": [
         {"id": "st.rules", "名称": "规则说明（当前页）"},
         {"id": "st.deps", "名称": "数据依赖"},
         {"id": "st.versions", "名称": "版本对比"},
     ]},
    {"id": "backtest", "名称": "回测复盘", "来源": "NavigationSidebar id=backtest / BacktestView.tsx",
     "视图": [
         {"id": "bt.workbench.batch", "名称": "历史回测 · 按批次"},
         {"id": "bt.workbench.run", "名称": "历史回测 · 按单 run"},
         {"id": "bt.review.batch", "名称": "验收复盘 · 按批次"},
         {"id": "bt.review.run", "名称": "验收复盘 · 按单 run"},
     ]},
]

# ────────────────────────────────────────────── 每个视图必须验证的操作
OPS = [
    # —— 交易工作台 ——
    ("W1", "workbench.main", "顶部状态条：BTC/ETH 价格与涨跌幅、模拟盘权益、行情连接状态可见且非占位",
     ["截图", "DOM 文本", "与 /api/v1/markets、/api/v1/paper/accounts 对账"]),
    ("W2", "workbench.main", "信号区渲染：条数与 /api/v1/signals?scope=current 一致；空态文案正确",
     ["截图", "DOM 文本", "接口条数对账"]),
    ("W3", "workbench.main", "开仓总闸：点「已暂停/运行中」→ POST /api/v1/paper/control，回读状态翻转",
     ["截图前后", "POST 回执", "gate 文件 mtime 与内容", "回读 /api/v1/paper/orders quality"]),
    ("W4", "workbench.main", "行情表：行数 = /api/v1/markets 条数；9 组列头齐全；排序可用",
     ["截图", "DOM 行数与列头", "接口条数对账"]),
    ("W5", "workbench.main", "行内 K 线：点行展开；周期 5m/15m/30m/1h/4h/8h 逐一切换均出图",
     ["逐周期截图", "candles 接口调用与返回根数", "K 线首末时间与接口一致"]),
    ("W6", "workbench.main", "持仓区：账户框数值 = /api/v1/paper/accounts；空态/异常态文案不伪装成 0",
     ["截图", "DOM 数值", "接口对账"]),
    ("W7", "workbench.main", "「按选中信号开多/开空」在未选信号/未接通时必须给出明确拒绝提示，不得静默",
     ["截图", "提示文案", "网络层无写请求 或 POST 被拒回执"]),
    ("W8", "workbench.main", "「清理」信号 → POST /api/v1/signals/clear，回执与库内行数变化一致",
     ["截图", "POST 回执", "live_signals 表行数前后"]),
    ("W9", "workbench.main", "主题切换（浅/深）不改变任何数值口径",
     ["截图前后", "关键数值 DOM 对比"]),
    # —— 数据中心 ——
    ("D1", "dc.dashboard", "17 项数据流红绿灯：条数与状态 = /api/v1/data/feeds",
     ["截图", "DOM 条数/状态", "接口对账"]),
    ("D2", "dc.pending", "待补数据与核销清单：条数 = /api/v1/data/whitelist|backfill",
     ["截图", "DOM 条数", "接口对账"]),
    ("D3", "dc.matrix", "全资产协同矩阵：标的数 = /api/v1/data/coverage，缺口标红口径与接口一致",
     ["截图", "DOM 计数", "接口对账"]),
    ("D4", "dc.kline", "K 线 8 周期视图：8 个周期均能切换且有数据/明确空态",
     ["8 张截图", "接口调用", "每周期首末时间"]),
    ("D5", "dc.oi", "OI 明细：行数 ≤ limit、字段与 /api/v1/data/oi 一致",
     ["截图", "DOM 行", "接口逐行对账"]),
    ("D6", "dc.funding", "资金费明细：符号口径与 /api/v1/data/funding 一致（不得把负号吃掉）",
     ["截图", "DOM 数值", "接口对账"]),
    ("D7", "dc.smartmoney", "聪明钱：来源标注与本机快照一致，缺数据不补 0",
     ["截图", "DOM 文本", "快照文件 mtime/内容"]),
    ("D8", "dc.metrics", "指标矩阵：字段契约可读，缺失一律 null + missing_reason",
     ["截图", "接口 quality.status", "field-contract 对账"]),
    # —— 策略管理 ——
    ("S1", "st.rules", "策略列表条数 = /api/v1/strategies；条件序列与后端阈值字段一致",
     ["截图", "DOM 文本", "接口对账"]),
    ("S2", "st.deps", "数据依赖页：依赖项与 /api/v1/data/status 一致",
     ["截图", "DOM 文本", "接口对账"]),
    ("S3", "st.versions", "版本对比页：版本标识来自真实字段，无杜撰版本号",
     ["截图", "DOM 文本", "接口/文件 SHA 对账"]),
    # —— 回测复盘 ——
    ("B1", "bt.workbench.batch", "批次列表：条数与 /api/v1/backtests/batches 一致",
     ["截图", "DOM 条数", "接口对账"]),
    ("B2", "bt.workbench.batch", "批次汇总：笔数/净收益/回撤与逐笔行现算一致（不得自相矛盾）",
     ["截图", "DOM 数值", "接口字段 vs 逐笔重算"]),
    ("B3", "bt.workbench.run", "单 run 列表 + 搜索：过滤在截断之前生效",
     ["截图", "DOM 条数", "接口 q 参数对账"]),
    ("B4", "bt.workbench.run", "单 run 详情：权益曲线/逐笔/止损轨迹与源账本一致",
     ["截图", "DOM 数值", "trades csv / summary.json 对账"]),
    ("B5", "bt.review.batch", "验收复盘 · 按批次：与历史回测批次口径区分，不混用",
     ["截图", "DOM 文本", "接口 mode 参数"]),
    ("B6", "bt.review.run", "验收复盘 · 按单 run：同上",
     ["截图", "DOM 文本", "接口 mode 参数"]),
    ("B7", "bt.workbench.run", "深链 ?run=<id> 直接打开指定 run（确定性入口）",
     ["截图", "URL", "DOM run 标识"]),
]

# ────────────────────────────────────────────── 接口契约检查
API_CHECKS = [
    ("A1", "GET /api/v1/health", "200 且 items 非空；失败项 status != ok"),
    ("A2", "GET /api/v1/overview", "counts.markets_total 与 /api/v1/markets 条数一致"),
    ("A3", "GET /api/v1/markets", "条数与 pool 口径一致；关键字段非空率"),
    ("A4", "GET /api/v1/markets/{ex}/{sym}/candles", "参数 tf/limit 生效；返回根数 ≤ limit；时间递增无重复"),
    ("A5", "GET /api/v1/markets/{ex}/{sym}/oi", "缺失时 null + missing_reason，不补 0"),
    ("A6", "GET /api/v1/markets/{ex}/{sym}/funding", "符号保留；与库内逐行一致"),
    ("A7", "GET /api/v1/signals?scope=current", "条数与库内符合口径的行数一致"),
    ("A8", "GET /api/v1/paper/accounts", "账户数与账本臂数一致；initialEquity/cash 可加总闭合"),
    ("A9", "GET /api/v1/paper/positions", "持仓行与账本 open 仓位逐笔一致"),
    ("A10", "GET /api/v1/paper/orders", "not_connected 必须与写入口实际行为一致（不得只读说未接通、写口仍可写）"),
    ("A11", "POST /api/v1/paper/orders", "隔离实例下：未知臂 400 / 缺参 400 / 账本缺失 503 / 正常 200 或 409"),
    ("A12", "GET /api/v1/live/health", "worker 未运行必须 down，不得因接口 200 报 ok"),
    ("A13", "GET /api/v1/accounts", "空态来自无快照，missing_reason 明确"),
    ("A14", "GET /api/v1/strategies", "条数与注册表一致"),
    ("A15", "GET /api/v1/data/feeds", "17 项；停更项状态不得为正常"),
    ("A16", "GET /api/v1/data/oi|funding|smart-money|all-in-one", "items+total 信封；limit/q 生效"),
    ("A17", "GET /api/v1/data/coverage", "覆盖矩阵规模与库内 (标的,周期) 组合数一致"),
    ("A18", "GET /api/v1/backtests/*", "列表/详情/子资源；q 与 status 过滤在截断之前"),
    ("A19", "POST /api/v1/signals/clear", "回执 cleared_count = 实际删除行数"),
    ("A20", "POST /api/v1/paper/control", "闸门翻转持久化到文件，重启后仍生效"),
    ("A21", "未知路径/未知方法", "404 且不改任何状态"),
]

# ────────────────────────────────────────────── 账本/数据源对账
LEDGER_CHECKS = [
    ("L1", "纸面账户闭合：cash + 持仓权益 = initialEquity + 已实现 + 未实现；逐臂可加总"),
    ("L2", "手续费/资金费各计一次：profit_abs = (平-开)×数量 − 手续费 + funding_fees"),
    ("L3", "标识贯通：signal_uid → order → fill → position → ledger 行可互查"),
    ("L4", "初始止损与跟踪上移：止损价可从 15m/4h K 线因果重建，生效时间不早于参考 K 线收盘"),
    ("L5", "撮合因果：成交价不优于「请求时刻之后第一根已闭合 15m 的开盘价」"),
    ("L6", "期末持仓：未平仓状态与账本 is_open/exit_reason 一致，不得靠强制结算伪装已实现"),
    ("L7", "回测账本：run 目录、summary.json、trades csv、events.jsonl 标识与金额可对账"),
    ("L8", "研究数据未被污染：审核前后 数据/ 与研究目录关键库 SHA/行数不变"),
]

# ────────────────────────────────────────────── 生命周期场景（隔离实例）
LIFECYCLE = [
    ("C1", "正常开仓 → 持仓出现 → 平仓 → 已实现盈亏入账，余额闭合"),
    ("C2", "拒单：未知臂 / 缺 symbol / 缺 atr / 重复 signal_uid / 槽位已满 / 最小下单额"),
    ("C3", "重复请求：同一 signal_uid 连发 2 次，只允许一笔成交（幂等）"),
    ("C4", "重启恢复：写入后重启后端，账户/持仓/闸门/订单仍在"),
    ("C5", "数据失效：K 线缺失或时间倒退时拒单或挂起，不得用未来价成交"),
    ("C6", "真实交易未启用：全链路无任何下单到交易所的路径（live_order_enabled 恒 False）"),
]

# ────────────────────────────────────────────── 反重力交付清单交叉
DELIVERY = {
    "目录": "evidence/SYSTEM-CLOSELOOP-01/",
    "审核开始时内容": sorted(p.name for p in (BASE / "evidence/SYSTEM-CLOSELOOP-01").glob("*")) if (BASE / "evidence/SYSTEM-CLOSELOOP-01").exists() else [],
    "页面清单是否可核对": False,
    "说明": "审核开始时该目录为空，未提供页面清单/逐页证据 ⇒ 页面总数只能按实际路由与菜单枚举，"
            "反重力清单待其交付后再交叉核对（本条在报告中单列为「未核对」）。",
    "已发现的对方产物": ["UI界面/web/scripts/test_all_ui_pages.mjs（巡检脚本，非验收证据）"],
}

FROZEN = {
    "工单": "SYSTEM-CLOSELOOP-AUDIT-01",
    "冻结时刻": datetime.now(TZ).isoformat(timespec="seconds"),
    "冻结说明": "先冻结清单，再看结果。之后不得增删条目；变更走 amendments 并注明原因。",
    "amendments": [],
    "被审版本指纹": fingerprint,
    "运行态": RUNTIME,
    "页面总数": {"顶级页面": len(PAGES), "可切换视图": sum(len(p["视图"]) for p in PAGES),
                 "视图清单": [v["id"] for p in PAGES for v in p["视图"]]},
    "页面": PAGES,
    "操作项": [{"id": a, "视图": b, "要求": c, "证据": d} for a, b, c, d in OPS],
    "接口清单": {"GET": GET_ROUTES, "POST": POST_ROUTES, "数量": len(GET_ROUTES) + len(POST_ROUTES)},
    "接口契约检查": [{"id": a, "接口": b, "要求": c} for a, b, c in API_CHECKS],
    "账本检查": [{"id": a, "要求": b} for a, b in LEDGER_CHECKS],
    "生命周期场景": [{"id": a, "要求": b} for a, b in LIFECYCLE],
    "反重力交付": DELIVERY,
    "判定规则": {
        "单条": "通过 / 不通过 / 未验证 三态；未验证必须写原因，不得计入通过",
        "三层一致": "UI 显示 = 接口返回 = 账本落库，三者任一不一致即该操作判不通过",
        "不可替代": "绿色测试、接口 200、截图、登记回执均不能单独构成通过",
        "总判": "四问分开回答：UI 完整性 / 模拟交易闭环 / 回测可追溯可对账 / 前向验证工程条件",
    },
}

if FREEZE.exists():
    raise SystemExit(f"拒绝覆盖：{FREEZE} 已存在（冻结时刻 "
                     f"{json.loads(FREEZE.read_text(encoding='utf-8'))['冻结时刻']}）")

FREEZE.write_text(json.dumps(FROZEN, ensure_ascii=False, indent=2), encoding="utf-8")

# 人读版
L = [f"# 验收清单（冻结于 {FROZEN['冻结时刻']}）", "",
     f"- 顶级页面 **{FROZEN['页面总数']['顶级页面']}** 个，可切换视图 **{FROZEN['页面总数']['可切换视图']}** 个",
     f"- 操作项 **{len(OPS)}** 条，接口契约 **{len(API_CHECKS)}** 条，账本检查 **{len(LEDGER_CHECKS)}** 条，"
     f"生命周期场景 **{len(LIFECYCLE)}** 条", "",
     "## 页面与视图", "", "| 页面 | 视图 | 来源 |", "|---|---|---|"]
for p in PAGES:
    for v in p["视图"]:
        L.append(f"| {p['名称']} | {v['名称']} | `{v['id']}` |")
L += ["", "## 操作项", "", "| # | 视图 | 必须验证 | 证据 |", "|---|---|---|---|"]
for a, b, c, d in OPS:
    L.append(f"| {a} | `{b}` | {c} | {'、'.join(d)} |")
L += ["", "## 接口契约", "", "| # | 接口 | 要求 |", "|---|---|---|"]
for a, b, c in API_CHECKS:
    L.append(f"| {a} | `{b}` | {c} |")
L += ["", "## 账本检查", "", "| # | 要求 |", "|---|---|"]
for a, b in LEDGER_CHECKS:
    L.append(f"| {a} | {b} |")
L += ["", "## 生命周期场景（隔离实例）", "", "| # | 要求 |", "|---|---|"]
for a, b in LIFECYCLE:
    L.append(f"| {a} | {b} |")
L += ["", "## 反重力交付清单", "", f"- 目录：`{DELIVERY['目录']}`",
      f"- 审核开始时内容：{DELIVERY['审核开始时内容'] or '（空）'}",
      f"- 页面清单是否可核对：**{'是' if DELIVERY['页面清单是否可核对'] else '否'}**",
      f"- {DELIVERY['说明']}", "",
      "## 判定规则", ""]
for k, v in FROZEN["判定规则"].items():
    L.append(f"- **{k}**：{v}")
(OUT / "00_验收清单_冻结.md").write_text("\n".join(L) + "\n", encoding="utf-8")

print("冻结完成：", FROZEN["冻结时刻"])
print("页面：", FROZEN["页面总数"]["顶级页面"], "个顶级 /", FROZEN["页面总数"]["可切换视图"], "个视图")
print("操作项", len(OPS), "接口契约", len(API_CHECKS), "账本", len(LEDGER_CHECKS), "生命周期", len(LIFECYCLE))
print("路由：GET", len(GET_ROUTES), "POST", len(POST_ROUTES))
print("反重力交付目录内容：", DELIVERY["审核开始时内容"] or "（空）")
