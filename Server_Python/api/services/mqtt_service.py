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
            logger.info(f'MQTT connected to mqtt://{settings.MQTT_HOST}:{settings.MQTT_PORT}')
            client.subscribe('gateway/#')
            logger.info('Subscribed to gateway/#')
        else:
            logger.error(f'MQTT connection failed with code: {rc}')
    
    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        logger.warning('MQTT connection closed')
    
    def on_message(self, client, userdata, msg):
        logger.info(f'📨 MQTT Received → {msg.topic}: {msg.payload.decode()}')
        self.handle_message(msg.topic, msg.payload)
    
    def handle_message(self, topic, payload):
        try:
            data = json.loads(payload.decode())
            parts = topic.split('/')
            
            if len(parts) < 2:
                return
            
            gateway_id = parts[1] if len(parts) > 1 else None
            msg_type = parts[2] if len(parts) > 2 else None
            device_id = parts[3] if len(parts) > 3 else None
            
            if msg_type == 'telemetry' and device_id:
                self.handle_telemetry(gateway_id, device_id, data)
            elif msg_type == 'access' and device_id:
                self.handle_access(gateway_id, device_id, data)
            elif msg_type == 'status':
                self.handle_gateway_status(gateway_id, data)
            
        except Exception as e:
            logger.error(f'Error handling MQTT message: {e}')
    
    def handle_telemetry(self, gateway_id, device_id, data):
        try:
            query = """
                INSERT INTO telemetry (device_id, gateway_id, user_id, data)
                SELECT %s, %s, d.user_id, %s
                FROM devices d WHERE d.device_id = %s
            """
            db.query(query, (device_id, gateway_id, json.dumps(data), device_id))
            logger.info(f'Saved telemetry: {device_id}')
        except Exception as e:
            logger.error(f'Error saving telemetry: {e}')
    
    def handle_access(self, gateway_id, device_id, data):
        try:
            query = """
                INSERT INTO access_logs (device_id, gateway_id, user_id, rfid_uid, result, method)
                SELECT %s, %s, d.user_id, %s, %s, %s
                FROM devices d WHERE d.device_id = %s
            """
            db.query(query, (
                device_id, 
                gateway_id, 
                data.get('rfid_uid'),
                data.get('result'),
                data.get('method'),
                device_id
            ))
            logger.info(f'Saved access log: {device_id}')
        except Exception as e:
            logger.error(f'Error saving access log: {e}')
    
    def handle_gateway_status(self, gateway_id, data):
        try:
            query = """
                UPDATE gateways 
                SET status = %s, last_heartbeat = NOW()
                WHERE gateway_id = %s
            """
            db.query(query, (data.get('status', 'online'), gateway_id))
            logger.info(f'Updated gateway status: {gateway_id}')
        except Exception as e:
            logger.error(f'Error updating gateway status: {e}')
    
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
            logger.warning('MQTT not connected')
            return False
        
        payload = json.dumps(message) if isinstance(message, dict) else str(message)
        result = self.client.publish(topic, payload, qos=0)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f'📤 MQTT Published → {topic}: {payload}')
            return True
        else:
            logger.error(f'Failed to publish to {topic}')
            return False
    
    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info('MQTT disconnected')

mqtt_service = MQTTService()