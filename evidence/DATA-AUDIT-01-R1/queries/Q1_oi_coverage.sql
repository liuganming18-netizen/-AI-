-- Q1 · 持仓量 5m 覆盖（全量，逐币）
--
-- 结论：池内 526/526 全覆盖；全库 528 币（池外 2 个是加密指数 BTCDOMUSDT/ALLUSDT）
-- 统计时点（本单那次运行）：2026-10-26T03:03:53Z
-- 当时完整结果：池内 526 币 6,188,452 行；全库 528 币 6,197,631 行；
--               跨度 2026-09-12T00:00:00Z → 2026-10-26T02:55:00Z
--
-- 只读：用 sqlite3 -readonly 或 python 的 file:...?mode=ro 打开。
-- 依赖：先执行 queries/_pool_526.sql 建出 pool 视图（同一会话）。
--
-- 注意：oi_history.ts 是 **整数毫秒**，不是 ISO 字符串；用 strftime 前必须 /1000。
--       ts 是 K 线槽位时刻，fetched_at 才是采集时刻。

-- ① 池内逐币明细（526 行）
WITH per AS (
  SELECT symbol,
         COUNT(*)                        AS rows_5m,
         datetime(MIN(ts) / 1000, 'unixepoch') AS min_utc,
         datetime(MAX(ts) / 1000, 'unixepoch') AS max_utc
  FROM oi_history
  WHERE period = '5m'
  GROUP BY symbol
)
SELECT p.symbol,
       per.rows_5m,
       per.min_utc,
       per.max_utc
FROM pool p
LEFT JOIN per ON per.symbol = p.symbol
ORDER BY p.symbol;

-- ② 覆盖计数（应得 covered=526 / missing=0）
WITH per AS (
  SELECT symbol, COUNT(*) AS n FROM oi_history WHERE period='5m' GROUP BY symbol
)
SELECT (SELECT COUNT(*) FROM pool)                                          AS pool_size,
       SUM(CASE WHEN per.n IS NOT NULL THEN 1 ELSE 0 END)                   AS covered,
       SUM(CASE WHEN per.n IS NULL     THEN 1 ELSE 0 END)                   AS missing,
       SUM(per.n)                                                           AS rows_in_pool
FROM pool LEFT JOIN per ON per.symbol = pool.symbol;

-- ③ 全库口径（含池外，应得 528 币 / 6,197,631 行那一档）
SELECT COUNT(DISTINCT symbol)                                     AS symbols_all,
       COUNT(*)                                                   AS rows_all,
       datetime(MIN(ts)/1000,'unixepoch')                         AS min_utc,
       datetime(MAX(ts)/1000,'unixepoch')                         AS max_utc
FROM oi_history WHERE period = '5m';

-- ④ 池外有 5m 的币（应得 BTCDOMUSDT / ALLUSDT 两个指数）
SELECT DISTINCT symbol
FROM oi_history
WHERE period = '5m' AND symbol NOT IN (SELECT symbol FROM pool)
ORDER BY symbol;
