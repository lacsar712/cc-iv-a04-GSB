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
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz,
    cable_section_code text,
    ampacity_a double precision
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS cable_section_code text;
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS ampacity_a double precision;

-- 电缆截面台账：一份截面编号只绑一串（bound_string 唯一），一串也只能被一份截面绑
CREATE TABLE IF NOT EXISTS cables (
    section_code text PRIMARY KEY,
    ampacity_a double precision NOT NULL,
    bound_string text UNIQUE,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    bound_at timestamptz
);

-- 挡回样例：载流不够或没绑截面时整笔退回，扫描单不留痕，只在这本账上留样
CREATE TABLE IF NOT EXISTS scan_rejections (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    cable_section_code text,
    cable_ampacity_a double precision,
    voc_v double precision,
    isc_a double precision,
    fill_factor double precision,
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
