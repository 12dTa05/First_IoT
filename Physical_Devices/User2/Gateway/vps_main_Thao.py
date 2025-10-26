import time
import json
import os
from datetime import datetime
from collections import deque
import paho.mqtt.client as mqtt
import ssl
import threading
import logging
from logging.handlers import RotatingFileHandler

# ============= LOGGING SETUP =============
def setup_logging():
    log_dir = './logs'
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('gateway2')
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'gateway2.log'),
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
    'gateway_id': 'Gateway2',
    'user_id': 'user2',
    
    'local_broker': {
        'host': '192.168.1.205',  # Local broker trên cùng máy với gateway
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'username': 'Gateway2',
        'password': '125'
    },
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'client_id': 'Gateway2',
        'ca_cert': './certs/ca.cert.pem',
        'cert_file': './certs/gateway2.cert.pem',
        'key_file': './certs/gateway2.key.pem',
    },
    
    'topics': {
        # Local topics - Passkey device connects via WiFi/MQTT
        'local_passkey_request': 'home/devices/passkey_01/request',
        'local_passkey_status': 'home/devices/passkey_01/status',
        'local_passkey_response': 'home/devices/passkey_01/response',
        
        # VPS topics
        'vps_status': 'gateway/Gateway2/status/{device_id}',
        'vps_access': 'gateway/Gateway2/access/{device_id}',
        'vps_command': 'gateway/Gateway2/command/#',
        'vps_gateway_status': 'gateway/Gateway2/status/gateway',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'heartbeat_interval': 60,
}

# ============= DATABASE MANAGER =============
class DatabaseManager:
    def __init__(self, db_path, devices_db):
        self.db_path = db_path
        self.devices_file = os.path.join(db_path, devices_db)
        os.makedirs(db_path, exist_ok=True)
        self.devices_data = self.load_devices()
        
    def load_devices(self):
        if os.path.exists(self.devices_file):
            with open(self.devices_file, 'r') as f:
                return json.load(f)
        return {'passwords': {}, 'devices': {}}
    
    def save_devices(self):
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def verify_password(self, password_id):
        """Verify if password is valid and active"""
        password = self.devices_data.get('passwords', {}).get(password_id)
        
        if not password:
            return False, 'invalid_password'
        
        if not password.get('active', False):
            return False, 'inactive_password'
        
        # Check expiration
        expires_at = password.get('expires_at')
        if expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.now(expire_time.tzinfo) > expire_time:
                    return False, 'expired_password'
            except:
                pass
        
        return True, None

