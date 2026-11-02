-- ==============================================================================
-- 资金费周期元数据 · 生产库迁移预备件（DDL）
-- 【隔离合成测试夹具 / SYNTHETIC TEST FIXTURE ONLY】
-- 仅供离线回归测试使用，禁止作为生产数据执行
-- ==============================================================================

CREATE TABLE IF NOT EXISTS source_files (
    source_id INTEGER PRIMARY KEY,
    zip_name TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS funding_interval_meta (
    exchange TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    calc_time_ms INTEGER NOT NULL,
    funding_interval_hours INTEGER NOT NULL,
    last_funding_rate TEXT,
    avail_settlement_ms INTEGER NOT NULL,
    avail_archive_publish_ms INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (exchange, market, symbol, calc_time_ms),
    FOREIGN KEY (source_id) REFERENCES source_files(source_id)
);

CREATE INDEX IF NOT EXISTS idx_fim_sym_calc ON funding_interval_meta (exchange, market, symbol, calc_time_ms);
CREATE INDEX IF NOT EXISTS idx_fim_avail_settle ON funding_interval_meta (exchange, market, symbol, avail_settlement_ms);
CREATE INDEX IF NOT EXISTS idx_fim_avail_archive ON funding_interval_meta (exchange, market, symbol, avail_archive_publish_ms);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
