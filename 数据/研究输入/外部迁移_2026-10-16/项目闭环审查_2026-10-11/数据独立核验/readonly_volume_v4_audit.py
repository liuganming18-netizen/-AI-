#!/usr/bin/env python3
"""Codex bounded read-only audit. Writes only beside this script; never opens DB rw."""
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
AUDIT_STARTED = time.monotonic()
OUT = Path(__file__).resolve().parent
WORK = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/OKX成交量v4分批执行_2026-10-11')
V4 = WORK.parent / 'OKX成交量生产修复与验收_2026-10-11/工具v4候选'
FROZEN = WORK.parent / 'OKX成交量执行验收整改_2026-10-11/05_生产冻结计划_v3.json'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
report = {'scope': 'OKX volume v4 63 approved sources only', 'production_write': False,
          'full_database_scan': False, 'full_history_acceptance': False, 'backtest_ready': False,
          'checks': {}, 'limitations': []}
problems = []

def check(name, ok, detail=None):
    report['checks'][name] = {'ok': bool(ok), 'detail': detail}
    if not ok:
        problems.append(name)

pre = load(WORK / '00_前置校验_v4.json')
check('approved_tool_hashes', all(sha(V4 / n) == v['expected'] for n, v in pre['tool_sha256'].items()))
check('approved_frozen_plan_sha', sha(FROZEN) == 'f30640f5c1e67311550adee27a7fb689561f85c13d4457d0a266d1bfc4750a6c')
manifest = []
for line in (WORK / '46_产物SHA清单_v4.txt').read_text().splitlines():
    wanted, name = line.split('  ', 1)
    manifest.append({'file': name, 'ok': sha(WORK / name) == wanted})
report['work_artifact_sha'] = {'total': len(manifest), 'matching': sum(x['ok'] for x in manifest),
                             'mismatch': [x['file'] for x in manifest if not x['ok']]}
