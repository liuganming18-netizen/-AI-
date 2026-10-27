#!/usr/bin/env python3
"""只读实际执行模块；纯合成输入/:memory:/tempfile。退出1为发现验收缺陷。"""
import copy, importlib.util, json, os, sqlite3, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
HERE=Path(__file__).resolve().parent
WORK=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/接线路整改1')
BE=WORK.parent.parent/'后端实现'
sys.path.insert(0,str(BE))
from strategies import wx_config, wx_execution as X, wx_ledger as L, wx_gate
spec=importlib.util.spec_from_file_location('codex_s3a_fixture',WORK/'S3a执行链_证据.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
view,_=wx_config.load_view()
Q=f.Q; BASE=f.BASE; ENTRY=f.ENTRY_TS; H=f.HOUR
facts={}
def replay(name,path,*,events=None,funding=None,exits=None,changed_view=None,remove_detail_ts=None):
    con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;L.create_schema(con)
    resolved=wx_config.resolve(con,'3a')
    sd={('binance','ZZZUSDT'):{'events':events or [f._ev(name)],
        'detail':f.detail(path),'funding':funding or [],'funding_available':True,
        'donchian_exits':exits or [],'contract':X.Contract(1e-6,5.0)}}
    if remove_detail_ts is not None:
        sd[('binance','ZZZUSDT')]['detail']=[b for b in sd[('binance','ZZZUSDT')]['detail'] if b[0]!=remove_detail_ts]
        sd[('okx','AUXUSDT')]={'events':[],'detail':f.detail(f.p_flat),'funding':[],
            'funding_available':True,'donchian_exits':[],'contract':X.Contract(1e-6,5.0)}
    result=X.run_arm(con=con,run_id=name,arm='3a',config_id='3a',resolved=resolved,
                     view=changed_view or view,symbol_data=sd)
    positions=[dict(r) for r in con.execute('SELECT * FROM research_position ORDER BY entry_time')]
    return con,result,positions

# actual entry=100.05，合同 stop=100.05-3*2=94.05；实际94.0。
con,res,pos=replay('stop_anchor',f.p_flat)
p=pos[0]
facts['E01_actual_fill_stop_anchor']={'expected':p['entry_price']-6,'actual':p['stop_price'],
    'entry':p['entry_price'],'accepted':abs(p['stop_price']-(p['entry_price']-6))<1e-9}
facts['E02_terminal_equity_after_costs']={'review_final':res['review']['metrics']['final_equity'],
    'cash_after_all_closed':10000+sum(p['net_pnl'] for p in pos),
    'reconciliation_ok':res['reconciliation']['ok'],
    'accepted':abs(res['review']['metrics']['final_equity']-(10000+sum(p['net_pnl'] for p in pos)))<=0.01}
con.close()

# 仅改变资金费时点之后那根15m的未来close，T时刻旧仓/开盘/实际rate完全相同。
FT=ENTRY+Q
fund_amounts={}
for future_close in [100.0,200.0]:
    def path(ts,c=future_close):
        return (100.0,max(100.1,c+0.1),99.9,c) if ts==FT else f.p_flat(ts)
    con,res,pos=replay('fund_future_'+str(future_close),path,funding=[(FT,0.0001)])
    fund_amounts[str(future_close)]=con.execute('SELECT amount FROM research_funding').fetchone()[0]
    con.close()
facts['E03_T_funding_uses_future_close']={'future_close_to_amount':fund_amounts,
    'expected':'T同刻funding不随其后15m收盘价格改变；须用T实际事件mark及单位',
    'accepted':abs(fund_amounts['100.0']-fund_amounts['200.0'])<1e-9}
con,res,pos=replay('fund_missing_detail_mark',f.p_flat,funding=[(FT,0.0001)],remove_detail_ts=FT)
missing_amount=con.execute('SELECT amount FROM research_funding').fetchone()[0]
facts['E03_T_funding_uses_future_close']['missing_event_mark_still_accepted']=True
facts['E03_T_funding_uses_future_close']['missing_T_detail_funding_amount']=missing_amount
facts['E03_T_funding_uses_future_close']['fallback_entry_price_formula']=-pos[0]['quantity']*pos[0]['entry_price']*0.0001
con.close()

# 已安排的唐奇安退出在T开盘执行，T之后的low不能抢占该退出。
EXIT=BASE+2*H+Q
def due_exit_path(ts):
    return (100.0,100.1,93.0,100.0) if ts==EXIT else f.p_flat(ts)
con,res,pos=replay('scheduled_exit_future_low',due_exit_path,exits=[BASE+H])
facts['E04_scheduled_exit_overridden_by_future_low']={'exit_reason':pos[0]['exit_reason'],
    'exit_time':pos[0]['exit_time'],'exit_price':pos[0]['exit_price'],
    'expected':'T开盘donchian_exit，99.95；当根后续low不得先改成fixed_atr_stop',
    'accepted':pos[0]['exit_reason']=='donchian_exit' and abs(pos[0]['exit_price']-99.95)<1e-9}
con.close()

# 沿用Work自己的小DD阈值路径夹具；只能改变触发时间，不能改变锁止语义。
vdd=copy.deepcopy(view);vdd['risk']['maximum_account_drawdown_fraction']=1e-6
e1=f._ev('dd_first');e2=dict(f._ev('dd_next'),event_time=e1['event_time']+H,
                           confirmed_at=e1['event_time']+H)
con,res,pos=replay('dd_after_lock',f.p_flat,events=[e1,e2],changed_view=vdd)
facts['E05_drawdown_next_open_and_no_resume']={'halted':res['halted'],
    'entry_times':[p['entry_time'] for p in pos],'exit_times':[p['exit_time'] for p in pos],
    'exit_reasons':[p['exit_reason'] for p in pos],
    'expected':'权益收盘触发后下一15m开盘清仓，后续候选拒绝且run不恢复',
    'accepted':len(pos)==1 and pos[0]['exit_time']==ENTRY+Q}
con.close()

# 共享资金已占6000时真实free=4000；新增1000应可预占，不能再次扣已占6000。
con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;L.create_schema(con)
led=L.Ledger(con,'atomic_double_count')
led.open_run(arm='3a',config_id='3a',strategy_version='fixture',config_hash='fixture',
             data_version='fixture',engine='fixture',initial_equity=10000)
r1=led.try_reserve(position_id='p1',amount=6000,cash_available=10000,created_at=1)
r2=led.try_reserve(position_id='p2',amount=1000,cash_available=4000,created_at=2)
facts['E06_atomic_cash_double_count']={'first':r1,'second':r2,'held':led.held_amount(),
    'expected':'free_cash已扣held，不可再用held+amount与free_cash比较','accepted':r2[0]}
con.close()

# 门禁缺sha也能all_pass；仅标签，即使自己生成sha也没有run/data/code证据绑定。
tmp=Path(tempfile.mkdtemp(prefix='codex_wx_gate_fixture_'));gate=tmp/'unhashed_fixture.json'
gate.write_text(json.dumps({'gates':{g:'pass' for g in wx_gate.GATE_IDS}}))
binding=wx_gate.binding(str(gate))
facts['E07_unhashed_gate_labels_accepted']={'all_pass':binding['all_pass'],
    'self_check':binding['self_check'],'expected':'无sha/独立接受证据及具体run绑定必须fail closed',
    'accepted':binding['all_pass'] is False}

# 对账直接信任stored gross_pnl：调同一仓 gross和net并配合末日equity，可全绿。
con,res,pos=replay('stored_pnl_forged',f.p_flat,exits=[BASE+H])
before=L.reconcile(con,'stored_pnl_forged')
con.execute("UPDATE research_position SET gross_pnl=gross_pnl+100,net_pnl=net_pnl+100 WHERE run_id='stored_pnl_forged'")
con.execute("UPDATE research_equity SET equity=equity+100 WHERE run_id='stored_pnl_forged' AND ts=(SELECT MAX(ts) FROM research_equity WHERE run_id='stored_pnl_forged')")
con.commit();after=L.reconcile(con,'stored_pnl_forged')
facts['E08_reconciliation_trusts_stored_gross']={'before_ok':before['ok'],'after_ok':after['ok'],
    'stored_gross_increase':100,'orders_prices_and_quantities_changed':False,
    'expected':'必须按成交价格/数量独立重算gross并发现伪造；G04另需独立引擎逐笔对拍',
    'accepted':after['ok'] is False}
con.close()

effective=wx_config.effective_configs_check(view)
resolved_cost=wx_config.resolve(None,'3b_cost2x');resolved_delay=wx_config.resolve(None,'3a_delay30')
result={'source':'actual wx_execution/wx_ledger/wx_gate pure synthetic counterexamples',
    'database':'all SQLite databases :memory:; gate fixture only tempfile',
    'production_or_work_original_written':False,
    'effective_18_identity':{'status':effective['status'],'count':effective['count'],'sha256':effective['sha256'],
        'cost2x_exec':resolved_cost['exec'],'delay30_exec':resolved_delay['exec']},
    'freqtrade_imports_or_calls_in_modules':False,'cases':facts,
    'failed':[k for k,v in facts.items() if not v['accepted']]}
result['exit_code']=1 if result['failed'] else 0
(HERE/'05_S3a定点严格结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(result['exit_code'])
