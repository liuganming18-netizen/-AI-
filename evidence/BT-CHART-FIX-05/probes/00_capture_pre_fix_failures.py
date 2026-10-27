import json
import urllib.parse
import sys
from pathlib import Path

# 添加框架路径
sys.path.insert(0, str(Path("框架/backend").resolve()))
from backtest_api import get_chart, _CHART_TF_ALLOWED

failures = {}

# 1. 检验 30m 在后端的支持状态
test_run = "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02"
chart_30m, code_30m = get_chart(test_run, {"symbol": "BCH", "timeframe": "30m"})
failures["timeframe_30m_backend_support"] = {
    "allowed_tfs": list(_CHART_TF_ALLOWED),
    "status_code": code_30m,
    "missing_reason": chart_30m.get("missing_reason"),
    "is_rejected_400": code_30m == 400,
}

# 2. 检验默认获取的 chart 数据周期（没有传 timeframe 时）
chart_default, code_def = get_chart(test_run, {"symbol": "BCH"})
c_def = chart_default.get("chart", {})
candles = c_def.get("candles", [])
first_diff = (candles[1]["t"] - candles[0]["t"]) if len(candles) > 1 else None
failures["default_chart_timeframe_mismatch"] = {
    "status_code": code_def,
    "exec_timeframe": c_def.get("exec_timeframe"),
    "display_timeframe": c_def.get("display_timeframe"),
    "candle_count": len(candles),
    "first_candle_step_ms": first_diff,
    "step_is_1h": first_diff == 3600000,
    "description": "后端返回 candles 为 1h (3600000ms)，但 exec_timeframe 标为 15m，导致前端以为蜡烛是 15m 并做错误聚合"
}

# 3. 检验同一小时内多笔事件覆盖情况（如 BCH 或 CHZ 订单）
import backtest_store as S
trades = S.read_ledger_rows(test_run, "trades")
orders = S.read_ledger_rows(test_run, "orders")

# 统计同一小时内的订单
orders_by_hour = {}
for o in orders:
    ts = o.get("ts_ms") or o.get("created_at") or 0
    hr = ts // 3600000
    orders_by_hour.setdefault(hr, []).append(o)

same_hour_collisions = {k: [x.get("order_id") for x in v] for k, v in orders_by_hour.items() if len(v) > 1}
failures["same_hour_events_collision"] = {
    "total_orders": len(orders),
    "collision_hours_count": len(same_hour_collisions),
    "sample_collision": dict(list(same_hour_collisions.items())[:3]),
    "risk": "前端原实现用 Map<number, string> 按小时键存储，同小时多个事件直接覆盖导致反查只能查到最后一条"
}

out_path = Path("evidence/BT-CHART-FIX-05/out/pre_fix_failures.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Pre-fix failures captured to {out_path}:")
print(json.dumps(failures, indent=2, ensure_ascii=False))
