import logging
import asyncio
from datetime import datetime, timedelta
from services.database import db
from services.websocket_manager import ws_manager
import json

logger = logging.getLogger(__name__)

class OfflineDetector:
    def __init__(self, check_interval=10, device_timeout=90, gateway_timeout=90):
        """
        Args:
            check_interval: Seconds between checks (default: 10) - faster detection
            device_timeout: Seconds before device marked offline (default: 90)
            gateway_timeout: Seconds before gateway marked offline (default: 90)
        """
        self.check_interval = check_interval
        self.device_timeout = device_timeout
        self.gateway_timeout = gateway_timeout
        self.running = False
        self.task = None
        
        # In-memory cache of last check times to reduce database queries
        self.last_device_check = {}
        self.last_gateway_check = {}
    
    async def start(self):
        """Start the offline detection loop"""
        if self.running:
            return
        
        self.running = True
        self.task = asyncio.create_task(self._detection_loop())
        logger.info(f'Offline detector started (check every {self.check_interval}s, '
                   f'device timeout: {self.device_timeout}s, gateway timeout: {self.gateway_timeout}s)')
    
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
        """Main detection loop - runs every check_interval seconds"""
        while self.running:
            try:
                # Check gateways first (if gateway offline, all its devices are offline)
                await self.check_offline_gateways()
                
                # Then check devices
                await self.check_offline_devices()
                
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f'Error in offline detection: {e}', exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    async def check_offline_devices(self):
        """Check for devices that have gone offline"""
        try:
            # Calculate cutoff time - devices not seen in last device_timeout seconds
            cutoff_time = datetime.now() - timedelta(seconds=self.device_timeout)
            
            # Query with explicit timeout comparison
            query = """
                UPDATE devices
                SET status = 'offline', updated_at = NOW()
                WHERE status = 'online' AND (last_seen IS NULL OR last_seen < %s)
                RETURNING device_id, user_id, device_type, gateway_id, last_seen
            """
            
            offline_devices = db.query(query, (cutoff_time,))
            
            if offline_devices and len(offline_devices) > 0:
                logger.warning(f'Detected {len(offline_devices)} devices going offline')
                
                for device in offline_devices:
                    # Log to system_logs
                    log_query = """
                        INSERT INTO system_logs (time, gateway_id, device_id, user_id, log_type, event, severity, message, metadata)
                        VALUES (NOW(), %s, %s, %s, 'device_event', 'device_offline', 'warning', %s, %s)
                    """
                    
                    message = f"Device {device['device_id']} went offline (last seen: {device['last_seen']})"
                    
                    metadata = json.dumps({
                        'last_seen': str(device['last_seen']) if device['last_seen'] else None,
                        'device_type': device['device_type'],
                        'timeout_seconds': self.device_timeout,
                        'detection_method': 'periodic_check'
                    })
                    
                    db.query(log_query, (
                        device['gateway_id'],
                        device['device_id'],
                        device['user_id'],
                        message,
                        metadata
                    ))
                    
                    logger.warning(f"Device offline: {device['device_id']} (type: {device['device_type']}, "
                                 f"last_seen: {device['last_seen']})")
                    
                    # Broadcast to WebSocket clients
                    try:
                        await ws_manager.broadcast_device_status(
                            device['device_id'],
                            device['user_id'],
                            {
                                'status': 'offline',
                                'timestamp': datetime.now().isoformat(),
                                'reason': 'timeout'
                            }
                        )
                    except Exception as ws_error:
                        logger.error(f'WebSocket broadcast error: {ws_error}')
        
        except Exception as e:
            logger.error(f'Error checking offline devices: {e}', exc_info=True)
    
    async def check_offline_gateways(self):
        """Check for gateways that have gone offline"""
        try:
            # Calculate cutoff time - gateways not seen in last gateway_timeout seconds
            cutoff_time = datetime.now() - timedelta(seconds=self.gateway_timeout)
            
            # Query with explicit timeout comparison
            query = """
                UPDATE gateways
                SET status = 'offline', updated_at = NOW()
                WHERE status = 'online' 
                  AND (last_seen IS NULL OR last_seen < %s)
                RETURNING gateway_id, user_id, name, last_seen
            """
            
            offline_gateways = db.query(query, (cutoff_time,))
            
            if offline_gateways and len(offline_gateways) > 0:
                gateway_ids = [g['gateway_id'] for g in offline_gateways]
                logger.error(f'Detected {len(offline_gateways)} gateways going offline: {gateway_ids}')
                
                for gateway in offline_gateways:
                    # Log to system_logs
                    log_query = """
                        INSERT INTO system_logs (time, gateway_id, user_id, log_type, event, severity, message, metadata)
                        VALUES (NOW(), %s, %s, 'system_event', 'gateway_offline', 'critical', %s, %s)
                    """
                    
                    message = f"Gateway {gateway['gateway_id']} went offline (last seen: {gateway['last_seen']})"
                    
                    metadata = json.dumps({
                        'last_seen': str(gateway['last_seen']) if gateway['last_seen'] else None,
                        'name': gateway.get('name'),
                        'timeout_seconds': self.gateway_timeout,
                        'detection_method': 'periodic_check'
                    })
                    
                    db.query(log_query, (
                        gateway['gateway_id'],
                        gateway['user_id'],
                        message,
                        metadata
                    ))
                    
                    logger.error(f"Gateway offline: {gateway['gateway_id']} (name: {gateway.get('name')}, "
                               f"last_seen: {gateway['last_seen']})")
                
                # CASCADE: Mark all devices under offline gateways as offline
                if gateway_ids:
                    cascade_query = """
                        UPDATE devices
                        SET status = 'offline', updated_at = NOW()
                        WHERE gateway_id = ANY(%s) AND status != 'offline'
                        RETURNING device_id, device_type
                    """
                    
                    cascaded_devices = db.query(cascade_query, (gateway_ids,))
                    
                    if cascaded_devices and len(cascaded_devices) > 0:
                        logger.warning(f'Cascaded offline status to {len(cascaded_devices)} devices '
                                     f'under offline gateways')
                        
                        # Log cascade for each device
                        for device in cascaded_devices:
                            cascade_log_query = """
                                INSERT INTO system_logs (time, device_id, user_id, log_type, event, severity, message, metadata)
                                SELECT NOW(), %s, d.user_id, 'device_event', 'device_offline', 'warning', %s, %s
                                FROM devices d
                                WHERE d.device_id = %s
                            """
                            
                            cascade_message = f"Device {device['device_id']} marked offline (gateway offline)"
                            cascade_metadata = json.dumps({
                                'reason': 'gateway_offline',
                                'device_type': device['device_type'],
                                'detection_method': 'cascade'
                            })
                            
                            db.query(cascade_log_query, (
                                device['device_id'],
                                cascade_message,
                                cascade_metadata,
                                device['device_id']
                            ))
        
        except Exception as e:
            logger.error(f'Error checking offline gateways: {e}', exc_info=True)
    
    async def force_check_device(self, device_id):
        """Force immediate check of a specific device status"""
        try:
            cutoff_time = datetime.now() - timedelta(seconds=self.device_timeout)
            
            query = """
                UPDATE devices
                SET status = 'offline', updated_at = NOW()
                WHERE device_id = %s
                  AND status = 'online'
                  AND (last_seen IS NULL OR last_seen < %s)
                RETURNING device_id, user_id, last_seen
            """
            
            result = db.query(query, (device_id, cutoff_time))
            
            if result and len(result) > 0:
                logger.warning(f'Force check: Device {device_id} marked offline')
                return True
            
            return False
            
        except Exception as e:
            logger.error(f'Error in force check device: {e}', exc_info=True)
            return False
    
    async def force_check_gateway(self, gateway_id):
        """Force immediate check of a specific gateway status"""
        try:
            cutoff_time = datetime.now() - timedelta(seconds=self.gateway_timeout)
            
            query = """
                UPDATE gateways
                SET status = 'offline', updated_at = NOW()
                WHERE gateway_id = %s
                  AND status = 'online'
                  AND (last_seen IS NULL OR last_seen < %s)
                RETURNING gateway_id, user_id
            """
            
            result = db.query(query, (gateway_id, cutoff_time))
            
            if result and len(result) > 0:
                logger.error(f'Force check: Gateway {gateway_id} marked offline')
                
                # Cascade to devices
                cascade_query = """
                    UPDATE devices
                    SET status = 'offline', updated_at = NOW()
                    WHERE gateway_id = %s AND status != 'offline'
                """
                db.query(cascade_query, (gateway_id,))
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f'Error in force check gateway: {e}', exc_info=True)
            return False

# Singleton instance
offline_detector = OfflineDetector(
    check_interval=10,      # Check every 10 seconds for faster detection
    device_timeout=90,      # Devices offline after 90 seconds (3x heartbeat interval)
    gateway_timeout=90      # Gateways offline after 90 seconds (3x heartbeat interval)
)