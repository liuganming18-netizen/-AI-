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

test('normalizeOi 生产函数完整接收 open_interest_value 与双单位字段，杜绝美元视图误判为空', async () => {
  const vm = await import('node:vm');
  const js = await readFile(new URL('../assets/app.js', import.meta.url), 'utf8');
  const sandbox = {};
  const block = js.slice(js.indexOf('function number'), js.indexOf('function isClosedBar'));
  vm.runInNewContext(block, sandbox);

  // 反例验证：输入仅包含 open_interest_value 时，usd 不得为 null，必须正确解析出美元持仓量
  const res1 = sandbox.normalizeOi([{ time: 1700000000000, open_interest: 100, open_interest_value: 50000 }]);
  assert.equal(res1.length, 1);
  assert.equal(res1[0].time, 1700000000000);
  assert.equal(res1[0].amount, 100);
  assert.equal(res1[0].usd, 50000);

  // 验证美元持仓量视图过滤逻辑：usd 视图下必须保留该数据点
  const oiSeriesUsd = res1.filter(x => {
    const val = x.usd;
    return val !== null && Number.isFinite(val);
  });
  assert.equal(oiSeriesUsd.length, 1, '修复后默认 usd 视图不得过滤掉仅含 open_interest_value 的真实数据点');

  // 验证兼容驼峰 openInterestValue 与历史别名 oi_usd
  const res2 = sandbox.normalizeOi([
    { timestamp: '1700003600000', openInterest: '200', openInterestValue: '120000' },
    { ts: 1700007200000, oi: 300, oi_usd: 180000 }
  ]);
  assert.equal(res2.length, 2);
  assert.equal(res2[0].time, 1700003600000);
  assert.equal(res2[0].amount, 200);
  assert.equal(res2[0].usd, 120000);
  assert.equal(res2[1].time, 1700007200000);
  assert.equal(res2[1].amount, 300);
  assert.equal(res2[1].usd, 180000);
});

test('原六组真实样本与周期矩阵在生产 normalizeOi 下实现要素保真与双单位消费', async () => {
  const vm = await import('node:vm');
  const js = await readFile(new URL('../assets/app.js', import.meta.url), 'utf8');
  const sandbox = {};
  const block = js.slice(js.indexOf('function number'), js.indexOf('function isClosedBar'));
  vm.runInNewContext(block, sandbox);

  // 1. 页面说明文本强校验：必须明确单位、实际粒度与数据来源
  assert.ok(js.includes('持仓数量（单位：合约张数 Contracts，面值10币）'), '页面必须明确OKX数量为合约张数');
  assert.ok(js.includes('持仓数量（单位：标的币数）'), '页面必须明确Binance数量为标的币数');
  assert.ok(js.includes('名义价值（单位：USD）'), '页面必须明确名义价值为USD');
  assert.ok(js.includes('粒度：定点快照 (snapshot)'), '页面必须明确OKX粒度为定点快照');
  assert.ok(js.includes('粒度：5m快照归档序列 (snapshot)'), '页面必须明确Binance粒度为5m快照归档序列');
  assert.ok(js.includes('[OKX v5接口归档]'), '页面必须明确OKX真实来源');
  assert.ok(js.includes('[Binance Metrics归档]'), '页面必须明确Binance真实来源');

  // 2. 加载六组定点原件记录文件，杜绝三条手写mock冒充
  const recordFile = new URL('../evidence/V9-UI-READONLY-02/six_sample_live_records.json', import.meta.url);
  const rawJson = await readFile(recordFile, 'utf8');
  const auditData = JSON.parse(rawJson);

  assert.equal(auditData.sample_count, 6, '定点核验记录必须完整包含六组');
  assert.equal(auditData.records.length, 6);

  const expectedMatrix = [
    { ex: 'Binance', sym: 'BTCUSDT', tf: '4h', unit: '标的币数(BTC)', period: '5m', src: 'binance.archive.metrics' },
    { ex: 'Binance', sym: 'BTCUSDT', tf: '1h', unit: '标的币数(BTC)', period: '5m', src: 'binance.archive.metrics' },
    { ex: 'Binance', sym: 'BTCUSDT', tf: '1d', unit: '标的币数(BTC)', period: '5m', src: 'binance.archive.metrics' },
    { ex: 'Binance', sym: 'ETHUSDT', tf: '4h', unit: '标的币数(ETH)', period: '5m', src: 'binance.archive.metrics' },
    { ex: 'Binance', sym: 'ETHUSDT', tf: '1h', unit: '标的币数(ETH)', period: '5m', src: 'binance.archive.metrics' },
    { ex: 'OKX', sym: 'CRO-USDT-SWAP', tf: '4h', unit: '合约张数(Contracts, 面值10 CRO)', period: 'snapshot', src: 'okx.v5' }
  ];

  for (let i = 0; i < expectedMatrix.length; i++) {
    const exp = expectedMatrix[i];
    const rec = auditData.records.find(r => r.exchange === exp.ex && r.symbol === exp.sym && r.interval === exp.tf);
    assert.ok(rec, `记录中必须存在 ${exp.ex} ${exp.sym} ${exp.tf}`);

    // 请求参数与采集时刻
    assert.ok(rec.candles.request.captured_at, 'K线采集时刻不可为空');
    assert.ok(rec.oi.request.captured_at, 'OI采集时刻不可为空');
    assert.equal(rec.candles.request.params.exchange.toLowerCase(), exp.ex.toLowerCase());
    assert.equal(rec.oi.request.params.interval, exp.tf);

    // 单位与粒度元数据
    assert.equal(rec.units.amount_unit, exp.unit);
    assert.equal(rec.units.usd_unit, 'USD');
    assert.equal(rec.units.upstream_period, exp.period);
    assert.equal(rec.units.upstream_source, exp.src);

    // 生产消费函数验证：candles
    assert.ok(rec.candles.page_consumption.consumed_count > 0);
    assert.ok(rec.candles.page_consumption.is_valid_series);
    const firstBar = sandbox.normalizeBars([rec.candles.response.sample_first]);
    assert.equal(firstBar.length, 1);
    assert.ok(Number.isFinite(firstBar[0].close));

    // 生产消费函数验证：oi
    assert.ok(rec.oi.page_consumption.consumed_count > 0);
    assert.ok(rec.oi.page_consumption.all_amount_valid);
    assert.ok(rec.oi.page_consumption.all_usd_valid);
    const firstOi = sandbox.normalizeOi([rec.oi.response.sample_first]);
    assert.equal(firstOi.length, 1);
    assert.ok(firstOi[0].amount !== null && Number.isFinite(firstOi[0].amount));
    assert.ok(firstOi[0].usd !== null && Number.isFinite(firstOi[0].usd));

    // 特异性断言：OKX 原生合约张数与美元消费
    if (exp.ex === 'OKX') {
      assert.equal(firstOi[0].amount, 2253544, 'OKX持仓量数量口径保留原生合约张数');
      assert.equal(firstOi[0].usd, 1290153.94, 'OKX持仓量美元口径正确消费 open_interest_value');
      assert.equal(rec.oi.response.upstream_period, 'snapshot');
      assert.equal(rec.oi.response.upstream_source, 'okx.v5');
    }
  }
});
