import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 3000);
const periods = new Set(['5m','15m','30m','1h','4h','8h','12h','1d']);
const symbolPattern = /^[A-Z0-9]{2,30}$/;

export function validateQuery(url) {
  const symbol = (url.searchParams.get('symbol') || 'BTCUSDT').toUpperCase();
  const interval = url.searchParams.get('interval') || '1h';
  if (!symbolPattern.test(symbol) || !periods.has(interval)) return null;
  return {symbol, interval};
}
export function transformKlines(rows) {
  if (!Array.isArray(rows)) throw new Error('行情格式异常');
  return rows.map(row => ({
    time: Number(row[0]), open: Number(row[1]), high: Number(row[2]),
    low: Number(row[3]), close: Number(row[4]), volume: Number(row[5])
  })).filter(r => Object.values(r).every(Number.isFinite));
}
export async function handler(req,res) {
  const url = new URL(req.url || '/', 'http://localhost');
  if (url.pathname === '/api/health') {
    res.writeHead(200, {'content-type':'application/json; charset=utf-8'});
    res.end(JSON.stringify({status:'ok'})); return;
  }
  if (url.pathname === '/api/klines') {
    const query = validateQuery(url);
    if (!query) {
      res.writeHead(400, {'content-type':'application/json; charset=utf-8'});
      res.end(JSON.stringify({error:'代币代码或周期不合法'})); return;
    }
    const upstream = new URL('https://fapi.binance.com/fapi/v1/klines');
    upstream.searchParams.set('symbol',query.symbol);
    upstream.searchParams.set('interval',query.interval);
    upstream.searchParams.set('limit','240');
    try {
      const response = await fetch(upstream, {signal:AbortSignal.timeout(12000)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.msg || '交易所请求失败');
      res.writeHead(200, {'content-type':'application/json; charset=utf-8','cache-control':'no-store'});
      res.end(JSON.stringify({source:'币安合约',symbol:query.symbol,interval:query.interval,rows:transformKlines(data)}));
    } catch(e) {
      res.writeHead(502, {'content-type':'application/json; charset=utf-8'});
      res.end(JSON.stringify({error:'行情暂时不可用',detail:String(e.message).slice(0,160)}));
    }
    return;
  }
  if (url.pathname === '/' || url.pathname === '/index.html') {
    const html=await readFile(join(root,'index.html'));
    res.writeHead(200, {'content-type':'text/html; charset=utf-8','cache-control':'no-store'});
    res.end(html); return;
  }
  res.writeHead(404, {'content-type':'text/plain; charset=utf-8'}); res.end('页面不存在');
}
if (process.argv[1] && fileURLToPath(import.meta.url) === join(process.cwd(),process.argv[1].replace(/^\.\//,''))) {
  http.createServer((req,res)=>{handler(req,res).catch(()=>{res.writeHead(500);res.end('服务异常')})}).listen(port, '127.0.0.1',()=>console.log('行情终端已启动：http://127.0.0.1:'+port));
}
