"""只读审核：由现有文件导出逐币清单；不联网、不改源码/行情/旧证据。"""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import Counter
import csv, json, hashlib, time
import numpy as np
import pyarrow.feather as pa

TASK = Path(__file__).parent
OUT = TASK / '逐币抽查清单_2026-10-11'
BASE = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究')
R = BASE / '最新唐奇安威科夫接通与收尾_2026-10-11'
W8 = R / '正式回测输入补齐与终验8'
VIEW = R / '5000U收尾与回测条件终验6/10_研究参数生效视图_v2_2_user5000_shared.json'
BJ = ZoneInfo('Asia/Shanghai')

def sh(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write_csv(name, rows):
    with (OUT/name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

def now():
    return datetime.now(timezone.utc).isoformat()

def log(step, start, end, files):
    with (TASK/'逐步工时记录.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(dict(执行方='Codex',步骤=step,开始时间=start,
            结束时间=end,实际秒数=round((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds(),3),
            是否返工=False,返工原因=None,产物=files,时间依据='执行时实际记录'),ensure_ascii=False)+'\n')

start = now(); OUT.mkdir(exist_ok=True)
mp = W8/'07_正式研究manifest.json'
manifest = json.loads(mp.read_text())
offp = W8/'05_官方规格与生命周期证据.json'
official = json.loads(offp.read_text())
coins = official['admission_mask']
bykey = {(x['exchange'],x['symbol'],x['kind'],x.get('tf')):x for x in manifest['files']}
dates = {}; stable = {}; sizes = {}
for x in manifest['files']:
    p = Path(x['abs'])
    if p.suffix != '.feather' or not p.is_file(): continue
    before = p.stat()
    t = pa.read_table(p, columns=['date']).column(0)
    dates[str(p)] = (len(t),t[0].as_py() if len(t) else None,t[-1].as_py() if len(t) else None)
    after=p.stat(); stable[str(p)] = (before.st_mtime_ns,before.st_size)==(after.st_mtime_ns,after.st_size)
    sizes[str(p)] = after.st_size

groups = [('01_1小时价量_549币.csv','ohlcv','1h'),
          ('02_15分钟价量_549币.csv','ohlcv','15m'),
          ('03_真实成交额_1098份.csv','quote_sidecar',None),
          ('04_实际资金费_549币含缺失.csv','funding','1h'),
          ('05_标记价格_549币含缺失.csv','mark_1h','1h')]
summary = []
for name,kind,tf0 in groups:
    rows=[]
    for m in sorted(coins,key=lambda x:(x['exchange'],x['symbol'])):
        ex,sym=m['exchange'],m['symbol']
        for tf in (['1h','15m'] if kind=='quote_sidecar' else [tf0]):
            k=ex+'_'+kind
            entry=bykey.get((ex,sym,k,tf)); p=Path(entry['abs']) if entry else None
            exists=bool(p and p.is_file()); first=last=None; n=None; origin=p
            aligned='不适用'; unchanged=None
            if exists and p.suffix=='.feather':
                n,first,last=dates[str(p)];unchanged=stable[str(p)]
            elif exists and kind=='quote_sidecar':
                paired=bykey.get((ex,sym,ex+'_ohlcv',tf))
                origin=Path(paired['abs']) if paired else None
                n=len(np.load(p,mmap_mode='r'))
                if origin and str(origin) in dates:
                    count,first,last=dates[str(origin)]
                    aligned='长度一致' if n==count else '长度不一致，时间映射未接受'
                    unchanged=stable[str(origin)]
                    if n!=count:first=last=None
            rows.append({'交易所':'币安' if ex=='binance' else '欧易','币':sym,'周期':tf,
                '研究文件存在_不代表验收':'是' if exists else '否',
                '文件名':p.name if p else '无完整研究输入','绝对路径':str(p) if p else '',
                '首记录时间_比北京慢8小时':first.isoformat() if first else '',
                '末记录时间_比北京慢8小时':last.isoformat() if last else '',
                '末记录北京时间':last.astimezone(BJ).isoformat() if last else '',
                '行数':n if n is not None else '', '时间来源文件':str(origin) if exists and origin else '',
                '成交额与时间长度对齐':aligned,'读取期间文件未变':unchanged,
                '旧清单登记校验值_非本轮重新计算':entry.get('sha256','') if entry else '',
                '正式验收':'未全部接受',
                '缺失说明':('当前归档可得性未完全证明，见07清单' if ex=='binance' and kind=='mark_1h' and not exists
                    else '现有短期材料尚未形成完整研究输入' if not exists and ex=='okx' else '')})
    write_csv(name,rows)
    summary.append({'文件':name,'应有记录':len(rows),'实际存在':sum(x['研究文件存在_不代表验收']=='是' for x in rows),
                    '缺失':sum(x['研究文件存在_不代表验收']=='否' for x in rows)})

snapshot_rows=[]; binance_rows=[]
generation=json.loads((W8/'16_合约代际与OHLCV起始口径.json').read_text())
generation_rows={r['symbol']:r for r in generation['rows']}
flagged={r['symbol']:r for r in generation['flagged']}
raw_bn_path=Path(next(x['saved'] for x in official['requests'] if x['source']=='binance_fapi_exchangeInfo'))
raw_bn=json.loads(raw_bn_path.read_text()); raw_symbols={x['symbol']:x for x in raw_bn['symbols']}
for m in sorted(coins,key=lambda x:(x['exchange'],x['symbol'])):
    src=next(x for x in official['requests'] if ('binance' in x['source'])==(m['exchange']=='binance'))
    snapshot_rows.append({'交易所':'币安' if m['exchange']=='binance' else '欧易','币':m['symbol'],
        '材料文件名':Path(src['saved']).name,'绝对路径':src['saved'],'币记录位置':'symbols列表中的symbol='+m['symbol'] if m['exchange']=='binance' else 'data列表中的instId='+m['symbol'],
        '材料取得时间_比北京慢8小时':src['fetched_utc'],'当前身份在官方快照':m['present_in_official_exchangeInfo'],
        '历史规格证明完整':False,'历史全程身份完整接受':False,'说明':'当前官方快照不等于历史全程证明'})
    if m['exchange']!='binance': continue
    raw=raw_symbols.get(m['symbol']); checks={}
    if raw:
        ff={x['filterType']:x for x in raw.get('filters',[])}
        checks={'价格步进':str(ff.get('PRICE_FILTER',{}).get('tickSize'))==str(m['specs']['tickSize']),
                '数量步进':str(ff.get('LOT_SIZE',{}).get('stepSize'))==str(m['specs']['stepSize']),
                '最小金额':str(ff.get('MIN_NOTIONAL',{}).get('notional'))==str(m['specs']['minNotional']),
                '上市时刻':raw.get('onboardDate')==m['official_listing_ts']}
    gr=generation_rows.get(m['symbol'],{}); flag=flagged.get(m['symbol'],{})
    binance_rows.append({'币':m['symbol'],'原始当前官方文件':str(raw_bn_path),
        '当前身份直接核对通过':bool(raw and all(checks.values())),
        '当前字段核对':json.dumps(checks,ensure_ascii=False),'当前官方上市时刻_毫秒':m['official_listing_ts'],
        '历史身份与规格组合证明通过':False,
        '历史规格缺口':'只有当前规格，没有覆盖研究区间的历史变更时间表',
        '历史身份缺口':'全程合约身份及旧新合约段的证明尚未形成可验收记录',
        '资料历史发布时间缺口':'未证明资料在各历史时点何时可知',
        '价量起点与当前上市口径粗核一致':gr.get('generation_start_aligned','未核'),
        '资金费较价量早超过7天':m['symbol'] in flagged,
        '旧历史价量归档已有可得记录':bool(flag.get('official_earlier_available')),
        '当前材料取得时间':'2026-10-11T12:48:51Z'})
write_csv('06_当前官方材料_549币.csv',snapshot_rows)
write_csv('08_币安526币历史证明逐币判断.csv',binance_rows)

probe=json.loads((W8/'12b_缺失mark官方证据与补缺.json').read_text())
missing=probe['official_mark1h_daily_unavailable_symbols']; evidence=[]
for s in missing:
    for q in probe['probe_evidence']:
        if q['symbol']==s and q['dataset']=='markPriceKlines':
            evidence.append({'币':s,'周期':q['tf'],'档案类型':q['kind'],'请求对应日期':q['period'],
                '已有请求地址':q['url'],'已有响应状态':q['http'],
                '请求对象':'校验文件' if q['url'].endswith('.CHECKSUM') else '档案文件',
                '证据文件':str(W8/'12b_缺失mark官方证据与补缺.json'),
                '可以证明':'上述探测地址当时未取到校验文件',
                '不能证明':'其他官方渠道没有数据，或压缩档案本体不存在',
                '本轮新增下载':0})
write_csv('07_缺标记价格5币_原请求证据.csv',evidence)
v=json.loads(VIEW.read_text())
write_csv('09_费用风险规则_1套.csv',[{'规则文件名':VIEW.name,'绝对路径':str(VIEW),
    '当前实际校验值':sh(VIEW),'文件修改北京时间':datetime.fromtimestamp(VIEW.stat().st_mtime,BJ).isoformat(),
    '配置数':len(v['registered_configs']),'配置内容':json.dumps(v['registered_configs'],ensure_ascii=False),
    '说明':'1套规则中的18配置；不是18份下载数据'}])
stats={'清点开始':start,'清点结束':now(),'数据时间说明':'日期列原值比北京时间慢8小时，另附北京时间；文件末日不等于允许研究末日',
    '来源清单':str(mp),'来源清单实际校验值':sh(mp),'分类库存':summary,'当前官方材料':len(snapshot_rows),
    '币安当前字段直接核对通过':sum(r['当前身份直接核对通过'] for r in binance_rows),
    '币安历史身份规格组合通过':0,'币安历史身份规格组合未通过':len(binance_rows),
    '原因统计_重叠不能相加':{'缺研究区间历史规格表':526,'历史身份和代际证明尚未闭合':526,'历史材料可知时刻未证明':526,
         '价量起点与当前上市口径粗核一致':sum(r['价量起点与当前上市口径粗核一致'] is True for r in binance_rows),
         '资金费起点较价量早超过7天':sum(r['资金费较价量早超过7天'] for r in binance_rows),
         '该组旧历史价量归档有可得记录':sum(r['旧历史价量归档已有可得记录'] for r in binance_rows)},
    '读取时发生变化文件':[p for p,z in stable.items() if not z],'正式全量验收':False,'本轮新增下载':0}
(OUT/'10_独立清点汇总.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
lines=['# 可逐币抽查清单','', '本轮只读清点，不下载任何数据；文件存在不代表正式验收。','',
       '每条价量/资金费/标价时间均实际读取现有文件日期列；成交额本身没有日期列，使用配套价量文件日期并核对长度。', '',
       '| 清单 | 应有记录 | 实际存在 | 缺失 |','|---|---:|---:|---:|']
for s in summary:lines.append(f"| [{s['文件']}]({OUT/s['文件']}) | {s['应有记录']} | {s['实际存在']} | {s['缺失']} |")
lines += ['', '其他证明清单：']
for n in ['06_当前官方材料_549币.csv','07_缺标记价格5币_原请求证据.csv','08_币安526币历史证明逐币判断.csv','09_费用风险规则_1套.csv','10_独立清点汇总.json']:
    lines.append(f'- [{n}]({OUT/n})')
lines += ['', '当前549不是全部历史合约的最终分母；欧易短期原件没有计作完整研究输入。',
          '缺标价的失败记录不能证明官方所有渠道无数据；本轮不联网扩大探测。']
(OUT/'00_清单索引.md').write_text('\n'.join(lines))
end=now();log('只读导出逐币文件名、日期及历史证明清单',start,end,[str(OUT/'00_清单索引.md')])
print(json.dumps(stats,ensure_ascii=False))