# ============= MQTT MANAGER =============
class MQTTManager:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.local_client = None
        self.vps_client = None
        self.connected_local = False
        self.connected_vps = False
        
    def setup_local_broker(self):
        """Connect to local MQTT broker for Passkey device"""
        self.local_client = mqtt.Client(client_id=f"{self.config['gateway_id']}_local")
        
        if self.config['local_broker']['use_tls']:
            self.local_client.tls_set(
                ca_certs=self.config['local_broker']['ca_cert'],
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
        
        self.local_client.username_pw_set(
            self.config['local_broker']['username'],
            self.config['local_broker']['password']
        )
        
        self.local_client.on_connect = self.on_local_connect
        self.local_client.on_disconnect = self.on_local_disconnect
        self.local_client.on_message = self.on_local_message
        
        try:
            self.local_client.connect(
                self.config['local_broker']['host'],
                self.config['local_broker']['port'],
                60
            )
            self.local_client.loop_start()
            logger.info(" Connected to Local Broker")
        except Exception as e:
            logger.error(f" Failed to connect to Local Broker: {e}")
    
    def setup_vps_broker(self):
        """Connect to VPS MQTT broker"""
        self.vps_client = mqtt.Client(client_id=self.config['vps_broker']['client_id'])
        
        self.vps_client.tls_set(
            ca_certs=self.config['vps_broker']['ca_cert'],
            certfile=self.config['vps_broker']['cert_file'],
            keyfile=self.config['vps_broker']['key_file'],
            tls_version=ssl.PROTOCOL_TLSv1_2
        )
        
        self.vps_client.on_connect = self.on_vps_connect
        self.vps_client.on_disconnect = self.on_vps_disconnect
        self.vps_client.on_message = self.on_vps_message
        
        try:
            self.vps_client.connect(
                self.config['vps_broker']['host'],
                self.config['vps_broker']['port'],
                60
            )
            self.vps_client.loop_start()
            logger.info(" Connected to VPS Broker")
        except Exception as e:
            logger.error(f" Failed to connect to VPS Broker: {e}")
    
    def on_local_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_local = True
            logger.info(" Local Broker Connected")
            
            # Subscribe to passkey topics
            topics = [
                self.config['topics']['local_passkey_request'],
                self.config['topics']['local_passkey_status']
            ]
            for topic in topics:
                client.subscribe(topic)
                logger.info(f" Subscribed to: {topic}")
        else:
            logger.error(f" Local Broker Connection Failed: {rc}")
    
    def on_local_disconnect(self, client, userdata, rc):
        self.connected_local = False
        logger.warning(" Local Broker Disconnected")
    
    def on_local_message(self, client, userdata, msg):
        """Handle messages from local devices (Passkey)"""
        try:
            logger.info(f" Local message: {msg.topic}")
            data = json.loads(msg.payload.decode())
            
            if 'request' in msg.topic:
                self.handle_passkey_request(data)
            elif 'status' in msg.topic:
                self.forward_status_to_vps(data)
                
        except Exception as e:
            logger.error(f" Error processing local message: {e}")
    
    def handle_passkey_request(self, data):
        """Handle passkey access request from local device"""
        password_id = data.get('password_id')
        nonce = data.get('nonce')
        
        logger.info(f" Passkey request: {password_id}")
        
        # Verify password from local database
        granted, deny_reason = self.db_manager.verify_password(password_id)
        
        # Send response back to passkey device (local)
        response = {
            'password_id': password_id,
            'nonce': nonce,
            'result': 'granted' if granted else 'denied'
        }
        
        topic = self.config['topics']['local_passkey_response']
        self.local_client.publish(topic, json.dumps(response))
        
        result_text = ' Access GRANTED' if granted else ' Access DENIED'
        logger.info(f"{result_text}: {password_id} {f'({deny_reason})' if deny_reason else ''}")
        
        # Forward access log to VPS
        access_log = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'passkey_01',
            'method': 'passkey',
            'password_id': password_id,
            'result': 'granted' if granted else 'denied',
            'deny_reason': deny_reason,
            'timestamp': datetime.now().isoformat()
        }
        
        vps_topic = self.config['topics']['vps_access'].format(device_id='passkey_01')
        self.publish_to_vps(vps_topic, access_log)
    
    def forward_status_to_vps(self, data):
        """Forward device status from local to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'passkey_01',
            'status': data.get('status', 'unknown'),
            'timestamp': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['vps_status'].format(device_id='passkey_01')
        self.publish_to_vps(topic, payload)
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            logger.info(" VPS Broker Connected")
            
            topic = self.config['topics']['vps_command']
            client.subscribe(topic)
            logger.info(f" Subscribed to: {topic}")
            
            self.publish_gateway_status('online')
        else:
            logger.error(f" VPS Broker Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        self.connected_vps = False
        logger.warning(" VPS Broker Disconnected")
    
    def on_vps_message(self, client, userdata, msg):
        """Handle commands from VPS"""
        try:
            logger.info(f" VPS Command: {msg.topic}")
            data = json.loads(msg.payload.decode())
            
            # Commands for passkey can be forwarded here if needed
            logger.info(f"Command data: {data}")
        except Exception as e:
            logger.error(f" Error processing VPS message: {e}")
    
    def publish_to_vps(self, topic, payload):
        if self.connected_vps:
            self.vps_client.publish(topic, json.dumps(payload), qos=1)
            logger.info(f" Published to VPS: {topic}")
        else:
            logger.warning(" VPS not connected, message buffered locally")
    
    def publish_gateway_status(self, status):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        topic = self.config['topics']['vps_gateway_status']
        self.publish_to_vps(topic, payload)

# ============= HEARTBEAT =============
def heartbeat_loop(mqtt_manager, interval):
    while True:
        try:
            mqtt_manager.publish_gateway_status('online')
            logger.info(" Heartbeat sent")
            time.sleep(interval)
        except Exception as e:
            logger.error(f" Heartbeat error: {e}")
            time.sleep(interval)

# ============= MAIN =============
def main():
    logger.info("=" * 60)
    logger.info(" Starting Gateway 2 (User 2 - Passkey via MQTT)")
    logger.info("=" * 60)
    
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
    mqtt_manager = MQTTManager(CONFIG, db_manager)
    
    # Connect to both local and VPS brokers
    logger.info(" Connecting to Local Broker...")
    mqtt_manager.setup_local_broker()
    time.sleep(2)
    
    logger.info(" Connecting to VPS Broker...")
    mqtt_manager.setup_vps_broker()
    time.sleep(2)
    
    # Start heartbeat
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(mqtt_manager, CONFIG['heartbeat_interval'])
    )
    heartbeat_thread.daemon = True
    heartbeat_thread.start()
    
    logger.info("=" * 60)
    logger.info(" Gateway 2 Running Successfully")
    logger.info(" Local: Connected to " + CONFIG['local_broker']['host'])
    logger.info("  VPS: Connected to " + CONFIG['vps_broker']['host'])
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(" Shutting down Gateway 2...")
        mqtt_manager.local_client.loop_stop()
        mqtt_manager.vps_client.loop_stop()
        logger.info(" Gateway 2 stopped")

if __name__ == '__main__':
    main()