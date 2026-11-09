import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const root = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 3000);
const periods = new Set(['5m','15m','30m','1h','4h','8h','12h','1d']);
const oiPeriods = new Set(['5m','15m','30m','1h','4h','12h','1d']);
const symbolPattern = /^[A-Z0-9]{2,30}$/;
const staticFiles = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/assets/app.js', ['assets/app.js', 'text/javascript; charset=utf-8']],
  ['/assets/style.css', ['assets/style.css', 'text/css; charset=utf-8']],
]);
const tickerCache = { at: 0, rows: null };

function json(res, code, payload) {
  res.writeHead(code, {'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});
  res.end(JSON.stringify(payload));
}
function errorText(err) { return String(err?.message || err || '接口异常').slice(0, 180); }
function querySymbol(url) {
  const symbol = (url.searchParams.get('symbol') || 'BTCUSDT').toUpperCase();
  return symbolPattern.test(symbol) && symbol.endsWith('USDT') ? symbol : null;
}
export function validateQuery(url) {
  const symbol = querySymbol(url);
  const interval = url.searchParams.get('interval') || '4h';
  if (!symbol || !periods.has(interval)) return null;
  return {symbol, interval};
}
export function transformKlines(rows) {
  if (!Array.isArray(rows)) throw new Error('行情格式异常');
  return rows.map(row => ({
    time: Number(row[0]), open: Number(row[1]), high: Number(row[2]),
    low: Number(row[3]), close: Number(row[4]), volume: Number(row[5])
  })).filter(row => Object.values(row).every(Number.isFinite));
}
export function transformOi(rows) {
  if (!Array.isArray(rows)) throw new Error('OI格式异常');
  return rows.map(row => ({
    time: Number(row.timestamp),
    amount: row.sumOpenInterest == null ? null : Number(row.sumOpenInterest),
    usd: row.sumOpenInterestValue == null ? null : Number(row.sumOpenInterestValue)
  })).filter(row =>
    Number.isFinite(row.time) &&
    (row.amount === null || Number.isFinite(row.amount)) &&
    (row.usd === null || Number.isFinite(row.usd))
  );
}
function binanceUrl(path, values = {}) {
  const url = new URL('https://fapi.binance.com' + path);
  for (const [key, value] of Object.entries(values)) url.searchParams.set(key, String(value));
  return url;
}
async function getJson(url, timeoutMs = 12000) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), headers: {'accept':'application/json'} });
  const body = await response.json();
  if (!response.ok) throw new Error(body?.msg || body?.message || '上游接口响应失败：' + response.status);
  return body;
}
async function liveMarketRows() {
  if (tickerCache.rows && Date.now() - tickerCache.at < 15000) {
    return { rows:tickerCache.rows, cache:true, capturedAt:tickerCache.at };
  }
  const body = await getJson(binanceUrl('/fapi/v1/ticker/24hr'));
  if (!Array.isArray(body)) throw new Error('交易所返回的行情列表格式异常');
  const rows = body.filter(r => typeof r.symbol === 'string' && r.symbol.endsWith('USDT')).map(r => ({
    symbol:r.symbol, source:'Binance USD-M',
    price:Number.isFinite(Number(r.lastPrice)) ? Number(r.lastPrice) : null,
    change24h:Number.isFinite(Number(r.priceChangePercent)) ? Number(r.priceChangePercent) : null,
    weightedAvgPrice:Number.isFinite(Number(r.weightedAvgPrice)) ? Number(r.weightedAvgPrice) : null,
    quoteVolume24h:Number.isFinite(Number(r.quoteVolume)) ? Number(r.quoteVolume) : null,
    closeTime:Number.isFinite(Number(r.closeTime)) ? Number(r.closeTime) : null,
  }));
  tickerCache.rows=rows;
  tickerCache.at=Date.now();
  return { rows, cache:false, capturedAt:tickerCache.at };
}
function localBase() {
  const raw=process.env.GM_DATA_API_URL || 'http://127.0.0.1:8787';
  const parsed=new URL(raw);
  if (parsed.protocol !== 'http:' || !['127.0.0.1','localhost','[::1]'].includes(parsed.hostname)) {
    throw new Error('统一数据网关仅允许本机回环地址');
  }
  return parsed;
}
async function localRead(path) {
  const base=localBase();
  const target=new URL(path,base);
  const body=await getJson(target,5000);
  return body;
}
async function serveCatalog(res) {
  const raw=await readFile(join(root,'data/market-universe.json'),'utf8');
  const list=JSON.parse(raw);
  json(res,200,{
    source:'封存标的身份索引（仅元数据）', snapshot:'2026-11-05',
    dataClasses:'本索引是1h标的清单，不代表其他周期、OI或资金费可得性',
    items:list
  });
}
export async function handler(req,res) {
  const url=new URL(req.url || '/', 'http://localhost');
  if (req.method !== 'GET') { json(res,405,{error:'仅开放只读 GET 请求'}); return; }
  if (url.pathname === '/api/health') {
    json(res,200,{status:'ok', scope:'仅表示本前端服务可访问；不表示数据、AI或模拟交易已连接',
      marketData:'另查 /api/market-data', historicalData:'另查只读数据网关'}); return;
  }
  if (url.pathname === '/api/catalog') {
    try { await serveCatalog(res); } catch(e) {json(res,500,{error:'封存标的索引读取失败',detail:errorText(e)});} return;
  }
  if (url.pathname === '/api/market-data') {
    try {
      const data=await liveMarketRows();
      json(res,200,{source:'Binance 官方公开24h合约行情',asOf:new Date(data.capturedAt).toISOString(),cached:data.cache,items:data.rows});
    } catch(e){json(res,502,{error:'Binance公开实时行情不可用',detail:errorText(e)});}
    return;
  }
  if (url.pathname === '/api/klines') {
    const q=validateQuery(url);
    if (!q){json(res,400,{error:'仅支持Binance USDT永续和5m/15m/30m/1h/4h/8h/12h/1d'});return;}
    try {
      const count=q.interval==='4h'?600:q.interval==='1d'?120:600;
      const body=await getJson(binanceUrl('/fapi/v1/klines',{symbol:q.symbol,interval:q.interval,limit:count}));
      const rows=transformKlines(body);
      json(res,200,{source:'Binance 官方 USD-M K线',symbol:q.symbol,interval:q.interval,
        timeDefinition:'K线开盘UTC毫秒；最后一根可能尚未收盘',items:rows,rows});
    }catch(e){json(res,502,{error:'K线数据不可用',detail:errorText(e)});}
    return;
  }
  if (url.pathname === '/api/oi') {
    const symbol=querySymbol(url),period=url.searchParams.get('period')||'4h';
    if (!symbol || !periods.has(period)) {json(res,400,{error:'OI标的或周期不合法'});return;}
    if (!oiPeriods.has(period)) {
      json(res,422,{error:'Binance公开历史OI接口无原生该周期',period,items:[],
        hint:'需要从已有封存历史数据的只读接口按真实时间查询；不在前端补造OI'});return;
    }
    try {
      const body=await getJson(binanceUrl('/futures/data/openInterestHist',{symbol,period,limit:500}));
      json(res,200,{source:'Binance 官方公开历史OI',symbol,period,
        warning:'公开历史OI仅覆盖交易所实际提供的近期范围；不代表封存全历史。无数值时不补0。',
        items:transformOi(body)});
    }catch(e){json(res,502,{error:'OI数据不可用',detail:errorText(e)});}
    return;
  }
  if (url.pathname === '/api/local/candles' || url.pathname === '/api/local/oi') {
    const exchange=(url.searchParams.get('exchange') || 'binance').toLowerCase();
    const symbol=(url.searchParams.get('symbol') || '').toUpperCase();
    const interval=url.searchParams.get('interval') || '4h';
    if (!['binance','okx'].includes(exchange) || !/^[A-Z0-9-]{2,42}$/.test(symbol) ||
        !symbol.includes('USDT') || !periods.has(interval)) {
      json(res,400,{connected:false,error:'只读历史数据查询参数不合法'});return;
    }
    const kind=url.pathname.endsWith('/oi')?'oi':'candles';
    const endpoint='/api/v1/markets/' + exchange + '/' + encodeURIComponent(symbol) + '/' + kind;
    const query=new URLSearchParams();
    if(kind==='candles')query.set('timeframe',interval);
    else query.set('period',interval);
    query.set('limit',kind==='candles'?'700':'800');
    if(kind==='candles')query.set('history_only','1');
    try {
      const body=await localRead(endpoint+'?'+query.toString());
      json(res,200,{connected:true,source:'本机封存历史数据网关（只读）',
        exchange,symbol,interval,payload:body});
    }catch(e){json(res,503,{connected:false,error:'历史数据网关未连通',detail:errorText(e)});}
    return;
  }
  const localRoutes={
    '/api/local/markets':'/api/v1/markets',
    '/api/local/signals':'/api/v1/signals?scope=current',
    '/api/local/overview':'/api/v1/overview',
    '/api/local/data-health':'/api/v1/health',
  };
  if (Object.hasOwn(localRoutes,url.pathname)) {
    try {
      const body=await localRead(localRoutes[url.pathname]);
      json(res,200,{connected:true,source:'本机旧数据服务（只读）',payload:body});
    } catch(e) {
      json(res,503,{connected:false,error:'本机只读数据网关未连通',detail:errorText(e)});
    }
    return;
  }
  if (staticFiles.has(url.pathname)) {
    const [path,type]=staticFiles.get(url.pathname);
    try {
      const content=await readFile(join(root,path));
      res.writeHead(200,{'content-type':type,'cache-control':'no-store','x-content-type-options':'nosniff'});
      res.end(content);
    }catch(e){json(res,500,{error:'页面资源未就绪',detail:errorText(e)});}
    return;
  }
  json(res,404,{error:'资源不存在'});
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  http.createServer((req,res)=>{handler(req,res).catch(e=>{
    if (!res.headersSent) json(res,500,{error:'服务异常',detail:errorText(e)});
    else res.end();
  })}).listen(port,'127.0.0.1',()=>console.log('只读趋势研究终端已启动：http://127.0.0.1:'+port));
}
