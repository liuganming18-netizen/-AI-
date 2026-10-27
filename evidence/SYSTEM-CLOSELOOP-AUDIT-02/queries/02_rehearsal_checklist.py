# -*- coding: utf-8 -*-
"""02 复验清单 —— 阶段 0 的第二件交付物。

工单原文：
  「现在先核对整改范围并准备复验清单。收到反重力版本冻结回执后，再对该固定版本执行正式验收；
    **不对持续变动的版本拼接通过项**。」
  「**版本一致**：检查验收开始、结束及实际运行进程的版本；**发生变动时作废受影响结果并重新核验**。」
  「上一轮 13 项通过也须核查是否受修改影响；3 项失败和 11 项未验证必须逐项补证。」

本脚本做三件事，全部机械可复算：

  1. 重算上一轮冻结的 29 个文件 SHA-256，与 AUDIT-01 冻结值比对 ⇒ 「代码变动集」
  2. 给 72 个验收条目逐条声明「依赖文件集 / 依赖数据」，
     与变动集求交 ⇒ 每条是否「受修改影响」（这是作废判定的唯一依据，不靠印象）
  3. 给每条写死「复验方法 / 判据 / 需要的隔离条件 / 当前是否阻塞」

产物：out/02_复验清单.json、out/02_复验清单.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sqlite3

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-02"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
A01 = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-01/out"

F = {
    "adapter": "框架/backend/adapter.py",
    "routes": "框架/backend/api/routes.py",
    "router": "框架/backend/api/router.py",
    "live_api": "框架/backend/live_api.py",
    "live_store": "框架/backend/live_store.py",
    "paper_engine": "框架/backend/paper_engine.py",
    "live_account": "框架/backend/live_account.py",
    "live_worker": "框架/backend/live_worker.py",
    "live_signal_engine": "框架/backend/live_signal_engine.py",
    "backtest_store": "框架/backend/backtest_store.py",
    "backtest_api": "框架/backend/backtest_api.py",
    "backtest_runner": "框架/backend/backtest_runner.py",
    "datacenter": "框架/backend/datacenter_service.py",
    "metrics": "框架/backend/metrics_api.py",
    "smart_money": "框架/backend/smart_money.py",
    "App": "UI界面/web/src/App.tsx",
    "useLiveTrading": "UI界面/web/src/api/useLiveTrading.ts",
    "liveTrading": "UI界面/web/src/api/liveTrading.ts",
    "useMarketData": "UI界面/web/src/api/useMarketData.ts",
    "client": "UI界面/web/src/api/client.ts",
    "Nav": "UI界面/web/src/components/NavigationSidebar.tsx",
    "SignalList": "UI界面/web/src/components/SignalListPanel.tsx",
    "Position": "UI界面/web/src/components/PositionPanel.tsx",
    "TokenTable": "UI界面/web/src/components/TokenTable.tsx",
    "DataCenter": "UI界面/web/src/components/DataCenterView.tsx",
    "Strategy": "UI界面/web/src/components/StrategyManagementView.tsx",
    "BacktestView": "UI界面/web/src/components/BacktestView.tsx",
    "BacktestWorkbench": "UI界面/web/src/components/BacktestWorkbench.tsx",
    "vite": "UI界面/web/vite.config.ts",
    "gate": "框架/backend/live/paper_new_entry_gate.json",
}

D_LIVE = "live_signals.sqlite3（生产 live 库）"
D_PAPER = "数据/paper/paper_3a~3d.sqlite3（纸面账本）"
D_GATE = "live/paper_new_entry_gate.json（生产闸门）"
D_BT = "backtest_results/（回测 run 目录）"
D_ISO = "隔离实例：端口 8899 + /tmp 账本"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 条目定义
# 字段：id / 组（对应工单 7 个重点项）/ 上轮 / 依赖文件键 / 依赖数据 / 方法 / 判据 / 隔离 / 备注
ITEMS: list[dict] = []


def add(i, g, prev, files, data, how, crit, iso, note=""):
    ITEMS.append({"id": i, "组": g, "上轮": prev, "依赖文件": files,
                  "依赖数据": data, "复验方法": how, "判据": crit,
                  "隔离条件": iso, "备注": note})


# ============ ① 交易因果 ============
add("L5", "① 交易因果", "不通过", ["live_api", "paper_engine", "live_store"], [D_LIVE, D_PAPER, D_GATE],
    "隔离实例发起开仓，比对三处时间：服务端接收锚点（now_ms 写入时刻）、数据可用时间（K 线 available_at）、"
    "成交时间（fill.ts）。要求 成交时间 >= max(接收锚点, 数据可用时间)",
    "对每一笔成交：fill_ms - 服务端接收时刻 >= 0 且 fill_ms 对应的 15m 桶起点+5m <= 决策时刻；任一为负即不通过",
    D_ISO + "；不得用生产库", "上一轮缺陷：成交可早于请求时刻")
add("L5b", "① 交易因果", "不通过", ["live_api"], [D_ISO],
    "读源码：写入口是否还从请求体读 requested_at / now_ms",
    "全后端 payload 取值里不得出现 requested_at / now_ms 参与成交时钟；出现即不通过",
    "只读源码，不需实例", "上一轮根因条目")
add("C1a", "① 交易因果", "通过", ["live_api", "paper_engine"], [D_ISO],
    "隔离实例用真实时钟开仓，观察是否登记挂单而非立即成交",
    "返回 pending，且未产生 fill 行；下一根 15m 收盘后才成交",
    D_ISO, "需重跑：live_api/paper_engine 均已变")
add("C1b", "① 交易因果", "不通过", ["live_api"], [D_ISO],
    "注入过去 requested_at（反例复现）：请求体带 requested_at = now-6h",
    "**必须被忽略**：成交时间不得因注入而提前；若仍按注入时刻成交 ⇒ 不通过",
    D_ISO, "这是反例矩阵第一条")
add("C1c", "① 交易因果", "通过", ["live_api", "live_store", "paper_engine"], [D_ISO],
    "平仓 → 已实现入账 → 余额闭合（隔离账本）",
    "initial + 已实现 == cash，差额绝对值 < 1e-9", D_ISO, "需重跑")
add("C3", "① 交易因果", "通过", ["live_api", "live_store"], [D_ISO],
    "同一 signal_uid 连续提交两次（重试反例）",
    "只成交一笔；第二次返回幂等回执而非新增 fill", D_ISO, "重试反例")
add("C5", "① 交易因果", "通过", ["live_api", "paper_engine"], [D_ISO],
    "对无 K 线标的开仓（数据失效反例）",
    "不得用任何价格成交；必须返回可解释拒因", D_ISO, "数据失效反例")
add("A11", "① 交易因果", "不通过", ["live_api", "adapter"], [D_ISO],
    "POST /api/v1/paper/orders 契约：正常请求必须能按既定规则完成成交",
    "**不能只靠全部拒单通过**：必须至少 1 笔正常请求走完 挂单→成交→持仓，且拒因码与实际原因一致",
    D_ISO, "工单明确：正常请求必须能成交")
add("NEW-CAUSAL-OK", "① 交易因果", "新增", ["live_api", "paper_engine", "live_store"], [D_ISO],
    "正向路径：完全正常的一笔开仓请求（无注入、真实时钟、有效标的、资金充足）",
    "必须成交且三处时间自洽；若只能全部拒单 ⇒ 整体不通过（工单禁止用拒单冒充通过）",
    D_ISO, "工单新增要求，上一轮未单列")

# ============ ② 隔离 ============
add("L8", "② 隔离", "不通过", ["live_api", "live_store"], [D_LIVE, D_PAPER, D_GATE],
    "测试前后对生产库/生产闸门取 SHA-256 与行数快照，逐项比对",
    "测试前后：生产 live 库 SHA 不变、四臂账本 SHA 不变、闸门文件 SHA 不变；任一变化即不通过",
    "测试必须指向隔离实例；生产侧只读采样", "上一轮合成信号已进生产库")
add("Z1", "② 隔离", "通过", ["paper_engine", "live_api"], [D_GATE],
    "隔离纪律自检：读生产闸门 SHA 前后比对",
    "生产闸门 SHA 不变；且隔离实例读到的是 PAPER_GATE_FILE 指定的隔离文件",
    "需 PAPER_GATE_FILE 环境变量生效", "上一轮靠硬编码 __file__ 同级，只能不点；现已修")
add("C0", "② 隔离", "通过", ["adapter", "live_api", "live_store"], [D_ISO],
    "启动隔离实例（8899，/tmp 账本，/tmp live 库）",
    "健康检查 200 且读到的库路径在 /tmp 下", D_ISO, "前置条件")
add("X3c", "② 隔离", "不通过", ["SignalList", "live_api"], [D_LIVE],
    "当前信号区是否混入合成测试信号",
    "页面当前信号区不得出现 sig_closeloop_* / sig_ui_test_* 之类测试标识；出现即不通过",
    "需用户批准清理范围后才能复验", "工单：不删除、不替用户批准清理")
add("A20", "② 隔离", "未验证", ["paper_engine", "live_api"], [D_GATE],
    "隔离实例上翻闸门，检查是否只写 PAPER_GATE_FILE 指定文件并落盘",
    "翻转后隔离闸门文件内容变化、生产闸门 SHA 不变；重启后仍保持", D_ISO, "上一轮因硬编码无法隔离")

# ============ ③ 执行身份 ============
add("A10", "③ 执行身份", "不通过", ["live_api", "adapter"], [],
    "读源码 + 读路由注册：只读声明、写入口 docstring、实际挂载的路由三者是否一致",
    "三处表述必须一致；出现「已接通新共享核心」但实际 return legacy 即不通过",
    "只读", "上一轮三句自相矛盾")
add("C6", "③ 执行身份", "通过", ["live_api", "live_worker"], [],
    "写路径是否存在可执行的交易所下单调用",
    "不得存在真实下单调用；存在即不通过", "只读源码", "需重跑：live_api 已变")
add("NEW-IDENTITY", "③ 执行身份", "新增", ["adapter", "live_api", "paper_engine"], [D_PAPER],
    "UI 实际调用的核心 / 策略版本 / 参数，与声明是否一致（工单第③项）",
    "UI 触发的写路径落到哪个核心、读的哪份参数文件、版本常量是什么，三者与「当前研究策略」声明一致；"
    "旧执行路径不得冒充当前研究策略", "只读 + 隔离实例实测一次写请求", "工单第③项，上一轮未单列")

# ============ ④ 账本闭环 ============
for i, t in [("L1", "纸面账户闭合（逐臂可加总）"), ("L1b", "四臂跨账户加总"),
             ("L2", "手续费/资金费各计一次"), ("L3", "标识贯通 signal_uid→order→fill→position→realized"),
             ("L6", "期末持仓与账本一致，未靠强制结算伪装已实现")]:
    add(i, "④ 账本闭环", "通过", ["live_store", "paper_engine"], [D_PAPER],
        f"重算：{t}", "差额绝对值 < 1e-9 且逐臂可加总", "只读账本副本", "需重跑：live_store/paper_engine 已变")
add("L4a", "④ 账本闭环", "未验证", ["live_store", "paper_engine"], [D_PAPER],
    "初始止损可重建：每笔持仓的 stop_price 必须等于 入场价 − 3×ATR，且 paper_stop_history 有 initial_stop 行",
    "逐笔：|stop_price − (entry_price − 3×atr_at_entry)| < 1e-6，且轨迹表首行 reason='initial_stop'",
    "只读", "本轮已发现 position 1 差 666.9（81200 vs 80533.1），违规 1/3")
add("L4b", "④ 账本闭环", "未验证", ["paper_engine"], [D_PAPER],
    "跟踪上移可重建：止损上移必须有带时点的记录，且引擎在真实推进中会调用写入",
    "**必须证明引擎会调用 record_stop_update**，不能只证明该函数单独调用时能写成功；"
    "同时与回测口径（trailing_trigger_r=2.0 / lookback 10 根 4h）比对",
    "只读源码 + 隔离实例推进", "本轮已查明：record_stop_update 全后端零调用；"
    "paper_engine L19 声明 ROI/trailing=off ⇒ 模拟盘按设计不做跟踪，与回测不同规则，必须在⑥对账里显式写明")
add("L7", "④ 账本闭环", "通过", ["backtest_store", "backtest_api"], [D_BT],
    "回测 run 目录 / summary.json / trades.jsonl 三者对账",
    "逐 run：笔数、净盈亏、期末持仓三处一致", "只读", "需重跑：backtest_store 已变")
add("C4", "④ 账本闭环", "通过", ["live_store", "paper_engine"], [D_ISO],
    "写入后重启隔离实例，账户/持仓仍在",
    "重启前后账户与持仓逐字段一致", D_ISO, "重启恢复")
add("NEW-IDEMPOTENT", "④ 账本闭环", "新增", ["live_api", "live_store"], [D_ISO],
    "幂等：同一请求重复提交 N 次，订单/成交/持仓/已实现/权益五表行数不得线性增长",
    "五表行数与单次提交完全一致；重复提交只产生幂等回执", D_ISO, "工单第④项要求验证幂等")

# ============ ⑤ 全 UI（27 项） ============
UI = [
    ("W1", "workbench.main", "通过", ["App", "useMarketData", "liveTrading", "live_api"],
     "顶栏 BTC/ETH 价格涨跌幅、模拟盘权益、连接状态", "三处数值与 /markets、/paper/accounts 精确相等（2 位小数）"),
    ("W2", "workbench.main", "不通过", ["SignalList", "live_api", "App"],
     "信号区：卡片数、signal_id 可查、无合成测试信号", "卡片数 = n_cards 且每条 signal_id 页面可查且无测试标识"),
    ("W3", "workbench.main", "未验证", ["App", "live_api", "paper_engine"],
     "闸门开关点击 → 落盘 → 重启保持", "隔离闸门文件内容变化、生产闸门 SHA 不变"),
    ("W4", "workbench.main", "未验证", ["TokenTable", "useMarketData", "adapter"],
     "行情表逐行对账 9 列数值", "抽样 N 行，9 列逐列与 /markets 相等"),
    ("W5", "workbench.main", "未验证", ["TokenTable", "useMarketData", "adapter"],
     "行内 K 线面板逐周期点击均出图", "8 个周期逐个点击，每次都有 /candles 请求且图有数据"),
    ("W6", "workbench.main", "通过", ["Position", "liveTrading", "live_api", "live_store"],
     "持仓区与账本一致", "逐笔字段相等；空态可解释"),
    ("W7", "workbench.main", "未验证", ["SignalList", "live_api"],
     "未选中信号就点开多（前端拦截）", "必须有可解释提示，不得发出请求或必须被明确拒"),
    ("W8", "workbench.main", "通过", ["SignalList", "live_api", "live_store"],
     "清空信号：回执删除行数 = 实际减少行数", "两数相等"),
    ("W9", "workbench.main", "未验证", ["App", "vite"],
     "浅/深主题切换后口径不变", "切换前后数值口径一致、无白屏/错色"),
    ("D1", "dc.dashboard", "通过", ["DataCenter", "datacenter", "useMarketData"],
     "数据中心主视窗 17 项数据源齐全", "17 项按简称全部命中，页面自报三处一致"),
    ("D2", "dc.pending", "未验证", ["DataCenter", "datacenter"],
     "待处理/回填视图与 /data/whitelist 条数对账", "页面条数 = 接口条数"),
    ("D3", "dc.matrix", "未验证", ["DataCenter", "datacenter", "metrics"],
     "覆盖矩阵与 /data/coverage 标的数对账", "页面标的数 = coverage 标的数"),
    ("D4", "dc.kline", "通过", ["DataCenter", "useMarketData", "adapter"],
     "K 线视图 8 个周期入口齐全", "8 个入口可点且各自出图"),
    ("D5", "dc.oi", "通过", ["DataCenter", "useMarketData", "adapter"],
     "OI 表格行数 = /data/oi items", "行数相等（含表头换算）"),
    ("D6", "dc.funding", "通过", ["DataCenter", "useMarketData", "adapter"],
     "费率行数 = /data/funding items 且符号保留", "行数相等且有负值未被取绝对值"),
    ("D7", "dc.smartmoney", "通过", ["DataCenter", "useMarketData", "smart_money"],
     "聪明钱行数 = /data/smart-money items", "行数相等"),
    ("D8", "dc.metrics", "通过", ["DataCenter", "metrics"],
     "指标视图有真实内容", "渲染出表格且文本量充足，非空壳"),
    ("S1", "st.rules", "未验证", ["Strategy", "live_api", "metrics"],
     "策略管理：策略条数与注册表一致", "页面逐条计数 = /strategies 条数"),
    ("S2", "st.deps", "不通过", ["Strategy"],
     "数据依赖页有独立内容", "切换后正文必须变化且与该页主题相关"),
    ("S3", "st.versions", "不通过", ["Strategy"],
     "版本对比页有独立内容", "切换后正文必须变化且与该页主题相关"),
    ("B1", "bt.workbench.batch", "通过", ["BacktestWorkbench", "backtest_store", "backtest_api"],
     "批次列表条数 = /backtests/batches total", "两数相等"),
    ("B2", "bt.workbench.batch", "通过", ["BacktestWorkbench", "backtest_store", "backtest_api"],
     "批次汇总「合计 N 笔 / 净盈亏 X」= summary", "笔数与金额精确相等"),
    ("B3", "bt.workbench.run", "通过", ["BacktestWorkbench", "backtest_store", "backtest_api"],
     "按 run 列表条数 = 有成交子集，且 q 过滤在截断之前", "两数相等且过滤生效"),
    ("B4", "bt.workbench.run", "通过", ["BacktestWorkbench", "backtest_api", "backtest_store"],
     "run 详情 equity 可读，目录/summary/逐笔可对账", "三处一致"),
    ("B5", "bt.review.batch", "未验证", ["BacktestWorkbench", "backtest_store", "backtest_api"],
     "验收复盘批次：与历史回测批次口径区分不混用", "页面明确标注口径，且条数/金额不跨口径混算"),
    ("B6", "bt.review.run", "未验证", ["BacktestWorkbench", "backtest_api"],
     "验收复盘 run：同上", "同上"),
    ("B7", "bt.workbench.run", "未验证", ["BacktestWorkbench", "backtest_api", "backtest_store"],
     "深链直达 run 详情并留证", "留存该视图 DOM + 截图，7 个子接口均被触发"),
]
for i, view, prev, deps, title, crit in UI:
    add(i, "⑤ 全 UI", prev, deps, [D_LIVE, D_PAPER] if i in ("W1", "W6", "W8", "W2", "W3") else [],
        f"浏览器实测：{view} → {title}", crit,
        "真实浏览器（playwright chromium_headless_shell-1248）；写操作走隔离实例",
        "工单要求逐项执行，不接受接口 200 / 截图代替")

# ============ ⑥ 回测与模拟 ============
add("NEW-RECON", "⑥ 回测与模拟", "新增", ["paper_engine", "backtest_store", "backtest_api"], [D_PAPER, D_BT],
    "同规则对账：把回测成交模型与模拟盘成交模型逐参数比对，并对固定输入独立复算",
    "参数逐项对齐或显式列出差异；差异必须给出原因。"
    "**必须区分已实现权益、盯市权益、期末技术结算三者**，不得混算",
    "只读 + 独立复算脚本", "工单第⑥项，上一轮未单列；已知差异：paper 声明 ROI/trailing=off")
add("NEW-REALIZED", "⑥ 回测与模拟", "新增", ["live_store", "paper_engine"], [D_PAPER],
    "已实现 / 盯市 / 期末技术结算三分：逐笔标注退出原因后分别求和",
    "三者分别给出金额与笔数，且 已实现 + 未实现盯市 + 期末技术结算 == 总盈亏；不得把 force_exit 当已实现",
    "只读", "上一轮踩坑：is_open 全 False、left_open_trades 元素为 None")

# ============ ⑦ 版本一致 ============
add("VER-D1", "⑦ 版本一致", "新增", [], [],
    "同一文件集 T0/T1 两次采样零变化", "变动数 == 0", "只读", "见 00_版本漂移探针")
add("VER-D2", "⑦ 版本一致", "新增", [], [],
    "采样时点之后无新写入", "所有被审文件 mtime <= 采样时刻", "只读", "同上")
add("VER-D3", "⑦ 版本一致", "新增", [], [],
    "运行中后端进程启动时刻 >= 最后一次代码变更", "进程启动晚于最晚 mtime", "只读 ps", "同上")


def main() -> None:
    now = dt.datetime.now().astimezone()
    frozen = json.loads((A01 / "00_验收清单_冻结.json").read_text(encoding="utf-8"))
    fp = frozen["被审版本指纹"]

    # ---- 1. 代码变动集
    changed, same = [], []
    for rel, rec in fp.items():
        p = BASE / rel
        if not p.exists():
            changed.append({"文件": rel, "变化": "文件消失"})
            continue
        cur = sha(p)
        st = p.stat()
        row = {"文件": rel, "AUDIT01_sha": rec["sha256"][:12], "当前_sha": cur[:12],
               "AUDIT01_字节": rec["字节"], "当前_字节": st.st_size,
               "当前_mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")}
        (changed if cur != rec["sha256"] else same).append(row)
    changed_files = {c["文件"] for c in changed}

    # ---- 2. 数据侧变动（与 AUDIT-01 落库值比对，能比的才比）
    l1 = json.loads((A01 / "03_账本对账.json").read_text(encoding="utf-8"))
    arm3a = l1["明细"][0]["实测"]["per_arm"][0]
    db = BASE / "数据/paper/paper_3a.sqlite3"
    data_now = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        # 注意：cash 在 paper_state（paper_account 表本来就是 0 行，读它会得到 null 并误判「账本已变」）
        cash = cur.execute("SELECT cash FROM paper_state WHERE account_id=?",
                           (arm3a["account_id"],)).fetchone()
        n_re = cur.execute("SELECT COUNT(*) FROM paper_realized").fetchone()[0]
        n_pos = cur.execute("SELECT COUNT(*) FROM paper_position").fetchone()[0]
        tabs = sorted(r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        con.close()
        data_now = {"3a_cash": (cash[0] if cash else None),
                    "3a_realized行数": n_re, "3a_position行数": n_pos,
                    "AUDIT01_cash": arm3a["cash"], "AUDIT01_realized行数": arm3a["realized行数"],
                    "表清单": tabs,
                    "AUDIT01之后新增表": [t for t in tabs if t in
                                          ("paper_request_anchor", "paper_stop_history")]}
        # AUDIT-01 落盘时 cash 保留 8 位小数（4995.65454473 vs 真值 ...45447261），
        # 容差必须放到 1e-6，否则会把 3.9e-9 的取整差误判成「账本变动」。
        data_now["账本是否变动"] = (
            abs((data_now["3a_cash"] or 0) - arm3a["cash"]) > 1e-6
            or n_re != arm3a["realized行数"])
    except Exception as exc:                                     # noqa: BLE001
        data_now = {"错误": str(exc)}

    gate_p = BASE / F["gate"]
    gate = json.loads(gate_p.read_text(encoding="utf-8"))
    gate_now = {"sha": sha(gate_p)[:12],
                "allow_new_entries": gate.get("allow_new_entries"),
                "set_by": gate.get("set_by"), "reason": gate.get("reason"),
                "mtime": dt.datetime.fromtimestamp(gate_p.stat().st_mtime).astimezone().isoformat(timespec="seconds")}

    # ---- 3. 逐条判受影响
    for it in ITEMS:
        dep = {F[k] for k in it["依赖文件"] if k in F}
        hit = sorted(dep & changed_files)
        it["受影响文件"] = hit
        it["受修改影响"] = bool(hit)
        it["依赖数据已变"] = bool(data_now.get("账本是否变动")) and (D_PAPER in it["依赖数据"])
        it["当前状态"] = ("作废待重跑" if it["受修改影响"] else
                          ("待重跑（数据已变）" if it["依赖数据已变"] else "待复验"))

    # ---- 4. 统计
    ui_items = [x for x in ITEMS if x["组"] == "⑤ 全 UI"]
    prev_pass = [x for x in ui_items if x["上轮"] == "通过"]
    prev_fail = [x for x in ui_items if x["上轮"] == "不通过"]
    prev_unv = [x for x in ui_items if x["上轮"] == "未验证"]
    stat = {
        "条目总数": len(ITEMS),
        "上轮27项": {"通过": len(prev_pass), "不通过": len(prev_fail), "未验证": len(prev_unv)},
        "13项通过中受代码修改影响": len([x for x in prev_pass if x["受修改影响"]]),
        "13项通过中不受影响": len([x for x in prev_pass if not x["受修改影响"]]),
        "受影响明细": [x["id"] for x in prev_pass if x["受修改影响"]],
        "不受影响明细": [x["id"] for x in prev_pass if not x["受修改影响"]],
        "代码变动文件数": f"{len(changed)}/{len(fp)}",
        "代码变动文件": [c["文件"] for c in changed],
        "账本是否变动": data_now.get("账本是否变动"),
        "闸门是否变动": True,   # 上一轮 19:30:03 为 true，现为 false
    }

    res = {"生成时刻": now.isoformat(timespec="seconds"),
           "AUDIT01冻结时刻": frozen["冻结时刻"],
           "判据说明": {
               "受修改影响": "条目的依赖文件集 ∩ 代码变动集 ≠ ∅ ⇒ 该条上一轮结论作废，必须重跑",
               "依赖数据已变": "条目读纸面账本，而账本 cash 或已实现行数与 AUDIT-01 记录不一致 ⇒ 必须重跑",
               "不受影响": "仅表示被审代码未变；**不等于结论仍有效**，仍需运行态（进程启动时刻）确认",
           },
           "代码变动": {"变动": changed, "未变数": len(same)},
           "数据侧": {"纸面账本": data_now, "生产闸门": gate_now},
           "统计": stat,
           "条目": ITEMS}
    (OUT / "02_复验清单.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 5. Markdown
    L = []
    L.append("# 复验清单（阶段 0 交付物，未开始正式验收）\n")
    L.append(f"- 生成时刻：`{now.isoformat(timespec='seconds')}`")
    L.append(f"- 对照基准：AUDIT-01 冻结时刻 `{frozen['冻结时刻']}`，被审文件 {len(fp)} 个")
    L.append(f"- 条目总数：**{len(ITEMS)}**\n")
    L.append("## 一、代码变动集（作废判定的唯一依据）\n")
    L.append("| 文件 | AUDIT-01 | 当前 | 字节 | 当前 mtime |")
    L.append("|---|---|---|---|---|")
    for c in changed:
        L.append(f"| `{c['文件']}` | `{c['AUDIT01_sha']}` | `{c['当前_sha']}` | "
                 f"{c['AUDIT01_字节']} → {c['当前_字节']} | {c['当前_mtime']} |")
    L.append(f"\n**变动 {len(changed)} / {len(fp)}**，未变 {len(same)}。\n")
    L.append("## 二、数据侧变动\n")
    L.append(f"- 纸面账本 3a：cash `{data_now.get('AUDIT01_cash')}` → `{data_now.get('3a_cash')}`，"
             f"已实现行数 `{data_now.get('AUDIT01_realized行数')}` → `{data_now.get('3a_realized行数')}` "
             f"⇒ **变动 = {data_now.get('账本是否变动')}**")
    L.append(f"- 生产闸门：`allow_new_entries={gate_now['allow_new_entries']}`、"
             f"`set_by={gate_now['set_by']}`、sha `{gate_now['sha']}`、mtime `{gate_now['mtime']}`\n")
    L.append("## 三、上一轮 13 项通过：是否受修改影响\n")
    L.append("| 编号 | 上轮 | 受影响 | 命中的变动文件 | 当前状态 |")
    L.append("|---|---|---|---|---|")
    for x in prev_pass:
        L.append(f"| {x['id']} | 通过 | {'**是**' if x['受修改影响'] else '否'} | "
                 f"{'、'.join('`'+h.split('/')[-1]+'`' for h in x['受影响文件']) or '—'} | {x['当前状态']} |")
    L.append(f"\n**受代码修改影响 {stat['13项通过中受代码修改影响']} 项，不受影响 "
             f"{stat['13项通过中不受影响']} 项。**")
    L.append("不受影响只代表被审代码没动，**不代表结论还能用**：运行态（后端进程启动时刻）、"
             "账本数据、闸门状态都变了，仍需在冻结版本上重跑一遍才能写进最终结论。\n")
    L.append("## 四、逐条复验清单\n")
    order = ["① 交易因果", "② 隔离", "③ 执行身份", "④ 账本闭环", "⑤ 全 UI", "⑥ 回测与模拟", "⑦ 版本一致"]
    for g in order:
        rows = [x for x in ITEMS if x["组"] == g]
        if not rows:
            continue
        L.append(f"### {g}（{len(rows)} 条）\n")
        L.append("| 编号 | 上轮 | 复验方法 | 判据 | 隔离条件 |")
        L.append("|---|---|---|---|---|")
        for x in rows:
            L.append(f"| {x['id']} | {x['上轮']} | {x['复验方法']} | {x['判据']} | {x['隔离条件']} |")
        L.append("")
    (OUT / "02_复验清单.md").write_text("\n".join(L), encoding="utf-8")

    print("=== 代码变动 ===")
    print(f"{len(changed)}/{len(fp)} 变动")
    for c in changed:
        print(f"  {c['文件']:44s} {c['AUDIT01_sha']} → {c['当前_sha']}  {c['当前_mtime']}")
    print("\n=== 数据侧 ===")
    print("  纸面账本:", json.dumps(data_now, ensure_ascii=False))
    print("  生产闸门:", json.dumps(gate_now, ensure_ascii=False))
    print("\n=== 上轮 13 项通过 ===")
    for x in prev_pass:
        print(f"  {x['id']:4s} {'受影响' if x['受修改影响'] else '不受影响':6s} {x['当前状态']}")
    print(f"\n受影响 {stat['13项通过中受代码修改影响']} / 不受影响 {stat['13项通过中不受影响']}")
    print(f"\n条目总数 {len(ITEMS)}")


if __name__ == "__main__":
    main()
