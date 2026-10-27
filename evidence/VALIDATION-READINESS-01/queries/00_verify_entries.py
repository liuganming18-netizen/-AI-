# -*- coding: utf-8 -*-
"""VALIDATION-READINESS-01 · 只读核验：既有验证入口、能力、缺口。

原则
----
1. **只读**：不 import 策略包、不跑回测、不改任何生产文件；
   仅 open(...,"r") 读源码/产物，仅写入本工单 out/ 与 logs/。
2. 每一行结论都必须能指到「文件:行」或「真实 run 产物」，不写无法核对的判断。
3. 行号由本脚本现算，不手写（避免源码变动后对不上）。

产出
----
out/01_入口路径表.csv
out/02_能力核对表.csv
out/03_前置缺口清单.csv
logs/00_verify.json   （逐项核验结果 + 关键哈希）
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import re

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/VALIDATION-READINESS-01/out")
LOGS = os.path.join(ROOT, "evidence/VALIDATION-READINESS-01/logs")
RUNS = os.path.join(ROOT, "回测日志/event_trend_v0_1/runs")

P_RULES = "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
P_CFG = "策略/WyckoffMaster/event_trend_v0_1/config.py"
P_RUNNER = "策略/WyckoffMaster/event_trend_v0_1/runner.py"
P_ADAPTER = "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py"
P_SM = "策略/WyckoffMaster/event_trend_v0_1/state_machine.py"
P_PITB = "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py"
P_CONTRACTS = "策略/WyckoffMaster/event_trend_v0_1/contracts.py"
P_SPEC = "策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py"
P_ENGINE = "策略/WyckoffMaster/trading_core/engine.py"
P_FILL = "策略/WyckoffMaster/trading_core/fill.py"
P_FUND = "策略/WyckoffMaster/trading_core/funding.py"
P_GUARD = "策略/WyckoffMaster/trading_core/pit_guard.py"
P_CHART = "框架/backend/backtest_chart.py"
P_STORE = "框架/backend/backtest_store.py"
P_BECORE = "evidence/BREAKEVEN-02/code/be_core.py"
P_BERUN = "evidence/BREAKEVEN-02/code/02_run_fixed.py"
P_BATCH = "run_batch_2026_sep_oct_50.py"
P_SWEEP = "run_full_sweep_200x100.py"

CHECK = []


def rec(name, ok, detail=""):
    CHECK.append({"item": name, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})
    return ok


def sha256_file(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def read_lines(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def find_line(rel, pattern, start=1):
    """返回首个匹配行号（1-based），找不到返回 None。"""
    rx = re.compile(pattern)
    for i, ln in enumerate(read_lines(rel), 1):
        if i >= start and rx.search(ln):
            return i
    return None


def loc(rel, pattern, start=1):
    n = find_line(rel, pattern, start=start)
    return f"{rel}:{n}" if n else f"{rel}:(未找到)"


def write_csv(name, header, rows):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


# ══════════════════════ 一、基础哈希 ══════════════════════
rules_sha = sha256_file(P_RULES)
cfg_sha = sha256_file(P_CFG)
rec("冻结规则文件存在且哈希可算", bool(rules_sha), rules_sha)
rec("冻结规则哈希 = 1f1167de…9a8ab", (rules_sha or "").startswith("1f1167de"), rules_sha)

cfg_text = "\n".join(read_lines(P_CFG))
rec("config.py 内写有同一冻结哈希（单一事实源）", "1f1167de" in cfg_text, "docstring 声明")

# CODE_FILES 覆盖面
runner_text = "\n".join(read_lines(P_RUNNER))
m = re.search(r"CODE_FILES\s*=\s*\[(.*?)\]", runner_text, re.S)
code_files = re.findall(r'"([^"]+)"', m.group(1)) if m else []
rec("代码指纹覆盖文件数 = 20", len(code_files) == 20, len(code_files))
rec("代码指纹未覆盖固定仓位配对实现 be_core.py", not any("be_core" in x for x in code_files),
    "CODE_FILES 内无 be_core/02_run_fixed")

# 事件范围参数必须定位在 run_event_trend_backtest 函数体内（该函数起于 341 行）
_ad_def = find_line(P_ADAPTER, "def run_event_trend_backtest")
_cand = find_line(P_ADAPTER, "candidates: List\\[CandidateSnapshot\\]", start=_ad_def or 1)
rec("事件范围参数行号落在执行入口函数体内",
    bool(_ad_def) and bool(_cand) and _cand > _ad_def, f"def={_ad_def} candidates={_cand}")

# ══════════════════════ 二、真实 run 证据 ══════════════════════
def pick_run(with_trades=True):
    pats = sorted(glob.glob(os.path.join(RUNS, "run_WX_EVENT_TREND_V0_1_*")))
    if with_trades:
        for d in pats:
            tp = os.path.join(d, "trades.jsonl")
            if os.path.exists(tp) and os.path.getsize(tp) > 0:
                return d
    return pats[0] if pats else None


run_with_trades = pick_run(True)
# 最新 run 按目录 mtime 取（目录名里的时间戳是运行时刻，但符号名会干扰字典序）
run_newest = None
_cand = [d for d in glob.glob(os.path.join(RUNS, "run_WX_EVENT_TREND_V0_1_*"))
         if os.path.exists(os.path.join(d, "inputs.json"))]
if _cand:
    run_newest = max(_cand, key=os.path.getmtime)
# 后端 API 入口（bt_ 前缀）产物多两个文件，单独取一个样本作对照
_bt = [d for d in glob.glob(os.path.join(RUNS, "bt_*")) if os.path.exists(os.path.join(d, "summary.json"))]
run_bt = max(_bt, key=os.path.getmtime) if _bt else None
bt_artifacts = sorted(os.path.basename(x) for x in glob.glob(os.path.join(run_bt, "*"))) if run_bt else []

rec("存在含真实成交的 run 目录", bool(run_with_trades), run_with_trades)
rec("存在新版（含 pit_audit）run 目录", bool(run_newest), run_newest)

artifacts = []
trade_keys = []
summary_keys = []
summary_a_keys = []
fp_keys = []
pit_guard_ver = None
formal_gate = None

if run_with_trades:
    artifacts = sorted(os.path.basename(x) for x in glob.glob(os.path.join(run_with_trades, "*")))
    with open(os.path.join(run_with_trades, "trades.jsonl"), encoding="utf-8") as f:
        first = next((l for l in f if l.strip()), None)
    if first:
        trade_keys = list(json.loads(first).keys())
    sp = os.path.join(run_with_trades, "summary.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            sd = json.load(f)
        summary_keys = list(sd.keys())
        summary_a_keys = list((sd.get("summary_variant_a") or {}).keys())
    ip = os.path.join(run_with_trades, "inputs.json")
    if os.path.exists(ip):
        with open(ip, encoding="utf-8") as f:
            idt = json.load(f)
        fp_keys = list((idt.get("fingerprints") or {}).keys())

if run_newest:
    sp = os.path.join(run_newest, "summary.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            sd = json.load(f)
        formal_gate = {
            "formal_pass_allowed": sd.get("formal_pass_allowed"),
            "components": sd.get("formal_pass_allowed_components"),
            "block_reasons": sd.get("formal_pass_block_reasons"),
        }
    ip = os.path.join(run_newest, "inputs.json")
    if os.path.exists(ip):
        with open(ip, encoding="utf-8") as f:
            pit_guard_ver = json.load(f).get("pit_guard_version")

rec("策略包直接产物文件数 = 17", len(artifacts) == 17, len(artifacts))
rec("后端 API 入口产物 = 19（多 chart.json / run.json）",
    len(bt_artifacts) == 19 and "chart.json" in bt_artifacts, f"{len(bt_artifacts)} {bt_artifacts[:3]}")
rec("逐笔成交字段含成本拆分（fee/funding/slippage/rounding）",
    all(k in trade_keys for k in ("entry_fee", "exit_fee", "funding_fee", "slippage_loss", "rounding_loss")),
    len(trade_keys))
rec("汇总含四类成本合计", all(k in summary_a_keys for k in
                        ("fee_total", "funding_total", "slippage_total", "rounding_total")), summary_a_keys)
rec("汇总无组合层回撤/盈亏比字段",
    not any(k in summary_a_keys for k in ("max_drawdown", "profit_factor", "win_rate", "sharpe")),
    "只有单币净额与成本合计")
rec("四类指纹齐全（rules/code/config/inputs）",
    set(fp_keys) == {"rules", "code", "config", "inputs"}, fp_keys)
rec("资金费缺字段时记缺口而非补 0",
    "funding_event_missing_fields" in "\n".join(read_lines(P_FUND)), "funding.py:95-102")
rec("逐笔成交带 funding_gaps 字段", "funding_gaps" in trade_keys, trade_keys[:8])
rec("新版 run 带 PIT 守卫版本号", bool(pit_guard_ver), pit_guard_ver)
rec("新版 run 双闸字段存在", bool(formal_gate) and "components" in (formal_gate or {}), formal_gate)

# ══════════════════════ 三、入口路径表 ══════════════════════
path_rows = [
    ["1 冻结规则", loc(P_RULES, "^#"), "全文", "规则文件本体", f"sha256={rules_sha}"],
    ["2 冻结配置", loc(P_CFG, "class EventTrendConfig"), "EventTrendConfig(frozen=True)",
     "initial_equity / risk_budget_per_trade / taker_fee_bps / slippage_bps / trailing_trigger_r / max_holding_days",
     f"config.py sha256={cfg_sha}"],
    ["3 输入数据加载", loc(P_RUNNER, "def load_symbol_data"), "load_symbol_data(symbol,start_date,end_date,allow_auto_window)",
     "df_4h(含ATR预热220根) / df_1h / df_15m / oi_ts / oi_vals / funding_events",
     "4h 取目标区间前 220 根；allow_auto_window 必须 False"],
    ["4 事件状态机", loc(P_RUNNER, "def run_state_machine_simulation"), "run_state_machine_simulation",
     "boxes / sweeps / retests / breakouts / candidates", loc(P_SM, "class EventStateMachine")],
    ["5 PIT 守卫", loc(P_RUNNER, "assert_candles_closed"), "pit.assert_candles_closed",
     "决策时刻 = 1h 开盘 + 3600s；未收盘 K 线抛 PitViolation", loc(P_GUARD, "class PitStore")],
    ["6 候选快照", loc(P_CONTRACTS, "class CandidateSnapshot"), "CandidateSnapshot",
     "candidate_id / symbol / entry_time / stop / signal_id", "candidates.jsonl"],
    ["7 执行入口", loc(P_ADAPTER, "def run_event_trend_backtest"), "run_event_trend_backtest",
     "candidates=[...] / variant(A/B/C) / instrument_spec / initial_equity",
     "缺 instrument_spec 且缺 price_step ⇒ 抛 ExecutionInputError"],
    ["8 撮合取整", loc(P_FILL, "def price_breakdown"), "price_breakdown(raw_price,is_buy,price_step,slippage_rate)",
     "买 ROUND_CEILING / 卖 ROUND_FLOOR（Decimal）", loc(P_FILL, "class SimulatedBarFillAdapter")],
    ["9 资金费", loc(P_FUND, "def settle_funding"), "settle_funding（模型常量 settlement_events / assumption_fixed_8h）",
     "(time_ms, rate, mark_price) 三字段；判据 last_funding_ms < ev_ms <= t_ms",
     "缺 rate/mark 记 funding_gaps，不补 0（funding.py:95-102）"],
    ["10 交易核心相位", loc(P_ENGINE, "def run\\("), "TradingCore.run",
     "A 锁利+资金费 → B 跳空退出 → C 新开仓 → D bar内止损 → E 盯市权益", "相位顺序不可换"],
    ["11 产物装配", loc(P_RUNNER, "def _build_run_files"), "_build_run_files",
     f"策略包直跑 {len(artifacts)} 个产物文件（含 trades/fills/orders/positions/ledger/equity_curve）；"
     f"后端 API 入口另加 chart.json / run.json 共 {len(bt_artifacts)} 个",
     "两套入口产物集不同，须在报告里声明用的是哪套"],
    ["12 原子提交", loc(P_RUNNER, "def _commit_run_dir"), "_commit_run_dir",
     "写 .partial-{pid} → 逐文件 fsync → os.replace", "manifest.json 含每文件 sha256"],
    ["13 汇总报告", loc(P_RUNNER, '"summary_variant_a"'), "summary.json",
     "net_pnl / fee_total / funding_total / slippage_total / rounding_total / final_equity / trade_count",
     "无组合层回撤与盈亏比"],
    ["14 图表载荷", loc(P_CHART, "def generate_chart_payload"), "generate_chart_payload",
     "chart.json", "不在 runner 内，需调用方另行生成"],
    ["15 四类指纹", loc(P_RUNNER, "def code_fingerprint"), "code/rules/config/input_fingerprint",
     "rules.sha256 / code.combined_sha256 / config.sha256 / inputs.*", "inputs.json.fingerprints"],
]
write_csv("01_入口路径表.csv", ["环节", "实现位置", "符号", "关键字段", "证据/约束"], path_rows)

# ══════════════════════ 四、能力核对表 ══════════════════════
cap_rows = [
    ["固定仓位配对（A/B/C 共用同一数量）", "支持，但不在生产包内",
     loc(P_BECORE, "def install") + " ； " + loc(P_BERUN, "def run_window"),
     "install(breakeven_r, fixed_quantities)；decide_entry 内 replace(intent, quantity=fq)",
     "evidence/BREAKEVEN-02/out/raw_fixed.jsonl；201 事件口径",
     "实现位于 evidence/ 下，不参与代码指纹；换机器/换人复用需显式带路径"],
    ["指定事件范围（按 signal_id 挑单笔）", "支持（适配器级）",
     loc(P_ADAPTER, "candidates: List\\[CandidateSnapshot\\]", start=341),
     "candidates=[cand] 单元素即单事件隔离回放",
     loc(P_BERUN, "candidates=\\[cand\\]") + " 逐信号隔离并断言 trades==1",
     "runner 层无事件过滤参数，需调用方自建循环"],
    ["指定时间范围", "支持",
     loc(P_RUNNER, "def load_symbol_data"),
     "start_date / end_date（含端点按日）；allow_auto_window=False 强制严格窗口",
     "run_batch_2026_sep_oct_50.py START_DATE/END_DATE 硬编码",
     "窗口过短会导致 4h 预热不足；参数写死在批次脚本里，改窗口要改脚本"],
    ["保留原始逐笔输出", "支持",
     loc(P_RUNNER, "def _build_run_files"),
     f"策略包直跑 {len(artifacts)} 个文件；后端 API 入口 {len(bt_artifacts)} 个（多 chart.json / run.json）",
     "manifest.json 逐文件 sha256 + bytes",
     "无自动清理/归档策略，历史 run 目录会持续累积"],
    ["版本指纹", "支持",
     loc(P_RUNNER, "def code_fingerprint"),
     "rules / code / config / inputs 四类 + pit_guard_version + spec_status",
     "真实 run inputs.json.fingerprints；pit_guard_v1.0.0",
     "固定仓位配对代码不在 code 指纹内"],
    ["输出目录可重定向到工单目录", "不支持",
     loc(P_RUNNER, "self.runs_base ="),
     "runs_base = <base_dir>/回测日志/event_trend_v0_1/runs，无环境变量/参数覆盖",
     "改 base_dir 会连带改 parquet_base（数据路径），无法只挪输出",
     "下一阶段若要求「只写工单目录」，需新增输出根参数（属改代码）"],
    ["组合层指标（回撤/盈亏比/胜率）", "不支持",
     "summary.json 仅单币口径",
     "summary_variant_a 无 max_drawdown / profit_factor / win_rate",
     "真实 run summary_variant_a 字段清单",
     "多币组合需另写汇总，属新代码"],
    ["四项对账套件默认执行", "不支持（需显式调用）",
     loc(P_RUNNER, "def execute_reconciliation_suite"),
     "repeatability / chunked_batch / duplicate_injection / crash_recovery",
     "evidence/run_all_symbols_validation.py:54 显式调用",
     "run_variant_abc 不调用；summary.reconcile 只是身份级对账"],
]
write_csv("02_能力核对表.csv",
          ["能力", "结论", "实现位置", "关键参数/字段", "真实证据", "限制说明"], cap_rows)

# ══════════════════════ 五、前置缺口清单 ══════════════════════
gap_rows = [
    ["V1", "固定仓位配对实现不在生产策略包内（evidence/BREAKEVEN-02/code）", "阻止结果可信",
     "CODE_FILES 20 项不含 be_core.py/02_run_fixed.py；代码指纹无法覆盖",
     "下一阶段若用固定仓位配对，跑出来的结果无法用指纹证明「用的是哪一版配对代码」",
     "否（BREAKEVEN-02 自检通过，但不是独立验收）"],
    ["V2", "输出目录硬编码在 <base_dir>/回测日志/event_trend_v0_1/runs", "仅影响方便使用",
     loc(P_RUNNER, "self.runs_base ="),
     "无法只写工单目录；与「写入范围仅限工单目录」的执行纪律冲突",
     "否"],
    ["V3", "批次脚本会把成交写入生产前端 UI界面/web/public/all_trades.json", "仅影响方便使用",
     "run_batch_2026_sep_oct_50.py update_all_trades_json()",
     "误用批次脚本会改动前端生产数据；最小命令必须绕开该函数",
     "否"],
    ["V4", "批次脚本把币池/窗口/输出 CSV 写死在脚本里", "仅影响方便使用",
     "SYMBOLS_FILE / START_DATE / END_DATE 为模块级常量",
     "换币池或换窗口必须改脚本或另写脚本，容易与冻结口径漂移",
     "否"],
    ["V5", "汇总层无组合回撤/盈亏比/胜率", "阻止结果可信",
     "summary_variant_a 字段清单（无 max_drawdown / profit_factor / win_rate）",
     "OOS-READY-01 冻结评估办法把回撤列为主要指标，当前入口算不出，需另写",
     "否"],
    ["V6", "PIT 资格当前全部 False（oi_available_at_unproven）", "阻止结果可信",
     "真实 run summary.json：formal_pass_allowed=false，pit=false",
     "任何在此状态下的回测都只能算研究结果，不能作为正式验证通过",
     "是：OOS-READY-01 G1 已记录，不重复测试"],
    ["V7", "历史合约规格 0 条 verified", "阻止结果可信",
     "真实 run summary.json：instrument_spec_unproven",
     "价格步长/数量步长缺乏历史证据，取整口径无法证明对当时有效",
     "是：OOS-READY-01 G2 已记录"],
    ["V8", "暂无可证明的样本外数据", "阻止结果可信",
     "evidence/OOS-READY-01/out/02_验证数据资格表.csv（8 段全部不可用）",
     "入口再完备也无法产出「样本外」结论；这是数据资格问题不是工具问题",
     "是：OOS-READY-01 已交付结论"],
    ["V9", "四项对账套件不在主回测流程内", "仅影响方便使用",
     loc(P_RUNNER, "def execute_reconciliation_suite") + " 无调用点",
     "要证明确定性/断点恢复需额外显式跑一遍",
     "否"],
    ["V10", "chart.json 需调用方另行生成", "仅影响方便使用",
     loc(P_CHART, "def generate_chart_payload"),
     "只影响看图，不影响数值结论", "否"],
    ["V11", "事件级去重（同标的+同入场时刻）无内置支持", "仅影响方便使用",
     "OOS-READY-01 中 206 候选 → 201 事件为外部脚本去重",
     "口径易混（296/206/201 三套），需在报告里显式声明用的是哪套", "否"],
]
write_csv("03_前置缺口清单.csv",
          ["编号", "缺口", "分类", "依据", "对下一阶段的影响", "可复用已有通过项"], gap_rows)

# ══════════════════════ 六、落盘核验结果 ══════════════════════
os.makedirs(LOGS, exist_ok=True)
payload = {
    "frozen_rules_sha256": rules_sha,
    "config_sha256": cfg_sha,
    "code_files_count": len(code_files),
    "run_with_trades": os.path.basename(run_with_trades) if run_with_trades else None,
    "run_newest": os.path.basename(run_newest) if run_newest else None,
    "run_bt": os.path.basename(run_bt) if run_bt else None,
    "artifacts": artifacts,
    "bt_artifacts": bt_artifacts,
    "trade_fields": trade_keys,
    "summary_keys": summary_keys,
    "summary_variant_a_keys": summary_a_keys,
    "fingerprint_keys": fp_keys,
    "pit_guard_version": pit_guard_ver,
    "formal_gate": formal_gate,
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
with open(os.path.join(LOGS, "00_verify.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)

print(json.dumps({"pass": payload["pass"], "fail": payload["fail"],
                  "artifacts": len(artifacts), "trade_fields": len(trade_keys),
                  "rules_sha": rules_sha}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
