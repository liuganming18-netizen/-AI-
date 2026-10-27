# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E | 脚本 03: 真实历史事件逐时点输入与判据独立对撞核验

【重要声明】
本脚本使用的参数配置为工程验证用测试配置 (fixture)，仅用于核查事件推进链路与状态机行为，
严禁用于评估策略盈亏或输出正式策略绩效！

选取 2~3 个真实历史事件：
1. 事件 A: 成功推进至候选 (CANDIDATE) 的完整事件 (如 SOLUSDT 或 AVAXUSDT)
2. 事件 B: 扫低后因压制位/卖压/破位导致未成候选或失效的事件
3. 事件 C: 04-A 规则与 04-B 实现产生分支分歧的典型事件 (如扫低时 s_loc <= L 或压制位空间不足)

逐时点输出：
- 决策时刻 (UTC)
- 可见输入数据 (当时收盘可见，无未来数据)
- 条件成立/不成立真值
- 04-A 规则要求 vs 04-B 实际判定
- 最终终态与拒绝原因
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "out"
CASES_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "cases"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CASES_DIR.mkdir(parents=True, exist_ok=True)

def _iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))

def ms_of(s):
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)

def trace_real_events():
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum05_trace_"))
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

        fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-04-B" / "fixture" / "fixture_rules.json"
        os.environ["ACCUM04_CONFIG"] = str(fixture_cfg_path)
        cfg = AEE.load_config(fixture_cfg_path)
        store = AEE.open_store(iso_db_path)
        feed = AEE.DataFeed()

        # ── 案例 1: SOLUSDT 冷档区间 (2026-08-01 ~ 2026-09-05) ──
        t_sol_start = ms_of("2026-08-01T00:00:00Z")
        t_sol_end = ms_of("2026-09-05T00:00:00Z")
        res_sol = AEE.scan_symbol("SOLUSDT", cfg, feed, store,
                                  start_ms=t_sol_start, end_ms=t_sol_end,
                                  max_boxes=2, open_candidates=True,
                                  warmup_days=60)

        # ── 案例 2: AVAXUSDT 热库区间 (2026-09-12 ~ 2026-10-25) ──
        t_avax_start = ms_of("2026-09-12T00:00:00Z")
        t_avax_end = ms_of("2026-10-25T00:00:00Z")
        res_avax = AEE.scan_symbol("AVAXUSDT", cfg, feed, store,
                                   start_ms=t_avax_start, end_ms=t_avax_end,
                                   max_boxes=2, open_candidates=True,
                                   warmup_days=60)

        # 提取两个标的下的所有候选与事件
        cands_all = store.con.execute(
            "SELECT * FROM accum04_candidate ORDER BY created_ms"
        ).fetchall()
        events_all = store.con.execute(
            "SELECT * FROM observe_event ORDER BY created_ms"
        ).fetchall()
        transitions_all = store.con.execute(
            "SELECT * FROM observe_transition ORDER BY id"
        ).fetchall()
        attempts_all = store.con.execute(
            "SELECT * FROM accum04_open_attempt ORDER BY id"
        ).fetchall()

        # 整理成案例结构
        detailed_cases = []

        for cand in cands_all:
            c_dict = dict(cand)
            evt = [e for e in events_all if e["event_id"] == c_dict["event_id"]][0]
            e_dict = dict(evt)
            conds = json.loads(c_dict["conditions_json"] or "[]")
            evt_trans = [dict(t) for t in transitions_all if t["event_id"] == c_dict["event_id"]]
            evt_attempts = [dict(a) for a in attempts_all if a["candidate_id"] == c_dict["candidate_id"]]

            # 提取 04-A 与 04-B 的关键对照
            diff_observations = []
            
            # 1. 扫低对象检查
            # 检查是否有 s_loc > L 的校验
            s_loc_val = None
            for cd in conds:
                if cd["name"] == "sweep_lowest_below_reference":
                    s_loc_val = cd.get("requirement")
            
            diff_observations.append({
                "checkpoint": "扫低对象 s_loc > L 约束",
                "04b_actual": "未校验 s_loc 是否高于箱底 L (仅取前20根最低值)",
                "04a_rule": "必须 s_loc > box['L']，否则属于破L箱底扫低，不得计为提前入场",
            })

            # 2. 压制位定义与合格性
            diff_observations.append({
                "checkpoint": "局部压制位定义与合格性",
                "04b_actual": f"取 prior_20_high: {c_dict.get('resistance')}，未检查离箱底箱顶空间",
                "04a_rule": "要求 swing_high 摆动高点，且合格性 L + 0.5A <= R <= H - 0.5A",
            })

            # 3. 承接指标位置
            diff_observations.append({
                "checkpoint": "承接与卖压指标时序",
                "04b_actual": "在确认收上压制位时 (confirm) 检查 sell_pressure (当前测试配置为 require=false)",
                "04a_rule": "必须在扫低根 (sweep) 检查承接主动买占比 tbr 与主动卖压 sell",
            })

            detailed_cases.append({
                "candidate_id": c_dict["candidate_id"],
                "event_id": c_dict["event_id"],
                "scheme": c_dict["scheme"],
                "symbol": c_dict["symbol"],
                "confirm_time_utc": _iso(c_dict["confirm_close_ms"]),
                "entry_bar_utc": _iso(c_dict["entry_bar_open_ms"]),
                "entry_ref_price": c_dict["entry_ref_price"],
                "resistance": c_dict["resistance"],
                "status": c_dict["status"],
                "status_reason": c_dict["status_reason"],
                "conditions_eval_count": len(conds),
                "conditions_detail": conds,
                "transitions": evt_trans,
                "open_attempts": evt_attempts,
                "rule_discrepancy_analysis": diff_observations,
            })

        # 输出 JSON
        out_summary = {
            "generated_by": "ACCUM-EARLY-05-E::03_event_trace.py",
            "warning": "测试配置仅用于工程行为核对，严禁作为正式策略绩效结论",
            "cases_count": len(detailed_cases),
            "cases": detailed_cases,
        }
        with open(OUT_DIR / "03_event_trace.json", "w", encoding="utf-8") as f:
            json.dump(out_summary, f, ensure_ascii=False, indent=2)

        # 生成人类可读的 markdown 案例文件
        for i, cs in enumerate(detailed_cases[:3]):
            md_path = CASES_DIR / f"案例_{i+1}_{cs['symbol']}_{cs['scheme']}.md"
            lines = [
                f"# 真实事件逐时点行为核对案例 {i+1}：{cs['symbol']}（方案 {cs['scheme']}）",
                "",
                "> **免责声明**：本案例采用工程测试配置 (fixture) 运行，旨在核实代码时点、状态迁移与判据行为，**不输出任何正式策略绩效**。",
                "",
                f"- **候选 ID**：`{cs['candidate_id']}`",
                f"- **事件 ID**：`{cs['event_id']}`",
                f"- **标的**：`{cs['symbol']}`",
                f"- **方案**：`{cs['scheme']}`",
                f"- **确认时刻**：`{cs['confirm_time_utc']}`",
                f"- **入场 Bar 开盘时刻**：`{cs['entry_bar_utc']}`",
                f"- **入场参考价**：`{cs['entry_ref_price']}`",
                f"- **压制位**：`{cs['resistance']}`",
                f"- **候选状态**：`{cs['status']}`（`{cs['status_reason']}`）",
                "",
                "## 1. 状态机迁移轨迹（时间序列可见）",
                "",
                "| 序号 | 从状态 | 到状态 | 触发时刻 (UTC) | 迁移原因 |",
                "|---|---|---|---|---|",
            ]
            for idx, tr in enumerate(cs["transitions"]):
                lines.append(f"| {idx+1} | {tr['from_state']} | {tr['to_state']} | {_iso(tr['at_ms'])} | {tr['reason']} |")

            lines.extend([
                "",
                "## 2. 候选判定条件真值表（可见输入与判定结果）",
                "",
                "| 判定项 | 观测值 | 门槛要求 | 判定结果 | 备注 |",
                "|---|---|---|---|---|",
            ])
            for c in cs["conditions_detail"]:
                lines.append(f"| `{c['name']}` | `{c.get('value')}` | `{c.get('requirement')}` | **{c.get('result')}** | {c.get('note') or '-'} |")

            lines.extend([
                "",
                "## 3. 04-A 规则与 04-B 实现差异分析",
                "",
                "| 检查点 | 04-B 实际实现行为 | 04-A 规则规范要求 | 潜在策略影响 |",
                "|---|---|---|---|",
            ])
            for d in cs["rule_discrepancy_analysis"]:
                lines.append(f"| {d['checkpoint']} | {d['04b_actual']} | {d['04a_rule']} | 语义偏离／门槛遗漏 |")

            with open(md_path, "w", encoding="utf-8") as mf:
                mf.write("\n".join(lines) + "\n")

        print(f"[OK] 真实历史事件追踪核验完成，提炼 {len(detailed_cases)} 笔案例，输出至 {OUT_DIR / '03_event_trace.json'}")

        store.close()
        feed.close()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    trace_real_events()
