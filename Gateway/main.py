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

# Configure logging
def setup_logging():
    log_dir = './logs'
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('gateway')
    logger.setLevel(logging.INFO)
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'gateway.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

CONFIG = {
    'lora_port': 'COM5',
    'lora_baudrate': 9600,
    'broker_mqtt': {
        'host': '192.168.1.148',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './gateway_cert/ca.cert.pem',
        # 'gateway_cert': './gateway_cert/gateway.cert.pem',
        # 'gateway_key': './gateway_cert/gateway.key.pem',
        # 'client_cert_required': True
        
        'username': 'Gateway1',
        'password': '125'
    },
    'aws_mqtt': {
        'broker': 'a1abhlypowx6d1-ats.iot.ap-southeast-2.amazonaws.com',
        'port': 8883,
        'client_id': 'Gateway1',
        'ca_cert': './aws_cert/AmazonRootCA1.pem',
        'cert_file': './aws_cert/certificate.pem.crt',
        'key_file': './aws_cert/private.pem.key',
    },
    'hmac_key': bytes([
        0x5A, 0x5A, 0x2B, 0x3F, 0x87, 0xDA, 0x01, 0xF9,
        0xDE, 0xE1, 0x83, 0xAD, 0x84, 0x54, 0xB5, 0x34,
        0x77, 0x68, 0x47, 0x8C, 0xE8, 0xFD, 0x73, 0x1F,
        0xBD, 0xE1, 0x3C, 0x42, 0x79, 0xB8, 0xFE, 0xA4
    ]),
    'topics': {
        'device_telemetry': 'home/devices/+/telemetry',
        'device_request': 'home/devices/+/request',
        'device_status': 'home/devices/+/status',
        'device_command': 'home/devices/{device_id}/command',
        'device_response': 'home/devices/{device_id}/response',
        'aws_sensor_data': 'aws/sensor/data',
        'aws_device_control': 'aws/device/control',
        'aws_system_logs': 'aws/system/logs',
    },
    'db_path': './data/',
    'devices_db': 'devices.json',
    'logs_db': 'logs.json',
    'security': {
        'max_failed_attempts': 5,
        'lockout_duration_seconds': 300,  # 5 minutes
        'timestamp_tolerance_seconds': 300,  # 5 minutes
        'nonce_cache_size': 1000
    }
}

MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x02: 'temp_update',
    0x03: 'motion_detect',
    0x04: 'relay_control',
    0x05: 'passkey',
    0x06: 'gate_status',
    0x08: 'door_status',
    0x07: 'system_status',
    0x80: 'ack',
    0xFF: 'error'
}

DEVICE_TYPES = {
    0x01: 'rfid_gate',
    0x02: 'relay_fan',
    0x03: 'temp_DH11',
    0x04: 'gateway',
    0x05: 'passkey',
    0x07: 'motion_outdoor',
    0x08: 'motion_indoor'
}

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
    """Verify HMAC-SHA256 signature using full hash"""
    calculated = hmac.new(key, body_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated, received_hmac)

def calculate_hmac(data_str, key):
    """Calculate HMAC-SHA256 signature"""
    return hmac.new(key, data_str.encode(), hashlib.sha256).hexdigest()


