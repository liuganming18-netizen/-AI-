const navItems = ['加密货币','数据监控','策略分析','历史回测'];
const periods = ['5m','15m','30m','1h','4h','8h','12h','1d'];
const periodMs = { '5m':300000,'15m':900000,'30m':1800000,'1h':3600000,'4h':14400000,'8h':28800000,'12h':43200000,'1d':86400000 };
const modules = [
  {name:'交易所接入', services:[['Binance主源',['行情订阅','OI读取','资金费率']],['OKX补位',['合约身份检查','补位数据读取']]]},
  {name:'实时采集', services:[['采集进程',['闭合K线','断点恢复']],['任务队列',['限流监控','检查点']]]},
  {name:'统一数据层', services:[['历史查询',['有效版本查询','冷热读取']],['状态记录',['事件ID','消费位点']]]},
  {name:'时间与质量', services:[['缺口检测',['连续性检查','影响范围定位']],['PIT时间校验',['事件时间','数据可得时间']]]},
  {name:'全市场扫描', services:[['量化扫描',['多周期结构','量价与OI']],['观察池',['入池','失效']]]},
  {name:'AI与量化', services:[['主模型',['结构假设','绘图锚点']],['独立复核',['反向证据','量化优先']]]},
  {name:'信号中心', services:[['事件服务',['信号去重','状态迁移']],['通知服务',['优先级检查','Telegram回执']]]},
  {name:'模拟与回放', services:[['只读持仓',['模拟账户接入','风险展示']],['历史回测',['逐根回放','订单核算']]]}
];
const state = {
  page:'加密货币',catalog:[],market:new Map(),localMarket:new Map(),signals:[],
  marketState:'尚未请求',marketError:'',localConnected:false,localHealth:null,
  liveAt:null,selectedId:null,expandedId:null,query:'',source:'all',sort:'index',
  tf:'4h',kOpen:true,oiOpen:true,oiUnit:'usd',chartKey:'',chartLoading:false,
  bars:[],oi:[],barSource:'',oiSource:'',chartError:'',oiError:'',
  signalFilter:'全部',dataTab:'K线',moduleLevel:0,moduleIndex:3,serviceIndex:0,taskIndex:0,
  monitorFault:'none',monitorTick:0,stage:'早期',strategyTab:'规则说明',
  backTab:'逐笔与事件',scope:'样本外',cursor:0,playing:false
};
let loadAbort = null, playTimer = null;
const $ = id => document.getElementById(id);
const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;':'&quot;'}[ch]||ch));
function number(v){ if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null; }
function fmt(v,d=2){return v===null||v===undefined||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});}
function money(v){const n=number(v);if(n===null)return '—';return Math.abs(n)>=1e8?fmt(n/1e8,2)+'亿':Math.abs(n)>=1e4?fmt(n/1e4,2)+'万':fmt(n,2);}
function px(v){const n=number(v);return n===null?'—':fmt(n,n<0.01?7:n<1?5:n<100?4:2);}
function pct(v){const n=number(v);return n===null?'—':(n>0?'+':'')+fmt(n,2)+'%';}
function time(v){const ms=typeof v==='number'?v:Date.parse(v);return Number.isFinite(ms)?new Date(ms).toLocaleString('zh-CN',{hour12:false}):'—';}
function stamp(v){if(typeof v==='number')return v>1e12?v:v*1000;if(typeof v==='string'){const n=Number(v);if(Number.isFinite(n)&&v.trim()!=='')return n>1e12?n:n*1000;return Date.parse(v);}return NaN;}
function tag(v,warn=false){return '<span class="badge '+(warn?'warn':'muted-badge')+'">'+esc(v)+'</span>';}
function selected(){return state.catalog.find(c=>c.identity===state.selectedId) || state.catalog[0] || null;}
function asKey(c){return c.venue+':'+c.symbol;}
function localRow(c){if(!c)return null;return state.localMarket.get(asKey(c))||null;}
function liveRow(c){return c?.venue==='Binance'?state.market.get(c.symbol)||null:null;}
function field(c,name){
  const l=localRow(c),r=liveRow(c);
  if(name==='price')return number(l?.last_price ?? l?.mark_price ?? r?.price);
  if(name==='change')return number(l?.price_change_ratio_24h)==null?number(r?.change24h):number(l.price_change_ratio_24h)*100;
  if(name==='volume')return number(l?.volume_24h_quote ?? r?.quoteVolume24h);
  if(name==='marketCap')return number(l?.market_cap_usd);
  if(name==='funding')return number(l?.funding_rate);
  if(name==='oi')return number(l?.open_interest_usd ?? l?.oi?.oi_usd);
  if(name==='vwap')return number(r?.weightedAvgPrice);
  return null;
}
async function api(path,signal){
  const response=await fetch(path,{cache:'no-store',signal});
  let body;try{body=await response.json();}catch{throw Error('响应不是JSON');}
  if(!response.ok)throw Error(body.error||body.detail||'接口返回 '+response.status);
  return body;
}
function normalizeArray(body){if(Array.isArray(body))return body;if(!body||typeof body!=='object')return [];return [body.items,body.bars,body.points,body.rows,body.data,body.signals].find(Array.isArray)||[];}
function normalizeBars(body){
  return normalizeArray(body).map(row=>{
    if(Array.isArray(row))return {time:stamp(row[0]),open:number(row[1]),high:number(row[2]),low:number(row[3]),close:number(row[4]),volume:number(row[5])};
    return {time:stamp(row.time??row.open_time??row.openTime??row.timestamp),
      open:number(row.open),high:number(row.high),low:number(row.low),close:number(row.close),
      volume:number(row.volume??row.base_volume),closed:row.is_closed};
  }).filter(r=>Number.isFinite(r.time)&&[r.open,r.high,r.low,r.close].every(Number.isFinite))
    .sort((a,b)=>a.time-b.time).filter((row,i,arr)=>i===arr.length-1||row.time!==arr[i+1].time);
}
function normalizeOi(body){
  return normalizeArray(body).map(row=>({
    time:stamp(row.time??row.timestamp??row.ts??row.period_ts),
    amount:number(row.amount??row.oiAmount??row.oi_amount??row.sumOpenInterest??row.open_interest),
    usd:number(row.usd??row.oiUsd??row.oi_usd??row.sumOpenInterestValue)
  })).filter(r=>Number.isFinite(r.time)&& (r.amount!==null||r.usd!==null))
    .sort((a,b)=>a.time-b.time).filter((row,i,arr)=>i===arr.length-1||row.time!==arr[i+1].time);
}
function isClosedBar(bar,tf){
  if(bar.closed===false)return false;
  return bar.time+periodMs[tf]<=Date.now();
}
function structure(){
  $('nav-list').innerHTML=navItems.map(x=>'<button class="nav-btn '+(state.page===x?'active':'')+'" data-nav="'+x+'">'+x+'</button>').join('');
}
function overview(){
 const btc=state.market.get('BTCUSDT')||null,eth=state.market.get('ETHUSDT')||null;
 const metrics=[
 ['BTC / ETH',px(btc?.price)+' / '+px(eth?.price)],
 ['行情状态',state.marketState],
 ['封存标的',state.catalog.length?String(state.catalog.length):'—'],
 ['Binance / OKX','526 / 23'],
 ['更新时点',state.liveAt?time(state.liveAt):'—'],
 ['Telegram通知','未连接']
 ];
 $('overview').innerHTML='<div class="between"><div><h1>'+esc(state.page==='加密货币'?'全市场行情与信号研判':state.page==='数据监控'?'数据覆盖与运行监控':state.page==='策略分析'?'策略分析与证据复核':'历史回测与样本外验证')+'</h1><div class="muted">统一原始数据入口 · 历史数据不重新封存 · 只读研究终端</div></div>'+tag('服务状态与策略验收独立',true)+'</div><div class="top-metrics">'+metrics.map(x=>'<div class="top-metric"><span class="muted">'+esc(x[0])+'</span><b>'+esc(x[1])+'</b></div>').join('')+'</div>';
 $('nav-status').textContent=state.localConnected?'本机数据网关：只读可访问':'本机数据网关：未连接';
}
function side(){
 if(state.page==='加密货币'){
  const filtered=state.signals.filter(s=>state.signalFilter==='全部'||s.status===state.signalFilter);
  return '<div class="between"><h2>信号池</h2>'+tag(state.localConnected?'本机来源':'未连接')+'</div><div class="tabs" style="margin-top:11px">'+['全部','有效','观察','失效'].map(t=>'<button class="tab '+(state.signalFilter===t?'active':'')+'" data-sigfilter="'+t+'">'+t+'</button>').join('')+'</div>'+
    (filtered.length?filtered.slice(0,40).map(s=>'<button class="signal-card '+(s.direction==='多'?'long':s.direction==='空'?'short':'')+'" data-signal="'+esc(s.id)+'"><b>'+esc(s.direction)+'　'+esc(s.symbol)+'</b><small>'+esc(s.exchange||'来源未知')+' · '+esc(s.stage||'阶段未给出')+'</small><small>状态：'+esc(s.status||'未知')+'</small><small>事件：'+esc(s.eventId||'—')+'</small><small>点击定位中间合约</small></button>').join(''):'<div class="signal-empty">没有可展示的真实信号。<br/>'+(state.localConnected?'当前事件接口未提供记录。':'需连接本机只读信号服务；不会生成假信号。')+'</div>')+
    '<div class="side-note">信号只引用真实事件及状态版本。观察、确认与模拟入场必须分开。</div>';
 }
 if(state.page==='数据监控'){
  return '<h2>数据与服务</h2>'+['交易所来源','历史数据覆盖','统一读取入口','数据质量','运行画布','任务与追踪'].map((v,i)=>'<button class="signal-card" data-sidebar-data="'+i+'"><b>'+v+'</b><small>查看数据和节点状态</small></button>').join('')+'<div class="side-note">画布节点只有拿到真实心跳与追踪证据才可显示“运行正常”。</div>';
 }
 if(state.page==='策略分析')return '<h2>行为阶段研究</h2>'+[['早期','BOX / SPRING'],['中期','TEST'],['末期','SOS / LPS']].map(x=>'<button class="signal-card '+(state.stage===x[0]?'long':'')+'" data-stage="'+x[0]+'"><b>'+x[0]+'</b><small>'+x[1]+'</small></button>').join('')+'<div class="side-note">缺数据不是看涨或看跌证据。AI不允许代替独立量化风控。</div>';
 return '<h2>实验与样本隔离</h2>'+['样本内','验证集','样本外'].map(x=>'<button class="signal-card '+(state.scope===x?'long':'')+'" data-scope="'+x+'"><b>'+x+'</b><small>冻结实验版本后验证</small></button>').join('')+'<div class="side-note">没有真实运行记录时，净收益、胜率、回撤等均应显示“未计算”。</div>';
}
function right(){
 return '<div class="between"><h2>模拟交易</h2>'+tag('只读 / 未接入',true)+'</div><div class="wallet"><span class="muted">产品计划初始资金</span><b>2,000.00 USDT</b><span class="tiny">不是实际账户权益或可用余额</span></div><div class="right-stats"><div class="card"><span class="muted">账户权益</span><b>—</b></div><div class="card"><span class="muted">可用保证金</span><b>—</b></div><div class="card"><span class="muted">持仓笔数</span><b>—</b></div><div class="card"><span class="muted">未实现盈亏</span><b>—</b></div></div><h3 style="margin-top:15px">当前持仓与订单</h3><div class="empty">交易所模拟账户只读接口尚未授权与接通，不能虚构持仓。</div><h3 style="margin-top:14px">风控与执行</h3><div class="note" style="margin-top:10px">量化风险管理独立于AI。杠杆产品约束≤5x；账户读取和自动操作是不同权限。本页面没有任何下单、撤单或改仓入口。</div><div class="side-note">新项目只允许研究与模拟环境；真实交易指令未实现。</div>';
}
function statusLabel(c){
 const ticker=liveRow(c);
 return c.ticker===null?'待映射':c.venue==='Binance'?(ticker?'公开行情':'未取得行情'):'只读补位待接';
}
function token(c){
 const symbol=c.ticker||'待映射';
 const base=symbol.endsWith('USDT')?symbol.slice(0,-4):symbol.split('-')[0];
 const glyph={BTC:'₿',ETH:'◆',SOL:'≋',BNB:'◆',DOGE:'Ð'}[base]||base.slice(0,2);
 const style=['BTC','ETH','SOL','BNB'].includes(base)?' '+base.toLowerCase():'';
 return '<span class="token"><span class="token-icon'+style+'" aria-label="代币简化图标">'+esc(glyph||'—')+'</span><span><b>'+esc(symbol)+'</b><div class="tiny">'+esc(c.venue)+' · '+esc(statusLabel(c))+'</div><div class="tiny">流通市值：'+money(field(c,'marketCap'))+'</div></span></span>';
}
function filteredCatalog(){
 let a=state.catalog.filter(c=>{
  if(state.source!=='all'&&c.venue!==state.source)return false;
  return (c.ticker||c.symbol||'').toLowerCase().includes(state.query.toLowerCase());
 });
 if(state.sort==='symbol')a.sort((x,y)=>(x.ticker||x.symbol).localeCompare(y.ticker||y.symbol));
 return a;
}
function detail(c){
 const active=selected()?.identity===c.identity,notLinked=c.ticker===null;
 return '<tr class="expanded"><td colspan="7"><div class="between wrap"><h3>'+esc(c.ticker||'代码待映射')+' · 历史结构与证据</h3><div class="row wrap"><button class="btn '+(state.kOpen?'active':'')+'" data-fold="k">'+(state.kOpen?'▾':'▸')+' K线图</button><button class="btn '+(state.oiOpen?'active':'')+'" data-fold="oi">'+(state.oiOpen?'▾':'▸')+' OI柱形图</button><button class="btn" data-close="1">关闭</button></div></div>'+
 '<div class="filters"><span class="muted">周期</span>'+periods.map(p=>'<button class="btn '+(state.tf===p?'active':'')+'" data-tf="'+p+'">'+p+'</button>').join('')+tag('最新完整历史为准')+'</div>'+
 (state.chartLoading?'<div class="note">正在查询封存本机接口和交易所公开K线/OI；不使用生成数据。</div>':'')+
 (state.kOpen?'<div class="chart-section"><div class="chart-head"><b>K线与成交量</b>'+tag(state.barSource||'未接入')+'</div>'+(state.bars.length?'<canvas class="chart-canvas" id="candles" aria-label="真实行情K线"></canvas>':'<div class="chart-blank">'+esc(state.chartError||'尚无可展示的真实已收盘K线')+'</div>')+'<div class="chart-info">'+(state.bars.length?'已收盘 '+state.bars.length+' 根；首根 '+time(state.bars[0].time)+'，末根 '+time(state.bars.at(-1).time):'真实历史源缺失时不绘图')+'</div></div>':'')+
 (state.oiOpen?'<div class="chart-section"><div class="chart-head"><b>OI总持仓量 · 柱形图</b><div class="row"><button class="btn '+(state.oiUnit==='amount'?'active':'')+'" data-oiunit="amount">数量</button><button class="btn '+(state.oiUnit==='usd'?'active':'')+'" data-oiunit="usd">名义价值</button></div></div>'+(state.oi.length?'<canvas class="chart-canvas oi" id="oi-chart" aria-label="真实OI柱形图"></canvas>':'<div class="chart-blank">'+esc(state.oiError||'尚无同周期OI观测')+'</div>')+'<div class="chart-info">'+esc(state.oiSource||'未接入')+'；OI缺口不插值、不补零，按真实UTC时间落位。</div></div>':'')+
 '<div class="card-grid two" style="margin-top:10px"><div class="card"><b>信号逻辑与数据详情</b><div class="muted">结构、证据、反证及失效条件来自已通过的信号服务；当前不伪造结论。</div></div><div class="card"><b>交易衍生指标</b><div class="muted">24h成交量 '+money(field(c,'volume'))+' USDT · 24h加权均价 '+px(field(c,'vwap'))+' · 聪明钱多空均价未接入。</div></div></div></td></tr>';
}
function marketRow(c){
 const active=c.identity===state.expandedId;
 const funding=field(c,'funding');
 return '<tr class="market-row '+(active?'active':'')+'" data-coin="'+esc(c.identity)+'"><td>'+token(c)+'</td>'+
 '<td><div class="cell-lines"><strong>24h成交额：'+money(field(c,'volume'))+'</strong><span>动态周期量：—</span></div></td>'+
 '<td><div class="cell-lines"><strong>聪明钱多头：—</strong><span>持仓额/占比/均价：—</span></div></td>'+
 '<td><div class="cell-lines"><strong>聪明钱空头：—</strong><span>持仓额/占比/均价：—</span></div></td>'+
 '<td><div class="cell-lines"><strong>'+money(field(c,'oi'))+'</strong><span>数量与名义额分开</span></div></td>'+
 '<td>'+ (funding===null?'—':pct(funding*100))+'</td>'+
 '<td class="tiny">费率序列：—</td></tr>'+(active?detail(c):'');
}
function market(){
 const a=filteredCatalog();
 return '<section class="panel"><div class="between"><div><h2>全市场代币排序</h2><span class="muted">旧草图七组主表字段 · '+a.length+' / '+state.catalog.length+' 标的 · 榜单不固定高度</span></div>'+tag('排行信号源未接入',true)+'</div>'+
 '<div class="filters"><input id="search" class="field" value="'+esc(state.query)+'" placeholder="搜索BTC / 0G / BONK..." style="width:220px" aria-label="搜索合约"/><select class="field" id="source"><option value="all">全部来源</option><option value="Binance" '+(state.source==='Binance'?'selected':'')+'>Binance</option><option value="OKX" '+(state.source==='OKX'?'selected':'')+'>OKX补充</option></select><select class="field" id="sort"><option value="index">封存索引顺序</option><option value="symbol" '+(state.sort==='symbol'?'selected':'')+'>合约名称排序</option></select><button class="btn" data-clear="1">清空筛选</button>'+tag(state.marketState,state.marketState!=='行情已更新')+'</div>'+
 '<table class="data-table"><colgroup><col style="width:17%"/><col style="width:14%"/><col style="width:18%"/><col style="width:18%"/><col style="width:12%"/><col style="width:10%"/><col style="width:11%"/></colgroup><thead><tr><th>代币/市值</th><th>成交量</th><th>聪明钱多头</th><th>聪明钱空头</th><th>总OI持仓</th><th>资金费率</th><th>24h费率变化</th></tr></thead><tbody id="market-rows">'+(a.length?a.map(marketRow).join(''):'<tr><td colspan="7">暂无符合条件的标的</td></tr>')+'</tbody></table></section>';
}
function flow(){
 const l=state.moduleLevel;
 const m=modules[state.moduleIndex];
 const items=l===0?modules.map((x,i)=>({name:x.name,ix:i})):l===1?m.services.map((x,i)=>({name:x[0],ix:i})):m.services[state.serviceIndex][1].map((x,i)=>({name:x,ix:i}));
 const positions=l===0?[[10,14],[242,14],[474,14],[706,14],[706,189],[474,189],[242,189],[10,189]]:items.map((x,i)=>[items.length===2?150+i*410:items.length===3?38+i*310:12+i*232,106]);
 const paths=positions.slice(0,-1).map((p,i)=>{
   const n=positions[i+1],same=p[1]===n[1],toRight=n[0]>p[0];
   const x1=same?(toRight?p[0]+190:p[0]):p[0]+95,x2=same?(toRight?n[0]:n[0]+190):n[0]+95;
   const y1=same?p[1]+54:p[1]+108,y2=same?n[1]+54:n[1];
   return '<path d="M'+x1+' '+y1+' L'+x2+' '+y2+'"/>';
 }).join('');
 const health=state.localConnected?'网关响应可读':'未接入';
 return '<div class="breadcrumbs"><button data-level="0">全链路</button>'+(l>0?'<span>›</span><button data-level="1">'+esc(m.name)+'</button>':'')+(l>1?'<span>›</span>'+esc(m.services[state.serviceIndex][0]):'')+'<span class="muted" style="margin-left:auto">'+['模块','服务','任务'][l]+'层</span></div>'+
 '<div class="flow-canvas"><div class="flow-board"><svg class="flow-links" width="920" height="330">'+paths+'</svg>'+
 items.map((x,i)=>{
  const isGateway=l===0&&x.ix===2;
  const live=isGateway&&state.localConnected;
  const fault=state.monitorFault!=='none'&&(l===0&&[3,4,5,6].includes(x.ix));
  const isActive=i===(l===0?state.moduleIndex:l===1?state.serviceIndex:state.taskIndex);
  return '<button class="flow-node '+(live?'':fault?'warning':'missing')+' '+(isActive?'active':'')+'" style="left:'+positions[i][0]+'px;top:'+positions[i][1]+'px" data-mnode="'+i+'"><small>'+['模块','服务','任务'][l]+' '+(i+1)+' · '+(fault?'模拟故障':live?'网关可读':'未接遥测')+'</small><b>'+esc(x.name)+'</b><small>心跳：'+(live?'网关可访问':'—')+'</small><small>延迟 / 积压：— / —</small></button>';
 }).join('')+'</div></div>';
}
function dataPage(){
 const tabs=['K线','OI','资金费率','聪明钱与市值'];
 return '<section class="panel"><div class="between"><div><h2>数据监控 · 动态运行画布</h2><span class="muted">旧数据中心的数据覆盖 + 模块 → 服务 → 任务三级下钻</span></div>'+tag('真实任务遥测未接入',true)+'</div></section>'+
 '<div class="card-grid three" style="margin-bottom:10px">'+
 [['历史数据','已封存','沿用原件和校验清单'],['实时数据',state.marketState,'Binance公开行情与本机只读来源分开'],['数据质量',state.localConnected?'网关可读':'未核验','不推断缺口数和任务积压']].map(x=>'<div class="card"><div class="muted">'+esc(x[0])+'</div><div class="num">'+esc(x[1])+'</div><div class="hint">'+esc(x[2])+'</div></div>').join('')+'</div>'+
 '<section class="panel"><div class="between"><h3>模块运行拓扑</h3>'+tag(state.monitorFault==='none'?'真实状态未连接':'模拟故障高亮',state.monitorFault!=='none')+'</div><div class="filters"><button class="btn" data-monitorroot="1">全链路总览</button><button class="btn" data-fault="gap">演示数据缺口</button><button class="btn" data-fault="lag">演示采集积压</button><button class="btn" data-fault="ai">演示AI超时</button><button class="btn" data-fault="none">清除演示</button></div>'+flow()+'<div class="muted" style="margin-top:9px">真实心跳、延迟、积压、数据缺口、消费者检查点与trace_id必须由服务端接口提供；本机网关联通不代表所有模块正常。</div></section>'+
 '<div class="card-grid two" style="margin-bottom:10px"><div class="panel"><h3>节点、服务、任务检查</h3><div class="log" style="margin-top:10px">层级：'+['模块','服务','任务'][state.moduleLevel]+'\n网关：'+(state.localConnected?'可读':'未接入')+'\n真实心跳：—\n延迟分布：—\n任务积压：—\n检查点：—\nTrace ID：—</div></div><div class="panel"><h3>故障定位与恢复</h3><div class="log" style="margin-top:10px">当前演示：'+(state.monitorFault==='none'?'无':state.monitorFault) +'\n实际故障日志：未接入\n自动补齐执行：未授权\n恢复动作：只读界面不执行</div><div class="note" style="margin-top:10px">按受影响币种和周期隔离，恢复须验证幂等和时间可得性；不改动已封存数据。</div></div></div>'+
 '<section class="panel"><div class="between"><h3>数据覆盖与来源</h3>'+tag('源内历史未在本页重审')+'</div><div class="tabs" style="margin-top:11px">'+tabs.map(x=>'<button class="tab '+(state.dataTab===x?'active':'')+'" data-datatab="'+x+'">'+x+'</button>').join('')+'</div><div class="scroll-x" style="margin-top:11px"><table class="basic-table"><thead><tr><th>数据/周期</th><th>封存源</th><th>实时状态</th><th>有效历史范围</th><th>缺口</th></tr></thead><tbody>'+(state.dataTab==='K线'?periods:[state.dataTab]).map(x=>'<tr><td>'+esc(x)+'</td><td>原件已封存</td><td>'+esc(state.localConnected?'网关可读':'接口未连')+'</td><td>—</td><td>未在本页检验</td></tr>').join('')+'</tbody></table></div></section>';
}
function strategyPage(){
 const stage=state.stage;
 const descriptions={早期:'BOX / SPRING：区间形成与潜在承接',中期:'TEST：供需测试与结构确认',末期:'SOS / LPS：强势与回踩证据'};
 const c=selected();
 return '<section class="panel"><div class="between"><div><h2>策略分析 · 威科夫 × 唐奇安</h2><span class="muted">不改变已冻结研究路线；先事实计算，再AI假设与独立反证</span></div>'+tag('未产生新策略验证结果',true)+'</div><div class="tabs" style="margin-top:12px">'+['规则说明','数据依赖','版本对比'].map(x=>'<button class="tab '+(state.strategyTab===x?'active':'')+'" data-stratab="'+x+'">'+x+'</button>').join('')+'</div></section>'+
 '<div class="card-grid three" style="margin-bottom:11px">'+Object.entries(descriptions).map(([k,v])=>'<button class="card '+(stage===k?'active':'')+'" data-stage="'+k+'" style="text-align:left;color:inherit;cursor:pointer;'+(stage===k?'border-color:#ae91f1':'')+'"><div class="muted">吸筹 '+k+'</div><div class="num" style="font-size:15px">'+v.split('：')[0]+'</div><div class="hint">'+v.split('：')[1]+'</div></button>').join('')+'</div>'+
 '<section class="panel"><div class="between"><h3>'+stage+'研究 · '+descriptions[stage]+'</h3>'+tag('候选假设，不是已验证信号')+'</div><div class="card-grid three" style="margin-top:11px"><div class="note"><b>量价结构</b><div>区间边界、量能收缩与测试需记录事前锚点和可得时间。</div></div><div class="note"><b>衍生品证据</b><div>OI、资金费率和多空比独立记录，缺失不填0。</div></div><div class="note"><b>独立风控</b><div>量化负责风险和规则，不能因AI解释而放宽。</div></div></div></section>'+
 '<section class="panel"><h3>关联原始数据</h3><div class="muted" style="margin-top:7px">选中：'+esc(c?.ticker||'无可展示标的')+'</div><div class="card-grid four" style="margin-top:10px">'+[['市场价格',px(field(c,'price'))],['24h加权均价',px(field(c,'vwap'))],['OI名义价值',money(field(c,'oi'))],['流通市值',money(field(c,'marketCap'))]].map(x=>'<div class="card"><div class="muted">'+x[0]+'</div><div class="num">'+x[1]+'</div></div>').join('')+'</div><div class="note" style="margin-top:10px">以上仅为市场事实字段，不是“聪明钱均价”，也不能凭OI增加推断庄家吸筹。</div></section>'+
 '<section class="panel"><h3>'+state.strategyTab+'</h3><div class="log" style="margin-top:10px">'+(state.strategyTab==='版本对比'?'冻结规则版本：未在新仓库读通\n待测试候选规则：未冻结\n参数差异：—\n不能声称复现旧规则':state.strategyTab==='数据依赖'?'K线：已封存源，按实际可得时间读\nOI：同交易所同合约\n资金费率：事件时点独立检查\n多空比：不可跨来源拼接':'候选结构 → 量化计算 → AI假设 → 反证复核 → 独立风险校验\n研究结果在严格样本外之前保持未验收')+'</div></section>';
}
function backRange(){const n=state.bars.length;if(!n)return [0,0];if(state.scope==='样本内')return [0,Math.max(0,Math.floor(n*.6)-1)];if(state.scope==='验证集')return [Math.floor(n*.6),Math.max(Math.floor(n*.6),Math.floor(n*.8)-1)];return [Math.floor(n*.8),n-1];}
function backPage(){
 const [lo,hi]=backRange(),metrics=['净收益','最大回撤','Sharpe','Sortino','Calmar','Profit Factor','胜率','盈亏比','完成订单','每笔期望','95%置信区间','样本外超额'];
 return '<section class="panel"><div class="between"><div><h2>历史回测 · 科学验证工作台</h2><span class="muted">历史数据浏览不等于已执行回测；未接回测引擎之前所有绩效保持空态</span></div>'+tag('Run ID：未生成',true)+'</div><div class="tabs" style="margin-top:11px">'+['样本内','验证集','样本外'].map(x=>'<button class="tab '+(state.scope===x?'active':'')+'" data-scope="'+x+'">'+x+'</button>').join('')+'</div></section>'+
 '<div class="card-grid four" style="margin-bottom:11px">'+metrics.map(x=>'<div class="card"><div class="muted">'+x+'</div><div class="num">—</div><div class="hint">真实回测未运行</div></div>').join('')+'</div>'+
 '<section class="panel"><div class="between"><h3>真实K线逐根浏览 · '+esc(selected()?.ticker||'无标的')+'</h3>'+tag(state.scope+' / '+lo+'–'+hi)+'</div><div class="filters"><button class="btn" data-bstep="-1">◀ 上一根</button><button class="btn '+(state.playing?'active':'')+'" data-bplay="1">'+(state.playing?'暂停':'▶ 播放')+'</button><button class="btn" data-bstep="1">下一根 ▶</button><button class="btn" data-breset="1">段首</button><span class="muted">可见K线 '+state.bars.length+' 根 · T+'+state.cursor+'</span></div><input type="range" class="range" id="back-seek" min="'+lo+'" max="'+hi+'" value="'+Math.max(lo,Math.min(hi,state.cursor))+'" '+(!state.bars.length?'disabled':'')+'/>'+
 '<div class="row wrap" style="margin:8px 0"><button class="btn '+(state.kOpen?'active':'')+'" data-fold="k">K线 '+(state.kOpen?'▾':'▸')+'</button><button class="btn '+(state.oiOpen?'active':'')+'" data-fold="oi">OI柱形 '+(state.oiOpen?'▾':'▸')+'</button>'+tag('仅浏览已收盘行情')+'</div>'+
 (state.kOpen?'<div class="chart-section"><div class="chart-head"><b>K线与成交量</b>'+tag(state.barSource||'未接入')+'</div>'+(state.bars.length?'<canvas id="candles" class="chart-canvas"></canvas>':'<div class="chart-blank">'+esc(state.chartError||'无K线')+'</div>')+'</div>':'')+
 (state.oiOpen?'<div class="chart-section"><div class="chart-head"><b>OI柱形</b><div class="row"><button class="btn '+(state.oiUnit==='amount'?'active':'')+'" data-oiunit="amount">数量</button><button class="btn '+(state.oiUnit==='usd'?'active':'')+'" data-oiunit="usd">名义额</button></div></div>'+(state.oi.length?'<canvas id="oi-chart" class="chart-canvas oi"></canvas>':'<div class="chart-blank">'+esc(state.oiError||'OI未接入')+'</div>')+'</div>':'')+'</section>'+
 '<section class="panel"><div class="tabs">'+['逐笔与事件','权益与回撤','科学性门禁'].map(x=>'<button class="tab '+(state.backTab===x?'active':'')+'" data-backtab="'+x+'">'+x+'</button>').join('')+'</div><div style="margin-top:12px">'+(state.backTab==='逐笔与事件'?'<div class="empty">回测订单与信号历史未接入；不能用K线涨跌生成假订单。</div>':state.backTab==='权益与回撤'?'<div class="card-grid two"><div class="empty">权益曲线：暂无真实回测数据</div><div class="empty">回撤曲线：暂无真实回测数据</div></div>':'<div class="card-grid two"><div class="log">未来函数测试：未执行\nPIT可得时间：未验收\nWalk-Forward：未执行\n样本隔离：仅为浏览视窗划分</div><div class="log">手续费：未核算\n滑点：未核算\n资金费：未核算\n统计置信区间：未计算</div></div>')+'</div></section>';
}
function render(reset=false){
 const old=$('main-panel').scrollTop;
 structure();overview();
 $('left-panel').innerHTML=side();
 $('right-panel').innerHTML=right();
 $('main-panel').innerHTML=state.page==='加密货币'?market():state.page==='数据监控'?dataPage():state.page==='策略分析'?strategyPage():backPage();
 $('main-panel').scrollTop=reset?0:old;
 drawCharts();
}
function prepareCanvas(id){
 const canvas=$(id);if(!canvas)return null;
 const width=Math.max(150,canvas.clientWidth),height=Math.max(80,canvas.clientHeight),dpr=Math.min(2,window.devicePixelRatio||1);
 canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
 const g=canvas.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
 return {g,w:width,h:height};
}
function drawK(){
 const c=prepareCanvas('candles');if(!c||!state.bars.length)return;
 const {g,w,h}=c,full=state.bars;
 const subset=state.page==='历史回测'?full.slice(0,Math.min(full.length,state.cursor+1)):full;
 if(!subset.length)return;
 const bars=subset.slice(-560),padTop=19,priceHeight=h*.76,lo=Math.min(...bars.map(x=>x.low)),hi=Math.max(...bars.map(x=>x.high)),range=hi-lo||1,step=(w-65)/bars.length;
 g.fillStyle='#0d182d';g.fillRect(0,0,w,h);
 const y=p=>padTop+(hi-p)/range*(priceHeight-padTop);
 g.strokeStyle='#31425d';g.fillStyle='#96abc8';g.font='11px sans-serif';
 for(let i=0;i<5;i++){const yy=padTop+(priceHeight-padTop)*i/4;g.beginPath();g.moveTo(0,yy);g.lineTo(w-65,yy);g.stroke();g.fillText(px(hi-range*i/4),w-60,yy+4);}
 const maxVol=Math.max(1,...bars.map(x=>x.volume||0));
 bars.forEach((b,i)=>{
   const x=(i+.5)*step,bull=b.close>=b.open,col=bull?'#38d5b1':'#f07a99',cw=Math.max(.6,Math.min(9,step*.64));
   g.strokeStyle=col;g.fillStyle=col;g.beginPath();g.moveTo(x,y(b.high));g.lineTo(x,y(b.low));g.stroke();
   g.fillRect(x-cw/2,Math.min(y(b.open),y(b.close)),cw,Math.max(1,Math.abs(y(b.open)-y(b.close))));
   const vh=(b.volume||0)/maxVol*(h-priceHeight-6);g.globalAlpha=.4;g.fillRect(x-cw/2,h-vh,cw,vh);g.globalAlpha=1;
 });
}
function drawOi(){
 const c=prepareCanvas('oi-chart');if(!c||!state.oi.length)return;
 const {g,w,h}=c,oi=state.oi,barSet=state.bars;
 const historyEnd=state.page==='历史回测'?barSet[Math.min(state.cursor,barSet.length-1)]?.time:barSet.at(-1)?.time;
 const historyStart=barSet[0]?.time;
 const series=oi.filter(x=> (state.oiUnit==='usd'?x.usd:x.amount)!==null && (!historyEnd||x.time<=historyEnd) && (!historyStart||x.time>=historyStart));
 g.fillStyle='#0c172c';g.fillRect(0,0,w,h);
 if(!series.length){g.fillStyle='#9eacc8';g.fillText('当前窗口未找到该单位的真实OI观测',15,25);return;}
 const minTime=historyStart??series[0].time,maxTime=historyEnd??series.at(-1).time;
 const max=Math.max(...series.map(p=>state.oiUnit==='usd'?p.usd:p.amount))||1;
 g.fillStyle='#95a7c8';g.font='11px sans-serif';g.fillText('最大 '+money(max),w-130,14);
 const base=h-12;
 for(let i=0;i<series.length;i++){
   const p=series[i],v=state.oiUnit==='usd'?p.usd:p.amount;
   const x=6+(p.time-minTime)/(maxTime-minTime||1)*(w-88);
   const height=Math.max(1,(v/max)*(h-25));
   const prev=i?state.oiUnit==='usd'?series[i-1].usd:series[i-1].amount:v;
   g.fillStyle=v>=prev?'#4fd4b7':'#e78aa4';
   g.fillRect(x,base-height,Math.max(1,Math.min(7,(w-90)/series.length*.8)),height);
 }
}
function drawCharts(){requestAnimationFrame(()=>{drawK();drawOi();});}
function normalizeSignals(payload){
 const raw=normalizeArray(payload);
 return raw.map(x=>({
   id:(String(x.exchange||'binance').toLowerCase()==='okx'?'OKX:':'Binance:')+String(x.market_id||x.symbol||'').toUpperCase(),
   symbol:String(x.market_id||x.symbol||'—').toUpperCase(),exchange:x.exchange||'—',
   eventId:x.signal_id??x.id??null,direction:/short|sell|空/i.test(x.direction||x.side)?'空':/long|buy|多/i.test(x.direction||x.side)?'多':'未知',
   status:/invalid|失效/i.test(x.status||x.state)?'失效':/active|confirmed|有效/i.test(x.status||x.state)?'有效':'观察',
   stage:x.stage||x.phase||null
 })).filter(x=>x.symbol!=='—');
}
async function loadCatalog(){
 try{
  const r=await api('/api/catalog');
  state.catalog=Array.isArray(r.items)?r.items.map((c,i)=>({...c,index:i})):[];
  const first=state.catalog.find(c=>c.symbol==='BTCUSDT')||state.catalog[0];
  if(first&&!state.selectedId){state.selectedId=first.identity;state.expandedId=first.identity;}
 }catch(e){state.catalog=[];toast('封存标的索引读取失败：'+e.message);}
 render(true);
 if(state.selectedId)await loadDetail();
}
async function loadMarket(){
 try{
  const r=await api('/api/market-data');state.market=new Map((r.items||[]).map(x=>[x.symbol,x]));
  state.marketState='行情已更新';state.marketError='';state.liveAt=r.asOf;
 }catch(e){state.marketState='行情不可用';state.marketError=e.message;}
 render();
}
async function loadLocal(){
 try{
  const r=await api('/api/local/markets');state.localConnected=true;
  const a=normalizeArray(r.payload);state.localMarket=new Map(a.map(x=>[String(x.exchange||'Binance').toLowerCase()==='okx'?'OKX:'+String(x.market_id||x.symbol):'Binance:'+String(x.market_id||x.symbol),x]));
 }catch{state.localConnected=false;state.localMarket=new Map();}
 try{const r=await api('/api/local/signals');state.signals=normalizeSignals(r.payload);}catch{state.signals=[];}
 render();
}
async function loadDetail(){
 const c=selected();if(!c||!c.ticker)return;
 const key=c.identity+'|'+state.tf;
 if(state.chartKey===key&&state.bars.length)return;
 loadAbort?.abort();loadAbort=new AbortController();const signal=loadAbort.signal;
 state.chartKey=key;state.chartLoading=true;state.bars=[];state.oi=[];
 state.chartError='';state.oiError='';state.barSource='';state.oiSource='';
 render();
 const query='exchange='+encodeURIComponent(c.venue.toLowerCase())+'&symbol='+encodeURIComponent(c.symbol)+'&interval='+state.tf;
 const publicQ='symbol='+encodeURIComponent(c.symbol)+'&interval='+state.tf;
 const localCandles=api('/api/local/candles?'+query,signal).then(r=>({data:normalizeBars(r.payload),source:r.source})).catch(e=>({data:[],error:e.message}));
 const liveCandles=c.venue==='Binance'?api('/api/klines?'+publicQ,signal).then(r=>({data:normalizeBars(r),source:r.source})).catch(e=>({data:[],error:e.message})):Promise.resolve({data:[],error:'OKX缺少公开K线适配器'});
 const localOi=api('/api/local/oi?'+query,signal).then(r=>({data:normalizeOi(r.payload),source:r.source})).catch(e=>({data:[],error:e.message}));
 const publicOi=c.venue==='Binance'?api('/api/oi?symbol='+encodeURIComponent(c.symbol)+'&period='+state.tf,signal).then(r=>({data:normalizeOi(r),source:r.source})).catch(e=>({data:[],error:e.message})):Promise.resolve({data:[],error:'OKX需从本机封存OI读取'});
 const r=await Promise.all([localCandles,liveCandles,localOi,publicOi]);
 if(signal.aborted||state.chartKey!==key)return;
 const [lc,pc,lo,po]=r,barChoice=lc.data.length?lc:pc,oiChoice=lo.data.length?lo:po;
 state.bars=barChoice.data.filter(x=>isClosedBar(x,state.tf));
 state.oi=oiChoice.data;state.barSource=barChoice.source||'';state.oiSource=oiChoice.source||'';
 state.chartError=state.bars.length?'':lc.error||pc.error||'没有已收盘行情';
 state.oiError=state.oi.length?'':lo.error||po.error||'没有OI观测';
 state.chartLoading=false;
 state.cursor=state.bars.length?state.bars.length-1:0;
 render();
}
function toast(message){const el=$('toast');el.textContent=message;el.style.display='block';clearTimeout(toast.id);toast.id=setTimeout(()=>el.style.display='none',4000);}
function goCoin(identity){
 const c=state.catalog.find(x=>x.identity===identity);
 if(!c)return toast('未找到对应合约身份');
 state.selectedId=identity;state.expandedId=identity;state.tf='4h';
 state.page='加密货币';state.query='';state.source='all';
 stopPlayback();render(true);void loadDetail();
}
function stopPlayback(){state.playing=false;if(playTimer){clearInterval(playTimer);playTimer=null;}}
function jumpCursor(n){const [a,b]=backRange();state.cursor=Math.max(a,Math.min(b,n));render();}
document.getElementById('app').addEventListener('click',e=>{
 const b=e.target.closest('button,tr[data-coin]');if(!b)return;
 const d=b.dataset;
 if(d.nav){stopPlayback();state.page=d.nav;render(true);if(d.nav==='历史回测'&&!state.bars.length)void loadDetail();return;}
 if(d.signal){goCoin(d.signal);return;}
 if(d.sigfilter){state.signalFilter=d.sigfilter;render();return;}
 if(d.coin){const isSame=state.expandedId===d.coin;state.selectedId=d.coin;state.expandedId=isSame?null:d.coin;state.tf='1d';render();if(!isSame)void loadDetail();return;}
 if(d.clear){state.query='';state.source='all';state.sort='index';render(true);return;}
 if(d.fold){if(d.fold==='k')state.kOpen=!state.kOpen;else state.oiOpen=!state.oiOpen;render();return;}
 if(d.close){state.expandedId=null;render();return;}
 if(d.tf){state.tf=d.tf;state.chartKey='';void loadDetail();return;}
 if(d.oiunit){state.oiUnit=d.oiunit;render();return;}
 if(d.monitorroot){state.moduleLevel=0;render();return;}
 if(d.level!==undefined){state.moduleLevel=Number(d.level);render();return;}
 if(d.mnode!==undefined){
  const i=Number(d.mnode);if(state.moduleLevel===0){state.moduleIndex=i;state.serviceIndex=0;state.moduleLevel=1;}
  else if(state.moduleLevel===1){state.serviceIndex=i;state.taskIndex=0;state.moduleLevel=2;}
  else state.taskIndex=i;render();return;
 }
 if(d.fault){state.monitorFault=d.fault;state.moduleLevel=0;render();return;}
 if(d.datatab){state.dataTab=d.datatab;render();return;}
 if(d.stage){state.stage=d.stage;render();return;}
 if(d.stratab){state.strategyTab=d.stratab;render();return;}
 if(d.scope){stopPlayback();state.scope=d.scope;state.cursor=backRange()[0];render();return;}
 if(d.backtab){state.backTab=d.backtab;render();return;}
 if(d.bstep){stopPlayback();jumpCursor(state.cursor+Number(d.bstep));return;}
 if(d.breset){stopPlayback();jumpCursor(backRange()[0]);return;}
 if(d.bplay){
  if(state.playing){stopPlayback();render();return;}
  if(!state.bars.length)return toast('没有可回放的真实K线');
  state.playing=true;playTimer=setInterval(()=>{const end=backRange()[1];if(state.cursor>=end){stopPlayback();render();return;}state.cursor++;render();},600);render();return;
 }
});
document.getElementById('app').addEventListener('change',e=>{
 const t=e.target;
 if(t.id==='source'){state.source=t.value;render();}
 if(t.id==='sort'){state.sort=t.value;render();}
 if(t.id==='back-seek'){stopPlayback();jumpCursor(Number(t.value));}
});
document.getElementById('app').addEventListener('input',e=>{
 if(e.target.id!=='search')return;
 state.query=e.target.value;
 const rows=$('market-rows');if(rows){const a=filteredCatalog();rows.innerHTML=a.map(marketRow).join('');
  const hint=document.querySelector('#main-panel .panel .between .muted');if(hint)hint.textContent='旧草图七组主表字段 · '+a.length+' / '+state.catalog.length+' 标的 · 榜单不固定高度';
 }
});
window.addEventListener('resize',drawCharts);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlayback();});
render(true);
loadCatalog();
loadMarket();
loadLocal();
setInterval(()=>{if(document.hidden)return;void loadMarket();void loadLocal();},20000);
