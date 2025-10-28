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

# ============= LOGGING SETUP =============
def setup_logging():
    log_dir = './logs'
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('gateway1')
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'gateway1.log'),
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
    'gateway_id': 'Gateway1',
    'user_id': 'user1',
    
    # LoRa configuration cho RFID Gate
    'lora_port': 'COM7',  # Thay đổi theo port của bạn
    'lora_baudrate': 9600,
    
    # VPS broker
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'client_id': 'Gateway1',
        'ca_cert': './certs/ca.cert.pem',
        'cert_file': './certs/gateway1.cert.pem',
        'key_file': './certs/gateway1.key.pem',
    },
    
    'topics': {
        # RFID Gate KHÔNG dùng MQTT local - chỉ LoRa serial
        'vps_status': 'gateway/Gateway1/status/{device_id}',
        'vps_access': 'gateway/Gateway1/access/{device_id}',
        'vps_command': 'gateway/Gateway1/command/#',
        'vps_gateway_status': 'gateway/Gateway1/status/gateway',
    },
    
    'db_path': './data',
    'devices_db': 'devices.json',
    'heartbeat_interval': 300,
}

MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x06: 'gate_status',
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
        return {'rfid_cards': {}, 'devices': {}}
    
    def save_devices(self):
        with open(self.devices_file, 'w') as f:
            json.dump(self.devices_data, f, indent=2)
    
    def check_rfid_access(self, uid):
        """Check if RFID card has access permission"""
        card = self.devices_data.get('rfid_cards', {}).get(uid)
        
        if not card:
            return False, 'invalid_card'
        
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
        
        return True, None

