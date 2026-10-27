#!/usr/bin/env python3
# coding: utf-8
"""Codex audit only: frozen deliveries, isolated synthetic replay, narrow inventory."""
import datetime, hashlib, importlib.util, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
HERE=Path(__file__).resolve().parent
OUT=HERE/'第二轮独立复审'
OUT.mkdir(exist_ok=True)
BASE=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究')
ROOT=BASE/'最新唐奇安威科夫接通与收尾_2026-10-11'
BE=BASE/'后端实现'
sys.path.insert(0,str(BE))
from strategies import wx_gate, wx_config, wx_execution as X, wx_ledger as L
FTPY='/Users/boos/.workbuddy-ai/binaries/python/envs/freqtrade/bin/python'
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'production_written':False,'work_evidence_overwritten':False}
checks=[]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for folder,manifest in [('数据终验与输入收口1','05_非递归SHA清单.json'),('接线路整改2','文件SHA清单.json')]:
 d=json.loads((ROOT/folder/manifest).read_text())
 for key in ['payload','deliverables','business_sources','before_bytes']:
  for row in d.get(key,[]):
   raw=row.get('file',row.get('path'))
   p=Path(raw) if Path(raw).is_absolute() else ROOT/folder/raw
   checks.append({'path':str(p),'accepted':p.exists() and sha(p)==row['sha256'] and p.stat().st_size==row['bytes']})
result['file_checks']={'count':len(checks),'bad':[v for v in checks if not v['accepted']]}
receipt=json.loads((ROOT/'数据终验与输入收口1/06_READY_FOR_DATA_FINAL_AND_INPUT_REVIEW.json').read_text())
result['data_manifest_binding_ok']=sha(ROOT/'数据终验与输入收口1/05_非递归SHA清单.json')==receipt['manifest']['sha256']

# Run frozen entrypoints, redirect only HERE into isolated directories. Work originals untouched.
replays=[]
for name,source,python in [
 ('old_eight',HERE/'策略整改1独立复审/04_S3a定点独立反例.py',sys.executable),
 ('previous_regression',HERE/'策略整改1独立复审/01_复用Work入口独立复跑.py',sys.executable),
 ('round2_s3a',ROOT/'接线路整改2/S3a执行链_证据_整改2.py',sys.executable),
 ('round2_parity',ROOT/'接线路整改2/Freqtrade真实引擎_逐笔对拍.py',FTPY)]:
 temp=Path(tempfile.mkdtemp(prefix='codex_wx_round2_'))
 src=source.read_text()
 src=src.replace('HERE=Path(__file__).resolve().parent',f'HERE=Path({str(temp)!r})').replace('HERE = Path(__file__).resolve().parent',f'HERE = Path({str(temp)!r})')
 target=temp/source.name;target.write_text(src)
 r=subprocess.run([python,str(target)],capture_output=True,text=True,timeout=90,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
 (OUT/(name+'_stdout.txt')).write_text(r.stdout);(OUT/(name+'_stderr.txt')).write_text(r.stderr)
 replays.append({'name':name,'source':str(source),'source_sha256':sha(source),'actual_exit_code':r.returncode,'isolated_output':str(temp)})
 for f in temp.glob('*.json'):
  (OUT/(name+'_'+f.name)).write_bytes(f.read_bytes())
result['actual_replays']=replays

# New funding counterexample: trade OHLC present, all actual mark sources missing.
spec=importlib.util.spec_from_file_location('s3fixture',ROOT/'接线路整改1/S3a执行链_证据.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;L.create_schema(con)
ft=f.ENTRY_TS+f.Q
sd={('binance','ZZZUSDT'):{'events':[f._ev('mark_absent_trade_open_present')],'detail':f.detail(f.p_flat),'funding':[(ft,0.0001)],'funding_available':True,'donchian_exits':[],'contract':X.Contract(1e-6,5.0)}}
r=X.run_arm(con=con,run_id='codex_mark_absent',arm='3a',config_id='3a',resolved=wx_config.resolve(con,'3a'),view=wx_config.load_view()[0],symbol_data=sd)
rows=[dict(v) for v in con.execute('SELECT * FROM research_funding')]
mark,source=X.resolve_funding_mark(sd[('binance','ZZZUSDT')],{('binance','ZZZUSDT',int(b[0])):b for b in sd[('binance','ZZZUSDT')]['detail']},'binance','ZZZUSDT',ft,None)
result['strict_actual_event_mark_missing']={'no_event_mark':True,'no_marks_series':True,'trade_bar_open_present':True,'resolved_mark':mark,'source':source,'funding_rows':rows,'blocked_count':r.get('blocked_count'),'accepted':mark is None and all(v.get('amount') is None for v in rows)}
con.close()

# Fully hash-consistent labels must not become independently accepted evidence.
temp=Path(tempfile.mkdtemp(prefix='codex_fake_gate_'));p=temp/'invented.json'
d={'gates':{g:'pass' for g in wx_gate.GATE_IDS},'bindings':{k:'invented' for k in wx_gate.REQUIRED_BINDINGS},'evidence':{g:{'sha256':'invented','accepted_by':'work'} for g in wx_gate.GATE_IDS},'synthetic':False}
d['sha256']=wx_gate._canon(wx_gate._payload_for_hash(d));p.write_text(json.dumps(d))
b=wx_gate.binding(str(p))
result['strict_fake_evidence_binding']={'actual_binding':b,'accepted':b['all_pass'] is False,'fixture_not_production':True}

# Narrow inventory, no full history aggregation or DB copies.
datadir=BE/'历史下载/perp_datadir_real/data/binance/futures'
files=list(datadir.glob('*.feather'))
result['datadir_counts']={kind:sum(x.name.endswith('-'+kind+'.feather') for x in files) for kind in ['1h-futures','1h-mark','15m-futures','1h-funding_rate','1d-mark','1d-futures']}
try:
 import pandas as pd
 samples=[]
 for name in ['BTC_USDT_USDT-15m-futures.feather','BTC_USDT_USDT-1h-funding_rate.feather']:
  frame=pd.read_feather(datadir/name)
  samples.append({'file':name,'rows':len(frame),'columns':list(frame.columns),'first':str(frame['date'].min()),'last':str(frame['date'].max()),'negative_funding':int((frame['funding_rate']<0).sum()) if 'funding_rate' in frame else None})
 result['feather_samples']=samples
except Exception as e: result['feather_error']=str(e)
v=os.statvfs(str(BE));result['free_gib']=v.f_bavail*v.f_frsize/1024**3
import urllib.request
probes={}
for path in ['/api/health','/api/v1/strategies']:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8787'+path,timeout=5) as resp:
   doc=json.load(resp);probes[path]={'http':resp.status,'keys':[x.get('strategy_key') for x in doc.get('items',[])] if isinstance(doc,dict) else None}
 except Exception as e:probes[path]={'error':str(e)}
result['formal_api']=probes
result['freqtrade_scope_observed']='wx_engine persists fills/PnL already computed by wx_execution; actual Backtesting/Trade price calculation not invoked'
result['formal_backtest_accepted']=False
p=OUT/'26_第二轮独立核验结果.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='file_checks'},ensure_ascii=False,indent=2,default=str))
print('file_checks:',result['file_checks'])
