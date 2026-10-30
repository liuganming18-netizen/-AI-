"""只读接口路由表（架构重构第 1 期）。

这张表就是**接口清单本身**：加接口只改这里，不碰 `adapter.py`。

为什么用 `build(ns)` 而不是直接 import 处理函数
------------------------------------------------
第 1 期的纪律是「函数体原样搬、行为零变化」。处理函数此刻仍住在
`adapter.py` 里，且依赖大量模块级状态（STORE / live / backtest / datacenter /
缓存 / 降级文案）。用命名空间注入可以做到**不搬函数**就先把路由解耦，
等第 2、3 期再按资源逐个把函数搬进 `api/resources/`。

每一条 `note` 都写清「这个接口返回什么、缺数据时怎么表达」，
让这张表本身就能当接口文档看。
"""

from __future__ import annotations

from typing import Any

from api.router import Route, Router


def _signal_one(ns: dict[str, Any], signal_id: str) -> tuple[Any, int]:
    """`/api/v1/signals/{id}`：实时层 uid 优先，否则回落库内整数 id。

    详情/审计读取固定走 `scope=all`：按 uid 精确取单条，不受当前交易信号区
    默认口径限制（被移出当前区的历史/结构记录仍可按 uid 审计读取）。
    """
    live = ns.get("live")
    if live is not None:
        got = live.live_signals({"signal_id": [signal_id], "scope": ["all"]})
        items = got.get("items") or []
        if items:
            return ns["envelope"](
                signal=items[0], source="live_signal",
                quality={"status": "ok", "age_seconds": None,
                         "note": "实时信号层（展示与可执行分列；审计口径 scope=all）"}), 200
    return ns["v1_signal"](signal_id), 200


def _paper(ns: dict[str, Any], name: str, params: dict[str, list[str]],
           takes_params: bool = True) -> tuple[Any, int]:
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](f"实时/纸面模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"), 200
    fn = getattr(live, name)
    return (fn(params) if takes_params else fn()), 200


def _paper_http(ns: dict[str, Any], name: str,
                params: dict[str, list[str]]) -> tuple[Any, int]:
    """D03-R1：**保留处理器自己返回的状态码**（200 / 404 / 503）。

    与 `_paper` 的区别：`_paper` 恒把状态码钉成 200，只适合「缺什么就在 body 里说」
    的读接口。交易/权益两条契约要求用 **HTTP 状态码**区分
    「账户不存在(404)」「账本不可用(503)」「账户在但零成交(200)」，
    因此这里必须原样透传，不能统一改写成 200。
    """
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](f"实时/纸面模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"), 503
    return getattr(live, name)(params)


def _paper_daemon_status(ns: dict[str, Any], params: dict[str, list[str]]) -> tuple[Any, int]:
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](f"实时/纸面模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"), 200
    arm = (params.get("arm") or ["3a"])[0]
    try:
        try:
            import unified_runtime as UR
            st = UR.get_unified_runtime_status(arm)
            if st.get("runtime_identity", {}).get("status") != "not_started" or "market_data" in st:
                quality_status = st.get("runtime_identity", {}).get("overall_health", "ok")
                return ns["envelope"](item=st, quality={"status": quality_status, "note": "unified_runtime_status"}), 200
        except Exception:
            pass
        import paper_loop as PL
        st = PL.get_daemon_status(arm)
        quality_status = "ok" if st.get("running") else (st.get("status") or "stopped")
        return ns["envelope"](item=st, quality={"status": quality_status, "note": "execution_daemon_status"}), 200
    except Exception as exc:
        return ns["envelope"](missing_reason=f"获取守护进程状态失败: {exc}",
                             quality={"status": "error", "note": str(exc)}), 500


