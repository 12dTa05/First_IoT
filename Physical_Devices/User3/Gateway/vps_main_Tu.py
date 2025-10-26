import time
import json
import os
from datetime import datetime
import paho.mqtt.client as mqtt
import ssl
import threading
import logging
from logging.handlers import RotatingFileHandler

# ============= LOGGING SETUP =============
def setup_logging():
    log_dir = './logs'
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger('gateway3')
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'gateway3.log'),
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
    'gateway_id': 'Gateway3',
    'user_id': 'user3',
    
    'local_broker': {
        'host': '192.168.1.205',  # Local broker trên cùng máy với gateway
        'port': 1884,
        'use_tls': True,
        'ca_cert': './certs/ca.cert.pem',
        'username': 'Gateway3',
        'password': '125'
    },
    
    'vps_broker': {
        'host': '159.223.63.61',
        'port': 8883,
        'client_id': 'Gateway3',
        'ca_cert': './certs/ca.cert.pem',
        'cert_file': './certs/gateway3.cert.pem',
        'key_file': './certs/gateway3.key.pem',
    },
    
    'topics': {
        # Local topics - Temp and Fan devices via WiFi/MQTT
        'local_temp_telemetry': 'home/devices/temp_01/telemetry',
        'local_temp_status': 'home/devices/temp_01/status',
        'local_fan_status': 'home/devices/fan_01/status',
        'local_fan_telemetry': 'home/devices/fan_01/telemetry',
        'local_fan_command': 'home/devices/fan_01/command',
        
        # VPS topics
        'vps_telemetry': 'gateway/Gateway3/telemetry/{device_id}',
        'vps_status': 'gateway/Gateway3/status/{device_id}',
        'vps_command': 'gateway/Gateway3/command/#',
        'vps_gateway_status': 'gateway/Gateway3/status/gateway',
    },
    
    'db_path': './data',
    'automation': {
        'auto_fan_enabled': True,
        'temp_threshold': 30.0,
        'hysteresis': 1.0,  # Turn off when temp drops 1 degree below threshold
    },
    'heartbeat_interval': 60,
}

