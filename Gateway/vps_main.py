import serial
import time
import json
import struct
import os
from datetime import datetime, timedelta
from collections import deque
import paho.mqtt.client as mqtt
import ssl
import threading
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler

# ============= LOGGING =============
def setup_logging():
    log_dir = './logs'
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('gateway')
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'gateway.log'),
        maxBytes=10*1024*1024,
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ============= CONFIGURATION =============
CONFIG = {
    # LoRa Serial
    'lora_port': 'COM5',
    'lora_baudrate': 9600,
    
    # Local MQTT Broker (Devices connect here)
    'local_broker': {
        'host': '192.168.1.111',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './gateway_cert/ca.cert.pem',
        'username': 'Gateway1',
        'password': '125'
    },
    
    # VPS Cloud MQTT Broker (mTLS)
    'vps_broker': {
        'host': '128.199.137.3',  # ← Thay IP VPS của bạn
        'port': 8883,
        'client_id': 'Gateway1',
        'ca_cert': './gateway_cert/ca.cert.pem',
        'cert_file': './gateway_cert/gateway.cert.pem',
        'key_file': './gateway_cert/gateway.key.pem',
    },
    
    # HMAC Key
    'hmac_key': bytes([
        0x5A, 0x5A, 0x2B, 0x3F, 0x87, 0xDA, 0x01, 0xF9,
        0xDE, 0xE1, 0x83, 0xAD, 0x84, 0x54, 0xB5, 0x34,
        0x77, 0x68, 0x47, 0x8C, 0xE8, 0xFD, 0x73, 0x1F,
        0xBD, 0xE1, 0x3C, 0x42, 0x79, 0xB8, 0xFE, 0xA4
    ]),
    
    # Topics
    'topics': {
        # Local topics (devices)
        'local_telemetry': 'home/devices/+/telemetry',
        'local_request': 'home/devices/+/request',
        'local_status': 'home/devices/+/status',
        'local_command': 'home/devices/{device_id}/command',
        
        # VPS topics
        'vps_telemetry': 'gateway/telemetry/{device_id}',
        'vps_status': 'gateway/status/{device_id}',
        'vps_logs': 'gateway/logs/{device_id}',
        'vps_command': 'gateway/command/#',  # Subscribe
        'vps_gateway_status': 'gateway/status/Gateway1',
    },
    
    # Database
    'db_path': './data/',
    'devices_db': 'devices.json',
    'logs_db': 'logs.json',
    
    # Security
    'security': {
        'max_failed_attempts': 5,
        'lockout_duration_seconds': 300,
        'timestamp_tolerance_seconds': 300,
        'nonce_cache_size': 1000
    },
    
    # Buffer
    'buffer_max_size': 1000,
    'heartbeat_interval': 60,
}

# LoRa message types
MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x06: 'gate_status',
}

DEVICE_TYPES = {
    0x01: 'rfid_gate_01',
}

