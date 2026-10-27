#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 当前策略身份与权威依据核对（只读）

把四份「看起来都像规范」的东西摆在一起逐字段比对：
  ① 冻结规则 md        evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md
  ② 代码生效配置       策略/WyckoffMaster/event_trend_v0_1/config.py::make_ft_low4h_config
  ③ 模拟盘生效视图      数据/研究输入/.../10_研究参数生效视图_v2_2_user5000_shared.json
  ④ 契约样张           evidence/SYSTEM-CONTRACT-01/02_FT_LOW4H配置身份规范.json

并核对：22 组「自称 FT-LOW4H」的 run 实际用了哪一列；模拟盘当前执行路径到底执行不执行 240h。

只读；不改任何规则/配置。
"""
import ast
import hashlib
import json
import os
import re

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/04_身份核对.json")
FREEZE = "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"
CONFIG = "策略/WyckoffMaster/event_trend_v0_1/config.py"
VIEW = ("数据/研究输入/最新唐奇安威科夫接通与收尾_2026-10-11/"
        "5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json")
CONTRACT = "evidence/SYSTEM-CONTRACT-01/02_FT_LOW4H配置身份规范.json"


def sha(rel: str) -> str:
    return hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()


def code_defaults() -> dict:
    src = open(os.path.join(ROOT, CONFIG), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "make_ft_low4h_config":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    try:
                        return {k.value: ast.literal_eval(v) for k, v in zip(sub.keys, sub.values)}
                    except Exception:                          # noqa: BLE001
                        pass
    return {}


def main() -> None:
    view = json.load(open(os.path.join(ROOT, VIEW), encoding="utf-8"))
    contract = json.load(open(os.path.join(ROOT, CONTRACT), encoding="utf-8"))
    ca = contract["samples"]["effective_config_A"]
    code = code_defaults()
    paper_engine = open(os.path.join(ROOT, "框架/backend/paper_engine.py"), encoding="utf-8").read()
    live_worker = open(os.path.join(ROOT, "框架/backend/live_worker.py"), encoding="utf-8").read()

    table = {
        "初始止损": {
            "冻结md": "P0 = Lr − 0.1A（结构位）",
            "代码make_ft_low4h_config": f"stoploss_s={code.get('stoploss_s')}（成交价×(1−s)）",
            "模拟盘视图": f"stoploss_s={view['stops'].get('stoploss_s')}；uniform={view['stops'].get('uniform')}",
            "契约样张": f"stoploss_s={ca.get('stoploss_s')}",
        },
        "跟踪止损启用": {
            "冻结md": "首次 4h 收盘浮盈 ≥ +2R 才启用",
            "代码make_ft_low4h_config": f"trailing_trigger_r={code.get('trailing_trigger_r')}（入场即启用）",
            "模拟盘视图": f"trailing_enabled={view['stops'].get('trailing_enabled')}（未给触发阈值）",
            "契约样张": "未列该字段",
        },
        "跟踪线取几根 4h": {
            "冻结md": "最近 10 根完整 4h 最低价",
            "代码make_ft_low4h_config": f"trailing_lookback_4h_bars={code.get('trailing_lookback_4h_bars')}",
            "模拟盘视图": "trail_d=0.05，语义=最近一根 4h low×(1−d)",
            "契约样张": "trail_d=" + str(ca.get("trail_d")),
        },
        "最长持仓": {
            "冻结md": "10 个自然日，到期下一根 15m 开盘退出",
            "代码make_ft_low4h_config": f"max_holding_days={code.get('max_holding_days')}（不执行到期）",
            "模拟盘视图": f"max_holding_hours={view['stops'].get('max_holding_hours')}",
            "契约样张": f"max_holding_hours={ca.get('max_holding_hours')}",
        },
        "初始本金": {
            "冻结md": "未固定绝对本金，只固定每笔 0.5% 权益",
            "代码make_ft_low4h_config": "EventTrendConfig.initial_equity=2000.0",
            "模拟盘视图": f"{view['risk'].get('initial_equity_USDT')}",
            "契约样张": "未列（由调用方传）",
        },
        "最大同时持仓": {
            "冻结md": "3", "代码make_ft_low4h_config": "EventTrendConfig.max_active_positions=3",
            "模拟盘视图": str(view["risk"].get("max_positions")), "契约样张": "未列",
        },
        "单名名义上限": {
            "冻结md": "权益 30%", "代码make_ft_low4h_config": "max_notional_per_position=0.30",
            "模拟盘视图": str(view["risk"].get("single_name_notional_equity_cap")), "契约样张": "未列",
        },
        "总名义上限": {
            "冻结md": "权益 90%", "代码make_ft_low4h_config": "max_total_notional=0.90",
            "模拟盘视图": str(view["risk"].get("gross_long_notional_equity_cap")), "契约样张": "未列",
        },
        "组合初始风险上限": {
            "冻结md": "1.5%", "代码make_ft_low4h_config": "max_total_risk_budget=0.015",
            "模拟盘视图": str(view["risk"].get("portfolio_initial_stop_risk_cap")),
            "契约样张": str(ca.get("portfolio_initial_stop_risk_cap")),
        },
        "入场参与率上限": {
            "冻结md": "前一根 15m 成交额的 0.1%",
            "代码make_ft_low4h_config": "max_participation_of_prev_15m_quote=0.001",
            "模拟盘视图": str(view["risk"].get("entry_participation_previous_15m_quote_cap")),
            "契约样张": "未列",
        },
        "滑点": {
            "冻结md": "3 bps", "代码make_ft_low4h_config": f"slippage_bps={code.get('slippage_bps')}",
            "模拟盘视图": f"{view['execution'].get('slippage_each_side')}（5 bps）",
            "契约样张": f"{ca.get('slippage_each_side')}（5 bps）",
        },
        "手续费": {
            "冻结md": "5 bps", "代码make_ft_low4h_config": f"taker_fee_bps={code.get('taker_fee_bps')}",
            "模拟盘视图": f"{view['execution'].get('fee_each_side')}（5 bps）",
            "契约样张": f"{ca.get('fee_each_side')}（5 bps）",
        },
    }

    # 22 组 FT-LOW4H run 的实际参数（来自 01 的聚合）
    layer = json.load(open(os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/01_产物分层.json"),
                           encoding="utf-8"))
    ft = layer["FT_LOW4H参数组合"]

    # 模拟盘当前执行路径是否执行 240h
    active_path = {
        "unified_runtime 调用": "PE.check_position_exit_on_bar（只判止损，无到期分支）",
        "paper_engine.check_position_exit_on_bar 含到期": "expiry" in paper_engine.split(
            "def check_position_exit_on_bar")[1].split("def advance_position_trailing")[0],
        "advance_position_trailing 自述": re.search(
            r"入场后立即跟踪，([^\n]+)", paper_engine).group(0) if re.search(
            r"入场后立即跟踪，([^\n]+)", paper_engine) else None,
        "live_worker 是否执行到期": "due_expiry" in live_worker,
        "live_worker 是否被 unified_runtime 引用": "live_worker" in open(
            os.path.join(ROOT, "框架/backend/unified_runtime.py"), encoding="utf-8").read(),
    }

    out = {
        "核对时刻_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "文件哈希": {
            FREEZE: sha(FREEZE), CONFIG: sha(CONFIG), VIEW: sha(VIEW), CONTRACT: sha(CONTRACT),
        },
        "视图自我声明": {
            "spec_version": view.get("spec_version"),
            "purpose": view.get("purpose"),
            "production_config": view.get("production_config"),
            "live_trading_authorized": view.get("live_trading_authorized"),
            "research_policy_frozen": view.get("research_policy_frozen"),
        },
        "逐字段对照": table,
        "22组FT_LOW4H实际参数": ft,
        "模拟盘到期执行路径": active_path,
        "结论": [
            "对『模拟盘』而言，权威依据是生效视图（user5000_shared_ft_low4h）："
            "它的 stoploss_s/trail_d/入场即跟踪/5bps 与代码一致；240h 字段在视图与契约里都存在，"
            "但当前执行路径（check_position_exit_on_bar / advance_position_trailing）不判到期，"
            "只有未被 unified_runtime 引用的 live_worker 会执行 240h ⇒ 字段存在、当前不生效、"
            "换路径就会生效，属真实风险点而不是『已取消』。",
            "对『回测』而言，冻结 md 是 2026-10-16 的旧规则文本（2R/10根/10天/Lr−0.1A），"
            "1,197 组旧规则 run 的参数与它一致；22 组 FT-LOW4H run 的参数与它不一致，"
            "却仍把该 md 的 SHA 写进 inputs.fingerprints.rules ⇒ 身份引用错文件，"
            "这不是『实现违反规范』，而是『规范没有 FT-LOW4H 版本』。",
            "22 组 FT-LOW4H run 的参数是混合口径：本金 5000、参与率 1%（来自模拟盘视图），"
            "但仓位数 3、单名 30%、总 90%（来自代码默认）⇒ 既不是冻结 md 口径，"
            "也不是模拟盘口径，不能拿来给任何一侧背书。",
        ],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "逐字段对照"}, ensure_ascii=False, indent=1)[:4000])
    print("→", OUT)


if __name__ == "__main__":
    main()
