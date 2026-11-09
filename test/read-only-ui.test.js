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
  const r=await request('/api/health','POST');
  assert.equal(r.code,405);
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
