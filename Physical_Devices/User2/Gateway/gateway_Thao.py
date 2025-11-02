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
    'gateway_id': 'Gateway2',
    'user_id': '00002',
    
    'local_broker': {
        'host': '192.168.1.205',
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway2.cert.pem',
        'client_key': './certs/gateway2.key.pem',
        'username': 'Gateway2',
        'password': '125',
    },
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway2.cert.pem',
        'client_key': './certs/gateway2.key.pem',
    },
    
    'vps_api_url': 'http://159.223.63.61:3000',
    
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
    'heartbeat_interval': 30,  # Changed from 300 to 30 seconds
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
        backup_file = f"{self.devices_file}.backup"
        if os.path.exists(self.devices_file):
            import shutil
            shutil.copy2(self.devices_file, backup_file)
        
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def verify_password(self, password_hash):
        passwords = self.devices_data.get('passwords', {})
        
        for password_id, password_data in passwords.items():
            if password_data.get('hash') == password_hash:
                if not password_data.get('active', False):
                    return False, 'inactive_password', password_id
                
                # expires_at = password_data.get('expires_at')
                # if expires_at:
                #     try:
                #         expire_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                #         if datetime.now(expire_time.tzinfo) > expire_time:
                #             return False, 'expired_password', password_id
                #     except:
                #         pass
                
                # return True, None, password_id
        
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
        self.connection_lost_time = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
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
                self.config['topics']['local_passkey_request'],
                self.config['topics']['local_passkey_status']
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
            
            sync_topic = self.config['topics']['sync_trigger']
            client.subscribe(sync_topic, qos=1)
            logger.info(f" Subscribed to sync trigger: {sync_topic}")
            
            # Send immediate online status upon connection
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
            if 'request' in msg.topic:
                data = json.loads(msg.payload.decode())
                self.handle_passkey_request(data)
            elif 'status' in msg.topic:
                data = json.loads(msg.payload.decode())
                self.forward_status_to_vps(data)
        except Exception as e:
            logger.error(f"Error processing local message: {e}")
    
    def on_vps_message(self, client, userdata, msg):
        try:
            if 'sync/trigger' in msg.topic and self.sync_manager:
                data = json.loads(msg.payload.decode())
                logger.info(f" Sync trigger received: {data.get('reason', 'unknown')}")
                self.sync_manager.trigger_immediate_sync()
        except Exception as e:
            logger.error(f"Error processing VPS message: {e}")
    
    def handle_passkey_request(self, data):
        try:
            password_hash = data.get('pw')
            device_id = data.get('client_id', 'passkey_01')
            
            if not password_hash:
                logger.warning("[PASSKEY] Request missing password hash")
                self.send_unlock_response(device_id, False, 'missing_password')
                return
            
            granted, deny_reason, password_id = self.db_manager.verify_password(password_hash)
            
            self.send_unlock_response(device_id, granted, deny_reason)
            
            access_log = {
                'gateway_id': self.config['gateway_id'],
                'device_id': device_id,
                'password_id': password_id if granted else None,
                'result': 'granted' if granted else 'denied',
                'method': 'passkey',
                'deny_reason': deny_reason,
                'timestamp': now_compact()
            }
            
            topic = self.config['topics']['vps_access'].format(device_id=device_id)
            self.publish_to_vps(topic, access_log)
            
            if granted:
                logger.info(f"[PASSKEY] ACCESS GRANTED (password_id: {password_id})")
            else:
                logger.warning(f"[PASSKEY] ACCESS DENIED ({deny_reason})")
                
        except Exception as e:
            logger.error(f"Error handling passkey request: {e}")
    
    def send_unlock_response(self, device_id, granted, deny_reason):
        try:
            response = {
                'cmd': 'GRANT' if granted else 'LOCK',
                'reason': deny_reason if not granted else None,
                'timestamp': now_compact()
            }
            
            topic = self.config['topics']['local_passkey_response']
            payload = json.dumps(response)
            
            if self.connected_local:
                self.local_client.publish(topic, payload, qos=1)
                logger.debug(f"[PASSKEY] Response sent: {response['cmd']}")
            else:
                logger.error("[PASSKEY] Cannot send response - local broker disconnected")
                
        except Exception as e:
            logger.error(f"Error sending unlock response: {e}")
    
    def forward_status_to_vps(self, data):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': data.get('device_id', 'passkey_01'),
            'status': data.get('state', 'unknown'),
            'timestamp': data.get('timestamp', now_compact()),
            'metadata': data
        }
        
        topic = self.config['topics']['vps_status'].format(device_id=payload['device_id'])
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
                              f"Syncs: {sync_stats['sync_count']} | "
                              f"Errors: {sync_stats['sync_errors']} | "
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
    logger.info("  Gateway 2 (User 2 - Thao) - Passkey with Enhanced Heartbeat")
    logger.info("=" * 70)
    
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
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
    logger.info(" Gateway 2 Running - Enhanced heartbeat every 30 seconds")
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