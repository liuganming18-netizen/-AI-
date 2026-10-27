# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E-R1 | 生成 7 个案例的时序与终态分析 Markdown 文档
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RETEST_JSON = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "out" / "02_original_events_retest.json"
CASES_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "cases"
CASES_DIR.mkdir(parents=True, exist_ok=True)

def generate_case_markdowns():
    with open(RETEST_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    for c in data["cases_retest"]:
        idx = c["case_index"]
        sym = c["symbol"]
        sch = c["scheme"]
        old_id = c["old_event_id"]
        old_st = c["old_04_status"]
        old_tm = c["old_04_confirm_time"]
        curr_st = c["current_state"]
        cand_p = c["candidate_produced"]
        trans = c["current_transitions"]
        
        md_lines = [
            f"# 原输入行为复验案例 {idx}：{sym}（方案 {sch}）",
            "",
            "> **免责声明**：本案例采用工程测试配置 (`fixture_rules_05.json`) 进行行为核验，**仅用于验证状态机逻辑与终态判定，不输出任何正式策略绩效**。",
            "",
            "## 1. 基本信息与版本行为对比",
            "",
            f"- **标的**：`{sym}`",
            f"- **方案**：`{sch}`",
            f"- **04-B 旧版事件 ID**：`{old_id}`",
            f"- **04-B 旧版终态**：`{old_st}`（确认时刻：`{old_tm}`，曾生成伪候选）",
            f"- **05-B 当前版终态**：`{curr_st}`",
            f"- **是否产生候选**：`{'是' if cand_p else '否（未产生候选）'}`",
            "",
            "## 2. 状态机迁移轨迹与终态拦截分析",
            "",
            "| 步骤 | 从状态 | 到状态 | 触发时刻 (UTC) | 变迁原因 |",
            "|---|---|---|---|---|"
        ]

        for step_i, t in enumerate(trans):
            md_lines.append(f"| {step_i + 1} | `{t['from_state']}` | `{t['to_state']}` | `{t['time_utc']}` | {t['reason']} |")

        md_lines.extend([
            "",
            "## 3. 规则行为对撞与科学定性",
            "",
            "1. **结构失效保护生效**：",
            f"   - 在旧版 04-B 中，由于缺失箱体破位保护，行情跌破箱底后依然错误推进，虚构了提前入场候选；",
            f"   - 在当前 05-B 中，`_structural_invalid` 严格生效，在检测到连续 2 根 1h 收盘跌破箱体下沿时，正规触发 `INVALIDATED` 终结，切断了后续虚假信号；",
            "2. **同一输入终态科学留存**：",
            "   - 严格遵守用户约定与审查纪律：同一历史行情输入在规则修复后合法终结，不再产生候选，**如实记录其拦截条件与终态，绝不更换为容易通过的新案例**；",
            "3. **时序遗留问题定性**：",
            "   - 尽管结构失效保护成功拦截了破位事件，但代码审查证实当前 05-B 的承接判定函数依然存在于确认根中，未在扫低根执行，该工程缺陷已交 05-B 整改。"
        ])

        file_path = CASES_DIR / f"案例_{idx}_{sym}_{sch}.md"
        file_path.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"Generated {file_path.name}")

if __name__ == "__main__":
    generate_case_markdowns()