def _signals_observe(ns: dict[str, Any], params: dict[str, list[str]]) -> tuple[Any, int]:
    """`/api/v1/signals/observe`：处于观察状态的事件逐条明细（只读）。

    为什么单开一个接口、而不并进 `execution_daemon/status`
    ------------------------------------------------------
    状态文件每 2 秒全量重写一次；全池观察中事件约 346 条、约 221 KB，
    并进去等于每天 9~13 GB 写盘。明细又只在收盘推进/阶段迁移时才变，
    没有理由按 2 秒频率落盘 ⇒ 明细走独立文件（内容不变不写）+ 本接口按需读取。

    **三态必须分开**（工单要求：不能统一显示成「交易信号 0」）：
      · `quality.status="ok"`            —— 有/没有观察标的都是**真实结论**；
      · `quality.status="stale_record"`  —— 读到但已过期或写者已退出 ⇒ 不可当现状；
      · `quality.status="not_connected"` —— 运行时没起过 / 从未写出明细；
      · `quality.status="error"`         —— 文件读不了或解析失败。
    页面据此分别显示「没有符合条件的标的 / 数据未就绪 / 扫描或接口异常」。
    """
    arm = (params.get("arm") or ["3a"])[0]
    try:
        import unified_runtime as UR
        d = UR.get_observe_events(arm)
    except Exception as exc:  # 运行时模块不可用也算接口异常，如实降级
        return ns["envelope"](
            missing_reason=f"观察区明细不可用：{exc}",
            quality={"status": "error", "age_seconds": None, "note": str(exc)},
            counts={"BOX": 0, "SWEEP": 0, "RETEST": 0},
            observing_total=None, data_not_ready=None, universe=None,
        ), 200
    st = d.get("status") or "ok"
    items = d.get("items") or []
    return ns["envelope"](
        items=items,
        total=d.get("observing_total"),
        mode="observe_read_only",
        missing_reason=d.get("missing_reason"),
        quality={
            "status": st,
            "age_seconds": d.get("age_seconds"),
            "note": ("只读：不改状态、不推进 bar、不参与决策、不构成入场建议"
                     if st == "ok" else None),
        },
        # 三态判定所需的全部原始计数，页面不得自己合成
        counts=d.get("counts") or {"BOX": 0, "SWEEP": 0, "RETEST": 0},
        observing_total=d.get("observing_total"),
        data_not_ready=d.get("data_not_ready"),
        universe=d.get("universe"),
        stale=d.get("stale"),
        age_seconds=d.get("age_seconds"),
        writer_pid_alive=d.get("writer_pid_alive"),
        generated_at=d.get("generated_at"),
        scan_errors_total=d.get("scan_errors_total"),
        scan_errors=d.get("scan_errors") or [],
        shown=d.get("shown"),
        truncated=d.get("truncated"),
        definition=d.get("definition"),
        note=d.get("note"),
        arm=arm,
    ), 200


def _paper_stops(ns: dict[str, Any], position_id: str, params: dict[str, list[str]]) -> tuple[Any, int]:
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](f"实时/纸面模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"), 200
    arm = (params.get("arm") or ["3a"])[0]
    try:
        res = live.PE.get_stop_history(arm, int(position_id))
        if isinstance(res, dict) and not res.get("exists", True):
            return ns["envelope"](missing_reason=f"持仓 {position_id} 不存在 (404 Not Found)",
                                 quality={"status": "not_found", "note": "position_not_found"}), 404
        hist = (res.get("items") if isinstance(res, dict) else res) or []
        return ns["envelope"](items=hist, total=len(hist), mode="stop_history",
                             quality={"status": "ok", "age_seconds": None,
                                      "note": f"持仓 {position_id} 止损变更历史"}), 200
    except Exception as exc:
        return ns["envelope"](missing_reason=f"查询止损历史失败: {exc}",
                             quality={"status": "error", "note": str(exc)}), 500


def _live_call(ns: dict[str, Any], method: str, params: dict[str, list[str]],
               missing_note: str, takes_params: bool = True) -> tuple[Any, int]:
    """调用 live 模块的只读方法；模块不可用时如实降级，不抛 500。"""
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](missing_note), 200
    fn = getattr(live, method)
    return (fn(params) if takes_params else fn()), 200


