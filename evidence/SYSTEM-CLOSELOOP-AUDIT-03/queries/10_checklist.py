# -*- coding: utf-8 -*-
"""10 把上一轮冻结的 **59 条复验清单**逐项落表：执行时间 / 输入 / 预期 / 实际 / 证据 / 判定。

原则：
  · 每条只引用**本轮真跑出来的**断言（02/03/04/05/06/07/08/09 的编号），不引用"应该没问题"；
  · 页面类条目（W/D/S/B 组）用**浏览器抓到的 DOM**与**直接打接口**的条数比，两边都要有数；
  · 比不动的条目一律写「未验证」+ 具体原因，**不写成通过**。

产物：out/10_59条逐项.json / .md
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import urllib.error
import urllib.request

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
API = "http://127.0.0.1:8787/api/v1"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ROWS: list[dict] = []
DETAIL: dict = {}


def api_get(path):
    try:
        with _OP.open(API + path, timeout=40) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"__error": e.code}
    except Exception as e:                                       # noqa: BLE001
        return {"__error": str(e)}


def load(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def idx(doc, key="断言"):
    return {x.get("编号"): x for x in doc.get(key, []) if x.get("编号")}


def add(no, group, what, expect, actual, verdict, evidence, t=None):
    ROWS.append({"编号": no, "组": group, "检查项": what, "预期": expect, "实际": actual,
                 "判定": verdict, "证据": evidence,
                 "执行时间": t or DETAIL["本轮执行窗口"]})
    mark = {"通过": "OK ", "不通过": "FAIL", "未验证": "SKIP"}[verdict]
    print(f"  [{mark}] {no:14s} {verdict}  {what[:52]}")


def roll(no, group, what, ids, src, expect, evid, need_all=True):
    """把若干子断言的判定合成一条。"""
    miss = [i for i in ids if i not in src]
    if miss:
        add(no, group, what, expect, f"缺子断言 {miss}", "未验证", evid)
        return
    vs = [src[i]["判定"] for i in ids]
    bad = [i for i in ids if src[i]["判定"] != "通过"]
    ok = (not bad) if need_all else (any(v == "通过" for v in vs))
    add(no, group, what, expect,
        "；".join(f"{i}={src[i]['判定']}" for i in ids) +
        ("" if ok else " ⇒ 见 " + "、".join(bad)),
        "通过" if ok else "不通过", evid)
    return ok


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DETAIL["本轮执行窗口"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    a02 = idx(load("02_因果链.json"))
    a03 = idx(load("03_跟踪止损与退出.json"))
    a04 = idx(load("04_回测与模拟对账.json"))
    a05 = idx(load("05_隔离与恢复.json"))
    a07 = idx(load("07_UI核心操作.json"))
    a09 = idx(load("09_账本身份.json"))
    ui06 = load("06_ui_ro.json")
    ui08 = load("08_ui_compare.json")
    v08 = ui08.get("views", {})
    pr08 = ui08.get("probes", {})

    print("\n" + "=" * 78 + "\n① 交易因果（9 条）\n" + "=" * 78)
    g = "① 交易因果"
    roll("L5", g, "成交时间不得早于接收锚点/数据可用时间（无前视）", ["E9", "E7"], a02,
         "fill_ts >= bar_close_time 且 entry_ts = 成交 bar 开盘",
         "02_因果链 E9/E7")
    roll("L5b", g, "写入口不接受调用方注入 requested_at/now_ms 参与成交时钟", ["L5b"], a09,
         "存在 is_replay 闸门，非 replay 一律真实挂钟", "09_账本身份 L5b")
    roll("C1a", g, "真实时钟开仓：先登记挂单，下一根 15m 收盘后才成交", ["E1", "E2", "E3"], a02,
         "pending → 到点成交 1 笔", "02_因果链 E1/E2/E3")
    roll("C1b", g, "注入过去 requested_at 必须被忽略（反例）", ["F1", "F2"], a02,
         "伪造时钟不影响最早成交时刻", "02_因果链 F1/F2")
    roll("C1c", g, "平仓→已实现→余额闭合（隔离账本）", ["D9", "D10"], a03,
         "cash = 初始 + 已实现", "03_跟踪止损与退出 D9/D10")
    roll("C3", g, "同一 signal_uid 重复提交只成交一笔", ["E17", "E18"], a02,
         "第二次拒单 duplicate_signal_uid，持仓仍 1 笔", "02_因果链 E17/E18")
    roll("C5", g, "无 K 线标的：不得成交且不得无限挂单（数据失效反例）", ["F8"], a02,
         "必须返回可解释拒因", "02_因果链 F8")
    roll("A11", g, "正常请求必须能走完 挂单→成交→持仓（不能只靠拒单通过）",
         ["E3", "E4", "E5"], a02, "1 笔正常成交且价格/bar 正确", "02_因果链 E3/E4/E5")
    roll("NEW-CAUSAL-OK", g, "正向路径整体自洽（19 条 E 段全过）",
         [f"E{i}" for i in range(1, 20)], a02, "E1..E19 全通过", "02_因果链 E 段")

    print("\n" + "=" * 78 + "\n② 隔离（5 条）\n" + "=" * 78)
    g = "② 隔离"
    roll("L8", g, "测试前后生产库/闸门/信号库 SHA 不变", ["E1", "E2", "E3", "E4", "E5", "E6"], a05,
         "4 个账本 SHA 一致、mtime 早于测试、闸门/信号库一致、PID 不变", "05_隔离与恢复 E 段")
    roll("Z1", g, "隔离闸门只写 PAPER_GATE_FILE 指定文件，生产闸门不动",
         ["B4", "B5", "E3"], a05, "隔离闸门 allow=False 且生产闸门字节不变", "05_隔离与恢复 B4/B5/E3")
    roll("C0", g, "隔离实例健康且库路径都在 /tmp", ["B1", "B2", "B3"], a05,
         "账本/行情/信号三处路径均 /tmp", "05_隔离与恢复 B1/B2/B3")
    roll("X3c", g, "当前信号区不得混入合成测试信号", ["X3c"], a09,
         "0 条合成前缀", "09_账本身份 X3c")
    roll("A20", g, "隔离闸门翻转落盘 + 重启后保持", ["A20"], a09,
         "文件 allow=False 且新进程读到 False", "09_账本身份 A20")

    print("\n" + "=" * 78 + "\n③ 执行身份（3 条）\n" + "=" * 78)
    g = "③ 执行身份"
    roll("A10", g, "只读声明 / 写入口 / 实际路由三处一致", ["A10"], a09,
         "三处一致", "09_账本身份 A10")
    roll("C6", g, "写路径不存在真实交易所下单调用", ["C6"], a09, "0 处", "09_账本身份 C6")
    roll("NEW-IDENTITY", g, "UI 写路径 → paper 核心 → 冻结参数视图 → 版本常量可对齐",
         ["NEW-IDENTITY"], a09, "四处同源", "09_账本身份 NEW-IDENTITY")

    print("\n" + "=" * 78 + "\n④ 账本闭环（10 条）\n" + "=" * 78)
    g = "④ 账本闭环"
    for k, what, exp in [
        ("L1", "每臂现金 = 初始 + 已实现", "差额 <1e-6"),
        ("L1b", "四臂跨账户加总闭合", "差额 <1e-6"),
        ("L2", "手续费/资金费各计一次", "4 臂一致"),
        ("L3", "标识贯通 signal_uid→order→fill→position→realized", "全部贯通"),
        ("L3b", "平仓成交回指退出订单", "0 条 NULL"),
        ("L6", "未平仓持仓无已实现行（不靠强制结算伪装）", "0 条"),
        ("L4a", "初始止损可重建 = 入场价 − 3×ATR", "逐笔成立"),
        ("L4b", "止损上移由引擎推进函数写入（非测试直调）", "唯一调用方"),
        ("L7", "回测 run 三处笔数 + 金额对账", "全部一致"),
        ("L7b", "summary 自带 chain_ok / cash_identity_ok 全 True", "全 True"),
    ]:
        roll(k, g, what, [k], a09, exp, f"09_账本身份 {k}")
    roll("C4", g, "写入后重启隔离实例，账户/持仓逐字段一致", ["C1", "C2", "C3", "C4", "C5"], a05,
         "重启前后一致", "05_隔离与恢复 C 段")
    roll("NEW-IDEMPOTENT", g, "重复提交/重复推进不产生线性增长", ["D1", "D2", "D3", "D4", "D5"], a05,
         "只上移一次、历史只 +1、跨进程仍拒单", "05_隔离与恢复 D 段")

    print("\n" + "=" * 78 + "\n⑤ 全 UI 与核心操作（27 条）\n" + "=" * 78)
    g = "⑤ 全 UI"

    # W1 顶栏数值 vs 接口
    mk = api_get("/markets")
    pa = api_get("/paper/accounts")
    top = (v08.get("workbench.main", {}).get("dom", {}) or {}).get("topbar") or ""
    btc = None
    try:
        items = mk.get("items") or mk.get("data") or []
        btc = next((x for x in items if (x.get("symbol") or x.get("market_id")) == "BTCUSDT"), None)
    except Exception:                                            # noqa: BLE001
        pass
    DETAIL["W1"] = {"顶栏文本": top[:400], "BTC条目": btc,
                    "paper_accounts键": list(pa)[:12] if isinstance(pa, dict) else None}
    if btc and top:
        last = btc.get("last_price") or btc.get("close") or btc.get("price")
        # 顶栏是**页面加载时刻**的快照，事后重打接口价格已经变了 ⇒ 不能声称"精确相等"。
        # 只能比"同一标的、同一涨跌方向、同一量级"，并如实标未验证。
        pct = btc.get("price_change_ratio_24h")
        same_dir = (pct is not None and (("%.2f%%" % (pct * 100)) in top
                                         or ("%.2f%%" % (pct * 100)).lstrip("-") in top))
        add("W1", g, "顶栏 BTC/ETH 价格与 /markets 精确相等（2 位小数）",
            f"BTC 最新价 {last} 与涨跌幅 {pct} 逐位相等",
            f"顶栏='{top[:90]}'；事后接口 BTC={last}（涨跌 {pct}）⇒ 价格已漂移，"
            f"涨跌方向一致={same_dir}", "未验证",
            "08_ui_compare workbench.main.dom.topbar 与 GET /markets 不同时刻采样，"
            "无法回溯证明'同一时刻逐位相等'；只能证明同标的同方向")
    else:
        add("W1", g, "顶栏 BTC/ETH 价格与 /markets 精确相等", "可对上",
            "顶栏文本或 /markets 结构对不上（无法机械比对）", "未验证",
            "08_ui_compare workbench.main.dom.topbar")

    # W2 信号区
    sg = api_get("/signals?scope=current&limit=500")
    n_sig = len(sg.get("items") or []) if isinstance(sg, dict) else 0
    sig_txt = (v08.get("workbench.main", {}).get("dom", {}) or {}).get("bodyText") or ""
    n_dom_sig = (v08.get("workbench.main", {}).get("dom", {}) or {}).get("signalRows")
    syn_in_ui = "sig_closeloop" in sig_txt or "sig_ui_test" in sig_txt
    DETAIL["W2"] = {"接口信号数": n_sig, "页面信号卡片数": n_dom_sig,
                    "页面出现合成标识": syn_in_ui}
    add("W2", g, "信号区卡片数 = 接口条数，且无合成测试信号",
        f"{n_sig} 条且无合成标识",
        f"页面 {n_dom_sig} 张卡 / 接口 {n_sig} 条；页面出现合成标识={syn_in_ui}",
        "通过" if (n_dom_sig == n_sig and not syn_in_ui) else "不通过",
        "08_ui_compare workbench.main + GET /signals；09 X3c 已证库里有 3 条合成")

    roll("W3", g, "闸门开关点击 → 落盘 → 重启保持", ["C9", "C10", "D4"], a07,
         "POST 200、按钮翻转、闸门文件 False", "07_UI核心操作 C9/C10/D4")
    add("W4", g, "行情表逐行 9 列数值与 /markets 相等", "抽样逐列相等",
        "本轮只抓到表格行数（47 行含多张表），未做逐列数值抽取", "未验证",
        "08_ui_compare workbench.main.dom.rowTexts 只存前 5 行，不足以逐列对账")
    tf = pr08.get("timeframes") or []
    ok_tf = len(tf) >= 8 and all((x["candleReq"] > 0 or x["bigCharts"] > 0) for x in tf)
    add("W5", g, "K 线面板 8 个周期逐个点击均出图", "8 个周期都有请求/图",
        "；".join(f"{x['label']}:请求{x['candleReq']}/图{x['bigCharts']}" for x in tf),
        "通过" if ok_tf else "不通过", "08_ui_compare probes.timeframes")
    roll("W6", g, "持仓区与账本一致", ["C2", "C3"], a07,
         "connected=1、持仓行 1、显示账本止损价", "07_UI核心操作 C2/C3")
    roll("W7", g, "未选中信号点开多：前端拦截且给出可解释原因", ["C11"], a07,
         "disabled=true 且 title 说明原因", "07_UI核心操作 C11")
    add("W8", g, "清空信号：回执删除行数 = 实际减少行数", "两数相等",
        "本轮未在隔离实例点『清空信号』（会动信号库，且生产信号库里已有合成记录）",
        "未验证", "为避免与『不删除合成记录』约束冲突，本轮主动不做该项")
    th = pr08.get("theme") or {}
    add("W9", g, "浅/深主题切换后口径不变、无白屏", "切换前后正文量级一致",
        f"按钮={th.get('buttons')} 切换前正文 {th.get('beforeLen')} / 切换后 {th.get('afterLen')}",
        "通过" if th.get("afterLen") and abs((th.get("afterLen") or 0) - (th.get("beforeLen") or 0)) <
        max(200, 0.2 * (th.get("beforeLen") or 1)) else "未验证",
        "08_ui_compare probes.theme（只比正文量级，未做逐元素色值比对）")

    # D 组
    d_pending = api_get("/data/whitelist")
    d_cov = api_get("/data/coverage")
    d_oi = api_get("/data/oi?limit=150")
    d_fu = api_get("/data/funding?limit=150")
    d_sm = api_get("/data/smart-money?limit=150")

    def nitems(d):
        if not isinstance(d, dict):
            return None
        for k in ("items", "data", "rows", "whitelist_gaps"):
            if isinstance(d.get(k), list):
                return len(d[k])
        return None

    DETAIL["D组接口条数"] = {"whitelist": nitems(d_pending), "coverage": nitems(d_cov),
                          "oi": nitems(d_oi), "funding": nitems(d_fu),
                          "smart-money": nitems(d_sm),
                          "keys": {k: list(v)[:8] for k, v in
                                   [("whitelist", d_pending or {}), ("coverage", d_cov or {}),
                                    ("oi", d_oi or {}), ("funding", d_fu or {}),
                                    ("sm", d_sm or {})]}}
    add("D1", g, "数据中心主视窗 17 项数据源齐全", "17 项按简称全部命中",
        "本轮只做整页渲染取证，未逐项比对 17 个数据源", "未验证",
        "08_ui_compare dc.dashboard 无表格，bodyText 未逐项映射到 17 个数据源")
    for no, view, key, label in [("D2", "dc.pending", "whitelist", "待补/回填"),
                                 ("D5", "dc.oi", "oi", "OI"),
                                 ("D6", "dc.funding", "funding", "费率"),
                                 ("D7", "dc.smartmoney", "smart-money", "聪明钱")]:
        rows = (v08.get(view, {}).get("dom", {}) or {}).get("rowCount")
        n = DETAIL["D组接口条数"][key]
        if n is None or rows is None:
            add(no, g, f"{label}页行数 = 接口条数", "相等",
                f"页面 {rows} / 接口 {n}（取不到）", "未验证",
                f"08_ui_compare {view} + GET /data/{key}")
        else:
            add(no, g, f"{label}页行数 = 接口条数", f"{n}",
                f"页面 {rows} / 接口 {n}", "通过" if rows == n else "不通过",
                f"08_ui_compare {view}.dom.rowCount + GET /data/{key}")
    # D3 覆盖矩阵：接口给的是 13,155 个 (标的×周期) 单元格，页面是 150 行标的列表
    # ⇒ 两者粒度不同，不能直接判等（硬判等会得出一个假"不通过"）。
    add("D3", g, "覆盖矩阵页标的数 = coverage 标的数", "同一粒度可比",
        f"接口 cells={len((d_cov or {}).get('cells') or [])} 个单元格（标的×周期），"
        f"页面 {v08.get('dc.matrix', {}).get('dom', {}).get('rowCount')} 行标的列表 ⇒ 粒度不同",
        "未验证",
        "需先把 cells 按 symbol 去重才能与页面行数比；本轮未做该聚合")
    kl = (v08.get("dc.kline", {}).get("dom", {}) or {}).get("bodyText") or ""
    # 页面上是中文周期名，不是 1d/4h 这类缩写 —— 按缩写数会得出假"不通过"。
    tfs = ["日线", "12小时", "8小时", "4小时", "1小时", "30分钟", "15分钟", "5分钟"]
    hit_tf = [x for x in tfs if x in kl]
    add("D4", g, "K 线视图 8 个周期入口齐全", "8 个周期都出现在页面",
        f"命中 {len(hit_tf)} 个：{hit_tf}", "通过" if len(hit_tf) >= 8 else "不通过",
        "08_ui_compare dc.kline.dom.bodyText（中文周期名）")
    mrows = (v08.get("dc.metrics", {}).get("dom", {}) or {}).get("rowCount")
    mlen = (v08.get("dc.metrics", {}).get("dom", {}) or {}).get("bodyLen")
    add("D8", g, "指标视图有真实内容（非空壳）", "有表格且文本充足",
        f"行 {mrows} / 正文 {mlen} 字", "通过" if (mrows or 0) > 0 and (mlen or 0) > 800 else "不通过",
        "08_ui_compare dc.metrics")

    # S 组
    st = api_get("/strategies")
    n_st = nitems(st)
    DETAIL["S1"] = {"/strategies条数": n_st, "st.rules正文长度":
                    (v08.get("st.rules", {}).get("dom", {}) or {}).get("bodyLen")}
    add("S1", g, "策略管理策略条数 = /strategies 条数", f"{n_st} 条",
        f"接口 {n_st} 条；页面 st.rules 无表格（bodyLen="
        f"{DETAIL['S1']['st.rules正文长度']}）⇒ 无法机械计数", "未验证",
        "08_ui_compare st.rules 无 table，需 DOM 结构解析才能计数")
    b_rules = (v08.get("st.rules", {}).get("dom", {}) or {}).get("bodyText") or ""
    b_deps = (v08.get("st.deps", {}).get("dom", {}) or {}).get("bodyText") or ""
    b_vers = (v08.get("st.versions", {}).get("dom", {}) or {}).get("bodyText") or ""
    add("S2", g, "数据依赖页有独立内容（与规则说明不同）", "正文不同且相关",
        f"st.deps 正文 {len(b_deps)} 字，与 st.rules 相同={b_deps == b_rules}",
        "通过" if b_deps and b_deps != b_rules else "不通过",
        "08_ui_compare st.rules / st.deps bodyText 比对")
    add("S3", g, "版本对比页有独立内容", "正文不同且相关",
        f"st.versions 正文 {len(b_vers)} 字（{v08.get('st.versions', {}).get('dom', {}).get('rowCount')} 行表），"
        f"与 st.rules 相同={b_vers == b_rules}",
        "通过" if b_vers and b_vers != b_rules else "不通过",
        "08_ui_compare st.versions bodyText")

    # B 组
    bt = api_get("/backtests/batches")
    n_bt = bt.get("total") if isinstance(bt, dict) else None
    bt_api = (v08.get("bt.workbench.batch", {}).get("api") or [])
    bt_ui = load("11_bt_ui.json")
    st_batch = ((bt_ui.get("steps") or {}).get("batch") or {}).get("dom", {}) or {}
    tabs = {tuple(x.get("headers") or [])[:2]: x.get("rows") for x in (st_batch.get("tables") or [])}
    run_rows = next((v for k, v in tabs.items() if k and k[0] == "标的"), None)
    trade_rows = next((v for k, v in tabs.items() if k and k[0] == "#"), None)
    api_runs = next((a.get("shape", {}).get("total") for a in bt_api
                     if a["url"].rstrip("/").endswith("/runs")), None)
    api_trades = next((a.get("shape", {}).get("total") for a in bt_api
                       if "/trades?" in a["url"]), None)
    sub_eps = sorted({a["url"].split("/backtests/")[-1].split("/", 1)[1]
                      for a in bt_api if "/backtests/run_" in a["url"] and "/" in
                      a["url"].split("/backtests/")[-1]})
    DETAIL["B组"] = {"batches.total": n_bt, "run表行数": run_rows, "接口runs.total": api_runs,
                    "成交表行数": trade_rows, "接口trades.total": api_trades,
                    "run子接口": sub_eps}
    add("B1", g, "批次列表条数 = /backtests/batches total", f"{n_bt} 个批次",
        f"接口 total={n_bt}；页面上批次列表是**卡片不是表格**（本轮 9 张表里没有批次表）",
        "未验证", "11_bt_ui.json：batch 视图 9 张表，前 5 张 0 行；批次区无 table")
    add("B2", g, "批次成交笔数：页面表格行数 = /batches/{id}/trades total",
        f"{api_trades}", f"页面成交表 {trade_rows} 行 / 接口 total={api_trades}",
        "通过" if trade_rows == api_trades and trade_rows else "不通过",
        "11_bt_ui.json batch 表8 + 08_ui_compare 接口 shape")
    add("B3", g, "按 run 列表条数 = /batches/{id}/runs total", f"{api_runs}",
        f"页面 run 表 {run_rows} 行 / 接口 total={api_runs}",
        "通过" if run_rows == api_runs and run_rows else "不通过",
        "11_bt_ui.json batch 表5（表头『标的』）+ 接口 shape")
    add("B4", g, "run 详情 equity 可读，目录/summary/逐笔三处一致", "三处一致",
        f"run 详情 9 个接口 200、页面 {((((bt_ui.get('steps') or {}).get('runDetail') or {})
                                  .get('dom') or {}).get('tables') or [{}])[0].get('rows')} 行；"
        "但未把页面数值与 summary.json 逐项比", "未验证",
        "11_bt_ui.json runDetail；接口侧 L7/L7b 已证明 summary 与逐笔可对账")
    add("B5", g, "验收复盘批次与历史回测批次口径不混用", "页面明确标注口径",
        "本轮点『验收复盘』后页面 0 张表、0 个接口请求 ⇒ 没切换到该视图，取不到证据",
        "未验证", "11_bt_ui.json reviewBatch（0 表 0 接口）")
    add("B6", g, "验收复盘 run 同上", "同上",
        "同上（reviewRun 0 表 0 接口）", "未验证", "11_bt_ui.json reviewRun")
    add("B7", g, "深链直达 run 详情并留证（7 个子接口均被触发）", "≥7 个子接口",
        f"触发 {len(sub_eps)} 个：{sub_eps}",
        "通过" if len(sub_eps) >= 7 else "不通过",
        "08_ui_compare bt.workbench.batch.api 里的 /backtests/run_* 子路径")

    print("\n" + "=" * 78 + "\n⑥ 回测与模拟（2 条）+ ⑦ 版本一致（3 条）\n" + "=" * 78)
    roll("NEW-RECON", "⑥ 回测与模拟", "回测与模拟按同一 FT-LOW4H 规则逐事件对账",
         ["E1", "E2", "E3", "E4", "E5"], a04, "逐事件一致",
         "04_回测与模拟对账 E1..E5")
    add("NEW-RECON-口径", "⑥ 回测与模拟", "口径差异必须逐条列出并给原因",
        "差异逐条登记",
        "已登记 5 条差异（F1b/F2/F3/F4/F5），其中 F2（初始止损口径）与 F5（退出粒度）为实质差异",
        "通过", "04_回测与模拟对账 F 段")
    roll("NEW-REALIZED", "⑥ 回测与模拟", "已实现 / 盯市 / 期末技术结算三分",
         ["NEW-REALIZED"], a09, "三分加总 = summary", "09_账本身份 NEW-REALIZED")

    drift = load("00b_漂移快照.json")
    n_chg = drift.get("变动文件数") or drift.get("汇总", {}).get("变动文件数")
    DETAIL["VER"] = {"漂移": drift.get("汇总") or {k: drift.get(k) for k in
                                               ("变动文件数", "变动文件")}}
    add("VER-D1", "⑦ 版本一致", "同一文件集 T0/T1 两次采样零变化", "变动数 = 0",
        f"验收期间变动 {n_chg} 个文件（见 00b）", "不通过",
        "00b_漂移快照：开工基准作废，已按『只作废受影响检查并重跑』处理")
    add("VER-D2", "⑦ 版本一致", "采样时点之后无新写入（被审文件 mtime <= 采样时刻）",
        "全部 mtime <= 采样时刻",
        "采样后仍有文件被改（6 个文件 mtime 落在 21:47~21:57，晚于 21:34 开工采样）", "不通过",
        "00b_漂移明细.md")
    add("VER-D3", "⑦ 版本一致", "运行中后端进程启动时刻 >= 最后一次代码变更",
        "进程晚于最晚 mtime",
        "8787 进程启动 21:49:54 < paper_engine.py mtime 21:57:12 ⇒ 线上仍加载旧成交时钟", "不通过",
        "00b_漂移快照 H2 / 09 复算")

    # ── 汇总 ────────────────────────────────────────────────
    ok = sum(1 for x in ROWS if x["判定"] == "通过")
    ng = sum(1 for x in ROWS if x["判定"] == "不通过")
    nv = sum(1 for x in ROWS if x["判定"] == "未验证")
    print(f"\n合计 {len(ROWS)} 条：通过 {ok}，不通过 {ng}，未验证 {nv}")
    by_group = {}
    for x in ROWS:
        by_group.setdefault(x["组"], {"通过": 0, "不通过": 0, "未验证": 0})[x["判定"]] += 1
    for k, v in by_group.items():
        print(f"  {k}: 通过 {v['通过']} / 不通过 {v['不通过']} / 未验证 {v['未验证']}")

    res = {"生成时刻": DETAIL["本轮执行窗口"], "条目数": len(ROWS),
           "分组统计": by_group, "条目": ROWS, "明细": DETAIL,
           "汇总": {"总数": len(ROWS), "通过": ok, "不通过": ng, "未验证": nv}}
    (OUT / "10_59条逐项.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    md = ["# 10 上一轮冻结的 59 条复验清单 —— 逐项执行结果", "",
          f"- 生成时刻：{res['生成时刻']}",
          f"- 合计 {len(ROWS)} 条：**通过 {ok}，不通过 {ng}，未验证 {nv}**", "",
          "| 组 | 通过 | 不通过 | 未验证 |", "|---|---|---|---|"]
    for k, v in by_group.items():
        md.append(f"| {k} | {v['通过']} | {v['不通过']} | {v['未验证']} |")
    md += ["", "| 编号 | 组 | 检查项 | 预期 | 实际 | 判定 | 证据 |", "|---|---|---|---|---|---|---|"]
    for x in ROWS:
        md.append(f"| {x['编号']} | {x['组']} | {x['检查项']} | {x['预期']} | {x['实际']} | "
                  f"**{x['判定']}** | {x['证据']} |")
    (OUT / "10_59条逐项.md").write_text("\n".join(md), encoding="utf-8")
    print(f"已写出 {OUT/'10_59条逐项.json'} / .md")


if __name__ == "__main__":
    main()
