#!/usr/bin/env python3
"""仅审查入口：复跑 Work 合成反例并复现 8 项缺陷；不读写生产库或 Work 证据。

用 Python 3.13 执行。临时数据库只在 tempfile/memory；最终输出写本审查目录。
返回码 1 表示至少一个本应拒绝的反例未被拒绝，不能作为策略验收 PASS。
"""
import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from dataclasses import replace

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
HERE = Path(__file__).resolve().parent
BACKEND = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/后端实现')
WORK = BACKEND.parent / '最新唐奇安威科夫接通与收尾_2026-10-11/接线路'
TMP = Path(tempfile.mkdtemp(prefix='codex_wx_review_'))
sys.path.insert(0, str(BACKEND))

spec = importlib.util.spec_from_file_location('wx_review_factory', WORK / '反例_wx_core.py')
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
cc.HERE = TMP  # 原脚本会写 HERE；改为独立 tempfile，绝不覆盖 Work 原证据。
core_log = io.StringIO()
with contextlib.redirect_stdout(core_log):
    core_rc = cc.main()
(HERE / '06_原18例独立复跑命令输出.txt').write_text(core_log.getvalue(), encoding='utf-8')

# 一事件脚本唯一变更是末尾证据输出路径；其 migrate(db) 参数由原脚本 tempfile 生成。
chain_script = WORK / '一事件完整ID链.py'
tree = ast.parse(chain_script.read_text(encoding='utf-8'))
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'p' for t in node.targets):
        node.value = ast.Constant(str(TMP / 'signal_chain.json'))
ast.fix_missing_locations(tree)
chain_log = io.StringIO()
chain_ns = {'__name__': 'codex_review_chain', '__file__': str(chain_script)}
with contextlib.redirect_stdout(chain_log):
    exec(compile(tree, str(chain_script), 'exec'), chain_ns)
(HERE / '07_信号ID链独立复跑命令输出.txt').write_text(chain_log.getvalue(), encoding='utf-8')

from strategies import common, wx_config, wx_donchian_wyckoff as wx, registry
import strategy_runner

facts = {}
bars = [cc.flat(i) for i in range(240)] + [cc.mk(240, 100, 102, 99, 101.5, 2000)]
con = sqlite3.connect(':memory:')
con.row_factory = sqlite3.Row
con.executescript((BACKEND / 'business_schema.sql').read_text(encoding='utf-8'))
con.executescript((BACKEND / 'business_schema_v2.sql').read_text(encoding='utf-8'))
con.execute('CREATE TABLE sources(id INTEGER PRIMARY KEY, sha256 TEXT)')
con.executemany('INSERT INTO sources VALUES(?,?)', [(1, 'a' * 64), (2, 'b' * 64)])
dv1, src1, _ = strategy_runner.data_version_for(con, bars)
changed = list(bars)
changed[-1] = replace(changed[-1], close=101.6)
dv2, _, _ = strategy_runner.data_version_for(con, changed)
facts['R01_consumed_value_revision'] = {
    'expected': '实际消费 close 修订必须改变该数据身份',
    'before_close': 101.5, 'after_close': 101.6, 'dv_before': dv1, 'dv_after': dv2,
    'accepted': dv1 != dv2,
}

partial = list(bars)
partial[-1] = replace(partial[-1], source_id=None)
dvp, srcp, unknown = strategy_runner.data_version_for(con, partial)
events = wx.run(partial, cc.resolved('3a'), '3a')['events']
original_source = events[0].source_id
write = strategy_runner.write_events(con, events, 'binance', 'um', 'ISOLATED', '1h', dvp, srcp)
facts['R02_partial_unknown_source'] = {
    'expected': '部分消费来源未知也必须拒绝正式事件，不能用首个来源补身份',
    'unknown_source': unknown, 'write': write,
    'original_source_id': original_source, 'stored_source_id': events[0].source_id,
    'accepted': write['written'] == 0 and events[0].source_id is None,
}
con.execute('DELETE FROM signal_conditions')
con.execute('DELETE FROM signals')
con.commit()
events = wx.run(bars, cc.resolved('3a'), '3a')['events']
w1 = strategy_runner.write_events(con, events, 'binance', 'um', 'ISOLATED', '1h', dv1, src1)
extended = bars + [replace(cc.flat(241), source_id=2)]
dve, srce, _ = strategy_runner.data_version_for(con, extended)
w2 = strategy_runner.write_events(con, events, 'binance', 'um', 'ISOLATED', '1h', dve, srce)
count = con.execute('SELECT count(*) FROM signals').fetchone()[0]
facts['R03_future_prefix_extension'] = {
    'expected': '无关未来来源不能重写旧事件身份，同事件仅一条记录',
    'event_uid': events[0].evidence['event_uid'], 'dv_before': dv1, 'dv_after': dve,
    'write_before': w1, 'write_after': w2, 'signals_count': count, 'accepted': count == 1,
}

