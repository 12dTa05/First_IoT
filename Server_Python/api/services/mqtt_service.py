import json
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
import ssl
from services.database import db
from services.websocket_manager import ws_manager
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

class MQTTService:
    def __init__(self, config):
        self.config = config
        self.client = mqtt.Client()
        self.setup_client()
    
    def setup_client(self):
        """Configure MQTT client with mTLS"""
        if self.config.get('use_tls', False):
            self.client.tls_set(
                ca_certs=self.config['ca_cert'],
                certfile=self.config['client_cert'],
                keyfile=self.config['client_key'],
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
                self.config.get('port', 8883),
                60
            )
            self.client.loop_start()
            logger.info(f"MQTT service connected to {self.config['host']}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            logger.info("Connected to MQTT broker successfully")
            # Subscribe to all gateway topics
            self.client.subscribe('gateway/+/telemetry/+')
            self.client.subscribe('gateway/+/access/+')
            self.client.subscribe('gateway/+/status/+')
            logger.info("Subscribed to gateway topics")
        else:
            logger.error(f"Connection failed with code {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
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
        """
        Handle telemetry data from temperature sensors
        Topic: gateway/{gateway_id}/telemetry/{device_id}
        """
        try:
            # Extract timestamp from gateway
            timestamp = data.get('timestamp') or data.get('time')
            
            # Extract telemetry values safely
            telemetry_data = data.get('data', {})
            nested_data = telemetry_data.get('data', {})
            
            temperature = nested_data.get('temperature')
            humidity = nested_data.get('humidity')
            
            # Prepare metadata
            metadata = {
                'battery': nested_data.get('battery'),
                'signal': nested_data.get('signal'),
                'raw_data': telemetry_data
            }
            
            # Insert into database
            query = """
                INSERT INTO telemetry (time, device_id, gateway_id, user_id, temperature, humidity, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, %s, %s, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            result = db.query(query, (
                timestamp,
                device_id,
                gateway_id,
                temperature,
                humidity,
                json.dumps(metadata),
                device_id,
                gateway_id
            ))
            
            if result is not None:
                logger.info(f"Telemetry saved: {device_id} - {temperature}°C, {humidity}%")
                
                # Update device last_seen
                self.update_device_last_seen(device_id, gateway_id, timestamp)

                # Broadcast to WebSocket (THÊM MỚI)
                result_ = db.query_one(
                    'SELECT user_id FROM devices WHERE device_id = %s',
                    (device_id,)
                )
                if result_:
                    broadcast_to_websocket(ws_manager.broadcast_telemetry(
                        result_['user_id'],
                        {
                            'device_id': device_id,
                            'temperature': temperature,
                            'humidity': humidity,
                            'timestamp': timestamp
                        }
                    ))
            else:
                logger.warning(f"Device not found: {device_id} on {gateway_id}")
            
        except Exception as e:
            logger.error(f"Error saving telemetry: {e}")
    
    def handle_access(self, gateway_id, device_id, data):
        """
        Handle access control logs
        Topic: gateway/{gateway_id}/access/{device_id}
        """
        try:
            # Extract timestamp from gateway
            timestamp = data.get('timestamp') or data.get('time')
            
            method = data.get('method', 'unknown')
            result = data.get('result', 'unknown')
            
            # Prepare metadata
            metadata = {
                'location': data.get('location'),
                'source': data.get('source'),
                'command_id': data.get('command_id')
            }
            
            # Insert into access_logs
            query = """
                INSERT INTO access_logs (time, device_id, gateway_id, user_id, method, result, 
                                        password_id, rfid_uid, deny_reason, metadata)
                SELECT %s::timestamptz, %s, %s, d.user_id, %s, %s, %s, %s, %s, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            db.query(query, (
                timestamp,
                device_id,
                gateway_id,
                method,
                result,
                data.get('password_id'),
                data.get('rfid_uid'),
                data.get('deny_reason'),
                json.dumps(metadata),
                device_id,
                gateway_id
            ))
        
            logger.info(f"Access log: {device_id} - {method} - {result}")
            
            # Update device last_seen
            self.update_device_last_seen(device_id, gateway_id, timestamp)
            
            # Update password/rfid last_used if access granted
            if result == 'granted':
                if method == 'passkey' and data.get('password_id'):
                    self.update_password_last_used(data.get('password_id'), timestamp)
                elif method == 'rfid' and data.get('rfid_uid'):
                    self.update_rfid_last_used(data.get('rfid_uid'), timestamp)

            result_db = db.query_one('SELECT user_id FROM devices WHERE device_id = %s', (device_id,))
            if result_db:
                broadcast_to_websocket(ws_manager.broadcast_access_event(
                    result_db['user_id'],
                    {
                        'device_id': device_id,
                        'method': method,
                        'result': result,
                        'timestamp': timestamp
                    }
                ))
                
        except Exception as e:
            logger.error(f"Error saving access log: {e}")
    
    def handle_device_status(self, gateway_id, device_id, data):
        """
        Handle device status updates
        Topic: gateway/{gateway_id}/status/{device_id}
        """
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status') or data.get('state', 'unknown')
            
            # Determine if device is online
            is_online = status.lower() in ['online', 'on', 'opened', 'locked', 'unlocked']
            
            # Update device status in devices table
            query = """
                UPDATE devices
                SET status = %s, 
                    is_online = %s,
                    last_seen = %s::timestamptz,
                    updated_at = %s::timestamptz
                WHERE device_id = %s AND gateway_id = %s
            """
            
            db.query(query, (status, is_online, timestamp, timestamp, device_id, gateway_id))
            
            logger.info(f"Device status updated: {device_id} -> {status}")
            
            # Log status change to system_logs
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

            result = db.query_one('SELECT user_id FROM devices WHERE device_id = %s', (device_id,))
            if result:
                broadcast_to_websocket(ws_manager.broadcast_device_status(
                    device_id,
                    result['user_id'],
                    {
                        'status': status,
                        'is_online': is_online,
                        'timestamp': timestamp
                    }
                ))
            
        except Exception as e:
            logger.error(f"Error updating device status: {e}")
    
    def handle_gateway_status(self, gateway_id, data):
        """
        Handle gateway heartbeat status
        Topic: gateway/{gateway_id}/status/gateway
        """
        try:
            timestamp = data.get('timestamp') or data.get('time')
            status = data.get('status', 'online')
            
            # Update gateway in database
            query = """
                UPDATE gateways 
                SET status = %s, 
                    last_heartbeat = %s::timestamptz,
                    updated_at = %s::timestamptz
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
                    is_online = TRUE,
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
                logger.error(f"Failed to publish to {topic}")
                return False
                
        except Exception as e:
            logger.error(f"Error publishing message: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT service disconnected")

    def broadcast_to_websocket(coro):
    """Helper to run async broadcast in sync context"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(coro)
        else:
            loop.run_until_complete(coro)
    except Exception as e:
        logger.error(f'Error broadcasting to WebSocket: {e}')


# Global MQTT service instance
mqtt_service = None

def init_mqtt_service(config):
    """Initialize global MQTT service"""
    global mqtt_service
    mqtt_service = MQTTService(config)
    return mqtt_service.connect()