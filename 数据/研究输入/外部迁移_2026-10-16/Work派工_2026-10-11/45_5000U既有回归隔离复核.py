#!/usr/bin/env python3
"""复用Work已交付入口；仅改变审查输出目录，不写生产或Work原产物。"""
import hashlib, json, os, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode = True
D = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11/5000U收尾与回测条件终验6')
O = Path(__file__).resolve().parent / '5000U独立终验/45_隔离复跑'
O.mkdir(parents=True, exist_ok=True)
names = ['01_5000门禁接入_负例与正例.py', '02_5000执行与账本_负例与正例.py', '03_回归缺例收口_5000.py', '04_回归缺例_负例_5000.py']
rows = []
for i, name in enumerate(names):
    src = D / name
    dest = O / str(i + 1)
    dest.mkdir(exist_ok=True)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    code = '''import importlib.util,sys
from pathlib import Path
sys.dont_write_bytecode=True
s=importlib.util.spec_from_file_location('audit_target',sys.argv[1])
m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
m.HERE=Path(sys.argv[2])
sys.exit(m.main())
'''
    r = subprocess.run([sys.executable, '-c', code, str(src), str(dest)], cwd=dest,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                       capture_output=True, text=True, timeout=180)
    after = hashlib.sha256(src.read_bytes()).hexdigest()
    (dest/'stdout.txt').write_text(r.stdout)
    (dest/'stderr.txt').write_text(r.stderr)
    row = {'source':str(src),'source_sha256':before,'source_unchanged':before==after,
           'actual_child_exit_code':r.returncode,'output_dir':str(dest)}
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
result={'isolated_only':True,'formal_backtest':False,'production_db_read_or_written':False,
        'work_evidence_overwritten':False,'research_pass_claimed':False,'entries':rows}
(O/'45_实际子退出码.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(0 if all(r['actual_child_exit_code']==0 and r['source_unchanged'] for r in rows) else 1)
