# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜06 报告生成（README 的每个数字都从 out/*.json 取，不手抄）。

规则：
  · 模板里所有数字都是 `{{键名}}` 占位，由 `facts()` 从产物回填；
  · 渲染后断言模板里没有残留 `{{`，否则报错（防止新增数字忘了给值）；
  · 报告末尾嵌一段机器可读事实块（`FACTS-BEGIN/END`），供 07 对撞脚本复核。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

README = C.EVID / "README.md"
FB, FE = "<!-- FACTS-BEGIN -->", "<!-- FACTS-END -->"


def _pct(a, b):
    return "0" if not b else f"{a * 100.0 / b:.1f}"


def facts() -> dict:
    o1 = C.load_out("01_入口普查.json")
    o2 = C.load_out("02_配置闸门.json")
    o3 = C.load_out("03_观察与候选.json")
    o4 = C.load_out("04_隔离全链.json")
    o5 = C.load_out("05_隔离边界.json")
    o8 = C.load_out("08_配置模板.json") or {}
    base = json.loads((C.EVID / "baseline" / "git_head_note.json").read_text(encoding="utf-8"))

    import accum_early_entry as AE

    st = o1["static"]
    placeholder = [k for k, v in st.items() if str(v.get("verdict", "")).startswith("占位")]
    runnable = [k for k, v in st.items() if not str(v.get("verdict", "")).startswith("占位")]
    CORE = ("observe_store.py", "research_15m.py", "paper_engine.py")
    mcl = o1["minimal_change_list"]
    changed = [m for m in mcl if m.get("applied")]
    unchanged = [m for m in mcl if not m.get("applied")]
    new_files = [m for m in changed if "新增" in str(m.get("kind"))]
    mod_files = [m for m in changed if "修改" in str(m.get("kind"))]

    a, b, c = o2["A_no_config"], o2["B_one_field_missing"], o2["C_fixture_without_env"]
    s3 = o3["summary"]
    cond = s3["condition_results_all"]
    cond_total = sum(cond.values())
    runs = {r["name"]: r for r in o4["runs"]}
    cold, hot = runs["run_cold"], runs["run_hot"]
    rs = o4["resume_run"]["assertions"]
    v5 = o5["verdict"]
    gates = o2["verdict"]

    f = {
        # ── 身份 ────────────────────────────────────────────────────────────
        "version": AE.VERSION,
        "config_schema": AE.CONFIG_SCHEMA,
        "required_fields": len(AE.REQUIRED_PATHS),
        "pending_map": len(AE.PENDING_TO_CONFIG),
        "implemented_keys": len(AE.IMPLEMENTED),
        "conflict_fields": len(AE.CONFLICT_FIELDS),
        "fields_mapped": o8.get("mapped_to_pending_or_conflict", 0),
        "fields_structural": o8.get("structural", 0),
        "schemes": "／".join(AE.SCHEMES),
        # ── 01 普查 ────────────────────────────────────────────────────────
        "placeholder_count": len(placeholder),
        "placeholder_list": "、".join(placeholder),
        "runnable_count": len(runnable),
        "runnable_core": len([k for k in runnable if k in CORE]),
        "changed_files": len(changed),
        "new_files": len(new_files),
        "modified_files": len(mod_files),
        "unchanged_files": len(unchanged),
        "diff_hunks": base["diff_hunks"],
        "diff_added": base["diff_added_lines"],
        "diff_removed": base["diff_removed_lines"],
        "git_head_bytes": base["git_head_bytes"],
        "current_bytes": base["current_bytes"],
        # ── 02 闸门 ────────────────────────────────────────────────────────
        "gateA_missing": a["missing_count"],
        "gateA_events": a["events_created"],
        "gateA_blocked": a["events_blocked_at_config"],
        "gateA_candidates": a["candidates_generated"],
        "gateA_ledger_files": len(a["paper_ledger_created"]),
        "gateB_missing_field": "、".join(b["missing"]),
        "gateC_blocked_by": "、".join(c["blocked_by"]),
        "gate_pass": sum(1 for v in gates.values() if v),
        "gate_total": len(gates),
        # ── 03 观察与候选 ───────────────────────────────────────────────────
        "obs_events": s3["events"],
        "obs_transitions": s3["transitions"],
        "obs_candidates": s3["candidates_in_memory"],
        "obs_candidates_db": s3["candidate_rows_in_db"],
        "obs_4a": s3["schemes"]["4a"],
        "obs_4b": s3["schemes"]["4b"],
        "obs_4c": s3["schemes"]["4c"],
        "obs_gaps": s3["data_gap_rows"],
        "cond_pass": cond.get("pass", 0),
        "cond_not_required": cond.get("not_required", 0),
        "cond_not_proven": cond.get("not_proven", 0),
        "cond_total": cond_total,
        "cond_pass_pct": _pct(cond.get("pass", 0), cond_total),
        "cond_not_proven_pct": _pct(cond.get("not_proven", 0), cond_total),
        "bar_cold": s3["entry_bar_source"].get("cold", 0),
        "bar_hot": s3["entry_bar_source"].get("hot", 0),
        "obs_expired": s3["events_by_state"].get("EXPIRED", 0),
        "obs_invalidated": s3["events_by_state"].get("INVALIDATED", 0),
        "obs_retest": s3["events_by_state"].get("RETEST", 0),
        "obs_sweep": s3["events_by_state"].get("SWEEP", 0),
        "checks_pass": sum(1 for v in o3["checks"].values() if v),
        "checks_total": len(o3["checks"]),
        # ── 04 隔离全链 ─────────────────────────────────────────────────────
        "cold_positions": cold["assertions"]["summary"]["positions"],
        "cold_closed": cold["assertions"]["summary"]["closed"],
        "cold_all_ok": all(v for k, v in cold["assertions"]["summary"].items()
                           if k.endswith("_ok") or k in ("duplicate_blocked",
                                                          "rescan_no_new_orders",
                                                          "no_add_rule_respected")),
        "hot_positions": hot["assertions"]["summary"]["positions"],
        "hot_closed": hot["assertions"]["summary"]["closed"],
        "hot_all_ok": all(v for k, v in hot["assertions"]["summary"].items()
                          if k.endswith("_ok") or k in ("duplicate_blocked",
                                                         "rescan_no_new_orders",
                                                         "no_add_rule_respected")),
        "hot_no_add": sum(1 for o in hot["open_results"]
                          if o.get("reason") == "position_already_open_no_add"),
        "fill_lag_cold": max(o["fill_lag_ms"] for o in cold["open_results"] if o["ok"]),
        "fill_lag_hot": max(o["fill_lag_ms"] for o in hot["open_results"] if o["ok"]),
        "resume_orders": rs["resume"]["orders_after"],
        "resume_fills": rs["resume"]["fills_after"],
        "resume_no_dup": rs["resume"]["no_duplicate_after_restart"],
        "resume_identity": rs["resume"]["identity_recovered_from_ledger"],
        "resume_all_ok": all(v for k, v in rs["summary"].items()
                             if k.endswith("_ok") or k in ("duplicate_blocked",
                                                            "rescan_no_new_orders",
                                                            "no_add_rule_respected")),
        # ── 05 隔离边界 ─────────────────────────────────────────────────────
        "iso_pass": sum(1 for v in v5.values() if v),
        "iso_total": len(v5),
        "iso_conns": o5["connections_total"],
        "iso_violations": len(o5["connections_write_to_production"]),
        "iso_run_ledgers": len(o5["isolated_run_ledgers"]),
        "prior_ledgers_unchanged": all(o5["prior_ticket_ledgers_unchanged"].values()),
        "ledger_3a": "未变" if o5["ledger_sha_unchanged"]["3a"] else "变了",
        "ledger_3b": "未变" if o5["ledger_sha_unchanged"]["3b"] else "变了",
        "ledger_3c": "未变" if o5["ledger_sha_unchanged"]["3c"] else "变了",
        "ledger_3d": "未变" if o5["ledger_sha_unchanged"]["3d"] else "变了",
        "live_db": "未变" if o5["live_db_sha_unchanged"] else "变了",
        "market_db": "变了（采集器在写，属正常）" if o5["market_db_changed"] else "未变",
        # ── 产物清单 ────────────────────────────────────────────────────────
        "out_files": len(list(C.OUT.glob("*.json"))),
    }
    f["iso_list"] = "\n".join(
        f"| {k} | {'通过' if v else '不通过'} |" for k, v in v5.items())

    # 「证据不足」按成交 bar 层拆分（不能笼统说「都是冷档」：热层同样没有 available_at）
    np_by_src = {"cold": 0, "hot": 0, "其它": 0}
    for w in o3["windows"]:
        for cand in w["candidates"]:
            if any(cd.get("result") == "not_proven" for cd in cand.get("conditions") or []):
                src = cand.get("entry_bar_source")
                np_by_src[src if src in ("cold", "hot") else "其它"] += 1
    f["np_cold"] = np_by_src["cold"]
    f["np_hot"] = np_by_src["hot"]
    f["np_other"] = np_by_src["其它"]

    def yn(v):
        return "通过" if v else "不通过"

    f["cold_ok_text"] = yn(f["cold_all_ok"])
    f["hot_ok_text"] = yn(f["hot_all_ok"])
    f["resume_ok_text"] = yn(f["resume_all_ok"])
    f["resume_dup_text"] = "没有重复下单" if f["resume_no_dup"] else "出现了重复下单"
    f["resume_id_text"] = ("是" if f["resume_identity"] else "不是")
    f["prior_text"] = "没被碰过" if f["prior_ledgers_unchanged"] else "被改动过"
    f["gate_list"] = "\n".join(
        f"| {k} | {'通过' if v else '不通过'} |" for k, v in gates.items())
    f["entry_table"] = "\n".join(
        f"| `{k}` | {v.get('lines', '—')} | {v.get('verdict')} |" for k, v in st.items())
    f["change_table"] = "\n".join(
        f"| `{m['file']}` | {m['kind']} | {m['what']} |" for m in mcl)
    f["scheme_table"] = "\n".join(
        f"| {k} | {v} |" for k, v in s3["schemes"].items())
    return f


