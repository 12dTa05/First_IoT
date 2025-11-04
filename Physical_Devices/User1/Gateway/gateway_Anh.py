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
        'command': 'gateway/Gateway1/command/#',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'heartbeat_interval': 30,  # Changed from 60 to 30 seconds
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
        backup_file = f"{self.devices_file}.backup"
        if os.path.exists(self.devices_file):
            import shutil
            shutil.copy2(self.devices_file, backup_file)
        
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def verify_rfid(self, uid):
        rfid_cards = self.devices_data.get('rfid_cards', {})
        uid_lower = uid.lower()

        # Normalize all keys to lowercase for case-insensitive comparison
        rfid_cards_normalized = {k.lower(): v for k, v in rfid_cards.items()}

        if uid_lower not in rfid_cards_normalized:
            return False, 'unknown_card'

        card_data = rfid_cards_normalized[uid_lower]

        if not card_data.get('active', False):
            return False, 'inactive_card'

        expires_at = card_data.get('expires_at')
        if expires_at:
            try:
                expire_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.now(expire_time.tzinfo) > expire_time:
                    return False, 'expired_card'
            except:
                pass

        return True, None

# ============= VPS MQTT MANAGER =============
class VPSMQTTManager:
    def __init__(self, config, sync_manager=None):
        self.config = config
        self.sync_manager = sync_manager
        self.lora_handler = None
        self.vps_client = None
        self.connected_vps = False
        self.connection_lost_time = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
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
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            self.connection_lost_time = None
            self.reconnect_attempts = 0
            logger.info(" Connected to VPS Broker")
            
            sync_topic = self.config['topics']['sync_trigger']
            client.subscribe(sync_topic)
            logger.info(f" Subscribed to sync trigger: {sync_topic}")

            command_topic = self.config['topics']['command']
            client.subscribe(command_topic)
            logger.info(f" Subscribed to command topic: {command_topic}")
            
            # Send immediate online status upon connection
            self.publish_gateway_status('online')
            
        else:
            logger.error(f" VPS Broker Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        was_connected = self.connected_vps
        self.connected_vps = False
        
        if was_connected and self.connection_lost_time is None:
            self.connection_lost_time = datetime.now()
            logger.error(f" Disconnected from VPS Broker (rc={rc})")
        
        if rc != 0:
            logger.warning(f" Unexpected disconnect, attempting reconnect...")
            self.attempt_reconnect()
    
    def attempt_reconnect(self):
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            backoff_time = min(2 ** self.reconnect_attempts, 60)
            
            logger.info(f" Reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} "
                       f"in {backoff_time}s")
            
            time.sleep(backoff_time)
            
            try:
                self.vps_client.reconnect()
            except Exception as e:
                logger.error(f" Reconnect failed: {e}")
        else:
            logger.critical(f" Max reconnect attempts reached, please check VPS connection")
    
    def on_vps_message(self, client, userdata, msg):
        try:
            logger.info(f" VPS message: {msg.topic}")

            if 'sync/trigger' in msg.topic and self.sync_manager:
                data = json.loads(msg.payload.decode())
                logger.info(f" Sync trigger received: {data.get('reason', 'unknown')}")
                self.sync_manager.trigger_immediate_sync()

            elif 'command' in msg.topic:
                data = json.loads(msg.payload.decode())
                self.handle_command(msg.topic, data)

        except Exception as e:
            logger.error(f"Error processing VPS message: {e}")
    
    def publish_to_vps(self, topic, payload):
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
                logger.error(f"Failed to publish to VPS: {topic}, rc={result.rc}")
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
            'reconnect_count': self.reconnect_attempts
        }
        topic = self.config['topics']['vps_gateway_status']
        return self.publish_to_vps(topic, payload)

    def set_lora_handler(self, lora_handler):
        """Set LoRa handler reference for sending commands"""
        self.lora_handler = lora_handler
        logger.info(" LoRa Handler reference set")

    def handle_command(self, topic, data):
        """Handle incoming command from VPS"""
        try:
            # Parse topic: gateway/Gateway1/command/rfid_gate_01
            parts = topic.split('/')
            if len(parts) < 4:
                logger.warning(f" Invalid command topic: {topic}")
                return

            device_id = parts[3]
            command = data.get('command')
            command_id = data.get('command_id')
            params = data.get('params', {})

            logger.info(f" Command received: {command} for {device_id} (ID: {command_id})")

            if not self.lora_handler:
                logger.error(" LoRa handler not available")
                return

            # Send command via LoRa
            if device_id == 'rfid_gate_01':
                if command == 'unlock':
                    duration = params.get('duration', 5)
                    self.lora_handler.send_remote_unlock(command_id, data.get('user_id', 'unknown'), duration)
                elif command == 'lock':
                    self.lora_handler.send_remote_lock(command_id, data.get('user_id', 'unknown'))
                else:
                    logger.warning(f" Unknown command: {command}")
            else:
                logger.warning(f" Unknown device: {device_id}")

        except Exception as e:
            logger.error(f"Error handling command: {e}")

