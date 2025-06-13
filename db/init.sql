-- Create Extensions
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create Tables
-- Create a table for OHLCV data
CREATE TABLE
    IF NOT EXISTS ohlcv_data (
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(10) NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL,
        open NUMERIC NOT NULL,
        high NUMERIC NOT NULL,
        low NUMERIC NOT NULL,
        close NUMERIC NOT NULL,
        volume NUMERIC NOT NULL,
        PRIMARY KEY (symbol, timestamp, timeframe)
    );

-- Create a hypertable for efficient time-series queries
SELECT
    create_hypertable ('ohlcv_data', 'timestamp', if_not_exists = > TRUE);
