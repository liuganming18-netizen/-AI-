# -*- coding: utf-8 -*-
import json
import pandas as pd
import numpy as np

# 1. 提取支持样例全过程的逐 bar 数据
df = pd.read_csv('evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/target_event_step_audit.csv')

# 筛选出属于目标事件的行
target_eid = "ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1716364800000"
df_target = df[df['event_id'] == target_eid].copy()

# 增加北京时间列
df_target['bar_close_bj'] = pd.to_datetime(df_target['close_ms'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
df_target['bar_open_bj'] = pd.to_datetime(df_target['timestamp_ms'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')

# 输出目标事件的关键步骤精简表
key_steps = df_target[df_target['transition'] != '无迁移'][
    ['bar_close_bj', 'close_ms', 'state_before', 'state_after', 'transition', 'open', 'high', 'low', 'close', 'volume', 'box_H', 'box_L', 'box_A']
]
key_steps.to_csv('evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/support_sample_key_steps.csv', index=False, encoding='utf-8-sig')
print("支持样例关键步骤已保存:", len(key_steps))

# 2. 失败样例（ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1705492800000）
fail_eid = "ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1705492800000"
df_fail = df[df['event_id'] == fail_eid].copy()
df_fail['bar_close_bj'] = pd.to_datetime(df_fail['close_ms'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
key_fail = df_fail[df_fail['transition'] != '无迁移'][
    ['bar_close_bj', 'close_ms', 'state_before', 'state_after', 'transition', 'open', 'high', 'low', 'close', 'volume', 'box_H', 'box_L', 'box_A']
]
key_fail.to_csv('evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/failure_sample_key_steps.csv', index=False, encoding='utf-8-sig')
print("失败样例关键步骤已保存:", len(key_fail))

# 3. 未触发样例（ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1704081600000）
untrig_eid = "ev_WX_EVENT_TREND_V0_1_binance_ORDIUSDT_1704081600000"
df_untrig = df[df['event_id'] == untrig_eid].copy()
df_untrig['bar_close_bj'] = pd.to_datetime(df_untrig['close_ms'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
key_untrig = df_untrig[df_untrig['transition'] != '无迁移'][
    ['bar_close_bj', 'close_ms', 'state_before', 'state_after', 'transition', 'open', 'high', 'low', 'close', 'volume', 'box_H', 'box_L', 'box_A']
]
key_untrig.to_csv('evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/untriggered_sample_key_steps.csv', index=False, encoding='utf-8-sig')
print("未触发样例关键步骤已保存:", len(key_untrig))
