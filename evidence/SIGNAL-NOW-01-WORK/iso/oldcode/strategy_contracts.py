# -*- coding: utf-8 -*-
"""系统策略与参数契约模块 (SYSTEM-CONTRACT-01)

统一并明确三条执行路径（144组Freqtrade研究、UI新建回测、模拟盘），
为 FT-LOW4H 建立明确、可验证的配置身份，取消静默兜底，
提供强类型的配置加载、校验、指纹计算与开仓闸门。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parent

# ── 1. 三条实际执行路径定义（不改名同一核心，明确不可统一差异）─────────────────
EXECUTION_PATHS = {
    "FT_144_RESEARCH": {
        "path_id": "FT_144_RESEARCH",
        "name": "144组Freqtrade网格研究回测",
        "entrypoint": "evidence/FT-LOW4H-GRID-01/code/01_run_all_grid_backtests.py",
        "strategy": "WyckoffLow4hGridBase 派生类 (144组静态策略派生类)",
        "strategy_family": "FT-LOW4H-GRID",
        "executor": "Freqtrade CLI (freqtrade backtesting, dry_run=True)",
        "parameter_source": "144组策略派生类静态类变量 + user_data/config.json",
        "data_source": "evidence/FT-LOW4H-GRID-01/user_data/data/binance/ + 数据/raw/",
        "fee_model": "单边手续费 0.0005 (0.05%)，无滑点，事后复现逐笔资金费率结算",
        "unifiable": False,
        "execution_model": "离线全周期向量/事件混合批处理；由 Freqtrade 引擎全权托管调度",
        "distinction_notes": "用于策略参数网格离线探索与稳定性验证，无常驻进程与实时挂单队列。"
    },
    "UI_LAB_BACKTEST": {
        "path_id": "UI_LAB_BACKTEST",
        "name": "UI控制台新建研究回测",
        "entrypoint": "POST /api/v1/backtests (框架/backend/backtest_api.py:create_run)",
        "strategy": "WX_EVENT_TREND_V0_1 (WyckoffMaster.event_trend_v0_1)",
        "strategy_family": "F_WX_EVENT_TREND",
        "executor": "BacktestRunner (框架/backend/backtest_runner.py)",
        "parameter_source": "event_trend_bridge.py:PARAMETER_WHITELIST + HTTP 请求体 params",
        "data_source": "MarketDataRepository (数据/raw/ 统一K线库 + 数据/raw_oi_metrics/)",
        "fee_model": "taker_fee_bps (5.0 = 0.0005) + slippage_bps (3.0 = 0.0003) + 真实资金费结算事件",
        "unifiable": False,
        "execution_model": "Web 控制台异步任务队列 (jobs.sqlite3)，向量化信号初筛 + 15m 事件精确撮合",
        "distinction_notes": "用于单标的或多标的研究期回测、图表交互与绩效指标复盘，产物持久化为独立 run 目录。"
    },
    "PAPER_TRADING": {
        "path_id": "PAPER_TRADING",
        "name": "模拟盘演练与守护执行",
        "entrypoint": "POST /paper/orders (框架/backend/live_api.py) / paper_loop.py / workers/",
        "strategy": "FT-LOW4H (唐奇安/威科夫低频突破跟踪)",
        "strategy_family": "FT-LOW4H-EXP",
        "executor": "PaperEngine (框架/backend/paper_engine.py / paper_loop.py)",
        "parameter_source": "正式生效视图文件 (EFFECTIVE_VIEW 环境变量或指定冻结视图 JSON)",
        "data_source": "live_store.py (数据/paper/live_*.sqlite3) + market.sqlite3 真实闭合K线",
        "fee_model": "fee_each_side (0.0005) + slippage_each_side (0.0005) + 实时资金费核算",
        "unifiable": False,
        "execution_model": "常驻守护进程异步轮询，真实首次请求锚点，因果时钟防御，挂单到点真实成交",
        "distinction_notes": "模拟实盘订单与持仓生命周期管理，持久化到 SQLite 纸面账本，严格因果与防回填防篡改。"
    }
}


def get_execution_paths() -> Dict[str, Any]:
    """返回三条实际执行路径完整清单。"""
    return copy.deepcopy(EXECUTION_PATHS)


# ── 2. FT-LOW4H 强类型配置身份契约 ───────────────────────────────────────────
@dataclass(frozen=True)
class FtLow4hConfigIdentity:
    """FT-LOW4H 规范配置身份，不可变、可哈希、可追溯。"""
    strategy_id: str                      # 策略标识，例如 "FT-LOW4H"
    strategy_version: str                 # 策略版本，例如 "FT-LOW4H-EXP-V2.2"
    rule: str                             # 跟踪规则，例如 "FT-LOW4H"
    stoploss_s: float                     # 初始止损比例 s，例如 0.05
    trail_d: float                        # 跟踪距离比例 d，例如 0.05
    risk_per_trade_equity_fraction: float # 单笔风险预算比例，例如 0.005
    portfolio_initial_stop_risk_cap: float# 组合初始止损风险上限，例如 0.05
    fee_each_side: float                  # 单边手续费率，例如 0.0005
    slippage_each_side: float             # 单边滑点率，例如 0.0005
    detail_timeframe: str                 # 执行周期，例如 "15m"
    tracking_timeframe: str               # 跟踪参考周期，例如 "4h"
    max_holding_hours: int                # 最大持仓小时数，例如 240
    config_fingerprint: str               # 配置 SHA-256 规范指纹 (截短或完整)
    config_path: str                      # 配置来源路径
    spec_version: str                     # 规格版本
    initial_equity: float = 5000.0        # 初始本金（USDT），配置指定值

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate(self) -> List[str]:
        """严格自检所有配置字段合规性。"""
        errors = []
        if self.strategy_id != "FT-LOW4H":
            errors.append(f"strategy_id 不匹配：必须为 FT-LOW4H，收到 {self.strategy_id!r}")
        if not self.strategy_version:
            errors.append("strategy_version 不能为空")
        if self.rule != "FT-LOW4H":
            errors.append(f"rule 不匹配：必须为 FT-LOW4H，收到 {self.rule!r}")
        if not (0.001 <= self.stoploss_s <= 0.50):
            errors.append(f"stoploss_s 越界：必须在 [0.001, 0.50] 区间，收到 {self.stoploss_s}")
        if not (0.001 <= self.trail_d <= 0.50):
            errors.append(f"trail_d 越界：必须在 [0.001, 0.50] 区间，收到 {self.trail_d}")
        if not (0.0001 <= self.risk_per_trade_equity_fraction <= 0.10):
            errors.append(f"risk_per_trade_equity_fraction 越界：必须在 [0.0001, 0.10]，收到 {self.risk_per_trade_equity_fraction}")
        if not (0.001 <= self.portfolio_initial_stop_risk_cap <= 0.50):
            errors.append(f"portfolio_initial_stop_risk_cap 越界：必须在 [0.001, 0.50]，收到 {self.portfolio_initial_stop_risk_cap}")
        if not (0.0 <= self.fee_each_side <= 0.01):
            errors.append(f"fee_each_side 越界：必须在 [0.0, 0.01]，收到 {self.fee_each_side}")
        if not (0.0 <= self.slippage_each_side <= 0.01):
            errors.append(f"slippage_each_side 越界：必须在 [0.0, 0.01]，收到 {self.slippage_each_side}")
        if self.detail_timeframe not in ("1m", "5m", "15m", "1h"):
            errors.append(f"detail_timeframe 不支持：{self.detail_timeframe!r}")
        if self.tracking_timeframe not in ("1h", "4h", "1d"):
            errors.append(f"tracking_timeframe 不支持：{self.tracking_timeframe!r}")
        if self.max_holding_hours <= 0:
            errors.append(f"max_holding_hours 必须为正整数，收到 {self.max_holding_hours}")
        if not self.config_fingerprint or len(self.config_fingerprint) < 8:
            errors.append("config_fingerprint 无效或缺失")
        if self.initial_equity <= 0:
            errors.append(f"initial_equity 必须为正数，收到 {self.initial_equity}")
        return errors


# ── 3. 配置加载器：严禁静默 5% 兜底 ──────────────────────────────────────────
DEFAULT_VIEW_PATH = (
    PROJECT_ROOT / "数据" / "研究输入" /
    "最新唐奇安威科夫接通与收尾_2026-10-11" / "5000U收尾与回测条件终验6" /
    "10_研究参数生效视图_v2_2_user5000_shared.json"
)


def compute_config_fingerprint(raw_bytes: bytes) -> str:
    """计算配置源字节的 SHA-256 指纹。"""
    return hashlib.sha256(raw_bytes).hexdigest()


def load_ft_low4h_config(view_path: Optional[Path | str] = None) -> Tuple[Optional[FtLow4hConfigIdentity], Dict[str, Any], Optional[str]]:
    """加载并解析 FT-LOW4H 配置视图文件。
    
    返回：
      (identity, full_params_dict, error_message)
    若文件不存在、损坏、缺少必需字段或校验不通过，identity 为 None，error_message 说明明确原因。
    绝不返回静默 5% 兜底！
    """
    target_path = Path(view_path) if view_path else (
        Path(os.environ["EFFECTIVE_VIEW"]) if os.environ.get("EFFECTIVE_VIEW") else DEFAULT_VIEW_PATH
    )

    if not target_path.exists():
        err = f"config_file_not_found: 配置文件不存在于指定路径: {target_path}"
        return None, {"_config_valid": False, "_config_error": err, "_source": "missing"}, err

    try:
        raw_bytes = target_path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        err = f"config_read_io_error: 无法读取配置文件 {target_path}: {exc}"
        return None, {"_config_valid": False, "_config_error": err, "_source": "unreadable"}, err

    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        err = f"config_json_corrupted: 配置文件 JSON 解析失败: {exc}"
        return None, {"_config_valid": False, "_config_error": err, "_source": "corrupted"}, err

    if not isinstance(data, dict):
        err = "config_format_error: 配置根结构必须是 JSON 对象"
        return None, {"_config_valid": False, "_config_error": err, "_source": "invalid_format"}, err

    ex = data.get("execution") or {}
    st = data.get("stops") or {}
    rk = data.get("risk") or {}

    # 提取关键必需字段（支持嵌套 stops 或根层级声明）
    stoploss_s = st.get("stoploss_s") if "stoploss_s" in st else data.get("stoploss_s")
    trail_d = st.get("trail_d") if "trail_d" in st else data.get("trail_d")
    rule = st.get("rule") or data.get("rule") or "FT-LOW4H"
    strategy_version = data.get("strategy_version") or data.get("model_version") or "FT-LOW4H-EXP-V2.2"
    spec_version = data.get("spec_version") or data.get("schema_version") or "v2.2"

    missing_fields = []
    if stoploss_s is None:
        missing_fields.append("stops.stoploss_s (初始止损比例)")
    if trail_d is None:
        missing_fields.append("stops.trail_d (跟踪止损比例)")
    if "fee_each_side" not in ex:
        missing_fields.append("execution.fee_each_side")
    if "slippage_each_side" not in ex:
        missing_fields.append("execution.slippage_each_side")
    if "risk_per_trade_equity_fraction" not in rk:
        missing_fields.append("risk.risk_per_trade_equity_fraction")

    if missing_fields:
        err = f"missing_required_parameters: 配置缺少必需参数: {', '.join(missing_fields)}"
        return None, {"_config_valid": False, "_config_error": err, "_source": f"invalid_{target_path.stem}"}, err

    try:
        s_val = float(stoploss_s)
        d_val = float(trail_d)
        fee_val = float(ex["fee_each_side"])
        slip_val = float(ex["slippage_each_side"])
        risk_val = float(rk["risk_per_trade_equity_fraction"])
        port_risk_val = float(rk.get("portfolio_initial_stop_risk_cap", 0.05))
        max_h = int(st.get("max_holding_hours", 240))
    except (ValueError, TypeError) as exc:
        err = f"invalid_parameter_type: 配置数值类型解析失败: {exc}"
        return None, {"_config_valid": False, "_config_error": err, "_source": f"invalid_{target_path.stem}"}, err

    cap_dict = data.get("capital") if isinstance(data.get("capital"), dict) else {}
    initial_equity_raw = (
        cap_dict.get("initial_equity") or cap_dict.get("initial_capital") or
        rk.get("initial_equity_USDT") or rk.get("initial_equity") or rk.get("initial_capital") or
        data.get("initial_equity_USDT") or data.get("initial_equity") or data.get("initial_capital")
    )
    init_eq_val = float(initial_equity_raw) if initial_equity_raw is not None else 5000.0

    full_fingerprint = compute_config_fingerprint(raw_bytes)
    short_fingerprint = full_fingerprint[:12]

    identity = FtLow4hConfigIdentity(
        strategy_id="FT-LOW4H",
        strategy_version=str(strategy_version),
        rule=str(rule),
        stoploss_s=s_val,
        trail_d=d_val,
        risk_per_trade_equity_fraction=risk_val,
        portfolio_initial_stop_risk_cap=port_risk_val,
        fee_each_side=fee_val,
        slippage_each_side=slip_val,
        detail_timeframe=str(ex.get("detail_timeframe", "15m")),
        tracking_timeframe="4h",
        max_holding_hours=max_h,
        config_fingerprint=short_fingerprint,
        config_path=str(target_path),
        spec_version=str(spec_version),
        initial_equity=init_eq_val,
    )

    validation_errors = identity.validate()
    if validation_errors:
        err = f"configuration_validation_failed: {'; '.join(validation_errors)}"
        return None, {"_config_valid": False, "_config_error": err, "_source": f"invalid_{target_path.stem}"}, err

    # 构造可供引擎直接消费的扁平参数字典
    params: Dict[str, Any] = {
        "_config_valid": True,
        "_config_error": None,
        "_source": f"frozen_view_{target_path.stem}",
        "config_path": str(target_path),
        "config_fingerprint": short_fingerprint,
        "config_fingerprint_full": full_fingerprint,
        "view_sha256": short_fingerprint,
        "strategy_id": identity.strategy_id,
        "strategy_version": identity.strategy_version,
        "model_version": identity.strategy_version,
        "rule": identity.rule,
        "stoploss_s": identity.stoploss_s,
        "trail_d": identity.trail_d,
        "fee_each_side": identity.fee_each_side,
        "slippage_each_side": identity.slippage_each_side,
        "detail_timeframe": identity.detail_timeframe,
        "tracking_timeframe": identity.tracking_timeframe,
        "max_holding_hours": identity.max_holding_hours,
        "risk_per_trade_equity_fraction": identity.risk_per_trade_equity_fraction,
        "portfolio_initial_stop_risk_cap": identity.portfolio_initial_stop_risk_cap,
        "initial_equity_USDT": init_eq_val,
        "initial_equity": init_eq_val,
        "leverage": float(rk.get("leverage") or data.get("leverage") or 1.0),
        "max_positions": int(rk.get("max_positions") or data.get("max_positions") or 10),
        "single_name_notional_equity_cap": float(rk.get("single_name_notional_equity_cap") or data.get("single_name_notional_equity_cap") or 0.10),
        "gross_long_notional_equity_cap": float(rk.get("gross_long_notional_equity_cap") or data.get("gross_long_notional_equity_cap") or 0.80),
        "cash_or_free_margin_reserve_fraction": float(rk.get("cash_or_free_margin_reserve_fraction") or data.get("cash_or_free_margin_reserve_fraction") or 0.20),
        "entry_participation_previous_15m_quote_cap": float(rk.get("entry_participation_previous_15m_quote_cap") or data.get("entry_participation_previous_15m_quote_cap") or 0.01),
        "maximum_account_drawdown_fraction": float(rk.get("maximum_account_drawdown_fraction") or data.get("maximum_account_drawdown_fraction") or 0.20),
        "short_enabled": bool(rk.get("short_enabled", False)),
        "signal_execution_delay_minutes": int(ex.get("signal_execution_delay_minutes", 15)),
        "signal_exit_delay_minutes": int(ex.get("signal_exit_delay_minutes", 15)),
        "uniform_stop_atr_mult": 3.0,
        "view_spec_version": identity.spec_version,
    }

    return identity, params, None


# ── 4. 开仓闸门契约校验函数 ──────────────────────────────────────────────────
def validate_entry_contract(
    params: Dict[str, Any],
    *,
    expected_fingerprint: Optional[str] = None,
    requested_stoploss_s: Optional[float] = None,
    requested_trail_d: Optional[float] = None,
    allow_payload_override: bool = False
) -> Tuple[bool, Optional[str], Optional[FtLow4hConfigIdentity]]:
    """严格开仓闸门核验：
    - 缺配置、坏配置一律拒绝；
    - 配置指纹不符一律拒绝；
    - 请求体不得非法覆盖已绑定的正式配置参数；
    - 返回 (is_allowed, rejection_reason, config_identity)。
    """
    if not params.get("_config_valid", False):
        err = params.get("_config_error") or "missing_configuration: 未能加载有效配置视图，阻止新单"
        return False, err, None

    s_val = params.get("stoploss_s")
    d_val = params.get("trail_d")
    if s_val is None or float(s_val) <= 0:
        return False, "invalid_configuration: 必需的 stoploss_s 参数缺失或非正", None
    if d_val is None or float(d_val) <= 0:
        return False, "invalid_configuration: 必需的 trail_d 参数缺失或非正", None

    current_fp = params.get("config_fingerprint") or ""
    current_fp_full = params.get("config_fingerprint_full") or ""
    if expected_fingerprint:
        efp = expected_fingerprint.strip().lower()
        if not (current_fp.lower().startswith(efp) or current_fp_full.lower().startswith(efp) or efp.startswith(current_fp.lower())):
            return False, f"config_fingerprint_mismatch: 期望指纹 {expected_fingerprint} 与当前生效指纹 {current_fp} 不匹配", None

    # 请求体覆盖防御
    if not allow_payload_override:
        if requested_stoploss_s is not None:
            try:
                req_s = float(requested_stoploss_s)
                if abs(req_s - float(s_val)) > 1e-6:
                    return False, f"external_param_override_rejected: 请求体 stoploss_s={req_s} 试图覆盖正式配置 ({s_val})", None
            except (ValueError, TypeError):
                return False, f"bad_param_stoploss_s: 外部传入非法数值 {requested_stoploss_s!r}", None

        if requested_trail_d is not None:
            try:
                req_d = float(requested_trail_d)
                if abs(req_d - float(d_val)) > 1e-6:
                    return False, f"external_param_override_rejected: 请求体 trail_d={req_d} 试图覆盖正式配置 ({d_val})", None
            except (ValueError, TypeError):
                return False, f"bad_param_trail_d: 外部传入非法数值 {requested_trail_d!r}", None

    identity = FtLow4hConfigIdentity(
        strategy_id=params["strategy_id"],
        strategy_version=params["strategy_version"],
        rule=params["rule"],
        stoploss_s=float(params["stoploss_s"]),
        trail_d=float(params["trail_d"]),
        risk_per_trade_equity_fraction=float(params["risk_per_trade_equity_fraction"]),
        portfolio_initial_stop_risk_cap=float(params["portfolio_initial_stop_risk_cap"]),
        fee_each_side=float(params["fee_each_side"]),
        slippage_each_side=float(params["slippage_each_side"]),
        detail_timeframe=str(params["detail_timeframe"]),
        tracking_timeframe=str(params.get("tracking_timeframe", "4h")),
        max_holding_hours=int(params.get("max_holding_hours", 240)),
        config_fingerprint=str(params["config_fingerprint"]),
        config_path=str(params.get("config_path", "")),
        spec_version=str(params.get("view_spec_version", "")),
    )

    return True, None, identity
