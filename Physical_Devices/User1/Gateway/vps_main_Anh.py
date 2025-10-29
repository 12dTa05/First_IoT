#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import ssl
import json
import os
import serial
import time
import logging
import struct
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
    'gateway_id': 'Gateway1',
    'user_id': '00001',
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'client_cert': './certs/gateway1.cert.pem',
        'client_key': './certs/gateway1.key.pem',
    },
    
    'vps_api_url': 'http://159.223.63.61:3000',
    
    'lora_serial': {
        'port': 'COM7',
        'baudrate': 9600,
    },
    
    'topics': {
        'vps_access': 'gateway/Gateway1/access/{device_id}',
        'vps_status': 'gateway/Gateway1/status/{device_id}',
        'vps_gateway_status': 'gateway/Gateway1/status/gateway',
        'sync_trigger': 'gateway/Gateway1/sync/trigger',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'heartbeat_interval': 60,
}

# ============= CRC32 =============
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
    
    def verify_rfid(self, uid):
        """Verify RFID card"""
        card = self.devices_data.get('rfid_cards', {}).get(uid)
        
        if not card:
            return False, 'unknown_card'
        
        if not card.get('active', False):
            return False, 'inactive_card'
        
        # Check expiration
        expires_at = card.get('expires_at')
        if expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.now(expire_time.tzinfo) > expire_time:
                    return False, 'expired_card'
            except:
                pass
        
        # Update last_used
        card['last_used'] = datetime.now().isoformat()
        self.save_devices()
        
        return True, None

