"""用户本轮授权的现有文件连续性/准入审查；不联网，不修改行情/工程，不跑策略。"""
from pathlib import Path
from datetime import datetime, timezone
import csv,json,collections,hashlib
import numpy as np
import pyarrow as pa
import pyarrow.feather as feather

OUT=Path(__file__).parent
AUDIT=Path('/Users/boos/.codex/worktrees/c9d7/加密货币趋势判断系统/Work派工_2026-10-11')
H=3600000; DAY=24*H
def read(p):
    with p.open(encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def stamp(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)
def iso(ms):return datetime.fromtimestamp(int(ms)/1000,timezone.utc).isoformat().replace('+00:00','Z')
def write(n,rs):
    with (OUT/n).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
ids=read(OUT/'05_526币身份可证明点与未证明时段.csv')
prices={r['币']:r for r in read(AUDIT/'逐币抽查清单_2026-10-11/本轮仅币安/01_1小时价量_526币.csv')}
quotes={r['币']:r for r in read(AUDIT/'逐币抽查清单_2026-10-11/本轮仅币安/03_真实成交额_1052份.csv') if r['周期']=='1h'}
START=stamp('2020-12-07T00:00:00Z');CUT=stamp('2026-10-10T07:00:00Z')
EMBARGO=[(stamp(a),stamp(a)+30*DAY) for a in ('2020-12-07T00:00:00Z','2023-11-08T00:00:00Z','2025-01-08T00:00:00Z')]
begin=datetime.now(timezone.utc).isoformat()
report=[];anomalies=[];daily=[];sources=[]
for ident in ids:
    if not ident['主要未通过原因'].startswith('起点粗核'):continue
    sym=ident['币'];fp=Path(prices[sym]['绝对路径']);fq=Path(quotes[sym]['绝对路径'])
    bs=(fp.stat().st_mtime_ns,fp.stat().st_size,fq.stat().st_mtime_ns,fq.stat().st_size)
    tab=feather.read_table(fp,columns=['date','open','high','low','close','volume'],memory_map=True)
    ts=tab['date'].cast(pa.int64()).to_numpy();o=tab['open'].to_numpy();hi=tab['high'].to_numpy();lo=tab['low'].to_numpy();cl=tab['close'].to_numpy();vol=tab['volume'].to_numpy();q=np.load(fq,mmap_mode='r')
    assert len(ts)==len(q),sym+'量价不对齐'
    listed=stamp(ident['当前官方上市时间']);use=(ts>=max(START,listed))&(ts<CUT)
    full_ok=np.isfinite(o)&np.isfinite(hi)&np.isfinite(lo)&np.isfinite(cl)&np.isfinite(vol)&np.isfinite(q)&(o>0)&(hi>0)&(lo>0)&(cl>0)&(vol>=0)&(q>=0)
    pair=use[:-1]&use[1:]&full_ok[:-1]&full_ok[1:]&(np.diff(ts)==H)
    factor=np.maximum.reduce([cl[1:]/cl[:-1],cl[:-1]/cl[1:],o[1:]/cl[:-1],cl[:-1]/o[1:]])
    badprice=np.flatnonzero(pair&(factor>=2.0))
    goodunit=(vol>0)&(q>0)&full_ok
    # 成交均价应在当根高低价范围内。极宽影线不能被误称成交量单位变化。
    average=np.full(len(ts),np.nan);average[goodunit]=q[goodunit]/vol[goodunit]
    unit=average/cl
    uf=np.maximum(unit[1:]/unit[:-1],unit[:-1]/unit[1:])
    outside=(average>hi*10.0)|(average<lo/10.0)
    badunit=np.flatnonzero(pair&goodunit[:-1]&goodunit[1:]&(uf>=10.0)&(outside[:-1]|outside[1:]))
    invalid=int(np.sum(use&~full_ok));off=int(np.sum(use&(ts%H!=0)));dup=int(np.sum(np.diff(ts[use])<=0))
    for typ,inds,fac in [('相邻价格至少2倍跳变',badprice,factor),('量额价格换算单位至少10倍突变',badunit,uf)]:
        for j in inds:anomalies.append({'币':sym,'异常类型':typ,'前时刻':iso(ts[j]),'后时刻':iso(ts[j+1]),'倍数':float(fac[j]),'前收盘':float(cl[j]),'后开盘':float(o[j+1]),'后收盘':float(cl[j+1]),'前成交数量':float(vol[j]),'后成交数量':float(vol[j+1]),'前成交额':float(q[j]),'后成交额':float(q[j+1]),'性质':'质量筛选异常，不证明改名或换代','价格文件':str(fp),'成交额文件':str(fq)})
    continuity_ok=len(badprice)==0 and len(badunit)==0 and invalid==0 and off==0 and dup==0 and np.sum(use)>0
    # 已有原规则：90日上市龄，前30完整UTC日日成交额中位>=500万，连续240小时预热。
    # 只检查出现过可准入日，不计算信号/交易/收益。
    dmin=int(ts[0]//DAY);dmax=int((CUT-1)//DAY);di=(ts//DAY-dmin).astype(int)
    n=dmax-dmin+1;valid=(ts<CUT)&full_ok&(ts%H==0)
    counts=np.bincount(di[valid],minlength=n)[:n];tot=np.bincount(di[valid],weights=np.asarray(q)[valid],minlength=n)[:n]
    resets=np.zeros(len(ts),dtype=int);resets[0]=1;resets[1:]=(~full_ok[1:])|(~full_ok[:-1])|(np.diff(ts)!=H)
    latest=np.maximum.accumulate(np.where(resets,np.arange(len(ts)),0));runs=np.arange(len(ts))-latest+1
    qualifying=[]
    if continuity_ok:
        for d in range(max(30,int(START//DAY)-dmin),n):
            dayms=(d+dmin)*DAY
            if dayms<listed+90*DAY or dayms>=stamp('2026-10-10T00:00:00Z') or any(a<=dayms<b for a,b in EMBARGO):continue
            if not np.all(counts[d-30:d]==24):continue
            med=float(np.median(tot[d-30:d]))
            if med<5000000:continue
            j=int(np.searchsorted(ts,dayms)-1)
            if j<0 or ts[j]!=dayms-H or runs[j]<240 or not full_ok[j]:continue
            qualifying.append(dayms)
            daily.append({'币':sym,'资格日':iso(dayms),'此前30日成交额中位U':med,'上市日数':(dayms-listed)/DAY,'前240小时连续有效':True,'身份性质':'同一合约研究假设，未当历史官方证明','性质':'出现准入资格，不表示策略已开仓或行情全已验收'})
    status='纳入同一合约假设候选池' if continuity_ok else '异常剔除'
    report.append({'币':sym,'用户身份假设':'研究期间视为同一合约；非已证明历史事实','审查起点':iso(max(START,listed)),'审查截止':iso(CUT),'检查小时数':int(np.sum(use)),'至少2倍价格跳变次数':len(badprice),'至少10倍单位突变次数':len(badunit),'其他无效数据数':invalid,'非整点数':off,'重复倒序数':dup,'连续性结论':status,'有过原规则准入资格':bool(qualifying),'首资格日':iso(qualifying[0]) if qualifying else '', '末资格日':iso(qualifying[-1]) if qualifying else '', '资格日数':len(qualifying),'无资格说明':'' if qualifying else ('连续性异常先剔除' if not continuity_ok else '研究期内未同时满足90日/前30完整日500万/240小时预热/段首禁入'),'价格文件':str(fp),'成交额文件':str(fq)})
    assert bs==(fp.stat().st_mtime_ns,fp.stat().st_size,fq.stat().st_mtime_ns,fq.stat().st_size),sym+'审查中源文件变化'
    sources.append({'币':sym,'价格校验码':hashlib.sha256(fp.read_bytes()).hexdigest(),'成交额校验码':hashlib.sha256(fq.read_bytes()).hexdigest()})

write('10_442币连续性与实际准入汇总.csv',report)
if anomalies:write('11_价格与单位异常逐项.csv',anomalies)
else:(OUT/'11_价格与单位异常逐项.csv').write_text('币,异常类型,前时刻,后时刻,倍数\n',encoding='utf-8-sig')
if daily:write('12_通过连续性币的逐日准入资格.csv',daily)
eligible=[r['币'] for r in report if r['有过原规则准入资格']]
counts={'开始时间':begin,'结束时间':datetime.now(timezone.utc).isoformat(),'用户授权442假设币数':len(report),'连续性通过币数':sum(r['连续性结论'].startswith('纳入') for r in report),'异常剔除币数':sum(r['连续性结论']=='异常剔除' for r in report),'实际研究期有过准入资格币数':len(eligible),'连续性通过但无准入资格币数':sum(r['连续性结论'].startswith('纳入') and not r['有过原规则准入资格'] for r in report),'价格跳变涉及币':[r['币'] for r in report if r['至少2倍价格跳变次数']],'单位突变涉及币':[r['币'] for r in report if r['至少10倍单位突变次数']],'最终假设研究币池':eligible,'仍待解释71币不计入':71,'6加7排除':13,'阈值':'检查前固定：相邻完整小时价格2倍，量额价格换算单位10倍；数据缺口不跨越比较，遇缺仍按新停算规则','真实历史身份证明与正式研究门禁通过':False,'网络请求次数':0,'运行策略次数':0,'源输入':sources}
(OUT/'13_最终假设研究币池与审查证据.json').write_text(json.dumps(counts,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in counts.items() if k not in ('源输入','最终假设研究币池')},ensure_ascii=False,indent=2))
