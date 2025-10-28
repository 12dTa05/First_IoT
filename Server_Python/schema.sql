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
('Gateway1', '00001', 'Gateway Tu', 'Ho Chi Minh', 'online'),
('Gateway2', '00002', 'Gateway Thao', 'Ha Noi', 'online'),
('Gateway3', '00003', 'Gateway Anh', 'Da Nang', 'online')
ON CONFLICT (gateway_id) DO NOTHING;

-- Insert 4 devices
INSERT INTO devices (device_id, gateway_id, user_id, device_type, location, communication, status) VALUES
('rfid_gate_01', 'Gateway1', '00001', 'rfid_gate', 'Main Gate', 'LoRa', 'online'),
('passkey_01', 'Gateway2', '00002', 'passkey', 'Front Door', 'WiFi', 'online'),
('temp_01', 'Gateway3', '00003', 'temp_DH11', 'Living Room', 'WiFi', 'online'),
('fan_01', 'Gateway3', '00003', 'relay_fan', 'Bedroom', 'WiFi', 'online')
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

INSERT INTO telemetry (time, device_id, gateway_id, user_id, temperature, humidity, data) VALUES
('2025-09-27 08:15:00+07', 'temp_01', 'Gateway3', '00003', 24.5, 65.2, '{"battery": 98, "signal": -45}'),
('2025-09-27 08:30:00+07', 'temp_01', 'Gateway3', '00003', 24.8, 64.8, '{"battery": 98, "signal": -44}'),
('2025-09-27 08:45:00+07', 'temp_01', 'Gateway3', '00003', 25.2, 64.5, '{"battery": 98, "signal": -46}'),
('2025-09-27 09:00:00+07', 'temp_01', 'Gateway3', '00003', 25.6, 64.0, '{"battery": 98, "signal": -45}'),
('2025-09-27 12:00:00+07', 'temp_01', 'Gateway3', '00003', 27.3, 62.5, '{"battery": 97, "signal": -43}'),
('2025-09-27 15:00:00+07', 'temp_01', 'Gateway3', '00003', 28.5, 60.8, '{"battery": 97, "signal": -44}'),
('2025-09-27 18:00:00+07', 'temp_01', 'Gateway3', '00003', 26.8, 63.2, '{"battery": 97, "signal": -45}'),
('2025-09-27 21:00:00+07', 'temp_01', 'Gateway3', '00003', 25.4, 65.8, '{"battery": 97, "signal": -46}'),
('2025-09-28 08:20:00+07', 'temp_01', 'Gateway3', '00003', 24.2, 66.1, '{"battery": 97, "signal": -45}'),
('2025-09-28 12:15:00+07', 'temp_01', 'Gateway3', '00003', 26.9, 63.4, '{"battery": 96, "signal": -44}'),
('2025-09-28 15:30:00+07', 'temp_01', 'Gateway3', '00003', 28.1, 61.2, '{"battery": 96, "signal": -43}'),
('2025-09-28 18:45:00+07', 'temp_01', 'Gateway3', '00003', 26.5, 64.0, '{"battery": 96, "signal": -45}'),
('2025-09-28 22:00:00+07', 'temp_01', 'Gateway3', '00003', 25.0, 66.5, '{"battery": 96, "signal": -46}'),
('2025-09-29 07:30:00+07', 'temp_01', 'Gateway3', '00003', 23.8, 67.2, '{"battery": 96, "signal": -44}'),
('2025-09-29 11:00:00+07', 'temp_01', 'Gateway3', '00003', 26.4, 64.5, '{"battery": 95, "signal": -45}'),
('2025-09-29 14:20:00+07', 'temp_01', 'Gateway3', '00003', 27.8, 62.1, '{"battery": 95, "signal": -43}'),
('2025-09-29 17:45:00+07', 'temp_01', 'Gateway3', '00003', 26.2, 64.8, '{"battery": 95, "signal": -44}'),
('2025-09-29 20:30:00+07', 'temp_01', 'Gateway3', '00003', 24.8, 66.9, '{"battery": 95, "signal": -46}'),
('2025-10-01 08:00:00+07', 'temp_01', 'Gateway3', '00003', 25.5, 64.3, '{"battery": 95, "signal": -45}'),
('2025-10-01 12:30:00+07', 'temp_01', 'Gateway3', '00003', 28.2, 61.5, '{"battery": 94, "signal": -44}'),
('2025-10-01 16:00:00+07', 'temp_01', 'Gateway3', '00003', 29.5, 59.2, '{"battery": 94, "signal": -43}'),
('2025-10-01 19:15:00+07', 'temp_01', 'Gateway3', '00003', 27.3, 62.8, '{"battery": 94, "signal": -45}'),
('2025-10-01 22:30:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 65.4, '{"battery": 94, "signal": -46}'),
('2025-10-02 08:15:00+07', 'temp_01', 'Gateway3', '00003', 26.1, 63.7, '{"battery": 94, "signal": -45}'),
('2025-10-02 13:00:00+07', 'temp_01', 'Gateway3', '00003', 29.8, 58.5, '{"battery": 93, "signal": -43}'),
('2025-10-02 16:45:00+07', 'temp_01', 'Gateway3', '00003', 30.5, 57.1, '{"battery": 93, "signal": -42}'),
('2025-10-02 20:00:00+07', 'temp_01', 'Gateway3', '00003', 27.8, 61.9, '{"battery": 93, "signal": -44}'),
('2025-10-02 23:00:00+07', 'temp_01', 'Gateway3', '00003', 26.2, 64.5, '{"battery": 93, "signal": -46}'),
('2025-10-05 09:00:00+07', 'temp_01', 'Gateway3', '00003', 27.5, 62.0, '{"battery": 93, "signal": -44}'),
('2025-10-05 13:30:00+07', 'temp_01', 'Gateway3', '00003', 31.2, 55.8, '{"battery": 92, "signal": -42}'),
('2025-10-05 17:00:00+07', 'temp_01', 'Gateway3', '00003', 32.5, 53.2, '{"battery": 92, "signal": -41}'),
('2025-10-05 20:30:00+07', 'temp_01', 'Gateway3', '00003', 28.9, 59.5, '{"battery": 92, "signal": -43}'),
('2025-10-06 08:30:00+07', 'temp_01', 'Gateway3', '00003', 27.8, 61.2, '{"battery": 92, "signal": -44}'),
('2025-10-06 14:00:00+07', 'temp_01', 'Gateway3', '00003', 32.8, 52.5, '{"battery": 91, "signal": -41}'),
('2025-10-06 18:15:00+07', 'temp_01', 'Gateway3', '00003', 31.5, 54.8, '{"battery": 91, "signal": -42}'),
('2025-10-06 21:45:00+07', 'temp_01', 'Gateway3', '00003', 29.2, 58.3, '{"battery": 91, "signal": -43}'),
('2025-10-07 09:15:00+07', 'temp_01', 'Gateway3', '00003', 28.3, 60.5, '{"battery": 91, "signal": -43}'),
('2025-10-07 14:30:00+07', 'temp_01', 'Gateway3', '00003', 33.2, 51.2, '{"battery": 90, "signal": -40}'),
('2025-10-07 18:45:00+07', 'temp_01', 'Gateway3', '00003', 31.8, 53.5, '{"battery": 90, "signal": -41}'),
('2025-10-07 22:15:00+07', 'temp_01', 'Gateway3', '00003', 29.5, 57.8, '{"battery": 90, "signal": -43}'),
('2025-10-10 10:00:00+07', 'temp_01', 'Gateway3', '00003', 28.8, 59.2, '{"battery": 90, "signal": -42}'),
('2025-10-10 15:00:00+07', 'temp_01', 'Gateway3', '00003', 33.5, 50.5, '{"battery": 89, "signal": -40}'),
('2025-10-10 19:30:00+07', 'temp_01', 'Gateway3', '00003', 32.1, 52.8, '{"battery": 89, "signal": -41}'),
('2025-10-10 23:00:00+07', 'temp_01', 'Gateway3', '00003', 29.8, 56.5, '{"battery": 89, "signal": -43}'),
('2025-10-11 08:45:00+07', 'temp_01', 'Gateway3', '00003', 28.5, 60.0, '{"battery": 89, "signal": -42}'),
('2025-10-11 15:30:00+07', 'temp_01', 'Gateway3', '00003', 34.2, 49.2, '{"battery": 88, "signal": -39}'),
('2025-10-11 20:00:00+07', 'temp_01', 'Gateway3', '00003', 32.8, 51.5, '{"battery": 88, "signal": -41}'),
('2025-10-12 09:30:00+07', 'temp_01', 'Gateway3', '00003', 29.2, 58.8, '{"battery": 88, "signal": -42}'),
('2025-10-12 16:00:00+07', 'temp_01', 'Gateway3', '00003', 34.8, 48.5, '{"battery": 87, "signal": -38}'),
('2025-10-12 21:00:00+07', 'temp_01', 'Gateway3', '00003', 31.5, 53.2, '{"battery": 87, "signal": -41}'),
('2025-10-15 08:00:00+07', 'temp_01', 'Gateway3', '00003', 26.8, 62.5, '{"battery": 87, "signal": -43}'),
('2025-10-15 13:00:00+07', 'temp_01', 'Gateway3', '00003', 29.5, 58.2, '{"battery": 86, "signal": -42}'),
('2025-10-15 17:30:00+07', 'temp_01', 'Gateway3', '00003', 28.2, 60.5, '{"battery": 86, "signal": -43}'),
('2025-10-15 21:00:00+07', 'temp_01', 'Gateway3', '00003', 26.5, 63.8, '{"battery": 86, "signal": -44}'),
('2025-10-16 09:00:00+07', 'temp_01', 'Gateway3', '00003', 26.2, 63.2, '{"battery": 86, "signal": -43}'),
('2025-10-16 14:15:00+07', 'temp_01', 'Gateway3', '00003', 28.8, 59.5, '{"battery": 85, "signal": -42}'),
('2025-10-16 18:45:00+07', 'temp_01', 'Gateway3', '00003', 27.5, 61.8, '{"battery": 85, "signal": -43}'),
('2025-10-16 22:30:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 64.5, '{"battery": 85, "signal": -45}'),
('2025-10-17 08:30:00+07', 'temp_01', 'Gateway3', '00003', 25.5, 64.8, '{"battery": 85, "signal": -44}'),
('2025-10-17 13:30:00+07', 'temp_01', 'Gateway3', '00003', 28.2, 60.2, '{"battery": 84, "signal": -42}'),
('2025-10-17 17:00:00+07', 'temp_01', 'Gateway3', '00003', 27.0, 62.5, '{"battery": 84, "signal": -43}'),
('2025-10-17 21:30:00+07', 'temp_01', 'Gateway3', '00003', 25.2, 65.2, '{"battery": 84, "signal": -45}'),
('2025-10-20 10:00:00+07', 'temp_01', 'Gateway3', '00003', 26.5, 63.0, '{"battery": 84, "signal": -43}'),
('2025-10-20 15:00:00+07', 'temp_01', 'Gateway3', '00003', 29.0, 59.0, '{"battery": 83, "signal": -42}'),
('2025-10-20 19:00:00+07', 'temp_01', 'Gateway3', '00003', 27.8, 61.0, '{"battery": 83, "signal": -43}'),
('2025-10-20 23:00:00+07', 'temp_01', 'Gateway3', '00003', 26.0, 64.0, '{"battery": 83, "signal": -44}'),
('2025-10-21 09:00:00+07', 'temp_01', 'Gateway3', '00003', 26.2, 63.5, '{"battery": 83, "signal": -43}'),
('2025-10-21 14:00:00+07', 'temp_01', 'Gateway3', '00003', 28.5, 60.0, '{"battery": 82, "signal": -42}'),
('2025-10-21 18:00:00+07', 'temp_01', 'Gateway3', '00003', 27.3, 61.8, '{"battery": 82, "signal": -43}'),
('2025-10-21 22:00:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 64.2, '{"battery": 82, "signal": -44}'),
('2025-10-22 08:00:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 64.0, '{"battery": 82, "signal": -44}'),
('2025-10-22 13:00:00+07', 'temp_01', 'Gateway3', '00003', 28.0, 60.5, '{"battery": 81, "signal": -42}'),
('2025-10-22 17:00:00+07', 'temp_01', 'Gateway3', '00003', 27.0, 62.0, '{"battery": 81, "signal": -43}'),
('2025-10-22 21:00:00+07', 'temp_01', 'Gateway3', '00003', 25.5, 64.5, '{"battery": 81, "signal": -44}'),
('2025-10-23 09:30:00+07', 'temp_01', 'Gateway3', '00003', 26.0, 63.8, '{"battery": 81, "signal": -43}'),
('2025-10-23 14:30:00+07', 'temp_01', 'Gateway3', '00003', 28.8, 59.5, '{"battery": 80, "signal": -41}'),
('2025-10-23 18:30:00+07', 'temp_01', 'Gateway3', '00003', 27.5, 61.5, '{"battery": 80, "signal": -43}'),
('2025-10-23 22:30:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 64.0, '{"battery": 80, "signal": -44}'),
('2025-10-24 08:15:00+07', 'temp_01', 'Gateway3', '00003', 25.5, 64.5, '{"battery": 80, "signal": -44}'),
('2025-10-24 13:15:00+07', 'temp_01', 'Gateway3', '00003', 28.2, 60.0, '{"battery": 79, "signal": -42}'),
('2025-10-24 17:15:00+07', 'temp_01', 'Gateway3', '00003', 27.2, 62.0, '{"battery": 79, "signal": -43}'),
('2025-10-24 21:15:00+07', 'temp_01', 'Gateway3', '00003', 26.0, 64.0, '{"battery": 79, "signal": -44}'),
('2025-10-25 09:00:00+07', 'temp_01', 'Gateway3', '00003', 26.3, 63.5, '{"battery": 79, "signal": -43}'),
('2025-10-25 14:00:00+07', 'temp_01', 'Gateway3', '00003', 28.5, 60.0, '{"battery": 78, "signal": -42}'),
('2025-10-25 18:00:00+07', 'temp_01', 'Gateway3', '00003', 27.5, 61.5, '{"battery": 78, "signal": -43}'),
('2025-10-25 22:00:00+07', 'temp_01', 'Gateway3', '00003', 26.0, 64.0, '{"battery": 78, "signal": -44}'),
('2025-10-26 08:00:00+07', 'temp_01', 'Gateway3', '00003', 25.8, 64.2, '{"battery": 78, "signal": -44}'),
('2025-10-26 09:00:00+07', 'temp_01', 'Gateway3', '00003', 26.5, 63.5, '{"battery": 78, "signal": -43}'),
('2025-10-26 10:00:00+07', 'temp_01', 'Gateway3', '00003', 27.2, 62.8, '{"battery": 78, "signal": -42}'),
('2025-10-26 11:00:00+07', 'temp_01', 'Gateway3', '00003', 28.0, 61.5, '{"battery": 77, "signal": -42}'),
('2025-10-26 12:00:00+07', 'temp_01', 'Gateway3', '00003', 29.0, 60.0, '{"battery": 77, "signal": -41}');

INSERT INTO access_logs (time, device_id, gateway_id, user_id, method, result, rfid_uid, metadata) VALUES
('2025-09-27 07:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-27 18:45:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-27 19:15:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'denied', 'unknown123', '{"location": "Main Gate", "reason": "Card not registered"}'),
('2025-09-28 08:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-28 17:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-29 07:45:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-29 18:20:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-29 22:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'denied', 'abc12345', '{"location": "Main Gate", "reason": "Invalid card format"}'),
('2025-09-30 08:15:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-09-30 17:45:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-01 07:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-01 18:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-02 08:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-02 17:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-02 20:15:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'denied', '11223344', '{"location": "Main Gate", "reason": "Card not registered"}'),
('2025-10-03 07:45:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-03 18:15:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-04 08:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-04 17:45:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-05 07:30:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}'),
('2025-10-05 18:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'rfid', 'granted', '8675f205', '{"location": "Main Gate", "card_type": "MIFARE Classic"}');

INSERT INTO access_logs (time, device_id, gateway_id, user_id, method, result, password_id, deny_reason, metadata) VALUES
('2025-09-27 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-27 12:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-27 16:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'denied', NULL, 'Wrong password', '{"location": "Front Door", "attempts": 1}'),
('2025-09-27 19:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-27 22:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-28 08:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-28 13:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-28 17:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-28 21:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-29 09:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-29 14:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'denied', NULL, 'Wrong password', '{"location": "Front Door", "attempts": 2}'),
('2025-09-29 14:05:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-29 18:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-29 22:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-30 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-30 12:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-30 17:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-09-30 20:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-01 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-01 13:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-01 18:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-01 21:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-02 08:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-02 12:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-02 16:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'denied', NULL, 'Wrong password', '{"location": "Front Door", "attempts": 1}'),
('2025-10-02 16:35:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-02 20:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-03 09:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-03 14:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-03 18:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-03 22:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-04 08:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-04 13:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-04 17:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-04 21:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-05 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-05 12:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-05 17:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-05 20:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-08 08:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-08 13:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-08 18:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-09 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-09 14:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'denied', NULL, 'Wrong password', '{"location": "Front Door", "attempts": 3}'),
('2025-10-09 14:05:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-09 19:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-10 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-10 12:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-10 17:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-10 21:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-11 09:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-11 13:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-11 18:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-11 22:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-12 08:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-12 12:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-12 17:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-12 21:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-15 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-15 14:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-15 18:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-16 08:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-16 13:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-16 17:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-16 21:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-17 09:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-17 14:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-17 18:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-18 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-18 12:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-18 17:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-18 20:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-19 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-19 13:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-19 18:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-19 21:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-22 08:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-22 13:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-22 17:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-22 21:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-23 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-23 14:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-23 18:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-24 08:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-24 12:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-24 17:15:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-24 20:45:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-25 09:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-25 13:30:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-25 18:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-26 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}'),
('2025-10-26 12:00:00+07', 'passkey_01', 'Gateway2', '00002', 'passkey', 'granted', 'passwd_00002', NULL, '{"location": "Front Door"}');

INSERT INTO device_status (time, device_id, gateway_id, user_id, status, sequence, metadata) VALUES
('2025-09-27 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 1, '{"uptime": 86400, "memory_usage": 45}'),
('2025-09-28 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 2, '{"uptime": 172800, "memory_usage": 46}'),
('2025-09-29 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 3, '{"uptime": 259200, "memory_usage": 47}'),
('2025-09-30 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 4, '{"uptime": 345600, "memory_usage": 48}'),
('2025-10-01 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 5, '{"uptime": 432000, "memory_usage": 49}'),
('2025-10-02 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 6, '{"uptime": 518400, "memory_usage": 50}'),
('2025-10-03 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 7, '{"uptime": 604800, "memory_usage": 51}'),
('2025-10-04 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 8, '{"uptime": 691200, "memory_usage": 52}'),
('2025-10-05 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 9, '{"uptime": 777600, "memory_usage": 53}'),
('2025-10-06 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 10, '{"uptime": 864000, "memory_usage": 54}'),
('2025-10-07 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 11, '{"uptime": 950400, "memory_usage": 55}'),
('2025-10-08 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 12, '{"uptime": 1036800, "memory_usage": 56}'),
('2025-10-09 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 13, '{"uptime": 1123200, "memory_usage": 57}'),
('2025-10-10 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 14, '{"uptime": 1209600, "memory_usage": 58}'),
('2025-10-11 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 15, '{"uptime": 1296000, "memory_usage": 59}'),
('2025-10-12 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 16, '{"uptime": 1382400, "memory_usage": 60}'),
('2025-10-13 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 17, '{"uptime": 1468800, "memory_usage": 61}'),
('2025-10-14 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 18, '{"uptime": 1555200, "memory_usage": 62}'),
('2025-10-15 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 19, '{"uptime": 1641600, "memory_usage": 63}'),
('2025-10-16 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 20, '{"uptime": 1728000, "memory_usage": 64}'),
('2025-10-17 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 21, '{"uptime": 1814400, "memory_usage": 65}'),
('2025-10-18 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 22, '{"uptime": 1900800, "memory_usage": 66}'),
('2025-10-19 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 23, '{"uptime": 1987200, "memory_usage": 67}'),
('2025-10-20 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 24, '{"uptime": 2073600, "memory_usage": 68}'),
('2025-10-21 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 25, '{"uptime": 2160000, "memory_usage": 69}'),
('2025-10-22 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 26, '{"uptime": 2246400, "memory_usage": 70}'),
('2025-10-23 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 27, '{"uptime": 2332800, "memory_usage": 71}'),
('2025-10-24 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 28, '{"uptime": 2419200, "memory_usage": 72}'),
('2025-10-25 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 29, '{"uptime": 2505600, "memory_usage": 73}'),
('2025-10-26 07:00:00+07', 'rfid_gate_01', 'Gateway1', '00001', 'online', 30, '{"uptime": 2592000, "memory_usage": 74}'),
('2025-09-27 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 1, '{"uptime": 82800, "cpu_usage": 12}'),
('2025-09-28 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 2, '{"uptime": 169200, "cpu_usage": 13}'),
('2025-09-29 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 3, '{"uptime": 255600, "cpu_usage": 14}'),
('2025-09-30 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 4, '{"uptime": 342000, "cpu_usage": 15}'),
('2025-10-01 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 5, '{"uptime": 428400, "cpu_usage": 16}'),
('2025-10-02 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 6, '{"uptime": 514800, "cpu_usage": 17}'),
('2025-10-03 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 7, '{"uptime": 601200, "cpu_usage": 18}'),
('2025-10-04 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 8, '{"uptime": 687600, "cpu_usage": 19}'),
('2025-10-05 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 9, '{"uptime": 774000, "cpu_usage": 20}'),
('2025-10-06 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 10, '{"uptime": 860400, "cpu_usage": 21}'),
('2025-10-07 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 11, '{"uptime": 946800, "cpu_usage": 22}'),
('2025-10-08 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 12, '{"uptime": 1033200, "cpu_usage": 23}'),
('2025-10-09 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 13, '{"uptime": 1119600, "cpu_usage": 24}'),
('2025-10-10 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 14, '{"uptime": 1206000, "cpu_usage": 25}'),
('2025-10-11 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 15, '{"uptime": 1292400, "cpu_usage": 26}'),
('2025-10-12 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 16, '{"uptime": 1378800, "cpu_usage": 27}'),
('2025-10-13 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 17, '{"uptime": 1465200, "cpu_usage": 28}'),
('2025-10-14 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 18, '{"uptime": 1551600, "cpu_usage": 29}'),
('2025-10-15 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 19, '{"uptime": 1638000, "cpu_usage": 30}'),
('2025-10-16 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 20, '{"uptime": 1724400, "cpu_usage": 31}'),
('2025-10-17 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 21, '{"uptime": 1810800, "cpu_usage": 32}'),
('2025-10-18 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 22, '{"uptime": 1897200, "cpu_usage": 33}'),
('2025-10-19 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 23, '{"uptime": 1983600, "cpu_usage": 34}'),
('2025-10-20 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 24, '{"uptime": 2070000, "cpu_usage": 35}'),
('2025-10-21 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 25, '{"uptime": 2156400, "cpu_usage": 36}'),
('2025-10-22 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 26, '{"uptime": 2242800, "cpu_usage": 37}'),
('2025-10-23 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 27, '{"uptime": 2329200, "cpu_usage": 38}'),
('2025-10-24 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 28, '{"uptime": 2415600, "cpu_usage": 39}'),
('2025-10-25 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 29, '{"uptime": 2502000, "cpu_usage": 40}'),
('2025-10-26 08:00:00+07', 'passkey_01', 'Gateway2', '00002', 'online', 30, '{"uptime": 2588400, "cpu_usage": 41}'),
('2025-09-27 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 1, '{"battery": 98, "signal_strength": -45}'),
('2025-09-28 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 2, '{"battery": 97, "signal_strength": -44}'),
('2025-09-29 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 3, '{"battery": 96, "signal_strength": -46}'),
('2025-09-30 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 4, '{"battery": 96, "signal_strength": -45}'),
('2025-10-01 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 5, '{"battery": 95, "signal_strength": -44}'),
('2025-10-02 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 6, '{"battery": 94, "signal_strength": -43}'),
('2025-10-02 15:30:00+07', 'temp_01', 'Gateway3', '00003', 'offline', 7, '{"battery": 94, "signal_strength": null, "reason": "Connection timeout"}'),
('2025-10-02 15:45:00+07', 'temp_01', 'Gateway3', '00003', 'online', 8, '{"battery": 94, "signal_strength": -45}'),
('2025-10-03 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 9, '{"battery": 93, "signal_strength": -44}'),
('2025-10-04 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 10, '{"battery": 93, "signal_strength": -43}'),
('2025-10-05 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 11, '{"battery": 92, "signal_strength": -42}'),
('2025-10-06 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 12, '{"battery": 91, "signal_strength": -41}'),
('2025-10-07 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 13, '{"battery": 90, "signal_strength": -40}'),
('2025-10-08 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 14, '{"battery": 90, "signal_strength": -42}'),
('2025-10-09 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 15, '{"battery": 89, "signal_strength": -43}'),
('2025-10-10 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 16, '{"battery": 88, "signal_strength": -44}'),
('2025-10-11 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 17, '{"battery": 87, "signal_strength": -45}'),
('2025-10-12 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 18, '{"battery": 87, "signal_strength": -43}'),
('2025-10-13 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 19, '{"battery": 86, "signal_strength": -44}'),
('2025-10-14 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 20, '{"battery": 85, "signal_strength": -45}'),
('2025-10-15 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 21, '{"battery": 85, "signal_strength": -43}'),
('2025-10-16 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 22, '{"battery": 84, "signal_strength": -44}'),
('2025-10-17 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 23, '{"battery": 83, "signal_strength": -45}'),
('2025-10-18 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 24, '{"battery": 83, "signal_strength": -43}'),
('2025-10-19 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 25, '{"battery": 82, "signal_strength": -44}'),
('2025-10-20 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 26, '{"battery": 81, "signal_strength": -45}'),
('2025-10-21 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 27, '{"battery": 80, "signal_strength": -43}'),
('2025-10-22 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 28, '{"battery": 80, "signal_strength": -44}'),
('2025-10-23 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 29, '{"battery": 79, "signal_strength": -45}'),
('2025-10-24 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 30, '{"battery": 78, "signal_strength": -44}'),
('2025-10-25 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 31, '{"battery": 78, "signal_strength": -43}'),
('2025-10-26 08:00:00+07', 'temp_01', 'Gateway3', '00003', 'online', 32, '{"battery": 77, "signal_strength": -42}'),
('2025-09-27 08:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 1, '{"power_state": "off", "speed": 0}'),
('2025-10-05 13:45:00+07', 'fan_01', 'Gateway3', '00003', 'online', 2, '{"power_state": "on", "speed": 2}'),
('2025-10-05 20:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 3, '{"power_state": "off", "speed": 0}'),
('2025-10-06 14:30:00+07', 'fan_01', 'Gateway3', '00003', 'online', 4, '{"power_state": "on", "speed": 3}'),
('2025-10-06 21:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 5, '{"power_state": "off", "speed": 0}'),
('2025-10-07 14:45:00+07', 'fan_01', 'Gateway3', '00003', 'online', 6, '{"power_state": "on", "speed": 3}'),
('2025-10-07 21:30:00+07', 'fan_01', 'Gateway3', '00003', 'online', 7, '{"power_state": "off", "speed": 0}'),
('2025-10-10 15:15:00+07', 'fan_01', 'Gateway3', '00003', 'online', 8, '{"power_state": "on", "speed": 3}'),
('2025-10-10 22:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 9, '{"power_state": "off", "speed": 0}'),
('2025-10-11 15:45:00+07', 'fan_01', 'Gateway3', '00003', 'online', 10, '{"power_state": "on", "speed": 3}'),
('2025-10-11 19:30:00+07', 'fan_01', 'Gateway3', '00003', 'online', 11, '{"power_state": "off", "speed": 0}'),
('2025-10-12 16:15:00+07', 'fan_01', 'Gateway3', '00003', 'online', 12, '{"power_state": "on", "speed": 3}'),
('2025-10-12 20:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 13, '{"power_state": "off", "speed": 0}'),
('2025-10-15 08:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 14, '{"power_state": "off", "speed": 0}'),
('2025-10-26 08:00:00+07', 'fan_01', 'Gateway3', '00003', 'online', 15, '{"power_state": "off", "speed": 0}');


INSERT INTO system_logs (time, gateway_id, user_id, log_type, event, severity, message, metadata) VALUES
('2025-09-27 06:00:00+07', 'Gateway1', '00001', 'system_event', 'gateway_started', 'info', 'Gateway Gateway1 started successfully', '{"version": "2.1.0", "ip": "192.168.1.100"}'),
('2025-09-27 06:00:30+07', 'Gateway2', '00002', 'system_event', 'gateway_started', 'info', 'Gateway Gateway2 started successfully', '{"version": "2.1.0", "ip": "192.168.1.101"}'),
('2025-09-27 06:01:00+07', 'Gateway3', '00003', 'system_event', 'gateway_started', 'info', 'Gateway Gateway3 started successfully', '{"version": "2.1.0", "ip": "192.168.1.102"}'),
('2025-09-27 06:05:00+07', 'Gateway1', '00001', 'system_event', 'device_registered', 'info', 'Device rfid_gate_01 registered', '{"device_type": "rfid_gate", "communication": "LoRa"}'),
('2025-09-27 06:05:30+07', 'Gateway2', '00002', 'system_event', 'device_registered', 'info', 'Device passkey_01 registered', '{"device_type": "passkey", "communication": "WiFi"}'),
('2025-09-27 06:06:00+07', 'Gateway3', '00003', 'system_event', 'device_registered', 'info', 'Device temp_01 registered', '{"device_type": "temp_DH11", "communication": "WiFi"}'),
('2025-09-27 06:06:30+07', 'Gateway3', '00003', 'system_event', 'device_registered', 'info', 'Device fan_01 registered', '{"device_type": "relay_fan", "communication": "WiFi"}'),
('2025-09-28 14:22:00+07', 'Gateway1', '00001', 'system_event', 'network_reconnect', 'warning', 'Gateway reconnected after brief network interruption', '{"downtime_seconds": 45}'),
('2025-09-30 03:15:00+07', 'Gateway2', '00002', 'system_event', 'backup_completed', 'info', 'System backup completed successfully', '{"backup_size_mb": 125, "duration_seconds": 180}'),
('2025-10-01 10:30:00+07', 'Gateway3', '00003', 'alert', 'automation_triggered', 'info', 'Fan automation triggered due to high temperature', '{"temperature": 31.5, "threshold": 30.0}'),
('2025-10-02 15:30:00+07', 'Gateway3', '00003', 'alert', 'device_offline', 'warning', 'Device temp_01 went offline', '{"last_seen": "2025-10-02 15:25:00"}'),
('2025-10-02 15:45:00+07', 'Gateway3', '00003', 'alert', 'device_online', 'info', 'Device temp_01 back online', '{"downtime_minutes": 15}'),
('2025-10-03 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 6, "cpu_usage": 15, "memory_usage": 45}'),
('2025-10-03 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 6, "cpu_usage": 12, "memory_usage": 38}'),
('2025-10-03 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 6, "cpu_usage": 18, "memory_usage": 52}'),
('2025-10-05 14:00:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'warning', 'Temperature exceeded threshold', '{"temperature": 32.5, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-06 14:30:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'warning', 'Temperature exceeded threshold', '{"temperature": 32.8, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-07 14:45:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'warning', 'Temperature exceeded threshold', '{"temperature": 33.2, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-08 02:00:00+07', 'Gateway1', '00001', 'system_event', 'firmware_update_check', 'info', 'Firmware update check - system up to date', '{"current_version": "2.1.0", "latest_version": "2.1.0"}'),
('2025-10-08 02:00:30+07', 'Gateway2', '00002', 'system_event', 'firmware_update_check', 'info', 'Firmware update check - system up to date', '{"current_version": "2.1.0", "latest_version": "2.1.0"}'),
('2025-10-08 02:01:00+07', 'Gateway3', '00003', 'system_event', 'firmware_update_check', 'info', 'Firmware update check - system up to date', '{"current_version": "2.1.0", "latest_version": "2.1.0"}'),
('2025-09-27 19:15:00+07', 'Gateway1', '00001', 'alert', 'unauthorized_access_attempt', 'warning', 'Unknown RFID card attempted access', '{"card_uid": "unknown123", "device": "rfid_gate_01"}'),
('2025-09-27 16:45:00+07', 'Gateway2', '00002', 'alert', 'failed_password_attempt', 'warning', 'Failed password attempt', '{"device": "passkey_01", "attempts": 1}'),
('2025-09-29 22:30:00+07', 'Gateway1', '00001', 'alert', 'unauthorized_access_attempt', 'warning', 'Invalid RFID card format', '{"card_uid": "abc12345", "device": "rfid_gate_01"}'),
('2025-09-29 14:00:00+07', 'Gateway2', '00002', 'alert', 'failed_password_attempt', 'warning', 'Failed password attempt', '{"device": "passkey_01", "attempts": 2}'),
('2025-10-02 20:15:00+07', 'Gateway1', '00001', 'alert', 'unauthorized_access_attempt', 'warning', 'Unknown RFID card attempted access', '{"card_uid": "11223344", "device": "rfid_gate_01"}'),
('2025-10-02 16:30:00+07', 'Gateway2', '00002', 'alert', 'failed_password_attempt', 'warning', 'Failed password attempt', '{"device": "passkey_01", "attempts": 1}'),
('2025-10-09 14:00:00+07', 'Gateway2', '00002', 'alert', 'failed_password_attempt', 'critical', 'Multiple failed password attempts', '{"device": "passkey_01", "attempts": 3, "time_window_minutes": 10}'),
('2025-10-10 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 13, "cpu_usage": 16, "memory_usage": 48}'),
('2025-10-10 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 13, "cpu_usage": 13, "memory_usage": 40}'),
('2025-10-10 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 13, "cpu_usage": 19, "memory_usage": 55}'),
('2025-10-10 15:30:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'warning', 'Temperature exceeded threshold', '{"temperature": 33.5, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-11 15:45:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'warning', 'Temperature exceeded threshold', '{"temperature": 34.2, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-12 16:15:00+07', 'Gateway3', '00003', 'alert', 'high_temperature', 'critical', 'Critical temperature level', '{"temperature": 34.8, "threshold": 30.0, "device": "temp_01"}'),
('2025-10-13 03:00:00+07', 'Gateway1', '00001', 'system_event', 'database_cleanup', 'info', 'Database cleanup completed', '{"deleted_records": 2500, "duration_seconds": 120}'),
('2025-10-13 03:00:30+07', 'Gateway2', '00002', 'system_event', 'database_cleanup', 'info', 'Database cleanup completed', '{"deleted_records": 3200, "duration_seconds": 145}'),
('2025-10-13 03:01:00+07', 'Gateway3', '00003', 'system_event', 'database_cleanup', 'info', 'Database cleanup completed', '{"deleted_records": 4100, "duration_seconds": 180}'),
('2025-10-15 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 18, "cpu_usage": 15, "memory_usage": 47}'),
('2025-10-15 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 18, "cpu_usage": 12, "memory_usage": 39}'),
('2025-10-15 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 18, "cpu_usage": 17, "memory_usage": 53}'),
('2025-10-16 11:30:00+07', 'Gateway2', '00002', 'system_event', 'config_update', 'info', 'Configuration updated', '{"updated_fields": ["password_expiry", "max_attempts"], "updated_by": "00002"}'),
('2025-10-18 09:15:00+07', 'Gateway1', '00001', 'system_event', 'backup_completed', 'info', 'Automated backup completed', '{"backup_size_mb": 135, "duration_seconds": 195}'),
('2025-10-18 09:15:30+07', 'Gateway2', '00002', 'system_event', 'backup_completed', 'info', 'Automated backup completed', '{"backup_size_mb": 142, "duration_seconds": 210}'),
('2025-10-18 09:16:00+07', 'Gateway3', '00003', 'system_event', 'backup_completed', 'info', 'Automated backup completed', '{"backup_size_mb": 158, "duration_seconds": 240}'),
('2025-10-20 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 23, "cpu_usage": 16, "memory_usage": 49}'),
('2025-10-20 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 23, "cpu_usage": 14, "memory_usage": 41}'),
('2025-10-20 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 23, "cpu_usage": 18, "memory_usage": 54}'),
('2025-10-21 14:30:00+07', 'Gateway1', '00001', 'system_event', 'security_scan', 'info', 'Security scan completed - no issues found', '{"scan_duration_seconds": 45, "files_scanned": 1250}'),
('2025-10-21 14:30:30+07', 'Gateway2', '00002', 'system_event', 'security_scan', 'info', 'Security scan completed - no issues found', '{"scan_duration_seconds": 52, "files_scanned": 1180}'),
('2025-10-21 14:31:00+07', 'Gateway3', '00003', 'system_event', 'security_scan', 'info', 'Security scan completed - no issues found', '{"scan_duration_seconds": 58, "files_scanned": 1320}'),
('2025-10-22 16:45:00+07', 'Gateway3', '00003', 'system_event', 'automation_update', 'info', 'Automation rule updated', '{"rule": "fan_control", "updated_by": "00003", "changes": "threshold adjusted"}'),
('2025-10-23 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 26, "cpu_usage": 15, "memory_usage": 48}'),
('2025-10-23 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 26, "cpu_usage": 13, "memory_usage": 40}'),
('2025-10-23 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 26, "cpu_usage": 17, "memory_usage": 53}'),
('2025-10-24 10:20:00+07', 'Gateway1', '00001', 'system_event', 'certificate_check', 'info', 'SSL certificate validity check passed', '{"expires_in_days": 87}'),
('2025-10-24 10:20:30+07', 'Gateway2', '00002', 'system_event', 'certificate_check', 'info', 'SSL certificate validity check passed', '{"expires_in_days": 87}'),
('2025-10-24 10:21:00+07', 'Gateway3', '00003', 'system_event', 'certificate_check', 'info', 'SSL certificate validity check passed', '{"expires_in_days": 87}'),
('2025-10-25 02:00:00+07', 'Gateway1', '00001', 'system_event', 'storage_check', 'info', 'Storage usage within normal limits', '{"used_percent": 45, "free_gb": 125}'),
('2025-10-25 02:00:30+07', 'Gateway2', '00002', 'system_event', 'storage_check', 'info', 'Storage usage within normal limits', '{"used_percent": 38, "free_gb": 142}'),
('2025-10-25 02:01:00+07', 'Gateway3', '00003', 'system_event', 'storage_check', 'warning', 'Storage usage approaching threshold', '{"used_percent": 72, "free_gb": 68}'),
('2025-10-25 15:30:00+07', 'Gateway3', '00003', 'system_event', 'log_rotation', 'info', 'Log files rotated successfully', '{"archived_size_mb": 245, "archived_files": 15}'),
('2025-10-26 08:00:00+07', 'Gateway1', '00001', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 29, "cpu_usage": 16, "memory_usage": 49}'),
('2025-10-26 08:00:00+07', 'Gateway2', '00002', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 29, "cpu_usage": 14, "memory_usage": 41}'),
('2025-10-26 08:00:00+07', 'Gateway3', '00003', 'system_event', 'heartbeat', 'info', 'Gateway heartbeat - all systems normal', '{"uptime_days": 29, "cpu_usage": 18, "memory_usage": 55}'),
('2025-10-26 09:30:00+07', 'Gateway3', '00003', 'system_event', 'telemetry_update', 'info', 'Telemetry data synchronized', '{"records_synced": 48, "sync_duration_seconds": 12}'),
('2025-10-26 10:15:00+07', 'Gateway1', '00001', 'system_event', 'access_log_sync', 'info', 'Access logs synchronized to VPS', '{"records_synced": 15, "sync_duration_seconds": 8}'),
('2025-10-26 10:15:30+07', 'Gateway2', '00002', 'system_event', 'access_log_sync', 'info', 'Access logs synchronized to VPS', '{"records_synced": 22, "sync_duration_seconds": 10}'),
('2025-10-26 11:00:00+07', 'Gateway3', '00003', 'system_event', 'device_health_check', 'info', 'All devices responding normally', '{"devices_checked": 2, "all_healthy": true}'),
('2025-10-26 11:30:00+07', 'Gateway1', '00001', 'system_event', 'mqtt_reconnect', 'info', 'MQTT connection reestablished', '{"broker": "VPS", "reconnect_attempts": 1}');

INSERT INTO system_logs (time, gateway_id, user_id, log_type, event, severity, message, metadata) VALUES
('2025-09-28 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 24, "alerts": 1}'),
('2025-09-29 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 35, "alerts": 2}'),
('2025-09-30 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-01 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 22, "alerts": 0}'),
('2025-10-02 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 38, "alerts": 1}'),
('2025-10-03 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-04 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 20, "alerts": 0}'),
('2025-10-05 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 36, "alerts": 0}'),
('2025-10-06 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 2}'),
('2025-10-07 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 18, "alerts": 0}'),
('2025-10-08 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 32, "alerts": 0}'),
('2025-10-09 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-10 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 16, "alerts": 0}'),
('2025-10-11 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 40, "alerts": 0}'),
('2025-10-12 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 3}'),
('2025-10-13 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 14, "alerts": 0}'),
('2025-10-14 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 28, "alerts": 0}'),
('2025-10-15 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-16 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 18, "alerts": 0}'),
('2025-10-17 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 34, "alerts": 0}'),
('2025-10-18 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-19 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 22, "alerts": 0}'),
('2025-10-20 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 36, "alerts": 0}'),
('2025-10-21 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-22 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 20, "alerts": 0}'),
('2025-10-23 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 30, "alerts": 0}'),
('2025-10-24 08:00:00+07', 'Gateway3', '00003', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"telemetry_readings": 96, "fan_activations": 0}'),
('2025-10-25 08:00:00+07', 'Gateway1', '00001', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 16, "alerts": 0}'),
('2025-10-26 08:00:00+07', 'Gateway2', '00002', 'system_event', 'daily_report_generated', 'info', 'Daily activity report generated', '{"access_count": 28, "alerts": 0}');


INSERT INTO alerts (time, alert_id, device_id, gateway_id, user_id, alert_type, severity, message, value, threshold, acknowledged, metadata) VALUES
('2025-10-05 13:30:00+07', 'alert_001', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature exceeded normal threshold', 31.2, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-05 17:00:00+07', 'alert_002', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature still high', 32.5, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-06 14:00:00+07', 'alert_003', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature exceeded normal threshold', 32.8, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-06 14:00:00+07', 'alert_003', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature exceeded normal threshold', 32.8, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-06 18:15:00+07', 'alert_004', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature still elevated', 31.5, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-07 14:30:00+07', 'alert_005', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'critical', 'Critical temperature level reached', 33.2, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-07 18:45:00+07', 'alert_006', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature decreasing but still high', 31.8, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-10 15:00:00+07', 'alert_007', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'critical', 'Critical temperature level reached', 33.5, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-10 19:30:00+07', 'alert_008', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature still high', 32.1, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-11 15:30:00+07', 'alert_009', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'critical', 'Highest temperature recorded today', 34.2, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-11 20:00:00+07', 'alert_010', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature normalizing', 32.8, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-12 16:00:00+07', 'alert_011', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'critical', 'Record high temperature', 34.8, 30.0, false, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-12 21:00:00+07', 'alert_012', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature decreasing', 31.5, 30.0, true, '{"location": "Living Room", "fan_activated": true}'),
('2025-10-02 15:30:00+07', 'alert_013', 'temp_01', 'Gateway3', '00003', 'device_offline', 'warning', 'Temperature sensor not responding', NULL, NULL, true, '{"last_seen": "2025-10-02 15:25:00", "downtime_minutes": 15}'),
('2025-09-27 19:15:00+07', 'alert_014', 'rfid_gate_01', 'Gateway1', '00001', 'unauthorized_access', 'warning', 'Unknown RFID card attempted access', NULL, NULL, true, '{"card_uid": "unknown123", "method": "rfid"}'),
('2025-09-29 22:30:00+07', 'alert_015', 'rfid_gate_01', 'Gateway1', '00001', 'unauthorized_access', 'warning', 'Invalid RFID card format detected', NULL, NULL, true, '{"card_uid": "abc12345", "method": "rfid"}'),
('2025-10-02 20:15:00+07', 'alert_016', 'rfid_gate_01', 'Gateway1', '00001', 'unauthorized_access', 'warning', 'Unregistered RFID card', NULL, NULL, false, '{"card_uid": "11223344", "method": "rfid"}'),
('2025-09-27 16:45:00+07', 'alert_017', 'passkey_01', 'Gateway2', '00002', 'failed_authentication', 'warning', 'Incorrect password entered', NULL, NULL, true, '{"attempts": 1, "method": "passkey"}'),
('2025-09-29 14:00:00+07', 'alert_018', 'passkey_01', 'Gateway2', '00002', 'failed_authentication', 'warning', 'Failed password attempt', NULL, NULL, true, '{"attempts": 2, "method": "passkey"}'),
('2025-10-02 16:30:00+07', 'alert_019', 'passkey_01', 'Gateway2', '00002', 'failed_authentication', 'warning', 'Wrong password entered', NULL, NULL, true, '{"attempts": 1, "method": "passkey"}'),
('2025-10-09 14:00:00+07', 'alert_020', 'passkey_01', 'Gateway2', '00002', 'failed_authentication', 'critical', 'Multiple failed password attempts detected', NULL, NULL, false, '{"attempts": 3, "method": "passkey", "time_window": "10 minutes"}'),
('2025-10-18 08:00:00+07', 'alert_021', 'temp_01', 'Gateway3', '00003', 'battery_low', 'info', 'Battery level below 85%', 83, 85, true, '{"battery_percent": 83}'),
('2025-10-22 08:00:00+07', 'alert_022', 'temp_01', 'Gateway3', '00003', 'battery_low', 'warning', 'Battery level below 80%', 80, 80, false, '{"battery_percent": 80}'),
('2025-10-26 08:00:00+07', 'alert_023', 'temp_01', 'Gateway3', '00003', 'battery_low', 'warning', 'Battery level declining', 77, 80, false, '{"battery_percent": 77}'),
('2025-10-25 02:01:00+07', 'alert_024', NULL, 'Gateway3', '00003', 'storage_warning', 'warning', 'Storage usage high', 72, 70, false, '{"used_percent": 72, "free_gb": 68}'),
('2025-09-28 14:22:00+07', 'alert_025', NULL, 'Gateway1', '00001', 'network_interruption', 'info', 'Brief network disconnection', NULL, NULL, true, '{"downtime_seconds": 45, "auto_recovered": true}'),
('2025-10-13 15:00:00+07', 'alert_026', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature spike detected', 30.8, 30.0, true, '{"location": "Living Room"}'),
('2025-10-14 16:30:00+07', 'alert_027', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature elevated', 31.2, 30.0, true, '{"location": "Living Room"}'),
('2025-10-15 14:00:00+07', 'alert_028', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'info', 'Temperature slightly above threshold', 30.3, 30.0, true, '{"location": "Living Room"}'),
('2025-10-16 13:30:00+07', 'alert_029', 'temp_01', 'Gateway3', '00003', 'signal_weak', 'info', 'WiFi signal strength low', -55, -50, true, '{"signal_strength": -55, "device": "temp_01"}'),
('2025-10-17 15:45:00+07', 'alert_030', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature elevated again', 31.5, 30.0, true, '{"location": "Living Room"}'),
('2025-10-08 10:30:00+07', 'alert_031', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'info', 'Temperature slightly elevated', 30.2, 30.0, true, '{"location": "Living Room"}'),
('2025-10-09 14:45:00+07', 'alert_032', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature increasing', 31.0, 30.0, true, '{"location": "Living Room"}'),
('2025-10-03 11:20:00+07', 'alert_033', NULL, 'Gateway2', '00002', 'system_update_available', 'info', 'New firmware version available', NULL, NULL, true, '{"current_version": "2.1.0", "available_version": "2.1.1"}'),
('2025-10-04 09:15:00+07', 'alert_034', NULL, 'Gateway1', '00001', 'maintenance_reminder', 'info', 'Scheduled maintenance reminder', NULL, NULL, true, '{"next_maintenance": "2025-11-01"}'),
('2025-10-19 12:00:00+07', 'alert_035', 'temp_01', 'Gateway3', '00003', 'humidity_low', 'info', 'Humidity below normal range', 48, 50, true, '{"humidity_percent": 48}'),
('2025-10-20 16:30:00+07', 'alert_036', 'temp_01', 'Gateway3', '00003', 'temperature_high', 'warning', 'Temperature spike', 31.8, 30.0, false, '{"location": "Living Room"}'),
('2025-09-28 10:00:00+07', 'alert_037', NULL, 'Gateway3', '00003', 'system_startup', 'info', 'System startup completed', NULL, NULL, true, '{"startup_time_seconds": 45}'),
('2025-09-29 08:30:00+07', 'alert_038', 'passkey_01', 'Gateway2', '00002', 'config_change', 'info', 'Device configuration updated', NULL, NULL, true, '{"updated_by": "00002"}'),
('2025-09-30 14:20:00+07', 'alert_039', 'rfid_gate_01', 'Gateway1', '00001', 'device_health_check', 'info', 'Device health check passed', NULL, NULL, true, '{"all_tests_passed": true}'),
('2025-10-01 09:00:00+07', 'alert_040', NULL, 'Gateway1', '00001', 'backup_completed', 'info', 'Automated backup successful', NULL, NULL, true, '{"backup_size_mb": 120}'),
('2025-10-04 15:30:00+07', 'alert_041', 'temp_01', 'Gateway3', '00003', 'sensor_calibration', 'info', 'Sensor calibration completed', NULL, NULL, true, '{"accuracy": "±0.5°C"}'),
('2025-10-21 11:15:00+07', 'alert_042', NULL, 'Gateway2', '00002', 'security_scan_passed', 'info', 'Security scan completed successfully', NULL, NULL, true, '{"threats_found": 0}'),
('2025-10-23 13:45:00+07', 'alert_043', 'fan_01', 'Gateway3', '00003', 'device_command', 'info', 'Manual fan control activated', NULL, NULL, true, '{"command": "manual_on", "user": "00003"}'),
('2025-10-24 10:30:00+07', 'alert_044', NULL, 'Gateway1', '00001', 'certificate_renewed', 'info', 'SSL certificate validation successful', NULL, NULL, true, '{"expires_in_days": 87}'),
('2025-10-25 16:00:00+07', 'alert_045', 'temp_01', 'Gateway3', '00003', 'data_sync', 'info', 'Telemetry data synced to cloud', NULL, NULL, true, '{"records_synced": 288}'),
('2025-10-26 09:00:00+07', 'alert_046', NULL, 'Gateway3', '00003', 'system_health', 'info', 'All systems operating normally', NULL, NULL, true, '{"uptime_days": 29}'),
('2025-10-03 16:20:00+07', 'alert_047', 'passkey_01', 'Gateway2', '00002', 'usage_pattern', 'info', 'Unusual access time detected', NULL, NULL, true, '{"time": "03:15:00", "normal_range": "07:00-23:00"}'),
('2025-10-05 08:45:00+07', 'alert_048', 'rfid_gate_01', 'Gateway1', '00001', 'reader_status', 'info', 'RFID reader functioning normally', NULL, NULL, true, '{"read_success_rate": 99.8}'),
('2025-10-07 12:30:00+07', 'alert_049', NULL, 'Gateway2', '00002', 'database_optimization', 'info', 'Database optimization completed', NULL, NULL, true, '{"duration_seconds": 180, "space_freed_mb": 45}'),
('2025-10-08 14:15:00+07', 'alert_050', 'temp_01', 'Gateway3', '00003', 'reading_anomaly', 'warning', 'Unusual temperature fluctuation', 28.5, NULL, true, '{"change_rate": "2°C/15min"}'),
('2025-10-09 10:00:00+07', 'alert_051', NULL, 'Gateway1', '00001', 'network_latency', 'info', 'Network latency within acceptable range', NULL, NULL, true, '{"avg_latency_ms": 45}'),
('2025-10-11 13:20:00+07', 'alert_052', 'fan_01', 'Gateway3', '00003', 'automation_trigger', 'info', 'Automation rule triggered successfully', NULL, NULL, true, '{"rule": "temperature_control"}'),
('2025-10-12 09:45:00+07', 'alert_053', NULL, 'Gateway2', '00002', 'log_rotation', 'info', 'Log files rotated and archived', NULL, NULL, true, '{"archived_logs": 15, "size_mb": 120}'),
('2025-10-14 11:30:00+07', 'alert_054', 'rfid_gate_01', 'Gateway1', '00001', 'access_pattern', 'info', 'Normal access pattern detected', NULL, NULL, true, '{"daily_average": 20}'),
('2025-10-15 15:15:00+07', 'alert_055', 'temp_01', 'Gateway3', '00003', 'sensor_status', 'info', 'Sensor readings stable', NULL, NULL, true, '{"variance": 0.3}'),
('2025-10-16 08:50:00+07', 'alert_056', NULL, 'Gateway3', '00003', 'connectivity_test', 'info', 'All devices responding to ping', NULL, NULL, true, '{"devices_online": 2}'),
('2025-10-17 14:40:00+07', 'alert_057', 'passkey_01', 'Gateway2', '00002', 'keypad_status', 'info', 'Keypad functioning correctly', NULL, NULL, true, '{"button_test": "passed"}'),
('2025-10-18 10:25:00+07', 'alert_058', NULL, 'Gateway1', '00001', 'mqtt_connection', 'info', 'MQTT connection stable', NULL, NULL, true, '{"uptime_hours": 456}'),
('2025-10-19 16:10:00+07', 'alert_059', 'temp_01', 'Gateway3', '00003', 'battery_status', 'info', 'Battery discharge rate normal', NULL, NULL, true, '{"discharge_rate": "1%/day"}'),
('2025-10-20 12:55:00+07', 'alert_060', NULL, 'Gateway2', '00002', 'user_activity', 'info', 'User activity summary generated', NULL, NULL, true, '{"total_accesses": 142}'),
('2025-10-21 09:20:00+07', 'alert_061', 'fan_01', 'Gateway3', '00003', 'device_power', 'info', 'Fan power consumption normal', NULL, NULL, true, '{"power_watts": 35}'),
('2025-10-22 15:35:00+07', 'alert_062', NULL, 'Gateway1', '00001', 'system_resources', 'info', 'System resources within limits', NULL, NULL, true, '{"cpu": 16, "memory": 49}'),
('2025-10-23 11:05:00+07', 'alert_063', 'rfid_gate_01', 'Gateway1', '00001', 'reader_maintenance', 'info', 'RFID reader self-test passed', NULL, NULL, true, '{"test_cycles": 1000}'),
('2025-10-24 13:40:00+07', 'alert_064', 'temp_01', 'Gateway3', '00003', 'data_quality', 'info', 'Sensor data quality excellent', NULL, NULL, true, '{"quality_score": 98}'),
('2025-10-25 10:15:00+07', 'alert_065', NULL, 'Gateway3', '00003', 'automation_stats', 'info', 'Automation statistics updated', NULL, NULL, true, '{"triggers_this_month": 12}'),
('2025-10-01 14:25:00+07', 'alert_066', 'passkey_01', 'Gateway2', '00002', 'password_strength', 'info', 'All passwords meet security requirements', NULL, NULL, true, '{"compliance": 100}'),
('2025-10-02 11:50:00+07', 'alert_067', NULL, 'Gateway1', '00001', 'access_report', 'info', 'Monthly access report ready', NULL, NULL, true, '{"total_entries": 485}'),
('2025-10-03 09:30:00+07', 'alert_068', 'temp_01', 'Gateway3', '00003', 'telemetry_health', 'info', 'Telemetry collection healthy', NULL, NULL, true, '{"collection_rate": 100}'),
('2025-10-04 16:45:00+07', 'alert_069', NULL, 'Gateway2', '00002', 'encryption_status', 'info', 'All communications encrypted', NULL, NULL, true, '{"protocol": "TLS 1.3"}'),
('2025-10-06 12:10:00+07', 'alert_070', 'fan_01', 'Gateway3', '00003', 'relay_status', 'info', 'Relay switching cycles normal', NULL, NULL, true, '{"total_cycles": 245}'),
('2025-10-08 15:20:00+07', 'alert_071', NULL, 'Gateway1', '00001', 'time_sync', 'info', 'Time synchronization successful', NULL, NULL, true, '{"ntp_server": "time.google.com"}'),
('2025-10-10 10:35:00+07', 'alert_072', 'rfid_gate_01', 'Gateway1', '00001', 'antenna_power', 'info', 'RFID antenna power optimal', NULL, NULL, true, '{"power_level": 85}'),
('2025-10-11 14:50:00+07', 'alert_073', 'temp_01', 'Gateway3', '00003', 'calibration_due', 'info', 'Next calibration due in 30 days', NULL, NULL, true, '{"last_calibration": "2025-09-27"}'),
('2025-10-13 11:05:00+07', 'alert_074', NULL, 'Gateway2', '00002', 'audit_log', 'info', 'Audit log archived successfully', NULL, NULL, true, '{"entries": 1250}'),
('2025-10-14 16:25:00+07', 'alert_075', 'passkey_01', 'Gateway2', '00002', 'button_wear', 'info', 'Keypad buttons within spec', NULL, NULL, true, '{"button_presses": 15420}'),
('2025-10-16 09:40:00+07', 'alert_076', NULL, 'Gateway3', '00003', 'firmware_check', 'info', 'Running latest firmware version', NULL, NULL, true, '{"version": "2.1.0"}'),
('2025-10-17 13:55:00+07', 'alert_077', 'fan_01', 'Gateway3', '00003', 'motor_health', 'info', 'Fan motor operating smoothly', NULL, NULL, true, '{"vibration_level": "normal"}'),
('2025-10-18 10:10:00+07', 'alert_078', NULL, 'Gateway1', '00001', 'power_stability', 'info', 'Power supply stable', NULL, NULL, true, '{"voltage_variance": 0.5}'),
('2025-10-19 15:30:00+07', 'alert_079', 'temp_01', 'Gateway3', '00003', 'wireless_health', 'info', 'WiFi connection quality good', NULL, NULL, true, '{"signal_quality": 85}'),
('2025-10-21 12:45:00+07', 'alert_080', NULL, 'Gateway2', '00002', 'memory_usage', 'info', 'Memory usage stable', NULL, NULL, true, '{"used_percent": 41}'),
('2025-10-22 09:15:00+07', 'alert_081', 'rfid_gate_01', 'Gateway1', '00001', 'card_read_rate', 'info', 'Card read success rate high', NULL, NULL, true, '{"success_rate": 99.9}'),
('2025-10-23 14:30:00+07', 'alert_082', NULL, 'Gateway3', '00003', 'temperature_trend', 'info', 'Temperature trending downward', NULL, NULL, true, '{"avg_decrease": "0.5°C/day"}'),
('2025-10-24 11:50:00+07', 'alert_083', 'passkey_01', 'Gateway2', '00002', 'response_time', 'info', 'Device response time optimal', NULL, NULL, true, '{"avg_ms": 125}'),
('2025-10-25 16:05:00+07', 'alert_084', NULL, 'Gateway1', '00001', 'data_integrity', 'info', 'Data integrity check passed', NULL, NULL, true, '{"errors_found": 0}'),
('2025-10-26 10:20:00+07', 'alert_085', 'fan_01', 'Gateway3', '00003', 'energy_usage', 'info', 'Energy consumption within budget', NULL, NULL, true, '{"kwh_month": 2.4}'),
('2025-09-30 17:30:00+07', 'alert_086', NULL, 'Gateway3', '00003', 'cloud_sync', 'info', 'Cloud synchronization complete', NULL, NULL, true, '{"sync_time_seconds": 45}'),
('2025-10-02 08:45:00+07', 'alert_087', 'temp_01', 'Gateway3', '00003', 'sensor_drift', 'info', 'No sensor drift detected', NULL, NULL, true, '{"drift_value": 0.1}'),
('2025-10-05 13:10:00+07', 'alert_088', NULL, 'Gateway2', '00002', 'vpn_status', 'info', 'VPN connection active', NULL, NULL, true, '{"tunnel_uptime": "100%"}'),
('2025-10-07 16:35:00+07', 'alert_089', 'rfid_gate_01', 'Gateway1', '00001', 'access_frequency', 'info', 'Access frequency normal', NULL, NULL, true, '{"accesses_per_hour": 1.2}'),
('2025-10-09 11:25:00+07', 'alert_090', NULL, 'Gateway3', '00003', 'disk_health', 'info', 'Storage disk health good', NULL, NULL, true, '{"smart_status": "passed"}'),
('2025-10-12 14:40:00+07', 'alert_091', 'passkey_01', 'Gateway2', '00002', 'lcd_status', 'info', 'LCD display functioning normally', NULL, NULL, true, '{"backlight": 100}'),
('2025-10-15 10:55:00+07', 'alert_092', NULL, 'Gateway1', '00001', 'traffic_analysis', 'info', 'Network traffic patterns normal', NULL, NULL, true, '{"peak_mbps": 2.5}'),
('2025-10-17 15:15:00+07', 'alert_093', 'temp_01', 'Gateway3', '00003', 'humidity_trend', 'info', 'Humidity levels stable', NULL, NULL, true, '{"avg_humidity": 62}'),
('2025-10-19 09:30:00+07', 'alert_094', NULL, 'Gateway2', '00002', 'session_management', 'info', 'Active sessions within limits', NULL, NULL, true, '{"max_sessions": 10}'),
('2025-10-21 13:50:00+07', 'alert_095', 'fan_01', 'Gateway3', '00003', 'blade_speed', 'info', 'Fan blade speed consistent', NULL, NULL, true, '{"rpm": 1250}'),
('2025-10-23 16:10:00+07', 'alert_096', NULL, 'Gateway1', '00001', 'api_health', 'info', 'API endpoints responding normally', NULL, NULL, true, '{"response_time_ms": 85}'),
('2025-10-24 12:25:00+07', 'alert_097', 'rfid_gate_01', 'Gateway1', '00001', 'electromagnetic', 'info', 'No electromagnetic interference', NULL, NULL, true, '{"emi_level": "low"}'),
('2025-10-25 14:45:00+07', 'alert_098', NULL, 'Gateway3', '00003', 'task_scheduler', 'info', 'All scheduled tasks running', NULL, NULL, true, '{"tasks_executed": 48}'),
('2025-10-26 11:05:00+07', 'alert_099', 'temp_01', 'Gateway3', '00003', 'prediction_model', 'info', 'Temperature prediction accurate', NULL, NULL, true, '{"accuracy": 94}'),
('2025-10-26 12:30:00+07', 'alert_100', NULL, 'Gateway2', '00002', 'system_summary', 'info', 'Monthly system summary generated', NULL, NULL, true, '{"uptime_percent": 99.8}');

-- ============================================================================
-- DONE
-- ============================================================================
SELECT 'Schema V2 migration complete!' AS status;