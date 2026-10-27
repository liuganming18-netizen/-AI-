#!/usr/bin/env python3
"""只读 Work 交付；复跑输出全部写 Codex 隔离目录。"""
import concurrent.futures, copy, hashlib, importlib.util, json, os, shutil, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
OUT=HERE/'收口4独立复审'
WORK=Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/回测前门禁收口4')
PY='/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3'
OUT.mkdir(exist_ok=True)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
result={'delivery':str(WORK),'sha_errors':[],'reruns':{},'probes':{}}
manifest=json.loads((WORK/'非递归SHA清单.json').read_text())
for r in manifest['files']:
    p=WORK/r['file']
    if not p.exists() or sha(p)!=r['sha256'] or p.stat().st_size!=r['bytes']:result['sha_errors'].append(r['file'])
result['sha_count']=len(manifest['files'])
def rerun(n):
    folder=OUT/n[:2];folder.mkdir(exist_ok=True)
    shutil.copy2(WORK/'门禁_真实运行绑定.py',folder/'门禁_真实运行绑定.py')
    (folder/'日志').mkdir(exist_ok=True)
    code="import importlib.util,sys;sys.dont_write_bytecode=True;from pathlib import Path;"+f"s=importlib.util.spec_from_file_location('audit_run',{str(WORK/n)!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.HERE=Path({str(folder)!r});sys.exit(m.main())"
    r=subprocess.run([PY,'-c',code],capture_output=True,text=True,timeout=600,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    (folder/'stdout.txt').write_text(r.stdout);(folder/'stderr.txt').write_text(r.stderr)
    return n,{'actual_exit_code':r.returncode,'stdout_tail':r.stdout[-3000:],'stderr_tail':r.stderr[-1500:],'isolated_output':str(folder)}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    for n,r in ex.map(rerun,['01_回归收口_26加8.py','06_门禁_真实绑定与拒绝反例.py','07_三币代际与PIT身份.py','08_隔离API预览与回滚.py']):
        result['reruns'][n]=r
        print(n,r['actual_exit_code'],flush=True)
G=load('gate_extra',WORK/'门禁_真实运行绑定.py')
T=load('gate_fixture_extra',WORK/'06_门禁_真实绑定与拒绝反例.py')
gate=G.wx_gate
tmp=OUT/'负例';tmp.mkdir(exist_ok=True)
real=G.compute_real_bindings()
bindings={k:{f:real[k].get(f) for f in set(gate.BINDING_SPEC[k].get('required',()))|set(gate.BINDING_SPEC[k].get('equals',{}))} for k in gate.REQUIRED_BINDINGS}
run={'run_id':'independent_fixture','code_sha256':bindings['code']['source_sha256'],'data_snapshot_sha256':bindings['data']['snapshot_sha256'],'engine_sha256':bindings['engine']['sha256'],'configs_18_sha256':bindings['configs_18']['effective_sha256'],'holdout_sha256':bindings['holdout']['frozen_sha256'],'capital_USDT':10000}
reg,rsha=T._mk_registry(tmp,gate.GATE_IDS)
ev=T._mk_evidence(tmp,gate.GATE_IDS,'codex_synth_pos')
led=tmp/'ledger.json';led.write_text(json.dumps(T._ledger(bindings,ev)))
def probe(name,**kwargs):
    res=G.gate(str(led),**kwargs)
    result['probes'][name]={k:res.get(k) for k in ['all_pass','research_pass','real_bindings_ok','run_binding_ok','isolated','self_check']}
probe('unrelated_acceptance_path_sha_scope_with_explicit_synthetic_index_normal_mode',run=run,acceptance_path=str(reg),acceptance_sha=rsha,isolated=False)
probe('missing_run_accepted',acceptance_path=str(reg),acceptance_sha=rsha,isolated=False)
old=copy.deepcopy(run);old['run_id']='old_unaccepted_run'
probe('old_run_id_only_reuse',run=old,acceptance_path=str(reg),acceptance_sha=rsha,isolated=False)
wrong=copy.deepcopy(bindings);wrong['code']['source_sha256']='a'*64
wrongdoc=T._ledger(wrong,ev);wrongpath=tmp/'wrong-code-ledger.json';wrongpath.write_text(json.dumps(wrongdoc))
r=gate.binding(str(wrongpath),acceptance_path=str(reg),acceptance_sha=rsha)
result['probes']['actual_wx_gate_does_not_recompute_code']={'all_pass':r['all_pass'],'bindings_concrete':r['bindings_concrete'],'evidence_verified':r['evidence_verified']}
# 收口聚合器的缺例负例：仅替换隔离加载对象，绝不修改 Work 文件。
H=load('harness_extra',WORK/'01_回归收口_26加8.py')
H.HERE=tmp
original=json.loads((OUT/'01/回归收口_26加8.json').read_text())['original_entries']
def fake_original(name,source,python):
    rec=copy.deepcopy(next(x for x in original if x['name']==name));rec['artifacts']={};return rec
H.run_original=fake_original
H.run_adapted_set=lambda:[{'name':n,'actual_exit_code':0,'cases':{},'cases_pass':0,'cases_fail':[]} for n,_,_,_ in H.ADAPTED]
rc=H.main();fake=json.loads((tmp/'回归收口_26加8.json').read_text())
result['probes']['all_case_artifacts_missing']={'outer_exit_code':rc,'unexpected_failures':fake['unexpected_failures'],'adapted_failures':fake['adapted_failures'],'counts':fake['counts']}
result['limits']=['合成负例只在隔离参数中执行，未写 Codex 生产接受索引','未改 Work 交付、业务源码、生产行情库或生产服务','G01-G05 未放行']
(OUT/'35_独立结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'sha_count':result['sha_count'],'sha_errors':result['sha_errors'],'probes':result['probes']},ensure_ascii=False,indent=2))
