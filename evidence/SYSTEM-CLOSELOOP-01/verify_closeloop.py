#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-CLOSELOOP-01: 端到端模拟交易闭环验证脚本。

链路验证：
1. 注入/确认有效信号 (live_signal)
2. 检查开仓总闸 (allow_new_entries=True)
3. 模拟下单开仓 (POST /api/v1/paper/orders)
4. 验证模拟撮合成交与产生持仓 (paper_order -> paper_fill -> paper_position)
5. 止损/持仓态验证与获取 position_id
6. 模拟平仓 (POST /api/v1/paper/orders action=close)
7. 账本结算核对 (paper_realized, paper_state, paper_equity)
8. 输出稳定的链路关联标识：
   strategy_version -> signal_id -> order_id -> fill_id -> position_id -> ledger_event_id
"""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

# 禁用代理，防止本机回环走系统代理
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

BASE_URL = "http://127.0.0.1:8787"
PROJ_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
LIVE_DB = PROJ_ROOT / "框架" / "backend" / "live" / "live_signals.sqlite3"
PAPER_DB = PROJ_ROOT / "数据" / "paper" / "paper_3a.sqlite3"

def http_post(path, data):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def http_get(path):
    url = f"{BASE_URL}{path}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("=== SYSTEM-CLOSELOOP-01 交易链路闭环测试 ===")
    
    # 1. 开启新开仓总闸
    print("\n[Step 1] 开启开仓总闸...")
    gate_res = http_post("/api/v1/paper/toggle_gate", {"action": "start"})
    print("  总闸状态:", gate_res.get("items", [{}])[0].get("allow_new_entries"))

    # 2. 准备信号时间与最新 15m K线时间对齐
    now_ms = int(time.time() * 1000)
    # 找到最新已闭合的 15m bar
    mkt_db = PROJ_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
    with sqlite3.connect(mkt_db) as mcon:
        mcon.row_factory = sqlite3.Row
        cur = mcon.cursor()
        bar = cur.execute(
            "SELECT open_time, close_time, open, close FROM candles "
            "WHERE symbol='BTCUSDT' AND timeframe='15m' AND is_closed=1 "
            "ORDER BY open_time DESC LIMIT 1"
        ).fetchone()
        latest_bar_open = int(bar["open_time"])
        print(f"  最新已闭合 15m bar: open_time={latest_bar_open}, open={bar['open']}, close={bar['close']}")

    # 信号时刻设为该 bar 前 15 分钟（即 10:45），这样 +15m 延迟恰好等于 latest_bar_open
    sig_event_time = latest_bar_open - 15 * 60 * 1000
    signal_uid = f"sig_closeloop_btc_{int(time.time())}"
    strategy_version = "WX_EVENT_TREND_V0_1"

    print(f"\n[Step 2] 写入并激活实时入场信号: {signal_uid}...")
    with sqlite3.connect(LIVE_DB) as lcon:
        payload = {
            "role": "enterable_arm_entry",
            "arm": "3a",
            "token_symbol": "BTC",
            "raw_symbol": "BTCUSDT",
            "strategy": strategy_version
        }
        lcon.execute(
            "INSERT OR REPLACE INTO live_signal("
            "signal_uid, source, origin, exchange, market, symbol, timeframe, "
            "strategy_key, strategy_version, direction, event_time, confirmed_at, "
            "available_at, bar_open_time, bar_close_time, detection_low, detection_atr, "
            "display_state, executable_state, admission, payload_json, first_seen_at, updated_at) "
            "VALUES(?, 'live_detector', 'live', 'binance', 'um', 'BTCUSDT', '1h', "
            "'wx_event_trend_v0_1', ?, 'long', ?, ?, ?, ?, ?, ?, ?, 'displayable', 'enterable', 1, ?, ?, ?)",
            (
                signal_uid, strategy_version,
                sig_event_time, sig_event_time, sig_event_time,
                sig_event_time - 3600 * 1000, sig_event_time - 1,
                81000.0, 500.0,
                json.dumps(payload), now_ms, now_ms
            )
        )
        lcon.commit()

    # 验证信号接口
    sig_resp = http_get(f"/api/v1/signals/{signal_uid}")
    print(f"  信号接口核验: uid={sig_resp.get('signal_uid')}, exec_state={sig_resp.get('executable_state')}")

    # 3. 发起开仓订单
    print(f"\n[Step 3] 下达模拟开仓订单 (POST /api/v1/paper/orders)...")
    open_payload = {
        "action": "open",
        "arm": "3a",
        "symbol": "BTCUSDT",
        "direction": "long",
        "atr": 500.0,
        "signal_uid": signal_uid,
        "event_time": sig_event_time,
        "available_at": sig_event_time,
        "requested_at": sig_event_time,
        "now_ms": latest_bar_open + 60000,
    }
    open_resp = http_post("/api/v1/paper/orders", open_payload)
    item = open_resp.get("items", [{}])[0]
    print(f"  开仓结果: ok={item.get('ok')}, reason={item.get('reason')}")
    position_id = item.get("position_id")
    order_id = item.get("order_id")
    fill_price = item.get("entry_price")
    qty = item.get("qty")
    stop_price = item.get("stop_price")
    print(f"  成交详情: position_id={position_id}, order_id={order_id}, fill_price={fill_price}, qty={qty}, stop_price={stop_price}")

    if not item.get("ok"):
        print("❌ 开仓未直接成交，检查是否挂单:", item)
        return

    # 4. 从 SQLite 查询 fill 和 position 详情
    with sqlite3.connect(PAPER_DB) as pcon:
        pcon.row_factory = sqlite3.Row
        fill_row = pcon.execute("SELECT * FROM paper_fill WHERE position_id=?", (position_id,)).fetchone()
        fill_id = fill_row["fill_id"] if fill_row else "unknown"
        pos_row = pcon.execute("SELECT * FROM paper_position WHERE position_id=?", (position_id,)).fetchone()
        print(f"  账本核对: fill_id={fill_id}, position_id={pos_row['position_id']}, status={pos_row['status']}")

    # 5. 前端 API 验证持仓存在
    print("\n[Step 4] 前端 API 持仓查询 (/api/v1/paper/positions)...")
    pos_api = http_get("/api/v1/paper/positions")
    found_pos = [p for p in pos_api.get("items", []) if p.get("position_id") == position_id]
    print(f"  API 返回持仓数: {len(pos_api.get('items', []))}, 命中当前 position_id={len(found_pos)}")

    # 6. 发起平仓
    print(f"\n[Step 5] 执行模拟平仓 (POST /api/v1/paper/orders action=close)...")
    close_payload = {
        "action": "close",
        "arm": "3a",
        "position_id": position_id,
        "reason": "manual_closeloop_test"
    }
    close_resp = http_post("/api/v1/paper/orders", close_payload)
    close_item = close_resp.get("items", [{}])[0]
    print(f"  平仓结果: ok={close_item.get('ok')}, reason={close_item.get('reason')}, exit_price={close_item.get('exit_price')}, net_pnl={close_item.get('net_pnl')}")

    # 7. 账本核验已实现盈亏与事件
    with sqlite3.connect(PAPER_DB) as pcon:
        pcon.row_factory = sqlite3.Row
        real_row = pcon.execute("SELECT * FROM paper_realized WHERE position_id=?", (position_id,)).fetchone()
        ledger_event_id = real_row["id"] if real_row else "unknown"
        net_pnl = real_row["net_pnl"] if real_row else 0.0
        state_row = pcon.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone()
        pos_after = pcon.execute("SELECT status FROM paper_position WHERE position_id=?", (position_id,)).fetchone()
        print(f"  结算确认: ledger_event_id={ledger_event_id}, net_pnl={net_pnl}, 持仓状态={pos_after['status']}, 账户现金={state_row['cash']}")

    # 8. 闭环链路标识溯源链总结
    print("\n" + "="*50)
    print("🎯 完整交易链路可追溯标识闭环:")
    trace = {
        "strategy_version": strategy_version,
        "signal_id": signal_uid,
        "order_id": order_id,
        "fill_id": fill_id,
        "position_id": position_id,
        "ledger_event_id": ledger_event_id,
        "symbol": "BTCUSDT",
        "qty": qty,
        "fill_price": fill_price,
        "exit_price": close_item.get("exit_price"),
        "net_pnl": net_pnl,
        "status": pos_after["status"]
    }
    print(json.dumps(trace, indent=2, ensure_ascii=False))
    print("="*50)
    
    # 写入证据文件
    out_file = PROJ_ROOT / "evidence" / "SYSTEM-CLOSELOOP-01" / "02_trading_closeloop_trace.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)
    print(f"✅ 链路闭环证据已保存至: {out_file}")

if __name__ == "__main__":
    main()
