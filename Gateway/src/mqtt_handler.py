"""
MQTT Handler for Gateway
Manages connections to local broker and AWS IoT
"""

import json
import ssl
import paho.mqtt.client as mqtt
import threading
from typing import Dict, Any, Callable, Optional


class MQTTHandler:
    """Manages MQTT connections and message handling"""
    
    def __init__(self, config: Dict[str, Any], security, logger):
        """Initialize MQTT handler"""
        self.config = config
        self.security = security
        self.logger = logger
        
        # Clients
        self.local_client: Optional[mqtt.Client] = None
        self.aws_client: Optional[mqtt.Client] = None
        
        # Callback
        self.message_callback: Optional[Callable] = None
        
        # Connection state
        self.local_connected = False
        self.aws_connected = False
        
        # Thread lock
        self.lock = threading.Lock()
        
        self.logger.info("MQTT handler initialized")
    
    def start(self, on_message_callback: Callable):
        """Start MQTT connections"""
        self.message_callback = on_message_callback
        
        # Setup local broker connection
        if self.config.get('local'):
            self._setup_local_broker()
        
        # Setup AWS IoT connection
        if self.config.get('aws'):
            self._setup_aws_client()
        
        self.logger.info("MQTT handler started")
    
    def stop(self):
        """Stop MQTT connections gracefully"""
        self.logger.info("Stopping MQTT handler...")
        
        if self.local_client:
            self.local_client.loop_stop()
            self.local_client.disconnect()
            self.logger.info("Local MQTT disconnected")
        
        if self.aws_client:
            self.aws_client.loop_stop()
            self.aws_client.disconnect()
            self.logger.info("AWS MQTT disconnected")
    
    # ========== Local Broker Setup ==========
    
    def _setup_local_broker(self):
        """Setup connection to local Mosquitto broker"""
        try:
            local_config = self.config['local']
            
            # Create client
            self.local_client = mqtt.Client(client_id=local_config['client_id'])
            
            # Setup TLS
            if local_config.get('use_tls', True):
                context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                context.load_verify_locations(local_config['ca_cert'])
                
                if local_config.get('client_cert_required', True):
                    context.load_cert_chain(
                        local_config['client_cert'],
                        local_config['client_key']
                    )
                
                context.check_hostname = False
                context.verify_mode = ssl.CERT_REQUIRED
                
                self.local_client.tls_set_context(context)
            
            # Set callbacks
            self.local_client.on_connect = self._on_local_connect
            self.local_client.on_message = self._on_local_message
            self.local_client.on_disconnect = self._on_local_disconnect
            
            # Connect
            self.local_client.connect(
                local_config['host'],
                local_config['port'],
                keepalive=60
            )
            
            # Start loop
            self.local_client.loop_start()
            
            self.logger.info(
                f"Local broker connecting to "
                f"{local_config['host']}:{local_config['port']}"
            )
            
        except Exception as e:
            self.logger.error(f"Error setting up local broker: {e}", exc_info=True)
    
    def _on_local_connect(self, client, userdata, flags, rc):
        """Callback when connected to local broker"""
        if rc == 0:
            self.local_connected = True
            self.logger.info("Connected to local MQTT broker")
            
            # Subscribe to topics
            topics = self.config['local'].get('subscribe_topics', [])
            for topic in topics:
                client.subscribe(topic)
                self.logger.info(f"Subscribed to: {topic}")
        else:
            self.logger.error(f"Local MQTT connection failed: rc={rc}")
            self.local_connected = False
    
    def _on_local_disconnect(self, client, userdata, rc):
        """Callback when disconnected from local broker"""
        self.local_connected = False
        
        if rc != 0:
            self.logger.warning(f"Unexpected disconnect from local broker: rc={rc}")
        else:
            self.logger.info("Disconnected from local broker")
    
    def _on_local_message(self, client, userdata, msg):
        """Callback for messages from local broker"""
        try:
            # Parse payload
            payload = json.loads(msg.payload.decode('utf-8'))
            
            self.logger.debug(f"Local MQTT << {msg.topic}")
            
            # Call gateway callback
            if self.message_callback:
                self.message_callback(msg.topic, payload)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON from {msg.topic}: {e}")
        except Exception as e:
            self.logger.error(f"Error handling local message: {e}", exc_info=True)
    
    # ========== AWS IoT Setup ==========
    
    def _setup_aws_client(self):
        """Setup connection to AWS IoT Core"""
        try:
            aws_config = self.config['aws']
            
            # Create client
            self.aws_client = mqtt.Client(client_id=aws_config['client_id'])
            
            # Setup TLS
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.load_verify_locations(aws_config['ca_cert'])
            context.load_cert_chain(
                aws_config['cert_file'],
                aws_config['key_file']
            )
            
            self.aws_client.tls_set_context(context)
            
            # Set callbacks
            self.aws_client.on_connect = self._on_aws_connect
            self.aws_client.on_message = self._on_aws_message
            self.aws_client.on_disconnect = self._on_aws_disconnect
            
            # Connect
            self.aws_client.connect(
                aws_config['broker'],
                aws_config['port'],
                keepalive=60
            )
            
            # Start loop
            self.aws_client.loop_start()
            
            self.logger.info(f"AWS IoT connecting to {aws_config['broker']}")
            
        except Exception as e:
            self.logger.error(f"Error setting up AWS client: {e}", exc_info=True)
    
    def _on_aws_connect(self, client, userdata, flags, rc):
        """Callback when connected to AWS IoT"""
        if rc == 0:
            self.aws_connected = True
            self.logger.info("Connected to AWS IoT")
            
            # Subscribe to control topics
            topics = self.config['aws'].get('subscribe_topics', [])
            for topic in topics:
                client.subscribe(topic)
                self.logger.info(f"AWS subscribed to: {topic}")
        else:
            self.logger.error(f"AWS IoT connection failed: rc={rc}")
            self.aws_connected = False
    
    def _on_aws_disconnect(self, client, userdata, rc):
        """Callback when disconnected from AWS IoT"""
        self.aws_connected = False
        
        if rc != 0:
            self.logger.warning(f"Unexpected disconnect from AWS IoT: rc={rc}")
        else:
            self.logger.info("Disconnected from AWS IoT")
    
    def _on_aws_message(self, client, userdata, msg):
        """Callback for messages from AWS IoT"""
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            
            self.logger.debug(f"AWS IoT << {msg.topic}")
            
            # Handle AWS commands here if needed
            # For now, just log
            self.logger.info(f"AWS command: {payload}")
            
        except Exception as e:
            self.logger.error(f"Error handling AWS message: {e}", exc_info=True)
    
    # ========== Publish Functions ==========
    
    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 1) -> bool:
        """Publish to local broker"""
        if not self.local_client or not self.local_connected:
            self.logger.error("Local broker not connected")
            return False
        
        try:
            payload_str = json.dumps(payload)
            result = self.local_client.publish(topic, payload_str, qos=qos)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.debug(f"Local MQTT >> {topic}")
                return True
            else:
                self.logger.error(f"Publish failed: rc={result.rc}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error publishing to local broker: {e}")
            return False
    
    def publish_to_aws(self, topic: str, payload: Dict[str, Any], qos: int = 1) -> bool:
        """Publish to AWS IoT"""
        if not self.aws_client or not self.aws_connected:
            self.logger.warning("AWS IoT not connected")
            return False
        
        try:
            payload_str = json.dumps(payload)
            result = self.aws_client.publish(topic, payload_str, qos=qos)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.debug(f"AWS IoT >> {topic}")
                return True
            else:
                self.logger.error(f"AWS publish failed: rc={result.rc}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error publishing to AWS: {e}")
            return False
    
    # ========== Status ==========
    
    def is_connected(self) -> Dict[str, bool]:
        """Get connection status"""
        return {
            'local': self.local_connected,
            'aws': self.aws_connected
        }