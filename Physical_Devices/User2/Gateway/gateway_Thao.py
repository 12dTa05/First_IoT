#!/usr/bin/env python3
"""
Gateway 2 - User 2 (Thao) - Passkey Door via WiFi
With Database Sync every 5 seconds
"""

import paho.mqtt.client as mqtt
import ssl
import json
import os
import time
import logging
from datetime import datetime
from threading import Thread
from database_sync_manager import DatabaseSyncManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============= CONFIGURATION =============
CONFIG = {
    'gateway_id': 'Gateway2',
    'user_id': '00002',
    
    # Local Broker (for Passkey device)
    'local_broker': {
        'host': '192.168.1.205',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',  # Same CA as VPS
        'client_cert': './certs/gateway2.cert.pem',  # Client certificate for Gateway2
        'client_key': './certs/gateway2.key.pem',   # Client key for Gateway2
        'username': 'Gateway2',                     # MQTT username
        'password': '125',         # MQTT password (replace with actual password)
    },
    
    # VPS Broker (mTLS)
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway2.cert.pem',
        'client_key': './certs/gateway2.key.pem',
    },
    
    # VPS API for sync
    'vps_api_url': 'http://159.223.63.61:3000',
    
    # MQTT Topics
    'topics': {
        'local_passkey_request': 'home/devices/passkey_01/request',
        'local_passkey_response': 'home/devices/passkey_01/command',
        'local_passkey_status': 'home/devices/passkey_01/status',
        'vps_access': 'gateway/Gateway2/access/{device_id}',
        'vps_status': 'gateway/Gateway2/status/{device_id}',
        'vps_gateway_status': 'gateway/Gateway2/status/gateway',
        'sync_trigger': 'gateway/Gateway2/sync/trigger',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'heartbeat_interval': 300,
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
        return {'passwords': {}, 'rfid_cards': {}, 'devices': {}}
    
    def save_devices(self):
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def verify_password(self, password_hash):
        """Verify password hash against stored hashes"""
        passwords = self.devices_data.get('passwords', {})
        
        for password_id, password_data in passwords.items():
            if password_data.get('hash') == password_hash:
                if not password_data.get('active', False):
                    return False, 'inactive_password', password_id
                
                # Check expiration
                expires_at = password_data.get('expires_at')
                if expires_at:
                    try:
                        expire_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                        if datetime.now(expire_time.tzinfo) > expire_time:
                            return False, 'expired_password', password_id
                    except:
                        pass
                
                # Update last_used
                password_data['last_used'] = datetime.now().isoformat()
                self.save_devices()
                
                return True, None, password_id
        
        return False, 'invalid_password', None

# ============= MQTT MANAGER =============
class MQTTManager:
    def __init__(self, config, db_manager, sync_manager=None):
        self.config = config
        self.db_manager = db_manager
        self.sync_manager = sync_manager
        self.local_client = None
        self.vps_client = None
        self.connected_local = False
        self.connected_vps = False
        
    def setup_local_broker(self):
        """Connect to local MQTT broker for Passkey device"""
        self.local_client = mqtt.Client(client_id=f"{self.config['gateway_id']}")
        
        if self.config['local_broker']['use_tls']:
            self.local_client.tls_set(
                ca_certs=self.config['local_broker']['ca_cert'],
                certfile=self.config['local_broker']['client_cert'],
                keyfile=self.config['local_broker']['client_key'],
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
        
        # Set username and password for authentication
        self.local_client.username_pw_set(
            username=self.config['local_broker']['username'],
            password=self.config['local_broker']['password']
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
            time.sleep(1)
            return True
        except Exception as e:
            logger.error(f" Local Broker Connection Failed: {e}")
            return False
    
    def setup_vps_broker(self):
        """Connect to VPS MQTT broker with mTLS"""
        self.vps_client = mqtt.Client(
            client_id=f"{self.config['gateway_id']}",
            clean_session=False
        )
        
        if self.config['vps_broker']['use_tls']:
            self.vps_client.tls_set(
                ca_certs=self.config['vps_broker']['ca_cert'],
                certfile=self.config['vps_broker']['client_cert'],
                keyfile=self.config['vps_broker']['client_key'],
                cert_reqs=ssl.CERT_REQUIRED,
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
            time.sleep(2)
            return True
        except Exception as e:
            logger.error(f" VPS Broker Connection Failed: {e}")
            return False
    
    def on_local_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_local = True
            logger.info(" Connected to Local Broker")
            
            topics = [
                self.config['topics']['local_passkey_request'],
                self.config['topics']['local_passkey_status']
            ]
            for topic in topics:
                client.subscribe(topic)
                logger.info(f" Subscribed: {topic}")
        else:
            logger.error(f" Local Broker Connection Failed: {rc}")
    
    def on_local_disconnect(self, client, userdata, rc):
        self.connected_local = False
        logger.warning(" Disconnected from Local Broker")
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            logger.info(" Connected to VPS Broker")
            
            # Subscribe to sync trigger
            sync_topic = self.config['topics']['sync_trigger']
            client.subscribe(sync_topic)
            logger.info(f" Subscribed to sync trigger: {sync_topic}")
        else:
            logger.error(f" VPS Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        self.connected_vps = False
        logger.warning(" Disconnected from VPS Broker")
    
    def on_local_message(self, client, userdata, msg):
        """Handle messages from local Passkey device"""
        try:
            if 'request' in msg.topic:
                data = json.loads(msg.payload.decode())
                self.handle_passkey_request(data)
            elif 'status' in msg.topic:
                data = json.loads(msg.payload.decode())
                self.forward_status_to_vps(data)
        except Exception as e:
            logger.error(f"Error processing local message: {e}")
    
    def on_vps_message(self, client, userdata, msg):
        """Handle messages from VPS (sync triggers, commands)"""
        try:
            if 'sync/trigger' in msg.topic and self.sync_manager:
                data = json.loads(msg.payload.decode())
                logger.info(f" Sync trigger received: {data.get('reason', 'unknown')}")
                self.sync_manager.trigger_immediate_sync()
        except Exception as e:
            logger.error(f"Error processing VPS message: {e}")
    
    def handle_passkey_request(self, data):
        """Handle passkey unlock request from device"""
        password_hash = data.get('pw')
        nonce = data.get('nonce')
        
        logger.info(f"[PASSKEY] Unlock request received")
        
        # Verify password using synced database
        granted, deny_reason, password_id = self.db_manager.verify_password(password_hash)
        
        # Send response to device
        response = {
            'cmd': 'UNLOCK' if granted else 'LOCK',
            'nonce': nonce
        }
        
        if not granted:
            response['reason'] = deny_reason
        
        topic = self.config['topics']['local_passkey_response']
        self.local_client.publish(topic, json.dumps(response))
        
        # Log access to VPS
        access_log = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'passkey_01',
            'password_id': password_id,
            'result': 'granted' if granted else 'denied',
            'method': 'passkey',
            'deny_reason': deny_reason,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['vps_access'].format(device_id='passkey_01')
        self.publish_to_vps(topic, access_log)
        
        if granted:
            logger.info(f"[PASSKEY] ACCESS GRANTED")
        else:
            logger.warning(f"[PASSKEY] ACCESS DENIED ({deny_reason})")
    
    def forward_status_to_vps(self, data):
        """Forward device status to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'passkey_01',
            'status': data.get('state', 'unknown'),
            'timestamp': datetime.now().isoformat(),
            'metadata': data
        }
        
        topic = self.config['topics']['vps_status'].format(device_id='passkey_01')
        self.publish_to_vps(topic, payload)
    
    def publish_to_vps(self, topic, payload):
        """Publish to VPS broker"""
        if not self.connected_vps:
            logger.warning(" Cannot publish - VPS not connected")
            return False
        
        try:
            payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            result = self.vps_client.publish(topic, payload_str, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f" Published to VPS: {topic}")
                return True
        except Exception as e:
            logger.error(f"Error publishing to VPS: {e}")
        
        return False
    
    def publish_gateway_status(self, status):
        """Publish gateway heartbeat"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        topic = self.config['topics']['vps_gateway_status']
        return self.publish_to_vps(topic, payload)

# ============= HEARTBEAT =============
def heartbeat_loop(mqtt_manager, sync_manager, interval):
    """Send periodic heartbeat with sync stats"""
    while True:
        try:
            mqtt_manager.publish_gateway_status('online')
            
            sync_stats = sync_manager.get_stats()
            logger.info(f" Heartbeat | Syncs: {sync_stats['sync_count']} | Errors: {sync_stats['sync_errors']} | Version: {sync_stats['current_version']}")
            
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            time.sleep(interval)

# ============= MAIN =============
def main():
    logger.info("=" * 70)
    logger.info("  Gateway 2 (User 2 - Thao) - Passkey with Database Sync")
    logger.info("=" * 70)
    
    # Initialize components
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
    logger.info(" Database Manager Initialized")
    
    sync_manager = DatabaseSyncManager(CONFIG, db_manager)
    logger.info(" Sync Manager Initialized")
    
    mqtt_manager = MQTTManager(CONFIG, db_manager, sync_manager)
    
    # Connect to brokers
    logger.info(" Connecting to Local Broker...")
    if not mqtt_manager.setup_local_broker():
        logger.error("Failed to connect to local broker. Exiting.")
        return
    
    logger.info(" Connecting to VPS Broker...")
    if not mqtt_manager.setup_vps_broker():
        logger.error("Failed to connect to VPS. Exiting.")
        return
    
    # Start sync service
    logger.info(" Starting Database Sync Service (5s interval)...")
    sync_manager.start()
    time.sleep(2)
    
    # Start heartbeat
    logger.info(" Starting Heartbeat Thread...")
    heartbeat_thread = Thread(
        target=heartbeat_loop,
        args=(mqtt_manager, sync_manager, CONFIG['heartbeat_interval']),
        daemon=True
    )
    heartbeat_thread.start()
    
    logger.info("=" * 70)
    logger.info(" Gateway 2 Running - Database syncing every 5 seconds")
    logger.info("=" * 70)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\ Shutdown signal received")
        sync_manager.stop()
        logger.info(" Gateway stopped")

if __name__ == '__main__':
    main()