"""
Offline Detection Service
Periodically checks for offline devices and gateways
Generates alerts when devices go offline
"""

import logging
import asyncio
from datetime import datetime, timedelta
from services.database import db

logger = logging.getLogger(__name__)

class OfflineDetector:
    def __init__(self, check_interval=60):
        """
        Args:
            check_interval: Seconds between checks (default: 60)
        """
        self.check_interval = check_interval
        self.running = False
        self.task = None
    
    async def start(self):
        """Start the offline detection loop"""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._detection_loop())
        logger.info('Offline detector started')
    
    async def stop(self):
        """Stop the offline detection loop"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info('Offline detector stopped')
    
    async def _detection_loop(self):
        """Main detection loop"""
        while self.running:
            try:
                await self.check_offline_devices()
                await self.check_offline_gateways()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Error in offline detection: {e}')
                await asyncio.sleep(self.check_interval)
    
    async def check_offline_devices(self):
        """Check for devices that have gone offline"""
        try:
            # Find devices that were online but haven't been seen in 5 minutes
            query = """
                UPDATE devices
                SET is_online = FALSE, status = 'offline', updated_at = NOW()
                WHERE is_online = TRUE AND last_seen < NOW() - INTERVAL '5 minutes'
                RETURNING device_id, user_id, device_type, last_seen
            """
            
            offline_devices = db.query(query)
            
            if offline_devices and len(offline_devices) > 0:
                for device in offline_devices:
                    # Log to system_logs
                    log_query = """
                        INSERT INTO system_logs (time, device_id, user_id, log_type, event, severity, message, metadata)
                        VALUES (NOW(), %s, %s, 'device_event', 'device_offline', 'warning', %s, %s)
                    """
                    
                    message = f"Device {device['device_id']} went offline"
                    import json
                    metadata = json.dumps({
                        'last_seen': str(device['last_seen']),
                        'device_type': device['device_type']
                    })
                    
                    db.query(log_query, (
                        device['device_id'],
                        device['user_id'],
                        message,
                        metadata
                    ))
                    
                    logger.warning(f"Device offline: {device['device_id']}")
        
        except Exception as e:
            logger.error(f'Error checking offline devices: {e}')
    
    async def check_offline_gateways(self):
        """Check for gateways that have gone offline"""
        try:
            # Find gateways that haven't sent heartbeat in 2 minutes
            query = """
                UPDATE gateways
                SET status = 'offline', updated_at = NOW()
                WHERE status = 'online' AND last_heartbeat < NOW() - INTERVAL '2 minutes'
                RETURNING gateway_id, user_id, name, last_heartbeat
            """
            
            offline_gateways = db.query(query)
            
            if offline_gateways and len(offline_gateways) > 0:
                for gateway in offline_gateways:
                    # Log to system_logs
                    log_query = """
                        INSERT INTO system_logs (time, gateway_id, user_id, log_type, event, severity, message, metadata)
                        VALUES (NOW(), %s, %s, 'system_event', 'gateway_offline', 'critical', %s, %s)
                    """
                    
                    message = f"Gateway {gateway['gateway_id']} went offline"
                    import json
                    metadata = json.dumps({
                        'last_heartbeat': str(gateway['last_heartbeat']),
                        'name': gateway.get('name')
                    })
                    
                    db.query(log_query, (
                        gateway['gateway_id'],
                        gateway['user_id'],
                        message,
                        metadata
                    ))
                    
                    logger.error(f"Gateway offline: {gateway['gateway_id']}")
        
        except Exception as e:
            logger.error(f'Error checking offline gateways: {e}')

# Singleton instance
offline_detector = OfflineDetector()