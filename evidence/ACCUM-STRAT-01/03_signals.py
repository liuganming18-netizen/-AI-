#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第3步：生成信号样本。

S0 = 字面规则（振幅<35% 且 30日主动买/卖>1.15）——工单原文口径
S1 = 替代样本（仅振幅<35%）——买量条件在本数据上不可复现时的降级样本，
     只用于检验 T+5 机制，**不代表原策略**。

数据：本地 1h（币安 USDT 永续 futures/um）聚合成日线，已与官方 1d 归档逐位核对一致。
"""
import os, json
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
CACHE = os.path.join(EV, 'cache')
OUT = os.path.join(EV, 'out')
os.makedirs(OUT, exist_ok=True)

WINDOWS = {'G1': ('2022-11-01', '2023-02-28'), 'G2': ('2023-11-01', '2024-02-29'),
           'G3': ('2024-11-01', '2025-02-28'), 'G4': ('2025-11-01', '2026-02-28')}
LOOK, RNG_TH, VR_TH, DEDUP = 30, 0.35, 1.15, 10


def scan(use_vr=True):
    rows = []
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith('.parquet') or f.startswith('_'):
            continue
        sym = f[:-8]
        d = pd.read_parquet(os.path.join(CACHE, f))
        d['day'] = pd.to_datetime(d['day']).dt.tz_localize(None)
        d = d[d['bars'] == 24].sort_values('day').reset_index(drop=True)
        if len(d) < LOOK + 2:
            continue
        hi30 = d['high'].rolling(LOOK).max()
        lo30 = d['low'].rolling(LOOK).min()
        rng = hi30 / lo30 - 1.0
        tb30 = d['taker_buy'].rolling(LOOK).sum()
        tv30 = d['volume'].rolling(LOOK).sum()
        vr = tb30 / (tv30 - tb30)
        m = (rng < RNG_TH)
        if use_vr:
            m &= (vr > VR_TH)
        for g, (lo, hi) in WINDOWS.items():
            sel = d.loc[m & (d['day'] >= pd.Timestamp(lo)) & (d['day'] <= pd.Timestamp(hi))]
            last = None
            for i in sel.index:
                t = d.at[i, 'day']
                if last is not None and (t - last).days < DEDUP:
                    continue
                last = t
                rows.append({
                    'group': g, 'symbol': sym, 'signal_day': t.strftime('%Y-%m-%d'),
                    'rng30': float(rng.iat[i]), 'vr30': float(vr.iat[i]),
                    'hi30': float(hi30.iat[i]), 'lo30': float(lo30.iat[i]),
                    'close_signal': float(d.at[i, 'close']),
                    'vol30': float(tv30.iat[i]), 'tb30': float(tb30.iat[i]),
                })
    return pd.DataFrame(rows)


def main():
    s0 = scan(True)
    s0.to_csv(os.path.join(OUT, '03_signals_S0_literal.csv'), index=False)
    print('S0 literal', len(s0), s0.groupby('group').size().to_dict(), flush=True)
    s1 = scan(False)
    s1.to_csv(os.path.join(OUT, '03_signals_S1_range_only.csv'), index=False)
    print('S1 range-only', len(s1), s1.groupby('group').size().to_dict(), flush=True)
    print('S1 symbols', s1.symbol.nunique())
    print(s1.head(5).to_string())


if __name__ == '__main__':
    main()
