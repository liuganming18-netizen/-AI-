"""独立账本金额/每日掩码/首个DD触发复算；仅mode=ro，不执行回测。"""
from pathlib import Path
from collections import defaultdict
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import sqlite3,json,hashlib,time

OUT=Path(__file__).parent
ROOT=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11')
W37=ROOT/'取消成交额硬门槛与375新准入37'
started=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat();timer=time.monotonic()
daily=json.loads((W37/'11_375逐日名单_DEV_37.json').read_text())
adm={int(d['day']):d.get('admission') or {} for d in daily['days']}
results={}
for arm in ['3a','3b','3c','3d']:
 p=W37/'临时账本37'/f'ledger_{arm}.sqlite3'
 con=sqlite3.connect(f'file:{p}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 rid='w37base_norm4h_'+arm
 run=list(con.execute('SELECT * FROM research_run WHERE run_id=?',(rid,)))
 pos=[dict(r) for r in con.execute('SELECT * FROM research_position WHERE run_id=?',(rid,))]
 funds=[dict(r) for r in con.execute('SELECT * FROM research_funding WHERE run_id=?',(rid,))]
 orders=[dict(r) for r in con.execute("SELECT * FROM research_order WHERE run_id=? AND status='fill' ORDER BY order_id",(rid,))]
 eq=[dict(r) for r in con.execute('SELECT * FROM research_equity WHERE run_id=? ORDER BY ts',(rid,))]
 rev=dict(con.execute('SELECT * FROM research_review WHERE run_id=?',(rid,)).fetchone())
 cash_events=defaultdict(float)
 for o in orders: cash_events[o['fill_time']]-=o['fee']
 for f in funds: cash_events[f['funding_time']]+=f['amount']
 gross_err=[];net_err=[]
 for q in pos:
  gross=q['quantity']*(q['exit_price']-q['entry_price'])
  gross_err.append(abs(gross-q['gross_pnl']))
  net_err.append(abs(gross-q['fees']+q['funding']-q['net_pnl']))
  cash_events[q['exit_time']]+=gross
 cash=5000.; timeline=sorted(cash_events);j=0;max_cash_error=0.;max_equity_identity=0.
 peak=5000.;max_dd=0.;first_dd=None
 for e in eq:
  while j<len(timeline) and timeline[j]<=e['ts']:
   cash+=cash_events[timeline[j]];j+=1
  max_cash_error=max(max_cash_error,abs(cash-e['cash']))
  max_equity_identity=max(max_equity_identity,abs(e['equity']-e['cash']-e['unrealized']))
  peak=max(peak,e['equity']);dd=(peak-e['equity'])/peak
  max_dd=max(max_dd,dd)
  if first_dd is None and dd>=.20:first_dd=e['ts']
 halted=int(datetime.fromisoformat(rev['halted'].replace('Z','+00:00')).timestamp()*1000)
 post_entries=[{'symbol':q['symbol'],'entry_time':q['entry_time']} for q in pos if q['entry_time']>halted]
 dd_exits=[{'symbol':q['symbol'],'exit_time':q['exit_time']} for q in pos if q['exit_reason']=='drawdown_lock']
 mask_bad=[{'symbol':q['symbol'],'entry_time':q['entry_time']} for q in pos if not adm.get(q['entry_time']//86400000*86400000,{}).get('binance:'+q['symbol'],False)]
 fee_sum=sum(o['fee'] for o in orders);fund_sum=sum(f['amount'] for f in funds)
 sum_net=sum(q['net_pnl'] for q in pos)
 results[arm]={'ledger_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
  'initial_equity':run[0]['initial_equity'],'run_record_count':len(run),'trades':len(pos),
  'final_equity':eq[-1]['equity'],'net_change':eq[-1]['equity']-5000.,
  'net_position_sum':sum_net,'fees':fee_sum,'funding':fund_sum,
  'final_equity_identity_error':abs(eq[-1]['equity']-5000.-sum_net),
  'max_trade_gross_error':max(gross_err),'max_trade_net_error':max(net_err),
  'equity_rows':len(eq),'max_cash_path_error':max_cash_error,
  'max_equity_row_identity_error':max_equity_identity,'max_drawdown':max_dd,
  'review_mdd_error':abs(max_dd-rev['max_drawdown']),
  'first_dd_cross_ms':first_dd,'recorded_halted_ms':halted,'first_dd_matches_halted':first_dd==halted,
  'drawdown_lock_exits':dd_exits,'lock_exits_next_bar':all(q['exit_time']==halted+900000 for q in dd_exits),
  'post_halt_entries':post_entries,'entry_not_in_saved_admission_mask':mask_bad,
  'admission_limit':'仅核进入已存当天名单，未独立重新生成资格原始依据',
  'exposure_limit':'未接受Work42使用前一根权益冒成交时权益的敞口核验'}
 con.close()
rep={'started_beijing':started,'completed_beijing':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
 'elapsed_seconds':round(time.monotonic()-timer,3),'per_arm':results,'construction':False,'backtest':False,'download':False}
(OUT/'独立账本金额与锁止核对.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2))
print(json.dumps({a:{k:r[k] for k in ['trades','max_trade_gross_error','max_trade_net_error','max_cash_path_error','first_dd_matches_halted','lock_exits_next_bar','post_halt_entries','entry_not_in_saved_admission_mask']} for a,r in results.items()},ensure_ascii=False))
