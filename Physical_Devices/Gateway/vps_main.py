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
from typing import Dict, Optional, Callable

# ============= LOGGING SETUP =============
def setup_logging():
    """Configure comprehensive logging system"""
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
    'lora_port': 'COM5',
    'lora_baudrate': 9600,
    
    'local_broker': {
        'host': '192.168.1.205',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './gateway_cert/ca.cert.pem',
        'username': 'Gateway1',
        'password': '125'
    },
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'client_id': 'Gateway1',
        'ca_cert': './gateway_cert/ca.cert.pem',
        'cert_file': './gateway_cert/gateway.cert.pem',
        'key_file': './gateway_cert/gateway.key.pem',
    },
    
    'hmac_key': bytes([
        0x5A, 0x5A, 0x2B, 0x3F, 0x87, 0xDA, 0x01, 0xF9,
        0xDE, 0xE1, 0x83, 0xAD, 0x84, 0x54, 0xB5, 0x34,
        0x77, 0x68, 0x47, 0x8C, 0xE8, 0xFD, 0x73, 0x1F,
        0xBD, 0xE1, 0x3C, 0x42, 0x79, 0xB8, 0xFE, 0xA4
    ]),
    
    'topics': {
        'local_telemetry': 'home/devices/+/telemetry',
        'local_request': 'home/devices/+/request',
        'local_status': 'home/devices/+/status',
        'local_command': 'home/devices/{device_id}/command',
        'vps_telemetry': 'gateway/telemetry/{device_id}',
        'vps_status': 'gateway/status/{device_id}',
        'vps_logs': 'gateway/logs/{device_id}',
        'vps_command': 'gateway/command/#',
        'vps_gateway_status': 'gateway/status/Gateway1',
    },
    
    'db_path': './data/',
    'devices_db': 'devices.json',
    'buffer_max_size': 1000,
    'heartbeat_interval': 60,
    
    'security': {
        'max_failed_attempts': 5,
        'lockout_duration_seconds': 300,
        'timestamp_tolerance_seconds': 300,
        'nonce_cache_size': 1000
    },
    
    'automation': {
        'auto_fan_enabled': True,
        'default_temp_threshold': 28.0,
        'fan_device_id': 'fan_01',
        'temp_device_id': 'temp_01'
    }
}

MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x06: 'gate_status',
}

DEVICE_TYPES = {
    0x01: 'rfid_gate_01',
}

# ============= UTILITY FUNCTIONS =============
def crc32(data: bytes, poly=0x04C11DB7, init=0xFFFFFFFF, xor_out=0xFFFFFFFF) -> int:
    """Calculate CRC32 checksum"""
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
    """Verify HMAC signature"""
    calculated = hmac.new(key, body_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated, received_hmac)

# ============= SECURITY MANAGER =============
class SecurityManager:
    """Centralized security management for authentication and access control"""
    
    def __init__(self, config):
        self.config = config
        self.failed_attempts = {}
        self.lockout_until = {}
        self.used_nonces = deque(maxlen=config['security']['nonce_cache_size'])
        self.lock = threading.Lock()
    
    def is_locked_out(self, device_id):
        """Check if device is currently locked out"""
        with self.lock:
            if device_id in self.lockout_until:
                if datetime.now() < self.lockout_until[device_id]:
                    return True
                else:
                    del self.lockout_until[device_id]
                    self.failed_attempts[device_id] = 0
            return False
    
    def record_failed_attempt(self, device_id):
        """Record failed authentication attempt and trigger lockout if needed"""
        with self.lock:
            self.failed_attempts[device_id] = self.failed_attempts.get(device_id, 0) + 1
            
            if self.failed_attempts[device_id] >= self.config['security']['max_failed_attempts']:
                lockout_duration = timedelta(seconds=self.config['security']['lockout_duration_seconds'])
                self.lockout_until[device_id] = datetime.now() + lockout_duration
                logger.warning(f"Device {device_id} locked out until {self.lockout_until[device_id]}")
                return True
            return False
    
    def record_success(self, device_id):
        """Clear failed attempts on successful authentication"""
        with self.lock:
            self.failed_attempts.pop(device_id, None)
            self.lockout_until.pop(device_id, None)
    
    def validate_timestamp(self, timestamp):
        """Validate timestamp to prevent replay attacks"""
        current_time = int(time.time())
        tolerance = self.config['security']['timestamp_tolerance_seconds']
        return abs(current_time - timestamp) <= tolerance
    
    def validate_nonce(self, nonce):
        """Check nonce uniqueness to prevent replay attacks"""
        with self.lock:
            if nonce in self.used_nonces:
                return False
            self.used_nonces.append(nonce)
            return True

