import paho.mqtt.client as mqtt
import json
import logging
import asyncio
from queue import Queue
from datetime import datetime, timedelta
from services.database import db
from services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# Queue for WebSocket broadcasts (thread-safe)
ws_broadcast_queue = Queue(maxsize=1000)

class MQTTService:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.connected = False
        
        # Track gateway heartbeats in memory for faster detection
        self.gateway_heartbeats = {}  # {gateway_id: last_heartbeat_time}
        self.expected_heartbeat_interval = 30  # Expected interval in seconds
        
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self.client = mqtt.Client(client_id='vps_mqtt_service', clean_session=False)
            
            if self.config.get('username') and self.config.get('password'):
                self.client.username_pw_set(
                    username=self.config['username'],
                    password=self.config['password']
                )
            
            if self.config.get('use_tls', False):
                self.client.tls_set(
                    ca_certs=self.config.get('ca_certs'),
                    certfile=self.config.get('certfile'),
                    keyfile=self.config.get('keyfile')
                )
            
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message
            
            self.client.connect(
                self.config['host'],
                self.config['port'],
                keepalive=60
            )
            
            self.client.loop_start()
            logger.info(f"MQTT service connecting to {self.config['host']}:{self.config['port']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}", exc_info=True)
            return False
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            logger.info("MQTT service connected to broker")
            
            # Subscribe to all gateway topics with QoS 1 for reliability
            self.client.subscribe('gateway/+/telemetry/+', qos=1)
            self.client.subscribe('gateway/+/access/+', qos=1)
            self.client.subscribe('gateway/+/status/+', qos=1)
            logger.info("Subscribed to gateway topics with QoS 1")
        else:
            self.connected = False
            logger.error(f"Connection failed with code {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection (code {rc}), will attempt reconnect...")
        else:
            logger.info("MQTT service disconnected normally")
    
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
            
            # Validate timestamp to prevent clock drift issues
            timestamp = data.get('timestamp') or data.get('time')
            if timestamp:
                if not self._validate_timestamp(timestamp, gateway_id):
                    logger.warning(f"Invalid timestamp from {gateway_id}: {timestamp}")
                    # Use server time instead
                    timestamp = datetime.now().isoformat()
                    data['timestamp'] = timestamp
            else:
                # If no timestamp provided, use server time
                timestamp = datetime.now().isoformat()
                data['timestamp'] = timestamp
            
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
    
    def _validate_timestamp(self, timestamp, gateway_id):
        """Validate timestamp is within acceptable range (±5 minutes)"""
        try:
            if isinstance(timestamp, str):
                msg_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            else:
                msg_time = datetime.fromtimestamp(timestamp)
            
            now = datetime.now(msg_time.tzinfo) if msg_time.tzinfo else datetime.now()
            time_diff = abs((now - msg_time).total_seconds())
            
            # Allow up to 5 minutes clock drift
            if time_diff > 300:
                logger.warning(f"Gateway {gateway_id} clock drift: {time_diff}s")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating timestamp: {e}")
            return False
    
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
                
                # Update device last_seen and ensure status is online
                self.update_device_last_seen_and_status(device_id, gateway_id, timestamp)

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
            
        except Exception as e:
            logger.error(f"Error saving telemetry: {e}", exc_info=True)
    
    def handle_access(self, gateway_id, device_id, data):
        """Handle access control events (RFID/Keypad)"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            method = data.get('method', 'unknown')
            result = data.get('result', 'unknown')
            identifier = data.get('identifier') or data.get('rfid_uid') or data.get('password_id')
            deny_reason = data.get('deny_reason')
            
            query = """
                INSERT INTO access_logs (time, device_id, gateway_id, user_id, method, result, password_id, rfid_uid, deny_reason, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, %s, %s, 
                       CASE WHEN %s = 'passkey' THEN %s ELSE NULL END,
                       CASE WHEN %s = 'rfid' THEN %s ELSE NULL END,
                       %s, %s
                FROM devices d
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            metadata = json.dumps(data.get('metadata', {}))
            
            db.query(query, (
                timestamp, device_id, gateway_id, method, result,
                method, identifier,  # password_id
                method, identifier,  # rfid_uid
                deny_reason, metadata,
                device_id, gateway_id
            ))
            
            logger.info(f"Access log saved: {device_id} - {method} - {result}")
            
            # Update device last_seen and ensure status is online
            self.update_device_last_seen_and_status(device_id, gateway_id, timestamp)
            
            # Update last_used for password or RFID
            if method == 'passkey' and identifier and result == 'granted':
                self.update_password_last_used(identifier, timestamp)
            elif method == 'rfid' and identifier and result == 'granted':
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
            logger.error(f"Error saving access log: {e}", exc_info=True)
    
    def handle_device_status(self, gateway_id, device_id, data):
        """Handle device status updates - CRITICAL for online/offline tracking"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status') or data.get('state', 'unknown')
            
            # Normalize status values to 'online' or 'offline'
            if status.lower() in ['on', 'online', 'locked', 'unlocked', 'opened', 'closed', 'active', 'ready', 'alive']:
                normalized_status = 'online'
            elif status.lower() in ['off', 'offline', 'error', 'disconnected']:
                normalized_status = 'offline'
            else:
                # Default to online if device is sending status
                normalized_status = 'online'
                logger.debug(f"Unknown status '{status}' from {device_id}, defaulting to online")
            
            # Update device status and last_seen atomically
            query = """
                UPDATE devices
                SET status = %s, 
                    last_seen = %s::timestamptz, 
                    updated_at = %s::timestamptz
                WHERE device_id = %s AND gateway_id = %s
                RETURNING device_id, user_id, device_type
            """
            
            result = db.query(query, (normalized_status, timestamp, timestamp, device_id, gateway_id))
            
            if result and len(result) > 0:
                logger.info(f"Device status updated: {device_id} -> {normalized_status}")
                
                # Log status change to system_logs
                log_query = """
                    INSERT INTO system_logs (time, gateway_id, device_id, user_id, log_type, event, severity, message, metadata)
                    VALUES (%s::timestamptz, %s, %s, %s, 'device_event', 'device_status_change', 'info', %s, %s)
                """
                
                message = f"Device {device_id} status changed to {normalized_status}"
                metadata = json.dumps({
                    'original_status': status,
                    'normalized_status': normalized_status,
                    'device_type': result[0]['device_type'],
                    'raw_data': data
                })
                
                db.query(log_query, (
                    timestamp, gateway_id, device_id, result[0]['user_id'], message, metadata
                ))

                # Queue WebSocket broadcast
                ws_broadcast_queue.put({
                    'type': 'device_status',
                    'user_id': result[0]['user_id'],
                    'device_id': device_id,
                    'data': {
                        'status': normalized_status,
                        'timestamp': timestamp
                    }
                })
            else:
                logger.warning(f"Device not found for status update: {device_id} on {gateway_id}")
            
        except Exception as e:
            logger.error(f"Error updating device status: {e}", exc_info=True)
    
    def handle_gateway_status(self, gateway_id, data):
        """Handle gateway heartbeat status - CRITICAL for gateway online/offline tracking"""
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status', 'online')
            
            # Normalize status
            if status.lower() in ['online', 'active', 'connected']:
                normalized_status = 'online'
            else:
                normalized_status = 'offline'
            
            # Update gateway heartbeat tracking in memory
            self.gateway_heartbeats[gateway_id] = datetime.now()
            
            # Update gateway status and last_seen atomically
            query = """
                UPDATE gateways
                SET status = %s, last_seen = %s::timestamptz, updated_at = %s::timestamptz
                WHERE gateway_id = %s
                RETURNING gateway_id, user_id, name
            """

            result = db.query(query, (normalized_status, timestamp, timestamp, gateway_id))
            
            if result and len(result) > 0:
                logger.info(f"Gateway heartbeat: {gateway_id} -> {normalized_status} "
                          f"(name: {result[0].get('name', 'N/A')})")
                
                # If gateway just came online, mark all its devices as potentially online
                # (they will be marked offline by offline detector if they don't send heartbeat)
                if normalized_status == 'online':
                    # Don't automatically mark devices online - let them send their own status
                    # Just log that gateway is back
                    logger.info(f"Gateway {gateway_id} is online, waiting for device heartbeats")
                
            else:
                logger.warning(f"Gateway not found: {gateway_id}")
            
        except Exception as e:
            logger.error(f"Error updating gateway status: {e}", exc_info=True)
    
    def update_device_last_seen_and_status(self, device_id, gateway_id, timestamp):
        """Update device last_seen and ensure it's marked online if sending data"""
        try:
            query = """
                UPDATE devices
                SET last_seen = %s::timestamptz, status = 'online', updated_at = %s::timestamptz
                WHERE device_id = %s AND gateway_id = %s
            """
            db.query(query, (timestamp, timestamp, device_id, gateway_id))
            
        except Exception as e:
            logger.error(f"Error updating device last_seen: {e}", exc_info=True)
    
    def update_password_last_used(self, password_id, timestamp):
        """Update password last_used timestamp"""
        try:
            query = """
                UPDATE passwords
                SET last_used = %s::timestamptz, updated_at = %s::timestamptz
                WHERE password_id = %s
            """
            db.query(query, (timestamp, timestamp, password_id))
            
        except Exception as e:
            logger.error(f"Error updating password last_used: {e}", exc_info=True)
    
    def update_rfid_last_used(self, uid, timestamp):
        """Update RFID card last_used timestamp"""
        try:
            query = """
                UPDATE rfid_cards
                SET last_used = %s::timestamptz, updated_at = %s::timestamptz
                WHERE uid = %s
            """
            db.query(query, (timestamp, timestamp, uid))
            
        except Exception as e:
            logger.error(f"Error updating rfid last_used: {e}", exc_info=True)
    
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
            logger.error(f"Error publishing message: {e}", exc_info=True)
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
            
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Error processing WebSocket broadcast: {e}", exc_info=True)
            await asyncio.sleep(1)

# Global MQTT service instance
mqtt_service = None

def init_mqtt_service(config):
    """Initialize global MQTT service"""
    global mqtt_service
    mqtt_service = MQTTService(config)
    return mqtt_service.connect()