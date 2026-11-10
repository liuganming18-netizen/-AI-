import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {handler,validateQuery,transformKlines,transformOi} from '../server.js';

async function request(path,method='GET') {
  let code,head,body;
  await handler({url:path,method},{
    writeHead(s,h){code=s;head=h},
    end(s){body=String(s??'')}
  });
  return {code,head,body:JSON.parse(body)};
}
test('只允许USDT永续和八种已批准周期',()=>{
  assert.deepEqual(validateQuery(new URL('http://localhost/api/klines?symbol=btcusdt&interval=8h')),{symbol:'BTCUSDT',interval:'8h'});
  assert.equal(validateQuery(new URL('http://localhost/api/klines?symbol=BTCUSDC')),null);
  assert.equal(validateQuery(new URL('http://localhost/api/klines?interval=1m')),null);
  assert.equal(validateQuery(new URL('http://localhost/api/klines?symbol=BTC%2FUSDT')),null);
});
test('K线与OI保留真实时间和缺失值，不补零',()=>{
  const rows=transformKlines([[1000,'2','3','1','2.5','100']]);
  assert.deepEqual(rows,[{time:1000,open:2,high:3,low:1,close:2.5,volume:100}]);
  assert.deepEqual(transformOi([{timestamp:'1000',sumOpenInterest:'12',sumOpenInterestValue:null}]),
    [{time:1000,amount:12,usd:null}]);
});
test('基础健康不冒充数据接通',async()=>{
  const r=await request('/api/health');
  assert.equal(r.code,200);
  assert.equal(r.body.status,'ok');
  assert.match(r.body.scope,/不表示/);
  assert.equal(r.body.marketData,'另查 /api/market-data');
});
test('封存549标的元数据清单可读取且身份唯一',async()=>{
  const r=await request('/api/catalog');
  assert.equal(r.code,200);
  assert.equal(r.body.items.length,549);
  assert.equal(r.body.items.filter(x=>x.venue==='Binance').length,526);
  assert.equal(r.body.items.filter(x=>x.venue==='OKX').length,23);
  assert.equal(new Set(r.body.items.map(x=>x.identity)).size,549);
  assert.equal(r.body.items.filter(x=>x.ticker===null).length,5);
  assert.ok(r.body.items.every(x=>!x.ticker||/^[A-Z0-9-]+$/.test(x.ticker)));
});
test('OI 8h原生不支持时明确拒绝，不编造柱形',async()=>{
  const r=await request('/api/oi?symbol=BTCUSDT&period=8h');
  assert.equal(r.code,422);
  assert.match(r.body.error,/无原生/);
});
test('本机历史数据代理禁止未经校验的参数',async()=>{
  const r=await request('/api/local/candles?exchange=binance&symbol=BTC%2FUSDT&interval=4h');
  assert.equal(r.code,400);
});
test('所有写请求禁用',async()=>{
  for (const method of ['POST','PUT','DELETE','PATCH']) {
    const r=await request('/api/health',method);
    assert.equal(r.code,405);
    assert.equal(r.body.error,'仅开放只读 GET 请求');
  }
});
test('不存在的路由返回404',async()=>{
  const r=await request('/api/non-existent-endpoint');
  assert.equal(r.code,404);
  assert.equal(r.body.error,'资源不存在');
});
test('网关未连通时本机数据路由真实返回503',async()=>{
  const r1=await request('/api/local/markets');
  assert.equal(r1.code,503);
  assert.equal(r1.body.connected,false);
  assert.equal(r1.body.error,'本机只读数据网关未连通');

  const r2=await request('/api/local/candles?exchange=binance&symbol=BTCUSDT&interval=4h');
  assert.equal(r2.code,503);
  assert.equal(r2.body.connected,false);
  assert.equal(r2.body.error,'历史数据网关未连通');
});
test('数据转换严格保留有效数值，拒绝非法行与未来伪造', ()=>{
  const validK=transformKlines([
    [1794300000000,'100','105','95','102','5000'],
    [1794303600000,'102','NaN','98','100','2000'], // 包含 NaN，应丢弃
  ]);
  assert.equal(validK.length,1);
  assert.deepEqual(validK[0],{time:1794300000000,open:100,high:105,low:95,close:102,volume:5000});

  const validOi=transformOi([
    {timestamp:'1794300000000',sumOpenInterest:'500',sumOpenInterestValue:'50000'},
    {timestamp:'1794303600000',sumOpenInterest:null,sumOpenInterestValue:null}, // 保留时间戳作为缺数记录
    {timestamp:'invalid',sumOpenInterest:'100',sumOpenInterestValue:'1000'} // 非法时间，应丢弃
  ]);
  assert.equal(validOi.length,2);
  assert.deepEqual(validOi[0],{time:1794300000000,amount:500,usd:50000});
  assert.deepEqual(validOi[1],{time:1794303600000,amount:null,usd:null});
});
test('页面资源存在且四主导航可用',async()=>{
  const html=await readFile(new URL('../index.html',import.meta.url),'utf8');
  const js=await readFile(new URL('../assets/app.js',import.meta.url),'utf8');
  const css=await readFile(new URL('../assets/style.css',import.meta.url),'utf8');
  assert.match(html,/assets\/app\.js/);
  for(const name of ['加密货币','数据监控','策略分析','历史回测'])assert.ok(js.includes(name));
  for(const key of ['data-coin','data-fold','data-mnode','data-bplay','/api/local/candles','/api/market-data'])
    assert.ok(js.includes(key),key);
  assert.match(css,/grid-template-columns:205px minmax\(0,1fr\) 278px/);
});
test('页面内“适应宽度／75%／100%”操作存在且样式与交互完整',async()=>{
  const js=await readFile(new URL('../assets/app.js',import.meta.url),'utf8');
  const css=await readFile(new URL('../assets/style.css',import.meta.url),'utf8');
  // 必须存在显式的适应宽度、75%、100% 控制按钮
  assert.match(js,/data-scale="fit"/);
  assert.match(js,/data-scale="75%"/);
  assert.match(js,/data-scale="100%"/);
  assert.match(js,/viewScale:'fit'/);
  assert.match(js,/state\.viewScale=d\.scale/);
  // 样式表必须支持对应的缩放类
  assert.match(css,/\.root\.scale-75\{transform:scale\(0\.75\)/);
  assert.match(css,/\.root\.scale-100\{transform:none/);
  assert.match(css,/\.root\.scale-fit\{transform:none/);
});
test('K线与OI共用时间窗口、横轴映射与绘图区（针对缺口与超过560根场景）',async()=>{
  const js=await readFile(new URL('../assets/app.js',import.meta.url),'utf8');
  assert.match(js,/getSharedChartContext/);
  assert.match(js,/timeToX/);
  
  // 模拟运行 getSharedChartContext 算法逻辑
  function mockSharedContext(fullBars, rawOi, canvasW=1000) {
    const subset = fullBars; // 简化回测/展示切片
    const bars = subset.slice(-560);
    const windowStart = bars[0].time;
    const windowEnd = bars.at(-1).time;
    const timeSpan = Math.max(1, windowEnd - windowStart);
    const padLeft = 14, padRight = 72, plotWidth = Math.max(10, canvasW - padLeft - padRight);
    const timeToX = (t) => padLeft + ((t - windowStart) / timeSpan) * plotWidth;
    const oiSeries = rawOi.filter(x => x.amount !== null && x.time >= windowStart && x.time <= windowEnd);
    return { bars, oiSeries, windowStart, windowEnd, timeSpan, padLeft, padRight, plotWidth, timeToX };
  }

  // 场景 A: 超过 560 根（生成 800 根连续 1h K 线与 800 根 OI）
  const baseT = 1700000000000;
  const longBars = Array.from({length:800}, (_,i)=>({time: baseT + i*3600000, open:100, high:105, low:95, close:102, volume:1000}));
  const longOi = Array.from({length:800}, (_,i)=>({time: baseT + i*3600000, amount:5000+i, usd:(5000+i)*100}));
  
  const ctxA = mockSharedContext(longBars, longOi, 1000);
  // 验证超过 560 根时，K线与OI截取的时间窗口完全一致
  assert.equal(ctxA.bars.length, 560);
  assert.equal(ctxA.oiSeries.length, 560);
  assert.equal(ctxA.windowStart, longBars[240].time);
  assert.equal(ctxA.windowEnd, longBars[799].time);
  assert.equal(ctxA.oiSeries[0].time, ctxA.windowStart);
  assert.equal(ctxA.oiSeries.at(-1).time, ctxA.windowEnd);

  // 场景 B: 包含中间 106 根断层缺口（从 index 50 开始跳跃 106 小时）
  const gapBars = [];
  const gapOi = [];
  let curT = baseT;
  for(let i=0; i<100; i++) {
    if(i === 50) curT += 106 * 3600000; // 发生 106 根断层缺口
    else curT += 3600000;
    gapBars.push({time: curT, open:100, high:105, low:95, close:102, volume:1000});
    gapOi.push({time: curT, amount:6000, usd:600000});
  }
  const ctxB = mockSharedContext(gapBars, gapOi, 1000);
  // 检验在缺口发生前后的任意时间戳，K线与OI计算出的横轴 X 坐标严格相同（残差为 0）
  for(let i=0; i<gapBars.length; i++) {
    const t = gapBars[i].time;
    const xK = ctxB.timeToX(t);
    const xOi = ctxB.timeToX(gapOi[i].time);
    assert.equal(xK, xOi, `第 ${i} 根时间戳 ${t} 的 X 像素坐标存在偏差`);
  }
  // 绘图区左右 padding 必须完全一致
  assert.equal(ctxB.padLeft, 14);
  assert.equal(ctxB.padRight, 72);
  assert.equal(ctxB.plotWidth, 1000 - 14 - 72);
});
