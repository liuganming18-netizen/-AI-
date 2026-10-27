#!/usr/bin/env python3
# coding: utf-8
"""Focused independent review. Read production only; synthetic outputs isolated."""
import datetime, hashlib, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
HERE=Path(__file__).resolve().parent
OUT=HERE/'第三轮独立复审';OUT.mkdir(exist_ok=True)
BASE=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究')
ROOT=BASE/'最新唐奇安威科夫接通与收尾_2026-10-11'
S=ROOT/'接线路整改3';D=ROOT/'数据终验与输入试点2'
FTPY='/Users/boos/.workbuddy-ai/binaries/python/envs/freqtrade/bin/python'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
res={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'production_written':False,'work_original_overwritten':False}
checks=[]
for folder,name in [(S,'文件SHA清单.json'),(D,'07_非递归SHA清单.json')]:
 doc=json.loads((folder/name).read_text())
 for key in ['payload','deliverables','business_sources']:
  for v in doc.get(key,[]):
   p=Path(v.get('file',v.get('path')))
   if not p.is_absolute():p=folder/p
   checks.append({'path':str(p),'ok':p.exists() and sha(p)==v['sha256'] and p.stat().st_size==v['bytes']})
res['SHA_checks']={'count':len(checks),'bad':[v for v in checks if not v['ok']]}
rec=json.loads((D/'08_READY_FOR_DATA_INPUT_PILOT_REVIEW.json').read_text())
res['data_receipt_manifest_match']=sha(D/'07_非递归SHA清单.json')==rec['manifest']['sha256']
tmp=Path(tempfile.mkdtemp(prefix='codex_round3_review_'))
scripts=['Freqtrade真实引擎_逐笔对拍_整改3.py','03_对拍篡改反例_整改3.py','02_严格反例_整改3.py','04_复用真实调用证据.py']
for name in scripts:(tmp/name).write_bytes((S/name).read_bytes())
runs=[]
for name in scripts:
 r=subprocess.run([FTPY,str(tmp/name)],capture_output=True,text=True,timeout=90,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
 (OUT/(name+'.stdout.txt')).write_text(r.stdout);(OUT/(name+'.stderr.txt')).write_text(r.stderr)
 runs.append({'entry':name,'source_sha256':sha(S/name),'actual_process_exit':r.returncode})
res['isolated_runs']=runs
for p in tmp.glob('*.json'):(OUT/p.name).write_bytes(p.read_bytes())
for name in ['严格反例_整改3.json','对拍篡改反例_整改3.json','逐笔对拍_证据_整改3.json']:
 p=tmp/name
 if p.exists():
  d=json.loads(p.read_text());res[name]={k:d[k] for k in ['failed','exit_code','all_tampers_blocked','all_matched','config_coverage_all_matched'] if k in d}

# Re-read actual six files using Freqtrade, compare bounded current DB values and sidecars.
import pandas as pd
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType
db='/Users/boos/.codex/worktrees/90b1/加密货币趋势判断系统/data/market.sqlite3'
con=sqlite3.connect('file:'+db+'?mode=ro',uri=True);con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=1')
res['schema']={t:[r['name'] for r in con.execute('PRAGMA table_info('+t+')')] for t in ['candle_heads','candle_versions']}
start=int(datetime.datetime(2026,9,23,7,tzinfo=datetime.timezone.utc).timestamp()*1000)
cut=int(datetime.datetime(2026,10,10,7,tzinfo=datetime.timezone.utc).timestamp()*1000)
handler=get_datahandler(D/'pilot_datadir','feather');samples=[]
for ex,mk,sym,base in [('binance','um','BTCUSDT','BTC'),('binance','um','ETHUSDT','ETH'),('okx','swap','CRO-USDT-SWAP','CRO')]:
 for tf,iv in [('1h',3600000),('15m',900000)]:
  frame=handler.ohlcv_load(base+'/USDT:USDT',tf,CandleType.FUTURES,fill_missing=False,drop_incomplete=False,warn_no_data=False)
  rows=con.execute('SELECT h.open_time,v.open,v.high,v.low,v.close,v.volume,v.quote_volume,v.available_at,h.source_id FROM candle_heads h JOIN candle_versions v ON h.exchange=v.exchange AND h.market=v.market AND h.symbol=v.symbol AND h.timeframe=v.timeframe AND h.open_time=v.open_time AND h.source_id=v.source_id WHERE h.exchange=? AND h.market=? AND h.symbol=? AND h.timeframe=? AND h.open_time>=? AND h.open_time<? ORDER BY h.open_time',(ex,mk,sym,tf,start,cut)).fetchall()
  side=json.loads((D/'pilot_sidecar'/f'{base}_USDT_USDT-{tf}-sidecar.json').read_text())
  bad=[]
  if len(rows)!=len(frame) or len(rows)!=len(side['rows']):bad.append('row_count')
  for i,(r,fr,sr) in enumerate(zip(rows,frame.itertuples(index=False),side['rows'])):
   if int(fr.date.timestamp()*1000)!=r['open_time']:bad.append(['time',i])
   for col in ['open','high','low','close','volume']:
    if float(getattr(fr,col))!=float(r[col]):bad.append([col,i])
   db_quote=None if r['quote_volume'] is None else float(r['quote_volume'])
   if sr['quote_volume']!=db_quote or sr['source_id']!=r['source_id']:bad.append(['sidecar',i])
   expected_av=None if r['available_at'] is None else datetime.datetime.fromtimestamp(r['available_at']/1000,datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
   if sr['available_at']!=expected_av:bad.append(['available_at',i])
  samples.append({'exchange':ex,'symbol':sym,'tf':tf,'rows':len(rows),'expected':(cut-start)//iv,'value_mismatches':bad[:20],'match':not bad and len(rows)==(cut-start)//iv,'generation':side.get('generation'),'null_available_at':sum(r['available_at'] is None for r in rows),'null_quote_volume':sum(r['quote_volume'] is None for r in rows)})
res['pilot_value_comparisons']=samples
res['pilot_conversion_pass']=all(s['match'] for s in samples)
con.close()
res['pilot_research_identity_accepted']=False
res['G01_to_G05_all_accepted']=False
res['formal_backtest_allowed']=False
import urllib.request
for route in ['/api/health','/api/v1/strategies']:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8787'+route,timeout=5) as r:
   d=json.load(r);res[route]={'http':r.status,'strategy_keys':[v.get('strategy_key') for v in d.get('items',[])]}
 except Exception as e:res[route]={'error':str(e)}
p=OUT/'31_定点与试点独立结果.json';p.write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps({k:v for k,v in res.items() if k not in ['schema']},ensure_ascii=False,indent=2,default=str))
