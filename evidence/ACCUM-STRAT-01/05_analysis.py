#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第5步：分组统计、赢家集中度、次日开盘与信号日收盘的价差实测。"""
import os
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
OUT = os.path.join(EV, 'out')
LOGS = os.path.join(EV, 'logs')
os.makedirs(LOGS, exist_ok=True)


def gap_analysis():
    rows = []
    for tag in ('S0_open', 'S1_open'):
        df = pd.read_csv(os.path.join(OUT, f'04_trades_{tag}.csv'))
        df['gap'] = df['entry_price'] / df['close_signal'] - 1
        for g, d in df.groupby('group'):
            rows.append({'sample': tag, 'group': g, 'n': len(d),
                         'gap_mean': d['gap'].mean(), 'gap_median': d['gap'].median(),
                         'gap_abs_mean': d['gap'].abs().mean(),
                         'gap_p05': d['gap'].quantile(.05), 'gap_p95': d['gap'].quantile(.95),
                         'gap_min': d['gap'].min(), 'gap_max': d['gap'].max()})
        rows.append({'sample': tag, 'group': 'ALL', 'n': len(df),
                     'gap_mean': df['gap'].mean(), 'gap_median': df['gap'].median(),
                     'gap_abs_mean': df['gap'].abs().mean(),
                     'gap_p05': df['gap'].quantile(.05), 'gap_p95': df['gap'].quantile(.95),
                     'gap_min': df['gap'].min(), 'gap_max': df['gap'].max()})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, '05_gap_nextopen_vs_signalclose.csv'), index=False)
    return out


def group_summary():
    rows = []
    for tag in ('S1_open', 'S1_close'):
        df = pd.read_csv(os.path.join(OUT, f'04_trades_{tag}.csv'))
        df = df[df['status'] == 'OK']
        for g in ['G1', 'G2', 'G3', 'G4', 'ALL']:
            d = df if g == 'ALL' else df[df['group'] == g]
            if not len(d):
                continue
            for arm in ('A', 'B1', 'B2', 'B3'):
                s = d[f'{arm}_net'].dropna()
                if not len(s):
                    continue
                rows.append({'sample': tag, 'group': g, 'arm': arm, 'n': len(s),
                             'net_mean': s.mean(), 'net_median': s.median(),
                             'win_rate': (s > 0).mean(),
                             'hold_median': d[f'{arm}_hold_days'].median(),
                             'hold_mean': d[f'{arm}_hold_days'].mean(),
                             'pf': s[s > 0].sum() / abs(s[s < 0].sum()) if (s < 0).any() else np.nan,
                             'sum': s.sum()})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, '05_group_summary.csv'), index=False)
    return out


def concentration():
    rows = []
    for tag in ('S1_open',):
        df = pd.read_csv(os.path.join(OUT, f'04_trades_{tag}.csv'))
        df = df[df['status'] == 'OK']
        for g in ['G1', 'G2', 'G3', 'G4', 'ALL']:
            d = df if g == 'ALL' else df[df['group'] == g]
            for arm in ('A', 'B1'):
                s = d[f'{arm}_net'].dropna().sort_values(ascending=False)
                tot = s[s > 0].sum()
                rec = {'sample': tag, 'group': g, 'arm': arm, 'n': len(s),
                       'gross_profit': tot, 'gross_loss': abs(s[s < 0].sum()),
                       'n_win': int((s > 0).sum()), 'n_loss': int((s < 0).sum())}
                for q in (0.01, 0.05, 0.10):
                    k = max(1, int(round(len(s) * q)))
                    rec[f'top{int(q*100)}pct_share_of_profit'] = s.head(k).sum() / tot if tot else np.nan
                rec['max_single_share_of_profit'] = s.iloc[0] / tot if tot else np.nan
                rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, '05_winner_concentration.csv'), index=False)
    return out


if __name__ == '__main__':
    g = gap_analysis()
    gs = group_summary()
    c = concentration()
    with open(os.path.join(LOGS, '05_analysis.txt'), 'w', encoding='utf-8') as f:
        f.write('== 次日开盘 vs 信号日收盘 价差 ==\n')
        f.write(g.to_string(index=False) + '\n\n')
        f.write('== 分组统计 ==\n')
        f.write(gs.to_string(index=False) + '\n\n')
        f.write('== 赢家集中度 ==\n')
        f.write(c.to_string(index=False) + '\n')
    print(g.to_string(index=False))
    print(gs[gs['arm'].isin(['A', 'B1'])].to_string(index=False))
    print(c.to_string(index=False))