view, _ = wx_config.load_view()
mutated = copy.deepcopy(view)
mutated['signals']['don_entry_bars'] = 21
mutated['registered_configs'][0]['id'] = 'outside_registration'
load_original = wx_config.load_view
wx_config.load_view = lambda path=None: (mutated, 'unaccepted_changed_view')
try:
    resolved = wx_config.resolve(None, '3a')
    status, _, _ = common.validate_config(resolved, wx.REQUIRED_CONFIG, wx.VALIDATION, wx.RELATIONS)
    facts['R04_view_binding_drift'] = {
        'expected': '未登记的完整视图/18配置/参数漂移必须拒绝解析',
        'registered_check': wx_config.registered_configs_check(mutated)['status'],
        'resolved_hash_check': resolved['hash_check'], 'validation_status': status,
        'actual_don_entry_bars': resolved['config']['don_entry_bars'],
        'accepted': status != common.STATUS_OK or resolved['hash_check'] != 'ok',
    }
finally:
    wx_config.load_view = load_original

cfg = wx_config.signal_config_for(view, '3a')
cfg['warmup_contiguous_bars'] = 20
con.execute('INSERT INTO strategy_configs(strategy_key,version,config_json,config_hash,frozen,created_at) VALUES(?,?,?,?,?,?)',
            ('3a', 'unaccepted_override', json.dumps(cfg), common.config_hash(cfg), 1, 1))
con.commit()
resolved = wx_config.resolve(con, '3a')
description = registry.describe(con, '3a')
facts['R05_db_override_run_gate'] = {
    'expected': 'DB自洽hash/frozen不能覆盖生效参数及 G01–G05 放行',
    'view_run_frozen': view['run_frozen'], 'resolved_frozen': resolved['frozen'],
    'config_source': resolved['source'], 'warmup': resolved['config']['warmup_contiguous_bars'],
    'hash_check': resolved['hash_check'], 'registry_run_status': description['run_status'],
    'registry_gate_status': description['gate_status'],
    'accepted': not resolved['frozen'] and resolved['config']['warmup_contiguous_bars'] == 240,
}
shifted = [replace(b, open_time=b.open_time + 900000, close_time=b.close_time + 900000) for b in bars]
shifted_result = wx.run(shifted, cc.resolved('3a'), '3a')
facts['R06_native_hour_grid'] = {
    'expected': '原生1h UTC网格不对齐必须拒绝，不能用仅相邻差1h代替原生对齐',
    'open_offset_ms': shifted[0].open_time % 3600000,
    'segments': shifted_result['diagnostics']['segments'], 'events': len(shifted_result['events']),
    'accepted': len(shifted_result['events']) == 0,
}
invalid = list(bars)
invalid[-1] = replace(invalid[-1], close=103.0, quote_volume=-1)
invalid_result = wx.run(invalid, cc.resolved('3a'), '3a')
facts['R07_illegal_bar_values'] = {
    'expected': 'close>high 与负quote不得产生策略信号；真实合法0另行保留',
    'close': 103.0, 'high': 102.0, 'quote_volume': -1, 'events': len(invalid_result['events']),
    'accepted': len(invalid_result['events']) == 0,
}
c2 = sqlite3.connect(':memory:')
c2.row_factory = sqlite3.Row
c2.execute('CREATE TABLE candles(exchange TEXT,market TEXT,symbol TEXT,timeframe TEXT,is_closed INTEGER,open_time INTEGER,close_time INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,quote_volume REAL,trade_count INTEGER,taker_buy_volume REAL,taker_buy_quote_volume REAL,source_id INTEGER)')
b = bars[-1]
cutoff = b.open_time + 1800000
c2.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
           ('binance', 'um', 'ISOLATED', '1h', 1, b.open_time, b.close_time, b.open, b.high,
            b.low, b.close, b.volume, b.quote_volume, 1, 0, 0, 1))
read, _, lifecycle = strategy_runner.read_bars(c2, 'binance', 'um', 'ISOLATED', '1h', end=cutoff)
facts['R08_asof_cutoff'] = {
    'expected': '截至时刻只能读到已经完整收盘且因果可用的bar',
    'requested_end_ms': cutoff, 'returned_close_ms': read[0].close_time,
    'bars': len(read), 'lifecycle_applied': lifecycle['apply_lifecycle'],
    'accepted': all(b.close_time <= cutoff for b in read),
}
chain = json.loads((TMP / 'signal_chain.json').read_text(encoding='utf-8'))
result = {
    'core_exit_code': core_rc, 'core_passed': json.loads((TMP / '反例_wx_core_结果.json').read_text())['passed'],
    'temporary_output': str(TMP), 'chain_db': chain['isolation']['business_db'],
    'production_db_read_or_written': False, 'work_evidence_overwritten': False,
    'signal_chain_is_execution_ledger': False,
    'old_2020_signal_while_run_not_frozen': {
        'config_frozen': chain['config_identity']['frozen'],
        'confirmed_at': chain['api_readback']['brief']['confirmed_at'],
        'actionable': chain['api_readback']['brief']['actionable'],
    },
    'strict_acceptance': facts,
}
failures = [key for key, value in facts.items() if not value['accepted']]
result['failed_required_cases'] = failures
result['strict_acceptance_exit_code'] = 1 if failures else 0
(HERE / '08_新增8反例严格验收结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
con.close()
c2.close()
sys.exit(result['strict_acceptance_exit_code'])