def _backtests(ns: dict[str, Any], rest: list[str], params: dict[str, list[str]]) -> tuple[Any, int]:
    """不带 `mode` 走新通道（研究回测）；带 `mode`（history / paper_review）保持旧契约。"""
    mode = (params.get("mode") or [None])[0]
    backtest = ns.get("backtest")
    if mode is None and backtest is not None:
        return backtest.route(rest, params)
    live = ns.get("live")
    if live is None:
        return ns["_live_missing"](f"回测复盘模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"), 200
    return live.backtest_review(params), 200


def _dc_items(ns: dict[str, Any], method: str,
              params: dict[str, list[str]]) -> tuple[Any, int]:
    """数据中心四个明细表：统一 `items + total` 信封。"""
    datacenter = ns.get("datacenter")
    limit = int((params.get("limit") or [150])[0])
    q = (params.get("q") or [None])[0]
    items = getattr(datacenter, method)(limit, q) if datacenter else []
    return ns["envelope"](items=items, total=len(items)), 200


def _dc_feeds(ns: dict[str, Any]) -> tuple[Any, int]:
    feeds = ns["datacenter"].get_data_feeds_status() if ns.get("datacenter") else []
    return ns["envelope"](feeds=feeds, total=len(feeds)), 200


def _market_tail(ns: dict[str, Any], exchange: str, symbol: str,
                 tail: str, params: dict[str, list[str]]) -> tuple[Any, int]:
    """`/markets/{exchange}/{symbol}/{tail}` 的六个子资源。未知 tail 一律 404。"""
    if tail == "candles":
        return ns["v1_candles"](exchange, symbol, params), 200
    if tail == "oi":
        return ns["v1_oi"](exchange, symbol, params), 200
    if tail == "funding":
        return ns["v1_funding"](exchange, symbol, params), 200
    if tail == "rule-snapshots":
        return ns["v1_rule_snapshot"](exchange, symbol, params), 200
    if tail == "public-extras":
        # 币安公开扩展字段：只读本机快照（采集器周期写入），不触发任何交易所请求。
        return ns["v1_public_extras"](exchange, symbol, params), 200
    if tail == "metrics":
        # 指标矩阵：只读归档 parquet + 本机快照 + 只读库；缺失一律 null + missing_reason。
        metrics_api = ns.get("metrics_api")
        if metrics_api is None:
            return ns["missing"](f"指标模块不可用：{ns['_METRICS_IMPORT_ERROR']}",
                                 "not_connected"), 200
        return metrics_api.handle_metrics(exchange, symbol, params), 200
    raise LookupError("接口不存在")


