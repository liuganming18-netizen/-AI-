"""仅审阅现有材料、生成统计附件；不联网，不改任何源文件，不运行策略。"""
from pathlib import Path
from datetime import datetime, timezone
import csv, json, hashlib, collections, re
import numpy as np
import pyarrow as pa
import pyarrow.feather as feather

ROOT = Path('/Users/boos/WorkBuddy AI/2026-10-03-16-11-14/outputs/网站逆向研究/最新唐奇安威科夫接通与收尾_2026-10-11')
AUDIT = Path('/Users/boos/.codex/worktrees/c9d7/加密货币趋势判断系统/Work派工_2026-10-11')
OUT = Path(__file__).parent
H = 3600000
CUTOFF = int(datetime(2026, 10, 10, 7, tzinfo=timezone.utc).timestamp()*1000)
START = datetime.now(timezone.utc).isoformat()

def read_csv(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(name, rows):
    with (OUT/name).open('w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def iso(ms):
    return datetime.fromtimestamp(int(ms)/1000, timezone.utc).isoformat().replace('+00:00','Z')

def parse(s):
    return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)

old = read_csv(ROOT/'币安526标价资金费全缺口清点14/05_资金费_结算缺口_逐事件.csv')
old_candidates = set()
for r in old:
    if r['class'].startswith('同周期'):
        for s in re.findall(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', r['expected_settle_utc']):
            old_candidates.add((r['symbol'],parse(s)))

prior = json.loads((AUDIT/'82_资金费526文件日期与原件缓存独立核验.json').read_text())
official_path=ROOT/'正式回测输入补齐与终验8/05_官方证据/binance_fapi_exchangeInfo_2026-10-11T124851Z.json'
official={r['symbol']:r for r in json.loads(official_path.read_text())['symbols']}
summary, periods, gaps, recheck, inputs = [],[],[],[],[]
new_candidates=set(); candidate_period={}; actual_sets={}
for record in prior['records']:
    symbol=record['symbol']; path=Path(record['file']); stat=path.stat()
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha == record['actual_sha256'], '上次清单后文件变化：'+symbol
    arr=feather.read_table(path,columns=['date'],memory_map=True)['date'].cast(pa.int64()).to_numpy()
    arr=arr[arr<CUTOFF]
    assert len(arr)>0 and bool(np.all(np.diff(arr)>0)), symbol
    hrs=np.rint(arr/H).astype(np.int64)
    near=np.abs(arr-hrs*H)<=1000
    # 仅容忍原文件整点后毫秒抖动，保留原时刻；超出1秒不解释为正常周期。
    actual_sets[symbol]=set(int(x)*H for x in hrs[near])
    differences=np.diff(arr)/H
    labels=np.full(len(differences), '其他/不规则',dtype=object)
    for h in (1,4,8):
        good=(np.abs(np.diff(arr)-h*H)<=1000)&near[:-1]&near[1:]
        labels[good]=str(h)+'小时'
    idx=np.r_[0,np.flatnonzero(labels[1:]!=labels[:-1])+1,len(labels)]
    for j in range(len(idx)-1):
        a,b=int(idx[j]),int(idx[j+1])
        periods.append({'币':symbol,'观测间隔':labels[a], '首个左事件时间':iso(arr[a]),
            '最后右事件时间':iso(arr[b]),'相邻间隔数量':b-a,
            '最短间隔小时':float(np.min(differences[a:b])), '最长间隔小时':float(np.max(differences[a:b])),
            '性质':'文件实际观测段；不是官方周期生效证明', '源文件':str(path),'源校验码':sha})
    for j in range(1,len(labels)-1):
        before, after = labels[j-1],labels[j+1]
        if before!=after or before not in ('1小时','4小时','8小时'):
            continue
        ph=int(before[:-2]); dh=int(hrs[j+1]-hrs[j])
        if not (near[j] and near[j+1] and dh>ph and dh%ph==0):
            continue
        for hh in range(int(hrs[j])+ph,int(hrs[j+1]),ph):
            ms=hh*H; key=(symbol,ms); new_candidates.add(key);candidate_period[key]=ph
            gaps.append({'币':symbol,'候选结算时间':iso(ms),'前实际事件':iso(arr[j]),'后实际事件':iso(arr[j+1]),
                '前后邻接观测均为小时数':ph,'状态':'缺时点候选；当时官方有效周期仍未证明',
                '是否在当前快照上市前':ms<int(official[symbol]['onboardDate']),
                '是否超过当前快照交割时点':ms>=int(official[symbol]['deliveryDate']), '源文件':str(path)})
    counts=collections.Counter(labels)
    summary.append({'币':symbol,'实际事件数':len(arr),'首事件时间':iso(arr[0]),'末事件时间':iso(arr[-1]),
        '8小时间隔数':counts['8小时'],'4小时间隔数':counts['4小时'],'1小时间隔数':counts['1小时'],
        '其他间隔数':counts['其他/不规则'],'超出整点1秒事件数':int(np.sum(~near)),
        '存在的周期类型数':sum(counts[str(h)+'小时']>0 for h in (1,4,8)),
        '观测变化段数':len(idx)-1,'缺时点候选数':sum(1 for x in new_candidates if x[0]==symbol),
        '官方有效周期已证明':False,'源文件':str(path),'源校验码':sha})
    assert (path.stat().st_mtime_ns,path.stat().st_size)==(stat.st_mtime_ns,stat.st_size)
    inputs.append({'币':symbol,'文件':str(path),'校验码':sha,'与82一致':True})

for symbol,ms in sorted(old_candidates):
    key=(symbol,ms)
    if ms in actual_sets[symbol]: status='现有文件已有该时点'
    elif key in new_candidates: status='重算仍为前后同间隔候选，非已证实漏结算'
    else: status='重算不满足前后同间隔条件，仍不等于已证明无缺口'
    recheck.append({'币':symbol,'旧候选时点':iso(ms),'本次分类':status,
        '前后同间隔小时数':candidate_period.get(key,''),'当时是否应结算已证明':False,
        '是否在当前快照上市前':ms<int(official[symbol]['onboardDate'])})

write_csv('01_526币资金费观测间隔汇总.csv',summary)
write_csv('02_每币8小时4小时1小时观测起止及其他间隔.csv',periods)
write_csv('03_重新计算缺时点候选逐笔.csv',gaps)
write_csv('04_旧388币1774候选逐笔复核.csv',recheck)

history=read_csv(AUDIT/'逐币抽查清单_2026-10-11/08_币安526币历史证明逐币判断.csv')
generation=json.loads((ROOT/'正式回测输入补齐与终验8/16_合约代际与OHLCV起始口径.json').read_text())
gens={r['symbol']:r for r in generation['rows']}; flagged={r['symbol'] for r in generation['flagged']}
old_archive={r['symbol'] for r in generation['flagged'] if any(x.get('http')==200 for x in r.get('official_earlier_generation_probe',[]))}
identity=[]
for r in history:
    sym=r['币'];g=gens[sym];o=official[sym]
    if sym in old_archive: reason='同一币码有更早归档可得记录；旧新合约/改名/换代关系未证明'
    elif sym in flagged: reason='资金费早于现有价量超过7天；代际/起点冲突未证明'
    elif not g['generation_start_aligned']: reason='价量起点与当前上市口径不一致；历史对应材料缺失'
    else: reason='起点粗核接近；历史身份/当时可得性证明材料缺失'
    beg=max(parse(g['ohlcv_first_tradable']),int(o['onboardDate']),parse('2020-12-07T00:00:00Z'))
    end=min(CUTOFF,int(o['deliveryDate']))
    identity.append({'币':sym,'已证明当前快照时点':'2026-10-11T12:48:51Z',
        '正式已证明历史时段起点':'','正式已证明历史时段终点':'','历史时段证明状态':'现有资料未闭合',
        '候选最新合约研究段起点_不是已证明':iso(beg) if beg<end else '',
        '候选最新合约研究段终点_不是已证明':iso(end) if beg<end else '',
        '当前官方上市时间':iso(o['onboardDate']),'价量首可交易时间':g['ohlcv_first_tradable'],
        '主要未通过原因':reason,'改名已证实':'未知，未有证明材料','换代已证实':'未知，未有证明材料',
        '资金费价量起点冲突':sym in flagged,'更早归档地址已有成功记录':sym in old_archive,
        '官方来源':str(official_path),'历史判断来源':str(AUDIT/'逐币抽查清单_2026-10-11/08_币安526币历史证明逐币判断.csv')})
write_csv('05_526币身份可证明点与未证明时段.csv',identity)

intersection=old_candidates&new_candidates
total={'开始时间':START,'结束时间':datetime.now(timezone.utc).isoformat(),'联网次数':0,
    '读实际资金费文件数':len(summary),'源文件均与82校验一致':True,
    '间隔总数':{k:sum(r[k] for r in summary) for k in ('8小时间隔数','4小时间隔数','1小时间隔数','其他间隔数')},
    '有多种1或4或8小时间隔的币数':sum(r['存在的周期类型数']>1 for r in summary),
    '旧候选币数':len({s for s,t in old_candidates}),'旧候选时点数':len(old_candidates),
    '旧候选重算仍满足前后同间隔币数':len({s for s,t in intersection}),
    '旧候选重算仍满足前后同间隔时点数':len(intersection),
    '旧候选不再满足此条件时点数':len(old_candidates-new_candidates),
    '全部文件本次前后同间隔候选币数':len({s for s,t in new_candidates}),
    '全部文件本次前后同间隔候选时点数':len(new_candidates),
    '新候选旧表外时点数':len(new_candidates-old_candidates),
    '官方历史有效周期证明的确定漏结算数':'未知；当前没有逐时点已接受周期证据，不能把候选称漏结算',
    '候选在当前快照上市前数':sum(t<int(official[s]['onboardDate']) for s,t in new_candidates),
    '身份主要原因计数':dict(collections.Counter(r['主要未通过原因'] for r in identity)),
    '已证明研究期间身份币数':0,'当前快照身份匹配币数':526,
    '严格当前可放行币数':0,'保守模型当前可放行币数':0,
    '最终保守池大小':'不能确定：身份已证明时段及资金费有效周期材料不闭合；不是历史规格必须全部补齐',
    '源输入':inputs}
(OUT/'06_离线统计汇总与来源.json').write_text(json.dumps(total,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in total.items() if k!='源输入'},ensure_ascii=False,indent=2))
