"""Read-only bounded review; writes audit evidence only, never a market database."""
from pathlib import Path
import json, hashlib, sqlite3, decimal, datetime, shutil, importlib.util, sys

BASE = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11')
B = BASE / '数据路整改1'
OUT = Path(__file__).resolve().parent / '16_113整改独立复审结果.json'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
receipt = load(B / '04_审查点/READY_FOR_113_REVIEW_RETRY.json')
target = load(B / '01_取证/01_目标集与旧字段冻结.json')
fresh = load(B / '01_取证/02_官方重取证据.json')
planpath = B / '03_生产只读dryrun/生产冻结计划.json'
plan = load(planpath)
iplan = load(B / '02_隔离候选/05_frozen_plan.json')
errors = []
counts = {}
def check(ok, tag, detail=None):
    counts[tag] = counts.get(tag, 0) + int(bool(ok))
    if not ok:
        errors.append({'check': tag, 'detail': detail})

manifest_mismatches = [p for p,v in receipt['artifacts'].items()
                       if sha(B / p) != v['sha256']]
self_refs = ['04_审查点/READY_FOR_113_REVIEW_RETRY.json', '04_审查点/产物SHA清单.txt']
check(not(set(manifest_mismatches) - set(self_refs)), 'payload_manifest_ok', manifest_mismatches)
for name, v in receipt['tools_reused'].items():
    check(sha(Path(v['path'])) == v['sha256'], 'approved_tool_sha', name)
oldtarget = load(BASE / '数据路/01_取证/01_目标集与旧字段冻结.json')
check(target['all_keys'] == oldtarget['all_keys'] and len(target['all_keys']) == 113
      and len(target['per_source']) == 44 and target['set_digest'] == oldtarget['set_digest'],
      'closed_set_44_113')
byf = {f['source_id']: f for f in fresh['detail']}
byp = {p['old_source_id']:p for p in plan['plans']}
dbpath = Path(target['db'])
prod = sqlite3.connect(dbpath.as_uri()+'?mode=ro', uri=True)
iso = sqlite3.connect(Path(receipt['isolation']['isolated_db']).as_uri()+'?mode=ro', uri=True)
for c in (prod, iso):
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.execute('BEGIN')
check(prod.execute('PRAGMA query_only').fetchone()[0] == 1, 'prod_query_only')
for t in ['sources','candle_versions','candle_heads']:
    a = list(prod.execute('PRAGMA table_info('+t+')'))
    z = list(iso.execute('PRAGMA table_info('+t+')'))
    check([tuple(r) for r in a] == [tuple(r) for r in z], 'schema_equal', t)
for s in target['per_source']:
    sid = s['source_id']; f = byf[sid]; body = Path(f['body_path'])
    check(sha(body) == f['body_sha256'], 'body_sha', sid)
    raw = load(body)
    check(raw['code'] == '0' and f['request']['bar']=='12Hutc', 'official_closed_utc_source', sid)
    rr = {int(r[0]):r for r in raw['data']}
    for c,label in [(prod,'prod'),(iso,'iso')]:
        check(dict(c.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()) == s['source_all_fields'],
              'old_source_'+label, sid)
    pp = byp.get(sid)
    ns = None
    if pp:
        ns = iso.execute('SELECT * FROM sources WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND sha256=?',
                ('okx','swap',s['symbol'],'12h',pp['new_sha256'])).fetchone()
    for old in s['versions_all_fields']:
        key = tuple(old[k] for k in ['exchange','market','symbol','timeframe','open_time'])
        r = rr.get(old['open_time'])
        check(r is not None, 'raw_row_found', key)
        if r is None: continue
        ok = all(decimal.Decimal(old[n]) == decimal.Decimal(r[i]) for n,i in
                 [('open',1),('high',2),('low',3),('close',4),('quote_volume',7)])
        check(ok and r[8]=='1' and old['open_time']%43200000==0
              and old['close_time']==old['open_time']+43200000-1, 'raw_ohlcq_close_grid', key)
        changed = decimal.Decimal(old['volume']) != decimal.Decimal(r[6])
        counts['changed' if changed else 'same'] = counts.get('changed' if changed else 'same',0)+1
        for c,label in [(prod,'prod'),(iso,'iso')]:
            z = c.execute('SELECT * FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time=? AND source_id=?',key+(sid,)).fetchone()
            check(dict(z)==old, 'old_row_'+label, key)
        hp = prod.execute('SELECT source_id FROM candle_heads WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time=?',key).fetchone()[0]
        hi = iso.execute('SELECT source_id FROM candle_heads WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time=?',key).fetchone()[0]
        check(hp==sid, 'prod_head_on_old', key)
        if changed:
            z = iso.execute('SELECT * FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time=? AND source_id=?',key+(ns['id'],)).fetchone()
            expected=dict(old);expected.update(volume=str(r[6]),source_id=ns['id'],available_at=ns['imported_at'])
            fetched=int(datetime.datetime.fromisoformat(f['fetched_at'].replace('Z','+00:00')).timestamp()*1000)
            check(dict(z)==expected and hi==ns['id'] and z['available_at']>=fetched, 'iso_new_allfields_available_head', key)
        else: check(hi==sid, 'noop_head_iso', key)