# ============= VPS MQTT MANAGER (Chỉ VPS, không local) =============
class VPSMQTTManager:
    def __init__(self, config):
        self.config = config
        self.vps_client = None
        self.connected_vps = False
        
    def setup_vps_broker(self):
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
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            logger.info(" VPS Broker Connected")
            
            # Subscribe to command topic
            topic = self.config['topics']['vps_command']
            client.subscribe(topic)
            logger.info(f" Subscribed to: {topic}")
            
            # Publish gateway online status
            self.publish_gateway_status('online')
        else:
            logger.error(f" VPS Broker Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        self.connected_vps = False
        logger.warning(" VPS Broker Disconnected")
    
    def on_vps_message(self, client, userdata, msg):
        try:
            logger.info(f" VPS Command: {msg.topic}")
            data = json.loads(msg.payload.decode())
            # Commands for RFID gate (if any) can be handled here
            # For now, just log
            logger.info(f"Command data: {data}")
        except Exception as e:
            logger.error(f" Error processing VPS message: {e}")
    
    def publish_to_vps(self, topic, payload):
        if self.connected_vps:
            self.vps_client.publish(topic, json.dumps(payload), qos=1)
            logger.info(f" Published to VPS: {topic}")
        else:
            logger.warning(" VPS not connected, message not sent")
    
    def publish_gateway_status(self, status):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        topic = self.config['topics']['vps_gateway_status']
        self.publish_to_vps(topic, payload)

# ============= LORA HANDLER (Xử lý RFID qua Serial) =============
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
                self.config['lora_port'],
                self.config['lora_baudrate'],
                timeout=1
            )
            logger.info(f" LoRa Serial Connected: {self.config['lora_port']}")
            return True
        except Exception as e:
            logger.error(f" LoRa Serial Connection Failed: {e}")
            return False
    
    def start(self):
        self.running = True
        thread = threading.Thread(target=self.read_loop)
        thread.daemon = True
        thread.start()
        logger.info(" LoRa Handler Started")
    
    def read_loop(self):
        """Liên tục đọc từ cổng serial LoRa"""
        buffer = bytearray()
        
        while self.running:
            try:
                if self.serial_port and self.serial_port.in_waiting > 0:
                    buffer.extend(self.serial_port.read(self.serial_port.in_waiting))
                    
                    # Phân tích các gói tin từ buffer
                    while len(buffer) >= 12:  # Kích thước tối thiểu: 3 (tiêu đề) + 5 (tiêu đề + độ dài) + 4 (CRC)
                        # Kiểm tra tiêu đề: 0x00 0x02 0x17
                        if buffer[0] == 0x00 and buffer[1] == 0x02 and buffer[2] == 0x17:
                            # Lấy msg_type và version từ byte tiêu đề 0
                            header0 = buffer[3]
                            msg_type = (header0 >> 4) & 0x0F
                            version = header0 & 0x0F
                            
                            # Lấy flags và device_type từ byte tiêu đề 1
                            header1 = buffer[4]
                            flags = (header1 >> 4) & 0x0F
                            device_type = header1 & 0x0F
                            
                            # Lấy số thứ tự (2 byte, little-endian)
                            sequence = struct.unpack('<H', buffer[5:7])[0]
                            
                            # Lấy dấu thời gian (4 byte, little-endian)
                            timestamp = struct.unpack('<I', buffer[7:11])[0]
                            
                            # Lấy độ dài tải trọng
                            payload_length = buffer[11]
                            
                            # Tính kích thước gói tin đầy đủ
                            total_length = 12 + payload_length + 4  # 12 byte tiêu đề + tải trọng + 4 byte CRC
                            
                            if len(buffer) >= total_length:
                                packet = buffer[:total_length]
                                buffer = buffer[total_length:]
                                
                                # Tách tải trọng
                                payload = packet[12:12 + payload_length]
                                
                                # Kiểm tra CRC (tính từ byte thứ 3)
                                received_crc = struct.unpack('<I', packet[-4:])[0]
                                calculated_crc = crc32(packet[3:12 + payload_length])  # Tính CRC từ byte thứ 3
                                
                                if received_crc == calculated_crc:
                                    logger.info(f"Gói tin hợp lệ: msg_type={msg_type:02x}, sequence={sequence}, timestamp={timestamp}")
                                    self.process_packet(msg_type, payload, sequence, timestamp, device_type)
                                else:
                                    logger.warning(f"CRC không khớp: received={received_crc:08x}, calculated={calculated_crc:08x}")
                            else:
                                break  # Chờ thêm dữ liệu
                        else:
                            # Bỏ byte đầu tiên nếu tiêu đề không hợp lệ
                            logger.warning(f"Tiêu đề không hợp lệ: {buffer[0:3].hex()}")
                            buffer.pop(0)
                    
                    time.sleep(0.01)
            except Exception as e:
                logger.error(f"Lỗi đọc LoRa: {e}")
                time.sleep(1)
    
    def process_packet(self, msg_type, payload, sequence, timestamp, device_type):
        """Xử lý gói tin LoRa nhận được"""
        try:
            if msg_type == 0x01:  # RFID Scan
                uid = payload.hex()
                logger.info(f"RFID Scanned: {uid} (seq: {sequence}, device_type: {device_type:02x})")
                self.handle_rfid_access(uid)
                
            elif msg_type == 0x06:  # Gate Status
                status = payload.decode('utf-8', errors='ignore')
                logger.info(f" Gate Status: {status} (seq: {sequence}, device_type: {device_type:02x})")
                self.publish_gate_status(status, sequence)
                
            else:
                logger.warning(f"Loại thông điệp không xác định: {msg_type:02x}")
                
        except Exception as e:
            logger.error(f"Lỗi xử lý gói tin: {e}")
    
    def handle_rfid_access(self, uid):
        """Handle RFID access request with improved logic (same as new gateway code)"""

        logger.info(f"[RFID] Scanned UID: {uid}")

        # Check if card exists and active
        is_valid = uid in self.db_manager.devices_data.get('rfid_cards', {}) \
                and self.db_manager.devices_data['rfid_cards'][uid].get('active', False)

        access_allowed = False
        deny_reason = 'invalid_card'

        if is_valid:
            # ---- NEW: Check access rules like time/day ----
            card = self.db_manager.devices_data['rfid_cards'][uid]

            # Time restriction
            rules = self.db_manager.devices_data.get('access_rules', {})
            now = datetime.now()
            allow = True

            if rules.get('enabled', True):
                # Day restriction
                allowed_days = rules.get('allowed_days', [0,1,2,3,4,5,6])
                if now.weekday() not in allowed_days:
                    allow = False
                    deny_reason = 'outside_allowed_days'

                # Time window
                if 'start_hour' in rules and 'end_hour' in rules:
                    h = now.hour
                    if not (rules['start_hour'] <= h < rules['end_hour']):
                        allow = False
                        deny_reason = 'outside_allowed_hours'

            if allow:
                access_allowed = True
            else:
                access_allowed = False

        # Log entry (same format as new code)
        log_entry = {
            'method': 'rfid',
            'gateway_id': self.config['gateway_id'],
            'device_id': 'rfid_gate_01',
            'rfid_uid': uid,
            'result': 'granted' if (is_valid and access_allowed) else 'denied',
            'deny_reason': None if access_allowed else deny_reason,
            'timestamp': datetime.now().isoformat()
        }

        # ---- NEW: Save last_used ----
        if is_valid and access_allowed:
            self.db_manager.devices_data['rfid_cards'][uid]['last_used'] = datetime.now().isoformat()
            self.db_manager.save_devices()

        # ---- NEW: Send LoRa response matching new firmware ----
        response_status = "GRANT" if (is_valid and access_allowed) else "DENY5"
        self.send_access_response(response_status)

        # ---- NEW: Publish log to VPS using new topic ----
        topic = f"iot/{self.config['gateway_id']}/access"
        self.mqtt_manager.publish_to_vps(topic, log_entry)

        if access_allowed:
            logger.info(f"[RFID] {uid}:  ACCESS GRANTED")
        else:
            logger.warning(f"[RFID] {uid}:  ACCESS DENIED ({deny_reason})")

    
    def send_access_response(self, status):
        """Send access response to LoRa gate in new protocol format"""
        try:
            response_bytes = status.encode('utf-8')
            packet = bytearray([0xC0, 0x00, 0x00, 0x00, 0x00, 0x17, len(response_bytes)])
            packet.extend(response_bytes)
            self.serial_port.write(packet)
            logger.info(f"[LoRa] Sent response: {status}")
        except Exception as e:
            logger.error(f"[LoRa] Error sending response: {e}")

    
    def publish_gate_status(self, status, sequence):
        """Publish RFID gate status to VPS"""
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': 'rfid_gate_01',
            'status': status,
            'sequence': sequence,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['vps_status'].format(device_id='rfid_gate_01')
        self.mqtt_manager.publish_to_vps(topic, payload)
    
    def stop(self):
        self.running = False
        if self.serial_port:
            self.serial_port.close()
            logger.info(" LoRa Serial Closed")

