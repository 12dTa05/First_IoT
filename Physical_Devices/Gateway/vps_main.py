#!/usr/bin/env python3
"""
IoT Gateway - Complete Fixed Version
Manages communication between local devices and VPS server
Supports LoRa (RFID), WiFi devices, automation, and remote control
"""

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
    'lora_port': 'COM7',
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
        # Local topics (devices -> gateway)
        'local_telemetry': 'home/devices/+/telemetry',
        'local_request': 'home/devices/+/request',
        'local_status': 'home/devices/+/status',
        'local_command': 'home/devices/{device_id}/command',
        
        # VPS topics (gateway -> server) - FIXED TO MATCH API SERVER
        'vps_telemetry': 'iot/Gateway1/telemetry',
        'vps_status': 'iot/Gateway1/status',
        'vps_access': 'iot/Gateway1/access',
        'vps_alert': 'iot/Gateway1/alert',
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

# Message type mappings for LoRa protocol
MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x06: 'gate_status',
}

# Device type mappings for LoRa protocol
DEVICE_TYPES = {
    0x01: 'rfid_gate_01',
}

# FIXED: Map device_type_raw to actual device_id
DEVICE_TYPE_TO_ID = {
    0x01: 'rfid_gate_01',
}

# ============= UTILITY FUNCTIONS =============
def crc32(data: bytes, poly=0x04C11DB7, init=0xFFFFFFFF, xor_out=0xFFFFFFFF) -> int:
    """Calculate CRC32 checksum matching device implementation"""
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
    """Verify HMAC signature for authentication"""
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
                    if pwd_data.get('active', False) and pwd_data.get('hash') == password_hash:
                        return True, pwd_id
                return False, None
        except:
            return False, None
    
    def check_access_rules(self, method, identifier):
        """Check if access is allowed based on rules"""
        try:
            with self.lock:
                rules = self.settings.get('access_rules', {})
                
                if not rules.get('enabled', True):
                    return True, None
                
                current_time = datetime.now()
                current_hour = current_time.hour
                current_day = current_time.weekday()
                
                time_restriction = rules.get('time_restriction', {})
                if time_restriction.get('enabled', False):
                    start_hour = time_restriction.get('start_hour', 0)
                    end_hour = time_restriction.get('end_hour', 24)
                    
                    if not (start_hour <= current_hour < end_hour):
                        return False, 'outside_allowed_hours'
                
                day_restriction = rules.get('day_restriction', {})
                if day_restriction.get('enabled', False):
                    allowed_days = day_restriction.get('allowed_days', [0,1,2,3,4,5,6])
                    if current_day not in allowed_days:
                        return False, 'outside_allowed_days'
                
                return True, None
        except Exception as e:
            logger.error(f"Error checking access rules: {e}")
            return True, None
    
    def get_automation_config(self):
        """Get automation configuration"""
        with self.lock:
            return self.settings.get('automation', {
                'auto_fan_enabled': CONFIG['automation']['auto_fan_enabled'],
                'default_temp_threshold': CONFIG['automation']['default_temp_threshold'],
                'fan_device_id': CONFIG['automation']['fan_device_id'],
                'temp_device_id': CONFIG['automation']['temp_device_id']
            })

