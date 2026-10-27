#!/usr/bin/env python3
"""只读当前 WX 配置与交付字节；不写生产行情、不发起回测。"""
import hashlib, json, os, shutil, sys, urllib.request
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
OUT=HERE/'5000U独立终验';OUT.mkdir(exist_ok=True)
BACKEND=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/后端实现')
ROOT=BACKEND.parent/'最新唐奇安威科夫接通与收尾_2026-10-11'
DELIVERY=ROOT/'5000U收尾与回测条件终验6'
sys.path.insert(0,str(BACKEND))
from strategies import wx_config,wx_donchian_wyckoff as WX,wx_gate,wx_execution
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
old=json.loads((OUT/'43_旧10000视图独立基线.json').read_text())
view,vsha=wx_config.load_view()
result={'source':'Codex read-only capital/input preflight','errors':[],'delivery':str(DELIVERY),'view_path':WX.EFFECTIVE_VIEW_PATH,'view_sha':vsha,'formal_backtest_run':False}
def check(name,ok,detail):
    result[name]={'ok':bool(ok),'detail':detail}
    if not ok:result['errors'].append(name)
check('old_view_preserved',sha(old['view_path'])==old['sha256'],sha(old['view_path']))
check('new_view_identity',vsha!=old['sha256'] and wx_config.identity_check(view,vsha)['ok'],wx_config.identity_check(view,vsha))
check('capital_model_5000',(view.get('capital_model') or {}).get('total_initial_equity_USDT_per_research_arm')==5000,view.get('capital_model'))
check('risk_capital_5000',(view.get('risk') or {}).get('initial_equity_USDT')==5000,view.get('risk'))
for k in ['signals','execution','sample','registered_configs']:
    check(k+'_unchanged',view.get(k)==old['before'].get(k),{'equal':view.get(k)==old['before'].get(k)})
riskold=dict(old['before']['risk']);risknew=dict(view['risk']);riskold.pop('initial_equity_USDT',None);risknew.pop('initial_equity_USDT',None)
check('risk_ratios_unchanged',riskold==risknew,{'equal':riskold==risknew})
configs=[]
for item in view.get('registered_configs',[]):
    resolved=wx_config.resolve(None,item['id'])
    configs.append({'id':item['id'],'identity_ok':resolved.get('identity_ok'),'exec':resolved.get('exec'),'param_hash':resolved.get('config_hash'),'run_frozen':resolved.get('run_frozen')})
check('all_18_resolved',len(configs)==18 and all(r['identity_ok'] for r in configs),configs)
policy=wx_execution.RiskConfig.from_view(view)
check('executor_policy_5000',policy.initial_equity==5000,policy.initial_equity)
check('executor_default_5000',wx_execution.RiskConfig().initial_equity==5000,wx_execution.RiskConfig().initial_equity)
check('gate_capital_5000',(wx_gate.BINDING_SPEC.get('capital',{}).get('equals') or {}).get('initial_equity_USDT')==5000,wx_gate.BINDING_SPEC.get('capital'))
result['real_binding']=wx_gate.compute_real_bindings()
result['normal_gate']=wx_gate.binding()
result['manifest_checks']=[]
for manifest in DELIVERY.glob('*SHA*.json'):
    m=json.loads(manifest.read_text());rows=m.get('files',[])
    if not isinstance(rows,list):continue
    errors=[]
    for row in rows:
        if not isinstance(row,dict) or not row.get('file'):continue
        p=DELIVERY/row['file']
        if not p.exists() or sha(p)!=row.get('sha256') or ('bytes' in row and p.stat().st_size!=row['bytes']):errors.append(row['file'])
    result['manifest_checks'].append({'file':str(manifest),'count':len(rows),'errors':errors})
for ready in DELIVERY.glob('READY*.json'):
    result.setdefault('ready_documents',{})[ready.name]=json.loads(ready.read_text())
pilot=ROOT/'数据终验与输入试点2/02_pilot_export_readin.json'
if pilot.exists():
    doc=json.loads(pilot.read_text())
    result['old_pilot_aux_exists']={k:Path(v['path']).exists() for k,v in doc['mark_funding_status'].items()}
result['health']={}
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for ep in ['/api/health','/api/v1/strategies/registered']:
    try:
        with op.open('http://127.0.0.1:8787'+ep,timeout=5) as r:result['health'][ep]={'http':r.status,'doc':json.load(r)}
    except Exception as e:result['health'][ep]={'error':str(e)}
result['free_GiB']=round(shutil.disk_usage('/Users/boos').free/1024**3,2)
(OUT/'43_资本与输入独立结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
print(json.dumps({'errors':result['errors'],'manifest_checks':result['manifest_checks'],'capital':policy.initial_equity,'normal_gate_all_pass':result['normal_gate'].get('all_pass'),'free_GiB':result['free_GiB']},ensure_ascii=False,indent=2))
sys.exit(1 if result['errors'] else 0)
