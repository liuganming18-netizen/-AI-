#!/usr/bin/env python3
"""审查证据入口；只把 Work 合成脚本的 HERE 输出改到独立 tempfile。"""
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
HERE = Path(__file__).resolve().parent
WORK = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/接线路整改1')
TMP = Path(tempfile.mkdtemp(prefix='codex_wx_retry1_'))
results = []
for name, filename, artifact in [
    ('counter', '反例_wx_接线_独立复跑.py', '反例_接线_独立复跑结果.json'),
    ('s3a', 'S3a执行链_证据.py', 'S3a执行链证据.json'),
]:
    spec = importlib.util.spec_from_file_location('codex_retry_'+name, WORK/filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HERE = TMP
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = mod.main()
    (HERE/(name+'_独立原始输出.txt')).write_text(output.getvalue(), encoding='utf-8')
    shutil.copy2(TMP/artifact, HERE/(name+'_独立复跑结果.json'))
    results.append({'entry':str(WORK/filename),'module_main_return_code':rc,'output':str(TMP/artifact)})
summary = {'temporary_output':str(TMP),'entries':results,'production_db_read_or_written':False,
           'work_original_evidence_overwritten':False,'gate_acceptance_claimed':False}
(HERE/'02_既有入口独立执行回执.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
sys.exit(0 if all(x['module_main_return_code']==0 for x in results) else 1)
