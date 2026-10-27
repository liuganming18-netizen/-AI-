#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第0步：把本地 1h 现货 K 线聚合成日线，落缓存。
只读生产数据，只写 evidence/ACCUM-STRAT-01/cache/。
"""
import os, sys, json, time
import pandas as pd
import pyarrow.parquet as pq

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
SRC = os.path.join(ROOT, '数据/研究输入/WyckoffMaster/ohlcv/1h')
OUT = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01/cache')
os.makedirs(OUT, exist_ok=True)

def build(sym):
    p = os.path.join(SRC, sym + '.parquet')
    df = pq.read_table(p, columns=['open_time','open','high','low','close','volume','taker_buy_volume','is_closed']).to_pandas()
    df = df[df['is_closed'] == 1]
    if df.empty:
        return None
    dt = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.assign(day=dt.dt.floor('D'))
    g = df.groupby('day', sort=True)
    daily = pd.DataFrame({
        'open': g['open'].first(),
        'high': g['high'].max(),
        'low': g['low'].min(),
        'close': g['close'].last(),
        'volume': g['volume'].sum(),
        'taker_buy': g['taker_buy_volume'].sum(),
        'bars': g['close'].size(),
    }).reset_index()
    return daily

def main():
    syms = sorted(f[:-8] for f in os.listdir(SRC) if f.endswith('.parquet'))
    rows = []
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        d = build(s)
        if d is None:
            continue
        d.to_parquet(os.path.join(OUT, s + '.parquet'), index=False)
        rows.append({'symbol': s, 'days': len(d),
                     'first_day': str(d['day'].min())[:10], 'last_day': str(d['day'].max())[:10],
                     'full_days': int((d['bars'] == 24).sum())})
        if i % 100 == 0:
            print(f'{i}/{len(syms)} {time.time()-t0:.1f}s', flush=True)
    meta = pd.DataFrame(rows)
    meta.to_csv(os.path.join(OUT, '_daily_meta.csv'), index=False)
    print('symbols', len(meta), 'total days', meta['days'].sum())
    print(meta['first_day'].describe())

if __name__ == '__main__':
    main()
