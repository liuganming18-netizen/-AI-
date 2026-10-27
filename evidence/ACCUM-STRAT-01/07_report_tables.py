#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACCUM-STRAT-01 / 第7步：成本列、与原四组数字的形态对照、剔除稳定币的敏感性。"""
import os, json
import numpy as np
import pandas as pd

ROOT = '/Users/boos/Desktop/威科夫因子共振趋势'
EV = os.path.join(ROOT, 'evidence/ACCUM-STRAT-01')
OUT = os.path.join(EV, 'out')

ORIG = {'G1': (196, 0.536, 0.0979), 'G2': (186, 0.677, 0.2217),
        'G3': (34, 0.588, 0.1630), 'G4': (61, 0.131, -0.0381)}
STABLE = {'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'BUSDUSDT', 'DAIUSDT', 'USDPUSDT', 'EURUSDT'}


def main():
    p = pd.read_csv(os.path.join(OUT, '06_pairs_S1_open_B1.csv'))
    p['gross_A'] = p['exit_price'] / p['entry_price'] - 1
    p['cost_A'] = p['gross_A'] - p['A_net']
    p.to_csv(os.path.join(OUT, '06_pairs_S1_open_B1.csv'), index=False)

    rows = []
    for g in ['G1', 'G2', 'G3', 'G4']:
        d = p[p['group'] == g]
        n0, w0, m0 = ORIG[g]
        rows.append({'group': g, '原_笔数': n0, '原_胜率': w0, '原_净均': m0,
                     '重建_笔数': len(d), '重建_胜率': (d['A_net'] > 0).mean(),
                     '重建_净均': d['A_net'].mean(),
                     '胜率差': (d['A_net'] > 0).mean() - w0, '净均差': d['A_net'].mean() - m0})
    pd.DataFrame(rows).to_csv(os.path.join(OUT, '07_vs_original.csv'), index=False)

    # 剔除稳定币的敏感性
    sub = p[~p['symbol'].isin(STABLE)]
    res = {}
    for name, d in (('全样本', p), ('剔除稳定币', sub)):
        dd = d['B1_net'] - d['A_net']
        res[name] = {'n': len(d), 'A净均': d['A_net'].mean(), 'B1净均': d['B1_net'].mean(),
                     '配对净改善每笔': dd.mean(), '改善笔数': int((dd > 1e-9).sum()),
                     '错杀笔数': int((dd < -1e-9).sum())}
    with open(os.path.join(OUT, '07_stable_sensitivity.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == '__main__':
    main()
