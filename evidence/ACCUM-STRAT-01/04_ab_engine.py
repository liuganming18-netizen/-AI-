#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第4步：A/B 同样本逐笔对照。

A：+40% 止盈 / -15% 硬止损 / 90 天到期
B：A 的全部规则 + T+5（入场后第 5 个交易日收盘）跌幅≤-10% 则按该收盘价退出

执行口径：
- 入场 = 信号次日 00:00 UTC 开盘价（15m 首根开盘）
- 日内先后用 15m K 线判定；同一根 15m 内止盈止损都触到 ⇒ 标记 AMBIG_TP_SL，不选有利路径
- 成本 单边 0.1%，净收益 = 出场价*0.999 / (入场价*1.001) - 1
"""
import os, json, sys
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
OHLCV = os.path.join(ROOT, '数据/研究输入/WyckoffMaster/ohlcv/15m')
OUT = os.path.join(EV, 'out')

TP, SL, HOLD_D = 0.40, -0.15, 90
T5_D, T5_TH = 5, -0.10
FEE = 0.001
MS = 86400_000


def simulate(sig_csv, tag, entry_mode='next_open'):
    sig = pd.read_csv(sig_csv)
    recs = []
    for sym, g in sig.groupby('symbol'):
        p = os.path.join(OHLCV, sym + '.parquet')
        if not os.path.exists(p):
            for _, r in g.iterrows():
                recs.append({**r.to_dict(), 'status': 'NO_15M'})
            continue
        b = pd.read_parquet(p, columns=['open_time', 'open', 'high', 'low', 'close', 'is_closed'])
        b = b[b['is_closed'] == 1].sort_values('open_time').reset_index(drop=True)
        t = b['open_time'].to_numpy(np.int64)
        o = b['open'].to_numpy(float)
        h = b['high'].to_numpy(float)
        l = b['low'].to_numpy(float)
        c = b['close'].to_numpy(float)
        n = len(t)

        for _, r in g.iterrows():
            sig_ms = int(pd.Timestamp(r['signal_day']).timestamp() * 1000)
            if entry_mode == 'next_open':
                entry_ms = sig_ms + MS
            else:  # signal_close：原研究口径，信号日收盘价成交
                entry_ms = sig_ms + MS - 15 * 60 * 1000
            i0 = int(np.searchsorted(t, entry_ms, 'left'))
            if i0 >= n or t[i0] != entry_ms:
                recs.append({**r.to_dict(), 'status': 'NO_ENTRY_BAR'})
                continue
            ep = o[i0] if entry_mode == 'next_open' else c[i0]
            i0s = i0 if entry_mode == 'next_open' else i0 + 1   # 收盘入场：当根 K 线已走完，不得回看
            if i0s >= n:
                recs.append({**r.to_dict(), 'status': 'NO_ENTRY_BAR'})
                continue
            tp_px, sl_px = ep * (1 + TP), ep * (1 + SL)
            end_ms = entry_ms + HOLD_D * MS
            j_end = int(np.searchsorted(t, end_ms, 'left'))

            seg_h, seg_l = h[i0s:], l[i0s:]
            tp_idx = np.argmax(seg_h >= tp_px) if np.any(seg_h >= tp_px) else -1
            sl_idx = np.argmax(seg_l <= sl_px) if np.any(seg_l <= sl_px) else -1
            cand = []
            if tp_idx >= 0:
                cand.append(tp_idx)
            if sl_idx >= 0:
                cand.append(sl_idx)
            if cand:
                k = min(cand)
                ambig = (tp_idx == sl_idx and tp_idx >= 0)
            else:
                k, ambig = -1, False
            timeout_hit = (k < 0) or (i0s + k >= j_end)

            if ambig:
                ex_i = i0s + k
                rec = dict(status='AMBIG_TP_SL', exit_reason='AMBIG_TP_SL',
                           entry_price=ep, exit_price=np.nan, exit_time=str(pd.to_datetime(t[ex_i], unit='ms')))
            elif not timeout_hit:
                ex_i = i0s + k
                if tp_idx == k:
                    rec = dict(status='OK', exit_reason='TP', entry_price=ep,
                               exit_price=max(tp_px, o[ex_i]))
                else:
                    rec = dict(status='OK', exit_reason='SL', entry_price=ep,
                               exit_price=min(sl_px, o[ex_i]))
                rec['exit_time'] = str(pd.to_datetime(t[ex_i], unit='ms'))
            else:
                if j_end >= n:
                    ex_i = n - 1
                    rec = dict(status='DATA_END', exit_reason='DATA_END', entry_price=ep, exit_price=c[ex_i])
                else:
                    ex_i = j_end
                    rec = dict(status='OK', exit_reason='TIMEOUT', entry_price=ep, exit_price=o[ex_i])
                rec['exit_time'] = str(pd.to_datetime(t[ex_i], unit='ms'))

            # ---- T+5 三种解释（定稿文字本身有歧义，全部列出，不挑有利的）----
            t5_ms = entry_ms + T5_D * MS
            j5 = int(np.searchsorted(t, t5_ms, 'left')) - 1   # 收盘价恰在 T+5 时刻的那根
            t5_ok = (j5 >= i0s) and (j5 < n) and (j5 < ex_i)
            t5_close = c[j5] if 0 <= j5 < n else np.nan
            t5_ret = t5_close / ep - 1 if np.isfinite(t5_close) else np.nan
            t5_low = l[j5] if 0 <= j5 < n else np.nan
            t5_ret_low = t5_low / ep - 1 if np.isfinite(t5_low) else np.nan

            b_recs = {}
            # B1：第 5 天收盘 ≤ -10% → 按该收盘价退出（定稿字面）
            if rec['status'] == 'AMBIG_TP_SL':
                b_recs['B1'] = dict(r='AMBIG_TP_SL', px=np.nan, tm=rec['exit_time'])
            elif t5_ok and np.isfinite(t5_ret) and t5_ret <= T5_TH:
                b_recs['B1'] = dict(r='T5', px=t5_close, tm=str(pd.to_datetime(t[j5], unit='ms')))
            else:
                b_recs['B1'] = dict(r=rec['exit_reason'], px=rec.get('exit_price'), tm=rec.get('exit_time'))

            # B2：入场后 5 天内任一时点跌破 -10% → 按 -10% 价（跳空则按开盘）退出
            t5_end_idx = j5 if j5 >= i0s else i0s
            thr = ep * (1 + T5_TH)
            if rec['status'] == 'AMBIG_TP_SL':
                b_recs['B2'] = dict(r='AMBIG_TP_SL', px=np.nan, tm=rec['exit_time'])
            else:
                seg = l[i0s:t5_end_idx + 1]
                hit = np.argmax(seg <= thr) if seg.size and np.any(seg <= thr) else -1
                if hit >= 0 and (i0s + hit) < ex_i:
                    k2 = i0s + hit
                    b_recs['B2'] = dict(r='T5_BREACH', px=min(thr, o[k2]),
                                        tm=str(pd.to_datetime(t[k2], unit='ms')))
                else:
                    b_recs['B2'] = dict(r=rec['exit_reason'], px=rec.get('exit_price'), tm=rec.get('exit_time'))

            # B3：第 5 天当天最低价 ≤ -10% → 按 -10% 价退出
            if rec['status'] == 'AMBIG_TP_SL':
                b_recs['B3'] = dict(r='AMBIG_TP_SL', px=np.nan, tm=rec['exit_time'])
            elif t5_ok and np.isfinite(t5_ret_low) and t5_ret_low <= T5_TH:
                b_recs['B3'] = dict(r='T5_LOW', px=min(thr, o[j5]),
                                    tm=str(pd.to_datetime(t[j5], unit='ms')))
            else:
                b_recs['B3'] = dict(r=rec['exit_reason'], px=rec.get('exit_price'), tm=rec.get('exit_time'))

            a_px = rec.get('exit_price')
            denom = ep * (1 + FEE)
            out = {**r.to_dict(), **rec, 'A_net': (a_px * (1 - FEE) / denom - 1) if np.isfinite(a_px) else np.nan,
                   'A_hold_days': (pd.Timestamp(rec['exit_time']) - pd.Timestamp(entry_ms, unit='ms')).total_seconds() / 86400
                   if rec.get('exit_time') else np.nan}
            for k, v in b_recs.items():
                px = v['px']
                out[f'{k}_exit_reason'] = v['r']
                out[f'{k}_exit_price'] = px
                out[f'{k}_exit_time'] = v['tm']
                out[f'{k}_net'] = (px * (1 - FEE) / denom - 1) if (px is not None and np.isfinite(px)) else np.nan
                out[f'{k}_hold_days'] = (pd.Timestamp(v['tm']) - pd.Timestamp(entry_ms, unit='ms')).total_seconds() / 86400 \
                    if v['tm'] else np.nan
            out.update({'t5_time': str(pd.to_datetime(t[j5], unit='ms')) if 0 <= j5 < n else None,
                        't5_close': t5_close, 't5_ret': t5_ret, 't5_low': t5_low,
                        't5_ret_low': t5_ret_low, 't5_before_A_exit': bool(t5_ok)})
            recs.append(out)

    df = pd.DataFrame(recs)
    df.to_csv(os.path.join(OUT, f'04_trades_{tag}.csv'), index=False)
    return df


def summarize(df, tag):
    ok = df[df['status'] == 'OK'].copy()
    amb = df[df['status'] == 'AMBIG_TP_SL']
    lines = []
    lines.append(f'[{tag}] 信号 {len(df)}  可用 {len(ok)}  日内不可判定 {len(amb)}  其他 {len(df)-len(ok)-len(amb)}')
    arms = [('A', 'A_net', 'exit_reason', 'A_hold_days')] + \
           [(k, f'{k}_net', f'{k}_exit_reason', f'{k}_hold_days') for k in ('B1', 'B2', 'B3')]
    for arm, col, rcol, hcol in arms:
        s = ok[col].dropna()
        if not len(s):
            continue
        lines.append(f'  {arm}: n={len(s)} 净均={s.mean():+.4%} 中位={s.median():+.4%} 胜率={(s>0).mean():.2%} '
                     f'合计={s.sum():+.2f}(逐笔简单相加) 持有中位={ok[hcol].median():.1f}天')
        lines.append(f'     出场: {ok[rcol].value_counts().to_dict()}')
    for k in ('B1', 'B2', 'B3'):
        d = (ok[f'{k}_net'] - ok['A_net']).dropna()
        gain, loss = d[d > 1e-9], d[d < -1e-9]
        lines.append(f'  配对差 {k}-A: 净均={d.mean():+.4%} 合计={d.sum():+.4f} 改善={len(gain)} 变差={len(loss)} 不变={int((d.abs()<=1e-9).sum())}')
        lines.append(f'     提前止损少亏={gain.sum():+.4f}（均{gain.mean() if len(gain) else 0:+.4%}） '
                     f'错杀放弃盈利={loss.sum():+.4f}（均{loss.mean() if len(loss) else 0:+.4%}）')
    txt = '\n'.join(lines)
    print(txt)
    with open(os.path.join(EV, 'logs', f'04_summary_{tag}.txt'), 'w', encoding='utf-8') as f:
        f.write(txt + '\n')
    return ok


if __name__ == '__main__':
    os.makedirs(os.path.join(EV, 'logs'), exist_ok=True)
    jobs = (('S0_open', '03_signals_S0_literal.csv', 'next_open'),
            ('S1_open', '03_signals_S1_range_only.csv', 'next_open'),
            ('S1_close', '03_signals_S1_range_only.csv', 'close'))
    for tag, fn, mode in jobs:
        df = simulate(os.path.join(OUT, fn), tag, mode)
        summarize(df, tag)