# ============= LORA HANDLER =============
class LoRaHandler:
    def __init__(self, config, db_manager, mqtt_manager):
        self.config = config
        self.db_manager = db_manager
        self.mqtt_manager = mqtt_manager
        self.serial_port = None
        self.running = False
        
    def connect(self):
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
        self.running = True
        thread = Thread(target=self.message_loop, daemon=True)
        thread.start()
        logger.info(" LoRa Handler Started")
    
    def message_loop(self):
        buffer = bytearray()
        
        while self.running:
            try:
                if self.serial_port.in_waiting > 0:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    buffer.extend(data)
                    
                    while len(buffer) >= 12:
                        if buffer[0] == 0x00 and buffer[1] == 0x02 and buffer[2] == 0x17:
                            header0 = buffer[3]
                            msg_type = (header0 >> 4) & 0x0F
                            version = header0 & 0x0F
                            
                            header1 = buffer[4]
                            flags = (header1 >> 4) & 0x0F
                            device_type = header1 & 0x0F
                            
                            sequence = struct.unpack('<H', buffer[5:7])[0]
                            timestamp = struct.unpack('<I', buffer[7:11])[0]
                            payload_length = buffer[11]
                            total_length = 12 + payload_length + 4
                            
                            if len(buffer) >= total_length:
                                packet = buffer[:total_length]
                                buffer = buffer[total_length:]
                                
                                received_crc = struct.unpack('<I', packet[-4:])[0]
                                calculated_crc = crc32(packet[3:12 + payload_length])
                                
                                if received_crc == calculated_crc:
                                    payload = packet[12:12 + payload_length]
                                    logger.info(f"Valid packet: msg_type={msg_type:02x}, seq={sequence}")
                                    self.process_packet(msg_type, payload, sequence, timestamp, device_type)
                                else:
                                    logger.warning(f"CRC mismatch: received={received_crc:08x}, "
                                                 f"calculated={calculated_crc:08x}")
                            else:
                                break
                        else:
                            logger.warning(f"Invalid header: {buffer[0:3].hex()}")
                            buffer.pop(0)
                
                time.sleep(0.01)
                
            except Exception as e:
                logger.error(f"LoRa message loop error: {e}")
                time.sleep(1)
    
    def process_packet(self, msg_type, payload, sequence, timestamp, device_type):
        try:
            if msg_type == 0x01:
                uid = payload.hex()
                logger.info(f"[RFID] Card detected: {uid} (seq: {sequence})")
                
                granted, deny_reason = self.db_manager.verify_rfid(uid)

                # CRITICAL: Delay to allow RFID device LoRa module to switch from TX to RX mode
                # Without this delay, device cannot receive our response
                time.sleep(0.15)  # 150ms delay

                status = "GRANT" if granted else "DENY5"
                self.send_access_response(status)
                
                access_log = {
                    'gateway_id': self.config['gateway_id'],
                    'device_id': 'rfid_gate_01',
                    'rfid_uid': uid,
                    'result': 'granted' if granted else 'denied',
                    'method': 'rfid',
                    'deny_reason': deny_reason,
                    'timestamp': now_compact()
                }
                
                topic = self.config['topics']['vps_access'].format(device_id='rfid_gate_01')
                self.mqtt_manager.publish_to_vps(topic, access_log)
                
                if granted:
                    logger.info(f"[RFID] {uid}: ACCESS GRANTED")
                else:
                    logger.warning(f"[RFID] {uid}: ACCESS DENIED ({deny_reason})")
            
            elif msg_type == 0x06:
                status = payload.decode('utf-8', errors='ignore')
                logger.info(f"[RFID] Status update: {status} (seq: {sequence})")
                self.publish_gate_status(status, sequence)
                
            else:
                logger.warning(f"Unknown message type: {msg_type:02x}")
                
        except Exception as e:
            logger.error(f"Error processing LoRa packet: {e}")
    
    def send_access_response(self, status):
        try:
            response_bytes = status.encode('utf-8')
            packet = bytearray([0xC0, 0x00, 0x00, 0x00, 0x00, 0x17, len(response_bytes)])
            packet.extend(response_bytes)

            # Debug: Print packet before sending
            logger.info(f"[LoRa] Sending response: {status} ({len(packet)} bytes)")
            logger.info(f"[LoRa] Packet: {' '.join([f'{b:02X}' for b in packet])}")

            bytes_written = self.serial_port.write(packet)
            self.serial_port.flush()  # Ensure data is sent immediately

            logger.info(f"[LoRa] Response sent: {status} ({bytes_written} bytes written)")
        except Exception as e:
            logger.error(f"[LoRa] Error sending response: {e}", exc_info=True)
    
    def publish_gate_status(self, status, sequence):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'rfid_gate_01',
            'status': status,
            'sequence': sequence,
            'timestamp': now_compact()
        }

        topic = self.config['topics']['vps_status'].format(device_id='rfid_gate_01')
        self.mqtt_manager.publish_to_vps(topic, payload)

    def send_remote_unlock(self, command_id, user_id, duration):
        """Send remote unlock command via LoRa"""
        try:
            # Convert duration from seconds to milliseconds
            duration_ms = duration * 1000

            # Format: REMOTE_UNLOCK:{command_id}:{user}:{duration_ms}
            command = f"REMOTE_UNLOCK:{command_id}:{user_id}:{duration_ms}"
            command_bytes = command.encode('utf-8')

            # Build LoRa packet
            packet = bytearray([0xC0, 0x00, 0x00, 0x00, 0x00, 0x17, len(command_bytes)])
            packet.extend(command_bytes)

            self.serial_port.write(packet)
            logger.info(f"[LoRa] Remote unlock sent: {command_id} (user: {user_id}, duration: {duration}s)")

        except Exception as e:
            logger.error(f"[LoRa] Error sending remote unlock: {e}")

    def send_remote_lock(self, command_id, user_id):
        """Send remote lock command via LoRa"""
        try:
            # Format: REMOTE_LOCK:{command_id}:{user}
            command = f"REMOTE_LOCK:{command_id}:{user_id}"
            command_bytes = command.encode('utf-8')

            # Build LoRa packet
            packet = bytearray([0xC0, 0x00, 0x00, 0x00, 0x00, 0x17, len(command_bytes)])
            packet.extend(command_bytes)

            self.serial_port.write(packet)
            logger.info(f"[LoRa] Remote lock sent: {command_id} (user: {user_id})")

        except Exception as e:
            logger.error(f"[LoRa] Error sending remote lock: {e}")

    def stop(self):
        self.running = False
        if self.serial_port:
            self.serial_port.close()
            logger.info(" LoRa Serial Closed")

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
                              f"Version: {sync_stats['current_version']}")
                else:
                    self.failed_heartbeats += 1
                    logger.warning(f" Heartbeat failed (consecutive: {self.failed_heartbeats})")
                    
                    if self.failed_heartbeats >= 3:
                        logger.error(" Multiple heartbeat failures detected, checking connection...")
                        if not self.mqtt_manager.connected_vps:
                            logger.error(" VPS connection lost, attempting reconnect...")
                            self.mqtt_manager.attempt_reconnect()
                
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
    logger.info("  Gateway 1 (User 1 - Tu) - RFID Gate with Enhanced Heartbeat")
    logger.info("=" * 70)
    
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
    logger.info(" Database Manager Initialized")
    
    sync_manager = DatabaseSyncManager(CONFIG, db_manager)
    logger.info(" Sync Manager Initialized")
    
    mqtt_manager = VPSMQTTManager(CONFIG, sync_manager)
    
    logger.info(" Connecting to VPS Broker...")
    if not mqtt_manager.setup_vps_broker():
        logger.error("Failed to connect to VPS. Exiting.")
        return
    
    logger.info(" Starting Database Sync Service (5s interval)...")
    sync_manager.start()
    time.sleep(2)
    
    logger.info(" Starting LoRa Handler...")
    lora_handler = LoRaHandler(CONFIG, db_manager, mqtt_manager)

    if lora_handler.connect():
        lora_handler.start()
        # Set LoRa handler reference in MQTT manager for command handling
        mqtt_manager.set_lora_handler(lora_handler)
    else:
        logger.error("Failed to start LoRa handler. Exiting.")
        return
    
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
    logger.info(" Gateway 1 Running - Enhanced heartbeat every 30 seconds")
    logger.info("=" * 70)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n Shutdown signal received")
        stop_event.set()
        sync_manager.stop()
        lora_handler.stop()
        logger.info(" Gateway stopped")

if __name__ == '__main__':
    main()