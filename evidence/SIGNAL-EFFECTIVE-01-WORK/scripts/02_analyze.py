# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜只读取证与判据表生成。

输入：`baseline/` 下的一致性快照（**不碰生产库**）。
输出：
  out/effective_epoch.json            生效水位逐层来源与一致性
  out/uid_effect_ledger.csv           289 条逐 UID 影响清单
  out/rule_vs_wiring_matrix.csv       规则层 vs 实现层逐层判据
  out/authorized_factor_inventory.csv 新增共振因子/仓位倍数批准来源盘点
  baseline/uid_289.csv                逐 UID 原始字段（快照直出）

纪律：本脚本**只读**，不写任何生产库、不写账本。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
BASE = EV / "baseline"
OUT = EV / "out"
OUT.mkdir(parents=True, exist_ok=True)

LIVE = BASE / "live_signals_snapshot.sqlite3"
ARMS = ("3a", "3b", "3c", "3d")
LEDGER_TABLES = ("paper_pending", "paper_order", "paper_fill",
                 "paper_position", "paper_realized", "paper_processed")


def iso(ms) -> str | None:
    if ms is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))
    except (ValueError, OSError, TypeError):
        return None


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_line(rel: str, needle: str) -> int | None:
    """在源码里定位一行（取证用，返回行号，避免手抄漂移）。"""
    p = PROJ / rel
    try:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                return i
    except OSError:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. 生效水位逐层
