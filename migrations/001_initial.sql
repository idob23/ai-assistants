-- 001_initial.sql — начальная схема БД для бота-автоэлектрика

CREATE TABLE IF NOT EXISTS vehicles (
    id              SERIAL PRIMARY KEY,
    vin             VARCHAR(17),
    vin_masked      VARCHAR(20),
    make            VARCHAR(100),
    model           VARCHAR(100),
    year            INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vehicles_vin ON vehicles (vin);
CREATE INDEX IF NOT EXISTS idx_vehicles_vin_masked ON vehicles (vin_masked);

CREATE TABLE IF NOT EXISTS diagnostic_sessions (
    id              SERIAL PRIMARY KEY,
    vehicle_id      INTEGER REFERENCES vehicles(id),
    report_code     VARCHAR(50),
    report_type     VARCHAR(10),
    diag_datetime   VARCHAR(50),
    source_url      TEXT,
    raw_report      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_diagnostic_sessions_vehicle_id ON diagnostic_sessions (vehicle_id);

CREATE TABLE IF NOT EXISTS fault_codes (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES diagnostic_sessions(id),
    vehicle_id      INTEGER REFERENCES vehicles(id),
    code            VARCHAR(20) NOT NULL,
    description     TEXT,
    status          TEXT,
    subsystem_name  VARCHAR(200),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fault_codes_session_id ON fault_codes (session_id);
CREATE INDEX IF NOT EXISTS idx_fault_codes_code ON fault_codes (code);
CREATE INDEX IF NOT EXISTS idx_fault_codes_vehicle_id ON fault_codes (vehicle_id);

CREATE TABLE IF NOT EXISTS diagnosis_cases (
    id              SERIAL PRIMARY KEY,
    vehicle_id      INTEGER REFERENCES vehicles(id),
    session_id      INTEGER REFERENCES diagnostic_sessions(id),
    symptom         TEXT NOT NULL,
    resolution      TEXT,
    confidence      VARCHAR(20),
    hypotheses      JSONB,
    telegram_thread_id VARCHAR(100),
    status          VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_diagnosis_cases_status ON diagnosis_cases (status);
CREATE INDEX IF NOT EXISTS idx_diagnosis_cases_vehicle_id ON diagnosis_cases (vehicle_id);

CREATE TABLE IF NOT EXISTS agent_miscalls (
    id              SERIAL PRIMARY KEY,
    case_id         INTEGER NOT NULL REFERENCES diagnosis_cases(id),
    predicted       TEXT NOT NULL,
    actual          TEXT NOT NULL,
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_miscalls_case_id ON agent_miscalls (case_id);
