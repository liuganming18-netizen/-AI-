# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-04 / 00 · 冻结可交接账本（第一阶段）

工单要求：复用 ENTRY-AUDIT-03，输出机器可读交接包，含
  候选/交易ID、run_id、回放来源与时间窗口；
  成交/取消状态及原始原因；
  原始字段名、手续费、资金费、毛盈亏、净盈亏；
  费用符号与净盈亏公式；
  原账本路径和记录定位；
  执行有效性、历史规格资格及 PIT 限制。
163 笔连续回放与 42 笔窗口回放必须分别标明，不得静默合并。

本脚本只读 ENTRY-AUDIT-03 的产物 + 引擎原值，不重跑回测、不写任何 run 目录。
写入边界：仅 evidence/ENTRY-AUDIT-04/handoff/。

产出：
  handoff/ledger_handoff.csv         227 行主表（机器可读，UTF-8 BOM，Excel 直开）
  handoff/ledger_handoff.parquet     同内容 parquet（保留 dtype）
  handoff/signal_index.csv           226 个唯一信号索引（含 COTI 重复关系）
  handoff/coti_duplicate.csv         COTI 同快照两条逐项对比
  handoff/unfilled_22.csv            22 条未成交 + 真实拒单原因
  handoff/generation_split.json      两代际分账（禁拼接声明）
  handoff/field_dictionary.csv       原始字段名 ↔ 交接字段名 ↔ 来源
  handoff/handoff_meta.json          符号约定 / 净值公式 / 三态 / PIT / 口径说明
  handoff/paths_for_fe07.json        供 FE07 读取的准确路径 + sha256
  handoff/SHA256SUMS.txt             冻结哈希清单
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
A3 = ROOT / "evidence" / "ENTRY-AUDIT-03"
OUT = ROOT / "evidence" / "ENTRY-AUDIT-04"
HANDOFF = OUT / "handoff"

HANDOFF_VERSION = "ENTRY-AUDIT-04.handoff.v1"

# ── 净值公式与符号约定：全表唯一事实源，逐行写进主表，防止下游各算一套 ──
NET_FORMULA = "net_pnl = gross_pnl - fees - funding_fee"
FEE_FORMULA = "fees = entry_fee + exit_fee"
FUNDING_SIGN = "正=支出（付出资金费），负=收入（收到资金费）"
FEE_SIGN = "正=支出（手续费只可能是支出）"
GROSS_SIGN = "多空统一为「平仓现金流 − 开仓现金流」的净额，正=赚"
SLIPPAGE_NOTE = (
    "滑点与取整已经写进 entry_price / exit_price（= post_round_price），"
    "所以 net_pnl 只再减 fees 与 funding_fee，不再单独减 slippage_loss；"
    "再减一次即重复扣减"
)

# ── PIT 限制：全池共性，不是个别记录的错 ──
PIT_STATUS = "UNPROVEN"
PIT_LIMITATION = (
    "PIT（时点可得性）未证：候选特征与执行输入使用的是回放窗口内已闭合 K 线，"
    "但当时点真实可得的数据快照（含合约规格、资金费率、行情落盘时刻）无留痕，"
    "无法证明「在决策时刻就能拿到这些值」；且历史合约规格无变更证据 ⇒ 全池无 verified。"
    "因此本样本只能称「可信研究样本」，不能称「可正式回测 / 可上线」。"
)

GEN_LABEL = {
    "cand_ev": "连续回放（cand_ev_*）",
    "old_cand": "365 天窗口回放（old_cand_*）",
}
GEN_SOURCE = {
    "cand_ev": "连续回放：单标的 2021-12-01~2026-10-18 全区间一次跑完，状态机路径依赖",
    "old_cand": "窗口回放：每个「组×标的」各跑 365 天，与前序交易无关（起始权益独立）",
}

