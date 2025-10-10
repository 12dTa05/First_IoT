"""
LoRa Handler for Gateway
Manages serial communication with LoRa module (E32)
Handles message parsing, CRC verification, and protocol
"""

import serial
import struct
import threading
import time
from typing import Dict, Any, Optional
from collections import deque


# Message type constants
MESSAGE_TYPES = {
    0x01: 'rfid_scan',
    0x02: 'temp_update',
    0x03: 'motion_detect',
    0x04: 'relay_control',
    0x05: 'passkey',
    0x06: 'gate_status',
    0x07: 'system_status',
    0x08: 'door_status',
    0x80: 'ack',
    0xFF: 'error'
}

# Device type constants
DEVICE_TYPES = {
    0x01: 'rfid_gate',
    0x02: 'relay_fan',
    0x03: 'temp_sensor',
    0x04: 'gateway',
    0x05: 'passkey',
    0x07: 'motion_outdoor',
    0x08: 'motion_indoor'
}


class LoRaHandler:
    """Manages LoRa serial communication"""
    
    def __init__(self, config: Dict[str, Any], security, logger):
        """Initialize LoRa handler"""
        self.config = config
        self.security = security
        self.logger = logger
        
        # Serial connection
        self.serial_conn: Optional[serial.Serial] = None
        self.serial_lock = threading.Lock()
        
        # Buffer for incomplete messages
        self.buffer = b''
        self.buffer_lock = threading.Lock()
        
        # Message queue
        self.message_queue = deque(maxlen=100)
        self.queue_lock = threading.Lock()
        
        # Running state
        self.running = False
        self.receive_thread: Optional[threading.Thread] = None
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'crc_errors': 0,
            'parse_errors': 0
        }
        
        self.logger.info("LoRa handler initialized")
    
    def start(self):
        """Start LoRa communication"""
        if self.running:
            self.logger.warning("LoRa handler already running")
            return
        
        # Open serial port
        if not self._open_serial():
            self.logger.error("Failed to open LoRa serial port")
            return
        
        # Start receive thread
        self.running = True
        self.receive_thread = threading.Thread(
            target=self._receive_worker,
            name="LoRaReceiver",
            daemon=False
        )
        self.receive_thread.start()
        
        self.logger.info("LoRa handler started")
    
    def stop(self):
        """Stop LoRa communication"""
        if not self.running:
            return
        
        self.logger.info("Stopping LoRa handler...")
        self.running = False
        
        # Wait for thread
        if self.receive_thread:
            self.receive_thread.join(timeout=5)
        
        # Close serial
        self._close_serial()
        
        self.logger.info("LoRa handler stopped")
    
    def _open_serial(self) -> bool:
        """Open serial port for LoRa"""
        try:
            with self.serial_lock:
                if self.serial_conn and self.serial_conn.is_open:
                    self.logger.warning("Serial port already open")
                    return True
                
                self.serial_conn = serial.Serial(
                    port=self.config['port'],
                    baudrate=self.config['baudrate'],
                    timeout=0.1,
                    write_timeout=1.0
                )
                
                self.logger.info(
                    f"LoRa serial opened: {self.config['port']} "
                    f"@ {self.config['baudrate']} baud"
                )
                return True
                
        except serial.SerialException as e:
            self.logger.error(f"Serial port error: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error opening serial: {e}")
            return False
    
    def _close_serial(self):
        """Close serial port"""
        try:
            with self.serial_lock:
                if self.serial_conn and self.serial_conn.is_open:
                    self.serial_conn.close()
                    self.logger.info("LoRa serial closed")
        except Exception as e:
            self.logger.error(f"Error closing serial: {e}")
    
    # ========== Receive Thread ==========
    
    def _receive_worker(self):
        """Worker thread to receive and parse LoRa messages"""
        self.logger.info("LoRa receive thread started")
        
        while self.running:
            try:
                # Read available data
                with self.serial_lock:
                    if self.serial_conn and self.serial_conn.in_waiting > 0:
                        new_data = self.serial_conn.read(self.serial_conn.in_waiting)
                        
                        if new_data:
                            with self.buffer_lock:
                                self.buffer += new_data
                
                # Try to parse messages from buffer
                self._parse_buffer()
                
                time.sleep(0.01)  # Small delay to prevent busy loop
                
            except serial.SerialException as e:
                self.logger.error(f"Serial error in receive thread: {e}")
                time.sleep(1)
                # Try to reopen
                self._close_serial()
                time.sleep(2)
                self._open_serial()
                
            except Exception as e:
                self.logger.error(f"Error in receive thread: {e}", exc_info=True)
                time.sleep(1)
        
        self.logger.info("LoRa receive thread stopped")
    
    def _parse_buffer(self):
        """Parse messages from buffer"""
        with self.buffer_lock:
            while True:
                # Look for message header (0x00 0x02 0x17)
                header_idx = self.buffer.find(b'\x00\x02\x17')
                
                if header_idx == -1:
                    # No header found, keep only last 3 bytes in case of split
                    if len(self.buffer) > 3:
                        self.buffer = self.buffer[-3:]
                    break
                
                # Remove data before header
                if header_idx > 0:
                    self.buffer = self.buffer[header_idx:]
                
                # Check if we have enough data for header
                if len(self.buffer) < 12:  # 3 (header) + 9 (min message)
                    break
                
                # Try to parse message
                raw = self.buffer[3:]
                
                # Check minimum length
                if len(raw) < 9:
                    break
                
                # Get payload length
                uid_len = raw[8]
                expected_len = 9 + uid_len + 4  # header + payload + CRC
                
                # Wait for complete message
                if len(raw) < expected_len:
                    break
                
                # Extract complete message
                candidate = self.buffer[:3 + expected_len]
                
                # Parse message
                message = self._parse_message(candidate)
                
                if message:
                    # Add to queue
                    with self.queue_lock:
                        self.message_queue.append(message)
                    
                    self.stats['messages_received'] += 1
                    self.logger.debug(f"LoRa message parsed: {message['header']['msg_type']}")
                
                # Remove processed message from buffer
                self.buffer = self.buffer[3 + expected_len:]
    
    def _parse_message(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Parse LoRa message with CRC verification"""
        try:
            if len(data) < 12 or data[:3] != b'\x00\x02\x17':
                return None
            
            raw = data[3:]
            
            # Parse header
            header_byte0 = raw[0]
            version = header_byte0 & 0x0F
            msg_type_n = (header_byte0 >> 4) & 0x0F
            
            device_byte1 = raw[1]
            device_type_n = device_byte1 & 0x0F
            flags = (device_byte1 >> 4) & 0x0F
            
            seq = struct.unpack('<H', raw[2:4])[0]
            timestamp = struct.unpack('<I', raw[4:8])[0]
            
            # Parse payload
            uid_len = raw[8]
            expected_len = 9 + uid_len + 4
            
            if len(raw) < expected_len:
                return None
            
            payload_data = raw[9:9 + uid_len]
            crc_received = struct.unpack('<I', raw[9 + uid_len:9 + uid_len + 4])[0]
            
            # Verify CRC
            crc_data = raw[:9 + uid_len]
            calculated_crc = self._crc32(crc_data)
            
            if calculated_crc != crc_received:
                self.logger.warning(
                    f"CRC mismatch: calculated={hex(calculated_crc)}, "
                    f"received={hex(crc_received)}"
                )
                self.stats['crc_errors'] += 1
                return None
            
            # Build message
            msg_type_str = MESSAGE_TYPES.get(msg_type_n, 'unknown')
            device_type_str = DEVICE_TYPES.get(device_type_n, 'unknown')
            
            message = {
                'header': {
                    'version': version,
                    'msg_type': msg_type_str,
                    'msg_type_n': msg_type_n,
                    'device_type': device_type_str,
                    'device_type_raw': device_type_n,
                    'flags': flags,
                    'seq': seq,
                    'timestamp': timestamp
                },
                'payload': self._parse_payload(msg_type_n, payload_data),
                'crc': hex(crc_received)
            }
            
            return message
            
        except Exception as e:
            self.logger.error(f"Error parsing message: {e}", exc_info=True)
            self.stats['parse_errors'] += 1
            return None
    
    def _parse_payload(self, msg_type: int, payload_data: bytes) -> Dict[str, Any]:
        """Parse payload based on message type"""
        try:
            if msg_type == 0x01:  # RFID scan
                return {
                    'uid': ''.join(f'{b:02x}' for b in payload_data),
                    'uid_len': len(payload_data)
                }
            
            elif msg_type == 0x06:  # Gate status
                return {
                    'status': payload_data.decode('utf-8', errors='ignore')
                }
            
            elif msg_type == 0x08:  # Door status
                return {
                    'status': payload_data.decode('utf-8', errors='ignore')
                }
            
            else:
                return {
                    'raw': payload_data.hex()
                }
                
        except Exception as e:
            self.logger.error(f"Error parsing payload: {e}")
            return {'raw': payload_data.hex()}
    
    @staticmethod
    def _crc32(data: bytes, poly: int = 0x04C11DB7, 
               init: int = 0xFFFFFFFF, xor_out: int = 0xFFFFFFFF) -> int:
        """Calculate CRC32 for LoRa messages"""
        crc = init
        for byte in data:
            crc ^= (byte << 24)
            for _ in range(8):
                if crc & 0x80000000:
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
                crc &= 0xFFFFFFFF
        return crc ^ xor_out
    
    # ========== Send Functions ==========
    
    def send_response(self, device_type_numeric: int, response_text: str, 
                     retries: int = 3) -> bool:
        """Send response to LoRa device with retry"""
        for attempt in range(retries):
            if self._send_response_once(device_type_numeric, response_text):
                return True
            
            if attempt < retries - 1:
                self.logger.warning(f"Send failed, retry {attempt + 1}/{retries}")
                time.sleep(0.1)
        
        self.logger.error(f"Failed to send response after {retries} attempts")
        return False
    
    def _send_response_once(self, device_type_numeric: int, 
                           response_text: str) -> bool:
        """Send response via LoRa (single attempt)"""
        try:
            with self.serial_lock:
                if not self.serial_conn or not self.serial_conn.is_open:
                    self.logger.error("Serial port not open")
                    return False
                
                # Build packet
                response_data = response_text.encode('utf-8')
                
                # Header: C0 00 00
                head = b'\xC0\x00\x00'
                
                # Address (2 bytes, big-endian)
                addr = struct.pack('>H', int(device_type_numeric) & 0xFFFF)
                
                # Channel
                chan = bytes([23])  # Channel 23
                
                # Length
                length = bytes([len(response_data)])
                
                # Complete packet
                packet = head + addr + chan + length + response_data
                
                # Send
                self.serial_conn.write(packet)
                self.serial_conn.flush()
                
                self.stats['messages_sent'] += 1
                self.logger.debug(f"LoRa >> {response_text} to device {device_type_numeric}")
                
                return True
                
        except serial.SerialTimeoutException:
            self.logger.error("Serial write timeout")
            return False
        except Exception as e:
            self.logger.error(f"Error sending LoRa response: {e}")
            return False
    
    # ========== Receive Functions ==========
    
    def receive_message(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get next message from queue (non-blocking)"""
        with self.queue_lock:
            if self.message_queue:
                return self.message_queue.popleft()
        
        return None
    
    # ========== Statistics ==========
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get LoRa statistics"""
        return {
            'messages_received': self.stats['messages_received'],
            'messages_sent': self.stats['messages_sent'],
            'crc_errors': self.stats['crc_errors'],
            'parse_errors': self.stats['parse_errors'],
            'queue_size': len(self.message_queue)
        }