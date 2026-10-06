import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    cable_spec text,
    ampacity_a double precision,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS cable_spec text;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS ampacity_a double precision;

-- 电缆截面册：编号 + 允许载流（A），事后可改册
CREATE TABLE IF NOT EXISTS cables (
    id serial PRIMARY KEY,
    spec_code text NOT NULL UNIQUE,
    ampacity_a double precision NOT NULL CHECK (ampacity_a > 0),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

-- 绑串：一根电缆同时只绑一个组串，一个组串同时只绑一根电缆。
-- 两个唯一约束由数据库兜底，两人抢同一截面只准一份成功。
CREATE TABLE IF NOT EXISTS cable_bindings (
    id serial PRIMARY KEY,
    cable_id integer NOT NULL UNIQUE REFERENCES cables(id),
    string_code text NOT NULL UNIQUE,
    bound_by text NOT NULL,
    bound_at timestamptz NOT NULL
);

-- 挡回样例：载流不够或没绑截面时，整笔退回到这里，不入队
CREATE TABLE IF NOT EXISTS scan_rejections (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    cable_spec text,
    ampacity_a double precision,
    reason text NOT NULL,
    rejected_by text NOT NULL,
    rejected_at timestamptz NOT NULL
);

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