# ============= VPS MQTT MANAGER =============
class VPSMQTTManager:
    def __init__(self, config, sync_manager=None):
        self.config = config
        self.sync_manager = sync_manager
        self.vps_client = None
        self.connected_vps = False
        
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
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            logger.info(" Connected to VPS Broker")
            
            # Subscribe to sync trigger topic
            sync_topic = self.config['topics']['sync_trigger']
            client.subscribe(sync_topic)
            logger.info(f" Subscribed to sync trigger: {sync_topic}")
            
        else:
            logger.error(f" VPS Broker Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        self.connected_vps = False
        logger.warning(" Disconnected from VPS Broker")
    
    def on_vps_message(self, client, userdata, msg):
        """Handle messages from VPS (sync triggers, commands, etc.)"""
        try:
            logger.info(f" VPS message: {msg.topic}")
            
            if 'sync/trigger' in msg.topic and self.sync_manager:
                data = json.loads(msg.payload.decode())
                logger.info(f" Sync trigger received: {data.get('reason', 'unknown')}")
                
                # Trigger immediate sync
                self.sync_manager.trigger_immediate_sync()
                
        except Exception as e:
            logger.error(f"Error processing VPS message: {e}")
    
    def publish_to_vps(self, topic, payload):
        """Publish message to VPS broker"""
        if not self.connected_vps:
            logger.warning(" Cannot publish - VPS not connected")
            return False
        
        try:
            payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            result = self.vps_client.publish(topic, payload_str, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f" Published to VPS: {topic}")
                return True
            else:
                logger.error(f"Failed to publish to VPS: {topic}")
                return False
        except Exception as e:
            logger.error(f"Error publishing to VPS: {e}")
            return False
    
    def publish_gateway_status(self, status):
        """Publish gateway heartbeat status"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        topic = self.config['topics']['vps_gateway_status']
        return self.publish_to_vps(topic, payload)

# ============= LORA HANDLER =============
class LoRaHandler:
    def __init__(self, config, db_manager, mqtt_manager):
        self.config = config
        self.db_manager = db_manager
        self.mqtt_manager = mqtt_manager
        self.serial_port = None
        self.running = False
        
    def connect(self):
        """Connect to LoRa module"""
        try:
            self.serial_port = serial.Serial(
                port=self.config['lora_serial']['port'],
                baudrate=self.config['lora_serial']['baudrate'],
                timeout=1
            )
            logger.info(f" LoRa Serial Connected: {self.config['lora_serial']['port']}")
            return True
        except Exception as e:
            logger.error(f" LoRa Serial Connection Failed: {e}")
            return False
    
    def start(self):
        """Start LoRa message handler thread"""
        self.running = True
        thread = Thread(target=self.message_loop, daemon=True)
        thread.start()
        logger.info(" LoRa Handler Started")
    
    def message_loop(self):
        """Main loop to handle LoRa messages"""
        buffer = bytearray()
        
        while self.running:
            try:
                if self.serial_port.in_waiting > 0:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    buffer.extend(data)
                    
                    # Process complete packets
                    while len(buffer) >= 12:  # Minimum packet size: 3 (header) + 5 (header + length) + 4 (CRC)
                        if buffer[0] == 0x00 and buffer[1] == 0x02 and buffer[2] == 0x17:
                            # Extract header fields
                            header0 = buffer[3]
                            msg_type = (header0 >> 4) & 0x0F
                            version = header0 & 0x0F
                            
                            header1 = buffer[4]
                            flags = (header1 >> 4) & 0x0F
                            device_type = header1 & 0x0F
                            
                            # Extract sequence and timestamp
                            sequence = struct.unpack('<H', buffer[5:7])[0]
                            timestamp = struct.unpack('<I', buffer[7:11])[0]
                            
                            # Extract payload length
                            payload_length = buffer[11]
                            
                            # Calculate total packet length
                            total_length = 12 + payload_length + 4  # 12 bytes header + payload + 4 bytes CRC
                            
                            if len(buffer) >= total_length:
                                packet = buffer[:total_length]
                                buffer = buffer[total_length:]
                                
                                # Verify CRC
                                received_crc = struct.unpack('<I', packet[-4:])[0]
                                calculated_crc = crc32(packet[3:12 + payload_length])  # CRC from header0 to end of payload
                                
                                if received_crc == calculated_crc:
                                    payload = packet[12:12 + payload_length]
                                    logger.info(f"Gói tin hợp lệ: msg_type={msg_type:02x}, sequence={sequence}, timestamp={timestamp}")
                                    self.process_packet(msg_type, payload, sequence, timestamp, device_type)
                                else:
                                    logger.warning(f"CRC không khớp: received={received_crc:08x}, calculated={calculated_crc:08x}")
                            else:
                                break  # Wait for more data
                        else:
                            logger.warning(f"Tiêu đề không hợp lệ: {buffer[0:3].hex()}")
                            buffer.pop(0)
                
                time.sleep(0.01)
                
            except Exception as e:
                logger.error(f"LoRa message loop error: {e}")
                time.sleep(1)
    
    def process_packet(self, msg_type, payload, sequence, timestamp, device_type):
        """Process LoRa packet from RFID gate"""
        try:
            if msg_type == 0x01:  # RFID Scan
                uid = payload.hex()
                logger.info(f"[RFID] Card detected: {uid} (seq: {sequence}, device_type: {device_type:02x})")
                
                # Verify with local database
                granted, deny_reason = self.db_manager.verify_rfid(uid)
                
                # Send response back to gate
                status = "GRANT" if granted else "DENY5"
                self.send_access_response(status)
                
                # Log to VPS
                access_log = {
                    'gateway_id': self.config['gateway_id'],
                    'device_id': 'rfid_gate_01',
                    'rfid_uid': uid,
                    'result': 'granted' if granted else 'denied',
                    'method': 'rfid',
                    'deny_reason': deny_reason,
                    'time': datetime.now().isoformat()
                }
                
                topic = self.config['topics']['vps_access'].format(device_id='rfid_gate_01')
                self.mqtt_manager.publish_to_vps(topic, access_log)
                
                if granted:
                    logger.info(f"[RFID] {uid}:  ACCESS GRANTED")
                else:
                    logger.warning(f"[RFID] {uid}:  ACCESS DENIED ({deny_reason})")
            
            elif msg_type == 0x06:  # Gate Status
                status = payload.decode('utf-8', errors='ignore')
                logger.info(f"[RFID] Status update: {status} (seq: {sequence}, device_type: {device_type:02x})")
                self.publish_gate_status(status, sequence)
                
            else:
                logger.warning(f"Loại thông điệp không xác định: {msg_type:02x}")
                
        except Exception as e:
            logger.error(f"Error processing LoRa packet: {e}")
    
    def send_access_response(self, status):
        """Send access response to LoRa gate"""
        try:
            response_bytes = status.encode('utf-8')
            packet = bytearray([0xC0, 0x00, 0x00, 0x00, 0x00, 0x17, len(response_bytes)])
            packet.extend(response_bytes)
            self.serial_port.write(packet)
            logger.debug(f"[LoRa] Response sent: {status}")
        except Exception as e:
            logger.error(f"[LoRa] Error sending response: {e}")
    
    def publish_gate_status(self, status, sequence):
        """Publish RFID gate status to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'rfid_gate_01',
            'status': status,
            'sequence': sequence,
            'time': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['vps_status'].format(device_id='rfid_gate_01')
        self.mqtt_manager.publish_to_vps(topic, payload)
    
    def stop(self):
        self.running = False
        if self.serial_port:
            self.serial_port.close()
            logger.info(" LoRa Serial Closed")

# ============= HEARTBEAT =============
def heartbeat_loop(mqtt_manager, sync_manager, interval):
    """Send periodic heartbeat and sync stats to VPS"""
    while True:
        try:
            # Send gateway heartbeat
            mqtt_manager.publish_gateway_status('online')
            
            # Get sync stats
            sync_stats = sync_manager.get_stats()
            logger.info(f" Heartbeat | Syncs: {sync_stats['sync_count']} | Errors: {sync_stats['sync_errors']} | Version: {sync_stats['current_version']}")
            
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            time.sleep(interval)

# ============= MAIN =============
def main():
    logger.info("=" * 70)
    logger.info("  Gateway 1 (User 1 - Tu) - RFID Gate with Database Sync")
    logger.info("=" * 70)
    
    # Initialize database manager
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
    logger.info(" Database Manager Initialized")
    
    # Initialize sync manager
    sync_manager = DatabaseSyncManager(CONFIG, db_manager)
    logger.info(" Sync Manager Initialized")
    
    # Initialize MQTT manager with sync manager reference
    mqtt_manager = VPSMQTTManager(CONFIG, sync_manager)
    
    # Connect to VPS broker
    logger.info(" Connecting to VPS Broker...")
    if not mqtt_manager.setup_vps_broker():
        logger.error("Failed to connect to VPS. Exiting.")
        return
    
    # Start database sync service
    logger.info(" Starting Database Sync Service (5s interval)...")
    sync_manager.start()
    time.sleep(2)
    
    # Start LoRa handler
    logger.info(" Starting LoRa Handler...")
    lora_handler = LoRaHandler(CONFIG, db_manager, mqtt_manager)
    
    if lora_handler.connect():
        lora_handler.start()
    else:
        logger.error("Failed to start LoRa handler. Exiting.")
        sync_manager.stop()
        return
    
    # Start heartbeat thread
    logger.info(" Starting Heartbeat Thread...")
    heartbeat_thread = Thread(
        target=heartbeat_loop,
        args=(mqtt_manager, sync_manager, CONFIG['heartbeat_interval']),
        daemon=True
    )
    heartbeat_thread.start()
    
    logger.info("=" * 70)
    logger.info(" Gateway 1 Running - Database syncing every 5 seconds")
    logger.info("=" * 70)
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\ Shutdown signal received")
        lora_handler.stop()
        sync_manager.stop()
        logger.info(" Gateway stopped")

if __name__ == '__main__':
    main()