# ─────────────────────────────────────────────────────────────────────────────
def build_epoch(con: sqlite3.Connection) -> dict:
    run = {k: (json.loads(v) if v else None) for k, v in
           con.execute("SELECT key, value_json FROM live_run")}
    live_start = int(run["live_start_ms"])
    paper_epoch = int(run["paper_epoch_ms"])
    state_anchor = int(run["state_anchor_ms"])
    floor = max(live_start, paper_epoch)
    now = int(time.time() * 1000)
    # 与 live_store 的常量逐字对撞（避免手抄）
    const_src = (PROJ / "框架/backend/live_store.py").read_text(encoding="utf-8")
    m = re.search(r"PAPER_EPOCH_MS\s*=\s*([\d_]+)", const_src)
    const_epoch = int(m.group(1).replace("_", "")) if m else None
    return {
        "generated_at_utc": iso(now),
        "layers": [
            {
                "name": "live_start_ms",
                "value_ms": live_start, "value_utc": iso(live_start),
                "persisted_at": "live_run.live_start_ms",
                "persisted_updated_at_utc": iso(run.get("live_start_ms_updated")),
                "written_by": "live_store.ensure_live_start_ms()（首次落定，不覆盖）",
                "definition_file": "框架/backend/live_store.py",
                "definition_line": find_line("框架/backend/live_store.py",
                                             "LIVE_START_KEY = "),
                "semantics": "展示/因果水位：进程首次启动时刻；早于它的信号只展示、不自动开仓",
                "role": "有效下限的**候选之一**（与 paper_epoch 取 max）",
            },
            {
                "name": "paper_epoch_ms",
                "value_ms": paper_epoch, "value_utc": iso(paper_epoch),
                "persisted_at": "live_run.paper_epoch_ms",
                "written_by": "live_store.ensure_paper_epoch_ms()（幂等，只允许不降）",
                "definition_file": "框架/backend/live_store.py",
                "definition_line": find_line("框架/backend/live_store.py",
                                             "PAPER_EPOCH_MS = "),
                "constant_in_code_ms": const_epoch,
                "constant_matches_persisted": const_epoch == paper_epoch,
                "semantics": "本轮模拟盘启用边界 = 2026-10-14 09:00 北京时间（用户明确指令）",
                "role": "有效下限的**候选之一**（与 live_start 取 max）",
            },
            {
                "name": "state_anchor_ms",
                "value_ms": state_anchor, "value_utc": iso(state_anchor),
                "persisted_at": "live_run.state_anchor_ms",
                "definition_file": "框架/backend/live_store.py",
                "definition_line": find_line("框架/backend/live_store.py",
                                             "STATE_ANCHOR_KEY = "),
                "semantics": ("状态重建的**绝对锚点**：重启与持续运行从同一时刻回放，"
                              "保证终态恒等；只落一次、不随后移"),
                "role": "**不是**开仓水位，不进 effective_floor；只决定重建起点",
                "note": ("⚠ 该值与 live_start 相差约 118 天，若被误当水位会造成大规模追补；"
                         "本单核对它不参与任何开仓判定"),
            },
            {
                "name": "effective_floor_ms",
                "value_ms": floor, "value_utc": iso(floor),
                "formula": "max(live_start_ms, paper_epoch_ms)",
                "definition_file": "框架/backend/live_store.py",
                "definition_line": find_line("框架/backend/live_store.py",
                                             "def effective_floor_ms"),
                "semantics": "自动开仓 / 挂单推进 / 当前信号 API 共用的**同一下限**",
                "derived_from": ["live_start_ms", "paper_epoch_ms"],
            },
        ],
        "call_chain": [
            {"file": "框架/backend/live_api.py",
             "line": find_line("框架/backend/live_api.py", "start_ms = L.get_live_start_ms()"),
             "what": "当前区 API：start_ms / epoch_ms / floor_ms 三者各自取值，floor 缺失即如实空态"},
            {"file": "框架/backend/unified_runtime.py",
             "line": find_line("框架/backend/unified_runtime.py",
                               "ensure_live_start_ms(self.started_at_ms)"),
             "what": "运行时启动时落定 live_start（幂等，不覆盖既有值）"},
            {"file": "框架/backend/unified_runtime.py",
             "line": find_line("框架/backend/unified_runtime.py", "out.append(\"before_paper_epoch_no_backfill\")"),
             "what": "bootstrap 历史候选的逐条不可执行原因（回放期候选不落单）"},
            {"file": "框架/backend/paper_engine.py",
             "line": find_line("框架/backend/paper_engine.py", "before_paper_epoch_no_backfill"),
             "what": "开仓 epoch 门禁①：信号确认 bar 真实右边界 < epoch ⇒ 终态拒单"},
            {"file": "框架/backend/paper_engine.py",
             "line": find_line("框架/backend/paper_engine.py", "bar_open < epoch"),
             "what": "开仓 epoch 门禁②：成交 bar 开盘 < epoch ⇒ 终态拒单（双保险）"},
        ],
        "consistency_findings": [
            {"id": "E-1", "level": "一致",
             "fact": ("effective_floor = max(2026-10-23T09:47:24Z, 2026-10-14T01:00:00Z) "
                      "= 2026-10-23T09:47:24Z；代码常量与库内持久值逐位相等"),
             "evidence": "constant_matches_persisted = true"},
            {"id": "E-2", "level": "一致",
             "fact": "四臂 / 挂单推进 / 自动开仓 / 当前区 API 共用 effective_floor，无第二套时钟",
             "evidence": "live_store.effective_floor_ms 为唯一实现，调用点见 call_chain"},
            {"id": "E-3", "level": "边界口径需注意",
             "fact": ("库里 close_time 存「闭区间右端 −1ms」，比较时必须用 +1 的真实右边界；"
                      "原始值不得改写"),
             "evidence": "live_store.signal_bar_right_ms 文档 + 实测 14 条边界样本"},
            {"id": "E-4", "level": "缺陷（本单要修）",
             "fact": ("`_scan_actionable` 扫可执行信号时**没有** epoch 条件，"
                      "epoch 只在 open_position 里挡 ⇒ 老信号会被扫进路由再被终态拒单"),
             "evidence": f"unified_runtime.py:{find_line('框架/backend/unified_runtime.py', 'def _scan_actionable')}"},
            {"id": "E-5", "level": "未验证",
             "fact": "策略版本/获准配置变化时是否开新 epoch —— 代码里没有自动开新边界的路径，只有人工落定",
             "evidence": "live_store.ensure_paper_epoch_ms 只落常量，无版本判定"},
        ],
        "definitions": {
            "结构形成时间": "4h 箱体/扫低/回踩的 K 线时刻，可以早于 epoch（预热允许）",
            "候选确认时刻 confirmed_at": "1h 确认 bar 收盘时刻（闭区间右端 −1ms）",
            "真实可得时刻 available_at": "该信号在现实中可被决策者取到的时刻，必须有可审计证据",
            "进程启动时刻": "started_at_ms / live_start_ms",
            "账户生效时刻": "paper_epoch_ms（用户授权的本轮启用边界）",
        },
        "restart_and_version_rules": {
            "正常重启": "沿用库内 live_start / paper_epoch / state_anchor，幂等恢复挂单与持仓，水位不倒退",
            "首次启用": "按现有持久化语义落定；已存在即不覆盖",
            "版本/配置变化": "代码无自动新边界路径 ⇒ 需人工落定；本单不改",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 逐 UID 影响清单 + 账本关联
# ─────────────────────────────────────────────────────────────────────────────
def load_ledger_index() -> tuple[dict, dict]:
    """返回 (uid -> [(arm, table, status, ts)], 账本摘要)。只读快照。"""
    idx: dict[str, list] = {}
    summary = {}
    for arm in ARMS:
        p = BASE / f"paper_{arm}_snapshot.sqlite3"
        if not p.exists():
            summary[arm] = {"status": "snapshot_missing"}
            continue
        con = sqlite3.connect(str(p))
        per = {}
        try:
            for t in LEDGER_TABLES:
                try:
                    rows = list(con.execute(
                        f"SELECT signal_uid FROM {t} WHERE signal_uid IS NOT NULL"))
                except sqlite3.OperationalError:
                    per[t] = None
                    continue
                per[t] = len(rows)
                for (uid,) in rows:
                    idx.setdefault(uid, []).append({"arm": arm, "table": t})
        finally:
            con.close()
        summary[arm] = per
    return idx, summary


def build_uid_ledger(con: sqlite3.Connection, floor: int, ledger_idx: dict) -> tuple[list, dict]:
    cols = ("signal_uid,symbol,strategy_key,strategy_version,config_hash,origin,source,"
            "executable_state,display_state,event_time,confirmed_at,available_at,"
            "bar_open_time,bar_close_time,first_seen_at,updated_at,payload_json")
    rows = list(con.execute(f"SELECT {cols} FROM live_signal ORDER BY confirmed_at"))
    out, tally = [], {}
    for r in rows:
        d = dict(zip(cols.split(","), r))
        p = json.loads(d["payload_json"] or "{}")
        hits = ledger_idx.get(d["signal_uid"], [])
        conf_ok = int(d["confirmed_at"]) + 1 >= floor
        # 逐层判定（**当前生产实际状态**，不是应有状态）
        L1 = d["origin"] in ("live", "recovered")
        L2 = d["executable_state"] == "enterable"
        L3 = (p.get("role") == "enterable_arm_entry")
        L4 = d["available_at"] is not None
        L5 = (p.get("arm") == "3a")          # 运行臂是 3a
        L6 = conf_ok
        if hits:
            klass = "有账本关联"
        elif L1 and L2 and L3 and L4 and L5 and L6:
            klass = "会进当前区且可被本臂路由"
        elif L1 and L2 and L3 and L4 and L6:
            klass = "进当前区、被臂号挡住（唯一屏障）"
        elif L1 and L2 and L3 and L4:
            klass = "身份被改但被 epoch 挡住"
        else:
            klass = "无订单影响"
        tally[klass] = tally.get(klass, 0) + 1
        out.append({
            "signal_uid": d["signal_uid"],
            "symbol": d["symbol"],
            "arm_in_payload": p.get("arm"),
            "strategy_key": d["strategy_key"],
            "strategy_version": d["strategy_version"],
            "source": d["source"],
            "origin_actual": d["origin"],
            "origin_in_payload": p.get("origin"),
            "executable_state_actual": d["executable_state"],
            "executable_state_in_payload": p.get("executable_state"),
            "role_in_payload": p.get("role"),
            "approved_in_payload": p.get("approved"),
            "config_kind": p.get("config_kind"),
            "event_time_utc": iso(d["event_time"]),
            "confirmed_at_utc": iso(d["confirmed_at"]),
            "available_at_utc": iso(d["available_at"]),
            "available_at_status_in_payload": (p.get("timestamps") or {}).get("available_at_status"),
            "available_at_equals_confirmed": (d["available_at"] == d["confirmed_at"]),
            "first_seen_at_utc": iso(d["first_seen_at"]),
            "epoch_ms": floor,
            "epoch_utc": iso(floor),
            "confirmed_at_plus1_ge_epoch": conf_ok,
            "L1_origin_whitelisted": L1,
            "L2_executable_enterable": L2,
            "L3_role_arm_entry": L3,
            "L4_available_at_present": L4,
            "L5_payload_arm_matches_runtime": L5,
            "L6_after_epoch": L6,
            "ledger_hits": (";".join(f"{h['arm']}:{h['table']}" for h in hits) or ""),
            "ledger_hit_n": len(hits),
            "impact_class": klass,
            "decision": ("保留原行 + 恢复研究身份（origin/executable_state/available_at/role）"
                         if not hits else "保留原行 + 逐条人工判定"),
            "reason": ("研究回测产物，配置 approved=false / kind=fixture；"
                       "available_at 无证据 ⇒ 不得以 confirmed_at 代替"),
            "idempotency_key": d["signal_uid"],
        })
    return out, tally


# ─────────────────────────────────────────────────────────────────────────────
# 3. 规则层 vs 实现层
# ─────────────────────────────────────────────────────────────────────────────
def build_matrix(con: sqlite3.Connection, floor: int, tally: dict) -> list:
    n = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    n_after = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE confirmed_at+1>=?", (floor,)).fetchone()[0]
    n_avail_eq_conf = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE available_at=confirmed_at").fetchone()[0]
    n_approved_false = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE json_extract(payload_json,'$.approved')=0").fetchone()[0]
    n_fixture = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE json_extract(payload_json,'$.config_kind')='fixture'"
    ).fetchone()[0]
    n_role_entry = con.execute(
        "SELECT COUNT(*) FROM live_signal "
        "WHERE json_extract(payload_json,'$.role')='enterable_arm_entry'").fetchone()[0]
    n_origin_recovered = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE origin='recovered'").fetchone()[0]

    R = []

    def add(layer, rule, rule_state, impl_state, verdict, count, sample, ref):
        R.append({"层": layer, "规则要求": rule, "规则本身状态": rule_state,
                  "实现/数据现状": impl_state, "判定": verdict,
                  "命中条数": count, "样例UID": sample, "代码或数据位置": ref})

    add("L0 总账", "研究回测产物不得进入当前交易区、不得登记订单",
        "已批准（研究/实盘必须分离）",
        f"{n} 条全部被改成可执行身份；其中 {n_after} 条在 epoch 之后",
        "**实现错误**（数据身份被改写，非规则问题）", n,
        "accum05_4a_XTZUSDT_1784177999999", "live_signal.origin / executable_state")
    add("L1 来源", "当前区只收 origin∈{live,recovered}",
        "已批准（SIGNAL-UI-LINK-07）",
        f"origin='recovered' 共 {n_origin_recovered} 条，全部是研究回测导入行；"
        "payload 内 origin 仍写 historical_research ⇒ 列与 payload 自相矛盾",
        "**实现错误**（列值被改写，来源语义失真）", n_origin_recovered,
        "accum05_4b_ZORAUSDT_1792821599999", "live_api.py 当前区 where 子句")
    add("L2 可执行态", "研究行 executable_state 必须为 historical_not_executable",
        "已批准（08 单契约）",
        f"executable_state='enterable' 共 {n} 条；但同表 executable_reason 原文写着"
        "「只展示、不可执行、不进入当前交易区」⇒ 同一行两个字段互相否定",
        "**实现错误**（自相矛盾）", n, "accum05_4c_MAVUSDT_1793005199999",
        "live_signal.executable_state vs executable_reason")
    add("L3 角色", "研究行 payload.role 必须为 research_only",
        "已批准（08 单三重隔离第三重）",
        f"payload.role='enterable_arm_entry' 共 {n_role_entry} 条（全部研究行）",
        "**实现错误**（第三重隔离被拆除）", n_role_entry,
        "accum05_4a_AVAUSDT_1792792799999", "research_signal_import.ROLE_RESEARCH")
    add("L4 可得时刻", "available_at 必须有可审计的真实可得证据；无证据保留 NULL/unproven",
        "已批准（本单第四节第 4 条）",
        f"available_at=confirmed_at 共 {n_avail_eq_conf} 条；payload.timestamps."
        "available_at_ms 仍为 null、status 仍为 unproven ⇒ 列与 payload 互相否定",
        "**实现错误**（伪造可得时刻以绕过 API 硬门禁）", n_avail_eq_conf,
        "accum05_4a_HYPERUSDT_1792771199999", "live_signal.available_at")
    add("L5 配置批准", "approved=false / kind=fixture 的配置不得用于任何执行行为",
        "已批准（06-02 待决清单：参数未批准即不得执行）",
        f"payload.approved=false 共 {n_approved_false} 条、config_kind='fixture' 共 {n_fixture} 条；"
        "**代码里没有任何一处读取 approved 字段**，只写在文本原因里",
        "**实现缺口**（规则未落到代码，只落到文案）", n_approved_false,
        "accum05_4b_TREEUSDT_1792760399999", "grep approved → 仅 live_api 展示与 import 写入")
    add("L6 臂号", "可执行扫描要求 payload.arm == 运行臂",
        "已批准（多臂隔离）",
        "payload.arm∈{4a,4b,4c}，运行臂=3a ⇒ 全部不匹配（**当前唯一有效屏障**）",
        "有效但**属数据不变量、不是闸门**（改一次臂号即失效）", n,
        "全部 289 条", "unified_runtime._scan_actionable L3")
    PAPER_EPOCH = 1_791_939_600_000
    n_ge_paper_epoch = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE confirmed_at+1>=?", (PAPER_EPOCH,)).fetchone()[0]
    n_gap = con.execute(
        "SELECT COUNT(*) FROM live_signal WHERE confirmed_at+1>=? AND confirmed_at+1<?",
        (PAPER_EPOCH, floor)).fetchone()[0]
    add("L7 生效边界", "确认时刻早于 epoch 的信号不得补单",
        "已批准（2026-10-14 09 契约 + live_store 的「共用同一有效下限」）",
        f"门禁① 实际比的是 `paper_epoch`（10-14），不是 `effective_floor`（10-23）"
        f"⇒ 有 {n_ge_paper_epoch} 条能穿过门禁①，其中 {n_gap} 条落在两者之间的"
        f" 9 天 8 小时窗口里、本应被挡（隔离实测：这些会登记成挂单）；"
        f"仅 {n_after} 条真正晚于有效下限",
        "**实现错接**（已改为 effective_floor，只会更严）", n_gap,
        "accum05_4a_XTZUSDT_1792087199999（2026-10-15T17:59:59Z，落在窗口内）",
        "paper_engine.py epoch 门禁① / live_store.effective_floor_ms")
    add("L8 OI 门槛", "enable_oi_filter 默认 True（已批准策略参数）",
        "已批准（EventTrendConfig 默认值）",
        "UnifiedRuntime.__init__ 把默认值强制改成 False（WYCKOFF_RESONANCE 缺省即关）"
        "⇒ 突破不再要求持仓量确认",
        "**未批准的策略改动**（规则被静默修改）", None,
        "—", "unified_runtime.py UnifiedRuntime.__init__")
    add("L9 仓位口径", "risk_budget_per_trade 固定 0.5%/笔（冻结参数）",
        "已批准（冻结）",
        "paper_engine.open_position 把 risk 乘以 payload.position_multiplier（0.5–1.3）；"
        "缺数据时算得 0.8 ⇒ 默认缩仓 20%",
        "**未批准的参数进入下单量**", None, "—", "paper_engine.py adj_risk_fraction")
    add("L10 幂等", "同一 UID 不重复生成/重复下单",
        "已批准",
        "live_signal 主键 + paper_processed/paper_pending/paper_position 三重去重",
        "**实现正确**", 0, "—", "unified_runtime.route_pending_orders")
    return R


# ─────────────────────────────────────────────────────────────────────────────
# 4. 新增因子批准来源盘点
# ─────────────────────────────────────────────────────────────────────────────
def build_factor_inventory() -> list:
    wfr = PROJ / "框架/backend/wyckoff_factor_resonance.py"
    src = wfr.read_text(encoding="utf-8") if wfr.exists() else ""
    def line(needle):
        for i, l in enumerate(src.splitlines(), 1):
            if needle in l:
                return i
        return None
    return [
        {"因子": "OI 持仓量（6h 增长率）", "分值区间": "-4 ~ 15",
         "阈值": "≥5%→15；≥2%→12；≥0→8；≥-3%→4；否则 -4；缺失→中性 6",
         "数据来源": "metrics_feed.read_metrics_tail(symbol, decision_ms, 3)",
         "当时可得性证据": "无（metrics_feed 是否 PIT 未验证；本单未取证）",
         "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "是（进 payload；经 position_multiplier 影响下单量）",
         "位置": f"wyckoff_factor_resonance.py:{line('oi_score = 6.0')}",
         "裁决": "待裁决；本单已使其不改变下单量"},
        {"因子": "聪明钱阶段 VWAP 成本", "分值区间": "-4 ~ 15",
         "阈值": "溢价 0~5%→15；5~12%→11；-3~0%→8；>12%→5；否则 -4；缺失→中性 6",
         "数据来源": "metrics_feed.phase_avg_price(symbol, decision_ms-42d, decision_ms)",
         "当时可得性证据": "无", "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "是（同上）",
         "位置": f"wyckoff_factor_resonance.py:{line('whale_score = 6.0')}",
         "裁决": "待裁决；本单已使其不改变下单量"},
        {"因子": "主动买卖量比 Taker", "分值区间": "-4 ~ 15",
         "阈值": "≥1.20→15；≥1.05→12；≥0.95→7；≥0.85→3；否则 -4；缺失→中性 6",
         "数据来源": "metrics_feed.latest_metrics(symbol, decision_ms)['主动买卖量比']",
         "当时可得性证据": "无", "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "是（同上）",
         "位置": f"wyckoff_factor_resonance.py:{line('taker_score = 6.0')}",
         "裁决": "待裁决；本单已使其不改变下单量"},
        {"因子": "大户/散户多空比", "分值区间": "-4 ~ 15",
         "阈值": "≥1.20→15；≥1.05→11；≥0.95→6；否则 -4；缺失→中性 6",
         "数据来源": "metrics_feed.latest_metrics()['多空比']['top_position_ls_ratio']",
         "当时可得性证据": "无", "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "是（同上）",
         "位置": f"wyckoff_factor_resonance.py:{line('ls_score = 6.0')}",
         "裁决": "待裁决；本单已使其不改变下单量"},
        {"因子": "基础形态分 base_score", "分值区间": "固定 40",
         "阈值": "固定常量（无参数化）", "数据来源": "硬编码",
         "当时可得性证据": "不适用", "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "是（决定总分落档）",
         "位置": f"wyckoff_factor_resonance.py:{line('base_score = 40.0')}",
         "裁决": "待裁决"},
        {"因子": "共振分档位", "分值区间": "0~100",
         "阈值": "≥80 强共振；≥65 健康；≥50 基础；<50 偏弱",
         "数据来源": "五项求和后截断", "当时可得性证据": "不适用",
         "批准来源": "**未找到**", "批准版本": "—",
         "是否影响本次执行": "间接（决定倍数档）",
         "位置": f"wyckoff_factor_resonance.py:{line('if total_score >= 80.0')}",
         "裁决": "待裁决"},
        {"因子": "仓位倍数 position_multiplier", "分值区间": "0.5 / 0.8 / 1.0 / 1.3",
         "阈值": "按总分四档；**四项全缺失时总分 64 ⇒ 0.8x**",
         "数据来源": "同上四项之和 + 40 基础分",
         "当时可得性证据": "不适用（但缺数据仍给 0.8x，等于无依据缩仓）",
         "批准来源": "**未找到**（用户仅要求「所有已采集数据参与分析」，"
                     "不等于批准权重/阈值/动态仓位系数）", "批准版本": "—",
         "是否影响本次执行": "**是**：paper_engine 直接乘 risk_per_trade",
         "位置": "paper_engine.py:1211 adj_risk_fraction",
         "裁决": "待裁决；本单已改为「无显式批准标记则恒等于 1.0」"},
    ]


def write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    if not LIVE.exists():
        raise SystemExit("缺基线快照，请先跑 01_baseline_snapshot.py")
    con = sqlite3.connect(str(LIVE))
    con.row_factory = sqlite3.Row

    epoch = build_epoch(con)
    (OUT / "effective_epoch.json").write_text(
        json.dumps(epoch, ensure_ascii=False, indent=2), encoding="utf-8")
    floor = [x for x in epoch["layers"] if x["name"] == "effective_floor_ms"][0]["value_ms"]

    ledger_idx, ledger_summary = load_ledger_index()
    uid_rows, tally = build_uid_ledger(con, floor, ledger_idx)
    write_csv(OUT / "uid_effect_ledger.csv", uid_rows)
    write_csv(BASE / "uid_289.csv", uid_rows)

    matrix = build_matrix(con, floor, tally)
    write_csv(OUT / "rule_vs_wiring_matrix.csv", matrix)

    factors = build_factor_inventory()
    write_csv(OUT / "authorized_factor_inventory.csv", factors)

    # 账本关联汇总（逐臂逐表）
    ledger_report = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "source": "baseline/paper_*.sqlite3 一致性快照",
        "per_arm_table_rows": ledger_summary,
        "uids_with_any_ledger_hit": len([u for u, h in ledger_idx.items() if h]),
        "hits_detail": {u: h for u, h in ledger_idx.items() if h},
        "conclusion": ("289 条目标 UID 在四本账本（pending/order/fill/position/realized/"
                       "processed）中的关联条数 = "
                       f"{len([u for u in ledger_idx if u.startswith('accum05_')])}"),
    }
    (BASE / "ledger_linkage.json").write_text(
        json.dumps(ledger_report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print(f"有效下限 = {iso(floor)}")
    print(f"289 条影响分类：{json.dumps(tally, ensure_ascii=False)}")
    print(f"账本关联（accum05_*）："
          f"{len([u for u in ledger_idx if u.startswith('accum05_')])} 条")
    print("\n规则层判定：")
    for r in matrix:
        print(f"  {r['层']:12s} {r['判定']}")
    print(f"\n产物目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
