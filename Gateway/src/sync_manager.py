import time
from datetime import datetime
from typing import Dict, Any


class SyncManager:
    """Manages periodic sync with AWS IoT server"""
    
    def __init__(self, gateway, interval: int, logger):
        """
        Initialize sync manager
        
        Args:
            gateway: Reference to Gateway instance
            interval: Sync interval in seconds
            logger: Logger instance
        """
        self.gateway = gateway
        self.interval = interval
        self.logger = logger
        
        self.last_sync = 0
        
        self.logger.info(f"Sync manager initialized (interval={interval}s)")
    
    def auto_sync(self):
        """Perform automatic sync if interval has elapsed"""
        current_time = time.time()
        
        if current_time - self.last_sync >= self.interval:
            self.last_sync = current_time
            self.request_sync_from_server()
    
    def request_sync_from_server(self):
        """Request database sync from AWS server"""
        try:
            sync_info = self.gateway.db.get_sync_info()
            
            sync_request = {
                'gateway_id': self.gateway.config.get('gateway_id', 'Gateway1'),
                'last_sync': sync_info.get('last_sync_server', '1970-01-01T00:00:00Z'),
                'request_types': ['devices', 'passwords', 'rfid_cards', 'settings'],
                'timestamp': datetime.now().isoformat()
            }
            
            # Send request to AWS
            self.gateway._queue_aws_message('home/gateway/sync/request', sync_request)
            
            self.logger.info("Sync request sent to server")
            
        except Exception as e:
            self.logger.error(f"Error requesting sync: {e}", exc_info=True)
    
    def handle_sync_response(self, payload: Dict[str, Any]):
        """Handle sync response from server"""
        try:
            changes = payload.get('changes', {})
            
            if not changes:
                self.logger.info("No changes from server")
                return
            
            # Apply changes to database
            success = self.gateway.db.apply_sync_changes(changes)
            
            if success:
                # Update sync timestamp
                sync_time = payload.get('timestamp', datetime.now().isoformat())
                self.gateway.db.update_sync_info(sync_time)
                
                self.logger.info(f"Sync completed successfully at {sync_time}")
            else:
                self.logger.error("Failed to apply sync changes")
            
        except Exception as e:
            self.logger.error(f"Error handling sync response: {e}", exc_info=True)