collector = [dict(r) for r in prod.execute('SELECT name,pid,started_at,heartbeat_at,status,detail FROM collector_state')]
for c in (prod,iso):c.rollback();c.close()
modulepath = Path(receipt['tools_reused']['volccy_common.py']['path'])
spec=importlib.util.spec_from_file_location('review_volccy_common',modulepath)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
loaded=C.load_frozen_plan(plan,str(dbpath))
check(C.plan_global_digest(loaded['plans'])==plan['plan_global_digest'], 'production_plan_load_digest')
check(plan['plans']==iplan['plans'] and plan['totals']==iplan['totals'], 'production_isolation_same_plan')
check(plan['db_identity']==json.loads(json.dumps(C.db_identity(str(dbpath)))), 'live_production_identity')
rm={str(k):v['repair_ms'] for k,v in load(B/'02_隔离候选/06_journal.json')['sources'].items()}
accept=C.full_acceptance(receipt['isolation']['isolated_db'],iplan,
         raw_dir=iplan['raw_dir'],repair_ms_by_old=rm,phase='accept')
check(accept['verdict']=='PASS', 'independent_common_allfield_acceptance', accept['aggregate'])
neg=load(B/'02_隔离候选/07_反例测试.json')
actual={p.name:sha(p) for p in (B/'01_取证/fresh_bodies/12h').glob('*.body.json')}
check(actual==neg['body_sha_guard']['before']==neg['body_sha_guard']['after'], 'body_before_after_actual_unchanged')
polluted=BASE/'数据路/01_取证/fresh_bodies/12h/1282499_CRO-USDT-SWAP.body.json'
check(sha(polluted)=='28ab4236f757507a6321c9ad3ead548bcd21cd5ec308056c07df3a86a16db3ae', 'old_failed_evidence_preserved')
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'read_only':True,
        'counts':counts,'errors':errors,'verdict':'PASS_CANDIDATE_AND_PRODUCTION_PLAN' if not errors else 'FAIL',
        'production_plan_path':str(planpath),'production_plan_file_sha256':sha(planpath),
        'plan_global_digest':plan['plan_global_digest'],'source_identity_digest':plan['source_identity_digest'],
        'approved_plan_totals':plan['totals'],'schema_and_identity':C.db_identity(str(dbpath)),
        'isolated_full_acceptance':{'verdict':accept['verdict'],'aggregate':accept['aggregate']},
        'manifest_self_reference_errors':manifest_mismatches,'payload_manifest_checked':len(receipt['artifacts'])-len(self_refs),
        'collector':collector,'free_gib':shutil.disk_usage(dbpath.parent).free/2**30,
        'wal_bytes':Path(str(dbpath)+'-wal').stat().st_size,'formal_backtest_accepted':False}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(0 if not errors else 1)
