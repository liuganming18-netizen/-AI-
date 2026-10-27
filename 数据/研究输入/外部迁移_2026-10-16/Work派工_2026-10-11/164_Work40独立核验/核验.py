"""独立定向验收：仅读源代码/旧账本；合成用例只写内存，不跑历史回放。"""
import importlib.util
import json
import hashlib
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

START = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
TIMER = time.monotonic()
OUT = Path(__file__).parent
ROOT = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究')
W40 = ROOT / '最新唐奇安威科夫接通与收尾_2026-10-11/费用核验与AB存量结清闭环40'
spec = importlib.util.spec_from_file_location('work40_fixture', W40 / '02_测试_费用核验与AB_40.py')
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)  # 只导入既有fixture定义；不执行main，不写Work产物。
X, D, L = F.X, F.D, F.L
SOURCE = ROOT / '后端实现/strategies'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
before = {n: sha(SOURCE / n) for n in ['wx_execution.py','wx_daily_375.py','wx_ledger.py']}
CASES = []

con = F._syn_con(F.RID_B)
pos = [F._pos('p', F.T1, F.T2, 5.0)]
ords = [F._ord('p', F.T1, 5.0, 'BUY'), F._ord('p', F.T2, 5.0, 'SELL')]
mats = F._mats({('binance', F.SYN): [(F.T1, .001, 10.0), (F.T2, .002, 11.0)]})
missing = F._mats({('binance', F.SYN): [(F.T1, .001, 10.0), (F.T2, None, 11.0)]})
good = F._frow('p', F.T2, .002, 11.0, 5.0)

def audit(name, want, material=mats, rows=None, orders=None, **kw):
    a = X.audit_funding_usage(material, con=con, run_id=F.RID_B,
            position_rows=pos, order_rows=ords if orders is None else orders,
            ledger_rows=[good] if rows is None else rows, **kw)
    CASES.append({'case':name,'expected_ok':want,'actual_ok':a['ok'],
        'requirement_met':a['ok']==want,'n_expected':a['n_expected'],
        'n_charged':a['n_charged'],'n_formula_rows':a['n_formula_rows'],
        'total_abs_error':a['total_abs_error'],'problems':a['problems'],
        'forward_fail':a['forward']['fail']})

audit('非空正确费用对照', True)
audit('数量金额同乘2', False, rows=[dict(good,qty=10.,amount=good['amount']*2)])
audit('原率存在伪造估计标签', False, rows=[dict(good,rate_substituted=1,rate_source='adjacent_adverse_estimate')])
audit('同仓同刻重复费用', False, rows=[good,dict(good)])
audit('原费率缺失且未标估计，却填任意费率和自洽金额',False,
      material=missing, rows=[dict(good)],model='historical_events')
audit('真实原率存在，费用行费率NULL金额0',False,
      rows=[dict(good,rate=None,amount=0.)],model='historical_events')
audit('标为相邻不利估计但没有相邻原始费率材料',False,
      material=missing,rows=[dict(good,rate=.99,amount=-5*11*.99,
      rate_substituted=1,rate_source='adjacent_adverse_estimate')],model='norm4h')
audit('费用金额非有限NaN',False,rows=[dict(good,amount=float('nan'))])
foreign = [dict(o,exchange='okx',symbol='OTHERUSDT') for o in ords]
audit('相同持仓ID借用别的交易所和币的成交单',False, orders=foreign)

def run_mode(mode, two_missing=False):
    t0=F.ms('2021-01-01T00:00:00Z')
    sd=F._three_coin_sd(t0)
    if two_missing:
        sd[('binance','BBBUSDT')]['marks'].pop(t0+3*F.H)
        sd[('binance','BBBUSDT')]['donchian_exits']=[t0+3*F.H]
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;L.create_schema(c)
    rid='ind40_'+mode+('_two' if two_missing else '')
    r=X.run_arm(con=c,run_id=rid,arm='3a',config_id='3a',
        resolved={'exec':{},'config_hash':'h'*64,'version':'ind40'},
        view={'risk':{},'execution':{},'stops':{'max_holding_hours':720}},
        symbol_data=sd,approx_spec=D.approx_spec_375(),run_mode=mode)
    ps=[dict(p) for p in c.execute('SELECT symbol,status,entry_time,exit_time,exit_reason,exit_price,gross_pnl,net_pnl FROM research_position WHERE run_id=?',(rid,))]
    cols={'first_anomaly_ts':r.get('first_anomaly_ts'),'frozen_at':r.get('frozen_at'),
          'wind_down':r.get('wind_down'),'account_metrics_valid':r.get('account_metrics_valid')}
    if two_missing:
        p=next(p for p in ps if p['symbol']=='BBBUSDT')
        ok=p['status']=='uncomputable' and p['exit_price'] is None
    elif mode=='version_a':
        ok=all(p['status']=='uncomputable' and p['exit_price'] is None for p in ps)
    else:
        ok=(next(p for p in ps if p['symbol']=='AAAUSDT')['status']=='uncomputable'
             and sum(p['status']=='closed' for p in ps)==2 and r['account_metrics_valid'] is False)
    CASES.append({'case':f'{mode} '+('同刻两币缺标价，第二币该刻有平仓信号' if two_missing else '既有单币缺标价机制对照'),
        'requirement_met':ok,'positions':ps,'account':cols})

run_mode('version_a')
run_mode('version_b')
run_mode('version_b',True)

t0=F.ms('2021-01-01T00:00:00Z')
meta=[{'effective_from_ms':t0-F.H,'interval_hours':4.,'available_at_ms':t0+F.H,'nominal_anchor_ms':t0}]
try:
    e=D.build_norm4h_approx_events([(t0-F.H,.001)],{t0:100.},t0,t0+F.H,meta)
    CASES.append({'case':'周期记录生效在过去但实际可得时刻在未来','requirement_met':False,'emitted_events':e})
except D.FundingIntervalUnknownError as exc:
    CASES.append({'case':'周期记录生效在过去但实际可得时刻在未来','requirement_met':True,'error':str(exc)})

old = ROOT/'最新唐奇安威科夫接通与收尾_2026-10-11/取消成交额硬门槛与375新准入37/临时账本37'
counts={}
for arm in ['3a','3b','3c','3d']:
    p=old/f'ledger_{arm}.sqlite3'
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True); c.row_factory=sqlite3.Row
    rid='w37base_norm4h_'+arm
    counts[arm]={'sha256':sha(p),'funding_rows':c.execute('SELECT COUNT(*) FROM research_funding WHERE run_id=?',(rid,)).fetchone()[0]}
    c.close()
after={n:sha(SOURCE/n) for n in before}
report={'start_beijing':START,'end_beijing':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
  'seconds':round(time.monotonic()-TIMER,3),'construction':False,'history_backtest':False,
  'source_sha_before':before,'source_sha_after':after,'source_stable':before==after,
  'cases':CASES,'requirement_fail_count':sum(not c['requirement_met'] for c in CASES),
  'old37_readonly_counts':counts,'old37_full_source_reaudit':'未由Codex全量重新验证；Work报告不能直接算独立验收'}
(OUT/'结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'case_count':len(CASES),'failed':[c['case'] for c in CASES if not c['requirement_met']],
 'source_stable':before==after,'elapsed_seconds':report['seconds']},ensure_ascii=False))
