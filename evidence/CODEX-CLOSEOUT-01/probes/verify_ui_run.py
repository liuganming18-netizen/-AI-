"""只读复核真实 HTTP run 与隔离 paper 账本；不会发单或改账本。"""
import argparse
import hashlib
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'evidence/CODEX-CLOSEOUT-01/out'
p = argparse.ArgumentParser()
p.add_argument('--run-id', required=True)
p.add_argument('--note', default='',
               help='本次复算的补充说明（例如：某个源文件在 run 产出后被改过、改了什么、'
                    '为什么改、为何不影响本次对账结论）。写进证据，避免"哈希对上了"掩盖漂移。')
a = p.parse_args()
u = urllib.request.build_opener(urllib.request.ProxyHandler({}))
responses = {}
for suffix in ['', '/trades', '/metrics', '/chart']:
    response = json.load(u.open('http://127.0.0.1:8787/api/v1/backtests/' + a.run_id + suffix, timeout=20))
    responses[suffix] = response
    (OUT / ('http_' + a.run_id + (suffix.replace('/', '_') or '_detail') + '.json')).write_text(json.dumps(response, ensure_ascii=False, indent=2))
assert responses['']['status'] == 'succeeded'
assert len(responses['/trades']['items']) == 1
trade = responses['/trades']['items'][0]
metrics = responses['/metrics']['metrics']
ledger = ROOT / 'evidence/CODEX-CLOSEOUT-01/iso/VELVETUSDT/paper/paper_3a.sqlite3'
con = sqlite3.connect(f'file:{ledger}?mode=ro', uri=True)
con.row_factory = sqlite3.Row
pos = dict(con.execute('select * from paper_position').fetchone())
r = dict(con.execute('select * from paper_realized where coalesce(voided,0)=0').fetchone())
state = dict(con.execute('select * from paper_state').fetchone())
stops = [dict(x) for x in con.execute('select * from paper_stop_history order by id')]
con.close()
checks = []
def check(field, x, y):
    numeric = isinstance(x, (int,float)) and isinstance(y,(int,float))
    ok = abs(x-y)<1e-8 if numeric else x==y
    checks.append(dict(field=field, backtest=x, paper=y, **{'pass':ok}, tolerance=1e-8 if numeric else None))
for x,y in [('entry_price','entry_price'),('qty','qty'),('entry_fee','entry_fee'),('exit_price','exit_price'),('exit_fee','exit_fee'),('gross_pnl','gross_pnl'),('net_pnl','net_pnl'),('funding_fee','funding_paid')]:
    check(x,trade[x],r[y])
check('signal_id',trade['signal_id'],pos['signal_uid'])
check('entry_time_ms',trade['entry_time'],pos['entry_ts'])
check('initial_stop',trade['initial_stop'],pos['initial_stop_price'])
check('trail_effective_ms',trade['stop_trace'][-1]['t'],stops[-1]['effective_at'])
check('final_stop',trade['stop_trace'][-1]['stop'],stops[-1]['new_stop'])
check('trail_count',len(trade['stop_trace'])-1,len(stops)-1)
check('exit_bar_open_ms',trade['exit_time'],pos['exit_ts']-900000)
check('end_equity',metrics['final_equity'],state['cash'])
check('fees_total',trade['fees'],r['entry_fee']+r['exit_fee'])
assert all(x['pass'] for x in checks), checks
assert trade['trade_id'] and trade['entry_time_ms'] and trade['exit_time_ms']
paths = ['框架/backend/'+x+'.py' for x in ['paper_engine','unified_runtime','backtest_runner','backtest_api','ledger_links','strategy_contracts','experiment_registry']]
paths += ['策略/WyckoffMaster/event_trend_v0_1/'+x+'.py' for x in ['config','engine_adapter','runner']]
paths += ['策略/WyckoffMaster/trading_core/fill.py','UI界面/web/src/components/BacktestWorkbench.tsx',str(ledger.relative_to(ROOT))]
paths += ['回测日志/event_trend_v0_1/runs/'+a.run_id+'/'+x for x in ['run.json','summary.json','trades.jsonl']]
source_hashes = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
result = dict(run_id=a.run_id, generated_at_utc=datetime.now(timezone.utc).isoformat(), rule='FT-LOW4H', scope_kind='historical_sample_mechanism', scope='同一历史样本的机制闭环；不代表当前自然行情成交或策略有效性',
              clock_note='paper exit_ts为15m收盘确认时刻；backtest exit_time为同根15m开盘标记，按同根bar规范化比较，不宣称原始时刻相等。',
              checks=checks, passed=sum(c['pass'] for c in checks),total=len(checks), source_hashes=source_hashes,
              paper_ledger=str(ledger.relative_to(ROOT)),trade_id=trade['trade_id'])
if a.note:
    # 复算说明：只记录"为什么又算了一次"，不参与任何断言判定
    result['reverify_note'] = a.note
(OUT/'ui_paper_reconciliation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'run_id':a.run_id,'passed':result['passed'],'total':result['total'],'trade_id':trade['trade_id'],'net_pnl':trade['net_pnl']},ensure_ascii=False))