check('non_self_artifact_hashes', all(x['ok'] for x in manifest if x['file'] != '46_产物SHA清单_v4.txt'))
report['limitations'].append('Work SHA manifest self-entry is empty-file SHA and cannot verify itself; other 50 artifacts match.')
spec = importlib.util.spec_from_file_location('volccy_common', V4 / 'volccy_common.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
plan = load(FROZEN)
report['generated_utc'] = C.utc_now_str()
report['db'] = C.PROD_DB
identity = C.db_identity(C.PROD_DB)
report['db_identity'] = identity
check('real_db_identity', not C.identity_matches(identity, plan['db_identity']))
scope = set(load(C.SCOPE_JSON_DEFAULT)['okx_symbols'])
check('scope_23_matches', len(scope) == 23 and C.scope_digest(scope) == plan['scope_digest'], sorted(scope))
plans = plan['plans']
check('plan_global_digest', C.plan_global_digest(plans) == plan['plan_global_digest'])
check('plan_63_122543', len(plans) == 63 and sum(len(p['fixed_rows']) for p in plans) == 122543)
check('crypto_only', all(p['exchange'] == 'okx' and p['market'] == 'swap' and p['symbol'] in scope for p in plans))
journals = [load(WORK / n) for n in ('10_stageA_4src.journal.json', '20_stageB_cro.journal.json', '30_stageC_58src.journal.json')]
entries = {}
for j in journals:
    check('journal_identity_' + j['plan_global_digest'][:8], not C.identity_matches(identity, j['db_identity']))
    for sid, e in j['sources'].items():
        if sid in entries:
            problems.append('duplicate_journal_source')
        entries[sid] = e
check('journals_cover_frozen_ids', set(entries) == {str(p['old_source_id']) for p in plans})
check('journal_first_repair_and_verified', all(e['stage'] == 'verified' and e['repair_ms'] and
      e['rows_planned'] == e['rows_written'] == e['heads_switched'] for e in entries.values()))
repair = {sid: e['repair_ms'] for sid, e in entries.items()}
print('Readonly full_acceptance started', flush=True)
acc = C.full_acceptance(C.PROD_DB, plan, raw_dir=plan['raw_dir'], repair_ms_by_old=repair)
(OUT / '01_full_acceptance.json').write_text(json.dumps(acc, ensure_ascii=False, indent=1))
report['full_acceptance'] = {'verdict': acc['verdict'], 'aggregate': acc['aggregate']}
check('full_acceptance_PASS', acc['verdict'] == 'PASS', acc['aggregate'])
print('Readonly full_acceptance completed: ' + acc['verdict'], flush=True)

con = C.open_ro(C.PROD_DB)
report['query_only'] = con.execute('PRAGMA query_only').fetchone()[0]
report['schema'] = {t: [dict(r) for r in con.execute('PRAGMA table_info(' + t + ')')]
                    for t in ['sources', 'candle_versions', 'candle_heads']}
report['query_plan_bounded'] = [list(r) for r in con.execute(
    'EXPLAIN QUERY PLAN SELECT * FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND source_id=? AND open_time BETWEEN ? AND ?',
    ('okx', 'swap', 'CRO-USDT-SWAP', '15m', 4125198, 0, 9999999999999))]
before_src = load(WORK / '01a_执行前_旧sources全字段快照_v4.json')
old_ids = sorted(p['old_source_id'] for p in plans)
after_src = [dict(r) for r in con.execute('SELECT * FROM sources WHERE id IN (' + ','.join('?' for _ in old_ids) + ') ORDER BY id', old_ids)]
check('old_sources_all_13_fields_frozen', before_src == after_src, {'rows': len(after_src)})
before_ver = load(WORK / '01b_执行前_旧versions全字段快照_v4.json')
fields = 'open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,available_at,is_closed'
per_source = []
raw_bind_bad = []
tot_old = tot_new = tot_head = tot_changed = tot_same = 0
for p in plans:
    sid = p['old_source_id']
    key = (p['exchange'], p['market'], p['symbol'], p['timeframe'])
    old = [list(r) for r in con.execute('SELECT ' + fields + ' FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND source_id=? AND open_time BETWEEN ? AND ? ORDER BY open_time', (*key, sid, p['first_open'], p['last_close']))]
    old_ok = old == before_ver[str(sid)]['rows']
    new = dict(con.execute('SELECT * FROM sources WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND sha256=?', (*key, p['new_sha256'])).fetchone())
    journal = entries[str(sid)]
    metadata = (new['id'] == journal['new_source_id'] and new['source_url'] == p['source_url'] and
                new['imported_at'] == journal['repair_ms'] and new['downloaded_at'] == p['downloaded_at'] and
                new['first_open'] == p['first_open'] and new['last_close'] == p['last_close'] and
                new['row_count'] == p['old_row_count'] and new['file_path'] == '')
    # Complete symbol/timeframe prefix, efficient primary-key lookup, no source-id-only full scan.
    nv = [dict(r) for r in con.execute('SELECT * FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND source_id=? ORDER BY open_time', (*key, new['id']))]
    nh = [dict(r) for r in con.execute('SELECT v.* FROM candle_heads h JOIN candle_versions v ON v.exchange=h.exchange AND v.market=h.market AND v.symbol=h.symbol AND v.timeframe=h.timeframe AND v.open_time=h.open_time AND v.source_id=h.source_id WHERE h.exchange=? AND h.market=? AND h.symbol=? AND h.timeframe=? AND h.open_time BETWEEN ? AND ? ORDER BY h.open_time', (*key, p['first_open'], p['last_close']))]
    exp = {int(ot): v for ot, v in p['fixed_rows']}
    old_by_ot = {r[0]: r for r in old}
    equal = len(nv) == len(exp) and nv == nh
    for r in nv:
        ot = r['open_time']
        if ot not in exp or r['available_at'] != journal['repair_ms'] or not C.num_eq(r['volume'], exp.get(ot)):
            equal = False
        prior = old_by_ot.get(ot)
        if prior is None:
            equal = False
            continue
        # Only volume/source/availability may differ. All other fields remain exactly equal.
        for n, val in zip(fields.split(','), prior):
            if n not in ('volume', 'available_at') and r[n] != val:
                equal = False
        if C.num_eq(r['volume'], prior[6]):
            tot_same += 1
        else:
            tot_changed += 1
    links = C.load_sidecar(plan['raw_dir'], p['timeframe']).get(sid) or []
    bodies, berrs, _ = C.collect_bodies(links)
    errors = list(berrs)
    for lk in links:
        errors += C.validate_link_binding(lk, *key)
        if int(lk['source_id']) != sid or int(lk['open_times_n']) != len(old):
            errors.append('source or open_times_n')
        dg = C.sha256_text(','.join(str(r[0]) for r in old))
        if lk.get('open_times_digest') != dg:
            errors.append('open_times_digest')
        for ev in lk['evidence']:
            u = urlparse(ev['url']); qs = parse_qs(u.query)
            bar = {'12h':'12Hutc', '4h':'4H', '1h':'1H', '30m':'30m', '15m':'15m'}[p['timeframe']]
            if u.hostname != 'www.okx.com' or qs.get('instId') != [p['symbol']] or qs.get('bar') != [bar]:
                errors.append('URL instrument/timeframe binding')
    if errors:
        raw_bind_bad.append({'old_source_id': sid, 'errors': errors[:4]})
    rec = {'old_source_id': sid, 'new_source_id': new['id'], 'old_rows':len(old), 'new_rows':len(nv), 'current_head_rows':len(nh),
           'old_all_fields_including_available_at_frozen': old_ok, 'new_all_metadata_ok': metadata,
           'current_heads_exact_new_versions': equal, 'sidecar_expected_binding_ok': not errors,
           'repair_ms': journal['repair_ms'], 'new_version_available_at': sorted({r['available_at'] for r in nv})}
    per_source.append(rec)
    tot_old += len(old); tot_new += len(nv); tot_head += len(nh)
check('old_versions_all_fields_frozen', all(x['old_all_fields_including_available_at_frozen'] for x in per_source), tot_old)
check('new_sources_all_metadata_and_first_time', all(x['new_all_metadata_ok'] for x in per_source),len(per_source))
check('all_heads_all_values_correct', all(x['current_heads_exact_new_versions'] for x in per_source) and tot_head == 122543,
      {'old':tot_old,'new':tot_new,'heads':tot_head,'changed':tot_changed,'same':tot_same})
check('sidecar_full_expected_binding', not raw_bind_bad, raw_bind_bad[:5])
report['per_source'] = per_source
opens = load(WORK / '42_遗留OPEN清单_v4.json')
open_result=[]
for s in opens:
    sr = dict(con.execute('SELECT * FROM sources WHERE id=?', (s['source_id'],)).fetchone())
    key=(sr['exchange'],sr['market'],sr['symbol'],sr['timeframe'])
    hc=con.execute('SELECT COUNT(*) FROM candle_heads WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time BETWEEN ? AND ? AND source_id=?', (*key,sr['first_open'],sr['last_close'],s['source_id'])).fetchone()[0]
    vc=con.execute('SELECT COUNT(*) FROM candle_versions WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND open_time BETWEEN ? AND ? AND source_id=?', (*key,sr['first_open'],sr['last_close'],s['source_id'])).fetchone()[0]
    successors=con.execute('SELECT COUNT(*) FROM sources WHERE exchange=? AND market=? AND symbol=? AND timeframe=? AND source_url LIKE ?', (*key, '%supersedes='+str(s['source_id'])+'%')).fetchone()[0]
    open_result.append({'source_id':s['source_id'],'rows':s['rows'],'current_heads':hc,'versions':vc,'still_unrevised':C.REV_TAG not in sr['source_url'],'revision_successors':successors})
check('113_open_still_present', len(opens)==44 and sum(s['rows'] for s in opens)==113 and all(x['still_unrevised'] and x['revision_successors']==0 and x['current_heads']==x['versions']==x['rows'] for x in open_result),open_result)
report['collector_state'] = [dict(r) for r in con.execute('SELECT * FROM collector_state')]
con.close()
report['live_health'] = C.check_health(C.DEFAULT_HEALTH_URL)
report['free_gib'] = C.free_gb(C.PROD_DB)
report['wal_gib'] = C.wal_bytes(C.PROD_DB)/1024**3
check('healthy_capacity_now', report['live_health'][0] and report['free_gib']>=12 and report['wal_gib']<=2)
cut = load(WORK / '43_15m549固定cutoff复核_v4.json')
report['work_fixed_cutoff_claim'] = {k:v for k,v in cut.items() if k!='per_symbol'}
report['limitations'] += ['15m 549/549 is Work fixed-cutoff coverage claim; no full-pool coverage or 2887 unavailable proof rerun in this narrow audit.',
 'Cross-identity source_id-only global count was not repeated because query plan scans the entire 128M candle_versions; all approved instrument/timeframe prefixes independently reconcile.',
 'Production-period historical capacity/health is Work evidence; live health and capacity were independently checked now.']
report['data_repair_verdict'] = 'PASS' if not problems else 'FAIL'
report['problems'] = problems
report['elapsed_seconds'] = round(time.monotonic() - AUDIT_STARTED, 6)
(OUT/'02_bounded_independent_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=1))
print(json.dumps({'verdict':report['data_repair_verdict'],'problems':problems,'rows':tot_head,
                  'elapsed_seconds':report['elapsed_seconds'], 'work_sha':report['work_artifact_sha'],'free_gib':report['free_gib'],'wal_gib':report['wal_gib']},ensure_ascii=False,indent=1),flush=True)
sys.exit(0 if not problems else 1)
