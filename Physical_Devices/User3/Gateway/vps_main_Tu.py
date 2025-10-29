#!/usr/bin/env python3
"""
Gateway 3 - User 3 (Anh) - Temperature Sensor + Fan Control
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
    'gateway_id': 'Gateway3',
    'user_id': '00003',
    
    # Local Broker (for Temp + Fan devices)
    'local_broker': {
        'host': '192.168.1.205',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',  # Same CA as VPS
        'client_cert': './certs/gateway3.cert.pem',  # Client certificate for Gateway2
        'client_key': './certs/gateway3.key.pem',   # Client key for Gateway2
        'username': 'Gateway3',                     # MQTT username
        'password': '125',         # MQTT password (replace with actual password)
    },
    
    # VPS Broker (mTLS)
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway3.cert.pem',
        'client_key': './certs/gateway3.key.pem',
    },
    
    # VPS API for sync
    'vps_api_url': 'http://159.223.63.61:3000',
    
    # MQTT Topics
    'topics': {
        'local_temp_telemetry': 'home/devices/temp_01/telemetry',
        'local_temp_status': 'home/devices/temp_01/status',
        'local_fan_command': 'home/devices/fan_01/command',
        'local_fan_telemetry': 'home/devices/fan_01/telemetry',
        'local_fan_status': 'home/devices/fan_01/status',
        'vps_telemetry': 'gateway/Gateway3/telemetry/{device_id}',
        'vps_status': 'gateway/Gateway3/status/{device_id}',
        'vps_gateway_status': 'gateway/Gateway3/status/gateway',
        'sync_trigger': 'gateway/Gateway3/sync/trigger',  # NEW: Sync trigger
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'logs_db': 'logs.json',
    'settings_db': 'settings.json',
    'heartbeat_interval': 300,
    
    # Automation settings
    'automation': {
        'temp_threshold': 30.0,
        'auto_fan_enabled': True,
    }
}

# ============= DATABASE MANAGER =============
class DatabaseManager:
    def __init__(self, db_path, devices_db, logs_db, settings_db):
        self.db_path = db_path
        self.devices_file = os.path.join(db_path, devices_db)
        self.logs_file = os.path.join(db_path, logs_db)
        self.settings_file = os.path.join(db_path, settings_db)
        
        os.makedirs(db_path, exist_ok=True)
        
        self.devices_data = self.load_devices()
        self.logs_data = self.load_logs()
        self.settings_data = self.load_settings()
        
    def load_devices(self):
        if os.path.exists(self.devices_file):
            with open(self.devices_file, 'r') as f:
                return json.load(f)
        return {'passwords': {}, 'rfid_cards': {}, 'devices': {}}
    
    def load_logs(self):
        if os.path.exists(self.logs_file):
            with open(self.logs_file, 'r') as f:
                return json.load(f)
        return []
    
    def load_settings(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        return {
            'automation': {
                'auto_fan_enabled': True,
                'temp_threshold': 30.0
            }
        }
    
    def save_devices(self):
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def save_logs(self):
        # Keep only last 1000 logs
        if len(self.logs_data) > 1000:
            self.logs_data = self.logs_data[-1000:]
        
        with open(self.logs_file, 'w') as f:
            json.dump(self.logs_data, f, indent=2)
    
    def save_settings(self):
        with open(self.settings_file, 'w') as f:
            json.dump(self.settings_data, f, indent=2)
    
    def add_log(self, log_type, event, **kwargs):
        """Add log entry"""
        log_entry = {
            'type': log_type,
            'event': event,
            'timestamp': datetime.now().isoformat(),
            **kwargs
        }
        self.logs_data.append(log_entry)
        self.save_logs()

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
        
        # Automation state
        self.last_temperature = None
        self.fan_auto_on = False
        
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
            client_id=f"{self.config['gateway_id']}_vps",
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
                self.config['topics']['local_temp_telemetry'],
                self.config['topics']['local_temp_status'],
                self.config['topics']['local_fan_telemetry'],
                self.config['topics']['local_fan_status']
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
        """Handle messages from local devices"""
        try:
            data = json.loads(msg.payload.decode())
            
            if 'temp_01/telemetry' in msg.topic:
                self.handle_temperature_data(data)
            elif 'temp_01/status' in msg.topic:
                self.forward_status_to_vps('temp_01', data)
            elif 'fan_01/telemetry' in msg.topic:
                self.forward_telemetry_to_vps('fan_01', data)
            elif 'fan_01/status' in msg.topic:
                self.forward_status_to_vps('fan_01', data)
                
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
    
    def handle_temperature_data(self, data):
        """Handle temperature telemetry and automation"""
        temperature = data.get('temperature')
        humidity = data.get('humidity')
        
        if temperature is not None:
            self.last_temperature = temperature
            logger.info(f"[TEMP] {temperature}°C, {humidity}% RH")
            
            # Forward to VPS
            self.forward_telemetry_to_vps('temp_01', data)
            
            # Check automation
            auto_enabled = self.db_manager.settings_data.get('automation', {}).get('auto_fan_enabled', True)
            threshold = self.db_manager.settings_data.get('automation', {}).get('temp_threshold', 30.0)
            
            if auto_enabled:
                if temperature > threshold and not self.fan_auto_on:
                    logger.warning(f"[AUTO] Temperature {temperature}°C > {threshold}°C - Turning fan ON")
                    self.control_fan('on', 'auto')
                    self.fan_auto_on = True
                    
                    # Log alert
                    self.db_manager.add_log('alert', 'high_temperature', 
                                           device_id='temp_01', 
                                           temperature=temperature)
                    
                elif temperature <= threshold and self.fan_auto_on:
                    logger.info(f"[AUTO] Temperature {temperature}°C <= {threshold}°C - Turning fan OFF")
                    self.control_fan('off', 'auto')
                    self.fan_auto_on = False
    
    def control_fan(self, action, source='manual'):
        """Send fan control command"""
        command = {
            'cmd': 'set_power',
            'state': action,
            'source': source,
            'timestamp': time.time()
        }
        
        topic = self.config['topics']['local_fan_command']
        self.local_client.publish(topic, json.dumps(command))
        logger.info(f"[FAN] Command sent: {action} ({source})")
    
    def forward_telemetry_to_vps(self, device_id, data):
        """Forward device telemetry to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'timestamp': datetime.now().isoformat(),
            'data': data
        }
        
        topic = self.config['topics']['vps_telemetry'].format(device_id=device_id)
        self.publish_to_vps(topic, payload)
    
    def forward_status_to_vps(self, device_id, data):
        """Forward device status to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'status': data.get('state', 'unknown'),
            'timestamp': datetime.now().isoformat(),
            'metadata': data
        }
        
        topic = self.config['topics']['vps_status'].format(device_id=device_id)
        self.publish_to_vps(topic, payload)
    
    def publish_to_vps(self, topic, payload):
        """Publish to VPS broker"""
        if not self.connected_vps:
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
            'last_temperature': self.last_temperature,
            'fan_auto_on': self.fan_auto_on,
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
            logger.info(f" Heartbeat | Temp: {mqtt_manager.last_temperature}°C | Fan Auto: {mqtt_manager.fan_auto_on} | Syncs: {sync_stats['sync_count']}")
            
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            time.sleep(interval)

# ============= MAIN =============
def main():
    logger.info("=" * 70)
    logger.info("  Gateway 3 (User 3 - Anh) - Temp/Fan with Database Sync")
    logger.info("=" * 70)
    
    # Initialize components
    db_manager = DatabaseManager(
        CONFIG['db_path'],
        CONFIG['devices_db'],
        CONFIG['logs_db'],
        CONFIG['settings_db']
    )
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
    logger.info(" Gateway 3 Running - Database syncing every 5 seconds")
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