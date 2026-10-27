"""B双持仓同刻缺标价＋第二持仓当刻止损，全部用内存账本。"""
import importlib.util
import json
import sqlite3
from pathlib import Path

OUT = Path(__file__).parent
W40 = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/费用核验与AB存量结清闭环40')
spec = importlib.util.spec_from_file_location('f', W40/'02_测试_费用核验与AB_40.py')
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
t0=F.ms('2021-01-01T00:00:00Z'); gap=t0+3*F.H
sd=F._three_coin_sd(t0)
sd[('binance','BBBUSDT')]['marks'].pop(gap)
sd[('binance','BBBUSDT')]['detail']=[
    (b[0],b[1],b[2],80.,b[4],b[5]) if b[0]==gap else b
    for b in sd[('binance','BBBUSDT')]['detail']]
con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;F.L.create_schema(con)
F.X.run_arm(con=con,run_id='ind40_B_same_gap_stop',arm='3a',config_id='3a',
    resolved={'exec':{},'config_hash':'h'*64,'version':'ind40'},
    view={'risk':{},'execution':{},'stops':{'max_holding_hours':720}},
    symbol_data=sd,approx_spec=F.D.approx_spec_375(),run_mode='version_b')
p=dict(con.execute('SELECT symbol,status,entry_time,exit_time,exit_reason,exit_price FROM research_position WHERE symbol=?',('BBBUSDT',)).fetchone())
print(json.dumps({'gap_ms':gap,'actual':p,
    'requirement_met':p['status']=='uncomputable' and p['exit_price'] is None},ensure_ascii=False))
