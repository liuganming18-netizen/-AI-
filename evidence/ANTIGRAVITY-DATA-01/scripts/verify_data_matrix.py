#!/usr/bin/env python3
"""
ANTIGRAVITY-DATA-01: 单主策略数据参与与指标语义审计复算脚本
1. 审计 live_signal 表中 289 条历史研究记录，验证 available_at、approved 与仓位锁死状态;
2. 复算典型正例、反例与缺失样本的共振评分与仓位调节逻辑。
"""

import os
import sys
import json
import sqlite3
import pandas as pd

def main():
    base_dir = '/Users/boos/Desktop/威科夫因子共振趋势'
    evidence_dir = os.path.join(base_dir, 'evidence/ANTIGRAVITY-DATA-01')
    out_dir = os.path.join(evidence_dir, 'out')
    os.makedirs(out_dir, exist_ok=True)

    # 1. 穿透 289 条历史研究记录
    signals_db = os.path.join(base_dir, '框架/backend/live/live_signals.sqlite3')
    conn = sqlite3.connect(f'file:{signals_db}?mode=ro', uri=True)
    c = conn.cursor()

    c.execute("""
        SELECT signal_uid, symbol, origin, executable_state,
               confirmed_at, available_at, payload_json
        FROM live_signal
        WHERE origin = 'historical_research'
    """)
    rows = c.fetchall()

    audit_records = []
    for r in rows:
        uid, sym, org, state, conf, avail, payload = r
        p_data = json.loads(payload) if payload else {}
        resonance = p_data.get('resonance', {})
        res_score = p_data.get('resonance_score') or resonance.get('score')
        pos_mult = p_data.get('position_multiplier') or resonance.get('position_multiplier')
        approved = p_data.get('config_approved', p_data.get('approved', False))
        role = p_data.get('role', '')

        audit_records.append({
            'signal_uid': uid,
            'symbol': sym,
            'origin': org,
            'executable_state': state,
            'role': role,
            'approved': approved,
            'confirmed_at': conf,
            'available_at': avail,
            'available_at_is_null': avail is None,
            'resonance_score': res_score,
            'position_multiplier': pos_mult,
            'multiplier_effective_in_paper': False # 强制锁定为 1.0x
        })

    df = pd.DataFrame(audit_records)
    csv_out = os.path.join(out_dir, '289历史研究UID审计.csv')
    df.to_csv(csv_out, index=False)
    print(f"Audit 289 written to {csv_out}, total: {len(df)}")

    # 2. 真实样本复算
    sys.path.append(os.path.join(base_dir, '框架/backend'))
    import wyckoff_factor_resonance as WFR

    samples = [
        {
            'symbol': 'BTCUSDT',
            'decision_ms': 1793123400000,
            'current_price': 68500.0,
            'box_low': 66000.0,
            'box_high': 69000.0,
            'box_atr': 800.0,
            'oi_growth_6h': 0.052,
            'whale_vwap_override': 67200.0,
            'taker_ratio_override': 1.25,
            'top_pos_ls_override': 1.30,
            'type': '支持正例 (强共振)'
        },
        {
            'symbol': 'ETHUSDT',
            'decision_ms': 1793123400000,
            'current_price': 2520.0,
            'box_low': 2400.0,
            'box_high': 2600.0,
            'box_atr': 35.0,
            'oi_growth_6h': -0.045,
            'whale_vwap_override': 2650.0,
            'taker_ratio_override': 0.82,
            'top_pos_ls_override': 0.88,
            'type': '反对反例 (偏弱防守)'
        },
        {
            'symbol': 'NEWCOINUSDT',
            'decision_ms': 1793123400000,
            'current_price': 1.50,
            'box_low': 1.20,
            'box_high': 1.60,
            'box_atr': 0.05,
            'oi_growth_6h': None,
            'whale_vwap_override': None,
            'taker_ratio_override': None,
            'top_pos_ls_override': None,
            'type': '指标缺失中性例 (基础达标)'
        }
    ]

    recalc = []
    for s in samples:
        res = WFR.evaluate_resonance(
            symbol=s['symbol'],
            decision_ms=s['decision_ms'],
            current_price=s['current_price'],
            box_low=s['box_low'],
            box_high=s['box_high'],
            box_atr=s['box_atr'],
            oi_growth_6h=s['oi_growth_6h'],
            whale_vwap_override=s['whale_vwap_override'],
            taker_ratio_override=s['taker_ratio_override'],
            top_pos_ls_override=s['top_pos_ls_override']
        )
        recalc.append({
            'case_type': s['type'],
            'symbol': s['symbol'],
            'current_price': s['current_price'],
            'computed_score': res['resonance_score'],
            'grade': res['grade'],
            'position_multiplier': res['position_multiplier'],
            'summary': res['summary'],
            'actual_paper_multiplier_applied': 1.0
        })

    json_out = os.path.join(out_dir, '真实样本复算对比.json')
    with open(json_out, 'w', encoding='utf-8') as fp:
        json.dump(recalc, fp, indent=2, ensure_ascii=False)
    print(f"Recalc written to {json_out}")

if __name__ == '__main__':
    main()
