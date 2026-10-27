# -*- coding: utf-8 -*-
"""FT-ENTRY-09: 统一预热资格策略代码生成脚本

基于 FT-ENTRY-07 的策略基类，引入统一 200 根 4h 预热资格与逐币准入规则文件 pair_eligibility_rules.json。
在状态机候选生成与 confirm_trade_entry 两个层级落实严格拦截门禁。
同时生成 27 组实验策略派生类 (9组 C0 + 9组 C1 + 9组 C2)。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT07_BASE_PY = BASE_DIR / "evidence/FT-ENTRY-07/user_data/strategies/WyckoffParamExplorationBase.py"
FT09_STRAT_DIR = BASE_DIR / "evidence/FT-ENTRY-09/user_data/strategies"

with open(FT07_BASE_PY, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 替换路径标识
code = code.replace("evidence/FT-ENTRY-04/user_data", "evidence/FT-ENTRY-09/user_data")
code = code.replace("evidence/FT-ENTRY-07/out", "evidence/FT-ENTRY-09/out")

# 2. 修改 ParamTimingEventStateMachine.__init__ 签名及属性
old_sm_init = '''    def __init__(
        self,
        config: Optional[EventTrendConfig] = None,
        symbol: str = "SOLUSDT",
        venue: str = "binance",
        enable_oi_filter: bool = True,
        timing_mode: str = "baseline",
        a_entry_level_atr_offset: float = 0.5,
        a_volume_multiplier: float = 1.0,
        c_vol_ratio_vs: float = 0.8,
        c_retest_zone_atr: float = 0.5
    ):
        super().__init__(config=config, symbol=symbol, enable_oi_filter=enable_oi_filter)
        self.venue = venue
        self.timing_mode = timing_mode
        self.a_entry_level_atr_offset = a_entry_level_atr_offset
        self.a_volume_multiplier = a_volume_multiplier
        self.c_vol_ratio_vs = c_vol_ratio_vs
        self.c_retest_zone_atr = c_retest_zone_atr'''

new_sm_init = '''    def __init__(
        self,
        config: Optional[EventTrendConfig] = None,
        symbol: str = "SOLUSDT",
        venue: str = "binance",
        enable_oi_filter: bool = True,
        timing_mode: str = "baseline",
        a_entry_level_atr_offset: float = 0.5,
        a_volume_multiplier: float = 1.0,
        c_vol_ratio_vs: float = 0.8,
        c_retest_zone_atr: float = 0.5,
        q_start_ms: int = 0,
        q_end_ms: int = 0,
        qualified: bool = True
    ):
        super().__init__(config=config, symbol=symbol, enable_oi_filter=enable_oi_filter)
        self.venue = venue
        self.timing_mode = timing_mode
        self.a_entry_level_atr_offset = a_entry_level_atr_offset
        self.a_volume_multiplier = a_volume_multiplier
        self.c_vol_ratio_vs = c_vol_ratio_vs
        self.c_retest_zone_atr = c_retest_zone_atr
        self.q_start_ms = q_start_ms
        self.q_end_ms = q_end_ms
        self.qualified = qualified'''

assert old_sm_init in code, "old_sm_init not found"
code = code.replace(old_sm_init, new_sm_init)

# 3. 阶段 B: SWEEP 等待 RETEST (回踩阶段) 增加资格准入拦截
old_c_entry = '''                    # C0: 原缩量回踩直接入场
                    if self.timing_mode == "C":
                        initial_risk_r = close_p - initial_stop'''

new_c_entry = '''                    # C0: 原缩量回踩直接入场
                    if self.timing_mode == "C":
                        # FT-ENTRY-09: 统一预热资格准入拦截
                        if not self.qualified or close_time_ms < self.q_start_ms or close_time_ms > self.q_end_ms:
                            self._terminate_event(
                                event, EventState.INVALIDATED, close_time_ms,
                                f"C0缩量回踩发生在准入区间外({close_time_ms} not in [{self.q_start_ms}, {self.q_end_ms}])，跳过信号",
                                {"q_start_ms": self.q_start_ms, "q_end_ms": self.q_end_ms}
                            )
                            return None

                        initial_risk_r = close_p - initial_stop'''

assert old_c_entry in code, "old_c_entry not found"
code = code.replace(old_c_entry, new_c_entry)

# 4. 阶段 B: C1/C2 原信号记录处增加资格准入拦截
old_c12_pending = '''                    # C1 / C2: 记录原信号，等待紧接的下一根1h收盘确认
                    elif self.timing_mode in ("C1", "C2"):
                        event.pending_c_confirmation = {'''

new_c12_pending = '''                    # C1 / C2: 记录原信号，等待紧接的下一根1h收盘确认
                    elif self.timing_mode in ("C1", "C2"):
                        # FT-ENTRY-09: 统一预热资格准入拦截 (原信号必须在资格区间内)
                        if not self.qualified or close_time_ms < self.q_start_ms or close_time_ms > self.q_end_ms:
                            self._terminate_event(
                                event, EventState.INVALIDATED, close_time_ms,
                                f"{self.timing_mode}原信号发生在准入区间外({close_time_ms} not in [{self.q_start_ms}, {self.q_end_ms}])，跳过信号",
                                {"q_start_ms": self.q_start_ms, "q_end_ms": self.q_end_ms}
                            )
                            return None

                        event.pending_c_confirmation = {'''

assert old_c12_pending in code, "old_c12_pending not found"
code = code.replace(old_c12_pending, new_c12_pending)

# 5. 阶段 C: C1/C2 确认柱收盘处增加资格准入拦截
old_c_conf = '''                    # 1. 基本有效性检验：未跌破P0、未收盘突破H+0.1A、未连续2根破L
                    if low_p < P0:
                        failed_reason = "broke_p0_stop"'''

new_c_conf = '''                    # 1. 基本有效性检验：未跌破P0、未收盘突破H+0.1A、未连续2根破L
                    if not self.qualified or close_time_ms < self.q_start_ms or close_time_ms > self.q_end_ms:
                        failed_reason = "confirmation_outside_eligibility_window"
                    elif low_p < P0:
                        failed_reason = "broke_p0_stop"'''

assert old_c_conf in code, "old_c_conf not found"
code = code.replace(old_c_conf, new_c_conf)

# 6. WyckoffParamExplorationBase.__init__ 增加规则文件加载
old_strat_init = '''        # FT-ENTRY-04: 成交前价格约束审计记录与防重试集合
        self.evaluated_candidates: set = set()
        self.execution_audit_records: List[dict] = []
        out_dir = os.path.join(BASE_DIR, "evidence/FT-ENTRY-09/out")
        os.makedirs(out_dir, exist_ok=True)
        self.audit_log_path = os.path.join(out_dir, f"audit_orders_{self.__class__.__name__}.csv")'''

new_strat_init = '''        # FT-ENTRY-04 / 07 / 09: 成交前价格约束审计记录与防重试集合
        self.evaluated_candidates: set = set()
        self.execution_audit_records: List[dict] = []
        out_dir = os.path.join(BASE_DIR, "evidence/FT-ENTRY-09/out")
        os.makedirs(out_dir, exist_ok=True)
        self.audit_log_path = os.path.join(out_dir, f"audit_orders_{self.__class__.__name__}.csv")

        # FT-ENTRY-09: 加载统一预热资格规则表
        rules_path = os.path.join(os.path.dirname(__file__), "pair_eligibility_rules.json")
        if os.path.exists(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                self.eligibility_rules = json.load(f)
        else:
            self.eligibility_rules = {}'''

assert old_strat_init in code, "old_strat_init not found"
code = code.replace(old_strat_init, new_strat_init)

# 7. populate_indicators 传入准入参数
old_sm_inst = '''        sm = ParamTimingEventStateMachine(
            config=cfg,
            symbol=clean_symbol,
            timing_mode=self.timing_mode,
            enable_oi_filter=self.enable_oi_filter,
            a_entry_level_atr_offset=self.a_entry_level_atr_offset,
            a_volume_multiplier=self.a_volume_multiplier,
            c_vol_ratio_vs=self.c_vol_ratio_vs,
            c_retest_zone_atr=self.c_retest_zone_atr
        )'''

new_sm_inst = '''        pair_rule = self.eligibility_rules.get(pair, {})
        q_start_ms = pair_rule.get("q_start_ms", 0)
        q_end_ms = pair_rule.get("q_end_ms", 0)
        qualified = pair_rule.get("qualified", False)

        sm = ParamTimingEventStateMachine(
            config=cfg,
            symbol=clean_symbol,
            timing_mode=self.timing_mode,
            enable_oi_filter=self.enable_oi_filter,
            a_entry_level_atr_offset=self.a_entry_level_atr_offset,
            a_volume_multiplier=self.a_volume_multiplier,
            c_vol_ratio_vs=self.c_vol_ratio_vs,
            c_retest_zone_atr=self.c_retest_zone_atr,
            q_start_ms=q_start_ms,
            q_end_ms=q_end_ms,
            qualified=qualified
        )'''

assert old_sm_inst in code, "old_sm_inst not found"
code = code.replace(old_sm_inst, new_sm_inst)

# 8. _record_order_audit 增强资格字段
old_rec_audit = '''    def _record_order_audit(self, cand, pair: str, current_time: datetime, rate: float, frozen_H: float,
                            decision: str, reason: str) -> None:
        rec = {
            "strategy": self.__class__.__name__,
            "pair": pair,
            "candidate_id": cand.candidate_id,
            "signal_time_ms": cand.snapshot_time_ms,
            "signal_time": datetime.fromtimestamp(cand.snapshot_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z"),
            "frozen_H": frozen_H,
            "planned_execution_time": current_time.strftime("%Y-%m-%d %H:%M:%S%z"),
            "planned_execution_time_ms": _to_timestamp_ms(current_time),
            "fill_price": rate,
            "price_minus_h": rate - frozen_H,
            "decision": decision,
            "reason": reason
        }'''

new_rec_audit = '''    def _record_order_audit(self, cand, pair: str, current_time: datetime, rate: float, frozen_H: float,
                            decision: str, reason: str,
                            q_start_ms: Optional[int] = None, q_start_utc: Optional[str] = None,
                            q_end_ms: Optional[int] = None, q_end_utc: Optional[str] = None) -> None:
        if q_start_ms is None:
            rule = self.eligibility_rules.get(pair, {})
            q_start_ms = rule.get("q_start_ms", 0)
            q_start_utc = rule.get("q_start_utc", "")
            q_end_ms = rule.get("q_end_ms", 0)
            q_end_utc = rule.get("q_end_utc", "")

        rec = {
            "strategy": self.__class__.__name__,
            "pair": pair,
            "candidate_id": cand.candidate_id,
            "signal_time_ms": cand.snapshot_time_ms,
            "signal_time": datetime.fromtimestamp(cand.snapshot_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z"),
            "q_start_ms": q_start_ms,
            "q_start_utc": q_start_utc,
            "q_end_ms": q_end_ms,
            "q_end_utc": q_end_utc,
            "frozen_H": frozen_H,
            "planned_execution_time": current_time.strftime("%Y-%m-%d %H:%M:%S%z"),
            "planned_execution_time_ms": _to_timestamp_ms(current_time),
            "fill_price": rate,
            "price_minus_h": rate - frozen_H,
            "decision": decision,
            "reason": reason
        }'''

assert old_rec_audit in code, "old_rec_audit not found"
code = code.replace(old_rec_audit, new_rec_audit)

# 9. confirm_trade_entry 增强统一预热资格拦截与 C0 审计记录
old_confirm_entry = '''    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        if not entry_tag:
            return False

        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)

        if not cand:
            return False

        if cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            return False

        # FT-ENTRY-07: C1 / C2 路线确认后执行时刻合法性与防重试审计
        if self.timing_mode in ("C1", "C2"):
            fill_price = float(rate)
            initial_stop = float(cand.initial_stop_price)

            # 防重试机制：同一候选只评估一次
            if cand.candidate_id in self.evaluated_candidates:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="retry_prohibited_already_evaluated"
                )
                logger.info(f"[{self.timing_mode}][RETRY-REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price}")
                return False

            self.evaluated_candidates.add(cand.candidate_id)

            # 做多合法性检查：执行价必须严格高于初始止损价 P0
            if fill_price <= initial_stop:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="fill_price_le_initial_stop_p0"
                )
                logger.info(f"[{self.timing_mode}][REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price} <= P0={initial_stop}")
                return False

            # 基本边界检查
            if fill_price < cand.min_entry_price or fill_price > cand.max_entry_price:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="outside_min_max_bounds"
                )
                logger.info(f"[{self.timing_mode}][REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price} outside bounds")
                return False

            # 放行
            self._record_order_audit(
                cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                decision="PASSED", reason=f"{self.timing_mode}_confirmed_entry"
            )
            return True

        # FT-ENTRY-04: A 路线严格突破前真实成交约束'''

new_confirm_entry = '''    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        if not entry_tag:
            return False

        cands = self.pair_candidates.get(pair, [])
        cand = next((c for c in cands if c.candidate_id == entry_tag), None)

        if not cand:
            return False

        if cand.initial_stop_price is None or math.isnan(cand.initial_stop_price) or cand.initial_stop_price <= 0:
            return False

        fill_price = float(rate)
        current_time_ms = _to_timestamp_ms(current_time)

        # FT-ENTRY-09: 统一预热资格硬性检查 (C0 / C1 / C2 均受约束)
        pair_rule = self.eligibility_rules.get(pair, {})
        q_start_ms = pair_rule.get("q_start_ms", 0)
        q_end_ms = pair_rule.get("q_end_ms", 0)
        qualified = pair_rule.get("qualified", False)
        q_start_utc = pair_rule.get("q_start_utc", "")
        q_end_utc = pair_rule.get("q_end_utc", "")

        if not qualified or current_time_ms < q_start_ms or current_time_ms > q_end_ms:
            self._record_order_audit(
                cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                decision="REJECTED", reason="outside_eligibility_window",
                q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
            )
            logger.info(f"[{self.timing_mode}][ELIGIBILITY-REJECT] {pair} cand={cand.candidate_id} time={current_time} outside [{q_start_ms}, {q_end_ms}]")
            return False

        # FT-ENTRY-07 / FT-ENTRY-09: C1 / C2 路线确认后执行时刻合法性与防重试审计
        if self.timing_mode in ("C1", "C2"):
            initial_stop = float(cand.initial_stop_price)

            # 防重试机制：同一候选只评估一次
            if cand.candidate_id in self.evaluated_candidates:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="retry_prohibited_already_evaluated",
                    q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
                )
                logger.info(f"[{self.timing_mode}][RETRY-REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price}")
                return False

            self.evaluated_candidates.add(cand.candidate_id)

            # 做多合法性检查：执行价必须严格高于初始止损价 P0
            if fill_price <= initial_stop:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="fill_price_le_initial_stop_p0",
                    q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
                )
                logger.info(f"[{self.timing_mode}][REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price} <= P0={initial_stop}")
                return False

            # 基本边界检查
            if fill_price < cand.min_entry_price or fill_price > cand.max_entry_price:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="outside_min_max_bounds",
                    q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
                )
                logger.info(f"[{self.timing_mode}][REJECT] {pair} cand={cand.candidate_id} fill_price={fill_price} outside bounds")
                return False

            # 放行
            self._record_order_audit(
                cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                decision="PASSED", reason=f"{self.timing_mode}_confirmed_entry",
                q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
            )
            return True

        # FT-ENTRY-09: C0 路线执行
        if self.timing_mode == "C":
            if fill_price < cand.min_entry_price or fill_price > cand.max_entry_price:
                self._record_order_audit(
                    cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                    decision="REJECTED", reason="outside_min_max_bounds",
                    q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
                )
                return False

            self._record_order_audit(
                cand=cand, pair=pair, current_time=current_time, rate=fill_price, frozen_H=float(cand.box_high_h),
                decision="PASSED", reason="C_original_entry",
                q_start_ms=q_start_ms, q_start_utc=q_start_utc, q_end_ms=q_end_ms, q_end_utc=q_end_utc
            )
            return True

        # FT-ENTRY-04: A 路线严格突破前真实成交约束'''

assert old_confirm_entry in code, "old_confirm_entry not found"
code = code.replace(old_confirm_entry, new_confirm_entry)

# 写入基类文件
target_base_py = FT09_STRAT_DIR / "WyckoffParamExplorationBase.py"
with open(target_base_py, "w", encoding="utf-8") as f:
    f.write(code)
print(f"成功写入策略基类: {target_base_py}")

# 生成 27 组策略派生类
matrix = [
    ("Vs04_Z02", 0.4, 0.2),
    ("Vs04_Z04", 0.4, 0.4),
    ("Vs04_Z06", 0.4, 0.6),
    ("Vs06_Z02", 0.6, 0.2),
    ("Vs06_Z04", 0.6, 0.4),
    ("Vs06_Z06", 0.6, 0.6),
    ("Vs08_Z02", 0.8, 0.2),
    ("Vs08_Z04", 0.8, 0.4),
    ("Vs08_Z06", 0.8, 0.6),
]

modes = [
    ("C", "Wyckoff_C"),
    ("C1", "Wyckoff_C1"),
    ("C2", "Wyckoff_C2"),
]

count = 0
for mode, prefix in modes:
    for suffix, vs, z in matrix:
        strat_name = f"{prefix}_{suffix}"
        strat_code = f"""# -*- coding: utf-8 -*-
from WyckoffParamExplorationBase import WyckoffParamExplorationBase

class {strat_name}(WyckoffParamExplorationBase):
    timing_mode = "{mode}"
    c_vol_ratio_vs = {vs}
    c_retest_zone_atr = {z}
"""
        strat_file = FT09_STRAT_DIR / f"{strat_name}.py"
        with open(strat_file, "w", encoding="utf-8") as f:
            f.write(strat_code)
        count += 1

print(f"成功生成 {count} 个策略派生类文件至 {FT09_STRAT_DIR}")
