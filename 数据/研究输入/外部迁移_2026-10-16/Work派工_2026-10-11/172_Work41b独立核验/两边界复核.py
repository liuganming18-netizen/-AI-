"""复用旧夹具，仅调用实际共享函数；输出在本审核目录，账本只用内存。"""
from pathlib import Path
import importlib.util
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

OUT=Path(__file__).parent
beg=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(); timer=time.monotonic()
p=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/费用核验与AB存量结清闭环40/02_测试_费用核验与AB_40.py')
s=importlib.util.spec_from_file_location('f',p);F=importlib.util.module_from_spec(s);s.loader.exec_module(F)
X,D=F.X,F.D
t0=F.ms('2021-01-01T00:00:00Z'); checks=[]

metas=[
 ('字典简写缺可得时刻',{t0-F.H:4.},False),
 ('列表明确生效及可得时刻',[{'effective_from_ms':t0-F.H,'available_at_ms':t0-F.H,'interval_hours':4.}],True),
 ('列表缺可得时刻',[{'effective_from_ms':t0-F.H,'interval_hours':4.}],False),
 ('字典完整记录合法',{t0-F.H:{'effective_from_ms':t0-F.H,'available_at_ms':t0-F.H,'interval_hours':4.}},True),
 ('字典完整记录未来可得',{t0-F.H:{'effective_from_ms':t0-F.H,'available_at_ms':t0+F.H,'interval_hours':4.}},False),
]
for name,meta,want in metas:
 try:
  ev=D.build_norm4h_approx_events([(t0-F.H,.001)],{t0:100.},t0,t0+F.H,meta)
  allowed=True;detail={'events':ev}
 except D.FundingIntervalUnknownError as exc:
  allowed=False;detail={'error':type(exc).__name__}
 checks.append({'case':name,'requirement_met':allowed==want,'expected_allowed':want,'actual_allowed':allowed,**detail})

con=F._syn_con(F.RID_B)
pos=[F._pos('p',F.T1,F.T2,5.)]
good_orders=[F._ord('p',F.T1,5.,'BUY'),F._ord('p',F.T2,5.,'SELL')]
mat=F._mats({('binance',F.SYN):[(F.T1,.001,10.),(F.T2,.002,11.)]})
row=F._frow('p',F.T2,.002,11.,5.)
def audit(name,positions=pos,orders=good_orders,material=mat,want=False):
 try:
  a=X.audit_funding_usage(material,con=con,run_id=F.RID_B,position_rows=positions,
       order_rows=orders,ledger_rows=[row],model='norm4h')
  checks.append({'case':name,'requirement_met':a['ok']==want,'expected_ok':want,
      'actual_ok':a['ok'],'problems':a['problems'],'forward_fail':a['forward']['fail']})
 except Exception as exc:
  checks.append({'case':name,'requirement_met':False,'unexpected_crash':repr(exc)})

audit('正常有限成交数量正例',want=True)
for value,label in [(float('nan'),'NaN'),(float('inf'),'Inf'),('坏数字','非数字'),(None,'缺失')]:
 orders=[dict(o) for o in good_orders];orders[0]['quantity']=value
 audit('BUY成交源数量'+label,orders=orders)
orders=[dict(o) for o in good_orders];orders[0].update(fill_time=F.T1+1,side='HOLD')
audit('成交方向非法且非入退场时刻',orders=orders)
audit('持仓数量NaN',positions=[dict(pos[0],quantity=float('nan'))])
bad=F._mats({('binance',F.SYN):[(F.T1,.001,10.),(F.T2,float('nan'),11.)]})
audit('源费率NaN',material=bad)
bad=F._mats({('binance',F.SYN):[(F.T1,.001,10.),(F.T2,.002,float('nan'))]})
audit('源标价NaN',material=bad)

rep={'start_beijing':beg,'end_beijing':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
 'seconds':round(time.monotonic()-timer,3),'checks':checks,
 'n_checks':len(checks),'n_failed':sum(not c['requirement_met'] for c in checks),
 'scope':'上次两边界真实共享函数；只用内存夹具，无历史回放/共享修改'}
(OUT/'两边界复核结果.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2))
print(json.dumps({'n_checks':rep['n_checks'],'n_failed':rep['n_failed'],
 'failures':[c for c in checks if not c['requirement_met']]},ensure_ascii=False))
