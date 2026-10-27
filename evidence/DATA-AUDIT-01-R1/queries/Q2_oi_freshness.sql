-- Q2 · 持仓量 5m 新鲜度（全量，逐币）
--
-- 结论：池内 520/526（98.86%）在 1 小时内更新；1d~3d 2 个、>3d 4 个；最久 10.67 天
-- 统计时点（本单那次运行）：2026-10-26T03:03:53Z
-- 当时分位（秒）：min 533.7 / p10 533.7 / p50 833.7 / p90 1133.7 / max 921833.7
--
-- 只读；依赖 queries/_pool_526.sql 的 pool 视图。
-- 注意：ts 是整数毫秒，且 **新鲜度要按 MAX(ts) 算，不是 MAX(fetched_at)**——
--       fetched_at 在池内只有 42.98% 有值（见 Q9），用它算会把「没记录采集时刻」
--       误读成「数据陈旧」。

-- ① 分桶计数（池内 526 币）
WITH last AS (
  SELECT symbol, MAX(ts) AS max_ts
  FROM oi_history WHERE period='5m' GROUP BY symbol
),
age AS (
  SELECT pool.symbol,
         last.max_ts,
         (CAST(strftime('%s','now') AS INTEGER) * 1000 - last.max_ts) / 1000.0 AS age_sec
  FROM pool JOIN last ON last.symbol = pool.symbol
)
SELECT CASE
         WHEN age_sec <=   3600 THEN '<=1h'
         WHEN age_sec <=  86400 THEN '1h~1d'
         WHEN age_sec <= 259200 THEN '1d~3d'
         ELSE '>3d'
       END AS bucket,
       COUNT(*) AS symbols
FROM age
GROUP BY bucket
ORDER BY MIN(age_sec);

-- ② 陈旧币逐个列出（>1 小时的全部，应得 6 个）
WITH last AS (
  SELECT symbol, MAX(ts) AS max_ts
  FROM oi_history WHERE period='5m' GROUP BY symbol
)
SELECT pool.symbol,
       datetime(last.max_ts/1000,'unixepoch') AS last_bar_utc,
       ROUND((CAST(strftime('%s','now') AS INTEGER)*1000 - last.max_ts)/3600000.0, 2) AS age_hours
FROM pool JOIN last ON last.symbol = pool.symbol
WHERE (CAST(strftime('%s','now') AS INTEGER)*1000 - last.max_ts) > 3600000
ORDER BY last.max_ts;

-- ③ 分位（池内）：sqlite 没有 percentile，这里用「排序后按名次取」的做法
WITH last AS (
  SELECT symbol, MAX(ts) AS max_ts
  FROM oi_history WHERE period='5m' GROUP BY symbol
),
age AS (
  SELECT (CAST(strftime('%s','now') AS INTEGER)*1000 - last.max_ts)/1000.0 AS age_sec
  FROM pool JOIN last ON last.symbol = pool.symbol
),
ranked AS (
  SELECT age_sec, ROW_NUMBER() OVER (ORDER BY age_sec) AS rn, COUNT(*) OVER () AS n
  FROM age
)
SELECT MIN(age_sec) AS min_sec,
       MAX(CASE WHEN rn = CAST(n*0.10 AS INTEGER) THEN age_sec END) AS p10_sec,
       MAX(CASE WHEN rn = CAST(n*0.50 AS INTEGER) THEN age_sec END) AS p50_sec,
       MAX(CASE WHEN rn = CAST(n*0.90 AS INTEGER) THEN age_sec END) AS p90_sec,
       MAX(age_sec) AS max_sec
FROM ranked;