# ============= HEARTBEAT =============
def heartbeat_loop(mqtt_manager, interval):
    """Send periodic heartbeat to VPS"""
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
    logger.info(" Starting Gateway 1 (User 1 - RFID Gate via LoRa)")
    logger.info("=" * 60)
    
    # Initialize components
    db_manager = DatabaseManager(CONFIG['db_path'], CONFIG['devices_db'])
    mqtt_manager = VPSMQTTManager(CONFIG)
    
    # Connect to VPS broker only (RFID không dùng MQTT local)
    logger.info(" Connecting to VPS Broker...")
    mqtt_manager.setup_vps_broker()
    time.sleep(2)
    
    # Start LoRa handler for RFID communication
    logger.info(" Starting LoRa Handler...")
    lora_handler = LoRaHandler(CONFIG, db_manager, mqtt_manager)
    
    if lora_handler.connect():
        lora_handler.start()
    else:
        logger.error(" Failed to start LoRa handler. Exiting.")
        return
    
    # Start heartbeat thread
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(mqtt_manager, CONFIG['heartbeat_interval'])
    )
    heartbeat_thread.daemon = True
    heartbeat_thread.start()
    
    logger.info("=" * 60)
    logger.info(" Gateway 1 Running Successfully")
    logger.info(" LoRa: Listening for RFID scans on " + CONFIG['lora_port'])
    logger.info("  VPS: Connected to " + CONFIG['vps_broker']['host'])
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(" Shutting down Gateway 1...")
        lora_handler.stop()
        mqtt_manager.vps_client.loop_stop()
        logger.info(" Gateway 1 stopped")

if __name__ == '__main__':
    main()