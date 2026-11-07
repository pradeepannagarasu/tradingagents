-- 001_init.sql — PostgreSQL / TimescaleDB schema (runs once on first DB start via docker-entrypoint-initdb.d).
-- The application also calls metadata.create_all() (idempotent) and installs append-only triggers.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS price_bars (
  symbol    VARCHAR(64) NOT NULL,
  timeframe VARCHAR(8)  NOT NULL,
  ts        TIMESTAMP   NOT NULL,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  volume DOUBLE PRECISION,
  source VARCHAR(128),
  PRIMARY KEY (symbol, timeframe, ts)
);
SELECT create_hypertable('price_bars', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE TABLE IF NOT EXISTS datasets (
  symbol VARCHAR(64) NOT NULL, timeframe VARCHAR(8) NOT NULL,
  source VARCHAR(128), licence TEXT, url TEXT, retrieved_at TIMESTAMP, last_success TIMESTAMP,
  adjusted BOOLEAN, adjustment_note TEXT, simulated BOOLEAN, first_bar VARCHAR(10), last_bar VARCHAR(10),
  n_bars INTEGER, quality_status VARCHAR(8), quality_json JSON,
  PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS news_items (
  id VARCHAR(64) PRIMARY KEY, source VARCHAR(128), url TEXT, title TEXT, summary TEXT,
  published_at TIMESTAMP, retrieved_at TIMESTAMP, entities JSON, sentiment INTEGER, importance DOUBLE PRECISION,
  verified BOOLEAN, speculative BOOLEAN, duplicate_of VARCHAR(64), trust_tier VARCHAR(16)
);

CREATE TABLE IF NOT EXISTS decision_records (
  id VARCHAR(40) PRIMARY KEY, created_at TIMESTAMP, symbol VARCHAR(64), horizon VARCHAR(16),
  final_decision VARCHAR(16), snapshot_hash VARCHAR(64), simulated BOOLEAN, record_json JSON
);
CREATE INDEX IF NOT EXISTS ix_decision_records_created_at ON decision_records (created_at);
CREATE INDEX IF NOT EXISTS ix_decision_records_symbol ON decision_records (symbol);

CREATE TABLE IF NOT EXISTS signal_outcomes (
  id SERIAL PRIMARY KEY, decision_id VARCHAR(40), evaluated_at TIMESTAMP, outcome_json JSON
);
CREATE INDEX IF NOT EXISTS ix_signal_outcomes_decision_id ON signal_outcomes (decision_id);

CREATE TABLE IF NOT EXISTS order_tickets (
  id VARCHAR(40) PRIMARY KEY, decision_id VARCHAR(40), idempotency_key VARCHAR(64) UNIQUE, status VARCHAR(24),
  mode VARCHAR(8), ticket_json JSON, ticket_hash VARCHAR(64), broker_order_id VARCHAR(64),
  created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_order_tickets_decision_id ON order_tickets (decision_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id SERIAL PRIMARY KEY, ts TIMESTAMP, actor VARCHAR(32), event VARCHAR(64), payload JSON,
  prev_hash VARCHAR(64), hash VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS ix_audit_log_ts ON audit_log (ts);

CREATE TABLE IF NOT EXISTS risk_events (
  id SERIAL PRIMARY KEY, ts TIMESTAMP, decision_id VARCHAR(40), approved BOOLEAN, checks JSON
);

CREATE TABLE IF NOT EXISTS system_state (key VARCHAR(64) PRIMARY KEY, value JSON, updated_at TIMESTAMP);

-- Append-only enforcement for the evidence trail.
CREATE OR REPLACE FUNCTION triagent_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END; $$ LANGUAGE plpgsql;
DO $$ DECLARE t TEXT; BEGIN
  FOREACH t IN ARRAY ARRAY['decision_records','audit_log','risk_events'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I_append_only ON %I', t, t);
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION triagent_append_only()', t, t);
  END LOOP;
END $$;
