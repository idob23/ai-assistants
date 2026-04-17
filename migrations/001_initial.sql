-- 001_initial.sql — начальная схема БД для бота-автоэлектрика

CREATE TABLE IF NOT EXISTS vehicles (
    id              SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    make            VARCHAR(100),
    model           VARCHAR(100),
    year            INTEGER,
    vin             VARCHAR(17),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_vehicles_telegram_user_id ON vehicles (telegram_user_id);

CREATE TABLE IF NOT EXISTS diagnostic_sessions (
    id              SERIAL PRIMARY KEY,
    vehicle_id      INTEGER REFERENCES vehicles(id),
    telegram_user_id BIGINT NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'open',
    complaint       TEXT,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX idx_diagnostic_sessions_telegram_user_id ON diagnostic_sessions (telegram_user_id);
CREATE INDEX idx_diagnostic_sessions_status ON diagnostic_sessions (status);

CREATE TABLE IF NOT EXISTS fault_codes (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES diagnostic_sessions(id),
    code            VARCHAR(20) NOT NULL,
    description     TEXT,
    severity        VARCHAR(20),
    source          VARCHAR(50) DEFAULT 'x431',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fault_codes_session_id ON fault_codes (session_id);
CREATE INDEX idx_fault_codes_code ON fault_codes (code);

CREATE TABLE IF NOT EXISTS diagnosis_cases (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES diagnostic_sessions(id),
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    tokens_used     INTEGER,
    model           VARCHAR(50),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_diagnosis_cases_session_id ON diagnosis_cases (session_id);

CREATE TABLE IF NOT EXISTS agent_miscalls (
    id              SERIAL PRIMARY KEY,
    case_id         INTEGER NOT NULL REFERENCES diagnosis_cases(id),
    session_id      INTEGER NOT NULL REFERENCES diagnostic_sessions(id),
    user_comment    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_miscalls_session_id ON agent_miscalls (session_id);
CREATE INDEX idx_agent_miscalls_case_id ON agent_miscalls (case_id);