def build(ns: dict[str, Any]) -> Router:
    """用 adapter 模块命名空间构造路由表。"""
    routes = [
        # ── 健康与总览 ────────────────────────────────────────────────
        Route("/api/v1/health", lambda c: (ns["v1_health"](), 200),
              group="light", note="依赖连通明细；失败项不计作连通"),
        Route("/api/v1/overview", lambda c: (ns["v1_overview"](), 200),
              note="总数与数据健康摘要"),

        # ── 标的池与市场 ──────────────────────────────────────────────
        Route("/api/v1/markets", lambda c: (ns["v1_markets"](), 200),
              group="heavy", cache=3, note="549 标的工作台列表，2 秒 TTL"),
        Route("/api/v1/pool", lambda c: (ns["v1_pool"](), 200),
              note="数据总池（≠ 交易资格，eligibility 恒 null）"),
        Route("/api/v1/pool/status", lambda c: (ns["v1_pool_status"](), 200),
              group="light", note="下载任务与库存状态"),

        # ── 策略注册表 ────────────────────────────────────────────────
        Route("/api/v1/strategies", lambda c: (ns["v1_strategies"](), 200),
              note="策略列表"),
        Route("/api/v1/strategies/registered",
              lambda c: (ns["v1_registered_configs"](), 200),
              note="18 套配置生效身份 + 共享资金模型 + 门禁 pending"),
        Route("/api/v1/strategy", lambda c: (ns["v1_strategy"](), 200),
              note="旧单数接口（前端 client.ts 仍在调用），顶层为策略 A"),
        Route("/api/v1/strategies/{strategy_id}",
              lambda c: (ns["v1_strategy_one"](c["strategy_id"]), 200),
              note="单个策略详情"),

        # ── 信号 ──────────────────────────────────────────────────────
        Route("/api/v1/signals", lambda c: (ns["_v1_signals_merged"](c["params"]), 200),
              group="light", note="实时层 + 库内历史信号；默认 scope=current"),
        Route("/api/v1/signals/{signal_id}",
              lambda c: _signal_one(ns, c["signal_id"]),
              group="light", note="单条信号；实时 uid 优先，回落库内 id"),
        Route("/api/v1/signals/observe",
              lambda c: _signals_observe(ns, c["params"]),
              group="light", cache=2,
              note="观察中事件逐条明细（币种/阶段/最近更新/已满足/未满足）；"
                   "三态：ok / stale_record / not_connected / error"),
        Route("/api/v1/smart-money/{exchange}/{market_id}",
              lambda c: (ns["smart_money"].v1_smart_money(
                  c["exchange"], c["market_id"], c["params"]), 200),
              note="只读本机快照，不触发交易所请求"),

        # ── 纸面与实盘（只读） ────────────────────────────────────────
        Route("/api/v1/paper/identity",
              lambda c: _paper(ns, "paper_identity_get", c["params"], takes_params=False),
              group="light", note="执行身份权威映射链"),
        Route("/api/v1/paper/accounts",
              lambda c: _paper(ns, "paper_accounts", c["params"], takes_params=False),
              group="light", note="纸面账户"),
        Route("/api/v1/paper/positions",
              lambda c: _paper(ns, "paper_positions", c["params"], takes_params=False),
              group="light", note="纸面持仓"),
        Route("/api/v1/paper/orders",
              lambda c: _paper(ns, "paper_orders", c["params"]),
              group="light", note="只读订单/挂单/成交查询；写入口是 POST"),
        # D03-R1 ①②③④：已平仓逐笔。零交易 200 / 未知臂·未知持仓号 404 / 账本不可用 503
        Route("/api/v1/paper/trades",
              lambda c: _paper_http(ns, "paper_trades", c["params"]),
              group="light", note="D03-R1 已平仓逐笔（零交易/404/503 契约）"),
        # D03-R1 ⑤③④：权益曲线。`format=jsonl` 时附 NDJSON 文本
        Route("/api/v1/paper/equity",
              lambda c: _paper_http(ns, "paper_equity", c["params"]),
              group="light", note="D03-R1 权益曲线（JSONL 可选）"),
        Route("/api/v1/paper/funding-audit",
              lambda c: _paper(ns, "paper_funding_audit", c["params"]),
              group="light", note="资金费符号口径 + 迟到补记缺口"),
        Route("/api/v1/paper/legacy-audit",
              lambda c: _paper(ns, "legacy_audit", c["params"], takes_params=False),
              group="light", note="旧版/未验收/规则违规只读清单"),
        Route("/api/v1/paper/status",
              lambda c: _paper_daemon_status(ns, c["params"]),
              group="light", note="模拟盘执行守护进程状态别名"),
        Route("/api/v1/paper/execution_daemon/status",
              lambda c: _paper_daemon_status(ns, c["params"]),
              group="light", note="模拟盘执行守护进程状态"),
        Route("/api/v1/paper/stops/{position_id}",
              lambda c: _paper_stops(ns, c["position_id"], c["params"]),
              group="light", note="指定持仓的FT-LOW4H止损上移轨迹"),
        Route("/api/v1/live/health",
              lambda c: _live_call(ns, "live_health", c["params"],
                                   f"实时模块导入失败：{ns['_LIVE_IMPORT_ERROR']}",
                                   takes_params=False),
              group="light", note="实时层健康"),
        Route("/api/v1/accounts",
              lambda c: _live_call(ns, "live_accounts", c["params"],
                                   f"实盘只读模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"),
              group="light", note="实盘只读账户"),

        # ── 回测与复盘 ────────────────────────────────────────────────
        Route("/api/v1/backtests/*",
              lambda c: _backtests(ns, c["rest"], c["params"]),
              group="light", note="研究回测列表/详情/子资源；带 mode 走旧复盘契约"),
        Route("/api/v1/reviews/*",
              lambda c: _live_call(ns, "backtest_review", c["params"],
                                   f"回测复盘模块导入失败：{ns['_LIVE_IMPORT_ERROR']}"),
              group="light", note="旧复盘契约"),

        # ── 数据中心 ──────────────────────────────────────────────────
        Route("/api/v1/data/status", lambda c: (ns["v1_data_status"](), 200),
              note="库级状态"),
        Route("/api/v1/data/coverage",
              lambda c: (ns["v1_coverage"](c["params"]), 200),
              group="heavy", cache=3,
              note="覆盖矩阵；完整约 7.6MB，加 ?agg=1 只返计数与分布（几 KB）"),
        Route("/api/v1/data/import-plan", lambda c: (ns["v1_import_plan"](), 200),
              note="导入计划"),
        Route("/api/v1/data/oi",
              lambda c: _dc_items(ns, "get_oi_items", c["params"]),
              note="OI 明细"),
        Route("/api/v1/data/funding",
              lambda c: _dc_items(ns, "get_funding_items", c["params"]),
              note="资金费明细"),
        Route("/api/v1/data/smart-money",
              lambda c: _dc_items(ns, "get_smart_money_items", c["params"]),
              note="聪明钱明细"),
        Route("/api/v1/data/all-in-one",
              lambda c: _dc_items(ns, "get_all_in_one_table", c["params"]),
              note="合并表"),
        Route("/api/v1/data/feeds",
              lambda c: _dc_feeds(ns),
              note="数据源状态"),
        Route("/api/v1/data/pool-scope",
              lambda c: (ns["metrics_api"].handle_pool_scope(c["params"]), 200)
              if ns.get("metrics_api") else
              (ns["missing"](f"指标模块不可用：{ns['_METRICS_IMPORT_ERROR']}", "not_connected"), 200),
              note="加密池分母（官方分类口径）"),
        Route("/api/v1/data/field-contract",
              lambda c: (ns["metrics_api"].handle_field_contract(c["params"]), 200)
              if ns.get("metrics_api") else
              (ns["missing"](f"指标模块不可用：{ns['_METRICS_IMPORT_ERROR']}", "not_connected"), 200),
              note="机器可读字段契约"),
        Route("/api/v1/data/cold-archive-status",
              lambda c: (ns["metrics_api"].handle_cold_archive_status(c["params"]), 200)
              if ns.get("metrics_api") else
              (ns["missing"](f"指标模块不可用：{ns['_METRICS_IMPORT_ERROR']}", "not_connected"), 200),
              group="light", cache=5,
              note="冷层索引摘要（只读 catalog/state，不扫 Parquet 内容；声明量≠完整性验收）"),
        Route("/api/v1/system-flow/paper-reconciliation",
              lambda c: (ns["metrics_api"].handle_paper_reconciliation_evidence(c["params"]), 200)
              if ns.get("metrics_api") else
              (ns["missing"](f"指标模块不可用：{ns['_METRICS_IMPORT_ERROR']}", "not_connected"), 200),
              group="light", cache=5,
              note="历史样本逐笔对账证据（缺失/陈旧/结构不符一律 unverified）"),
        Route("/api/v1/data/whitelist",
              lambda c: (ns["envelope"](
                  **(ns["datacenter"].get_backfill_and_whitelist_summary()
                     if ns.get("datacenter") else {})), 200),
              note="白名单与补数摘要"),
        Route("/api/v1/data/backfill",
              lambda c: (ns["envelope"](
                  **(ns["datacenter"].get_backfill_and_whitelist_summary()
                     if ns.get("datacenter") else {})), 200),
              note="同 whitelist"),

        # ── 单标的与其子资源 ──────────────────────────────────────────
        Route("/api/v1/markets/{exchange}/{symbol}",
              lambda c: (ns["v1_market"](c["exchange"], c["symbol"]), 200),
              note="单标的详情"),
        Route("/api/v1/markets/{exchange}/{symbol}/{tail}",
              lambda c: _market_tail(ns, c["exchange"], c["symbol"],
                                     c["tail"], c["params"]),
              group="heavy", note="candles / oi / funding / rule-snapshots / public-extras / metrics"),
    ]
    return Router(routes)
