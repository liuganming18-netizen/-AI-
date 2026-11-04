import test from 'node:test';
import assert from 'node:assert/strict';
import { validateQuery, transformKlines, handler } from '../server.js';
test('合法代币和周期',()=>assert.deepEqual(validateQuery(new URL('http://localhost/api/klines?symbol=btcusdt&interval=8h')),{symbol:'BTCUSDT',interval:'8h'}));
test('拒绝非法参数',()=>{assert.equal(validateQuery(new URL('http://localhost/api/klines?symbol=BTC%2FUSDT')),null);assert.equal(validateQuery(new URL('http://localhost/api/klines?interval=1m')),null)});
test('K线格式转换',()=>{const r=transformKlines([[1000,'2','3','1','2.5','100']]);assert.deepEqual(r,[{time:1000,open:2,high:3,low:1,close:2.5,volume:100}])});
test('健康接口',async()=>{let code,body;await handler({url:'/api/health'},{writeHead(s){code=s},end(s){body=s}});assert.equal(code,200);assert.equal(JSON.parse(body).status,'ok')});
