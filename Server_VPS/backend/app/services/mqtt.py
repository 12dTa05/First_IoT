import asyncio
import json
import paho.mqtt.client as mqtt
from typing import Callable, Dict
from ..core.config import get_settings

settings = get_settings()

class MQTTService:
    def __init__(self):
        self.client = mqtt.Client(client_id="iot_backend", clean_session=True, protocol=mqtt.MQTTv5)
        self.client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASSWORD)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.callbacks: Dict[str, Callable] = {}
        self.connected = False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            client.subscribe("gateway/+/telemetry")
            client.subscribe("gateway/+/status")
            client.subscribe("gateway/+/access")
            client.subscribe("device/+/status")
        
    def _on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        if rc != 0:
            asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        while not self.connected:
            try:
                self.client.reconnect()
                await asyncio.sleep(5)
            except:
                await asyncio.sleep(10)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            for pattern, callback in self.callbacks.items():
                if pattern in topic:
                    asyncio.create_task(callback(topic, payload))
        except Exception as e:
            pass

    def subscribe_callback(self, pattern: str, callback: Callable):
        self.callbacks[pattern] = callback

    async def connect(self):
        self.client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, 60)
        self.client.loop_start()

    async def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: dict):
        if self.connected:
            self.client.publish(topic, json.dumps(payload), qos=1)

mqtt_service = MQTTService()