# ============= AUTOMATION MANAGER =============
class AutomationManager:
    def __init__(self, config, mqtt_manager):
        self.config = config
        self.mqtt_manager = mqtt_manager
        self.last_temperature = None
        self.fan_state = False
        
    def handle_temperature(self, temperature):
        """Handle temperature changes and control fan automatically"""
        self.last_temperature = temperature
        
        if not self.config['automation']['auto_fan_enabled']:
            return
        
        threshold = self.config['automation']['temp_threshold']
        hysteresis = self.config['automation']['hysteresis']
        
        # Turn ON fan if temp exceeds threshold
        if temperature > threshold and not self.fan_state:
            self.control_fan('on')
            logger.info(f" AUTO: Temp {temperature}°C > {threshold}°C → Fan ON")
            
        # Turn OFF fan if temp drops below (threshold - hysteresis)
        elif temperature <= (threshold - hysteresis) and self.fan_state:
            self.control_fan('off')
            logger.info(f" AUTO: Temp {temperature}°C <= {threshold - hysteresis}°C → Fan OFF")
    
    def control_fan(self, command):
        """Send fan control command"""
        payload = {
            'command': command,
            'auto': True,
            'timestamp': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['local_fan_command']
        self.mqtt_manager.local_client.publish(topic, json.dumps(payload))
        
        self.fan_state = (command == 'on')
        logger.info(f" Fan command sent: {command.upper()}")

# ============= MQTT MANAGER =============
class MQTTManager:
    def __init__(self, config, automation_manager=None):
        self.config = config
        self.automation_manager = automation_manager
        self.local_client = None
        self.vps_client = None
        self.connected_local = False
        self.connected_vps = False
        
    def set_automation_manager(self, automation_manager):
        self.automation_manager = automation_manager
        
    def setup_local_broker(self):
        """Connect to local MQTT broker for Temp and Fan devices"""
        self.local_client = mqtt.Client(client_id=f"{self.config['gateway_id']}_local")
        
        if self.config['local_broker']['use_tls']:
            self.local_client.tls_set(
                ca_certs=self.config['local_broker']['ca_cert'],
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
        
        self.local_client.username_pw_set(
            self.config['local_broker']['username'],
            self.config['local_broker']['password']
        )
        
        self.local_client.on_connect = self.on_local_connect
        self.local_client.on_disconnect = self.on_local_disconnect
        self.local_client.on_message = self.on_local_message
        
        try:
            self.local_client.connect(
                self.config['local_broker']['host'],
                self.config['local_broker']['port'],
                60
            )
            self.local_client.loop_start()
            logger.info(" Connected to Local Broker")
        except Exception as e:
            logger.error(f" Failed to connect to Local Broker: {e}")
    
    def setup_vps_broker(self):
        """Connect to VPS MQTT broker"""
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
    
    def on_local_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_local = True
            logger.info(" Local Broker Connected")
            
            # Subscribe to temp and fan topics
            topics = [
                self.config['topics']['local_temp_telemetry'],
                self.config['topics']['local_temp_status'],
                self.config['topics']['local_fan_status'],
                self.config['topics']['local_fan_telemetry']
            ]
            for topic in topics:
                client.subscribe(topic)
                logger.info(f" Subscribed to: {topic}")
        else:
            logger.error(f" Local Broker Connection Failed: {rc}")
    
    def on_local_disconnect(self, client, userdata, rc):
        self.connected_local = False
        logger.warning(" Local Broker Disconnected")
    
    def on_local_message(self, client, userdata, msg):
        """Handle messages from local devices (Temp & Fan)"""
        try:
            logger.info(f" Local message: {msg.topic}")
            data = json.loads(msg.payload.decode())
            
            if 'telemetry' in msg.topic:
                self.handle_telemetry(msg.topic, data)
            elif 'status' in msg.topic:
                self.forward_status_to_vps(msg.topic, data)
                
        except Exception as e:
            logger.error(f" Error processing local message: {e}")
    
    def handle_telemetry(self, topic, data):
        """Handle telemetry data from devices"""
        # Identify device from topic
        if 'temp_01' in topic:
            device_id = 'temp_01'
            temperature = data.get('temperature')
            humidity = data.get('humidity')
            
            logger.info(f" Temperature: {temperature}°C, Humidity: {humidity}%")
            
            # Forward to VPS
            payload = {
                'gateway_id': self.config['gateway_id'],
                'device_id': device_id,
                'temperature': temperature,
                'humidity': humidity,
                'timestamp': datetime.now().isoformat()
            }
            
            vps_topic = self.config['topics']['vps_telemetry'].format(device_id=device_id)
            self.publish_to_vps(vps_topic, payload)
            
            # Trigger automation if temperature available
            if self.automation_manager and temperature is not None:
                self.automation_manager.handle_temperature(temperature)
                
        elif 'fan_01' in topic:
            device_id = 'fan_01'
            
            # Forward fan telemetry to VPS if needed
            payload = {
                'gateway_id': self.config['gateway_id'],
                'device_id': device_id,
                'data': data,
                'timestamp': datetime.now().isoformat()
            }
            
            vps_topic = self.config['topics']['vps_telemetry'].format(device_id=device_id)
            self.publish_to_vps(vps_topic, payload)
    
    def forward_status_to_vps(self, local_topic, data):
        """Forward device status from local to VPS"""
        device_id = 'fan_01' if 'fan' in local_topic else 'temp_01'
        
        payload = {
            'gateway_id': self.config['gateway_id'],
            'device_id': device_id,
            'status': data.get('status', 'unknown'),
            'timestamp': datetime.now().isoformat()
        }
        
        vps_topic = self.config['topics']['vps_status'].format(device_id=device_id)
        self.publish_to_vps(vps_topic, payload)
    
    def on_vps_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected_vps = True
            logger.info(" VPS Broker Connected")
            
            topic = self.config['topics']['vps_command']
            client.subscribe(topic)
            logger.info(f" Subscribed to: {topic}")
            
            self.publish_gateway_status('online')
        else:
            logger.error(f" VPS Broker Connection Failed: {rc}")
    
    def on_vps_disconnect(self, client, userdata, rc):
        self.connected_vps = False
        logger.warning(" VPS Broker Disconnected")
    
    def on_vps_message(self, client, userdata, msg):
        """Handle commands from VPS"""
        try:
            logger.info(f" VPS Command: {msg.topic}")
            data = json.loads(msg.payload.decode())
            
            # Parse command topic: gateway/Gateway3/command/{device_id}
            parts = msg.topic.split('/')
            if len(parts) >= 4:
                device_id = parts[3]
                command = data.get('command')
                
                if device_id == 'fan_01' and command:
                    self.forward_fan_command(command, data)
                    
        except Exception as e:
            logger.error(f" Error processing VPS message: {e}")
    
    def forward_fan_command(self, command, data):
        """Forward fan control command from VPS to local device"""
        payload = {
            'command': command,
            'auto': data.get('auto', False),
            'timestamp': datetime.now().isoformat()
        }
        
        topic = self.config['topics']['local_fan_command']
        self.local_client.publish(topic, json.dumps(payload))
        logger.info(f" Forwarded fan command from VPS: {command.upper()}")
    
    def publish_to_vps(self, topic, payload):
        if self.connected_vps:
            self.vps_client.publish(topic, json.dumps(payload), qos=1)
            logger.debug(f" Published to VPS: {topic}")
        else:
            logger.warning(" VPS not connected, message buffered locally")
    
    def publish_gateway_status(self, status):
        payload = {
            'gateway_id': self.config['gateway_id'],
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        topic = self.config['topics']['vps_gateway_status']
        self.publish_to_vps(topic, payload)

# ============= HEARTBEAT =============
def heartbeat_loop(mqtt_manager, interval):
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
    logger.info(" Starting Gateway 3 (User 3 - Temp + Fan via MQTT)")
    logger.info("=" * 60)
    
    mqtt_manager = MQTTManager(CONFIG)
    automation_manager = AutomationManager(CONFIG, mqtt_manager)
    mqtt_manager.set_automation_manager(automation_manager)
    
    # Connect to both local and VPS brokers
    logger.info(" Connecting to Local Broker...")
    mqtt_manager.setup_local_broker()
    time.sleep(2)
    
    logger.info(" Connecting to VPS Broker...")
    mqtt_manager.setup_vps_broker()
    time.sleep(2)
    
    # Start heartbeat
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(mqtt_manager, CONFIG['heartbeat_interval'])
    )
    heartbeat_thread.daemon = True
    heartbeat_thread.start()
    
    logger.info("=" * 60)
    logger.info(" Gateway 3 Running Successfully")
    logger.info(" Local: Connected to " + CONFIG['local_broker']['host'])
    logger.info("  VPS: Connected to " + CONFIG['vps_broker']['host'])
    logger.info(" Automation: " + ("ENABLED" if CONFIG['automation']['auto_fan_enabled'] else "DISABLED"))
    logger.info(f" Temp Threshold: {CONFIG['automation']['temp_threshold']}°C")
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info(" Shutting down Gateway 3...")
        mqtt_manager.local_client.loop_stop()
        mqtt_manager.vps_client.loop_stop()
        logger.info(" Gateway 3 stopped")

if __name__ == '__main__':
    main()