import json
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
import ssl
from services.database import db
from services.websocket_manager import ws_manager
import asyncio
from queue import Queue
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Global queue for WebSocket broadcasts from MQTT thread
ws_broadcast_queue = Queue()

class MQTTService:
    def __init__(self, config):
        self.config = config
        self.client = mqtt.Client()
        self.connected = False
        self.setup_client()
    
    def setup_client(self):
        """Configure MQTT client with authentication"""
        # Set username/password if provided
        if 'username' in self.config and 'password' in self.config:
            self.client.username_pw_set(
                self.config['username'],
                self.config['password']
            )
        
        # Configure TLS if enabled
        if self.config.get('use_tls', False):
            self.client.tls_set(
                ca_certs=self.config.get('ca_cert'),
                certfile=self.config.get('client_cert'),
                keyfile=self.config.get('client_key'),
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
        
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self.client.connect(
                self.config['host'],
                self.config.get('port', 1883),
                60
            )
            self.client.loop_start()
            logger.info(f"MQTT service connected to {self.config['host']}:{self.config.get('port', 1883)}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            logger.info("Connected to MQTT broker successfully")
            # Subscribe to all gateway topics
            self.client.subscribe('gateway/+/telemetry/+')
            self.client.subscribe('gateway/+/access/+')
            self.client.subscribe('gateway/+/status/+')
            logger.info("Subscribed to gateway topics")
        else:
            self.connected = False
            logger.error(f"Connection failed with code {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection (code {rc}), attempting reconnect...")
    
    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.debug(f"Received message on {topic}")
            
            # Parse topic: gateway/{gateway_id}/{msg_type}/{device_or_entity}
            parts = topic.split('/')
            if len(parts) < 3:
                logger.warning(f"Invalid topic format: {topic}")
                return
            
            gateway_id = parts[1]
            msg_type = parts[2]
            device_or_entity = parts[3] if len(parts) > 3 else None
            
            # Parse JSON payload
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON payload from {topic}")
                return
            
            # Route message to appropriate handler
            if msg_type == 'telemetry' and device_or_entity:
                self.handle_telemetry(gateway_id, device_or_entity, data)
            
            elif msg_type == 'access' and device_or_entity:
                self.handle_access(gateway_id, device_or_entity, data)
            
            elif msg_type == 'status':
                if device_or_entity == 'gateway':
                    self.handle_gateway_status(gateway_id, data)
                elif device_or_entity:
                    self.handle_device_status(gateway_id, device_or_entity, data)
            
            else:
                logger.debug(f"Unhandled message type: {msg_type}")
            
        except Exception as e:
            logger.error(f"Error handling MQTT message: {e}", exc_info=True)
    
    def handle_telemetry(self, gateway_id, device_id, data):
        """Handle telemetry data from temperature sensors"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            telemetry_data = data.get('data', {})
            nested_data = telemetry_data.get('data', {})
            
            temperature = nested_data.get('temperature')
            humidity = nested_data.get('humidity')
            
            metadata = {
                'battery': nested_data.get('battery'),
                'signal': nested_data.get('signal'),
                'raw_data': telemetry_data
            }
            
            query = """
                INSERT INTO telemetry (time, device_id, gateway_id, user_id, temperature, humidity, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, %s, %s, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            result = db.query(query, (
                timestamp, device_id, gateway_id,
                temperature, humidity, json.dumps(metadata),
                device_id, gateway_id
            ))
            
            if result is not None:
                logger.info(f"Telemetry saved: {device_id} - {temperature}°C, {humidity}%")
                self.update_device_last_seen(device_id, gateway_id, timestamp)

                # Queue WebSocket broadcast (thread-safe)
                result_user = db.query_one(
                    'SELECT user_id FROM devices WHERE device_id = %s',
                    (device_id,)
                )
                if result_user:
                    ws_broadcast_queue.put({
                        'type': 'telemetry',
                        'user_id': result_user['user_id'],
                        'data': {
                            'device_id': device_id,
                            'temperature': temperature,
                            'humidity': humidity,
                            'timestamp': timestamp
                        }
                    })
            else:
                logger.warning(f"Device not found: {device_id} on {gateway_id}")

            self.update_device_last_seen(device_id, gateway_id, timestamp)
            
        except Exception as e:
            logger.error(f"Error saving telemetry: {e}")
    
    def handle_access(self, gateway_id, device_id, data):
        """Handle access control events (RFID/Keypad)"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            method = data.get('method', 'unknown')
            result = data.get('result', 'unknown')
            identifier = data.get('identifier') or data.get('uid') or data.get('password')
            
            query = """
                INSERT INTO access_logs (time, device_id, gateway_id, user_id, method, result, identifier, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, %s, %s, %s, %s
                FROM devices d
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            metadata = json.dumps(data.get('metadata', {}))
            
            db.query(query, (
                timestamp, device_id, gateway_id,
                method, result, identifier, metadata,
                device_id, gateway_id
            ))
            
            logger.info(f"Access log saved: {device_id} - {method} - {result}")
            
            self.update_device_last_seen(device_id, gateway_id, timestamp)
            
            # Update last_used for password or RFID
            if method == 'password' and identifier:
                self.update_password_last_used(identifier, timestamp)
            elif method == 'rfid' and identifier:
                self.update_rfid_last_used(identifier, timestamp)
            
            # Queue WebSocket broadcast
            result_user = db.query_one(
                'SELECT user_id FROM devices WHERE device_id = %s',
                (device_id,)
            )
            if result_user:
                ws_broadcast_queue.put({
                    'type': 'access_event',
                    'user_id': result_user['user_id'],
                    'data': {
                        'device_id': device_id,
                        'method': method,
                        'result': result,
                        'timestamp': timestamp
                    }
                })
                
        except Exception as e:
            logger.error(f"Error saving access log: {e}")
    
    def handle_device_status(self, gateway_id, device_id, data):
        """Handle device status updates"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status') or data.get('state', 'unknown')
            
            if status.lower() in ['on', 'locked', 'unlocked', 'opened', 'closed', 'active']:
                status = 'online'
            elif status.lower() == 'offline':
                status = 'offline'
            else:
                status = 'online'

            query = """
                UPDATE devices
                SET status = %s, last_seen = %s::timestamptz, updated_at = %s::timestamptz
                WHERE device_id = %s AND gateway_id = %s
            """
            
            db.query(query, (status, timestamp, timestamp, device_id, gateway_id))
            logger.info(f"Device status updated: {device_id} -> {status}")
            
            log_query = """
                INSERT INTO system_logs (time, gateway_id, device_id, user_id, log_type, event, severity, message, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, 'device_event', 'device_status_change', 'info', %s, %s
                FROM devices d
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            message = f"Device {device_id} status changed to {status}"
            metadata = json.dumps(data.get('metadata', {}))
            
            db.query(log_query, (
                timestamp, gateway_id, device_id, message, metadata, device_id, gateway_id
            ))

            # Queue WebSocket broadcast
            result_user = db.query_one(
                'SELECT user_id FROM devices WHERE device_id = %s',
                (device_id,)
            )
            if result_user:
                ws_broadcast_queue.put({
                    'type': 'device_status',
                    'user_id': result_user['user_id'],
                    'device_id': device_id,
                    'data': {
                        'status': status,
                        'timestamp': timestamp
                    }
                })
            
        except Exception as e:
            logger.error(f"Error updating device status: {e}")
    
    def handle_gateway_status(self, gateway_id, data):
        """Handle gateway heartbeat status"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status', 'online')
            
            query = """
                UPDATE gateways 
                SET status = %s, last_seen = %s::timestamptz, updated_at = %s::timestamptz
                WHERE gateway_id = %s
            """
            
            db.query(query, (status, timestamp, timestamp, gateway_id))
            logger.info(f"Gateway heartbeat: {gateway_id} -> {status}")
            
        except Exception as e:
            logger.error(f"Error updating gateway status: {e}")
    
    def update_device_last_seen(self, device_id, gateway_id, timestamp):
        """Update device last_seen timestamp"""
        try:
            query = """
                UPDATE devices
                SET last_seen = %s::timestamptz,
                    updated_at = %s::timestamptz
                WHERE device_id = %s AND gateway_id = %s
            """
            db.query(query, (timestamp, timestamp, device_id, gateway_id))
        except Exception as e:
            logger.error(f"Error updating last_seen: {e}")
    
    def update_password_last_used(self, password_id, timestamp):
        """Update password last_used timestamp"""
        try:
            query = """
                UPDATE passwords
                SET last_used = %s::timestamptz
                WHERE password_id = %s
            """
            db.query(query, (timestamp, password_id))
        except Exception as e:
            logger.error(f"Error updating password last_used: {e}")
    
    def update_rfid_last_used(self, uid, timestamp):
        """Update RFID card last_used timestamp"""
        try:
            query = """
                UPDATE rfid_cards
                SET last_used = %s::timestamptz
                WHERE uid = %s
            """
            db.query(query, (timestamp, uid))
        except Exception as e:
            logger.error(f"Error updating rfid last_used: {e}")
    
    def publish(self, topic, message):
        """Publish message to MQTT broker"""
        try:
            if isinstance(message, dict):
                message = json.dumps(message)
            
            result = self.client.publish(topic, message, qos=1)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.debug(f"Published to {topic}")
                return True
            else:
                logger.error(f"Failed to publish to {topic}: rc={result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Error publishing message: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False
        logger.info("MQTT service disconnected")

# Background task to process WebSocket broadcasts
async def process_websocket_broadcasts():
    """Process WebSocket broadcasts from queue"""
    logger.info("WebSocket broadcast processor started")
    while True:
        try:
            # Non-blocking get from queue
            if not ws_broadcast_queue.empty():
                msg = ws_broadcast_queue.get_nowait()
                
                msg_type = msg.get('type')
                user_id = msg.get('user_id')
                data = msg.get('data')
                
                if msg_type == 'telemetry':
                    await ws_manager.broadcast_telemetry(user_id, data)
                elif msg_type == 'access_event':
                    await ws_manager.broadcast_access_event(user_id, data)
                elif msg_type == 'device_status':
                    device_id = msg.get('device_id')
                    await ws_manager.broadcast_device_status(device_id, user_id, data)
                elif msg_type == 'alert':
                    await ws_manager.broadcast_alert(user_id, data)
            
            # Small delay to prevent CPU spinning
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error processing WebSocket broadcast: {e}")
            await asyncio.sleep(1)

# Global MQTT service instance
mqtt_service = None

def init_mqtt_service(config):
    """Initialize global MQTT service"""
    global mqtt_service
    mqtt_service = MQTTService(config)
    return mqtt_service.connect()