# ── 字段字典：原始字段名 ↔ 交接字段名 ──
# 工单要求「原始字段名」，因此金额列一律沿用引擎 _close_position 写出的键名，
# 不用上游 stage2 的 total_fees / total_funding（那两个键名是 163 条费用为 0 的根因）。
FIELD_DICT = [
    # (交接字段名, 原始字段名, 来源层, 说明)
    ("gross_pnl", "gross_pnl", "引擎 _close_position 返回的 trade rec",
     "毛盈亏（未扣费、未扣资金费）；引擎原值，未做任何换算"),
    ("entry_fee", "entry_fee", "引擎 trade rec", "开仓手续费，正=支出"),
    ("exit_fee", "exit_fee", "引擎 trade rec", "平仓手续费，正=支出"),
    ("fees", "fees", "引擎 trade rec", "= entry_fee + exit_fee；⚠ 不是上游的 total_fees"),
    ("funding_fee", "funding_fee", "引擎 trade rec", "资金费成本口径，正=支出、负=收入；⚠ 不是上游的 total_funding"),
    ("net_pnl", "net_pnl", "引擎 trade rec", "= gross_pnl − fees − funding_fee"),
    ("slippage_loss", "slippage_loss", "引擎 trade rec", "= slippage_loss_pure + rounding_loss；已体现在成交价里，不再从 net_pnl 扣"),
    ("slippage_loss_pure", "slippage_loss_pure", "引擎 trade rec", "纯滑点段（3bps）"),
    ("rounding_loss", "rounding_loss", "引擎 trade rec", "取整段（Decimal 买 ceil / 卖 floor）"),
    ("qty", "qty", "引擎 trade rec", "成交数量（取整后）"),
    ("entry_price", "entry_price", "引擎 trade rec", "成交价 = price_audit.post_round_price"),
    ("exit_price", "exit_price", "引擎 trade rec", "成交价 = price_audit.post_round_price"),
    ("exit_reason", "exit_reason", "引擎 trade rec", "退出原因原串"),
    ("entry_time_ms", "entry_time", "引擎 trade rec", "开仓成交时刻（毫秒 UTC）"),
    ("exit_time_ms", "exit_time", "引擎 trade rec", "平仓成交时刻（毫秒 UTC）"),
    ("funding_gaps_n", "funding_gaps", "引擎 trade rec", "资金费结算缺字段的次数；>0 表示这些结算没有计入费用，方向未知"),
    ("engine_signal_id", "signal_id", "引擎 trade rec", "引擎侧信号 ID；⚠ 窗口代际这里是引擎自生成的 cand_ev_*，不是 old_cand_*"),
    ("order_id", "order_id", "引擎 trade rec", "开仓订单号（账本 ORDER_FILLED 侧同值）"),
    ("exit_order_id", "exit_order_id", "引擎 trade rec", "平仓订单号"),
    ("candidate_id", "candidate_id", "ENTRY-AUDIT-03 身份映射", "候选 ID；连续回放为 cand_ev_*，窗口回放为 old_cand_*"),
    ("event_id", "event_id", "ENTRY-AUDIT-03 身份映射",
     "⚠ 227 条各不相同，但窗口代际取的是 cand_exec_earliest_ms 而非快照时刻 ⇒ 不能当「唯一信号键」用"),
    ("signal_key", "（派生）", "ENTRY-AUDIT-04 派生", "= {symbol}_{snapshot_time_ms}，226 个唯一值；跨代际比对唯一信号请用这个"),
    ("snapshot_time_ms", "（派生）", "ENTRY-AUDIT-03",
     "快照时刻。ValidationResult 本身没有这个字段，是用 cand_exec_earliest_ms − 900000 还原的（连续回放 185/185 验证成立）"),
    ("cancel_reason", "execution_canceled_reason", "引擎 ValidationResult",
     "取消原因原串。22 条未成交全部有值；已成交为 None（不是空串、不是 0）"),
    ("raw_signal_rejected_reason", "reason", "账本 SIGNAL_REJECTED 事件 payload",
     "原始拒单事件里的原因串；与 cancel_reason 逐字相同才算「原因来自原始事件」"),
    ("spec_qualification", "（判定结果）", "合约规格资格判定", "verified / unverified_historical / contradicted / not_listed"),
    ("formal_pass_allowed", "（判定结果）", "合约规格资格判定", "是否允许正式回测；227 条全 false"),
    ("exec_validity", "run_valid + invalid_reasons", "引擎 run 级自检", "valid / invalid"),
    ("research_usability", "（判定结果）", "ENTRY-AUDIT-03 可用性判定", "usable / usable_with_caveat / not_usable"),
    ("price_audit.*", "price_audit", "引擎 trade rec",
     "价格链路 raw_price → post_slip_price → post_round_price；⚠ 没有 final_price 这个键"),
    ("ledger_record_locator", "（派生）", "ENTRY-AUDIT-04 派生",
     "该笔在独立 run 目录 trades.csv 里的精确位置（行号从数据行 1 起，+1 为表头）"),
    ("ledger_position_closed_net_pnl", "payload.net_pnl", "账本 POSITION_CLOSED 事件", "账本侧的净盈亏，用于多重集比对；仅 5 个探针 run 有"),
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe(tid: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", tid)


def ms_to_utc(ms) -> str | None:
    if ms is None or (isinstance(ms, float) and pd.isna(ms)) or pd.isna(ms):
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_trade_index() -> dict:
    """按 (task_id, order_id) 建成交原值索引，并算出每个 run 内 trades.csv 的行号。

    ⚠ 必须用 (run_id, order_id) 复合键：同一个候选在连续回放与窗口回放里会生成
    **完全相同的 signal_id 与 order_id**（两者都由「标的 + 箱体起止时刻」派生，
    与回放窗口无关）⇒ 单用 order_id 做键会跨代际串账，取到另一代际的那一笔。
    实测 213 条成交里有 6 组 order_id 跨代际重复。

    02b_materialize_runs.py 用 pd.DataFrame(r["trades"]).to_csv 落盘，
    组内顺序与 02_replay_trades.jsonl 完全一致 ⇒ 可据此还原 csv 行号。
    """
    rows = [json.loads(l) for l in (A3 / "02_replay_trades.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    seq: dict[str, int] = {}
    idx: dict[tuple, dict] = {}
    for t in rows:
        tid = t.get("task_id")
        seq[tid] = seq.get(tid, 0) + 1
        t = dict(t)
        t["_csv_row"] = seq[tid]
        t["_run_dir"] = f"runs/{safe(tid)}"
        idx[(tid, t["order_id"])] = t
    return idx


def build_ledger_probe_index() -> dict:
    """账本事件探针：按 task_id 归集，并记录 jsonl 行号（1 起）。"""
    p = A3 / "04_ledger_events_probe.jsonl"
    out: dict[str, dict] = {}
    with open(p, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if not line.strip():
                continue
            e = json.loads(line)
            tid = e.get("task_id")
            rec = out.setdefault(tid, {"file": "evidence/ENTRY-AUDIT-03/04_ledger_events_probe.jsonl",
                                       "lines": [], "position_closed_nets": []})
            rec["lines"].append(i)
            if e.get("event_type") == "POSITION_CLOSED":
                rec["position_closed_nets"].append(float((e.get("payload") or {}).get("net_pnl", 0.0)))
    for tid, rec in out.items():
        rec["line_range"] = f"{min(rec['lines'])}-{max(rec['lines'])}" if rec["lines"] else None
    return out


def main() -> None:
    HANDOFF.mkdir(parents=True, exist_ok=True)

    ident = pd.read_csv(A3 / "03_identity_full.csv")
    assert len(ident) == 227, f"身份表应为 227 行，实际 {len(ident)}"

    trade_by_order = build_trade_index()
    probe_by_task = build_ledger_probe_index()
    window_plan = json.loads((A3 / "00_window_plan.json").read_text(encoding="utf-8"))
    config_fp = json.loads((A3 / "00_config_fingerprint.json").read_text(encoding="utf-8"))

    rows = []
    for i, r in ident.iterrows():
        gen = r["generation"]
        tid = r["replay_task_id"]
        executed = bool(r["executed"])
        tr = trade_by_order.get((tid, r["order_id"])) if executed else None
        if executed:
            assert tr is not None, f"成交行找不到引擎原值：{r['candidate_id']} / {tid} / {r['order_id']}"

        # ── 账本定位：独立 run 目录里的精确行 ──
        if executed and tr is not None:
            locator = (f"evidence/ENTRY-AUDIT-03/{tr['_run_dir']}/trades.csv"
                       f"#数据行{tr['_csv_row']}（文件第 {tr['_csv_row'] + 1} 行）")
            engine_signal_id = tr.get("signal_id")
        else:
            locator = f"evidence/ENTRY-AUDIT-03/runs/{safe(tid)}/candidates.csv（未成交，无成交记录）"
            engine_signal_id = None

        probe = probe_by_task.get(tid)
        ledger_pc_net = None
        if probe and executed and tr is not None:
            # 该 run 只有一笔时可直接对上；多笔时用多重集匹配，此处给出候选值列表长度
            cands = probe["position_closed_nets"]
            if len(cands) == 1:
                ledger_pc_net = round(cands[0], 9)

        rows.append({
            "handoff_version": HANDOFF_VERSION,
            "handoff_row_id": i + 1,
            # ── 身份 ──
            "candidate_id": r["candidate_id"],
            "event_id": r["event_id"],
            "signal_key": f"{r['symbol']}_{int(r['snapshot_time_ms'])}",
            "engine_signal_id": engine_signal_id,
            "order_id": r["order_id"] if executed else None,
            "exit_order_id": r["exit_order_id"] if executed else None,
            # ── run 与回放来源 ──
            "run_id": tid,
            "run_dir": f"evidence/ENTRY-AUDIT-03/runs/{safe(tid)}",
            "generation": gen,
            "generation_label": GEN_LABEL[gen],
            "replay_source": GEN_SOURCE[gen],
            "replay_group": (None if pd.isna(r["replay_group"]) else int(r["replay_group"])),
            "window_start": r["replay_window_start"],
            "window_end": r["replay_window_end"],
            "symbol": r["symbol"],
            "snapshot_time_ms": int(r["snapshot_time_ms"]),
            "snapshot_time_utc": ms_to_utc(r["snapshot_time_ms"]),
            "signal_time_utc": r["signal_time_utc"],
            # ── 状态与原始原因 ──
            "exec_status": "FILLED" if executed else "UNFILLED",
            "executed": executed,
            "cancel_reason": (None if pd.isna(r["cancel_reason"]) else r["cancel_reason"]),
            "cancel_reason_source": ("引擎 ValidationResult.execution_canceled_reason（与账本 SIGNAL_REJECTED 原串逐字相同）"
                                     if not executed else None),
            "raw_signal_rejected_reason": (None if pd.isna(r["raw_signal_rejected_reason"]) else r["raw_signal_rejected_reason"]),
            # ── 金额（引擎原字段名）──
            "gross_pnl": r["gross_pnl"],
            "entry_fee": tr["entry_fee"] if tr else None,
            "exit_fee": tr["exit_fee"] if tr else None,
            "fees": r["fee_paid"],
            "funding_fee": r["funding_cost"],
            "net_pnl": r["net_pnl"],
            "slippage_loss": r["slippage_cost"],
            "slippage_loss_pure": r["slippage_pure"],
            "rounding_loss": r["rounding_loss"],
            "qty": r["executed_qty"],
            "entry_price": r["entry_price"],
            "exit_price": r["exit_price"],
            "exit_reason": r["exit_reason"],
            "entry_time_ms": r["fill_time_ms"],
            "exit_time_ms": r["exit_time_ms"],
            "holding_hours": r["holding_hours"],
            "funding_gaps_n": (len(tr.get("funding_gaps") or []) if tr else None),
            # ── 净值公式（逐行带上，防下游自造）──
            "net_pnl_formula": NET_FORMULA,
            "net_pnl_residual": r["net_identity_residual"],
            # ── 账本定位 ──
            "ledger_record_locator": locator,
            "ledger_events_file": (probe["file"] if probe else None),
            "ledger_events_line_range": (probe["line_range"] if probe else None),
            "ledger_position_closed_net_pnl": ledger_pc_net,
            # ── 三态 ──
            "spec_qualification": r["spec_qualification"],
            "spec_status_engine": r["spec_status_engine"],
            "formal_pass_allowed": bool(r["formal_pass_allowed"]),
            "exec_validity": r["exec_validity"],
            "exec_invalid_reasons": (None if pd.isna(r["exec_invalid_reasons"]) else r["exec_invalid_reasons"]),
            "identity_chain_ok": bool(r["identity_chain_ok"]),
            "research_usability": r["research_usability"],
            "usability_reasons": r["usability_reasons"],
            "pit_status": PIT_STATUS,
            "pit_limitation": PIT_LIMITATION,
            # ── 价格审计链路 ──
            "entry_raw_price": r["entry_raw_price"],
            "entry_post_slip_price": r["entry_post_slip_price"],
            "entry_post_round_price": r["entry_post_round_price"],
            "entry_price_step": r["entry_price_step"],
            "exit_raw_price": r["exit_raw_price"],
            "exit_post_slip_price": r["exit_post_slip_price"],
            "exit_post_round_price": r["exit_post_round_price"],
            "exit_price_step": r["exit_price_step"],
        })

    h = pd.DataFrame(rows)

    # ── 一致性硬校验：先自证，再交付 ──
    checks = {}
    checks["rows_227"] = len(h) == 227
    checks["executed_205"] = int(h["executed"].sum()) == 205
    checks["unfilled_22"] = int((~h["executed"]).sum()) == 22
    checks["unique_signal_key_226"] = h["signal_key"].nunique() == 226
    checks["generation_split"] = (h["generation"].value_counts().to_dict() == {"cand_ev": 185, "old_cand": 42})
    filled = h[h["executed"]]
    checks["filled_fields_missing_0"] = int(filled[["order_id", "entry_price", "exit_price", "fees",
                                                     "funding_fee", "gross_pnl", "net_pnl"]].isna().sum().sum()) == 0
    res = (filled["net_pnl"] - (filled["gross_pnl"] - filled["fees"] - filled["funding_fee"])).abs()
    checks["net_identity_205"] = int((res <= 1e-9).sum()) == 205
    checks["fee_eq_entry_plus_exit_205"] = bool(((filled["fees"] - filled["entry_fee"] - filled["exit_fee"]).abs() <= 1e-9).all())
    checks["unfilled_all_have_reason"] = bool(h.loc[~h["executed"], "cancel_reason"].notna().all())
    checks["filled_no_cancel_reason"] = bool(h.loc[h["executed"], "cancel_reason"].isna().all())
    checks["cancel_reason_equals_raw_event"] = bool(
        (h.loc[~h["executed"], "cancel_reason"] == h.loc[~h["executed"], "raw_signal_rejected_reason"]).all())
    checks["formal_pass_all_false"] = bool((~h["formal_pass_allowed"]).all())
    checks["pit_all_unproven"] = bool((h["pit_status"] == PIT_STATUS).all())
    checks["no_generation_silently_merged"] = h["generation_label"].notna().all()

    # ── 身份键规则：order_id 单独用会串代际，必须 (run_id, order_id) ──
    dup_order = h.loc[h["executed"], "order_id"].duplicated(keep=False)
    n_dup_order_rows = int(dup_order.sum())
    # 引擎输出层（213 条成交记录）的撞号统计：这才是撞号真实发生的地方
    _tr_rows = [json.loads(l) for l in (A3 / "02_replay_trades.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    _sig_cnt: dict[str, int] = {}
    for t in _tr_rows:
        _sig_cnt[t["signal_id"]] = _sig_cnt.get(t["signal_id"], 0) + 1
    _dup_groups = {k: v for k, v in _sig_cnt.items() if v > 1}
    dup_engine = {"trade_records": len(_tr_rows), "dup_signal_groups": len(_dup_groups),
                  "dup_rows": int(sum(_dup_groups.values()))}
    checks["run_order_key_unique"] = not bool(
        h.loc[h["executed"], ["run_id", "order_id"]].duplicated().any())
    # 与 ENTRY-AUDIT-03 的身份表逐位对齐（防止索引键取错代际）
    a3_net = float(ident.loc[ident["executed"], "net_pnl"].sum())
    checks["sum_net_matches_03"] = abs(float(filled["net_pnl"].sum()) - a3_net) <= 1e-9
    a3_fee = float(ident.loc[ident["executed"], "fee_paid"].sum())
    checks["sum_fee_matches_03"] = abs(float(filled["fees"].sum()) - a3_fee) <= 1e-9
    a3_fund = float(ident.loc[ident["executed"], "funding_cost"].sum())
    checks["sum_funding_matches_03"] = abs(float(filled["funding_fee"].sum()) - a3_fund) <= 1e-9

    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise SystemExit(f"自检未通过：{bad}")
    # numpy.bool_ 不能直接进 json
    checks = {k: bool(v) for k, v in checks.items()}

    # ── 落盘：主表 ──
    # 可空整数列：避免下游把 3.0 当浮点、把空当 0
    for c in ("replay_group", "funding_gaps_n", "entry_time_ms", "exit_time_ms"):
        if c in h.columns:
            h[c] = h[c].astype("Int64")
    h.to_csv(HANDOFF / "ledger_handoff.csv", index=False, encoding="utf-8-sig")
    h.to_parquet(HANDOFF / "ledger_handoff.parquet", index=False)

    # ── 226 唯一信号索引 ──
    sig = []
    for (sym, snap), g in h.groupby(["symbol", "snapshot_time_ms"], sort=False):
        sig.append({
            "signal_key": f"{sym}_{int(snap)}",
            "symbol": sym,
            "snapshot_time_ms": int(snap),
            "snapshot_time_utc": ms_to_utc(snap),
            "n_rows": len(g),
            "candidate_ids": " | ".join(g["candidate_id"].tolist()),
            "generations": " | ".join(sorted(g["generation"].unique().tolist())),
            "generation_combo": "+".join(sorted(g["generation"].unique().tolist())),
            "n_filled": int(g["executed"].sum()),
            "n_unfilled": int((~g["executed"]).sum()),
            "has_duplicate_across_generations": len(g) > 1,
            "duplicate_note": (
                "同一快照被连续回放与窗口回放各记一次；两条结论相反（一条取消、一条成交）。"
                "这不是重复计数错误，是两代际分别得出的结果，必须并列保留、禁止去重后当一条用"
                if len(g) > 1 else None),
        })
    sig_df = pd.DataFrame(sig)
    assert len(sig_df) == 226, f"唯一信号应为 226，实际 {len(sig_df)}"
    sig_df.to_csv(HANDOFF / "signal_index.csv", index=False, encoding="utf-8-sig")

    # ── COTI 重复关系逐项对比 ──
    coti = h[h["signal_key"] == "COTIUSDT_1640228400000"].copy()
    coti_out = coti[[
        "candidate_id", "generation", "generation_label", "run_id", "snapshot_time_ms",
        "snapshot_time_utc", "exec_status", "cancel_reason",
        "entry_price", "exit_price", "gross_pnl", "fees", "funding_fee", "net_pnl",
        "spec_qualification", "ledger_record_locator",
    ]].copy()
    # 候选侧几何字段（箱体上下沿、ATR、max_entry）是 COTI 冲突的核心，单独带上
    cand_cols = ident[ident["symbol"] == "COTIUSDT"][[
        "candidate_id", "cand_box_high", "cand_box_low", "cand_box_atr",
        "cand_breakout_close", "cand_max_entry", "cand_min_entry", "cand_initial_stop",
        "cand_exec_earliest_ms",
    ]]
    coti_out = coti_out.merge(cand_cols, on="candidate_id", how="left")
    coti_out.to_csv(HANDOFF / "coti_duplicate.csv", index=False, encoding="utf-8-sig")

    # ── 22 条未成交 + 真实原因 ──
    unf = h[~h["executed"]][[
        "candidate_id", "signal_key", "symbol", "generation", "generation_label",
        "run_id", "snapshot_time_utc", "cancel_reason", "raw_signal_rejected_reason",
        "cancel_reason_source", "ledger_record_locator", "spec_qualification",
    ]].copy()
    unf["cancel_reason_class"] = unf["cancel_reason"].str.extract(r"^(canceled_[a-z_]+)")[0]
    unf.to_csv(HANDOFF / "unfilled_22.csv", index=False, encoding="utf-8-sig")

    # ── 两代际分账（禁拼接声明）──
    def gen_stats(g: pd.DataFrame) -> dict:
        f = g[g["executed"]]
        return {
            "label": GEN_LABEL[g["generation"].iloc[0]],
            "source": GEN_SOURCE[g["generation"].iloc[0]],
            "rows": len(g),
            "filled": int(g["executed"].sum()),
            "unfilled": int((~g["executed"]).sum()),
            "symbols": int(g["symbol"].nunique()),
            "runs": int(g["run_id"].nunique()),
            "window_start_min": g["window_start"].min(),
            "window_end_max": g["window_end"].max(),
            "sum_gross_pnl": round(float(f["gross_pnl"].sum()), 6),
            "sum_fees": round(float(f["fees"].sum()), 6),
            "sum_funding_fee": round(float(f["funding_fee"].sum()), 6),
            "sum_net_pnl": round(float(f["net_pnl"].sum()), 6),
            "win_count": int((f["net_pnl"] > 0).sum()),
            "loss_count": int((f["net_pnl"] <= 0).sum()),
            "win_rate": round(float((f["net_pnl"] > 0).mean()), 6) if len(f) else None,
            "funding_gaps_total": int(f["funding_gaps_n"].fillna(0).sum()),
        }

    split = {
        "audit": "ENTRY-AUDIT-04",
        "handoff_version": HANDOFF_VERSION,
        "merge_policy": {
            "rule": "两代际禁止拼接成同一运行",
            "reason": ("连续回放是单标的 5 年一次跑完、状态机路径依赖（箱体只能被突破一次、24h 冷却）；"
                       "窗口回放是每组各跑 365 天、起始权益独立、与前序交易无关。"
                       "两者净盈亏方向相反，合并算胜率/期望会抹平分裂"),
            "allowed": "可以并列报告两代际各自指标；主样本只能是连续回放",
            "forbidden": "不得把 163 + 42 相加当 205 笔同一样本算胜率/期望/夏普",
        },
        "generation_cand_ev": gen_stats(h[h["generation"] == "cand_ev"]),
        "generation_old_cand": gen_stats(h[h["generation"] == "old_cand"]),
        "combined_reference_only": {
            "note": "以下合计数仅供对账，不得作为策略指标",
            "rows": 227,
            "filled": 205,
            "sum_gross_pnl": round(float(filled["gross_pnl"].sum()), 6),
            "sum_fees": round(float(filled["fees"].sum()), 6),
            "sum_funding_fee": round(float(filled["funding_fee"].sum()), 6),
            "sum_net_pnl": round(float(filled["net_pnl"].sum()), 6),
        },
        "unique_signal_space": {
            "rows": 227,
            "unique_signal_key": int(h["signal_key"].nunique()),
            "duplicate_signals": int((sig_df["n_rows"] > 1).sum()),
            "duplicate_detail": sig_df[sig_df["n_rows"] > 1][["signal_key", "candidate_ids", "generation_combo"]].to_dict("records"),
        },
    }
    (HANDOFF / "generation_split.json").write_text(json.dumps(split, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 字段字典 ──
    pd.DataFrame(FIELD_DICT, columns=["交接字段名", "原始字段名", "来源层", "说明"]).to_csv(
        HANDOFF / "field_dictionary.csv", index=False, encoding="utf-8-sig")

    # ── 元数据 ──
    meta = {
        "audit": "ENTRY-AUDIT-04",
        "stage": "第一阶段 · 冻结可交接账本",
        "handoff_version": HANDOFF_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "derived_from": "evidence/ENTRY-AUDIT-03（不重跑回测、不改原账本）",
        "row_semantics": {
            "rows": 227,
            "unique_signal_key": 226,
            "filled": 205,
            "unfilled": 22,
            "why_227_not_226": ("COTIUSDT 同一快照（1640228400000）被连续回放与窗口回放各记一条，"
                                "结论相反；event_id 因窗口代际取 cand_exec_earliest_ms 而不同，"
                                "所以按 event_id 数是 227、按 symbol+snapshot 数是 226"),
        },
        "identity_key_rules": {
            "candidate_id": "全表唯一（227 条各不相同）",
            "event_id": "全表唯一（227），但窗口代际用的是 cand_exec_earliest_ms 而非快照时刻 ⇒ 不能当唯一信号键",
            "signal_key": "= {symbol}_{snapshot_time_ms}，226 个唯一值 ⇒ 跨代际比对唯一信号用这个",
            "order_id": ("⚠ **不是全局唯一**：同一个候选在连续回放与窗口回放里会生成完全相同的 "
                         "signal_id 与 order_id（两者都由「标的 + 箱体起止时刻」派生，与回放窗口无关）。"
                         f"引擎输出层 {dup_engine['trade_records']} 条成交记录里有 "
                         f"{dup_engine['dup_signal_groups']} 组重复（{dup_engine['dup_rows']} 行）"),
            "unique_order_key": "必须用 (run_id, order_id) 复合键；单用 order_id 会跨代际串账",
            "consequence": ("按 order_id 单键关联会取到另一代际的那一笔（qty / 金额都不同，"
                            "因为两代际起始权益与前序交易不同 ⇒ 手数不同），本交接包已按复合键生成"),
            "note_on_205": (f"映射到 205 个候选后重复行数为 {n_dup_order_rows} —— "
                            "不是因为撞号消失，而是因为候选映射按 (group, symbol, snapshot) "
                            "选中的是同一 run 里的另一笔成交；撞号仍存在于引擎输出层"),
            "collision_stats": dup_engine,
        },
        "sign_conventions": {
            "gross_pnl": GROSS_SIGN,
            "fees": FEE_SIGN,
            "entry_fee": FEE_SIGN,
            "exit_fee": FEE_SIGN,
            "funding_fee": FUNDING_SIGN,
            "net_pnl": "胜负一律按这个（扣费后）判，不用 gross_pnl",
            "ledger_FUNDING_FEE_payload_delta": "现金变动口径：支出为负、收入为正，与 funding_fee 符号相反",
            "ledger_relation": "cum_funding_fee = −Σ(payload.delta)；跨层比对必须先取负",
        },
        "formulas": {
            "net_pnl": NET_FORMULA,
            "fees": FEE_FORMULA,
            "slippage": "slippage_loss = slippage_loss_pure + rounding_loss",
            "price_chain": "raw_price → post_slip_price（滑点）→ post_round_price（取整）= 成交价",
            "slippage_not_double_counted": SLIPPAGE_NOTE,
        },
        "three_state_flags": {
            "spec_qualification": {
                "meaning": "合约规格资格：这笔用的历史合约规格（tickSize / stepSize / minQty）能不能被证明",
                "values": {k: int(v) for k, v in h["spec_qualification"].value_counts().items()},
                "verified_requires": "①快照按 serverTime 夹住窗口 ②区间内每份逐一比较一致 ③spec_changes_*.json 变更证据覆盖整窗",
            },
            "exec_validity": {
                "meaning": "执行有效性：这笔所在 run 的撮合是否有效",
                "values": {k: int(v) for k, v in h["exec_validity"].value_counts().items()},
            },
            "research_usability": {
                "meaning": "研究可用性：能不能当研究样本用（与赚不赚钱无关）",
                "values": {k: int(v) for k, v in h["research_usability"].value_counts().items()},
            },
            "formal_pass_allowed": {
                "meaning": "是否允许正式回测/上线口径",
                "values": {str(k).lower(): int(v) for k, v in h["formal_pass_allowed"].value_counts().items()},
            },
        },
        "pit": {"status": PIT_STATUS, "limitation": PIT_LIMITATION},
        "funding_gaps": {
            "total": int(h["funding_gaps_n"].fillna(0).sum()),
            "reason": "funding_event_missing_fields:mark —— 资金费事件缺 mark_price，引擎记 gap 不补 0",
            "consequence": "这些结算没有计入费用，方向未知；缺值不等于 0，不得当 0 处理",
        },
        "null_policy": "所有取不到的值为 None（CSV 里是空），**不是 0**；下游读到空必须报缺失，不得静默当 0",
        "config_fingerprint": {
            "config_sha256": config_fp["config_sha256"],
            "strategy_name": config_fp["config_instance"]["strategy_name"],
            "strategy_version": config_fp["config_instance"]["strategy_version"],
            "engine_contract_version": config_fp["engine_contract_version"],
            "taker_fee_bps": config_fp["config_instance"]["taker_fee_bps"],
            "slippage_bps": config_fp["config_instance"]["slippage_bps"],
            "initial_equity": config_fp["config_instance"]["initial_equity"],
        },
        "window_plan_ref": {
            "file": "evidence/ENTRY-AUDIT-03/00_window_plan.json",
            "generation_A": {k: v for k, v in window_plan["generation_A_continuous"].items() if k != "symbol_list"},
            "generation_B": {k: v for k, v in window_plan["generation_B_windows"].items() if k != "symbol_list"},
        },
        "self_checks": checks,
        "checks_failed": bad,
    }
    (HANDOFF / "handoff_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 供 FE07 读取的准确路径 ──
    files = [
        ("handoff/ledger_handoff.csv", "主表（227 行，机器可读；UTF-8 BOM，Excel 直开）", "FE07 必须读这个"),
        ("handoff/ledger_handoff.parquet", "主表 parquet（dtype 保真）", "FE07 优先读这个"),
        ("handoff/signal_index.csv", "226 个唯一信号索引", "跨代际按唯一信号比对时用"),
        ("handoff/coti_duplicate.csv", "COTI 同快照两条逐项对比", "处理重复信号时用"),
        ("handoff/unfilled_22.csv", "22 条未成交 + 真实拒单原因", "核对未成交口径时用"),
        ("handoff/generation_split.json", "两代际分账 + 禁拼接声明", "出指标前必须先读"),
        ("handoff/field_dictionary.csv", "原始字段名 ↔ 交接字段名", "字段口径有疑问时查"),
        ("handoff/handoff_meta.json", "符号约定 / 公式 / 三态 / PIT / 自检", "口径唯一事实源"),
    ]
    paths = {
        "audit": "ENTRY-AUDIT-04",
        "handoff_version": HANDOFF_VERSION,
        "generated_utc": meta["generated_utc"],
        "read_this": {
            "primary": "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet",
            "primary_csv_fallback": "evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.csv",
            "meta": "evidence/ENTRY-AUDIT-04/handoff/handoff_meta.json",
            "generation_split": "evidence/ENTRY-AUDIT-04/handoff/generation_split.json",
        },
        "do_not_read_for_costs": {
            "path": "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet",
            "why": ("该表的 total_fees / total_funding 两列是键名读错的产物，163 条全为 0；"
                    "用它算费用会得出「零成本」的错误结论"),
        },
        "files": [],
        "upstream_evidence": [],
    }
    for rel, desc, usage in files:
        p = OUT / rel
        paths["files"].append({
            "path": f"evidence/ENTRY-AUDIT-04/{rel}",
            "abs_path": str(p),
            "sha256": sha256(p),
            "bytes": p.stat().st_size,
            "description": desc,
            "usage": usage,
        })
    for rel, desc in [
        ("evidence/ENTRY-AUDIT-03/03_identity_full.csv", "身份映射原表（227 行 71 列）"),
        ("evidence/ENTRY-AUDIT-03/02_replay_trades.jsonl", "重放成交原值（213 条，引擎 trade rec 逐字）"),
        ("evidence/ENTRY-AUDIT-03/02_replay_rejects.jsonl", "原始拒单事件（48 条）"),
        ("evidence/ENTRY-AUDIT-03/04_cost_reconcile.json", "费用对账结果（含账本探针）"),
        ("evidence/ENTRY-AUDIT-03/00_freeze_manifest.json", "4139 个文件冻结清单"),
        ("evidence/ENTRY-AUDIT-03/00_config_fingerprint.json", "配置指纹"),
        ("evidence/ENTRY-AUDIT-03/00_window_plan.json", "窗口计划"),
        ("evidence/ENTRY-AUDIT-03/runs/index.json", "176 个独立 run 目录索引"),
    ]:
        p = ROOT / rel
        paths["upstream_evidence"].append({
            "path": rel, "abs_path": str(p), "sha256": sha256(p) if p.exists() else None,
            "bytes": p.stat().st_size if p.exists() else None, "description": desc,
        })
    (HANDOFF / "paths_for_fe07.json").write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 冻结哈希清单 ──
    lines = []
    for f in sorted(HANDOFF.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS.txt":
            lines.append(f"{sha256(f)}  handoff/{f.name}")
    (HANDOFF / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"交接包已生成 → {HANDOFF}")
    print(f"  主表 {len(h)} 行 / 唯一信号 {h['signal_key'].nunique()} / 成交 {int(h['executed'].sum())} / 未成交 {int((~h['executed']).sum())}")
    print(f"  自检 {len(checks)} 项全部通过")
    print(f"  A 净盈亏 {split['generation_cand_ev']['sum_net_pnl']} / B 净盈亏 {split['generation_old_cand']['sum_net_pnl']}")


if __name__ == "__main__":
    main()
