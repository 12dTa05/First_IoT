import paho.mqtt.client as mqtt
import json
import logging
from config.settings import settings
from services.database import db

logger = logging.getLogger(__name__)

class MQTTService:
    def __init__(self):
        self.client = None
        self.connected = False
    
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info(f' MQTT connected to mqtt://{settings.MQTT_HOST}:{settings.MQTT_PORT}')
            client.subscribe('gateway/#')
            logger.info(' Subscribed to gateway/#')
        else:
            logger.error(f' MQTT connection failed with code: {rc}')
    
    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        logger.warning(' MQTT connection closed')
    
    def on_message(self, client, userdata, msg):
        logger.info(f' MQTT Received → {msg.topic}: {msg.payload.decode()}')
        self.handle_message(msg.topic, msg.payload)
    
    def handle_message(self, topic, payload):
        try:
            data = json.loads(payload.decode())
            parts = topic.split('/')
            
            # Validate topic structure: gateway/{gateway_id}/{msg_type}/{device_id}
            if len(parts) < 3:
                logger.warning(f'Invalid topic structure: {topic}')
                return
            
            gateway_id = parts[1] if len(parts) > 1 else None
            msg_type = parts[2] if len(parts) > 2 else None
            device_or_entity = parts[3] if len(parts) > 3 else None
            
            # CRITICAL FIX: Distinguish between gateway status and device status
            if msg_type == 'status':
                # Check if this is a gateway status or device status
                if device_or_entity == 'gateway':
                    # This is gateway heartbeat status
                    # Topic: gateway/{gateway_id}/status/gateway
                    self.handle_gateway_status(gateway_id, data)
                elif device_or_entity:
                    # This is device status
                    # Topic: gateway/{gateway_id}/status/{device_id}
                    self.handle_device_status(gateway_id, device_or_entity, data)
                else:
                    logger.warning(f'Status message without device/gateway identifier: {topic}')
            
            elif msg_type == 'telemetry' and device_or_entity:
                # Topic: gateway/{gateway_id}/telemetry/{device_id}
                self.handle_telemetry(gateway_id, device_or_entity, data)
            
            elif msg_type == 'access' and device_or_entity:
                # Topic: gateway/{gateway_id}/access/{device_id}
                self.handle_access(gateway_id, device_or_entity, data)
            
            else:
                logger.debug(f'Unhandled message type: {msg_type} from {topic}')
            
        except json.JSONDecodeError as e:
            logger.error(f'JSON decode error: {e}')
        except Exception as e:
            logger.error(f'Error handling MQTT message: {e}', exc_info=True)
    
    def handle_telemetry(self, gateway_id, device_id, data):
        """Handle device telemetry data"""
        time = f"'{data.get('timestamp')}'::timestamptz"
        temp = data.get('data').get('data').get('temperature')
        humid = data.get('data').get('data').get('humidity')
        try:
            query = f"""
                INSERT INTO telemetry (time, device_id, gateway_id, user_id, temperature, humidity, data)
                SELECT {time} , %s, %s, d.user_id, {temp}, {humid}, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            result = db.query(
                query, 
                (device_id, gateway_id, json.dumps(data), device_id, gateway_id)
            )
            
            if result is not None:
                logger.info(f' Saved telemetry: {device_id}')
            else:
                logger.warning(f' Device not found: {device_id} on {gateway_id}')
                
        except Exception as e:
            logger.error(f' Error saving telemetry: {e}')
    
    def handle_access(self, gateway_id, device_id, data):
        """Handle access control logs"""
        try:
            query = """
                INSERT INTO access_logs (device_id, gateway_id, user_id, rfid_uid, result, method, deny_reason, password_id, metadata)
                SELECT %s, %s, d.user_id, %s, %s, %s, %s, %s, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            result = db.query(query, (
                device_id, 
                gateway_id, 
                data.get('rfid_uid'),
                data.get('result'),
                data.get('method'),
                data.get('deny_reason'),
                data.get('password_id'),
                json.dumps(data.get('metadata', {})),
                device_id,
                gateway_id
            ))
            
            if result is not None:
                result_emoji = '' if data.get('result') == 'granted' else ''
                logger.info(f'{result_emoji} Access log saved: {device_id} - {data.get("result")}')
            else:
                logger.warning(f' Device not found: {device_id} on {gateway_id}')
                
        except Exception as e:
            logger.error(f' Error saving access log: {e}')
    
    def handle_device_status(self, gateway_id, device_id, data):
        """
        Handle device status updates (NOT gateway status)
        Topic: gateway/{gateway_id}/status/{device_id}
        """
        try:
            status = data.get('status') or data.get('state', 'unknown')
            sequence = data.get('sequence')
            metadata = data.get('metadata', {})
            
            # Insert into device_status table (time-series)
            query_insert = """
                INSERT INTO device_status (time, device_id, gateway_id, user_id, status, sequence, metadata)
                SELECT NOW(), %s, %s, d.user_id, %s, %s, %s
                FROM devices d 
                WHERE d.device_id = %s AND d.gateway_id = %s
            """
            
            db.query(query_insert, (
                device_id, 
                gateway_id, 
                status, 
                sequence,
                json.dumps(metadata),
                device_id,
                gateway_id
            ))
            
            logger.info(f' Saved device status: {device_id} → {status} (seq: {sequence})')
            
            # Also update the device's current status in devices table
            query_update = """
                UPDATE devices
                SET status = %s, last_seen = NOW()
                WHERE device_id = %s AND gateway_id = %s
            """
            
            db.query(query_update, (status, device_id, gateway_id))
            
        except Exception as e:
            logger.error(f' Error saving device status: {e}')
    
    def handle_gateway_status(self, gateway_id, data):
        """
        Handle gateway heartbeat status
        Topic: gateway/{gateway_id}/status/gateway
        """
        try:
            status = data.get('status', 'online')
            
            # Update gateway status and last_heartbeat in gateways table
            query = """
                UPDATE gateways 
                SET status = %s, 
                    last_heartbeat = NOW()
                WHERE gateway_id = %s
            """
            
            db.query(query, (status, gateway_id))
            logger.info(f' Gateway heartbeat: {gateway_id} → {status}')
            
            # Optionally log to system_logs
            query_log = """
                INSERT INTO system_logs (time, gateway_id, user_id, log_type, event, severity, message, metadata)
                SELECT NOW(), %s, g.user_id, 'system_event', 'heartbeat', 'info', 'Gateway heartbeat received', %s
                FROM gateways g
                WHERE g.gateway_id = %s
            """
            
            db.query(query_log, (
                gateway_id, 
                json.dumps(data),
                gateway_id
            ))
            
        except Exception as e:
            logger.error(f' Error updating gateway status: {e}')
    
    def connect(self):
        try:
            self.client = mqtt.Client(client_id='iot-api-server', clean_session=False)
            
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message
            
            self.client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
            self.client.loop_start()
            
            return True
        except Exception as e:
            logger.error(f'Failed to connect MQTT: {e}')
            raise e
    
    def publish(self, topic, message):
        if not self.connected:
            logger.warning(' MQTT not connected')
            return False
        
        payload = json.dumps(message) if isinstance(message, dict) else str(message)
        result = self.client.publish(topic, payload, qos=0)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f' MQTT Published → {topic}: {payload}')
            return True
        else:
            logger.error(f' Failed to publish to {topic}')
            return False
    
    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info('MQTT disconnected')

mqtt_service = MQTTService()