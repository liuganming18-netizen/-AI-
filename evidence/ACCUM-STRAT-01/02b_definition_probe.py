#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第2步b：口径复原探针 v2（一次加载，多口径并行评估）。
对照目标：G1=196 G2=186 G3=34 G4=61（来自 Downloads/回测数据汇总表.md）。
"""
import os, json, itertools
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
CACHE = os.path.join(EV, 'cache')
OUT = os.path.join(EV, 'out')

WINDOWS = {'G1': ('2022-11-01', '2023-02-28'), 'G2': ('2023-11-01', '2024-02-29'),
           'G3': ('2024-11-01', '2025-02-28'), 'G4': ('2025-11-01', '2026-02-28')}
TARGET = {'G1': 196, 'G2': 186, 'G3': 34, 'G4': 61}
LOOK = 30


def load_all():
    data = {}
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith('.parquet') or f.startswith('_'):
            continue
        s = f[:-8]
        d = pd.read_parquet(os.path.join(CACHE, f))
        d['day'] = pd.to_datetime(d['day']).dt.tz_localize(None)
        d = d[d['bars'] == 24].sort_values('day').reset_index(drop=True)
        if len(d) >= LOOK + 5:
            data[s] = d
    return data


def range_series(d, mode):
    if mode == 'hl_low':
        return (d['high'].rolling(LOOK).max() - d['low'].rolling(LOOK).min()) / d['low'].rolling(LOOK).min()
    if mode == 'hl_close':
        return (d['high'].rolling(LOOK).max() - d['low'].rolling(LOOK).min()) / d['close']
    if mode == 'cc_low':
        return (d['close'].rolling(LOOK).max() - d['close'].rolling(LOOK).min()) / d['close'].rolling(LOOK).min()
    raise ValueError(mode)


def vr_series(d, mode):
    tb, vol = d['taker_buy'], d['volume']
    if mode == 'taker_30d':
        return tb.rolling(LOOK).sum() / (vol.rolling(LOOK).sum() - tb.rolling(LOOK).sum())
    if mode == 'taker_1d':
        return tb / (vol - tb)
    if mode == 'updown_30d':
        up = vol.where(d['close'] >= d['open'], 0.0).rolling(LOOK).sum()
        dn = vol.where(d['close'] < d['open'], 0.0).rolling(LOOK).sum()
        return up / dn.replace(0, np.nan)
    if mode == 'updown_cnt':
        up = (d['close'] >= d['open']).astype(float).rolling(LOOK).sum()
        return up / (LOOK - up)
    raise ValueError(mode)


def main():
    data = load_all()
    print('symbols loaded', len(data), flush=True)
    rows = []
    for rm, vm in itertools.product(('hl_low', 'hl_close', 'cc_low'),
                                    ('taker_30d', 'taker_1d', 'updown_30d', 'updown_cnt')):
        rng_cache = {s: range_series(d, rm) for s, d in data.items()}
        vr_cache = {s: vr_series(d, vm) for s, d in data.items()}
        per = {}
        for g, (lo, hi) in WINDOWS.items():
            n = 0
            for s, d in data.items():
                m = (rng_cache[s] < 0.35) & (vr_cache[s] > 1.15)
                cand = d.loc[m, 'day']
                cand = cand[(cand >= pd.Timestamp(lo)) & (cand <= pd.Timestamp(hi))]
                last = None
                for t in cand:
                    if last is None or (t - last).days >= 10:
                        n += 1
                        last = t
            per[g] = n
        row = {'rng': rm, 'vr': vm, **per, 'total': sum(per.values()),
               'sum_abs_diff': sum(abs(per[g] - TARGET[g]) for g in WINDOWS)}
        rows.append(row)
        print(row, flush=True)
    df = pd.DataFrame(rows).sort_values('sum_abs_diff')
    df.to_csv(os.path.join(OUT, '02_definition_probe_v2.csv'), index=False)
    print(df.to_string(index=False))


if __name__ == '__main__':
    main()