# ============= DATABASE MANAGER =============
class Database:
    """Centralized database access and management"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.load_database()
    
    def load_database(self):
        """Load all database files into memory"""
        with self.lock:
            self.devices = self._load_json(CONFIG['devices_db'])
            self.settings = self._load_json('settings.json')
            self.logs = []
    
    def _load_json(self, filename):
        """Load JSON file with error handling"""
        file_path = os.path.join(self.db_path, filename)
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
            return {}
    
    def _save_json(self, filename, data):
        """Save JSON file with backup mechanism"""
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
        """Save all modified databases"""
        with self.lock:
            self._save_json(CONFIG['devices_db'], self.devices)
            self._save_json('settings.json', self.settings)
    
    def authenticate_rfid(self, uid):
        """Authenticate RFID card"""
        try:
            with self.lock:
                card = self.devices.get('rfid_cards', {}).get(uid)
                return bool(card and card.get('active', False))
        except:
            return False
    
    def authenticate_passkey(self, password_hash):
        """Authenticate password hash"""
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
        """Check if access is allowed based on current rules"""
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
    
    def get_automation_config(self):
        """Get automation configuration"""
        with self.lock:
            return self.settings.get('automation', CONFIG['automation'])

# ============= UNIFIED REMOTE CONTROL AND AUTOMATION MANAGER =============
class RemoteControlManager:
    """
    Unified manager for all remote control operations and automation
    Handles: Keypad, RFID Gate, Fan control, and Automation rules
    """
    
    def __init__(self, gateway):
        self.gateway = gateway
        self.pending_commands = {}
        self.command_timeout = 30
        self.lock = threading.Lock()
        
        # Automation state
        self.automation_enabled = True
        self.last_temperature = None
        self.fan_state = None
        
        # Device command handlers registry
        self.device_handlers = {
            'passkey': self.handle_keypad_command,
            'rfid_gate': self.handle_rfid_gate_command,
            'relay_fan': self.handle_fan_command,
            'temp_DH11': self.handle_temp_sensor_data
        }
        
        logger.info("[REMOTE] Unified Remote Control Manager initialized")
        logger.info("[REMOTE] Supported devices: Keypad, RFID Gate, Fan")
        logger.info("[AUTOMATION] Automation engine integrated")
    
    def process_remote_command(self, device_id, command_data):
        """
        Main entry point for all remote commands from VPS
        Routes to appropriate handler based on device type
        """
        command_id = command_data.get('command_id', str(time.time()))
        command_type = command_data.get('cmd')
        user = command_data.get('user', 'unknown')
        
        logger.info(f"[REMOTE] Processing command '{command_type}' for {device_id} by {user}")
        
        # Validate device exists
        device = self.gateway.db.devices.get('devices', {}).get(device_id)
        if not device:
            logger.error(f"[REMOTE] Device {device_id} not found")
            self.send_command_response(device_id, command_id, False, "device_not_found")
            return False
        
        # Check device status
        if device.get('status') != 'online':
            logger.warning(f"[REMOTE] Device {device_id} is {device.get('status')}")
            self.send_command_response(device_id, command_id, False, f"device_{device.get('status')}")
            return False
        
        # Store pending command
        with self.lock:
            self.pending_commands[command_id] = {
                'device_id': device_id,
                'command': command_type,
                'user': user,
                'timestamp': time.time(),
                'status': 'pending'
            }
        
        # Route to appropriate handler
        device_type = device.get('device_type')
        handler = self.device_handlers.get(device_type)
        
        if not handler:
            logger.error(f"[REMOTE] No handler for device type: {device_type}")
            self.send_command_response(device_id, command_id, False, "unsupported_device_type")
            return False
        
        # Execute command
        success = handler(device_id, command_data)
        
        # Log remote access
        if success:
            self.log_remote_access(device_id, user, command_data.get('reason', 'no_reason'), command_type, command_id)
        
        return success
    
    # ===== KEYPAD COMMAND HANDLER =====
    def handle_keypad_command(self, device_id, command_data):
        """Handle remote commands for keypad device"""
        command = command_data.get('cmd')
        command_id = command_data.get('command_id')
        user = command_data.get('user')
        
        if command == 'remote_unlock':
            duration_ms = command_data.get('duration_ms', 5000)
            reason = command_data.get('reason', 'remote_unlock')
            
            # Validate duration
            if duration_ms < 1000 or duration_ms > 30000:
                duration_ms = 5000
                logger.warning(f"[REMOTE] Duration adjusted to {duration_ms}ms")
            
            mqtt_command = {
                'cmd': 'remote_unlock',
                'command_id': command_id,
                'user': user,
                'reason': reason,
                'duration_ms': duration_ms,
                'timestamp': int(time.time())
            }
            
            return self.send_local_mqtt_command(device_id, mqtt_command)
        
        elif command == 'remote_lock':
            mqtt_command = {
                'cmd': 'remote_lock',
                'command_id': command_id,
                'user': user,
                'timestamp': int(time.time())
            }
            
            return self.send_local_mqtt_command(device_id, mqtt_command)
        
        else:
            logger.error(f"[REMOTE] Unknown keypad command: {command}")
            return False
    
    # ===== RFID GATE COMMAND HANDLER =====
    def handle_rfid_gate_command(self, device_id, command_data):
        """Handle remote commands for RFID gate via LoRa"""
        command = command_data.get('cmd')
        command_id = command_data.get('command_id')
        user = command_data.get('user')
        
        if command == 'remote_unlock':
            duration_ms = command_data.get('duration_ms', 5000)
            
            # Format: "REMOTE_UNLOCK:{command_id}:{user}:{duration_ms}"
            lora_command = f"REMOTE_UNLOCK:{command_id}:{user}:{duration_ms}"
            
            return self.send_lora_command(0x01, lora_command)
        
        elif command == 'remote_lock':
            # Format: "REMOTE_LOCK:{command_id}:{user}"
            lora_command = f"REMOTE_LOCK:{command_id}:{user}"
            
            return self.send_lora_command(0x01, lora_command)
        
        else:
            logger.error(f"[REMOTE] Unknown RFID gate command: {command}")
            return False
    
    # ===== FAN COMMAND HANDLER =====
    def handle_fan_command(self, device_id, command_data):
        """Handle remote commands for fan device"""
        command = command_data.get('cmd')
        command_id = command_data.get('command_id')
        user = command_data.get('user')
        
        valid_commands = ['fan_on', 'fan_off', 'fan_toggle', 'set_auto']
        
        if command not in valid_commands:
            logger.error(f"[REMOTE] Invalid fan command: {command}")
            return False
        
        mqtt_command = {
            'cmd': command,
            'command_id': command_id,
            'user': user,
            'timestamp': int(time.time())
        }
        
        # Include additional parameters for set_auto
        if command == 'set_auto':
            mqtt_command['enable'] = command_data.get('enable', True)
            mqtt_command['threshold'] = command_data.get('threshold', CONFIG['automation']['default_temp_threshold'])
        
        success = self.send_local_mqtt_command(device_id, mqtt_command)
        
        # Update local fan state tracking
        if success and command in ['fan_on', 'fan_off']:
            with self.lock:
                self.fan_state = 'on' if command == 'fan_on' else 'off'
        
        return success
    
    # ===== TEMPERATURE SENSOR HANDLER WITH AUTOMATION =====
    def handle_temp_sensor_data(self, device_id, data):
        """
        Process temperature sensor data and trigger automation
        This is called when telemetry is received from temp sensor
        """
        try:
            temperature = data.get('data', {}).get('temperature')
            
            if temperature is None or not isinstance(temperature, (int, float)):
                return True  # Not an error, just no automation trigger
            
            with self.lock:
                self.last_temperature = temperature
            
            # Check if automation is enabled
            automation_config = self.gateway.db.get_automation_config()
            auto_enabled = automation_config.get('auto_fan_enabled', True)
            
            if not auto_enabled or not self.automation_enabled:
                logger.debug(f"[AUTOMATION] Disabled, temperature: {temperature}°C")
                return True
            
            # Get threshold
            threshold = automation_config.get('default_temp_threshold', CONFIG['automation']['default_temp_threshold'])
            fan_device = automation_config.get('fan_device_id', CONFIG['automation']['fan_device_id'])
            
            # Determine if fan should be on or off
            should_be_on = (temperature >= threshold)
            
            # Check current fan state
            if self.fan_state is None or (should_be_on and self.fan_state == 'off') or (not should_be_on and self.fan_state == 'on'):
                # State change needed
                command = 'fan_on' if should_be_on else 'fan_off'
                
                logger.info(f"[AUTOMATION] Temperature {temperature}°C (threshold: {threshold}°C)")
                logger.info(f"[AUTOMATION] Triggering: {command}")
                
                # Send automation command
                mqtt_command = {
                    'cmd': command,
                    'command_id': f"auto_{int(time.time())}",
                    'user': 'automation_engine',
                    'trigger': 'temperature_threshold',
                    'temperature': temperature,
                    'threshold': threshold,
                    'timestamp': int(time.time())
                }
                
                success = self.send_local_mqtt_command(fan_device, mqtt_command)
                
                if success:
                    with self.lock:
                        self.fan_state = 'on' if should_be_on else 'off'
                    
                    # Log automation action
                    self.log_automation_action(fan_device, command, temperature, threshold)
            
            return True
            
        except Exception as e:
            logger.error(f"[AUTOMATION] Error processing temperature: {e}")
            return False
    
    # ===== COMMUNICATION METHODS =====
    def send_local_mqtt_command(self, device_id, command):
        """Send command to device via local MQTT"""
        try:
            topic = CONFIG['topics']['local_command'].format(device_id=device_id)
            
            if self.gateway.local_mqtt and self.gateway.local_connected:
                self.gateway.local_mqtt.publish(
                    topic,
                    json.dumps(command),
                    qos=1
                )
                logger.info(f"[REMOTE] Sent MQTT command to {device_id}: {command.get('cmd')}")
                return True
            else:
                logger.error("[REMOTE] Local MQTT not connected")
                return False
                
        except Exception as e:
            logger.error(f"[REMOTE] Error sending MQTT command: {e}")
            return False
    
    def send_lora_command(self, device_type_numeric, command_text):
        """Send command via LoRa to device"""
        try:
            if not self.gateway.serial_conn:
                logger.error("[REMOTE] LoRa serial not connected")
                return False
            
            response_data = command_text.encode('utf-8')
            head = b'\xC0\x00\x00'
            addr = struct.pack('>H', int(device_type_numeric) & 0xFFFF)
            chan = bytes([23])
            length = bytes([len(response_data)])
            packet = head + addr + chan + length + response_data
            
            self.gateway.serial_conn.write(packet)
            logger.info(f"[REMOTE] Sent LoRa command: {command_text}")
            return True
            
        except Exception as e:
            logger.error(f"[REMOTE] LoRa send error: {e}")
            return False
    
    def send_command_response(self, device_id, command_id, success, status):
        """Send command response back to VPS"""
        if not self.gateway.vps_connected:
            return
        
        response = {
            'gateway_id': 'Gateway1',
            'device_id': device_id,
            'command_id': command_id,
            'success': success,
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = f"gateway/command/response/{device_id}"
        self.gateway.vps_mqtt.publish(topic, json.dumps(response), qos=1)
        
        logger.info(f"[REMOTE] Response sent: command_id={command_id}, success={success}, status={status}")
    
    # ===== LOGGING METHODS =====
    def log_remote_access(self, device_id, user, reason, action, command_id):
        """Log remote access event to VPS"""
        log_entry = {
            'type': 'remote_access',
            'device_id': device_id,
            'action': action,
            'user': user,
            'reason': reason,
            'command_id': command_id,
            'timestamp': datetime.now().isoformat(),
            'gateway_id': 'Gateway1'
        }
        
        self.gateway.forward_to_vps(device_id, 'logs', log_entry)
        logger.info(f"[REMOTE] Logged {action} by {user} for {device_id}")
    
    def log_automation_action(self, device_id, action, temperature, threshold):
        """Log automation action to VPS"""
        log_entry = {
            'type': 'automation',
            'device_id': device_id,
            'action': action,
            'trigger': 'temperature_threshold',
            'temperature': temperature,
            'threshold': threshold,
            'timestamp': datetime.now().isoformat(),
            'gateway_id': 'Gateway1'
        }
        
        self.gateway.forward_to_vps(device_id, 'logs', log_entry)
        logger.info(f"[AUTOMATION] Logged {action} for {device_id} (temp: {temperature}°C)")
    
    def cleanup_old_commands(self):
        """Remove expired pending commands"""
        current_time = time.time()
        expired = []
        
        with self.lock:
            for cmd_id, cmd_data in self.pending_commands.items():
                if current_time - cmd_data['timestamp'] > self.command_timeout:
                    expired.append(cmd_id)
            
            for cmd_id in expired:
                logger.warning(f"[REMOTE] Command {cmd_id} expired")
                del self.pending_commands[cmd_id]
    
    def get_status(self):
        """Get current status of remote control manager"""
        with self.lock:
            return {
                'automation_enabled': self.automation_enabled,
                'last_temperature': self.last_temperature,
                'fan_state': self.fan_state,
                'pending_commands': len(self.pending_commands)
            }

# ============= MAIN GATEWAY CLASS =============
class Gateway:
    """
    Main Gateway orchestrator
    Manages all communication between devices, local MQTT, VPS, and LoRa
    """
    
    def __init__(self):
        logger.info("=" * 60)
        logger.info("Initializing IoT Gateway")
        logger.info("=" * 60)
        
        # Core components
        self.db = Database(CONFIG['db_path'])
        self.security = SecurityManager(CONFIG)
        self.remote_control = RemoteControlManager(self)
        
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
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'messages_buffered': 0,
            'remote_commands_executed': 0,
            'automation_triggers': 0,
            'uptime_start': datetime.now()
        }
        
        # Initialize connections
        self.setup_local_mqtt()
        self.setup_vps_mqtt()
        self.setup_serial()
        
        logger.info("Gateway initialization complete")
    
    # ============= LOCAL MQTT SETUP =============
    def setup_local_mqtt(self):
        """Configure and connect to local MQTT broker"""
        logger.info("Setting up Local MQTT Broker connection...")
        
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
            logger.info("Local MQTT connection initiated")
        except Exception as e:
            logger.error(f"Local MQTT connection failed: {e}")
    
    def on_local_connect(self, client, userdata, flags, rc):
        """Callback when connected to local MQTT"""
        if rc == 0:
            logger.info("✓ Connected to Local MQTT Broker")
            self.local_connected = True
            
            client.subscribe(CONFIG['topics']['local_telemetry'], qos=1)
            client.subscribe(CONFIG['topics']['local_request'], qos=1)
            client.subscribe(CONFIG['topics']['local_status'], qos=1)
            
            logger.info("✓ Subscribed to local device topics")
        else:
            logger.error(f"✗ Local MQTT connection failed with code: {rc}")
            self.local_connected = False
    
    def on_local_disconnect(self, client, userdata, rc):
        """Callback when disconnected from local MQTT"""
        logger.warning(f"✗ Local MQTT disconnected (rc={rc})")
        self.local_connected = False
    
    def on_local_message(self, client, userdata, msg):
        """Handle messages from local devices"""
        try:
            self.stats['messages_received'] += 1
            
            # Parse topic to get device_id
            parts = msg.topic.split('/')
            device_id = parts[2] if len(parts) >= 3 else 'unknown'
            
            try:
                payload = json.loads(msg.payload.decode())
            except:
                payload = {'raw': msg.payload.decode()}
            
            logger.debug(f"[LOCAL] {device_id} → {msg.topic}")
            
            # Route to appropriate handler
            if 'telemetry' in msg.topic:
                self.handle_telemetry(device_id, payload)
            elif 'request' in msg.topic:
                self.handle_request(device_id, payload)
            elif 'status' in msg.topic:
                self.handle_status(device_id, payload)
        
        except Exception as e:
            logger.error(f"Error handling local message: {e}", exc_info=True)
    
    # ============= VPS MQTT SETUP =============
    def setup_vps_mqtt(self):
        """Configure and connect to VPS MQTT broker with mTLS"""
        logger.info("Setting up VPS MQTT Broker connection...")
        
        cfg = CONFIG['vps_broker']
        self.vps_mqtt = mqtt.Client(client_id=cfg['client_id'])
        
        # Configure mTLS
        try:
            self.vps_mqtt.tls_set(
                ca_certs=cfg['ca_cert'],
                certfile=cfg['cert_file'],
                keyfile=cfg['key_file'],
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
            self.vps_mqtt.tls_insecure_set(False)
            logger.info("✓ VPS mTLS configured")
        except Exception as e:
            logger.error(f"✗ VPS TLS setup error: {e}")
            return
        
        self.vps_mqtt.on_connect = self.on_vps_connect
        self.vps_mqtt.on_message = self.on_vps_message
        self.vps_mqtt.on_disconnect = self.on_vps_disconnect
        
        try:
            self.vps_mqtt.connect(cfg['host'], cfg['port'], 60)
            self.vps_mqtt.loop_start()
            logger.info("VPS MQTT connection initiated")
        except Exception as e:
            logger.error(f"VPS MQTT connection failed: {e}")
    
    def on_vps_connect(self, client, userdata, flags, rc):
        """Callback when connected to VPS MQTT"""
        if rc == 0:
            logger.info("✓ Connected to VPS MQTT Broker")
            self.vps_connected = True
            
            # Subscribe to command topics
            client.subscribe(CONFIG['topics']['vps_command'], qos=1)
            logger.info("✓ Subscribed to VPS command topics")
            
            # Send gateway online status
            self.send_gateway_status('online')
            
            # Flush buffered messages
            self.flush_buffer()
        else:
            logger.error(f"✗ VPS MQTT connection failed with code: {rc}")
            self.vps_connected = False
    
    def on_vps_disconnect(self, client, userdata, rc):
        """Callback when disconnected from VPS"""
        logger.warning(f"✗ VPS MQTT disconnected (rc={rc})")
        self.vps_connected = False
    
    def on_vps_message(self, client, userdata, msg):
        """Handle commands from VPS"""
        try:
            payload = json.loads(msg.payload.decode())
            
            # Extract device_id from topic: gateway/command/{device_id}
            parts = msg.topic.split('/')
            
            if len(parts) >= 3 and parts[1] == 'command':
                device_id = parts[2]
                command = payload.get('cmd')
                
                logger.info(f"[VPS] Command received: '{command}' for {device_id}")
                
                # Route all remote commands through RemoteControlManager
                if command in ['remote_unlock', 'remote_lock', 'fan_on', 'fan_off', 'fan_toggle', 'set_auto']:
                    self.remote_control.process_remote_command(device_id, payload)
                    self.stats['remote_commands_executed'] += 1
                
                elif command == 'status_request':
                    self.handle_status_request(device_id, payload)
                
                elif command == 'config_update':
                    self.handle_config_update(device_id, payload)
                
                else:
                    logger.warning(f"[VPS] Unknown command: {command}")
        
        except Exception as e:
            logger.error(f"Error handling VPS message: {e}", exc_info=True)
    
    # ============= SERIAL/LORA SETUP =============
    def setup_serial(self):
        """Configure LoRa serial connection"""
        try:
            self.serial_conn = serial.Serial(
                CONFIG['lora_port'],
                CONFIG['lora_baudrate'],
                timeout=1
            )
            logger.info(f"✓ LoRa connected on {CONFIG['lora_port']}")
        except Exception as e:
            logger.error(f"✗ LoRa connection failed: {e}")
            self.serial_conn = None
    
    # ============= MESSAGE HANDLERS =============
    def handle_telemetry(self, device_id, payload):
        """Process telemetry data from devices"""
        logger.debug(f"[TELEMETRY] {device_id}: {payload.get('msg_type')}")
        
        # Pass temperature data to automation engine
        if device_id == CONFIG['automation']['temp_device_id']:
            self.remote_control.handle_temp_sensor_data(device_id, payload)
            self.stats['automation_triggers'] += 1
        
        # Forward to VPS
        self.forward_to_vps(device_id, 'telemetry', payload)
    
    def handle_request(self, device_id, payload):
        """Process authentication requests from devices"""
        logger.info(f"[REQUEST] {device_id}: {payload.get('cmd', 'unknown')}")
        
        # Security checks
        if self.security.is_locked_out(device_id):
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'locked_out'})
            return
        
        if 'hmac' not in payload or 'body' not in payload:
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_format'})
            return
        
        # Verify HMAC
        if not verify_hmac(payload['body'], payload['hmac'], CONFIG['hmac_key']):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_signature'})
            
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
        
        # Process authentication command
        if body.get('cmd') == 'unlock_request':
            self.handle_passkey_request(device_id, body)
    
    def handle_passkey_request(self, device_id, body):
        """Process keypad password authentication"""
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
        
        self.forward_to_vps(device_id, 'logs', log_entry)
        
        # Grant or deny
        if is_valid and access_allowed:
            logger.info(f"✓ Access granted: {pwd_id}")
            self.security.record_success(device_id)
            self.send_local_response(device_id, {'cmd': 'OPEN'})
            
            self.db.settings['last_access'] = {
                'method': 'passkey',
                'password_id': pwd_id,
                'timestamp': datetime.now().isoformat()
            }
            self.db.save_all()
        else:
            logger.warning(f"✗ Access denied: {deny_reason}")
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': deny_reason})
    
    def handle_status(self, device_id, payload):
        """Process status updates from devices"""
        logger.debug(f"[STATUS] {device_id}: {payload.get('state')}")
        
        # Check for command acknowledgment
        if 'command_id' in payload:
            self.remote_control.send_command_response(
                device_id,
                payload['command_id'],
                payload.get('success', True),
                payload.get('status', 'completed')
            )
        
        # Forward to VPS
        self.forward_to_vps(device_id, 'status', payload)
    
    def handle_status_request(self, device_id, payload):
        """Handle status request from VPS"""
        topic = CONFIG['topics']['local_command'].format(device_id=device_id)
        command = {
            'cmd': 'status_request',
            'command_id': payload.get('command_id'),
            'timestamp': int(time.time())
        }
        self.local_mqtt.publish(topic, json.dumps(command), qos=1)
        logger.info(f"[VPS] Status request forwarded to {device_id}")
    
    def handle_config_update(self, device_id, payload):
        """Handle configuration update from VPS"""
        topic = CONFIG['topics']['local_command'].format(device_id=device_id)
        self.local_mqtt.publish(topic, json.dumps(payload), qos=1)
        logger.info(f"[VPS] Config update forwarded to {device_id}")
    
    # ============= VPS COMMUNICATION =============
    def forward_to_vps(self, device_id, msg_type, payload):
        """Forward message to VPS with offline buffering"""
        vps_payload = {
            'gateway_id': 'Gateway1',
            'device_id': device_id,
            'data': payload,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = CONFIG['topics'][f'vps_{msg_type}'].format(device_id=device_id)
        
        if self.vps_connected:
            self.publish_to_vps(topic, vps_payload)
        else:
            with self.buffer_lock:
                self.buffer.append({'topic': topic, 'payload': vps_payload})
                self.stats['messages_buffered'] += 1
                logger.debug(f"[BUFFER] Message buffered (total: {len(self.buffer)})")
    
    def publish_to_vps(self, topic, payload):
        """Publish to VPS MQTT"""
        try:
            result = self.vps_mqtt.publish(topic, json.dumps(payload), qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['messages_sent'] += 1
                logger.debug(f"[VPS] Published to {topic}")
            else:
                logger.error(f"[VPS] Publish failed with code: {result.rc}")
        except Exception as e:
            logger.error(f"[VPS] Error publishing: {e}")
    
    def flush_buffer(self):
        """Flush buffered messages when VPS reconnects"""
        with self.buffer_lock:
            if not self.buffer:
                return
            
            logger.info(f"[BUFFER] Flushing {len(self.buffer)} buffered messages")
            
            while self.buffer:
                try:
                    msg = self.buffer.popleft()
                    msg['payload']['_flushed'] = True
                    self.publish_to_vps(msg['topic'], msg['payload'])
                    time.sleep(0.05)
                except Exception as e:
                    logger.error(f"[BUFFER] Error flushing: {e}")
                    break
            
            logger.info("[BUFFER] Flush complete")
    
    def send_gateway_status(self, status):
        """Send gateway status to VPS"""
        if not self.vps_connected:
            return
        
        uptime = (datetime.now() - self.stats['uptime_start']).total_seconds()
        remote_status = self.remote_control.get_status()
        
        status_payload = {
            'gateway_id': 'Gateway1',
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': int(uptime),
            'stats': {
                'messages_received': self.stats['messages_received'],
                'messages_sent': self.stats['messages_sent'],
                'messages_buffered': len(self.buffer),
                'remote_commands_executed': self.stats['remote_commands_executed'],
                'automation_triggers': self.stats['automation_triggers'],
                'local_connected': self.local_connected,
                'vps_connected': self.vps_connected
            },
            'features': {
                'remote_control': True,
                'offline_buffer': True,
                'automation_engine': True,
                'lora_support': True,
                'supported_devices': ['keypad', 'rfid_gate', 'fan', 'temp_sensor']
            },
            'remote_control_status': remote_status
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
            logger.debug(f"[LOCAL] Response to {device_id}: {response}")
    
    # ============= LORA HANDLING =============
    def parse_lora_message(self, data):
        """Parse LoRa message from RFID gate"""
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
                logger.error("[LoRa] CRC check failed")
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
        """Process LoRa message and generate response"""
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
        
        logger.info(f"[RFID] {uid}: {result.upper()}")
        
        # Log to VPS
        self.forward_to_vps('rfid_gate_01', 'logs', {
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
            logger.info(f"[LoRa] >> {response_text}")
            return True
        except Exception as e:
            logger.error(f"[LoRa] Send error: {e}")
            return False
    
    # ============= MAIN LOOP =============
    def run(self):
        """Main gateway execution loop"""
        self.running = True
        
        logger.info("=" * 60)
        logger.info("IoT Gateway Started Successfully")
        logger.info(f"Local MQTT: {CONFIG['local_broker']['host']}:{CONFIG['local_broker']['port']}")
        logger.info(f"VPS MQTT: {CONFIG['vps_broker']['host']}:{CONFIG['vps_broker']['port']}")
        logger.info("Features: Remote Control (Keypad, RFID, Fan), Automation Engine")
        logger.info("=" * 60)
        
        # Start background threads
        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        
        cleanup_thread = threading.Thread(target=self.cleanup_loop, daemon=True)
        cleanup_thread.start()
        
        # LoRa processing
        lora_buffer = b''
        last_heartbeat = time.time()
        last_stats_log = time.time()
        
        while self.running:
            try:
                # Periodic heartbeat
                if time.time() - last_heartbeat > CONFIG['heartbeat_interval']:
                    if self.vps_connected:
                        self.send_gateway_status('online')
                    last_heartbeat = time.time()
                
                # Periodic stats logging
                if time.time() - last_stats_log > 300:  # Every 5 minutes
                    remote_status = self.remote_control.get_status()
                    logger.info(
                        f"[STATS] RX: {self.stats['messages_received']}, "
                        f"TX: {self.stats['messages_sent']}, "
                        f"Buffered: {len(self.buffer)}, "
                        f"Remote Cmds: {self.stats['remote_commands_executed']}, "
                        f"Auto Triggers: {self.stats['automation_triggers']}, "
                        f"Temp: {remote_status['last_temperature']}°C, "
                        f"Fan: {remote_status['fan_state']}, "
                        f"Local: {'OK' if self.local_connected else 'DOWN'}, "
                        f"VPS: {'OK' if self.vps_connected else 'DOWN'}"
                    )
                    last_stats_log = time.time()
                
                # Handle LoRa messages
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    new_data = self.serial_conn.read(self.serial_conn.in_waiting)
                    lora_buffer += new_data
                    
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
                logger.info("\n[SHUTDOWN] Received interrupt signal")
                self.running = False
            
            except Exception as e:
                logger.error(f"[ERROR] Gateway loop error: {e}", exc_info=True)
                time.sleep(1)
        
        self.cleanup()
    
    def heartbeat_loop(self):
        """Background thread for periodic heartbeat"""
        while self.running:
            try:
                time.sleep(CONFIG['heartbeat_interval'])
                if self.vps_connected:
                    self.send_gateway_status('online')
            except Exception as e:
                logger.error(f"[HEARTBEAT] Error: {e}")
    
    def cleanup_loop(self):
        """Background thread for cleanup tasks"""
        while self.running:
            try:
                time.sleep(60)  # Every minute
                self.remote_control.cleanup_old_commands()
            except Exception as e:
                logger.error(f"[CLEANUP] Error: {e}")
    
    def cleanup(self):
        """Cleanup all connections on shutdown"""
        logger.info("[SHUTDOWN] Cleaning up connections...")
        
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
        
        logger.info("[SHUTDOWN] Gateway stopped cleanly")

# ============= MAIN ENTRY POINT =============
def main():
    """Application entry point"""
    
    # Check prerequisites
    logger.info("Checking prerequisites...")
    
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
        logger.error("MISSING CERTIFICATES:")
        for cert in missing_certs:
            logger.error(f"  ✗ {cert}")
        logger.error("")
        logger.error("Please ensure all certificates are in place")
        logger.error("=" * 60)
        return
    
    # Check database
    db_file = os.path.join(CONFIG['db_path'], CONFIG['devices_db'])
    if not os.path.exists(db_file):
        logger.error(f"Database not found: {db_file}")
        return
    
    logger.info("✓ Prerequisites check passed")
    
    # Create and run gateway
    gateway = Gateway()
    
    try:
        gateway.run()
    except Exception as e:
        logger.critical(f"Gateway startup failed: {e}", exc_info=True)
    finally:
        gateway.cleanup()

if __name__ == "__main__":
    main()