# ============= REMOTE CONTROL MANAGER =============
class RemoteControlManager:
    """Manages remote control commands and automation"""
    
    def __init__(self, gateway):
        self.gateway = gateway
        self.pending_commands = {}
        self.command_timeout = 30
        self.lock = threading.Lock()
        
        self.automation_enabled = True
        self.last_temperature = None
        self.fan_state = None
    
    def process_remote_command(self, device_id, payload):
        """Process remote control command from VPS"""
        command = payload.get('cmd')
        command_id = payload.get('command_id', f"cmd_{int(time.time())}")
        user = payload.get('user', 'system')
        
        logger.info(f"[REMOTE] Processing {command} for {device_id} by {user}")
        
        with self.lock:
            self.pending_commands[command_id] = {
                'device_id': device_id,
                'command': command,
                'user': user,
                'timestamp': time.time()
            }
        
        if command in ['remote_unlock', 'remote_lock']:
            success = self.handle_keypad_remote_command(device_id, payload)
        elif command in ['fan_on', 'fan_off', 'fan_toggle', 'set_auto']:
            success = self.handle_fan_remote_command(device_id, command, payload)
        else:
            logger.warning(f"[REMOTE] Unknown command: {command}")
            success = False
        
        if not success:
            self.send_command_response(device_id, command_id, False, 'command_failed')
    
    def handle_keypad_remote_command(self, device_id, payload):
        """Handle remote unlock/lock commands for keypad"""
        command = payload.get('cmd')
        command_id = payload.get('command_id')
        user = payload.get('user', 'system')
        reason = payload.get('reason', 'remote_access')
        duration_ms = payload.get('duration_ms', 5000)
        
        mqtt_command = {
            'cmd': command,
            'command_id': command_id,
            'user': user,
            'reason': reason,
            'duration_ms': duration_ms,
            'timestamp': int(time.time())
        }
        
        success = self.send_local_mqtt_command(device_id, mqtt_command)
        
        if success:
            self.log_remote_access(device_id, user, reason, command, command_id)
        
        return success
    
    def send_local_mqtt_command(self, device_id, command):
        """Send command to local device via MQTT"""
        topic = CONFIG['topics']['local_command'].format(device_id=device_id)
        
        if self.gateway.local_mqtt and self.gateway.local_connected:
            result = self.gateway.local_mqtt.publish(topic, json.dumps(command), qos=1)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        
        return False
    
    def handle_fan_remote_command(self, device_id, command, command_data):
        """Handle fan control commands"""
        mqtt_command = {
            'cmd': command,
            'command_id': command_data.get('command_id'),
            'user': command_data.get('user', 'system'),
            'timestamp': int(time.time())
        }
        
        if command == 'set_auto':
            mqtt_command['enable'] = command_data.get('enable', True)
            mqtt_command['threshold'] = command_data.get('threshold', CONFIG['automation']['default_temp_threshold'])
        
        success = self.send_local_mqtt_command(device_id, mqtt_command)
        
        if success and command in ['fan_on', 'fan_off']:
            with self.lock:
                self.fan_state = 'on' if command == 'fan_on' else 'off'
        
        return success
    
    def handle_temp_sensor_data(self, device_id, data):
        """Process temperature sensor data and trigger automation"""
        try:
            temperature = data.get('data', {}).get('temperature')
            
            if temperature is None or not isinstance(temperature, (int, float)):
                return True
            
            with self.lock:
                self.last_temperature = temperature
            
            automation_config = self.gateway.db.get_automation_config()
            auto_enabled = automation_config.get('auto_fan_enabled', True)
            
            if not auto_enabled or not self.automation_enabled:
                logger.debug(f"[AUTOMATION] Disabled, temperature: {temperature}°C")
                return True
            
            threshold = automation_config.get('default_temp_threshold', CONFIG['automation']['default_temp_threshold'])
            fan_device = automation_config.get('fan_device_id', CONFIG['automation']['fan_device_id'])
            
            should_be_on = (temperature >= threshold)
            
            if self.fan_state is None or (should_be_on and self.fan_state == 'off') or (not should_be_on and self.fan_state == 'on'):
                command = 'fan_on' if should_be_on else 'fan_off'
                
                logger.info(f"[AUTOMATION] Temperature {temperature}°C (threshold: {threshold}°C)")
                logger.info(f"[AUTOMATION] Triggering: {command}")
                
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
                    
                    self.log_automation_action(fan_device, command, temperature, threshold)
            
            return True
            
        except Exception as e:
            logger.error(f"[AUTOMATION] Error processing temperature: {e}")
            return False
    
    def send_command_response(self, device_id, command_id, success, status):
        """Send command response back to VPS"""
        response = {
            'device_id': device_id,
            'command_id': command_id,
            'success': success,
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = f"gateway/command/response/{device_id}"
        self.gateway.vps_mqtt.publish(topic, json.dumps(response), qos=1)
        
        logger.info(f"[REMOTE] Response sent: command_id={command_id}, success={success}, status={status}")
    
    def log_remote_access(self, device_id, user, reason, action, command_id):
        """Log remote access event to VPS - FIXED"""
        log_entry = {
            'method': 'remote_control',
            'device_id': device_id,
            'result': 'granted',
            'action': action,
            'initiated_by': user,
            'reason': reason,
            'command_id': command_id,
            'timestamp': datetime.now().isoformat()
        }
        
        self.gateway.forward_to_vps(device_id, 'access', log_entry)
        logger.info(f"[REMOTE] Logged {action} by {user} for {device_id}")
    
    def log_automation_action(self, device_id, action, temperature, threshold):
        """Log automation action to VPS - FIXED"""
        log_entry = {
            'alert_type': 'automation_trigger',
            'severity': 'info',
            'device_id': device_id,
            'action': action,
            'trigger': 'temperature_threshold',
            'value': temperature,
            'threshold': threshold,
            'message': f'Fan {action} due to temperature {temperature}°C (threshold: {threshold}°C)',
            'timestamp': datetime.now().isoformat()
        }
        
        self.gateway.forward_to_vps(device_id, 'alert', log_entry)
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
    """Main Gateway orchestrator - manages all communication"""
    
    def __init__(self):
        logger.info("=" * 60)
        logger.info("Initializing IoT Gateway...")
        logger.info("=" * 60)
        
        self.running = False
        self.local_mqtt = None
        self.vps_mqtt = None
        self.serial_conn = None
        self.local_connected = False
        self.vps_connected = False
        
        self.buffer = deque(maxlen=CONFIG['buffer_max_size'])
        self.buffer_lock = threading.Lock()
        
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'messages_buffered': 0,
            'remote_commands_executed': 0,
            'automation_triggers': 0,
            'uptime_start': datetime.now()
        }
        
        self.db = Database(CONFIG['db_path'])
        self.security = SecurityManager(CONFIG)
        self.remote_control = RemoteControlManager(self)
        
        logger.info(" Database loaded")
        logger.info(" Security manager initialized")
        logger.info(" Remote control manager initialized")
    
    # ============= LOCAL MQTT SETUP =============
    def setup_local_mqtt(self):
        """Configure and connect to local MQTT broker"""
        logger.info("Setting up Local MQTT Broker connection...")
        
        cfg = CONFIG['local_broker']
        self.local_mqtt = mqtt.Client(client_id='Gateway_Local')
        
        if cfg['use_tls']:
            try:
                self.local_mqtt.tls_set(
                    ca_certs=cfg['ca_cert'],
                    tls_version=ssl.PROTOCOL_TLSv1_2
                )
                self.local_mqtt.tls_insecure_set(False)
                logger.info(" Local TLS configured")
            except Exception as e:
                logger.error(f" Local TLS setup error: {e}")
                return
        
        self.local_mqtt.username_pw_set(cfg['username'], cfg['password'])
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
            logger.info(" Connected to Local MQTT Broker")
            self.local_connected = True
            
            client.subscribe(CONFIG['topics']['local_telemetry'], qos=1)
            client.subscribe(CONFIG['topics']['local_request'], qos=1)
            client.subscribe(CONFIG['topics']['local_status'], qos=1)
            
            logger.info(" Subscribed to local device topics")
        else:
            logger.error(f" Local MQTT connection failed with code: {rc}")
            self.local_connected = False
    
    def on_local_disconnect(self, client, userdata, rc):
        """Callback when disconnected from local MQTT"""
        logger.warning(f" Local MQTT disconnected (rc={rc})")
        self.local_connected = False
    
    def on_local_message(self, client, userdata, msg):
        """Handle messages from local devices"""
        try:
            self.stats['messages_received'] += 1
            
            parts = msg.topic.split('/')
            device_id = parts[2] if len(parts) >= 3 else 'unknown'
            
            try:
                payload = json.loads(msg.payload.decode())
            except:
                payload = {'raw': msg.payload.decode()}
            
            logger.debug(f"[LOCAL] {device_id} → {msg.topic}")
            
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
            logger.error(f" VPS TLS setup error: {e}")
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
            logger.info(" Connected to VPS MQTT Broker")
            self.vps_connected = True
            
            client.subscribe(CONFIG['topics']['vps_command'], qos=1)
            logger.info(" Subscribed to VPS command topics")
            
            self.send_gateway_status('online')
            self.flush_buffer()
        else:
            logger.error(f" VPS MQTT connection failed with code: {rc}")
            self.vps_connected = False
    
    def on_vps_disconnect(self, client, userdata, rc):
        """Callback when disconnected from VPS"""
        logger.warning(f" VPS MQTT disconnected (rc={rc})")
        self.vps_connected = False
    
    def on_vps_message(self, client, userdata, msg):
        """Handle commands from VPS"""
        try:
            payload = json.loads(msg.payload.decode())
            
            parts = msg.topic.split('/')
            
            if len(parts) >= 3 and parts[1] == 'command':
                device_id = parts[2]
                command = payload.get('cmd')
                
                logger.info(f"[VPS] Command received: '{command}' for {device_id}")
                
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
            logger.info(f" LoRa connected on {CONFIG['lora_port']}")
        except Exception as e:
            logger.error(f" LoRa connection failed: {e}")
            self.serial_conn = None
    
    # ============= MESSAGE HANDLERS =============
    def handle_telemetry(self, device_id, payload):
        """Process telemetry data from devices"""
        logger.debug(f"[TELEMETRY] {device_id}: {payload.get('msg_type')}")
        
        if device_id == CONFIG['automation']['temp_device_id']:
            self.remote_control.handle_temp_sensor_data(device_id, payload)
            self.stats['automation_triggers'] += 1
        
        self.forward_to_vps(device_id, 'telemetry', payload)
    
    def handle_request(self, device_id, payload):
        """Process authentication requests from devices"""
        logger.info(f"[REQUEST] {device_id}: {payload.get('cmd', 'unknown')}")
        
        if self.security.is_locked_out(device_id):
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'locked_out'})
            return
        
        if 'hmac' not in payload or 'body' not in payload:
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_format'})
            return
        
        if not verify_hmac(payload['body'], payload['hmac'], CONFIG['hmac_key']):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_signature'})
            
            self.forward_to_vps(device_id, 'alert', {
                'alert_type': 'security_alert',
                'severity': 'high',
                'message': 'HMAC verification failed',
                'timestamp': datetime.now().isoformat()
            })
            return
        
        try:
            body = json.loads(payload['body'])
        except:
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_json'})
            return
        
        if not self.security.validate_timestamp(body.get('ts', 0)):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'invalid_timestamp'})
            return
        
        if not self.security.validate_nonce(body.get('nonce', 0)):
            self.security.record_failed_attempt(device_id)
            self.send_local_response(device_id, {'cmd': 'LOCK', 'reason': 'replay_attack'})
            return
        
        if body.get('cmd') == 'unlock_request':
            self.handle_passkey_request(device_id, body)
    
    def handle_passkey_request(self, device_id, body):
        """Process keypad password authentication - FIXED"""
        password_hash = body.get('pw')
        
        is_valid, pwd_id = self.db.authenticate_passkey(password_hash)
        
        access_allowed, deny_reason = True, 'invalid_password'
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('passkey', pwd_id)
        
        log_entry = {
            'method': 'passkey',
            'device_id': device_id,
            'result': 'granted' if (is_valid and access_allowed) else 'denied',
            'timestamp': datetime.now().isoformat()
        }
        
        if is_valid and pwd_id:
            log_entry['password_id'] = pwd_id
        if not access_allowed:
            log_entry['deny_reason'] = deny_reason
        
        self.forward_to_vps(device_id, 'access', log_entry)
        
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
        """Process status updates from devices"""
        logger.debug(f"[STATUS] {device_id}: {payload.get('state')}")
        
        if 'command_id' in payload:
            self.remote_control.send_command_response(
                device_id,
                payload['command_id'],
                payload.get('success', True),
                payload.get('status', 'completed')
            )
        
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
        """Forward message to VPS with offline buffering - FIXED WITH DEBUG LOGGING"""
        
        logger.info(f"[DEBUG] forward_to_vps called: device_id={device_id}, msg_type={msg_type}")
        logger.info(f"[DEBUG] vps_connected={self.vps_connected}")
        
        valid_types = ['telemetry', 'status', 'access', 'alert']
        if msg_type not in valid_types:
            logger.error(f"[VPS] Invalid msg_type: {msg_type}. Must be one of {valid_types}")
            logger.error(f"[VPS] Payload: {payload}")
            return False
        
        vps_payload = {
            'gateway_id': 'Gateway1',
            'device_id': device_id,
            'timestamp': datetime.now().isoformat()
        }
        
        if msg_type == 'telemetry':
            sensor_data = payload.get('data', {})
            vps_payload['temperature'] = sensor_data.get('temperature')
            vps_payload['humidity'] = sensor_data.get('humidity')
            vps_payload['msg_type'] = payload.get('msg_type', 'unknown')
            vps_payload['data'] = payload
            
        elif msg_type == 'status':
            vps_payload['status'] = payload.get('state', 'unknown')
            vps_payload['sequence'] = payload.get('sequence')
            vps_payload['metadata'] = payload
            
        elif msg_type == 'access':
            vps_payload['method'] = payload.get('method', 'unknown')
            vps_payload['result'] = payload.get('result', 'unknown')
            vps_payload['password_id'] = payload.get('password_id')
            vps_payload['rfid_uid'] = payload.get('rfid_uid')
            vps_payload['deny_reason'] = payload.get('deny_reason')
            vps_payload['action'] = payload.get('action')
            vps_payload['initiated_by'] = payload.get('initiated_by')
            vps_payload['metadata'] = payload
            logger.info(f"[DEBUG] Built access payload: {vps_payload}")
            
        elif msg_type == 'alert':
            vps_payload['alert_type'] = payload.get('alert_type') or payload.get('event', 'unknown')
            vps_payload['severity'] = payload.get('severity', 'warning')
            vps_payload['value'] = payload.get('temperature') or payload.get('value')
            vps_payload['threshold'] = payload.get('threshold')
            vps_payload['message'] = payload.get('message', '')
            vps_payload['action'] = payload.get('action')
            vps_payload['trigger'] = payload.get('trigger')
            vps_payload['metadata'] = payload
        
        try:
            topic_key = f'vps_{msg_type}'
            logger.info(f"[DEBUG] Looking for topic key: {topic_key}")
            
            if topic_key not in CONFIG['topics']:
                logger.error(f"[VPS] Topic key not found: {topic_key}")
                logger.error(f"[VPS] Available topics: {list(CONFIG['topics'].keys())}")
                return False
            
            topic = CONFIG['topics'][topic_key]
            logger.info(f"[DEBUG] Topic resolved to: {topic}")
            
        except KeyError as e:
            logger.error(f"[VPS] No topic configured for msg_type: {msg_type}")
            logger.error(f"[VPS] KeyError: {e}")
            return False
        
        if self.vps_connected:
            logger.info(f"[DEBUG] VPS connected, calling publish_to_vps")
            self.publish_to_vps(topic, vps_payload)
        else:
            logger.warning(f"[DEBUG] VPS not connected, buffering message")
            with self.buffer_lock:
                self.buffer.append({'topic': topic, 'payload': vps_payload})
                self.stats['messages_buffered'] += 1
                logger.debug(f"[BUFFER] Message buffered (total: {len(self.buffer)})")
        
        return True
    
    def publish_to_vps(self, topic, payload):
        """Publish to VPS MQTT - WITH DEBUG LOGGING"""
        logger.info(f"[DEBUG] publish_to_vps called for topic: {topic}")
        
        try:
            if not self.vps_mqtt:
                logger.error("[VPS] vps_mqtt client is None!")
                return
            
            logger.info(f"[DEBUG] Attempting to publish to {topic}")
            logger.info(f"[DEBUG] Payload: {json.dumps(payload)[:200]}...")
            
            result = self.vps_mqtt.publish(topic, json.dumps(payload), qos=1)
            
            logger.info(f"[DEBUG] Publish result code: {result.rc}")
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['messages_sent'] += 1
                logger.info(f"[VPS]  Published to {topic}")
            else:
                logger.error(f"[VPS]  Publish failed with code: {result.rc}")
                
        except Exception as e:
            logger.error(f"[VPS]  Error publishing: {e}", exc_info=True)
    
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
    
    # ============= LORA HANDLING - FIXED =============
    def parse_lora_message(self, data):
        """Parse LoRa message from RFID gate - FIXED to include device_id"""
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
            
            # FIXED: Map device_type to actual device_id
            device_id = DEVICE_TYPE_TO_ID.get(device_type_n, f'unknown_device_{device_type_n}')
            
            if msg_type_n == 0x01:
                payload = {
                    'uid': ''.join(f'{b:02x}' for b in payload_data),
                    'uid_len': len(payload_data)
                }
            elif msg_type_n == 0x06:
                payload = {'status': payload_data.decode('utf-8', errors='ignore')}
            else:
                payload = {'raw': payload_data.hex()}
            
            return {
                'device_id': device_id,
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
            logger.error(f"Error parsing LoRa message: {e}", exc_info=True)
            return None
    
    def process_lora_message(self, message):
        """Process LoRa message and route to appropriate handler - FIXED"""
        msg_type = message['header']['msg_type']
        device_id = message['device_id']
        
        logger.debug(f"[LoRa] Processing {msg_type} from {device_id}")
        
        if msg_type == 'rfid_scan':
            return self.handle_rfid_scan(message)
        elif msg_type == 'gate_status':
            return self.handle_gate_status(message)
        else:
            logger.warning(f"[LoRa] Unknown message type: {msg_type}")
        
        return None
    
    def handle_rfid_scan(self, message):
        """Handle RFID card scan from LoRa - FIXED WITH DEBUG LOGGING"""
        device_id = message['device_id']
        uid = message['payload']['uid']
        
        logger.info(f"[RFID] {device_id} scanned card: {uid}")
        logger.info(f"[DEBUG] vps_connected = {self.vps_connected}")
        
        is_valid = self.db.authenticate_rfid(uid)
        logger.info(f"[DEBUG] Card authentication result: {is_valid}")
        
        access_allowed = False
        deny_reason = 'invalid_card'
        
        if is_valid:
            access_allowed, deny_reason = self.db.check_access_rules('rfid', uid)
            logger.info(f"[DEBUG] Access rules check: allowed={access_allowed}, reason={deny_reason}")
        
        log_entry = {
            'method': 'rfid',
            'device_id': device_id,
            'result': 'granted' if (is_valid and access_allowed) else 'denied',
            'rfid_uid': uid,
            'timestamp': datetime.now().isoformat()
        }
        
        if not access_allowed:
            log_entry['deny_reason'] = deny_reason
        
        logger.info(f"[DEBUG] About to call forward_to_vps with log_entry: {log_entry}")
        
        # Call forward_to_vps and check return value
        result = self.forward_to_vps(device_id, 'access', log_entry)
        logger.info(f"[DEBUG] forward_to_vps returned: {result}")
        
        if is_valid and access_allowed:
            logger.info(f"[RFID] {uid}: GRANTED")
            self.security.record_success(device_id)
            response_status = "GRANT"
            
            if uid in self.db.devices.get('rfid_cards', {}):
                self.db.devices['rfid_cards'][uid]['last_used'] = datetime.now().isoformat()
                self.db.save_all()
        else:
            logger.warning(f"[RFID] {uid}: DENIED ({deny_reason})")
            self.security.record_failed_attempt(device_id)
            response_status = "DENY5"
        
        return response_status
    
    def handle_gate_status(self, message):
        """Handle status update from RFID gate - FIXED"""
        device_id = message['device_id']
        status = message['payload'].get('status', 'unknown')
        
        logger.info(f"[LoRa] {device_id} status: {status}")
        
        status_payload = {
            'state': status,
            'sequence': message['header']['seq'],
            'device_type': message['header']['device_type'],
            'timestamp': datetime.now().isoformat()
        }
        
        self.forward_to_vps(device_id, 'status', status_payload)
        
        return None
    
    def send_lora_response(self, device_id, status):
        """Send response back to LoRa device - FIXED"""
        try:
            response_str = status
            
            buffer = bytearray()
            buffer.append(0xC0)
            buffer.append(0x00)
            buffer.append(0x00)
            buffer.append(0x00)
            buffer.append(0x00)
            buffer.append(0x17)
            buffer.append(len(response_str))
            buffer.extend(response_str.encode('utf-8'))
            
            if self.serial_conn:
                self.serial_conn.write(buffer)
                logger.info(f"[LoRa] Response sent to {device_id}: {status}")
            else:
                logger.error("[LoRa] Serial connection not available")
                
        except Exception as e:
            logger.error(f"[LoRa] Error sending response: {e}")
    
    # ============= MAIN LOOP =============
    def run(self):
        """Main gateway loop"""
        self.setup_local_mqtt()
        self.setup_vps_mqtt()
        self.setup_serial()
        
        time.sleep(2)
        
        logger.info("Gateway initialization complete")
        logger.info("=" * 60)
        logger.info("IoT Gateway Started Successfully")
        logger.info(f"Local MQTT: {CONFIG['local_broker']['host']}:{CONFIG['local_broker']['port']}")
        logger.info(f"VPS MQTT: {CONFIG['vps_broker']['host']}:{CONFIG['vps_broker']['port']}")
        logger.info("Features: Remote Control (Keypad, RFID, Fan), Automation Engine")
        logger.info("=" * 60)
        
        self.running = True
        
        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        
        cleanup_thread = threading.Thread(target=self.cleanup_loop, daemon=True)
        cleanup_thread.start()
        
        lora_buffer = bytearray()
        last_stats_log = time.time()
        
        while self.running:
            try:
                if time.time() - last_stats_log > 300:
                    logger.info(
                        f"[STATS] Received: {self.stats['messages_received']}, "
                        f"Sent: {self.stats['messages_sent']}, "
                        f"Buffered: {len(self.buffer)}, "
                        f"Remote Cmds: {self.stats['remote_commands_executed']}, "
                        f"Auto Triggers: {self.stats['automation_triggers']}, "
                        f"Local: {'OK' if self.local_connected else 'DOWN'}, "
                        f"VPS: {'OK' if self.vps_connected else 'DOWN'}"
                    )
                    last_stats_log = time.time()
                
                if self.serial_conn and self.serial_conn.in_waiting > 0:
                    new_data = self.serial_conn.read(self.serial_conn.in_waiting)
                    lora_buffer += new_data
                    
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
                                device_id = message['device_id']
                                self.send_lora_response(device_id, response)
                        
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
                time.sleep(60)
                self.remote_control.cleanup_old_commands()
            except Exception as e:
                logger.error(f"[CLEANUP] Error: {e}")
    
    def cleanup(self):
        """Cleanup all connections on shutdown"""
        logger.info("[SHUTDOWN] Cleaning up connections...")
        
        if self.vps_connected:
            self.send_gateway_status('offline')
        
        if self.local_mqtt:
            self.local_mqtt.loop_stop()
            self.local_mqtt.disconnect()
        
        if self.vps_mqtt:
            self.vps_mqtt.loop_stop()
            self.vps_mqtt.disconnect()
        
        if self.serial_conn:
            self.serial_conn.close()
        
        logger.info("[SHUTDOWN] Gateway stopped cleanly")

# ============= MAIN ENTRY POINT =============
def main():
    """Application entry point"""
    
    logger.info("Checking prerequisites...")
    
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
            logger.error(f"   {cert}")
        logger.error("")
        logger.error("Please ensure all certificates are in place")
        logger.error("=" * 60)
        return
    
    db_file = os.path.join(CONFIG['db_path'], CONFIG['devices_db'])
    if not os.path.exists(db_file):
        logger.error(f"Database not found: {db_file}")
        return
    
    logger.info(" Prerequisites check passed")
    
    gateway = Gateway()
    
    try:
        gateway.run()
    except Exception as e:
        logger.critical(f"Gateway startup failed: {e}", exc_info=True)
    finally:
        gateway.cleanup()

if __name__ == "__main__":
    main()