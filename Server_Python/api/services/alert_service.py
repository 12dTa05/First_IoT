import logging
import asyncio
from datetime import datetime, timedelta
from services.database import db
from services.websocket_manager import ws_manager  # THÊM IMPORT
import json

logger = logging.getLogger(__name__)

class AlertService:
    def __init__(self, check_interval=60):
        """
        Args:
            check_interval: Seconds between checks (default: 60)
        """
        self.check_interval = check_interval
        self.running = False
        self.task = None
        
        # Default thresholds
        self.temp_high = 30.0  # Celsius
        self.temp_low = 18.0
        self.humidity_high = 75.0  # Percent
        self.humidity_low = 30.0
        
        # Cooldown to prevent alert spam (minutes)
        self.alert_cooldown = 15
        self.recent_alerts = {}  # device_id -> last_alert_time
    
    async def start(self):
        """Start the alert checking loop"""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._alert_loop())
        logger.info('Alert service started')
    
    async def stop(self):
        """Stop the alert checking loop"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info('Alert service stopped')
    
    async def _alert_loop(self):
        """Main alert checking loop"""
        while self.running:
            try:
                await self.check_temperature_alerts()
                await self.check_humidity_alerts()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Error in alert loop: {e}')
                await asyncio.sleep(self.check_interval)
    
    async def check_temperature_alerts(self):
        """Check for temperature threshold violations"""
        try:
            query = """
                SELECT DISTINCT ON (device_id) device_id, gateway_id, user_id, temperature, time
                FROM telemetry
                WHERE temperature IS NOT NULL AND time > NOW() - INTERVAL '5 minutes'
                ORDER BY device_id, time DESC
            """
            
            readings = db.query(query)
            
            for reading in readings:
                device_id = reading['device_id']
                temp = reading['temperature']
                
                if self._is_in_cooldown(device_id, 'temp'):
                    continue
                
                alert_type = None
                severity = None
                
                if temp > self.temp_high:
                    alert_type = 'high_temperature'
                    severity = 'warning' if temp < 40 else 'critical'
                elif temp < self.temp_low:
                    alert_type = 'low_temperature'
                    severity = 'warning'
                
                if alert_type:
                    await self._create_alert(
                        device_id=device_id,
                        gateway_id=reading['gateway_id'],
                        user_id=reading['user_id'],
                        alert_type=alert_type,
                        severity=severity,
                        value=temp,
                        threshold=self.temp_high if temp > self.temp_high else self.temp_low,
                        message=f'Temperature {temp}°C exceeds threshold',
                        timestamp=reading['time']
                    )
                    
                    self._update_cooldown(device_id, 'temp')
        
        except Exception as e:
            logger.error(f'Error checking temperature alerts: {e}')
    
    async def check_humidity_alerts(self):
        """Check for humidity threshold violations"""
        try:
            query = """
                SELECT DISTINCT ON (device_id) device_id, gateway_id, user_id, humidity, time
                FROM telemetry
                WHERE humidity IS NOT NULL AND time > NOW() - INTERVAL '5 minutes'
                ORDER BY device_id, time DESC
            """
            
            readings = db.query(query)
            
            for reading in readings:
                device_id = reading['device_id']
                humidity = reading['humidity']
                
                if self._is_in_cooldown(device_id, 'humidity'):
                    continue
                
                alert_type = None
                severity = 'warning'
                
                if humidity > self.humidity_high:
                    alert_type = 'high_humidity'
                elif humidity < self.humidity_low:
                    alert_type = 'low_humidity'
                
                if alert_type:
                    await self._create_alert(
                        device_id=device_id,
                        gateway_id=reading['gateway_id'],
                        user_id=reading['user_id'],
                        alert_type=alert_type,
                        severity=severity,
                        value=humidity,
                        threshold=self.humidity_high if humidity > self.humidity_high else self.humidity_low,
                        message=f'Humidity {humidity}% exceeds threshold',
                        timestamp=reading['time']
                    )
                    
                    self._update_cooldown(device_id, 'humidity')
        
        except Exception as e:
            logger.error(f'Error checking humidity alerts: {e}')
    
    def _is_in_cooldown(self, device_id, alert_category):
        """Check if device is in cooldown period"""
        key = f'{device_id}_{alert_category}'
        if key in self.recent_alerts:
            last_alert = self.recent_alerts[key]
            elapsed = (datetime.now() - last_alert).total_seconds() / 60
            return elapsed < self.alert_cooldown
        return False
    
    def _update_cooldown(self, device_id, alert_category):
        """Update cooldown timestamp"""
        key = f'{device_id}_{alert_category}'
        self.recent_alerts[key] = datetime.now()
    
    async def _create_alert(self, device_id, gateway_id, user_id, alert_type, 
                           severity, value, threshold, message, timestamp):
        """Create alert in system_logs and publish to MQTT + WebSocket"""
        try:
            # Insert into system_logs
            query = """
                INSERT INTO system_logs (time, gateway_id, device_id, user_id, log_type, event, severity, message, value, threshold, metadata)
                VALUES (%s::timestamptz, %s, %s, %s, 'alert', %s, %s, %s, %s, %s, %s)
            """
            
            metadata = json.dumps({
                'alert_type': alert_type,
                'auto_generated': True
            })
            
            db.query(query, (timestamp, gateway_id, device_id, user_id, alert_type, severity, message, value, threshold, metadata))
            
            logger.warning(f'ALERT: {device_id} - {message}')
            
            # Publish alert to MQTT
            from services.mqtt_service import mqtt_service
            if mqtt_service:
                topic = f'alert/{user_id}/{device_id}'
                alert_payload = {
                    'device_id': device_id,
                    'alert_type': alert_type,
                    'severity': severity,
                    'value': value,
                    'threshold': threshold,
                    'message': message,
                    'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
                }
                mqtt_service.publish(topic, alert_payload)
            
            # Broadcast to WebSocket
            await ws_manager.broadcast_alert(user_id, {
                'device_id': device_id,
                'alert_type': alert_type,
                'severity': severity,
                'value': value,
                'threshold': threshold,
                'message': message,
                'timestamp': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
            })
            
        except Exception as e:
            logger.error(f'Error creating alert: {e}')

# Singleton instance
alert_service = AlertService()