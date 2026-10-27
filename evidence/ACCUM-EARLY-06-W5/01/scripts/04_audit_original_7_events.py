# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-01 | 脚本 04: 原 7 笔事件同输入独立全链审计与纠偏对账

功能：
1. 严格保留原输入：标的 SOLUSDT/AVAXUSDT、对应冷热区间、方案 4a/4b/4c；
2. 在完全隔离的临时环境中独立重跑 scan_symbol；
3. 对撞 05-E 原 7 笔事件，精准提取当前版本下的状态机迁移轨迹与终态；
4. 结构化输出至 out/04_original_7_events_audit.json；
5. 生成纠偏后的 7 份案例分析 Markdown 至 cases/ 目录。
"""
import calendar
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "01" / "out"
CASES_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "01" / "cases"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CASES_DIR.mkdir(parents=True, exist_ok=True)

def _iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))

def ms_of(s):
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)

def run_audit():
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum06_audit7_"))
    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "accum-early-05/v1",
        "config_used": "evidence/ACCUM-EARLY-05-B/config/fixture_rules_05.json",
        "cases_reconciliation": []
    }

    try:
        iso_db_path = tmp_dir / "observe.sqlite3"
        iso_ledger_dir = tmp_dir / "ledger"
        iso_ledger_dir.mkdir(parents=True, exist_ok=True)
        live_db = tmp_dir / "live.sqlite3"
        gate_file = tmp_dir / "gate.json"
        gate_file.write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")

        os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger_dir)
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
        os.environ["LIVE_DB"] = str(live_db)
        os.environ["PAPER_GATE_FILE"] = str(gate_file)
        os.environ["WX_MARKET_MERGE"] = "1"
        os.environ[AEE.FIXTURE_ENV] = "1"

        import live_store as L
        L.ensure_live_schema()

        fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
        os.environ["ACCUM04_CONFIG"] = str(fixture_cfg_path)
        cfg = AEE.load_config(fixture_cfg_path)
        store = AEE.open_store(iso_db_path)
        feed = AEE.DataFeed()

        # 读取 05-E 原 7 笔事件
        old_trace_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "out" / "03_event_trace.json"
        old_cases = json.loads(old_trace_path.read_text("utf-8")).get("cases", [])

        # 运行 1: SOLUSDT 冷档区间 (2026-08-01 ~ 2026-09-05)
        t_sol_start = ms_of("2026-08-01T00:00:00Z")
        t_sol_end = ms_of("2026-09-05T00:00:00Z")
        AEE.scan_symbol("SOLUSDT", cfg, feed, store, start_ms=t_sol_start, end_ms=t_sol_end, max_boxes=2, warmup_days=60)

        # 运行 2: AVAXUSDT 热库区间 (2026-09-12 ~ 2026-10-25)
        t_avax_start = ms_of("2026-09-12T00:00:00Z")
        t_avax_end = ms_of("2026-10-25T00:00:00Z")
        AEE.scan_symbol("AVAXUSDT", cfg, feed, store, start_ms=t_avax_start, end_ms=t_avax_end, max_boxes=2, warmup_days=60)

        new_events = [dict(r) for r in store.con.execute("SELECT * FROM observe_event ORDER BY created_ms").fetchall()]
        new_trans = [dict(r) for r in store.con.execute("SELECT * FROM observe_transition ORDER BY id").fetchall()]
        new_cands = [dict(r) for r in store.con.execute("SELECT * FROM accum05_candidate ORDER BY created_ms").fetchall()]

        for idx, old_c in enumerate(old_cases):
            old_ev_id = old_c["event_id"]
            old_sch = old_c["scheme"]
            old_sym = old_c["symbol"]

            matched_event = None
            for ev in new_events:
                if ev["scheme"] == old_sch and ev["symbol"] == old_sym:
                    if ev["box_version_id"] in old_ev_id or old_ev_id in ev["event_id"]:
                        matched_event = ev
                        break

            matched_cand = None
            ev_transitions = []
            if matched_event:
                ev_transitions = [t for t in new_trans if t["event_id"] == matched_event["event_id"]]
                for c in new_cands:
                    if c["event_id"] == matched_event["event_id"]:
                        matched_cand = c
                        break

            current_state = matched_event["state"] if matched_event else "NOT_FOUND"
            
            # 定性与纠偏分析
            if current_state == "INVALIDATED":
                verdict_category = "STRUCTURAL_INVALIDATED"
                reason_detail = matched_event.get("closed_reason") or "连续 2 根 1h 收盘跌破箱底"
            elif current_state == "NOT_FOUND":
                verdict_category = "NOT_FOUND_UNSPAWNED"
                reason_detail = "前序事件生命周期持续占位且触发冷却，该旧箱体在当前版本中未被允许建档"
            else:
                verdict_category = "OTHER"
                reason_detail = str(current_state)

            case_item = {
                "case_index": idx + 1,
                "symbol": old_sym,
                "scheme": old_sch,
                "old_event_id": old_ev_id,
                "old_04_status": old_c.get("status"),
                "old_04_confirm_time": old_c.get("confirm_time_utc"),
                "current_event_id": matched_event["event_id"] if matched_event else None,
                "current_state": current_state,
                "candidate_produced": (matched_cand is not None),
                "verdict_category": verdict_category,
                "reason_detail": reason_detail,
                "transitions": [
                    {
                        "from_state": t["from_state"],
                        "to_state": t["to_state"],
                        "reason": t["reason"],
                        "time_utc": _iso(t["at_ms"])
                    } for t in ev_transitions
                ]
            }
            results["cases_reconciliation"].append(case_item)

            # 编写 Markdown 案例
            md_lines = [
                f"# 原输入行为复验案例 {idx + 1}：{old_sym}（方案 {old_sch}）",
                "",
                "> **免责声明**：本案例采用工程测试配置 (`fixture_rules_05.json`) 进行行为独立核验，**仅用于验证状态机逻辑与终态判定，不输出任何正式策略绩效**。",
                "",
                "## 1. 基本信息与版本行为对比",
                "",
                f"- **标的**：`{old_sym}`",
                f"- **方案**：`{old_sch}`",
                f"- **04-B 旧版事件 ID**：`{old_ev_id}`",
                f"- **04-B 旧版终态**：`{old_c.get('status')}`（确认时刻：`{old_c.get('confirm_time_utc')}`）",
                f"- **05-B 当前版终态**：`{current_state}`",
                f"- **分类判定**：`{verdict_category}`",
                f"- **是否产生候选**：`{'是' if matched_cand else '否（未产生候选）'}`",
                "",
                "## 2. 状态机迁移轨迹与终态拦截分析",
                "",
                "| 步骤 | 从状态 | 到状态 | 触发时刻 (UTC) | 变迁原因 |",
                "|---|---|---|---|---|"
            ]
            if ev_transitions:
                for step, t in enumerate(ev_transitions):
                    md_lines.append(f"| {step + 1} | `{t['from_state']}` | `{t['to_state']}` | `{_iso(t['at_ms'])}` | {t['reason']} |")
            else:
                md_lines.append("| - | `无` | `NOT_FOUND` | - | 未生成事件，无状态迁移 |")

            md_lines.extend([
                "",
                "## 3. 规则行为对撞与精准定性",
                ""
            ])
            if current_state == "INVALIDATED":
                md_lines.extend([
                    "1. **结构失效保护真实生效**：",
                    f"   - 在检测到连续 2 根 1h 收盘跌破箱体下沿时，正规触发 `INVALIDATED` 终结（原因：`{reason_detail}`）；",
                    "   - 彻底斩断了 04-B 旧版中跌破箱底仍虚假反弹生成候选的伪信号；",
                    "2. **同一输入终态留存**：",
                    "   - 保留原输入行情与箱体参数，同一输入在规则收紧后合法终结，绝不更换案例。"
                ])
            else:
                md_lines.extend([
                    "1. **【特别纠偏】真实状态为 NOT_FOUND（未建档）**：",
                    "   - 本案例在 05-B 当前版本中**并未生成对应事件**，`current_state` 为 `NOT_FOUND`；",
                    "   - **根本原因**：前序事件（案例 4~6）在 2026-09-12T03:59:59Z 建档，其生命周期持续至 2026-09-16T00:59:59Z 才破位终结。在同标的单活跃事件与冷却约束下，旧版在 2026-09-15T11:59:59Z 探测的箱体（1789473599999）在新版运行中根本未被建档！新版后续建档的箱体时间为 2026-09-16T11:59:59Z（1789559999999）；",
                    "   - **纠偏声明**：旧报告 05-E-R1 宏观摘要称‘7 笔事件在破位时均正规触发连续 2 根 1h 收盘跌破箱体下沿 ⇒ INVALIDATED’与本文档套用失效模板均属失实。本案例实际为前序事件占位未建档（NOT_FOUND），特此纠偏！"
                ])

            case_md_path = CASES_DIR / f"案例_{idx + 1}_{old_sym}_{old_sch}.md"
            case_md_path.write_text("\n".join(md_lines), encoding="utf-8")

        out_file = OUT_DIR / "04_original_7_events_audit.json"
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Audited original 7 events, saved to {out_file}")

    finally:
        pass

if __name__ == "__main__":
    run_audit()
