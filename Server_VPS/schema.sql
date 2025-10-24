-- ============================================================================
-- EXTENSION
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- GATEWAY TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS gateways (
    gateway_id TEXT PRIMARY KEY,
    name TEXT,
    installation_date TIMESTAMPTZ,
    database_version TEXT,
    status TEXT DEFAULT 'online',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- DEVICES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    gateway_id TEXT REFERENCES gateways(gateway_id) ON DELETE CASCADE,
    device_type TEXT NOT NULL,
    location TEXT,
    communication TEXT, -- 'WiFi', 'LoRa', etc.
    ip_address INET,
    mac_address MACADDR,
    firmware_version TEXT,
    status TEXT DEFAULT 'offline',
    registered_at TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    -- Device-specific fields
    health_check_interval INTEGER,
    expected_heartbeat_interval INTEGER,
    telemetry_interval INTEGER,
    lora_channel INTEGER,
    lora_address INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devices_gateway ON devices(gateway_id);
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

-- ============================================================================
-- USERS / PASSWORDS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS passwords (
    password_id TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    owner TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ,
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_passwords_active ON passwords(active);
CREATE INDEX IF NOT EXISTS idx_passwords_owner ON passwords(owner);

-- ============================================================================
-- RFID CARDS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS rfid_cards (
    uid TEXT PRIMARY KEY,
    active BOOLEAN DEFAULT TRUE,
    owner TEXT NOT NULL,
    card_type TEXT,
    description TEXT,
    registered_at TIMESTAMPTZ,
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    deactivated_at TIMESTAMPTZ,
    deactivation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_rfid_active ON rfid_cards(active);
CREATE INDEX IF NOT EXISTS idx_rfid_owner ON rfid_cards(owner);

-- ============================================================================
-- ACCESS RULES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS access_rules (
    rule_id TEXT PRIMARY KEY,
    enabled BOOLEAN DEFAULT TRUE,
    start_time TIME,
    end_time TIME,
    days_of_week INTEGER[], -- Array of 0-6 (Sunday-Saturday)
    allowed_methods TEXT[], -- Array of 'rfid', 'passkey', etc.
    restricted_users TEXT[], -- Array of password_ids
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_rules_enabled ON access_rules(enabled);

-- ============================================================================
-- HYPERTABLES - TIME SERIES DATA
-- ============================================================================

-- Telemetry Table (Temperature, Humidity, etc.)
CREATE TABLE IF NOT EXISTS telemetry (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    data JSONB
);

SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_gateway_time ON telemetry(gateway_id, time DESC);

-- Access Logs Table
CREATE TABLE IF NOT EXISTS access_logs (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    method TEXT NOT NULL, -- 'passkey', 'rfid'
    result TEXT NOT NULL, -- 'granted', 'denied'
    password_id TEXT,
    rfid_uid TEXT,
    deny_reason TEXT,
    metadata JSONB
);

SELECT create_hypertable('access_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_access_logs_device_time ON access_logs(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_method ON access_logs(method, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_result ON access_logs(result, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_password ON access_logs(password_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_rfid ON access_logs(rfid_uid, time DESC);

-- Device Status Table
CREATE TABLE IF NOT EXISTS device_status (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    status TEXT NOT NULL, -- 'ONLINE', 'OFFLINE', etc.
    sequence INTEGER,
    metadata JSONB
);

SELECT create_hypertable('device_status', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_device_status_device_time ON device_status(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_device_status_status ON device_status(status, time DESC);

-- System Logs Table
CREATE TABLE IF NOT EXISTS system_logs (
    time TIMESTAMPTZ NOT NULL,
    gateway_id TEXT,
    log_type TEXT NOT NULL, -- 'system_event', 'alert', etc.
    event TEXT NOT NULL,
    log_level TEXT DEFAULT 'info',
    message TEXT,
    metadata JSONB
);

SELECT create_hypertable('system_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_system_logs_type ON system_logs(log_type, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_event ON system_logs(event, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(log_level, time DESC);

-- Alerts Table (Time-series)
CREATE TABLE IF NOT EXISTS alerts (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    alert_type TEXT NOT NULL, -- 'high_temperature', 'low_battery', etc.
    severity TEXT DEFAULT 'warning',
    value DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    message TEXT,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    metadata JSONB
);

SELECT create_hypertable('alerts', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_alerts_device_time ON alerts(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged, time DESC);

-- ============================================================================
-- SETTINGS / CONFIGURATION TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_settings_updated ON settings(updated_at);

-- ============================================================================
-- CONTINUOUS AGGREGATES
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS access_daily CASCADE;
DROP MATERIALIZED VIEW IF EXISTS alerts_daily CASCADE;

-- Hourly Telemetry Aggregates
CREATE MATERIALIZED VIEW telemetry_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    device_id,
    gateway_id,
    AVG(temperature) AS avg_temperature,
    MIN(temperature) AS min_temperature,
    MAX(temperature) AS max_temperature,
    AVG(humidity) AS avg_humidity,
    COUNT(*) AS sample_count
FROM telemetry
GROUP BY bucket, device_id, gateway_id
WITH NO DATA;

-- Daily Access Aggregates
CREATE MATERIALIZED VIEW access_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    device_id,
    gateway_id,
    method,
    result,
    COUNT(*) AS access_count
FROM access_logs
GROUP BY bucket, device_id, gateway_id, method, result
WITH NO DATA;

-- Daily Alerts Aggregates
CREATE MATERIALIZED VIEW alerts_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    device_id,
    gateway_id,
    alert_type,
    COUNT(*) AS alert_count,
    AVG(value) AS avg_value,
    MAX(value) AS max_value
FROM alerts
GROUP BY bucket, device_id, gateway_id, alert_type
WITH NO DATA;

-- ============================================================================
-- CONTINUOUS AGGREGATE POLICIES
-- ============================================================================

SELECT add_continuous_aggregate_policy('telemetry_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('access_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('alerts_daily',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ============================================================================
-- DATA RETENTION POLICIES
-- ============================================================================

DO $$
BEGIN
    PERFORM remove_retention_policy('telemetry', if_exists => true);
    PERFORM remove_retention_policy('device_status', if_exists => true);
    PERFORM remove_retention_policy('access_logs', if_exists => true);
    PERFORM remove_retention_policy('system_logs', if_exists => true);
    PERFORM remove_retention_policy('alerts', if_exists => true);
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Could not remove existing retention policies: %', SQLERRM;
END $$;

SELECT add_retention_policy('telemetry', INTERVAL '90 days', if_not_exists => TRUE);
SELECT add_retention_policy('device_status', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('access_logs', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('system_logs', INTERVAL '90 days', if_not_exists => TRUE);
SELECT add_retention_policy('alerts', INTERVAL '180 days', if_not_exists => TRUE);

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function to update last_used timestamp for passwords
CREATE OR REPLACE FUNCTION update_password_last_used()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.password_id IS NOT NULL THEN
        UPDATE passwords 
        SET last_used = NEW.time 
        WHERE password_id = NEW.password_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function to update last_used timestamp for RFID cards
CREATE OR REPLACE FUNCTION update_rfid_last_used()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.rfid_uid IS NOT NULL THEN
        UPDATE rfid_cards 
        SET last_used = NEW.time 
        WHERE uid = NEW.rfid_uid;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

DROP TRIGGER IF EXISTS update_gateways_updated_at ON gateways;
CREATE TRIGGER update_gateways_updated_at
    BEFORE UPDATE ON gateways
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_devices_updated_at ON devices;
CREATE TRIGGER update_devices_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_access_rules_updated_at ON access_rules;
CREATE TRIGGER update_access_rules_updated_at
    BEFORE UPDATE ON access_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger to update password last_used
DROP TRIGGER IF EXISTS update_password_last_used_trigger ON access_logs;
CREATE TRIGGER update_password_last_used_trigger
    AFTER INSERT ON access_logs
    FOR EACH ROW
    EXECUTE FUNCTION update_password_last_used();

-- Trigger to update RFID last_used
DROP TRIGGER IF EXISTS update_rfid_last_used_trigger ON access_logs;
CREATE TRIGGER update_rfid_last_used_trigger
    AFTER INSERT ON access_logs
    FOR EACH ROW
    EXECUTE FUNCTION update_rfid_last_used();

-- ============================================================================
-- VIEWS
-- ============================================================================

-- Active Devices View
CREATE OR REPLACE VIEW active_devices AS
SELECT 
    d.device_id,
    d.gateway_id,
    d.device_type,
    d.location,
    d.status,
    d.last_seen,
    d.firmware_version,
    g.name AS gateway_name
FROM devices d
LEFT JOIN gateways g ON d.gateway_id = g.gateway_id
WHERE d.status IN ('online', 'active');

-- Active Access Credentials View
CREATE OR REPLACE VIEW active_credentials AS
SELECT 
    'password' AS credential_type,
    password_id AS credential_id,
    owner,
    active,
    created_at,
    last_used,
    expires_at
FROM passwords
WHERE active = TRUE AND (expires_at IS NULL OR expires_at > NOW())
UNION ALL
SELECT 
    'rfid' AS credential_type,
    uid AS credential_id,
    owner,
    active,
    registered_at AS created_at,
    last_used,
    expires_at
FROM rfid_cards
WHERE active = TRUE AND (expires_at IS NULL OR expires_at > NOW());

-- Recent Access Summary
CREATE OR REPLACE VIEW recent_access_summary AS
SELECT 
    device_id,
    method,
    result,
    COUNT(*) AS count,
    MAX(time) AS last_access
FROM access_logs
WHERE time > NOW() - INTERVAL '24 hours'
GROUP BY device_id, method, result
ORDER BY last_access DESC;

-- Device Health View
CREATE OR REPLACE VIEW device_health AS
SELECT 
    d.device_id,
    d.device_type,
    d.location,
    d.status,
    d.last_seen,
    EXTRACT(EPOCH FROM (NOW() - d.last_seen))/60 AS minutes_since_seen,
    CASE 
        WHEN d.last_seen IS NULL THEN 'unknown'
        WHEN d.last_seen > NOW() - INTERVAL '10 minutes' THEN 'healthy'
        WHEN d.last_seen > NOW() - INTERVAL '1 hour' THEN 'warning'
        ELSE 'critical'
    END AS health_status
FROM devices d;

-- Recent Alerts View
CREATE OR REPLACE VIEW recent_alerts AS
SELECT 
    a.time,
    a.device_id,
    a.alert_type,
    a.severity,
    a.value,
    a.threshold,
    a.acknowledged,
    d.device_type,
    d.location
FROM alerts a
LEFT JOIN devices d ON a.device_id = d.device_id
WHERE a.time > NOW() - INTERVAL '24 hours'
ORDER BY a.time DESC;

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO iot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO iot;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO iot;

-- ============================================================================
-- VERIFICATION
-- ============================================================================

SELECT 
    'Schema creation complete!' AS status,
    COUNT(*) AS varchar_columns_remaining
FROM information_schema.columns
WHERE table_schema = 'public'
    AND data_type = 'character varying';