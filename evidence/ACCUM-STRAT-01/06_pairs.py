#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第6步：A/B 逐笔配对表 + 汇总 JSON + 抽样人工复核。"""
import os, json
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
OUT = os.path.join(EV, 'out')


def pairs(tag='S1_open', arm='B1'):
    df = pd.read_csv(os.path.join(OUT, f'04_trades_{tag}.csv'))
    df = df[df['status'] == 'OK'].copy()
    df['delta'] = df[f'{arm}_net'] - df['A_net']
    df['category'] = np.where(df['delta'] > 1e-9, '改善',
                              np.where(df['delta'] < -1e-9, '错杀', '不变'))
    cols = ['group', 'symbol', 'signal_day', 'rng30', 'vr30', 'close_signal', 'entry_price',
            'exit_reason', 'exit_price', 'exit_time', 'A_net', 'A_hold_days',
            f'{arm}_exit_reason', f'{arm}_exit_price', f'{arm}_exit_time', f'{arm}_net',
            f'{arm}_hold_days', 't5_time', 't5_close', 't5_ret', 'delta', 'category']
    p = df[cols].sort_values(['category', 'delta'])
    p.to_csv(os.path.join(OUT, f'06_pairs_{tag}_{arm}.csv'), index=False)
    return df, p


def summary_json(df):
    d = df['delta']
    gain, loss = d[d > 1e-9], d[d < -1e-9]
    res = {
        '样本': 'S1（仅振幅条件，替代样本）',
        '笔数': int(len(df)),
        'A_净均': float(df['A_net'].mean()),
        'B1_净均': float(df['B1_net'].mean()),
        '配对净改善_每笔': float(d.mean()),
        '配对净改善_合计': float(d.sum()),
        '改善笔数': int(len(gain)), '错杀笔数': int(len(loss)),
        '提前止损少亏合计': float(gain.sum()), '错杀放弃盈利合计': float(loss.sum()),
        '改善笔均': float(gain.mean()) if len(gain) else 0.0,
        '错杀笔均': float(loss.mean()) if len(loss) else 0.0,
        '盈亏平衡所需命中比': float(abs(loss.mean()) / gain.mean()) if len(gain) and len(loss) and gain.mean() else None,
        '实际命中比': float(len(gain) / len(loss)) if len(loss) else None,
    }
    return res


def spot_check(df, n=3):
    """抽 n 笔，回原始 15m 逐根核对入场价与出场价。"""
    lines = []
    for _, r in df.head(n).iterrows():
        b = pd.read_parquet(os.path.join(ROOT, '数据/研究输入/WyckoffMaster/ohlcv/15m', r['symbol'] + '.parquet'),
                            columns=['open_time', 'open', 'high', 'low', 'close'])
        entry_ms = int(pd.Timestamp(r['signal_day']).timestamp() * 1000) + 86400_000
        row = b[b['open_time'] == entry_ms]
        lines.append(f"{r['symbol']} {r['signal_day']} 信号收盘={r['close_signal']:.8f} "
                     f"次日00:00开盘(原始)={float(row['open'].iloc[0]):.8f} 表内入场={r['entry_price']:.8f} "
                     f"出场={r['exit_reason']} {r['exit_price']:.8f} 净={r['A_net']:+.4%}")
    return lines


if __name__ == '__main__':
    df, p = pairs()
    res = summary_json(df)
    with open(os.path.join(OUT, '06_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print('\n'.join(spot_check(df)))
    print('\n错杀明细:')
    print(p[p['category'] == '错杀'][['symbol', 'group', 'signal_day', 'A_net', 'B1_net', 'delta',
                                    'exit_reason', 'B1_exit_reason']].to_string(index=False))
