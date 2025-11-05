import paho.mqtt.client as mqtt
import ssl
import json
import os
import time
import logging
from datetime import datetime
from threading import Thread, Event
from database_sync_manager import DatabaseSyncManager
from timestamp_utils import now_compact

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
    
    'local_broker': {
        'host': '192.168.1.205',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway3.cert.pem',
        'client_key': './certs/gateway3.key.pem',
        'username': 'Gateway3',
        'password': '125',
    },
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway3.cert.pem',
        'client_key': './certs/gateway3.key.pem',
    },
    
    'vps_api_url': 'http://159.223.63.61:3000',
    
    'topics': {
        'local_temp_telemetry': 'home/devices/temp_01/telemetry',
        'local_temp_status': 'home/devices/temp_01/status',
        'local_fan_command': 'home/devices/fan_01/command',
        'local_fan_telemetry': 'home/devices/fan_01/telemetry',
        'local_fan_status': 'home/devices/fan_01/status',
        'vps_telemetry': 'gateway/Gateway3/telemetry/{device_id}',
        'vps_status': 'gateway/Gateway3/status/{device_id}',
        'vps_gateway_status': 'gateway/Gateway3/status/gateway',
        'sync_trigger': 'gateway/Gateway3/sync/trigger',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'logs_db': 'logs.json',
    'settings_db': 'settings.json',
    'heartbeat_interval': 30,  # Changed from 300 to 30 seconds
    
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
        backup_file = f"{self.devices_file}.backup"
        if os.path.exists(self.devices_file):
            import shutil
            shutil.copy2(self.devices_file, backup_file)
        
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def save_logs(self):
        if len(self.logs_data) > 1000:
            self.logs_data = self.logs_data[-1000:]
        
        with open(self.logs_file, 'w') as f:
            json.dump(self.logs_data, f, indent=2)
    
    def save_settings(self):
        with open(self.settings_file, 'w') as f:
            json.dump(self.settings_data, f, indent=2)
    
    def add_log(self, log_type, event, **kwargs):
        log_entry = {
            'type': log_type,
            'event': event,
            'timestamp': now_compact(),
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
        self.connection_lost_time = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        self.last_temperature = None
        self.fan_auto_on = False
        
    def setup_local_broker(self):
        self.local_client = mqtt.Client(client_id=f"{self.config['gateway_id']}_local")
        
        if self.config['local_broker']['use_tls']:
            self.local_client.tls_set(
                ca_certs=self.config['local_broker']['ca_cert'],
                certfile=self.config['local_broker']['client_cert'],
                keyfile=self.config['local_broker']['client_key'],
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
        
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
                client.subscribe(topic, qos=1)
                logger.info(f" Subscribed: {topic}")
        else:
            logger.error(f" Local Broker Connection Failed: {rc}")
    
    def on_local_disconnect(self, client, userdata, rc):
        self.connected_local = False
        logger.warning(f" Disconnected from Local Broker (rc={rc})")
        
        if rc != 0:
            logger.warning(" Attempting to reconnect to local broker...")
            self.attempt_local_reconnect()
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            self.connection_lost_time = None
            self.reconnect_attempts = 0
            logger.info(" Connected to VPS Broker")

            # Subscribe to sync trigger
            sync_topic = self.config['topics']['sync_trigger']
            client.subscribe(sync_topic, qos=1)
            logger.info(f" Subscribed to sync trigger: {sync_topic}")

            # Subscribe to command topic to receive remote commands
            command_topic = f"gateway/{self.config['gateway_id']}/command/+"
            client.subscribe(command_topic, qos=1)
            logger.info(f" Subscribed to command topic: {command_topic}")

            self.publish_gateway_status('online')
        else:
            logger.error(f" VPS Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        was_connected = self.connected_vps
        self.connected_vps = False
        
        if was_connected and self.connection_lost_time is None:
            self.connection_lost_time = datetime.now()
            logger.error(f" Disconnected from VPS Broker (rc={rc})")
        
        if rc != 0:
            logger.warning(" Unexpected disconnect from VPS, attempting reconnect...")
            self.attempt_vps_reconnect()
    
    def attempt_local_reconnect(self):
        try:
            time.sleep(2)
            self.local_client.reconnect()
            logger.info(" Local broker reconnection initiated")
        except Exception as e:
            logger.error(f" Local broker reconnect failed: {e}")
    
    def attempt_vps_reconnect(self):
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            backoff_time = min(2 ** self.reconnect_attempts, 60)
            
            logger.info(f" VPS reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} "
                       f"in {backoff_time}s")
            
            time.sleep(backoff_time)
            
            try:
                self.vps_client.reconnect()
            except Exception as e:
                logger.error(f" VPS reconnect failed: {e}")
        else:
            logger.critical(" Max VPS reconnect attempts reached")
    
    def on_local_message(self, client, userdata, msg):
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
        try:
            if 'sync/trigger' in msg.topic and self.sync_manager:
                data = json.loads(msg.payload.decode())
                logger.info(f" Sync trigger received: {data.get('reason', 'unknown')}")
                self.sync_manager.trigger_immediate_sync()

            elif 'command' in msg.topic:
                # Handle remote commands from VPS
                data = json.loads(msg.payload.decode())
                self.handle_remote_command(msg.topic, data)
        except Exception as e:
            logger.error(f"Error processing VPS message: {e}")
    
    def handle_remote_command(self, topic, data):
        """Handle remote fan control commands from VPS"""
        try:
            # Parse topic: gateway/Gateway3/command/fan_01
            parts = topic.split('/')
            if len(parts) < 4:
                logger.warning(f"[REMOTE CMD] Invalid topic format: {topic}")
                return

            device_id = parts[3]
            command = data.get('command', '').lower()
            command_id = data.get('command_id')
            user_id = data.get('user_id', 'unknown')

            logger.info(f"[REMOTE CMD] Received {command} for {device_id} from user {user_id}")

            # Handle fan commands
            if command == 'fan_on':
                logger.info(f"[REMOTE CMD] Turning fan ON for {device_id}")
                self.control_fan('on', 'remote')

            elif command == 'fan_off':
                logger.info(f"[REMOTE CMD] Turning fan OFF for {device_id}")
                self.control_fan('off', 'remote')

            else:
                logger.warning(f"[REMOTE CMD] Unknown command: {command}")

        except Exception as e:
            logger.error(f"[REMOTE CMD] Error handling remote command: {e}")

    def handle_temperature_data(self, data):
        try:
            logger.debug(f"Received temperature data: {data}")
            temperature = data.get('data', {}).get('temperature')
            humidity = data.get('data', {}).get('humidity')
            
            if temperature is not None:
                self.last_temperature = float(temperature)
                logger.info(f"[TEMP] {temperature}°C, {humidity}% RH")
                self.forward_telemetry_to_vps('temp_01', data)
                
                auto_enabled = self.db_manager.settings_data.get('automation', {}).get('auto_fan_enabled', True)
                threshold = self.db_manager.settings_data.get('automation', {}).get('temp_threshold', 30.0)
                
                if auto_enabled:
                    if temperature > threshold and not self.fan_auto_on:
                        logger.warning(f"[AUTO] Temperature {temperature}°C > {threshold}°C - Turning fan ON")
                        self.control_fan('on', 'auto')
                        self.fan_auto_on = True
                        
                        self.db_manager.add_log('alert', 'high_temperature', 
                                               device_id='temp_01', 
                                               temperature=temperature)
                        
                    elif temperature <= threshold and self.fan_auto_on:
                        logger.info(f"[AUTO] Temperature {temperature}°C <= {threshold}°C - Turning fan OFF")
                        self.control_fan('off', 'auto')
                        self.fan_auto_on = False
        except Exception as e:
            logger.error(f"Error handling temperature data: {e}")
    
    def control_fan(self, action, source='manual'):
        try:
            command = {
                'cmd': 'fan_on' if action == 'on' else 'fan_off',
                'source': source,
                'timestamp': time.time()
            }
            
            topic = self.config['topics']['local_fan_command']
            
            if self.connected_local:
                self.local_client.publish(topic, json.dumps(command), qos=1)
                logger.info(f"[FAN] Command sent: {action} ({source})")
            else:
                logger.error("[FAN] Cannot send command - local broker disconnected")
        except Exception as e:
            logger.error(f"Error controlling fan: {e}")
    
    def forward_telemetry_to_vps(self, device_id, data):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'timestamp': now_compact(),
            'data': data
        }
        
        topic = self.config['topics']['vps_telemetry'].format(device_id=device_id)
        self.publish_to_vps(topic, payload)
    
    def forward_status_to_vps(self, device_id, data):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'status': data.get('state', 'unknown'),
            'timestamp': now_compact(),
            'metadata': data
        }
        
        topic = self.config['topics']['vps_status'].format(device_id=device_id)
        self.publish_to_vps(topic, payload)
    
    def publish_to_vps(self, topic, payload):
        if not self.connected_vps:
            logger.warning(" Cannot publish to VPS - not connected")
            return False
        
        try:
            payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            result = self.vps_client.publish(topic, payload_str, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f" Published to VPS: {topic}")
                return True
            else:
                logger.error(f" Failed to publish to VPS: {topic}, rc={result.rc}")
                return False
        except Exception as e:
            logger.error(f"Error publishing to VPS: {e}")
        
        return False
    
    def publish_gateway_status(self, status):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'last_temperature': self.last_temperature,
            'fan_auto_on': self.fan_auto_on,
            'timestamp': now_compact(),
            'uptime': time.time() - start_time if 'start_time' in globals() else 0,
            'local_connected': self.connected_local,
            'vps_connected': self.connected_vps,
            'reconnect_count': self.reconnect_attempts
        }
        topic = self.config['topics']['vps_gateway_status']
        return self.publish_to_vps(topic, payload)

# ============= ENHANCED HEARTBEAT =============
class HeartbeatManager:
    def __init__(self, mqtt_manager, sync_manager, interval, stop_event):
        self.mqtt_manager = mqtt_manager
        self.sync_manager = sync_manager
        self.interval = interval
        self.stop_event = stop_event
        self.heartbeat_count = 0
        self.failed_heartbeats = 0
        self.last_successful_heartbeat = None
        
    def run(self):
        logger.info(f" Heartbeat Manager started (interval: {self.interval}s)")
        
        while not self.stop_event.is_set():
            try:
                success = self.mqtt_manager.publish_gateway_status('online')
                
                if success:
                    self.heartbeat_count += 1
                    self.failed_heartbeats = 0
                    self.last_successful_heartbeat = datetime.now()
                    
                    sync_stats = self.sync_manager.get_stats()
                    logger.info(f" Heartbeat #{self.heartbeat_count} | "
                              f"Temp: {self.mqtt_manager.last_temperature}°C | "
                              f"Fan Auto: {self.mqtt_manager.fan_auto_on} | "
                              f"Syncs: {sync_stats['sync_count']} | "
                              f"Local: {'OK' if self.mqtt_manager.connected_local else 'FAIL'} | "
                              f"VPS: {'OK' if self.mqtt_manager.connected_vps else 'FAIL'}")
                else:
                    self.failed_heartbeats += 1
                    logger.warning(f" Heartbeat failed (consecutive: {self.failed_heartbeats})")
                    
                    if self.failed_heartbeats >= 3:
                        logger.error(" Multiple heartbeat failures - checking connections...")
                        
                        if not self.mqtt_manager.connected_local:
                            logger.error(" Local broker connection lost")
                            self.mqtt_manager.attempt_local_reconnect()
                        
                        if not self.mqtt_manager.connected_vps:
                            logger.error(" VPS connection lost")
                            self.mqtt_manager.attempt_vps_reconnect()
                
                if self.stop_event.wait(timeout=self.interval):
                    break
                    
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                if self.stop_event.wait(timeout=self.interval):
                    break
        
        logger.info(" Heartbeat Manager stopped")

# ============= MAIN =============
start_time = time.time()
stop_event = Event()

def main():
    logger.info("=" * 70)
    logger.info("  Gateway 3 (User 3 - Anh) - Temp/Fan with Enhanced Heartbeat")
    logger.info("=" * 70)
    
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
    
    logger.info(" Connecting to Local Broker...")
    if not mqtt_manager.setup_local_broker():
        logger.error("Failed to connect to local broker. Exiting.")
        return
    
    logger.info(" Connecting to VPS Broker...")
    if not mqtt_manager.setup_vps_broker():
        logger.error("Failed to connect to VPS. Exiting.")
        return
    
    logger.info(" Starting Database Sync Service (5s interval)...")
    sync_manager.start()
    time.sleep(2)
    
    logger.info(" Starting Enhanced Heartbeat Manager...")
    heartbeat_manager = HeartbeatManager(
        mqtt_manager,
        sync_manager,
        CONFIG['heartbeat_interval'],
        stop_event
    )
    heartbeat_thread = Thread(target=heartbeat_manager.run, daemon=True)
    heartbeat_thread.start()
    
    logger.info("=" * 70)
    logger.info(" Gateway 3 Running - Enhanced heartbeat every 30 seconds")
    logger.info("=" * 70)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n Shutdown signal received")
        stop_event.set()
        sync_manager.stop()
        logger.info(" Gateway stopped")

if __name__ == '__main__':
    main()