-- Enable TimescaleDB extension for time-series data
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    phone TEXT,
    role TEXT DEFAULT 'client', -- 'owner', 'admin', 'member'
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);

-- Gateways table
CREATE TABLE IF NOT EXISTS gateways (
    gateway_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT,
    location TEXT,
    status TEXT DEFAULT 'offline', -- 'online', 'offline', 'maintenance'
    last_heartbeat TIMESTAMPTZ,
    database_version TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gateways_user ON gateways(user_id);
CREATE INDEX IF NOT EXISTS idx_gateways_status ON gateways(status);
CREATE INDEX IF NOT EXISTS idx_gateways_heartbeat ON gateways(last_heartbeat);

-- Devices table: ESP8266 devices 
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL REFERENCES gateways(gateway_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    device_type TEXT NOT NULL,
    location TEXT,
    communication TEXT, -- 'WiFi', 'LoRa'
    status TEXT DEFAULT 'offline', -- 'online', 'offline'
    is_online BOOLEAN DEFAULT FALSE, -- Quick check for online status
    last_seen TIMESTAMPTZ, -- Last message received from device
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_gateway ON devices(gateway_id);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(is_online);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen);

-- Passwords table: passwords for keypad door access
CREATE TABLE IF NOT EXISTS passwords (
    password_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    hash TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_passwords_user ON passwords(user_id);
CREATE INDEX IF NOT EXISTS idx_passwords_active ON passwords(active);
CREATE INDEX IF NOT EXISTS idx_passwords_hash ON passwords(hash);

-- RFID cards table: RFID cards for gate access
CREATE TABLE IF NOT EXISTS rfid_cards (
    uid TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    active BOOLEAN DEFAULT TRUE,
    card_type TEXT,
    description TEXT,
    registered_at TIMESTAMPTZ NOT NULL,
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    deactivated_at TIMESTAMPTZ,
    deactivation_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rfid_user ON rfid_cards(user_id);
CREATE INDEX IF NOT EXISTS idx_rfid_active ON rfid_cards(active);

-- Telemetry table: temperature and humidity readings from sensors
CREATE TABLE telemetry (
    time TIMESTAMPTZ NOT NULL, -- Timestamp from gateway, not server
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    metadata JSONB -- Additional sensor data (battery, signal strength, etc.)
);

SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_telemetry_user_time ON telemetry(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_gateway_time ON telemetry(gateway_id, time DESC);

-- Access logs table: RFID and password access attempts
CREATE TABLE access_logs (
    time TIMESTAMPTZ NOT NULL, -- Timestamp from gateway, not server
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    method TEXT NOT NULL, -- 'rfid', 'passkey', 'remote'
    result TEXT NOT NULL, -- 'granted', 'denied'
    password_id TEXT,
    rfid_uid TEXT,
    deny_reason TEXT, -- Reason for denial if result is 'denied'
    metadata JSONB -- Additional context (source, command_id, etc.)
);

SELECT create_hypertable('access_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_access_logs_user_time ON access_logs(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_device_time ON access_logs(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_method ON access_logs(method, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_result ON access_logs(result, time DESC);

-- System logs table: system events, errors, alerts, and device status changes
CREATE TABLE system_logs (
    time TIMESTAMPTZ NOT NULL, -- Timestamp from gateway, not server
    gateway_id TEXT,
    device_id TEXT, -- NULL for gateway-level logs
    user_id TEXT,
    log_type TEXT NOT NULL, -- 'system_event', 'device_event', 'error', 'alert'
    event TEXT NOT NULL, -- Event name: 'device_online', 'device_offline', 'high_temperature', etc.
    severity TEXT NOT NULL, -- 'info', 'warning', 'error', 'critical'
    message TEXT,
    value DOUBLE PRECISION, -- For alerts with threshold values
    threshold DOUBLE PRECISION, -- Threshold that triggered the alert
    metadata JSONB -- Additional event data
);

SELECT create_hypertable('system_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_system_logs_user_time ON system_logs(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_gateway_time ON system_logs(gateway_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_device_time ON system_logs(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_type ON system_logs(log_type, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_severity ON system_logs(severity, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_event ON system_logs(event);

-- Command logs table: track commands sent to devices
CREATE TABLE command_logs (
    time TIMESTAMPTZ NOT NULL, -- Timestamp when command was sent
    command_id TEXT NOT NULL,
    source TEXT NOT NULL, -- 'client', 'gateway_auto', 'api'
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT,
    command_type TEXT NOT NULL, -- 'unlock', 'lock', 'fan_on', 'fan_off', 'set_auto', etc.
    status TEXT NOT NULL, -- 'sent', 'executing', 'completed', 'failed'
    params JSONB, -- Command parameters
    result JSONB, -- Command execution result
    completed_at TIMESTAMPTZ,
    metadata JSONB
);

SELECT create_hypertable('command_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_command_logs_device_time ON command_logs(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_command_logs_status ON command_logs(status);
CREATE INDEX IF NOT EXISTS idx_command_logs_command_id ON command_logs(command_id);

-- ============================================================================
-- RETENTION POLICIES (Auto-cleanup old data)
-- ============================================================================

-- Keep telemetry data for 90 days
SELECT add_retention_policy('telemetry', INTERVAL '90 days', if_not_exists => TRUE);

-- Keep access logs for 180 days (6 months)
SELECT add_retention_policy('access_logs', INTERVAL '180 days', if_not_exists => TRUE);

-- Keep system logs for 90 days
SELECT add_retention_policy('system_logs', INTERVAL '90 days', if_not_exists => TRUE);

-- Keep command logs for 30 days
SELECT add_retention_policy('command_logs', INTERVAL '30 days', if_not_exists => TRUE);

-- ============================================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================================

-- View: devices with owner information
CREATE OR REPLACE VIEW user_devices_view AS
SELECT 
    d.device_id,
    d.user_id,
    d.gateway_id,
    d.device_type,
    d.location,
    d.status,
    d.is_online,
    d.last_seen,
    u.username,
    u.full_name,
    g.name AS gateway_name
FROM devices d
JOIN users u ON d.user_id = u.user_id
JOIN gateways g ON d.gateway_id = g.gateway_id
WHERE u.active = TRUE;

-- View: device health status based on last_seen
CREATE OR REPLACE VIEW device_health_view AS
SELECT 
    d.device_id,
    d.user_id,
    d.device_type,
    d.location,
    d.status,
    d.is_online,
    d.last_seen,
    EXTRACT(EPOCH FROM (NOW() - d.last_seen))/60 AS minutes_since_seen,
    CASE 
        WHEN d.last_seen IS NULL THEN 'unknown'
        WHEN d.last_seen > NOW() - INTERVAL '5 minutes' THEN 'healthy'
        WHEN d.last_seen > NOW() - INTERVAL '15 minutes' THEN 'warning'
        ELSE 'critical'
    END AS health_status
FROM devices d;

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO iot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO iot;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO iot;

-- ============================================================================
-- SCHEMA MIGRATION COMPLETE
-- ============================================================================

SELECT 'Optimized schema created successfully!' AS status;