class SecurityManager:
    """Manages security features including rate limiting, nonce validation, and lockouts"""
    
    def __init__(self, config):
        self.config = config
        self.failed_attempts = {}  # device_id -> count
        self.lockout_until = {}    # device_id -> datetime
        self.used_nonces = deque(maxlen=config['security']['nonce_cache_size'])
        self.lock = threading.Lock()
    
    def is_locked_out(self, device_id):
        """Check if device is currently locked out"""
        with self.lock:
            if device_id in self.lockout_until:
                if datetime.now() < self.lockout_until[device_id]:
                    return True
                else:
                    # Lockout expired
                    del self.lockout_until[device_id]
                    self.failed_attempts[device_id] = 0
            return False
    
    def record_failed_attempt(self, device_id):
        """Record a failed authentication attempt"""
        with self.lock:
            self.failed_attempts[device_id] = self.failed_attempts.get(device_id, 0) + 1
            
            if self.failed_attempts[device_id] >= self.config['security']['max_failed_attempts']:
                lockout_duration = timedelta(seconds=self.config['security']['lockout_duration_seconds'])
                self.lockout_until[device_id] = datetime.now() + lockout_duration
                
                logger.warning(f"Device {device_id} locked out until {self.lockout_until[device_id]}")
                return True
            return False
    
    def record_successful_attempt(self, device_id):
        """Clear failed attempts on successful authentication"""
        with self.lock:
            if device_id in self.failed_attempts:
                del self.failed_attempts[device_id]
            if device_id in self.lockout_until:
                del self.lockout_until[device_id]
    
    def validate_timestamp(self, timestamp):
        """Validate timestamp is within acceptable range"""
        current_time = int(time.time())
        tolerance = self.config['security']['timestamp_tolerance_seconds']
        
        if abs(current_time - timestamp) > tolerance:
            logger.warning(f"Timestamp validation failed: {timestamp} vs {current_time}")
            return False
        return True
    
    def validate_nonce(self, nonce):
        """Check if nonce has been used before (replay attack prevention)"""
        with self.lock:
            if nonce in self.used_nonces:
                logger.warning(f"Replay attack detected: nonce {nonce} already used")
                return False
            self.used_nonces.append(nonce)
            return True


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
        
    def _load_json(self, filename, default=None):
        file_path = os.path.join(self.db_path, filename)
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
            return default or {}
    
    def _save_json(self, filename, data):
        file_path = os.path.join(self.db_path, filename)
        # Create backup before saving
        backup_path = file_path + '.backup'
        if os.path.exists(file_path):
            try:
                os.replace(file_path, backup_path)
            except Exception as e:
                logger.error(f"Error creating backup: {e}")
        
        try:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")
            # Restore from backup
            if os.path.exists(backup_path):
                os.replace(backup_path, file_path)
    
    def save_all(self):
        with self.lock:
            self._save_json(CONFIG['devices_db'], self.devices)
            self._save_json('settings.json', self.settings)
    
    def authenticate_rfid(self, uid):
        try:
            with self.lock:
                card = self.devices.get('rfid_cards', {}).get(uid)
                return bool(card and card.get('active', False))
        except Exception as e:
            logger.error(f"Error authenticating RFID: {e}")
            return False
    
    def authenticate_passkey(self, password_hash):
        """Authenticate using FULL hash (not truncated)"""
        try:
            with self.lock:
                stored_passwords = self.devices.get('passwords', {})
                
                if not stored_passwords:
                    logger.error("No passwords in database")
                    return False, None
                
                for pwd_id, pwd_data in stored_passwords.items():
                    stored_hash = pwd_data.get('hash')
                    is_active = pwd_data.get('active', False)
                    
                    if stored_hash == password_hash and is_active:
                        logger.info(f"Password authenticated: {pwd_id}")
                        return True, pwd_id
                
                logger.warning("No matching password found")
                return False, None
                
        except Exception as e:
            logger.error(f"Error authenticating passkey: {e}")
            return False, None
    
    def check_access_rules(self, method, user_id=None):
        """Check if access is allowed based on time-based rules"""
        try:
            with self.lock:
                rules = self.devices.get('access_rules', {})
                current_time = datetime.now().time()
                
                # Determine which rule applies
                for rule_name, rule_config in rules.items():
                    if not rule_config.get('enabled', False):
                        continue
                    
                    start_time = datetime.strptime(rule_config['start_time'], '%H:%M').time()
                    end_time = datetime.strptime(rule_config['end_time'], '%H:%M').time()
                    
                    # Check if current time is in this rule's range
                    in_range = False
                    if start_time <= end_time:
                        in_range = start_time <= current_time <= end_time
                    else:  # Rule spans midnight
                        in_range = current_time >= start_time or current_time <= end_time
                    
                    if in_range:
                        # Check if method is allowed
                        if method not in rule_config.get('allowed_methods', []):
                            logger.warning(f"Method {method} not allowed during {rule_name}")
                            return False, f"access_denied_{rule_name}"
                        
                        # Check if user is restricted
                        if user_id and user_id in rule_config.get('restricted_users', []):
                            logger.warning(f"User {user_id} restricted during {rule_name}")
                            return False, f"user_restricted_{rule_name}"
                        
                        return True, None
                
                # No rule matched, default allow
                return True, None
                
        except Exception as e:
            logger.error(f"Error checking access rules: {e}")
            return True, None  # Fail open


