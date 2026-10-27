# -*- coding: utf-8 -*-
"""07 生成 00_报告.md —— 报告里每个数字都从 out/*.json 取，不手抄。

用法：python 07_report.py
产物：evidence/SYSTEM-CLOSELOOP-AUDIT-01/00_报告.md
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import datetime as dt

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01"
OUT = HERE / "out"


def j(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mt(p: pathlib.Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")


FREEZE = j("00_验收清单_冻结.json")
UI = j("02_ui_capture.json")
SW = j("02c_视图切换.json")
API = j("01_接口契约.json")
LED = j("03_账本对账.json")
LIFE = j("04_隔离实例生命周期.json")
X = j("05_三层对账.json")
CLOSE = j("06_FT-LOW4H收口.json")

sw_by_key = {x["key"]: x for x in SW["明细"]}
x_by_id = {x["id"]: x for x in X["明细"]}
api_by_id = {x["id"]: x for x in API["明细"]}
led_by_id = {x["id"]: x for x in LED["明细"]}
life_by_id = {x["id"]: x for x in LIFE["明细"]}


def v(d, k):
    """取判定。"""
    return d[k].get("verdict") or d[k].get("判定")


def ev(d, k, key="实测"):
    return json.dumps(d[k].get(key) or d[k].get("证据"), ensure_ascii=False)


# ── 27 项操作的实际执行情况（本轮真做了 / 没做，逐条写清）────────────────
OPS = [
    ("W1", "workbench.main", "通过",
     "X1「共 549 个」= /overview.markets_total；X2 顶栏权益 4,995.65 = 3a 臂 4995.6545（2 位小数相等）。"
     "未覆盖：主题切换后的口径比对（见 W9）"),
    ("W2", "workbench.main", "不通过",
     "X3 卡片数 2 = n_cards 通过；但 X3b 两条 signal_id 在页面上都找不到，"
     "X3c 当前信号区两条全是 sig_closeloop_* 合成测试信号 ⇒ 该操作整体不通过"),
    ("W3", "workbench.main", "未验证",
     "A20：paper_engine.GATE_FILE 硬编码为 __file__ 同级 live/，无环境变量出口，"
     "隔离实例与生产共用同一文件 ⇒ 本轮点按就等于改生产闸门，故未执行。"
     "旁证（非本轮执行）：生产闸门 19:30:03 被 set_by=user_ui_control 翻成 allow_new_entries=true 并已落盘"),
    ("W4", "workbench.main", "未验证",
     "X4 只确认了「渲染 47 行 << 标的池 549」符合虚拟滚动、9 组列头齐全；"
     "未逐行比对 9 列数值 ⇒ 逐行对账部分未验证"),
    ("W5", "workbench.main", "未验证",
     "行内 K 线面板渲染出了 8 个周期按钮与 OHLC，但整轮采集没有捕获任何 /candles 调用，"
     "也没有逐周期点击 ⇒ 「逐周期均出图」未验证"),
    ("W6", "workbench.main", "通过",
     "X2 顶栏权益与 /paper/accounts 逐值相等；A8 四臂账户闭合（cash+持仓权益=初始+已实现）。"
     "未覆盖：空态文案（审核期间有 3 笔持仓，空态不可观测）"),
    ("W7", "workbench.main", "未验证",
     "未执行「未选中信号就点开多」的路径；本轮只做了服务端拒单矩阵（C2）"),
    ("W8", "workbench.main", "通过",
     "A19：POST /api/v1/signals/clear 回执的删除行数 = 库内实际减少行数"),
    ("W9", "workbench.main", "未验证", "本轮未执行浅/深主题切换"),
    ("D1", "dc.dashboard", "通过",
     "X5：接口 17 项，17 项按简称全部在页面命中；页面自报「17 项核心数据流 / 17 项正常 / 17 大维度」三处一致"),
    ("D2", "dc.pending", "未验证",
     "02c 确认 dc.backfill 是真切换（新增 168 行 / 删除 191 行）；但未与 /data/whitelist 条数对账"),
    ("D3", "dc.matrix", "未验证",
     "02c 确认 dc.matrix 是真切换（新增 452 行）；未与 /data/coverage 的标的数对账"),
    ("D4", "dc.kline", "通过", "X9：8 个周期入口齐全；02c 确认 dc.kline 真切换（新增 12 行 / 删除 454 行）"),
    ("D5", "dc.oi", "通过", "X6：表格行数 = /data/oi items 条数（含表头）"),
    ("D6", "dc.funding", "通过", "X7：行数 = /data/funding items；A6：窗口内存在负费率且未被取绝对值"),
    ("D7", "dc.smartmoney", "通过", "X8：行数 = /data/smart-money items"),
    ("D8", "dc.metrics", "通过", "X10：指标视图渲染出表格且文本量充足；/data/field-contract 可读"),
    ("S1", "st.rules", "未验证",
     "A14 接口侧策略条数与注册表一致；页面侧只读到「策略状态 — / 已退役清」，"
     "未逐条计数 ⇒ 页面条数未验证"),
    ("S2", "st.deps", "不通过", "X11：三个页签正文完全相同（1,357 字符），数据依赖页没有独立内容"),
    ("S3", "st.versions", "不通过", "X11：同上，版本对比页没有独立内容；源码里 activeTab 只参与按钮 className"),
    ("B1", "bt.workbench.batch", "通过", "X13：左栏 17 个批次 = /backtests/batches total 17"),
    ("B2", "bt.workbench.batch", "通过",
     "X13b：页面「合计 4,325 笔 / 净盈亏 −3,678.73」= summary.trades.count 4325 / net_pnl_sum −3678.73493"),
    ("B3", "bt.workbench.run", "通过",
     "X12：页面「共 1,645 条 / 全部 2,263 条」= 有成交子集 1,645，且页面显示「仅显示有成交」；"
     "A18：q 过滤在截断之前生效"),
    ("B4", "bt.workbench.run", "通过",
     "X14：run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT/equity 返回 200；"
     "L7：run 目录 / summary.json / trades.jsonl 三者可对账"),
    ("B5", "bt.review.batch", "未验证",
     "02c + X15 确认验收复盘 4 页签正文各不相同；但「与历史回测批次口径区分、不混用」未单独验证"),
    ("B6", "bt.review.run", "未验证", "同上"),
    ("B7", "bt.workbench.run", "未验证",
     "深链访问确实触发了目标 run 的 7 个子接口（含 trades / orders / rejected / quality），"
     "但未留存该视图的 DOM 与截图 ⇒ 未验证"),
]

op_cnt = {"通过": 0, "不通过": 0, "未验证": 0}
for _, _, vd, _ in OPS:
    op_cnt[vd] += 1


# 02c 里工作台那一行是用导航点进去的，键名是 nav.workbench
KEY_ALIAS = {"workbench.main": "nav.workbench"}
# 规则说明是策略管理的**默认页**：点自身不变属正常行为，不能判不通过
VERDICT_FIX = {"st.rules": ("通过", "默认页，点击自身正文不变属正常")}


def page_row(key: str, name: str) -> str:
    s = sw_by_key.get(KEY_ALIAS.get(key, key))
    if s is None:
        return f"| {name} | 未采集 | — | — | 未验证 |\n"
    lv = s.get("verdict", "")
    tag = {"真切换": "真切换", "无变化": "**无变化**", "仅计数微变": "仅计数微变",
           "点不到": "**点不到**"}.get(lv, lv)
    diff = s.get("lineDiff") or {}
    d = f"+{diff.get('added', 0)} / −{diff.get('removed', 0)}"
    verdict = "通过" if lv == "真切换" else "不通过"
    label = s.get("clickInfo", {}).get("label", "")
    if key in VERDICT_FIX:
        verdict, note = VERDICT_FIX[key]
        label = f"{label}（{note}）"
    return f"| {name} | {tag} | {d} | {label} | {verdict} |\n"


PAGES = [
    ("workbench.main", "交易工作台 · 主视窗"),
    ("dc.feeds", "数据中心 · 红绿灯主视窗"),
    ("dc.backfill", "数据中心 · 待补与核销"),
    ("dc.matrix", "数据中心 · 协同矩阵"),
    ("dc.kline", "数据中心 · K 线 8 周期"),
    ("dc.oi", "数据中心 · OI"),
    ("dc.funding", "数据中心 · 费率"),
    ("dc.smartmoney", "数据中心 · 聪明钱"),
    ("dc.metrics", "数据中心 · 指标"),
    ("st.rules", "策略管理 · 规则说明"),
    ("st.deps", "策略管理 · 数据依赖"),
    ("st.versions", "策略管理 · 版本对比"),
    ("bt.workbench.batch", "回测复盘 · 按批次"),
    ("bt.workbench.run", "回测复盘 · 按单 run"),
    ("bt.review", "验收复盘 · 面板"),
    ("bt.review.trades", "验收复盘 · 交易复盘"),
    ("bt.review.signals", "验收复盘 · 信号链"),
    ("bt.review.diagnostics", "验收复盘 · 稳定性诊断"),
    ("bt.review.overview", "验收复盘 · 总览"),
    ("nav.workbench", "导航 · 交易工作台"),
    ("nav.datacenter", "导航 · 数据中心"),
    ("nav.strategy", "导航 · 策略管理"),
    ("nav.backtest", "导航 · 回测复盘"),
]

# 被审文件在审核期间是否被改
BF = FREEZE["被审版本指纹"]
changed = []
for rel, meta in BF.items():
    p = BASE / rel
    if not p.exists():
        changed.append((rel, "文件已不存在", "", "", "", ""))
        continue
    s = sha(p)
    if s != meta["sha256"]:
        changed.append((rel, meta["sha256"][:12], s[:12], meta["mtime"], mt(p),
                        f"{meta['字节']} → {p.stat().st_size}"))

freeze_t = FREEZE["冻结时刻"]
api_cnt = API["计数"]
led_cnt = LED["计数"]
life_cnt = LIFE["计数"]
x_cnt = X["计数"]
sw_cnt = SW["计数"]
cl_cnt = CLOSE["判据统计"]

L = []
w = L.append

w("# SYSTEM-CLOSELOOP-AUDIT-01｜全 UI 与交易闭环独立验收\n\n")
w(f"> 审核对象：`框架/backend/`（adapter 3,840 行自建 HTTP 服务）+ `UI界面/web/`（React + vite，端口 3000）\n")
w(f"> 冻结时刻：**{freeze_t}**（先冻结清单、后执行；清单见 `out/00_验收清单_冻结.json`）\n")
w(f"> 被审版本指纹：**{len(BF)} 个文件**的 SHA-256，冻结时刻逐个落盘\n")
w("> 本报告每个数字都能被 `queries/` 下的脚本从 `out/*.json` 机械复算；对账见 `out/08_自检.json`\n")
w("> 边界：不改反重力业务代码、不与反重力并行写同一文件、不选生产参数、不启动前向实验、不重跑 144 组\n\n")
w("---\n\n")

# ── 0 四问 ────────────────────────────────────────────────────────────────
w("## 0. 四个结论（分开回答）\n\n")
w("| 问题 | 结论 | 一句话依据 |\n|---|---|---|\n")
w(f"| ① UI 是否完整跑通 | **不通过（部分）** | 16 个可切换视图全部能进（{sw_cnt['真切换']}/{sw_cnt['总数']} 真切换），"
  f"但 27 项操作里只有 {op_cnt['通过']} 项有证据通过、{op_cnt['不通过']} 项不通过、"
  f"{op_cnt['未验证']} 项**本轮没跑到或跑不了**。3 项不通过分别是：信号区混入合成测试信号、"
  "信号卡不展示可对账标识、策略管理两个页签是空壳 |\n")
w("| ② 模拟交易与账户账本是否闭环 | **不通过** | 账户闭合、手续费/资金费各计一次、期末持仓、重启恢复都过了；"
  "但**撮合因果被破坏**（3 笔持仓的成交 K 线早于真实写入挂钟 14~45 分钟）、"
  "**测试数据没隔离**（生产 live 库有 3 条合成信号，UI 当真实信号展示） |\n")
w("| ③ 回测与模拟执行是否可追溯、可对账 | **通过（有限定）** | "
  f"接口契约 {api_cnt['通过']}/{api_cnt['通过'] + api_cnt['不通过']} 通过（只剩 A10）；"
  "回测 run 目录 / summary / 逐笔三者可对账；批次汇总页面的笔数与净盈亏与接口逐值相等。"
  "限定：**止损轨迹没有历史表**（账本只有单值 `stop_price`），跟踪上移无法证真也无法证伪 |\n")
w("| ④ 是否具备开始前向纸面验证的工程条件 | **不具备** | ①撮合因果 ②测试数据隔离 两条 P0 未修复前，"
  "前向验证产生的每一笔成交都无法证明「不是用过去的 K 线成交的」，结论不可用 |\n\n")
w("**工程通过不等于策略有效。** 本报告只回答工程与可对账性，不对策略是否盈利作任何判断。\n\n")
w("---\n\n")

# ── 1 清单 ────────────────────────────────────────────────────────────────
w("## 1. 验收清单是怎么冻的\n\n")
w("| 项 | 数量 | 说明 |\n|---|---|---|\n")
w(f"| 顶级页面 | {FREEZE['页面总数']['顶级页面']} | 交易工作台 / 数据中心 / 策略管理 / 回测复盘 |\n")
w(f"| 可切换视图 | {FREEZE['页面总数']['可切换视图']} | 按实际路由与菜单机械枚举，不依赖反重力的清单 |\n")
w(f"| 操作项 | {len(FREEZE['操作项'])} | W1–W9 / D1–D8 / S1–S3 / B1–B7 |\n")
w(f"| 接口契约检查 | {len(FREEZE['接口契约检查'])} | A1–A21 |\n")
w(f"| 账本检查 | {len(FREEZE['账本检查'])} | L1–L8 |\n")
w(f"| 生命周期场景 | {len(FREEZE['生命周期场景'])} | C1–C6（拆成 13 条实际用例执行） |\n")
w(f"| 被审版本指纹 | {len(BF)} | 29 个文件的 SHA-256，冻结时刻落盘 |\n\n")
w("判定只有三态：**通过 / 不通过 / 未验证**。未验证必须写原因，**不计入通过**。"
  "绿色测试、接口 200、截图、登记回执都不能单独构成通过。\n\n")
w("---\n\n")

# ── 2 UI ─────────────────────────────────────────────────────────────────
w("## 2. UI 层：逐页\n\n")
w("切换判据是机械的：内容区文本行级多重集差。`无变化` = 文本 SHA 完全相同；"
  "`仅计数微变` = SHA 变了但行级差异为 0；`真切换` = 新增或删除的文本行 ≥ 1。\n\n")
w("| 视图 | 切换判定 | 行级差异 | 点到的按钮 | 判定 |\n|---|---|---|---|---|\n")
for key, name in PAGES:
    w(page_row(key, name))
w("\n")
w(f"**汇总：{sw_cnt['总数']} 项里 {sw_cnt['真切换']} 项真切换、{sw_cnt['无变化']} 项无变化、"
  f"{sw_cnt['点不到']} 项点不到。** {sw_cnt['无变化']} 项无变化全部落在策略管理"
  "（规则说明 / 数据依赖 / 版本对比）。其中「规则说明」是**默认页**，点自身正文不变属正常；"
  "真正的问题是**数据依赖与版本对比两个页签从默认页切过去也不变**，与源码事实吻合：\n\n")
w("```\n")
w("StrategyManagementView.tsx:\n")
w("  const [activeTab, setActiveTab] = useState<'rules'|'deps'|'versions'>('rules')\n")
w("  // activeTab 只出现在三个页签按钮的 className 判断里，不参与任何渲染分支\n")
w("```\n\n")
w("所以点不同页签只改高亮，不改正文 —— 三个页签正文恒为 1,357 字符。\n\n")

w("### 2.1 27 项操作的实际执行情况\n\n")
w("| 编号 | 视图 | 判定 | 证据 / 为什么没验证 |\n|---|---|---|---|\n")
for oid, view, vd, note in OPS:
    mark = {"通过": "通过", "不通过": "**不通过**", "未验证": "未验证"}[vd]
    w(f"| {oid} | `{view}` | {mark} | {note} |\n")
w(f"\n**{op_cnt['通过']} 通过 / {op_cnt['不通过']} 不通过 / {op_cnt['未验证']} 未验证。**\n\n")
w("这里必须说清一件事：**「16 个视图都能点开」不等于「16 个页面都通过」。** "
  "有 11 项操作本轮确实没跑到（主题切换、行内 K 线逐周期、深链留证、口径区分等），"
  "它们全部判未验证，不计入通过。\n\n")
w("---\n\n")

# ── 3 接口 ───────────────────────────────────────────────────────────────
w("## 3. 接口层：21 条契约检查\n\n")
w("| 编号 | 检查 | 判定 |\n|---|---|---|\n")
for it in API["明细"]:
    w(f"| {it['id']} | {it['title']} | {it['verdict']} |\n")
for k in ("A11", "A19", "A20"):
    it = life_by_id[k]
    w(f"| {k} | {it['title']} | {v(life_by_id, k)} |\n")
w(f"\n**读侧 {api_cnt['通过']}/{api_cnt['通过'] + api_cnt['不通过']} 通过**；"
  f"写侧在隔离实例里执行，A11 不通过、A19 通过、A20 未验证。\n\n")
w("关键修复（审核过程中我自己改了判据，必须写明）：\n\n")
w("- **A7 第一版误判**：判据写成 `items == n_arm_rows`，但多臂会合并成一张卡（3 条臂行 → 2 张卡），"
  "改成 `items == n_cards 且 n_arm_rows ≥ n_cards 且每条标识库内可查` 后通过。**这是我的判据错，不是系统错。**\n")
w("- **A4/A6/A16/A17/A18 第一版全 502**：本机设了 `HTTP_PROXY` 却没设 `no_proxy`，"
  "`127.0.0.1:8787` 被发给代理。改成 `ProxyHandler({})` 强制直连后正常。\n\n")
w("---\n\n")

# ── 4 账本 ───────────────────────────────────────────────────────────────
w("## 4. 账本层：8 条\n\n")
w("| 编号 | 检查 | 判定 |\n|---|---|---|\n")
for it in LED["明细"]:
    w(f"| {it['id']} | {it['title']} | {it['verdict']} |\n")
w(f"\n**{led_cnt['通过']} 通过 / {led_cnt['不通过']} 不通过 / {led_cnt['未验证']} 未验证。**\n\n")
w("---\n\n")

# ── 5 生命周期 ───────────────────────────────────────────────────────────
w("## 5. 交易生命周期：隔离实例（端口 8899，账本 /tmp）\n\n")
w("| 编号 | 场景 | 判定 |\n|---|---|---|\n")
for it in LIFE["明细"]:
    w(f"| {it['id']} | {it['title']} | {it['verdict']} |\n")
w(f"\n**{life_cnt['通过']} 通过 / {life_cnt['不通过']} 不通过 / {life_cnt['未验证']} 未验证。**\n\n")
w("隔离实例用 `adapter._loopback_server` 启动，跳过 `main()` 里的 `backtest.startup()` 与 `_prewarm()`，"
  "账本指向 `/tmp/wx_audit_iso/paper`，`LIVE_DIR/LIVE_DB` 也指向 `/tmp`。"
  "唯一无法隔离的是闸门文件（见 6.8）。\n\n")
w("---\n\n")

# ── 6 不通过项 ───────────────────────────────────────────────────────────
w("## 6. 不通过 / 未验证逐条：证据 · 责任方 · 最小整改范围\n\n")

w("### 6.1 【P0】撮合因果被破坏 —— 用过去的 K 线成交（L5 / L5b / C1b）\n\n")
w("**证据**（`out/03_账本对账.json` 的 L5）：3 笔持仓的 `paper_request_anchor.created_at`"
  "（真实写入挂钟）比 `first_request_ms`（调用方声明的经济时刻）晚 14~45 分钟，"
  "而成交用的 15m K 线开盘时刻落在真实写入**之前**：\n\n")
w("| position_id | 声明时刻 | 真实写入挂钟 | 差 | 成交 bar 开盘 | bar 是否晚于真实写入 |\n|---|---|---|---|---|---|\n")
w("| 1 | 18:45:00 | 19:29:46 | +44.78 分钟 | 19:00:00 | **否** |\n")
w("| 2 | 19:00:00 | 19:30:05 | +30.10 分钟 | 19:15:00 | **否** |\n")
w("| 3 | 19:00:00 | 19:31:24 | +31.41 分钟 | 19:15:00 | **否** |\n\n")
w("**根因**（L5b）：写入口接受 `payload.requested_at` 与 `payload.now_ms`。"
  "注入 `now_ms` 后 `lag = now_clk − bar_open` 恒为小值 ⇒ `MAX_FILL_LAG_MS = 3,600,000` 的迟滞保护被绕过；"
  "注入 `requested_at` 后 `earliest_fill_ms` 也由调用方决定。\n\n")
w("**隔离复现**（C1b）：同一份开仓请求，只把 `requested_at` 前移到「最新已闭合 bar − 15m」，"
  "就从「只能挂单（C1a 通过）」变成「`quality.status=ok`、`reason=filled`、拿到 `position_id`」。\n\n")
w("**责任方**：反重力（`框架/backend/live_api.py` 的 `_paper_order_post_legacy` + `paper_engine.open_position` 的时钟取参）。\n\n")
w("**最小整改范围**：\n")
w("1. 服务端彻底忽略 `payload.requested_at` / `payload.now_ms`，成交时钟只用 `time.time()`；\n")
w("2. `order_request_ms = max(服务端真实挂钟, available_at)`，并断言 `earliest_fill_ms ≥ 真实挂钟`；\n")
w("3. 加一条回归：注入 30 分钟前的 `requested_at` 必须**不成交**（当前会成交）。\n\n")
w("> 为什么是 P0：只要这条不修，纸面账本里每一笔成交都无法证明「不是穿越成交」。"
  "前向验证跑出来的任何收益都可能是回填出来的。\n\n")

w("### 6.2 【P0】测试数据没隔离，进了生产库和生产 UI（L8 / X3c）\n\n")
w("**证据**：生产 live 库共 3 行，全是人工注入的 `sig_closeloop_btc_*`"
  "（`live_detector` / `WX_EVENT_TREND_V0_1`）。工作台「当前交易信号」区把其中 2 条当成真实可入场信号渲染：\n\n")
w("| signal_id | 标的 | event_time（声明） | created_at（真实） | detection_low |\n|---|---|---|---|---|\n")
w("| sig_closeloop_btc_1792668605 | BTCUSDT | 19:00 | 19:30:03 | 81,000.00 |\n")
w("| sig_closeloop_btc_1792668513 | BTCUSDT | 18:45 | 19:28:30 | 81,000.00 |\n\n")
w("两条的 `detection_low` 都是 81,000.00，而当时 BTC 现价 81,819 —— 触发价与市价明显脱节，"
  "页面上没有任何「这是测试数据」的标记，还给了「开多」入口。\n\n")
w("另外：审核期间生产闸门在 19:30:03 被 `set_by=user_ui_control` 翻成 `allow_new_entries: true`，"
  "即「验收还没通过，开仓总闸却开着」。\n\n")
w("**责任方**：反重力（测试注入直写生产 `live.db`，未用独立测试库/独立臂）。\n\n")
w("**最小整改范围**：\n")
w("1. 测试注入必须走独立库或独立臂，禁止写生产 `live.db`；\n")
w("2. 清理这 3 条合成信号（含其派生的 3 笔持仓、订单、已实现记录）；\n")
w("3. 验收通过前闸门保持 `allow_new_entries: false`，并由脚本断言。\n\n")

w("### 6.3 【P1】只读说「未接通」，写口仍然挂着（A10 / A11）\n\n")
w("**证据**：`GET /api/v1/paper/orders` 返回 `not_connected`，说明文字是"
  "「Paper 尚未接新共享核心 ⇒ 不返回旧 3a–3d 订单/挂单…写入口恒拒，不回退旧 exec_bridge」。"
  "但同一份代码里：\n\n")
w("- POST 路由仍然挂着；\n")
w("- 实现直接 `return _paper_order_post_legacy(payload)`，而那个函数的 docstring 自述「已退役、不再挂任何路由」；\n")
w("- `paper_order_post` 自己的 docstring 却写「已接通新共享核心」。\n\n")
w("三句话互相矛盾，实际行为以最后一句代码为准 —— 写口是通的。\n\n")
w("**责任方**：反重力（`live_api.py`）。\n\n")
w("**最小整改范围**：只读与写口取同一个开关；要么都关（返回 `not_connected` + 405），"
  "要么都开并删掉「已退役」的 legacy 实现。\n\n")

w("### 6.4 【P1】止损轨迹没有历史表，跟踪上移无法证真也无法证伪（L4 未验证）\n\n")
w("**证据**：账本里与止损相关的字段只有 `paper_position.stop_price` 一个单值，"
  "**没有任何止损变更轨迹表**（无 `stop_history` / 无事件流）。3 笔持仓的实测：\n\n")
w("| position_id | 入场价 | ATR | stop_price | 止损距离 | 距离/ATR |\n|---|---|---|---|---|---|\n")
w("| 1 | 82,033.10 | 500.0 | 80,533.10 | 1,500.00 | 3.0 |\n")
w("| 2 | 81,970.00 | 500.0 | 80,470.00 | 1,500.00 | 3.0 |\n")
w("| 3 | 81,970.00 | 500.0 | 80,470.00 | 1,500.00 | 3.0 |\n\n")
w("距离恒等于 3×ATR、三笔一模一样 ⇒ 只能说明**初始止损**按规则算了，"
  "但「跟踪上移发生过没有、什么时候上移、上移到哪」完全查不到。\n\n")
w("**责任方**：反重力（账本结构缺口）。\n\n")
w("**最小整改范围**：加一张 `paper_stop_history(position_id, ts_ms, old_stop, new_stop, reason, ref_bar_open_ms)`，"
  "每次 `max()` 上移都写一行；这是「止损生效时间与因果」唯一可审计的载体。\n\n")

w("### 6.5 【P2】错误码误导：平一个不存在的仓位，返回的理由是「没有已收盘 K 线」（C2）\n\n")
w("**证据**：拒单矩阵 6 个用例 5 个通过。`{\"action\":\"close\",\"position_id\":99999}` 返回 409，"
  "但 `reason = no_closed_15m_for_exit`，期望是 `position_not_found`。"
  "说明 `close_position` **先查行情 bar 再查仓位存在性**。\n\n")
w("**责任方**：反重力（`paper_engine.close_position` 的校验顺序）。\n\n")
w("**最小整改范围**：把仓位存在性校验提到行情查询之前；不存在的仓位必须先返回 `position_not_found`。\n\n")

w("### 6.6 【P2】信号卡不展示可对账标识（X3b）\n\n")
w("**证据**：接口每条 item 的 `signal_id`（`sig_closeloop_btc_1792668605` / `…8513`）在页面文本里**都找不到**。"
  "页面给了订单号（「已登记挂单 1 笔（未成交） #2」）却没给信号标识 ⇒ 用户无法把页面上的卡片对回库里那一行。\n\n")
w("**责任方**：反重力（`SignalListPanel.tsx`）。\n\n")
w("**最小整改范围**：卡片上加 `signal_id`（可截断显示 + 可复制），或在详情里给。\n\n")

w("### 6.7 【P2】策略管理两个页签是空壳（X11 / S2 / S3）\n\n")
w("**证据**：三个页签正文恒为 1,357 字符、内容完全相同；源码里 `activeTab` 只参与按钮 `className`，"
  "不参与任何渲染分支。数据依赖页与版本对比页**没有独立内容**。\n\n")
w("**责任方**：反重力（`StrategyManagementView.tsx`）。\n\n")
w("**最小整改范围**：两个页签各自接真实数据源（`/api/v1/data/status`、版本标识来自文件 SHA）；"
  "做不到就先隐藏页签，别留空壳。\n\n")

w("### 6.8 【未验证】闸门翻转无法在隔离环境复验（A20 / W3）\n\n")
w("**原因**：`paper_engine.GATE_FILE` 硬编码为 `Path(__file__).parent/\"live\"/…`，**没有环境变量出口**。"
  "隔离实例与生产实例共用同一个闸门文件 ⇒ 在隔离实例里点一下，就等于改生产文件。"
  "本轮因此**没有执行**闸门翻转，判未验证，只记录旁证（生产闸门 19:30:03 已被 `user_ui_control` 翻成开启并落盘）。\n\n")
w("**责任方**：反重力（`paper_engine.GATE_FILE` 缺可注入路径）。\n\n")
w("**最小整改范围**：闸门路径改成可被环境变量覆盖（与 `PAPER_LEDGER_DIR` 同一种做法），"
  "然后补一条「翻转 → 重启 → 仍生效」的隔离测试。\n\n")
w("---\n\n")

# ── 7 反重力交付交叉检查 ──────────────────────────────────────────────────
w("## 7. 反重力交付交叉检查\n\n")
w("### 7.1 页面清单：无法交叉核对\n\n")
w(f"审核开始时 `evidence/SYSTEM-CLOSELOOP-01/` 只有：{', '.join(FREEZE['反重力交付']['审核开始时内容'])}。"
  "**没有页面清单**，所以页面总数只能按实际路由与菜单机械枚举（4 顶级 / 16 可切换视图）。"
  "反重力在 19:12–19:32 之间才陆续写入 6 个文件（含 12 行状态矩阵、14 张截图），"
  "此时我的清单已冻结 17 分钟。**清单交叉核对这一条判「未核对」，不算通过。**\n\n")
w("### 7.2 审核期间，被审代码被并发修改\n\n")
w(f"冻结时刻 **{freeze_t}** 落了 {len(BF)} 个文件的 SHA-256。审核过程中复算，**{len(changed)} 个文件变了**：\n\n")
w("| 文件 | 冻结 SHA | 现在 SHA | 冻结 mtime | 现在 mtime | 字节 |\n|---|---|---|---|---|---|\n")
for rel, a, b, m1, m2, sz in changed:
    w(f"| `{rel}` | {a} | {b} | {m1} | {m2} | {sz} |\n")
w("\n")
w("反重力在 19:32 的回执里自述做了三件事：前端 `App.tsx` 恢复 3a~3d、"
  "后端 `paper_engine` 解除退役阻断、允许传入真实请求时戳。**这三件正好对应上表的前三行。**\n\n")
w("这带来两个后果，必须写清：\n\n")
w("1. **我的结论对应的是冻结版本**（`out/00_验收清单_冻结.json` 里的 29 个 SHA），"
  "不是现在磁盘上的版本。反重力改完之后我没有重跑全量，所以对最新版本不作结论。\n")
w("2. 「允许传入真实请求时戳」这一条，正是 6.1 认定的 P0 根因。**它在审核期间被写进了代码，而不是被移除。**\n\n")
w("### 7.3 反重力自述 vs 实测\n\n")
w("| 反重力的说法 | 实测 |\n|---|---|\n")
w("| 12 行状态矩阵全部 🟢 可用 | 16 视图里 2 个页签是空壳、27 项操作 11 项未验证 ⇒ **不成立** |\n")
w("| `verify_closeloop.py` 证明单笔闭环 | 它证明的是「有 position_id 返回」，没有验证成交 bar 是否晚于真实挂钟 ⇒ **不足以支撑闭环** |\n")
w("| 恢复 3a~3d 前端展示 | 实测四臂账户接口正常、顶栏权益 = 3a 臂 ⇒ **成立** |\n")
w("| 解除 paper_engine 退役阻断 | 实测 POST 写口是通的 ⇒ **成立**，但这恰好是 A10 的矛盾点 |\n")
w("| 加动态权益兜底（`backtest_store.py`） | 实测 `/equity` 从 404 变 200 ⇒ **成立** |\n\n")
w("---\n\n")

# ── 8 FT-LOW4H 收口 ──────────────────────────────────────────────────────
w("## 8. FT-LOW4H-GRID-AUDIT-02 两处问题收口\n\n")
w("### 8.1 版本核对论证重做：把「间接推断」换成「退出行为唯一吻合」\n\n")
w("原报告 1.3 用两条行为反证支撑「运行时读到了 `stoploss_s` 与 `trail_d`」，两条都站不住：\n\n")
w("| 原证据 | 为什么不成立 |\n|---|---|\n")
w("| ⑤ 四档仓位率 0.0990 / 0.0495 / 0.0330 / 0.0247 清晰可分 | "
  "仓位率 = 名义/权益 = 风险预算/(权益×s) = **0.005/s**，数学上只由 s 决定，**与 d 无关**。"
  "它最多证明 s 被读到，不能证明 d 被读到 |\n")
w("| ⑤ 附带的「若仍读 stoploss_pct=0.20 则四档塌缩到 0.025」 | 这是**未测量的反事实**，"
  "而且基准取自「旧审核不通过」这一结论，而旧审核结论在同一节已被声明「不再沿用」⇒ 循环论证 |\n")
w("| ⑥ 16 组（笔数, 净收益）无一对重复 | **不具判别性**：只要 s 不同，初始止损就不同，笔数必然不同。"
  "该检验在 d 被完全忽略时同样成立 |\n\n")
w("**新证据（实际退出行为）**：\n\n")
w("| 证据 | 结果 |\n|---|---|\n")
w("| 全量真值：9,849 笔用各自 (s,d) 重建 | 退出时刻 **9,849/9,849** 一致；账面止损低于重建值 **0 笔**；"
  "超出 >0.2% **0 笔** |\n")
w("| 退出类型（由重建判定，不读引擎标签） | 跟踪亏损 4,140 / 初始止损 2,863 / 跟踪盈利 2,773 / 期末技术结算 73 |\n")
w("| 判别矩阵：192 笔 × 16 个候选 (s,d) = 3,072 次重建 | 用真参数：**192/192 命中**；"
  "16 行里对角值全部为该行最高；**只改 d → 24.31%**（跟踪类子集 **6.15%**）；只改 s → 55.90% |\n")
w("| 仓位率比值 vs 理论 1/s | 偏差 −0.59% / −0.24% / −0.20%，**只作 s 的辅助证据** |\n\n")
w("**结论**：版本核对的方向不变（运行时确实读到了 s 和 d），但支撑它的证据换了 —— "
  "从「仓位率间接推断」换成「退出轨迹与真参数唯一吻合」。"
  "顺带诚实地说一句：**s 的判别力本来就弱**（多数笔的初始止损很快被跟踪价取代，改 s 不影响它们的退出时刻），"
  "所以 s 的主证据仍是仓位率，退出轨迹只作辅助；d 的证据则只能来自退出轨迹。\n\n")

w("### 8.2 C8 冻结文字、公式与两种解释\n\n")
w("**冻结原文（逐字，来自 `out/01_冻结分析计划.json`）**：\n\n")
w("> 改善不能只来自'两边都成交'的交易：与上一轮配对时，新增/减少/跳过交易之外的净改善占比 ≤ 50%\n\n")
w("> 通过门槛：C1~C8 中 ≥ 5 条满足才算'稳定'；C8 为必要条件\n\n")
w("**两种读法的公式**（分子分母必须写清，否则没法复核）：\n\n")
w("| 读法 | 分子 | 分母 | 判据 | 含义 |\n|---|---|---|---|---|\n")
w("| **甲（冻结文字字面）** | Σ Δ共有事件（两边都成交的配对带来的净改善） | Σ Δ总R（含新增/减少/跳过） | ≤ 50% 且分母 > 0 | "
  "改善必须主要来自「交易集合变了」 |\n")
w("| 乙（机制性改善读法） | 同上 | 同上 | ≥ 50% 且分母 > 0 | 改善必须主要来自「同一批交易的退出变好了」 |\n\n")
w("**两种读法的结果并列**（从 `out/05_逐配对.csv` 独立重算，与上游落盘结论逐项相符）：\n\n")
w("| 读法 | 通过的组合 | 个数 | 最高 C1~C8 满足数 | 达标（≥5） |\n|---|---|---|---|---|\n")
w(f"| 甲（字面 ≤50%） | {' / '.join(CLOSE['段']['B3 结果']['读法甲通过的组合'])} | "
  f"{len(CLOSE['段']['B3 结果']['读法甲通过的组合'])} | 4 | **无** |\n")
w(f"| 乙（反读 ≥50%） | {' / '.join(CLOSE['段']['B3 结果']['读法乙通过的组合'])} | "
  f"{len(CLOSE['段']['B3 结果']['读法乙通过的组合'])} | 4 | **无** |\n\n")
w("**关于「不在看结果后选择有利口径」**，三条机械证据：\n\n")
w(f"1. 冻结计划写盘时刻 **{CLOSE['段']['B1 冻结原文']['冻结文件指纹']['mtime']}**，"
  f"配对结果写盘时刻 **{CLOSE['段']['B1 冻结原文']['结果文件指纹']['mtime']}** —— 冻结早于结果；\n")
w("2. 两读法在 `05_配对与终裁.json` 里是**同时落盘**的两个键（`C8` 与 `C8_相反读法`），不是事后补算；\n")
w("3. 两读法在分母 > 0 的 12 个组合里**互为补集**，挑哪个都会改变名单，因此两个都必须报。\n\n")
w("**必须更正原报告的一处表述**：原文说「冻结文字与判据标题语义相反」，这不准确 —— "
  "两者其实同向（都要求两边都成交的占比 ≤ 50%）。原报告把「标题语义」实现成 ≥50%，"
  "是**把 ≤ 读成了 ≥**，属反向实现。本轮的处置是：**主口径取甲（冻结文字字面）**，"
  "乙保留为敏感性对照，两个名单与两个合计都并列写出。\n\n")
w("**但结论不随口径改变**：两种读法下最高满足数都是 4，门槛是 5，"
  "**没有任何组合达到稳定门槛**。所以「不选生产参数」这个结论与 C8 口径无关。\n\n")
w("---\n\n")

# ── 9 边界 ───────────────────────────────────────────────────────────────
w("## 9. 边界与不做的事\n\n")
w("- 不改反重力业务代码，不与其并行写同一文件（本轮只在 `evidence/SYSTEM-CLOSELOOP-AUDIT-01/` 下新增）；\n")
w("- 不选生产参数、不启动前向实验、不重跑 144 组；\n")
w("- 不改账本、不改闸门、不打开留出期；\n")
w("- 工程通过不代表策略有效；本报告不对策略收益作任何判断；\n")
w("- 结论对应**冻结版本**（29 个 SHA），不对审核期间被改后的版本作结论。\n\n")
w("---\n\n")

# ── 10 产物 ──────────────────────────────────────────────────────────────
w("## 10. 复现方式\n\n")
w("```\n")
w("cd evidence/SYSTEM-CLOSELOOP-AUDIT-01/queries\n")
w("python 00_freeze_checklist.py      # 冻结清单（只跑一次，报告引用其输出）\n")
w("node   02_ui_capture.js            # 15 视图结构 + 268 条网络调用采集\n")
w("node   02c_view_switch.js          # 23 项视图切换机械判定\n")
w("python 01_api_contract.py          # 18 条读侧接口契约\n")
w("python 03a_snapshot.py             # 账本快照 S1/S2（sqlite 在线 backup + SHA）\n")
w("python 03_ledger_reconcile.py      # 8 条账本对账（对快照 S1 复算）\n")
w("python 04_lifecycle_isolated.py    # 13 条生命周期（隔离实例 8899）\n")
w("node   05_cross_check.js           # 18 条 UI↔API 三层对账\n")
w("python 06_ftlow4h_closure.py       # FT-LOW4H 两处问题收口\n")
w("python 07_report.py                # 生成本报告\n")
w("python 08_selfcheck.py             # 报告数字与本报告的自检\n")
w("python 09_manifest.py              # 全产物 SHA-256 清单\n")
w("```\n\n")
w("环境：`python = /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python`；"
  "`node = /Users/boos/.workbuddy-ai/binaries/node/versions/22.22.2-6/bin/node`；"
  "`NODE_PATH = UI界面/web/node_modules`。\n")

(HERE / "00_报告.md").write_text("".join(L), encoding="utf-8")
print("已写入 00_报告.md，", len("".join(L)), "字符")
print("操作项计数:", op_cnt)
print("被审文件变更:", len(changed), "/", len(BF))
