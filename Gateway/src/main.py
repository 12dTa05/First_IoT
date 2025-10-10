import json
import time
import threading
import queue
import signal
import sys
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

from database import Database
from mqtt_handler import MQTTHandler
from lora_handler import LoRaHandler
from security import SecurityManager
from sync_manager import SyncManager
from logger import setup_logger

class Gateway:
    """Main Gateway Controller with thread-safe operations"""
    
    def __init__(self, config_path: str = "../config/config.json"):
        """Initialize Gateway with configuration"""
        self.logger = setup_logger("Gateway")
        self.logger.info("Initializing Gateway...")
        
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Thread control
        self.running = False
        self.threads = []
        
        # Message queues for inter-thread communication
        self.lora_queue = queue.Queue(maxsize=10)
        self.mqtt_queue = queue.Queue(maxsize=50)
        self.aws_queue = queue.Queue(maxsize=120)
        
        # Initialize components
        self.db = Database(
            db_path=self.config['database']['path'],
            logger=self.logger
        )
        
        self.security = SecurityManager(
            hmac_key=bytes.fromhex(self.config['security']['hmac_key']),
            logger=self.logger
        )
        
        self.mqtt_handler = MQTTHandler(
            config=self.config['broker_mqtt'],
            security=self.security,
            logger=self.logger
        )
        
        self.lora_handler = LoRaHandler(
            config=self.config['lora'],
            security=self.security,
            logger=self.logger
        )
        
        self.sync_manager = SyncManager(
            gateway=self,
            interval=self.config['sync']['interval'],
            logger=self.logger
        )
        
        # Statistics
        self.stats = {
            'lora_received': 0,
            'lora_sent': 0,
            'mqtt_received': 0,
            'mqtt_sent': 0,
            'errors': 0,
            'uptime_start': time.time()
        }
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger.info("Gateway initialized successfully")
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            return config
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            sys.exit(1)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def start(self):
        """Start all gateway threads"""
        if self.running:
            self.logger.warning("Gateway already running")
            return
        
        self.running = True
        self.logger.info("Starting Gateway threads...")
        
        # Start MQTT handlers
        self.mqtt_handler.start(
            on_message_callback=self._on_mqtt_message
        )
        
        # Start LoRa handler
        self.lora_handler.start()
        
        # Start worker threads
        threads_config = [
            ("LoRaProcessor", self._lora_processor_thread),
            ("MQTTProcessor", self._mqtt_processor_thread),
            ("AWSProcessor", self._aws_processor_thread),
            ("SyncManager", self._sync_thread),
            ("HealthMonitor", self._health_monitor_thread)
        ]
        
        for name, target in threads_config:
            thread = threading.Thread(
                target=target,
                name=name,
                daemon=False
            )
            thread.start()
            self.threads.append(thread)
            self.logger.info(f"Started thread: {name}")
        
        self.logger.info("All threads started successfully")
    
    def stop(self):
        """Stop all gateway threads gracefully"""
        if not self.running:
            return
        
        self.logger.info("Stopping Gateway...")
        self.running = False
        
        # Stop handlers
        self.mqtt_handler.stop()
        self.lora_handler.stop()
        
        # Wait for all threads to finish
        for thread in self.threads:
            thread.join(timeout=5)
            if thread.is_alive():
                self.logger.warning(f"Thread {thread.name} did not stop gracefully")
        
        # Save database
        self.db.save_all()
        
        # Print statistics
        self._print_statistics()
        
        self.logger.info("Gateway stopped")
    
    def _print_statistics(self):
        """Print gateway statistics"""
        uptime = time.time() - self.stats['uptime_start']
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        
        self.logger.info("="*50)
        self.logger.info("Gateway Statistics:")
        self.logger.info(f"  Uptime: {hours}h {minutes}m")
        self.logger.info(f"  LoRa Messages: RX={self.stats['lora_received']}, TX={self.stats['lora_sent']}")
        self.logger.info(f"  MQTT Messages: RX={self.stats['mqtt_received']}, TX={self.stats['mqtt_sent']}")
        self.logger.info(f"  Errors: {self.stats['errors']}")
        self.logger.info("="*50)
    
    # ========== MQTT Message Handler ==========
    
    def _on_mqtt_message(self, topic: str, payload: Dict[str, Any]):
        """Callback for MQTT messages - runs in MQTT thread"""
        try:
            self.mqtt_queue.put_nowait({
                'topic': topic,
                'payload': payload,
                'timestamp': time.time()
            })
            self.stats['mqtt_received'] += 1
        except queue.Full:
            self.logger.error("MQTT queue full, dropping message")
            self.stats['errors'] += 1
    
    # ========== Thread Workers ==========
    
    def _lora_processor_thread(self):
        """Process LoRa messages from queue"""
        self.logger.info("LoRa processor thread started")
        
        while self.running:
            try:
                # Check for incoming LoRa messages
                message = self.lora_handler.receive_message(timeout=0.1)
                
                if message:
                    self.stats['lora_received'] += 1
                    self._handle_lora_message(message)
                
            except Exception as e:
                self.logger.error(f"LoRa processor error: {e}", exc_info=True)
                self.stats['errors'] += 1
                time.sleep(1)
        
        self.logger.info("LoRa processor thread stopped")
    
    def _mqtt_processor_thread(self):
        """Process MQTT messages from queue"""
        self.logger.info("MQTT processor thread started")
        
        while self.running:
            try:
                # Get message from queue with timeout
                try:
                    msg = self.mqtt_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Process based on topic
                self._handle_mqtt_message(msg['topic'], msg['payload'])
                
            except Exception as e:
                self.logger.error(f"MQTT processor error: {e}", exc_info=True)
                self.stats['errors'] += 1
        
        self.logger.info("MQTT processor thread stopped")
    
    def _aws_processor_thread(self):
        """Process messages to be sent to AWS"""
        self.logger.info("AWS processor thread started")
        
        while self.running:
            try:
                # Get message from queue with timeout
                try:
                    msg = self.aws_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                
                # Send to AWS IoT
                self._send_to_aws(msg['topic'], msg['payload'])
                
            except Exception as e:
                self.logger.error(f"AWS processor error: {e}", exc_info=True)
                self.stats['errors'] += 1
        
        self.logger.info("AWS processor thread stopped")
    
    def _sync_thread(self):
        """Periodic sync with AWS server"""
        self.logger.info("Sync thread started")
        
        while self.running:
            try:
                self.sync_manager.auto_sync()
                time.sleep(1)
            except Exception as e:
                self.logger.error(f"Sync error: {e}", exc_info=True)
                self.stats['errors'] += 1
        
        self.logger.info("Sync thread stopped")
    
    def _health_monitor_thread(self):
        """Monitor system health and log statistics"""
        self.logger.info("Health monitor thread started")
        
        last_check = time.time()
        check_interval = 60  # 1 minute
        
        while self.running:
            try:
                if time.time() - last_check >= check_interval:
                    last_check = time.time()
                    
                    # Log statistics
                    self.logger.info(
                        f"Health: LoRa RX={self.stats['lora_received']}, "
                        f"MQTT RX={self.stats['mqtt_received']}, "
                        f"Errors={self.stats['errors']}"
                    )
                    
                    # Check queue sizes
                    if self.mqtt_queue.qsize() > 50:
                        self.logger.warning(f"MQTT queue high: {self.mqtt_queue.qsize()}")
                    
                    if self.aws_queue.qsize() > 50:
                        self.logger.warning(f"AWS queue high: {self.aws_queue.qsize()}")
                
                time.sleep(5)
                
            except Exception as e:
                self.logger.error(f"Health monitor error: {e}", exc_info=True)
        
        self.logger.info("Health monitor thread stopped")
    
    # ========== Message Handlers ==========
    
    def _handle_lora_message(self, message: Dict[str, Any]):
        """Handle parsed LoRa message"""
        msg_type = message['header']['msg_type']
        
        self.logger.debug(f"LoRa message: type={msg_type}")
        
        if msg_type == 'rfid_scan':
            self._handle_rfid_scan(message)
        elif msg_type == 'gate_status':
            self._handle_gate_status(message)
        else:
            self.logger.warning(f"Unknown LoRa message type: {msg_type}")
    
    def _handle_rfid_scan(self, message: Dict[str, Any]):
        """Handle RFID scan from gate"""
        uid = message['payload'].get('uid')
        device_type = message['header'].get('device_type_raw', 1)
        
        if not uid:
            self.logger.error("RFID scan missing UID")
            return
        
        # Authenticate with database
        is_valid = self.db.authenticate_rfid(uid)
        
        self.logger.info(f"RFID scan: {uid} -> {'GRANTED' if is_valid else 'DENIED'}")
        
        # Send response via LoRa
        response = 'GRANT' if is_valid else 'DENY5'
        self.lora_handler.send_response(device_type, response)
        self.stats['lora_sent'] += 1
        
        # Log to AWS
        self._queue_aws_message('aws/system/logs', {
            'type': 'access_attempt',
            'method': 'rfid',
            'uid': uid,
            'result': 'granted' if is_valid else 'denied',
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat()
        })
        
        # Update home state if granted
        # if is_valid:
        #     self.db.update_home_state(occupied=True, method='rfid', uid=uid)
    
    def _handle_gate_status(self, message: Dict[str, Any]):
        """Handle gate status update"""
        status = message['payload'].get('status')
        
        self.logger.info(f"Gate status: {status}")
        
        # Forward to AWS
        self._queue_aws_message('aws/system/logs', {
            'type': 'gate_status',
            'status': status,
            'device': message['header']['device_type'],
            'timestamp': datetime.now().isoformat()
        })
    
    def _handle_mqtt_message(self, topic: str, payload: Dict[str, Any]):
        """Handle MQTT message from local devices"""
        # Extract device_id from topic
        parts = topic.split('/')
        if len(parts) < 3:
            self.logger.error(f"Invalid topic format: {topic}")
            return
        
        device_id = parts[2]
        
        if 'telemetry' in topic:
            self._handle_telemetry(device_id, payload)
        elif 'request' in topic:
            self._handle_request(device_id, payload)
        elif 'status' in topic:
            self._handle_status(device_id, payload)
        else:
            self.logger.warning(f"Unknown topic type: {topic}")
    
    def _handle_telemetry(self, device_id: str, payload: Dict[str, Any]):
        """Handle telemetry data from sensors"""
        self.logger.debug(f"Telemetry from {device_id}: {payload.get('msg_type')}")
        
        # Temperature-based automation
        if device_id == 'temp_01' and payload.get('msg_type') == 'temp_update':
            temp = payload.get('data', {}).get('temperature')
            
            if temp is not None:
                self._handle_temperature_automation(temp)
        
        # Forward to AWS
        self._queue_aws_message('aws/sensor/data', {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'data_type': payload.get('msg_type', 'telemetry'),
            'data': payload,
            'timestamp': datetime.now().isoformat()
        })
    
    def _handle_temperature_automation(self, temperature: float):
        """Handle automatic fan control based on temperature"""
        settings = self.db.get_automation_settings()
        
        if not settings.get('auto_fan_enabled', True):
            return
        
        threshold = settings.get('auto_fan_temp_threshold', 28.0)
        should_be_on = (temperature >= threshold)
        
        # Send command to fan
        command = {'cmd': 'fan_on' if should_be_on else 'fan_off'}
        self._send_device_command('fan_01', command)
        
        self.logger.info(f"[AUTO] Temp={temperature}°C → Fan {'ON' if should_be_on else 'OFF'}")
    
    def _handle_request(self, device_id: str, payload: Dict[str, Any]):
        """Handle request from device (e.g., unlock request)"""
        # CRITICAL: Must have HMAC
        if 'hmac' not in payload or 'body' not in payload:
            self.logger.error(f"Request from {device_id} missing HMAC")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'missing_signature'
            })
            
            # Log security alert
            self._queue_aws_message('aws/system/logs', {
                'type': 'security_alert',
                'event': 'missing_hmac',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            return
        
        # Verify HMAC
        if not self.security.verify_hmac(payload['body'], payload['hmac']):
            self.logger.error(f"HMAC verification failed for {device_id}")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_signature'
            })
            
            # Log security alert
            self._queue_aws_message('aws/system/logs', {
                'type': 'security_alert',
                'event': 'hmac_verification_failed',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            return
        
        # Parse body
        try:
            body = json.loads(payload['body'])
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON in body from {device_id}")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_json'
            })
            return
        
        # Check for replay attack
        if not self.security.verify_freshness(
            timestamp=body.get('ts', 0),
            nonce=body.get('nonce', 0)
        ):
            self.logger.error(f"Replay attack detected from {device_id}")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'replay_attack'
            })
            
            # Log security alert
            self._queue_aws_message('aws/system/logs', {
                'type': 'security_alert',
                'event': 'replay_attack_detected',
                'device_id': device_id,
                'timestamp': datetime.now().isoformat()
            })
            return
        
        # Process command
        cmd = body.get('cmd')
        
        if cmd == 'unlock_request':
            self._handle_passkey_request(device_id, body)
        else:
            self.logger.warning(f"Unknown request command from {device_id}: {cmd}")
    
    def _handle_passkey_request(self, device_id: str, body: Dict[str, Any]):
        """Handle password unlock request"""
        password_hash = body.get('pw')
        client_id = body.get('client_id')
        
        if not password_hash:
            self.logger.error("No password provided")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'no_password'
            })
            return
        
        # Authenticate password
        is_valid, pwd_id = self.db.authenticate_passkey(password_hash)
        
        # Log to AWS
        log_entry = {
            'type': 'access_attempt',
            'method': 'passkey',
            'device_id': device_id,
            'client_id': client_id,
            'result': 'granted' if is_valid else 'denied',
            'timestamp': datetime.now().isoformat()
        }
        
        if is_valid and pwd_id:
            log_entry['password_id'] = pwd_id
        
        self._queue_aws_message('aws/system/logs', log_entry)
        
        # Send response
        if is_valid:
            self.logger.info(f"Access granted for password ID: {pwd_id}")
            self._send_device_response(device_id, {'cmd': 'OPEN'})
            
            # Update home state
            self.db.update_home_state(
                occupied=True,
                method='passkey',
                password_id=pwd_id
            )
        else:
            self.logger.info("Access denied - invalid password")
            self._send_device_response(device_id, {
                'cmd': 'LOCK',
                'reason': 'invalid_password'
            })
    
    def _handle_status(self, device_id: str, payload: Dict[str, Any]):
        """Handle status update from device"""
        self.logger.debug(f"Status from {device_id}: {payload.get('state')}")
        
        # Forward to AWS
        self._queue_aws_message('aws/system/logs', {
            'type': 'device_status',
            'device_id': device_id,
            'status': payload,
            'timestamp': datetime.now().isoformat()
        })
    
    # ========== Send Functions ==========
    
    def _send_device_command(self, device_id: str, command: Dict[str, Any]):
        """Send command to device via MQTT"""
        topic = f"home/devices/{device_id}/command"
        self.mqtt_handler.publish(topic, command)
        self.stats['mqtt_sent'] += 1
    
    def _send_device_response(self, device_id: str, response: Dict[str, Any]):
        """Send response to device via MQTT"""
        topic = f"home/devices/{device_id}/command"
        self.mqtt_handler.publish(topic, response)
        self.stats['mqtt_sent'] += 1
    
    def _queue_aws_message(self, topic: str, payload: Dict[str, Any]):
        """Queue message to be sent to AWS"""
        try:
            self.aws_queue.put_nowait({
                'topic': topic,
                'payload': payload
            })
        except queue.Full:
            self.logger.error("AWS queue full, dropping message")
            self.stats['errors'] += 1
    
    def _send_to_aws(self, topic: str, payload: Dict[str, Any]):
        """Send message to AWS IoT"""
        # Implementation depends on AWS IoT SDK
        # This is a placeholder
        self.logger.debug(f"Sending to AWS: {topic}")
        # aws_iot_client.publish(topic, json.dumps(payload))
    
    def run(self):
        """Run the gateway (blocking)"""
        self.start()
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        finally:
            self.stop()


def main():
    """Main entry point"""
    gateway = Gateway(config_path="../config/config.json")
    gateway.run()


if __name__ == "__main__":
    main()