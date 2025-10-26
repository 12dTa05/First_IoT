-- ============================================================================
-- IOT MULTI-TENANT SYSTEM - DATABASE SCHEMA V2
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- USER MANAGEMENT
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    phone TEXT,
    role TEXT DEFAULT 'owner', -- 'owner', 'admin', 'member'
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);

-- ============================================================================
-- GATEWAY TABLE (với user_id)
-- ============================================================================

DROP TABLE IF EXISTS gateways CASCADE;

CREATE TABLE gateways (
    gateway_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name TEXT,
    location TEXT,
    installation_date TIMESTAMPTZ,
    database_version TEXT,
    status TEXT DEFAULT 'offline', -- 'online', 'offline', 'maintenance'
    last_heartbeat TIMESTAMPTZ,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gateways_user ON gateways(user_id);
CREATE INDEX IF NOT EXISTS idx_gateways_status ON gateways(status);

-- ============================================================================
-- DEVICES TABLE (với user_id)
-- ============================================================================

DROP TABLE IF EXISTS devices CASCADE;

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL REFERENCES gateways(gateway_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
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
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devices_gateway ON devices(gateway_id);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

-- ============================================================================
-- PASSWORDS TABLE (với user_id thay vì owner)
-- ============================================================================

DROP TABLE IF EXISTS passwords CASCADE;

CREATE TABLE passwords (
    password_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    hash TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_passwords_user ON passwords(user_id);
CREATE INDEX IF NOT EXISTS idx_passwords_active ON passwords(active);

-- ============================================================================
-- RFID CARDS TABLE (với user_id thay vì owner)
-- ============================================================================

DROP TABLE IF EXISTS rfid_cards CASCADE;

CREATE TABLE rfid_cards (
    uid TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    active BOOLEAN DEFAULT TRUE,
    card_type TEXT,
    description TEXT,
    registered_at TIMESTAMPTZ DEFAULT NOW(),
    last_used TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    deactivated_at TIMESTAMPTZ,
    deactivation_reason TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rfid_user ON rfid_cards(user_id);
CREATE INDEX IF NOT EXISTS idx_rfid_active ON rfid_cards(active);

-- ============================================================================
-- ACCESS RULES TABLE (với user_id)
-- ============================================================================

DROP TABLE IF EXISTS access_rules CASCADE;

CREATE TABLE access_rules (
    rule_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT TRUE,
    start_time TIME,
    end_time TIME,
    days_of_week INTEGER[], -- Array of 0-6 (Sunday-Saturday)
    allowed_methods TEXT[], -- Array of 'rfid', 'passkey', etc.
    restricted_passwords TEXT[], -- Array of password_ids
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_rules_user ON access_rules(user_id);
CREATE INDEX IF NOT EXISTS idx_access_rules_enabled ON access_rules(enabled);

-- ============================================================================
-- HYPERTABLES - TIME SERIES DATA
-- ============================================================================

-- Telemetry Table
DROP TABLE IF EXISTS telemetry CASCADE;

CREATE TABLE telemetry (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT NOT NULL, -- Thêm để query nhanh hơn
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    data JSONB
);

SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_telemetry_user_time ON telemetry(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_gateway_time ON telemetry(gateway_id, time DESC);

-- Access Logs Table
DROP TABLE IF EXISTS access_logs CASCADE;

CREATE TABLE access_logs (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT NOT NULL, -- Thêm để query nhanh hơn
    method TEXT NOT NULL, -- 'passkey', 'rfid'
    result TEXT NOT NULL, -- 'granted', 'denied'
    password_id TEXT,
    rfid_uid TEXT,
    deny_reason TEXT,
    metadata JSONB
);

SELECT create_hypertable('access_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_access_logs_user_time ON access_logs(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_device_time ON access_logs(device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_method ON access_logs(method, time DESC);
CREATE INDEX IF NOT EXISTS idx_access_logs_result ON access_logs(result, time DESC);

-- Device Status Table
DROP TABLE IF EXISTS device_status CASCADE;

CREATE TABLE device_status (
    time TIMESTAMPTZ NOT NULL,
    device_id TEXT NOT NULL,
    gateway_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL, -- 'ONLINE', 'OFFLINE', etc.
    sequence INTEGER,
    metadata JSONB
);

SELECT create_hypertable('device_status', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_device_status_user_time ON device_status(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_device_status_device_time ON device_status(device_id, time DESC);

-- System Logs Table
DROP TABLE IF EXISTS system_logs CASCADE;

CREATE TABLE system_logs (
    time TIMESTAMPTZ NOT NULL,
    gateway_id TEXT,
    user_id TEXT,
    log_type TEXT NOT NULL, -- 'system_event', 'alert', etc.
    event TEXT NOT NULL,
    severity TEXT, -- 'info', 'warning', 'error', 'critical'
    message TEXT,
    metadata JSONB
);

SELECT create_hypertable('system_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_system_logs_user_time ON system_logs(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_gateway_time ON system_logs(gateway_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_type ON system_logs(log_type, time DESC);

-- Alerts Table
DROP TABLE IF EXISTS alerts CASCADE;

CREATE TABLE alerts (
    time TIMESTAMPTZ NOT NULL,
    alert_id TEXT,
    device_id TEXT,
    gateway_id TEXT,
    user_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    value DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    metadata JSONB
);

SELECT create_hypertable('alerts', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_alerts_user_time ON alerts(user_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity, time DESC);

-- ============================================================================
-- SAMPLE DATA - 3 USERS & GATEWAYS
-- ============================================================================

-- Insert 3 users 
INSERT INTO users (user_id, username, email, password_hash, full_name, role) VALUES
('00001', 'Tu', 'atu@gmail.com', '$2b$10$rJ6/ERl/7ZK6jIXtakmdnufrGeIqwDXw3A.YQk4u7dziFMkHbhV.O', 'Thai Thi Minh Tu', 'owner'), --1510
('00002', 'Thao', 'Thao@gmail.com', '$2b$10$tjUzLukyVWZKE0ofwlsDl.bo.uQ/moJuN5eSKdOUIhpt13O/DT4m2', 'Vuong Linh Thao', 'owner'), --2512
('00003', 'Anh', 'anh@gmail.com', '$2b$10$.BercNlt4N4EF9dXFazrPecsN9eTtLs46d.rLiMrVUdifm.1ok/V2', 'Dam Vu Duc Anh', 'owner') --2003
ON CONFLICT (user_id) DO NOTHING;

-- Insert 3 gateways
INSERT INTO gateways (gateway_id, user_id, name, location, status) VALUES
('Gateway_00001', '00001', 'Gateway Tu', 'Ho Chi Minh', 'online'),
('Gateway_00002', '00002', 'Gateway Thao', 'Ha Noi', 'online'),
('Gateway_00003', '00003', 'Gateway Anh', 'Da Nang', 'online')
ON CONFLICT (gateway_id) DO NOTHING;

-- Insert 4 devices
INSERT INTO devices (device_id, gateway_id, user_id, device_type, location, communication, status) VALUES
('rfid_gate_01', 'Gateway_00001', '00001', 'rfid_gate', 'Main Gate', 'LoRa', 'online'),
('passkey_01', 'Gateway_00002', '00002', 'passkey', 'Front Door', 'WiFi', 'online'),
('temp_01', 'Gateway_00003', '00003', 'temp_DH11', 'Living Room', 'WiFi', 'online'),
('fan_01', 'Gateway_00003', '00003', 'relay_fan', 'Bedroom', 'WiFi', 'online')
ON CONFLICT (device_id) DO NOTHING;

-- Insert sample passwords
INSERT INTO passwords (password_id, user_id, hash, description) VALUES
('passwd_00002', '00002', '$2b$10$tjUzLukyVWZKE0ofwlsDl.bo.uQ/moJuN5eSKdOUIhpt13O/DT4m2', 'Vuong Linh Thao') --2512
ON CONFLICT (password_id) DO NOTHING;

-- Insert sample RFID cards
INSERT INTO rfid_cards (uid, user_id, card_type, description) VALUES
('8675f205', '00001', 'MIFARE Classic', 'Thai Thi Minh Tu')
ON CONFLICT (uid) DO NOTHING;

-- ============================================================================
-- VIEWS
-- ============================================================================

CREATE OR REPLACE VIEW user_devices_view AS
SELECT 
    d.device_id,
    d.user_id,
    d.gateway_id,
    d.device_type,
    d.location,
    d.status,
    d.last_seen,
    u.username,
    u.full_name,
    g.name AS gateway_name
FROM devices d
JOIN users u ON d.user_id = u.user_id
JOIN gateways g ON d.gateway_id = g.gateway_id
WHERE u.active = TRUE;

CREATE OR REPLACE VIEW device_health_view AS
SELECT 
    d.device_id,
    d.user_id,
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

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO iot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO iot;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO iot;

-- ============================================================================
-- DONE
-- ============================================================================
SELECT 'Schema V2 migration complete!' AS status;