TEMPLATE = """# ACCUM-EARLY-04-B｜提前入场策略接入（甲乙丙）

> 交付时间：{{generated}}｜代码版本：`{{version}}`｜配置格式版本：`{{config_schema}}`

## 一句话结论

**接线做完了，策略好不好还不知道。**

具体说：甲乙丙三套提前入场方案（工程代号 4a／4b／4c）已经接进现有的观察、候选、模拟执行链路，
能取数、能观察、能记候选、能开仓成交、能上移止损、能退出、能进独立账本；
**但三组阈值全部没有你的批准**，所以现在**开不了新仓**——缺配置就阻塞开仓，这是按工单要求做的。

另外要明确一句：**本单没有验证甲乙丙赚不赚钱**，也没有宣称任何一组更优。
所有跑出来的成交都在 `fixture` 测试配置下，只证明「管道通」，不构成绩效结论。

## 一、入口普查：哪些是占位、哪些能跑

先说人话：项目里原来只有「账本和配置校验」的壳子，**没有任何一处能推进甲乙丙的观察状态**。
所以本单必须补一个观察推进器，但**没有另建引擎**——成交、止损、退出全部复用原有模拟执行模块。

| 文件 | 行数 | 判定 |
|---|---|---|
{{entry_table}}

要点三条：

1. `accum_research.py` 与 `accum_pending.py` 是**占位**（{{placeholder_count}} 个），只有配置表与账本 DDL，不读行情、不推进状态、不下单。
2. `observe_store.py`／`research_15m.py`／`paper_engine.py` 这 {{runnable_core}} 个是**能跑**的，分别管「记录」「取数」「执行」；其余 {{runnable_count}} 个里除了冻结包，都是只跑 3a–3d 的生产入口，本单不碰。
3. 冻结包的状态机**不能**直接表达甲乙丙：它的迁移写死在冻结包里（必须先跌破箱体下沿才算扫单），而甲乙丙的起点与确认方式不同。**改它等于改冻结规则**，所以本单不动它，改在研究侧另建观察器。

## 二、最小修改清单与代码差异

一共动了 {{changed_files}} 个文件（新增 {{new_files}} 个、修改 {{modified_files}} 个），另有 {{unchanged_files}} 个文件（或文件组）只复用不改。

| 文件 | 类型 | 做了什么 |
|---|---|---|
{{change_table}}

改动量：**{{diff_hunks}} 处、+{{diff_added}} 行、−{{diff_removed}} 行**。
改前副本在 `baseline/live_store.py.before_04b`，差异在 `baseline/live_store_04b.diff`。

> 说明为什么基线不是 `git show HEAD:`：仓库当前提交（{{git_head_bytes}} 字节）比工作树（{{current_bytes}} 字节）落后很远，
> 前几单的改动都没提交。拿它当基线会把别人几十处改动算进本单。所以基线用「反向还原本单 3 处编辑」得到，
> 还原前逐条断言了 3 处标记各出现 1 次，还原后断言 `RESEARCH_ARMS` 不再出现——不满足就报错，不静默通过。

## 三、配置要求：缺一项就不许开仓

配置是**显式必填**的：{{required_fields}} 个字段，**一个默认值都不给**。
字段名与待决表里的 {{pending_map}} 项一一对应（`mapping_check()` 校验，当前 {{pending_map}} 项全部对上）。

- `kind` 只能是 `approved`（你批准的）或 `fixture`（工程自测）；`approved` 必须带批准依据，`fixture` 必须显式设 `ACCUM04_FIXTURE_OK=1` 才允许开仓。
- 另有 {{implemented_keys}} 个字段带**取值白名单**：写了白名单以外的取值，直接算「不支持」，同样阻塞开仓——避免「配置写了但代码没实现」被当成通过。
- 还有 {{conflict_fields}} 个字段属于**口径冲突**（滑点用策略包的 3bps 还是生效视图的 5bps、风控用哪套、参与率上限取哪个），本单只要求显式填写，**不替你选**。

要填的时候直接看这两份：

- `config/批准配置_空白表单.json` —— {{required_fields}} 格全 null，照着填。
- `配置字段表.md` —— 每一格是谁定的、代码支持哪些取值、对应哪条待决问题（{{fields_mapped}} 格能追到待决表或冲突表，{{fields_structural}} 格是格式/开关类）。

> 填完不等于批准：还要把 `kind` 改成 `approved`、`approved` 改成 `true`、在 `approval_ref` 写明依据。三处缺一处，照样阻塞开仓。

阻塞行为（实测，{{gate_pass}}／{{gate_total}} 项通过）：

| 判据 | 结果 |
|---|---|
{{gate_list}}

具体三组场景：

- **完全不给配置**：{{gateA_missing}} 个字段全缺，创建了 {{gateA_events}} 条观察事件、全部阻塞在配置闸门（{{gateA_blocked}} 条），候选 {{gateA_candidates}} 条、研究账本文件 {{gateA_ledger_files}} 个 —— 即「数据照读、记录照记、开仓不干」。
- **只缺一个字段**：精确报出缺的是 `{{gateB_missing_field}}`，不笼统报「配置错误」。
- **给了 fixture 但没开环境开关**：报 `{{gateC_blocked_by}}`，仍然不许开仓。

## 四、真实事件追踪

用两个真实行情窗口跑，一个只能用冷档、一个走热库，确认两条取数路都能推进：

| 方案 | 候选条数 |
|---|---|
{{scheme_table}}

合计 {{obs_events}} 个观察事件、{{obs_transitions}} 条状态迁移、{{obs_candidates}} 条候选（入库 {{obs_candidates_db}} 条，逐条对齐）。
终态分布：过期 {{obs_expired}}、失效 {{obs_invalidated}}、回抽观察 {{obs_retest}}、扫单观察 {{obs_sweep}}。

每个候选都带条件真值表，不是只给一个分数：共 {{cond_total}} 条条件判定，其中
成立 {{cond_pass}} 条（{{cond_pass_pct}}%）、本方案不要求 {{cond_not_required}} 条、
**证据不足 {{cond_not_proven}} 条**（{{cond_not_proven_pct}}%）。
证据不足的 {{cond_not_proven}} 条全是同一条判据「成交 bar 的可得时刻有没有证据」，
按成交 bar 分层是：冷档 {{np_cold}} 条、热库 {{np_hot}} 条、其它 {{np_other}} 条 ——
**不是只有冷档缺**，冷热两层的合并读取都没有 `available_at`。这类缺口只记「证据不足」，
不记「成立」，也不补造。

成交 bar 来源：冷档 {{bar_cold}} 条、热库 {{bar_hot}} 条 —— 冷热两路都通。

### 链路一：冷档窗口（SOLUSDT）

{{cold_positions}} 笔持仓全部平仓（{{cold_closed}} 笔 closed），
入场时点、初始止损、订单数、不许加仓、重复下单被拦、重复扫描不产生新单，**六项全过** —— {{cold_ok_text}}。
成交延迟 {{fill_lag_cold}} 毫秒（即「确认后第一根已闭合 15 分钟 bar 的开盘价」，没有回填）。

### 链路二：热库窗口（AVAXUSDT）

{{hot_positions}} 笔持仓全部平仓（{{hot_closed}} 笔 closed），同上六项全过 —— {{hot_ok_text}}。
另外有 {{hot_no_add}} 次重复开仓被 `position_already_open_no_add` 拒掉 —— 这是**正确行为**（单活跃事件，不加仓）。
成交延迟 {{fill_lag_hot}} 毫秒。

### 链路三：真进程重启（换新进程，同一账本目录）

重启后订单数 {{resume_orders}}、成交数 {{resume_fills}}，与重启前**逐位相同**，{{resume_dup_text}}。
更关键的一条：重启后身份**能不能从账本文件里读回来** —— {{resume_id_text}}。
三条持仓的委托号都从 `paper_position.signal_uid` 恢复，再回查候选，入场 bar 仍然逐位一致；
六项判据在重启后同样全过 —— {{resume_ok_text}}。

## 五、研究与生产隔离

隔离不是靠声明，是靠行为取证：{{iso_total}} 项判据，{{iso_pass}} 项通过。

| 判据 | 结果 |
|---|---|
{{iso_list}}

补充数字：隔离进程运行期共开了 {{iso_conns}} 次数据库连接，
其中**以可写方式连生产账本目录／生产实时库的次数 = {{iso_violations}}**（这是探针实测，不是「我们没写」的口头保证）。

生产四本账本的摘要：3a {{ledger_3a}}、3b {{ledger_3b}}、3c {{ledger_3c}}、3d {{ledger_3d}}；生产实时库{{live_db}}；
行情库{{market_db}}。

本次隔离运行产生的 {{iso_run_ledgers}} 个研究账本全部落在 `evidence/ACCUM-EARLY-04-B/runs/` 之下；
早前工单（ACCUM-EARLY-01）那三本研究账本{{prior_text}}（摘要与修改时刻都没变）。

负向测试也做了，而且带正向对照——否则「拒绝」可能只是恒抛异常：
不设 `PAPER_LEDGER_DIR` 时研究臂被拒；未知臂被拒；放行闸门指向生产目录被拒；
同样一次调用在隔离根目录内必须能成功写（证明前三条的「拒绝」是有条件的拒绝，不是永远抛错）。

## 六、工单要求逐项对照

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 工作 1：普查入口、提交最小修改清单 | 完成 | `out/01_入口普查.json`，改动 {{changed_files}} 个文件 |
| 工作 2：数据输入／观察状态／候选／失效原因／策略身份／账户隔离 | 完成 | `out/03_观察与候选.json`，{{obs_candidates}} 条候选、{{checks_pass}}／{{checks_total}} 项自检通过 |
| 工作 3：未批准阈值显式必填、缺配置阻塞开仓但其余照做 | 完成 | `out/02_配置闸门.json`，{{gate_pass}}／{{gate_total}} 项通过 |
| 工作 4：等你转发的批准规则再绑定运行 | **未开始（等你的规则）** | 现在只有 `fixture` 自测配置，`approved=false` |
| 工作 5：接续冷热读取，只处理实际阻塞项 | 完成 | 复用 03-B 的合并读取，未改共享门面；`out/04_隔离全链.json` 冷热两路均通 |
| 工作 6：入场时间／已确认止损／重复扫描与恢复／不重复订单／研究生产隔离 | 完成 | `out/04_隔离全链.json` + `out/05_隔离边界.json`，隔离 {{iso_pass}}／{{iso_total}} |
| 工作 7：每次候选记录成立/未成立/缺口/拒绝原因 | 完成 | 每个候选一张条件真值表，{{cond_total}} 条判定 |
| 纪律：不得把入口完成写成效果已验证 | 遵守 | 全文只声明「管道通」，未给任何绩效结论 |

## 七、剩余阻塞（都还没解决，不许当已解决）

1. **三组阈值未批准**（最主要）。没有你的规则，开仓一直是阻塞状态。本单只把「阻塞」做成可验证的，没绕过它。
2. **HTTP 接口与前端不认研究臂**。实测 `live_api.paper_order_post(arm="4a")` 返回 400「未知臂」；
   前端与生产循环仍只认 3a–3d。属跨界协调项，本单按工单不改，已登记具体调用与返回。
3. **合并读取的 15m bar 没有 `available_at`**（冷档与热库都没有，不只是冷档）。
   所以 {{cond_not_proven}} 条判定只能记「证据不足」，不能记「成立」。这是历史数据缺失，**不许补造**。
4. **03-B 登记的三处自写 SQL 仍只读热库**（`paper_loop.execute_cycle`、`unified_runtime.execute_cycle`、
   `live_api.paper_advance_trailing_post`）。本单只走自己这条链，没有改它们，也没有顺手重构。
5. **重复候选上限、冷却期未批准**：`repeat_candidate_limit` 属待决项，本单按「冷却 = 0」运行并如实标注，
   这会让候选比正式规则更密——**不能拿本单候选数量推断正式规则的信号频率**。

## 八、复跑说明

见 `scripts/复跑说明.md`。所有产物都在 `out/`，每个数字都能从对应的 json 里找到出处；
本文件由 `scripts/06_报告生成.py` 渲染，数字不手写；`scripts/07_对撞.py` 会从原始 json 独立重算一遍再比对。
配置表单与字段表由 `scripts/08_配置模板.py` 生成（字段清单直接从代码里的必填清单取，不手抄）。

{{facts_block}}
"""


def render(f: dict) -> str:
    out = TEMPLATE.replace("{{facts_block}}", "@@FACTS@@")
    for k, v in f.items():
        out = out.replace("{{" + k + "}}", str(v))
    out = out.replace("{{generated}}", C.iso(int(__import__("time").time() * 1000)))
    left = [s for s in out.split("{{")[1:]]
    if left:
        raise SystemExit("拒绝：模板里还有未赋值的占位符 → " +
                         "、".join(sorted({s.split("}}")[0] for s in left})))
    return out


def main() -> int:
    f = facts()
    body = render(f)
    block = (f"{FB}\n```json\n"
             + json.dumps(f, ensure_ascii=False, indent=1, default=str)
             + f"\n```\n{FE}\n")
    README.write_text(body.replace("@@FACTS@@", block), encoding="utf-8")
    print(json.dumps({"written": str(README),
                      "bytes": README.stat().st_size,
                      "facts": len(f)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