# ============= UTILITIES =============
def crc32(data: bytes, poly=0x04C11DB7, init=0xFFFFFFFF, xor_out=0xFFFFFFFF) -> int:
    crc = init
    for b in data:
        crc ^= (b << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFFFFFFFF
    return crc ^ xor_out

def verify_hmac(body_str, received_hmac, key):
    calculated = hmac.new(key, body_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated, received_hmac)

# ============= SECURITY MANAGER =============
class SecurityManager:
    def __init__(self, config):
        self.config = config
        self.failed_attempts = {}
        self.lockout_until = {}
        self.used_nonces = deque(maxlen=config['security']['nonce_cache_size'])
        self.lock = threading.Lock()
    
    def is_locked_out(self, device_id):
        with self.lock:
            if device_id in self.lockout_until:
                if datetime.now() < self.lockout_until[device_id]:
                    return True
                else:
                    del self.lockout_until[device_id]
                    self.failed_attempts[device_id] = 0
            return False
    
    def record_failed_attempt(self, device_id):
        with self.lock:
            self.failed_attempts[device_id] = self.failed_attempts.get(device_id, 0) + 1
            
            if self.failed_attempts[device_id] >= self.config['security']['max_failed_attempts']:
                lockout_duration = timedelta(seconds=self.config['security']['lockout_duration_seconds'])
                self.lockout_until[device_id] = datetime.now() + lockout_duration
                logger.warning(f"Device {device_id} locked out until {self.lockout_until[device_id]}")
                return True
            return False
    
    def record_success(self, device_id):
        with self.lock:
            self.failed_attempts.pop(device_id, None)
            self.lockout_until.pop(device_id, None)
    
    def validate_timestamp(self, timestamp):
        current_time = int(time.time())
        tolerance = self.config['security']['timestamp_tolerance_seconds']
        return abs(current_time - timestamp) <= tolerance
    
    def validate_nonce(self, nonce):
        with self.lock:
            if nonce in self.used_nonces:
                return False
            self.used_nonces.append(nonce)
            return True

# ============= DATABASE =============
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.load_database()
    
    def load_database(self):
        with self.lock:
            self.devices = self._load_json(CONFIG['devices_db'])
            self.settings = self._load_json('settings.json')
            self.logs = []
    
    def _load_json(self, filename):
        file_path = os.path.join(self.db_path, filename)
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
            return {}
    
    def _save_json(self, filename, data):
        file_path = os.path.join(self.db_path, filename)
        backup_path = file_path + '.backup'
        
        if os.path.exists(file_path):
            try:
                os.replace(file_path, backup_path)
            except:
                pass
        
        try:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")
            if os.path.exists(backup_path):
                os.replace(backup_path, file_path)
    
    def save_all(self):
        with self.lock:
            self._save_json(CONFIG['devices_db'], self.devices)
            self._save_json('settings.json', self.settings)
            
    def add_log(self, log_entry):
        """Add log entry to logs.json"""
        with self.lock:
            # Add timestamp if not exists
            if 'timestamp' not in log_entry:
                log_entry['timestamp'] = datetime.now().isoformat()
            
            # Add to logs list
            if not isinstance(self.logs, list):
                self.logs = []
            
            self.logs.append(log_entry)
            
            # Keep only last 1000 entries
            if len(self.logs) > 1000:
                self.logs = self.logs[-1000:]
            
            # Save to file
            self._save_json('logs.json', self.logs)
    
    def get_recent_logs(self, limit=100, log_type=None):
        """Get recent logs with optional filtering"""
        with self.lock:
            if not isinstance(self.logs, list):
                return []
            
            filtered = self.logs
            if log_type:
                filtered = [l for l in self.logs if l.get('type') == log_type]
            
            return filtered[-limit:]
    
    def authenticate_rfid(self, uid):
        try:
            with self.lock:
                card = self.devices.get('rfid_cards', {}).get(uid)
                return bool(card and card.get('active', False))
        except:
            return False
    
    def authenticate_passkey(self, password_hash):
        try:
            with self.lock:
                passwords = self.devices.get('passwords', {})
                for pwd_id, pwd_data in passwords.items():
                    if pwd_data.get('hash') == password_hash and pwd_data.get('active'):
                        return True, pwd_id
                return False, None
        except:
            return False, None
    
    def check_access_rules(self, method, user_id=None):
        try:
            with self.lock:
                rules = self.devices.get('access_rules', {})
                current_time = datetime.now().time()
                
                for rule_name, rule_config in rules.items():
                    if not rule_config.get('enabled'):
                        continue
                    
                    start = datetime.strptime(rule_config['start_time'], '%H:%M').time()
                    end = datetime.strptime(rule_config['end_time'], '%H:%M').time()
                    
                    in_range = start <= current_time <= end if start <= end else (current_time >= start or current_time <= end)
                    
                    if in_range:
                        if method not in rule_config.get('allowed_methods', []):
                            return False, f"method_not_allowed_{rule_name}"
                        if user_id in rule_config.get('restricted_users', []):
                            return False, f"user_restricted_{rule_name}"
                        return True, None
                
                return True, None
        except:
            return True, None

# ============= GATEWAY =============
class Gateway:
    def __init__(self):       
        self.db = Database(CONFIG['db_path'])
        self.security = SecurityManager(CONFIG)
        
        # MQTT clients
        self.local_mqtt = None
        self.vps_mqtt = None
        
        # LoRa
        self.serial_conn = None
        
        # State
        self.running = False
        self.seq_cnt = 0
        self.local_connected = False
        self.vps_connected = False
        
        # Buffer for offline messages
        self.buffer = deque(maxlen=CONFIG['buffer_max_size'])
        self.buffer_lock = threading.Lock()
        
        # Stats
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'messages_buffered': 0,
            'uptime_start': datetime.now()
        }
        
        # Setup
        self.setup_local_mqtt()
        self.setup_vps_mqtt()
        self.setup_serial()
    
    # ============= LOCAL MQTT =============
    def setup_local_mqtt(self):
        logger.info("Setting up Local MQTT Broker...")
        
        self.local_mqtt = mqtt.Client(client_id="Gateway1")
        
        cfg = CONFIG['local_broker']
        self.local_mqtt.username_pw_set(cfg['username'], cfg['password'])
        
        if cfg['use_tls']:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.load_verify_locations(cfg['ca_cert'])
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            self.local_mqtt.tls_set_context(context)
        
        self.local_mqtt.on_connect = self.on_local_connect
        self.local_mqtt.on_message = self.on_local_message
        self.local_mqtt.on_disconnect = self.on_local_disconnect
        
        try:
            self.local_mqtt.connect(cfg['host'], cfg['port'], 60)
            self.local_mqtt.loop_start()
        except Exception as e:
            logger.error(f"Local MQTT connection failed: {e}")
    
    def on_local_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(" Connected to Local MQTT")
            self.local_connected = True
            
            client.subscribe(CONFIG['topics']['local_telemetry'], qos=1)
            client.subscribe(CONFIG['topics']['local_request'], qos=1)
            client.subscribe(CONFIG['topics']['local_status'], qos=1)
            
            logger.info(" Subscribed to local topics")
        else:
            logger.error(f" Local MQTT failed: {rc}")
            self.local_connected = False
    
    def on_local_disconnect(self, client, userdata, rc):
        logger.warning(f" Local MQTT disconnected (rc={rc})")
        self.local_connected = False
    
    def on_local_message(self, client, userdata, msg):
        try:
            self.stats['messages_received'] += 1
            
            # Parse topic: home/devices/{device_id}/telemetry
            parts = msg.topic.split('/')
            device_id = parts[2] if len(parts) >= 3 else 'unknown'
            
            try:
                payload = json.loads(msg.payload.decode())
            except:
                payload = {'raw': msg.payload.decode()}
            
            logger.info(f" Local: {device_id} → {msg.topic}")
            
            # Route to handler
            if 'telemetry' in msg.topic:
                self.handle_telemetry(device_id, payload)
            elif 'request' in msg.topic:
                self.handle_request(device_id, payload)
            elif 'status' in msg.topic:
                self.handle_status(device_id, payload)
        
        except Exception as e:
            logger.error(f"Error handling local message: {e}", exc_info=True)
    
    # ============= VPS MQTT (mTLS) =============
    def setup_vps_mqtt(self):
        logger.info("Setting up VPS MQTT...")
        
        cfg = CONFIG['vps_broker']
        self.vps_mqtt = mqtt.Client(client_id=cfg['client_id'])
        
        # mTLS setup
        try:
            self.vps_mqtt.tls_set(
                ca_certs=cfg['ca_cert'],
                certfile=cfg['cert_file'],
                keyfile=cfg['key_file'],
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
            self.vps_mqtt.tls_insecure_set(False)
            logger.info(" VPS mTLS configured")
        except Exception as e:
            logger.error(f"VPS TLS setup error: {e}")
            return
        
        self.vps_mqtt.on_connect = self.on_vps_connect
        self.vps_mqtt.on_message = self.on_vps_message
        self.vps_mqtt.on_disconnect = self.on_vps_disconnect
        
        try:
            self.vps_mqtt.connect(cfg['host'], cfg['port'], 60)
            self.vps_mqtt.loop_start()
        except Exception as e:
            logger.error(f"VPS MQTT connection failed: {e}")
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(" Connected to VPS MQTT")
            self.vps_connected = True
            
            # Subscribe to commands
            client.subscribe(CONFIG['topics']['vps_command'], qos=1)
            logger.info(" Subscribed to VPS commands")
            
            # Send gateway online status
            self.send_gateway_status('online')
            
            # Flush buffered messages
            self.flush_buffer()
        else:
            logger.error(f" VPS MQTT failed: {rc}")
            self.vps_connected = False
    
    def on_vps_disconnect(self, client, userdata, rc):
        logger.warning(f" VPS MQTT disconnected (rc={rc})")
        self.vps_connected = False
    
    def on_vps_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            
            # Extract device_id from topic: gateway/command/{device_id}
            parts = msg.topic.split('/')
            device_id = parts[2] if len(parts) >= 3 else None
            
            logger.info(f" VPS Command: {msg.topic}")
            
            if device_id:
                self.forward_command_to_device(device_id, payload)
        
        except Exception as e:
            logger.error(f"Error handling VPS message: {e}")
    
    # ============= SERIAL/LORA =============
    def setup_serial(self):
        try:
            self.serial_conn = serial.Serial(
                CONFIG['lora_port'],
                CONFIG['lora_baudrate'],
                timeout=1
            )
            logger.info(f" LoRa connected on {CONFIG['lora_port']}")
        except Exception as e:
            logger.error(f"LoRa connection failed: {e}")
            self.serial_conn = None
    
    # ============= MESSAGE HANDLERS =============
    def handle_telemetry(self, device_id, payload):
        logger.debug(f"Telemetry: {device_id}")
        
        # Auto fan control
        if device_id == 'temp_01' and 'temperature' in payload.get('data', {}):
            temp = payload['data']['temperature']
            
            if temp > 28:
                self.db.add_log({
                    'type': 'alert',
                    'event': 'high_temperature',
                    'device_id': device_id,
                    'temperature': temp,
                    'timestamp': datetime.now().isoformat()
                })
            
            self.auto_fan_control(temp)
        
        # Forward to VPS
        self.forward_to_vps(device_id, 'telemetry', payload)
    
    def handle_request(self, device_id, payload):
        logger.info(f"Request: {device_id}")
        
        # Security checks
        if self.security.is_locked_out(device_id):
            self.db.add_log({
                'type': 'security_alert',
                'event': 'device_locked_out',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'locked_out'})
            return
        
        if 'hmac' not in payload or 'body' not in payload:
            self.db.add_log({
                'type': 'security_alert',
                'event': 'invalid_request_format',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_format'})
            return
        
        # Verify HMAC
        if not verify_hmac(payload['body'], payload['hmac'], CONFIG['hmac_key']):
            self.db.add_log({
                'type': 'security_alert',
                'event': 'hmac_verification_failed',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_signature'})
            
            # Log security event to VPS
            self.forward_to_vps(device_id, 'logs', {
                'type': 'security_alert',
                'event': 'hmac_verification_failed',
                'timestamp': datetime.now().isoformat()
            })
            return
        
        # Parse body
        try:
            body = json.loads(payload['body'])
        except:
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_json'})
            return
        
        # Validate timestamp & nonce
        if not self.security.validate_timestamp(body.get('ts', 0)):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_timestamp'})
            return
        
        if not self.security.validate_nonce(body.get('nonce', 0)):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'replay_attack'})
            return
        
        # Process command
        if body.get('cmd') == 'unlock_request':
            self.handle_passkey_request(device_id, body)
    
    def handle_passkey_request(self, device_id, body):
        password_hash = body.get('pw')
        
        # Authenticate
        is_valid, pwd_id = self.db.authenticate_passkey(password_hash)
        
        # Check access rules
        access_allowed, deny_reason = True, 'invalid_password'
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('passkey', pwd_id)
        
        # Log to VPS
        log_entry = {
            'type': 'access_attempt',
            'method': 'passkey',
            'device_id': device_id,
            'result': 'granted' if (is_valid and access_allowed) else 'denied',
            'timestamp': datetime.now().isoformat()
        }
        if is_valid and pwd_id:
            log_entry['password_id'] = pwd_id
        if not access_allowed:
            log_entry['deny_reason'] = deny_reason
            
        self.db.add_log(log_entry)
        
        self.forward_to_vps(device_id, 'logs', log_entry)
        
        # Grant or deny
        if is_valid and access_allowed:
            logger.info(f" Access granted: {pwd_id}")
            self.security.record_success(device_id)
            self.send_local_response(device_id, {'cmd': 'OPEN'})
            
            self.db.settings['last_access'] = {
                'method': 'passkey',
                'password_id': pwd_id,
                'timestamp': datetime.now().isoformat()
            }
            self.db.save_all()
        else:
            logger.warning(f" Access denied: {deny_reason}")
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': deny_reason})
    
    def handle_status(self, device_id, payload):
        logger.debug(f"Status: {device_id}")
        
        if device_id == 'fan_01':
            state = payload.get('state', 'unknown')
            auto_mode = payload.get('auto_mode')
            
            self.db.add_log({
                'type': 'device_status',
                'device_id': device_id,
                'state': state,
                'auto_mode': auto_mode,
                'trigger': payload.get('trigger', 'unknown'),
                'timestamp': datetime.now().isoformat()
            })
        
    # ← THÊM LOG CHO TEMP STATUS
        elif device_id == 'temp_01':
            device_state = payload.get('state', 'unknown')
            
            if device_state == 'error':
                self.db.add_log({
                    'type': 'device_status',
                    'device_id': device_id,
                    'state': device_state,
                    'error': payload.get('error'),
                    'timestamp': datetime.now().isoformat()
                })
        
        # ← THÊM LOG CHO PASSKEY STATUS
        elif device_id == 'passkey_01':
            device_state = payload.get('state', 'unknown')
            
            self.db.add_log({
                'type': 'device_status',
                'device_id': device_id,
                'state': device_state,
                'timestamp': datetime.now().isoformat()
            })
            
        self.forward_to_vps(device_id, 'status', payload)
    
    # ============= AUTO CONTROL =============
    def auto_fan_control(self, temperature):
        threshold = self.db.settings.get('automation', {}).get('auto_fan_temp_threshold', 28)
        auto_enabled = self.db.settings.get('automation', {}).get('auto_fan_enabled', True)
        
        if auto_enabled:
            should_be_on = (temperature >= threshold)
            command = {'cmd': 'fan_on' if should_be_on else 'fan_off'}
            self.send_local_command('fan_01', command)
            logger.info(f" Auto fan: {temperature}°C → {'ON' if should_be_on else 'OFF'}")
    
    # ============= VPS FORWARDING =============
    def forward_to_vps(self, device_id, msg_type, payload):
        """Forward message to VPS"""
        
        # Build VPS payload
        vps_payload = {
            'gateway_id': 'Gateway1',
            'device_id': device_id,
            'data': payload,
            'timestamp': datetime.now().isoformat()
        }
        
        # Determine topic
        topic = CONFIG['topics'][f'vps_{msg_type}'].format(device_id=device_id)
        
        # Send or buffer
        if self.vps_connected:
            self.publish_to_vps(topic, vps_payload)
        else:
            with self.buffer_lock:
                self.buffer.append({'topic': topic, 'payload': vps_payload})
                self.stats['messages_buffered'] += 1
                logger.debug(f" Buffered (total: {len(self.buffer)})")
    
    def publish_to_vps(self, topic, payload):
        """Publish to VPS MQTT"""
        try:
            result = self.vps_mqtt.publish(topic, json.dumps(payload), qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['messages_sent'] += 1
                logger.debug(f" Sent to VPS: {topic}")
            else:
                logger.error(f"Failed to publish to VPS: {result.rc}")
        except Exception as e:
            logger.error(f"Error publishing to VPS: {e}")
    
    def flush_buffer(self):
        """Flush buffered messages when VPS reconnects"""
        with self.buffer_lock:
            if not self.buffer:
                return
            
            logger.info(f" Flushing {len(self.buffer)} buffered messages")
            
            while self.buffer:
                try:
                    msg = self.buffer.popleft()
                    msg['payload']['_flushed'] = True
                    self.publish_to_vps(msg['topic'], msg['payload'])
                    time.sleep(0.05)  # Throttle
                except Exception as e:
                    logger.error(f"Error flushing: {e}")
                    break
    
    def send_gateway_status(self, status):
        """Send gateway status to VPS"""
        if not self.vps_connected:
            return
        
        uptime = (datetime.now() - self.stats['uptime_start']).total_seconds()
        
        status_payload = {
            'gateway_id': 'Gateway1',
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': int(uptime),
            'stats': {
                'messages_received': self.stats['messages_received'],
                'messages_sent': self.stats['messages_sent'],
                'messages_buffered': len(self.buffer),
                'local_connected': self.local_connected,
                'vps_connected': self.vps_connected
            }
        }
        
        self.vps_mqtt.publish(
            CONFIG['topics']['vps_gateway_status'],
            json.dumps(status_payload),
            qos=1,
            retain=True
        )
    
    # ============= LOCAL COMMUNICATION =============
    def send_local_response(self, device_id, response):
        """Send response to local device"""
        topic = CONFIG['topics']['local_command'].format(device_id=device_id)
        
        if self.local_mqtt and self.local_connected:
            self.local_mqtt.publish(topic, json.dumps(response), qos=1)
            logger.debug(f" Response to {device_id}: {response}")
    
    def send_local_command(self, device_id, command):
        """Send command to local device"""
        self.send_local_response(device_id, command)
    
    def forward_command_to_device(self, device_id, command):
        """Forward command from VPS to local device"""
        logger.info(f" Forwarding VPS command to {device_id}")
        self.send_local_command(device_id, command)
    
    # ============= LORA HANDLING =============
    def parse_lora_message(self, data):
        """Parse LoRa message (same as before)"""
        try:
            if len(data) < 3 or data[:3] != b'\x00\x02\x17':
                return None
            
            raw = data[3:]
            if len(raw) < 9:
                return None
            
            header_byte0 = raw[0]
            version = header_byte0 & 0x0F
            msg_type_n = (header_byte0 >> 4) & 0x0F
            
            device_byte1 = raw[1]
            device_type_n = device_byte1 & 0x0F
            
            seq = struct.unpack('<H', raw[2:4])[0]
            timestamp = struct.unpack('<I', raw[4:8])[0]
            
            uid_len = raw[8]
            expected_len = 9 + uid_len + 4
            
            if len(raw) < expected_len:
                return None
            
            payload_data = raw[9:9 + uid_len]
            crc_received = struct.unpack('<I', raw[9 + uid_len:9 + uid_len + 4])[0]
            
            crc_data = raw[:9 + uid_len]
            auth_crc = crc32(crc_data)
            
            if auth_crc != crc_received:
                logger.error("LoRa CRC check failed")
                return None
            
            msg_type_str = MESSAGE_TYPES.get(msg_type_n, 'unknown')
            device_type_str = DEVICE_TYPES.get(device_type_n, 'unknown')
            
            # Parse payload based on type
            if msg_type_n == 0x01:  # RFID
                payload = {
                    'uid': ''.join(f'{b:02x}' for b in payload_data),
                    'uid_len': len(payload_data)
                }
            elif msg_type_n == 0x06:  # Gate status
                payload = {'status': payload_data.decode('utf-8')}
            else:
                payload = {'raw': payload_data.hex()}
            
            return {
                'header': {
                    'version': version,
                    'msg_type': msg_type_str,
                    'msg_type_n': msg_type_n,
                    'device_type': device_type_str,
                    'device_type_raw': device_type_n,
                    'seq': seq,
                    'timestamp': timestamp
                },
                'payload': payload,
                'crc': hex(crc_received)
            }
        
        except Exception as e:
            logger.error(f"Error parsing LoRa message: {e}")
            return None
    
    def process_lora_message(self, message):
        """Process LoRa message and send response"""
        msg_type = message['header']['msg_type']
        
        if msg_type == 'rfid_scan':
            return self.handle_rfid_scan(message)
        elif msg_type == 'gate_status':
            return self.handle_gate_status(message)
        
        return None
    
    def handle_rfid_scan(self, message):
        """Handle RFID scan from LoRa"""
        uid = message['payload'].get('uid')
        
        # Authenticate
        is_valid = self.db.authenticate_rfid(uid)
        
        # Check access rules
        access_allowed, deny_reason = True, 'invalid_card'
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('rfid', uid)
        
        result = 'granted' if (is_valid and access_allowed) else 'denied'
        
        logger.info(f"RFID {uid}: {result.upper()}")
        
        # Log to VPS
        log_entry = {
        'type': 'access_attempt',
        'method': 'rfid',
        'uid': uid,
        'result': result,
        'device': message['header']['device_type'],
        'timestamp': datetime.now().isoformat(),
        'deny_reason': deny_reason if not (is_valid and access_allowed) else None
        }
        
        self.db.add_log(log_entry)
        
        self.forward_to_vps('rfid_gate_01', 'logs', log_entry)
        
        # Send LoRa response
        return 'GRANT' if (is_valid and access_allowed) else 'DENY5'
    
    def handle_gate_status(self, message):
        """Handle gate status update"""
        status = message['payload'].get('status')
        
        self.db.add_log({
            'type': 'door_status',
            'status': status,
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat(),
            'sequence': message['header']['seq']
        })
        
        self.forward_to_vps('rfid_gate_01', 'status', {
            'type': 'gate_status',
            'status': status,
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat()
        })
        
        return None
    
    def send_lora_response(self, device_type_numeric, response_text):
        """Send response via LoRa"""
        if not self.serial_conn:
            return False
        
        try:
            response_data = response_text.encode('utf-8')
            head = b'\xC0\x00\x00'
            addr = struct.pack('>H', int(device_type_numeric) & 0xFFFF)
            chan = bytes([23])
            length = bytes([len(response_data)])
            packet = head + addr + chan + length + response_data
            
            self.serial_conn.write(packet)
            logger.info(f"LoRa >> {response_text}")
            return True
        except Exception as e:
            logger.error(f"LoRa send error: {e}")
            return False
    
    # ============= MAIN LOOP =============
    def run(self):
        """Main gateway loop"""
        self.running = True
        
        self.db.add_log({
            'type': 'system_event',
            'event': 'gateway_started',
            'timestamp': datetime.now().isoformat()
        })
        
        logger.info("=" * 60)
        logger.info("Gateway started successfully")
        logger.info(f"Local MQTT: {CONFIG['local_broker']['host']}:{CONFIG['local_broker']['port']}")
        logger.info(f"VPS MQTT: {CONFIG['vps_broker']['host']}:{CONFIG['vps_broker']['port']}")
        logger.info("=" * 60)
        
        # Start heartbeat thread
        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        
        # LoRa buffer
        lora_buffer = b''
        last_heartbeat = time.time()
        
        while self.running:
            try:
                # Periodic heartbeat
                if time.time() - last_heartbeat > CONFIG['heartbeat_interval']:
                    if self.vps_connected:
                        self.send_gateway_status('online')
                    last_heartbeat = time.time()
                    
                    # Log stats
                    logger.info(
                        f"Stats - RX: {self.stats['messages_received']}, "
                        f"TX: {self.stats['messages_sent']}, "
                        f"Buffered: {len(self.buffer)}, "
                        f"Local: {'OK' if self.local_connected else 'False'}, "
                        f"VPS: {'OK' if self.vps_connected else 'OK'}"
                    )
                
                # Handle LoRa messages
                if self.serial_conn and self.serial_conn.in_waiting > 0:                  
                    new_data = self.serial_conn.read(self.serial_conn.in_waiting)
                    lora_buffer += new_data
                    
                    print(lora_buffer)
                    
                    # Process complete messages
                    while True:
                        header_idx = lora_buffer.find(b'\x00\x02\x17')
                        if header_idx == -1:
                            break
                        
                        raw = lora_buffer[header_idx + 3:]
                        if len(raw) < 9:
                            break
                        
                        uid_len = raw[8]
                        msg_len = 9 + uid_len + 4
                        
                        if len(raw) < msg_len:
                            break
                        
                        candidate = lora_buffer[header_idx:header_idx + 3 + msg_len]
                        message = self.parse_lora_message(candidate)
                        
                        if message:
                            response = self.process_lora_message(message)
                            if response:
                                device_numeric = message['header'].get('device_type_raw', 1)
                                self.send_lora_response(device_numeric, response)
                        
                        lora_buffer = lora_buffer[header_idx + 3 + msg_len:]
                
                time.sleep(0.1)
            
            except KeyboardInterrupt:
                logger.info("\nShutting down...")
                self.running = False
            
            except Exception as e:
                logger.error(f"Gateway error: {e}", exc_info=True)
                time.sleep(1)
        
        # Cleanup
        self.cleanup()
    
    def heartbeat_loop(self):
        """Send periodic heartbeat to VPS"""
        while self.running:
            try:
                time.sleep(CONFIG['heartbeat_interval'])
                if self.vps_connected:
                    self.send_gateway_status('online')
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    def cleanup(self):
        """Cleanup connections"""
        logger.info("Cleaning up connections...")
        
        # Send offline status
        if self.vps_connected:
            self.send_gateway_status('offline')
        
        # Stop MQTT loops
        if self.local_mqtt:
            self.local_mqtt.loop_stop()
            self.local_mqtt.disconnect()
        
        if self.vps_mqtt:
            self.vps_mqtt.loop_stop()
            self.vps_mqtt.disconnect()
        
        # Close serial
        if self.serial_conn:
            self.serial_conn.close()
        
        logger.info("Gateway stopped")


# ============= MAIN =============
def main():
    """Entry point"""
    
    # Check certificates
    local_cert = CONFIG['local_broker']['ca_cert']
    vps_ca = CONFIG['vps_broker']['ca_cert']
    vps_cert = CONFIG['vps_broker']['cert_file']
    vps_key = CONFIG['vps_broker']['key_file']
    
    missing_certs = []
    
    if not os.path.exists(local_cert):
        missing_certs.append(f"Local CA: {local_cert}")
    
    if not os.path.exists(vps_ca):
        missing_certs.append(f"VPS CA: {vps_ca}")
    
    if not os.path.exists(vps_cert):
        missing_certs.append(f"VPS Cert: {vps_cert}")
    
    if not os.path.exists(vps_key):
        missing_certs.append(f"VPS Key: {vps_key}")
    
    if missing_certs:
        logger.error("=" * 60)
        logger.error("Missing certificates:")
        for cert in missing_certs:
            logger.error(f"   - {cert}")
        logger.error("")
        logger.error("Please follow setup guide to create/download certificates")
        logger.error("=" * 60)
        return
    
    # Check database
    if not os.path.exists(os.path.join(CONFIG['db_path'], CONFIG['devices_db'])):
        logger.error(f"Database not found: {CONFIG['db_path']}{CONFIG['devices_db']}")
        return
    
    # Create and start gateway
    gateway = Gateway()
    
    try:
        gateway.run()
    except Exception as e:
        logger.critical(f"Gateway startup failed: {e}", exc_info=True)
    finally:
        gateway.cleanup()


if __name__ == "__main__":
    main()