class Gateway:
    def __init__(self):
        self.db = Database(CONFIG['db_path'])
        self.security = SecurityManager(CONFIG)
        
        self.broker_mqtt = None
        self.aws_mqtt = None
        self.serial_conn = None
        
        self.running = False
        self.seq_cnt = 0
        
        # Connection retry settings
        self.mqtt_retry_delay = 5
        self.max_mqtt_retries = 3
        
        self.setup_local_broker()
        self.setup_aws_mqtt()
        self.setup_serial()
    
    def setup_local_broker(self):
        """Setup local MQTT broker with proper error handling"""
        retries = 0
        while retries < self.max_mqtt_retries:
            try:
                self.broker_mqtt = mqtt.Client(client_id="Gateway1")
                
                self.broker_mqtt.username_pw_set(
                    CONFIG['broker_mqtt']['username'],
                    CONFIG['broker_mqtt']['password']
                )
                
                context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                context.load_verify_locations(CONFIG['broker_mqtt']['ca_cert'])
                # context.load_cert_chain(
                #     CONFIG['broker_mqtt']['gateway_cert'],
                #     CONFIG['broker_mqtt']['gateway_key']
                # )
                context.check_hostname = False
                context.verify_mode = ssl.CERT_REQUIRED
                
                self.broker_mqtt.tls_set_context(context)
                self.broker_mqtt.on_connect = self.on_broker_connect
                self.broker_mqtt.on_message = self.on_broker_message
                self.broker_mqtt.on_disconnect = self.on_broker_disconnect
                
                self.broker_mqtt.connect(
                    CONFIG['broker_mqtt']['host'],
                    CONFIG['broker_mqtt']['port']
                )
                self.broker_mqtt.loop_start()
                
                logger.info("Local MQTT broker connected successfully")
                return
                
            except Exception as e:
                retries += 1
                logger.error(f"Error setting up local broker (attempt {retries}): {e}")
                if retries < self.max_mqtt_retries:
                    time.sleep(self.mqtt_retry_delay)
                else:
                    logger.critical("Failed to connect to local MQTT broker after all retries")
    
    def on_broker_disconnect(self, client, userdata, rc):
        """Handle local broker disconnection"""
        if rc != 0:
            logger.warning(f"Local MQTT disconnected unexpectedly (rc={rc}), reconnecting...")
            time.sleep(self.mqtt_retry_delay)
    
    def setup_aws_mqtt(self):
        """Setup AWS IoT MQTT with retry logic"""
        retries = 0
        while retries < self.max_mqtt_retries:
            try:
                self.aws_mqtt = mqtt.Client(client_id=CONFIG['aws_mqtt']['client_id'])
                
                context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                context.load_verify_locations(CONFIG['aws_mqtt']['ca_cert'])
                context.load_cert_chain(
                    CONFIG['aws_mqtt']['cert_file'],
                    CONFIG['aws_mqtt']['key_file']
                )
                self.aws_mqtt.tls_set_context(context)
                
                self.aws_mqtt.on_connect = self.on_aws_connect
                self.aws_mqtt.on_message = self.on_aws_message
                self.aws_mqtt.on_disconnect = self.on_aws_disconnect
                
                self.aws_mqtt.connect(
                    CONFIG['aws_mqtt']['broker'],
                    CONFIG['aws_mqtt']['port'],
                    60
                )
                self.aws_mqtt.loop_start()
                
                logger.info("AWS MQTT connected successfully")
                return
                
            except Exception as e:
                retries += 1
                logger.error(f"Error setting up AWS MQTT (attempt {retries}): {e}")
                if retries < self.max_mqtt_retries:
                    time.sleep(self.mqtt_retry_delay)
                else:
                    logger.critical("Failed to connect to AWS MQTT after all retries")
    
    def on_aws_disconnect(self, client, userdata, rc):
        """Handle AWS disconnection"""
        if rc != 0:
            logger.warning(f"AWS MQTT disconnected unexpectedly (rc={rc}), reconnecting...")
            time.sleep(self.mqtt_retry_delay)
    
    def setup_serial(self):
        """Setup LoRa serial with error handling"""
        try:
            self.serial_conn = serial.Serial(
                CONFIG['lora_port'],
                CONFIG['lora_baudrate'],
                timeout=1
            )
            logger.info(f"LoRa connected on {CONFIG['lora_port']}")
        except Exception as e:
            logger.error(f"LoRa connection failed: {e}")
            self.serial_conn = None
    
    def on_broker_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to local MQTT broker")
            client.subscribe(CONFIG['topics']['device_telemetry'], qos=1)
            client.subscribe(CONFIG['topics']['device_request'], qos=1)
            client.subscribe(CONFIG['topics']['device_status'], qos=1)
        else:
            logger.error(f"Local MQTT connection failed: {rc}")
    
    def on_broker_message(self, client, userdata, msg):
        """Handle messages from local sensors with error handling"""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            
            logger.debug(f"Local MQTT << {topic}: {payload}")
            
            parts = topic.split('/')
            device_id = parts[2] if len(parts) >= 3 else 'unknown'
            
            if 'telemetry' in topic:
                self.handle_telemetry(device_id, payload)
            elif 'request' in topic:
                self.handle_request(device_id, payload)
            elif 'status' in topic:
                self.handle_status(device_id, payload)
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
        except Exception as e:
            logger.error(f"Error handling local message: {e}", exc_info=True)
    
    def on_aws_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to AWS IoT")
            client.subscribe(CONFIG['topics']['aws_device_control'])
        else:
            logger.error(f"AWS MQTT connection failed: {rc}")
    
    def on_aws_message(self, client, userdata, msg):
        """Handle commands from AWS"""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            
            logger.debug(f"AWS >> {topic}: {payload}")
            
            if topic == CONFIG['topics']['aws_device_control']:
                self.handle_aws_command(payload)
                
        except Exception as e:
            logger.error(f"Error handling AWS message: {e}", exc_info=True)
    
    def handle_request(self, device_id, payload):
        """Enhanced request handler with full security validation"""
        logger.info(f"Request from {device_id}: {payload}")
        
        # Check if device is locked out
        if self.security.is_locked_out(device_id):
            logger.warning(f"Device {device_id} is locked out")
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'device_locked_out'
            })
            return
        
        # Verify HMAC structure
        if 'hmac' not in payload or 'body' not in payload:
            logger.error("Missing HMAC or body in request")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_message_format'
            })
            return
        
        body_str = payload['body']
        received_hmac = payload['hmac']
        
        # Verify HMAC signature
        if not verify_hmac(body_str, received_hmac, CONFIG['hmac_key']):
            logger.error(f"HMAC verification failed for {device_id}")
            self.security.record_failed_attempt(device_id)
            
            self.publish_to_aws(CONFIG['topics']['aws_system_logs'], {
                'type': 'security_alert',
                'event': 'hmac_verification_failed',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_signature'
            })
            return
        
        # Parse body
        try:
            body = json.loads(body_str)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in body")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_json'
            })
            return
        
        # Validate timestamp
        timestamp = body.get('ts')
        if timestamp and not self.security.validate_timestamp(timestamp):
            logger.warning(f"Invalid timestamp from {device_id}")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_timestamp'
            })
            return
        
        # Validate nonce
        nonce = body.get('nonce')
        if nonce and not self.security.validate_nonce(nonce):
            logger.warning(f"Replay attack detected from {device_id}")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'replay_attack'
            })
            return
        
        # Process command
        cmd = body.get('cmd')
        if cmd == 'unlock_request':
            self.handle_passkey_request(device_id, body)
        else:
            logger.warning(f"Unknown command: {cmd}")
    
    def handle_passkey_request(self, device_id, body):
        """Enhanced passkey authentication with access rules"""
        password_hash = body.get('pw')
        client_id = body.get('client_id')
        
        if not password_hash:
            logger.error("No password provided")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'no_password'
            })
            return
        
        # Authenticate password
        is_valid, pwd_id = self.db.authenticate_passkey(password_hash)
        
        # Check access rules if authenticated
        access_allowed = True
        deny_reason = 'invalid_password'
        
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('passkey', pwd_id)
        
        # Log attempt
        log_entry = {
            'type': 'access_attempt',
            'method': 'passkey',
            'device_id': device_id,
            'client_id': client_id,
            'result': 'granted' if (is_valid and access_allowed) else 'denied',
            'timestamp': datetime.now().isoformat()
        }
        
        if is_valid and pwd_id:
            log_entry['password_id'] = pwd_id
        if not access_allowed:
            log_entry['deny_reason'] = deny_reason
        
        self.publish_to_aws(CONFIG['topics']['aws_system_logs'], log_entry)
        
        # Grant or deny access
        if is_valid and access_allowed:
            logger.info(f"Access granted for password ID: {pwd_id}")
            self.security.record_successful_attempt(device_id)
            self.send_response(device_id, {'cmd': 'OPEN'})
            
            self.db.settings['home_occupied'] = True
            self.db.settings['last_access'] = {
                'method': 'passkey',
                'password_id': pwd_id,
                'timestamp': datetime.now().isoformat()
            }
            self.db.save_all()
        else:
            logger.warning(f"Access denied: {deny_reason}")
            self.security.record_failed_attempt(device_id)
            self.send_response(device_id, {
                'cmd': 'LOCK',
                'reason': deny_reason
            })
    
    def handle_telemetry(self, device_id, payload):
        """Handle telemetry with automation logic"""
        logger.debug(f"Telemetry from {device_id}: {payload}")
        
        # Auto fan control
        if device_id == 'temp_01' and payload.get('msg_type') == 'temp_update':
            temp = payload.get('data', {}).get('temperature')
            
            if temp is not None:
                threshold = self.db.settings.get('automation', {}).get('auto_fan_temp_threshold', 28)
                auto_enabled = self.db.settings.get('automation', {}).get('auto_fan_enabled', True)
                
                if auto_enabled:
                    should_be_on = (temp >= threshold)
                    command = {'cmd': 'fan_on' if should_be_on else 'fan_off'}
                    self.send_command('fan_01', command)
                    logger.info(f"Auto fan control: Temp={temp}°C → Fan {'ON' if should_be_on else 'OFF'}")
        
        # Forward to AWS
        aws_payload = {
            'gateway_id': CONFIG['aws_mqtt']['client_id'],
            'device_id': device_id,
            'data_type': payload.get('msg_type', 'telemetry'),
            'data': payload,
            'timestamp': datetime.now().isoformat()
        }
        
        self.publish_to_aws(CONFIG['topics']['aws_sensor_data'], aws_payload)
    
    def handle_status(self, device_id, payload):
        """Handle status updates"""
        logger.debug(f"Status from {device_id}: {payload}")
        
        self.publish_to_aws(CONFIG['topics']['aws_system_logs'], {
            'type': 'device_status',
            'device_id': device_id,
            'status': payload,
            'timestamp': datetime.now().isoformat()
        })
    
    def handle_aws_command(self, payload):
        """Handle commands from AWS"""
        device_id = payload.get('device_id')
        command = payload.get('command')
        
        logger.info(f"AWS command for {device_id}: {command}")
        
        if command in ['relay_control', 'door_control', 'system_update']:
            self.send_command(device_id, payload)
    
    def send_response(self, device_id, response):
        """Send response with QoS 1"""
        topic = CONFIG['topics']['device_command'].format(device_id=device_id)
        
        if self.broker_mqtt:
            try:
                result = self.broker_mqtt.publish(
                    topic,
                    json.dumps(response),
                    qos=1  # At least once delivery
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(f"Response sent to {device_id}: {response}")
                else:
                    logger.error(f"Failed to send response to {device_id}: {result.rc}")
            except Exception as e:
                logger.error(f"Error sending response: {e}")
    
    def send_command(self, device_id, command):
        """Send command with QoS 1"""
        topic = CONFIG['topics']['device_command'].format(device_id=device_id)
        
        if self.broker_mqtt:
            try:
                result = self.broker_mqtt.publish(
                    topic,
                    json.dumps(command),
                    qos=1
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"Command sent to {device_id}: {command}")
                else:
                    logger.error(f"Failed to send command: {result.rc}")
            except Exception as e:
                logger.error(f"Error sending command: {e}")
    
    def publish_to_aws(self, topic, payload):
        """Publish to AWS with retry logic"""
        if self.aws_mqtt:
            try:
                result = self.aws_mqtt.publish(
                    topic,
                    json.dumps(payload),
                    qos=1
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    logger.debug(f"Published to AWS: {topic}")
                else:
                    logger.error(f"Failed to publish to AWS: {result.rc}")
            except Exception as e:
                logger.error(f"Error publishing to AWS: {e}")
    
    def parse_sensor_message(self, data):
        """Parse LoRa message with enhanced error handling"""
        try:
            if len(data) < 3 or data[:3] != b'\x00\x02\x17':
                return None
                
            raw = data[3:]
            if len(raw) < 9:
                logger.warning("LoRa message too short")
                return None
                
            header_byte0 = raw[0]
            version = header_byte0 & 0x0F
            msg_type_n = (header_byte0 >> 4) & 0x0F
            
            device_byte1 = raw[1]
            device_type_n = device_byte1 & 0x0F
            flags = (device_byte1 >> 4) & 0x0F
            
            seq = struct.unpack('<H', raw[2:4])[0]
            timestamp = struct.unpack('<I', raw[4:8])[0]
            
            uid_len = raw[8]
            expected_len = 9 + uid_len + 4
            
            if len(raw) < expected_len:
                logger.warning(f"LoRa message incomplete: expected {expected_len}, got {len(raw)}")
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
            
            message = {
                'header': {
                    'version': version,
                    'msg_type': msg_type_str,
                    'msg_type_n': msg_type_n,
                    'device_type': device_type_str,
                    'device_type_raw': device_type_n,
                    'flags': flags,
                    'seq': seq,
                    'timestamp': timestamp
                },
                'payload': self.parse_payload(msg_type_n, payload_data),
                'crc': hex(crc_received)
            }
            
            return message
        except Exception as e:
            logger.error(f"Error parsing LoRa message: {e}")
            return None
    
    def parse_payload(self, msg_type, payload_data):
        """Parse payload based on message type"""
        try:
            if msg_type == 0x01:  # RFID
                return {
                    'uid': ''.join(f'{b:02x}' for b in payload_data),
                    'uid_len': len(payload_data)
                }
            elif msg_type == 0x06:  # Gate status
                return {
                    'status': payload_data.decode('utf-8')
                }
            else:
                return {'raw': payload_data.hex()}
        except Exception as e:
            logger.error(f"Payload parse error: {e}")
            return None
    
    def process_lora_data(self, message):
        """Process LoRa message with access control"""
        msg_type = message['header']['msg_type']
        
        if msg_type == 'rfid_scan':
            return self.handle_rfid_scan(message)
        elif msg_type == 'gate_status':
            return self.handle_gate_status(message)
        
        return None
    
    def handle_rfid_scan(self, message):
        """Handle RFID scan with access rules"""
        uid = message['payload'].get('uid')
        
        # Authenticate RFID
        is_valid = self.db.authenticate_rfid(uid)
        
        # Check access rules
        access_allowed = True
        deny_reason = 'invalid_card'
        
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('rfid', uid)
        
        result = 'granted' if (is_valid and access_allowed) else 'denied'
        
        logger.info(f"RFID scan: {uid} -> {result.upper()}")
        
        # Log to AWS
        self.publish_to_aws(CONFIG['topics']['aws_system_logs'], {
            'type': 'access_attempt',
            'method': 'rfid',
            'uid': uid,
            'result': result,
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat(),
            'deny_reason': deny_reason if not (is_valid and access_allowed) else None
        })
        
        return 'GRANT' if (is_valid and access_allowed) else 'DENY5'
    
    def handle_gate_status(self, message):
        """Handle gate status update"""
        status = message['payload'].get('status')
        
        self.publish_to_aws(CONFIG['topics']['aws_system_logs'], {
            'type': 'gate_status',
            'status': status,
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat()
        })
        
        return None
    
    def send_lora_response(self, device_type_numeric, response_text):
        """Send response via LoRa with retry"""
        if not self.serial_conn:
            logger.error("LoRa connection not available")
            return False
        
        max_retries = 3
        for attempt in range(max_retries):
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
                logger.error(f"LoRa send error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
        
        return False
    
    def run(self):
        """Main loop with watchdog and error recovery"""
        self.running = True
        logger.info("Gateway started")
        
        buffer = b''
        last_heartbeat = time.time()
        heartbeat_interval = 60  # Send heartbeat every 60 seconds
        
        while self.running:
            try:
                # Send periodic heartbeat
                if time.time() - last_heartbeat > heartbeat_interval:
                    self.publish_to_aws(CONFIG['topics']['aws_system_logs'], {
                        'type': 'heartbeat',
                        'gateway_id': CONFIG['aws_mqtt']['client_id'],
                        'timestamp': datetime.now().isoformat(),
                        'uptime': time.time() - last_heartbeat
                    })
                    last_heartbeat = time.time()
                
                # Handle LoRa messages
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    new_data = self.serial_conn.read(self.serial_conn.in_waiting)
                    buffer += new_data
                    
                    # Process LoRa messages
                    while True:
                        header_idx = buffer.find(b'\x00\x02\x17')
                        if header_idx == -1:
                            break
                            
                        raw = buffer[header_idx + 3:]
                        if len(raw) < 9:
                            break
                            
                        uid_len = raw[8]
                        msg_len = 9 + uid_len + 4
                        
                        if len(raw) < msg_len:
                            break
                            
                        candidate = buffer[header_idx:header_idx + 3 + msg_len]
                        message = self.parse_sensor_message(candidate)
                        
                        if message:
                            response = self.process_lora_data(message)
                            if response:
                                device_numeric = message['header'].get('device_type_raw', 1)
                                self.send_lora_response(device_numeric, response)
                        
                        buffer = buffer[header_idx + 3 + msg_len:]
                
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                logger.info("Shutting down gateway...")
                self.running = False
            except Exception as e:
                logger.error(f"Gateway error: {e}", exc_info=True)
                time.sleep(1)
        
        # Cleanup
        logger.info("Cleaning up connections...")
        if self.broker_mqtt:
            self.broker_mqtt.loop_stop()
            self.broker_mqtt.disconnect()
        if self.aws_mqtt:
            self.aws_mqtt.loop_stop()
            self.aws_mqtt.disconnect()
        if self.serial_conn:
            self.serial_conn.close()
        
        logger.info("Gateway stopped")


def main():
    gateway = Gateway()
    try:
        gateway.run()
    except Exception as e:
        logger.critical(f"Gateway startup failed: {e}", exc_info=True)
    finally:
        gateway.running = False


if __name__ == "__main__":
    main()