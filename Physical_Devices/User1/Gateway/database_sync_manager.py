import json
import requests
import time
import logging
import os
import hashlib
from datetime import datetime
from threading import Thread, Event

logger = logging.getLogger(__name__)

class DatabaseSyncManager:
    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.gateway_id = config['gateway_id']
        
        # Sync settings
        self.sync_interval = 5  # 5 seconds
        self.api_base_url = config.get('vps_api_url', 'http://192.168.1.205:3000')
        self.current_version = None
        self.last_sync_time = None
        self.sync_enabled = True
        
        # Threading
        self.sync_thread = None
        self.stop_event = Event()
        
        # Stats
        self.sync_count = 0
        self.sync_errors = 0
        self.last_update_time = None
        
        logger.info(f"[SYNC] Initialized for gateway: {self.gateway_id}")
        logger.info(f"[SYNC] Sync interval: {self.sync_interval}s")
        logger.info(f"[SYNC] API URL: {self.api_base_url}")
    
    def calculate_local_version(self):
        """Calculate version hash of local database"""
        try:
            data = self.db_manager.devices_data
            json_str = json.dumps(data, sort_keys=True)
            return hashlib.sha256(json_str.encode()).hexdigest()[:16]
        except Exception as e:
            logger.error(f"[SYNC] Error calculating local version: {e}")
            return None
    
    def fetch_database_from_server(self):
        """Fetch database from server via API"""
        try:
            url = f"{self.api_base_url}/api/sync/database/{self.gateway_id}"
            
            headers = {}
            if self.current_version:
                headers['X-DB-Version'] = self.current_version
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.error(f"[SYNC] Gateway not found on server: {self.gateway_id}")
                return None
            else:
                logger.error(f"[SYNC] Server returned status {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("[SYNC] Request timeout")
            return None
        except requests.exceptions.ConnectionError:
            logger.error("[SYNC] Connection error - server unreachable")
            return None
        except Exception as e:
            logger.error(f"[SYNC] Error fetching from server: {e}")
            return None
    
    def apply_database_update(self, server_data):
        """Apply database update from server to local storage"""
        try:
            if 'database' not in server_data:
                logger.debug("[SYNC] No database update needed (version match)")
                return True
            
            database = server_data['database']
            
            # Backup current database
            backup_file = f"{self.db_manager.devices_file}.backup"
            try:
                with open(self.db_manager.devices_file, 'r') as f:
                    current_data = f.read()
                with open(backup_file, 'w') as f:
                    f.write(current_data)
            except Exception as e:
                logger.warning(f"[SYNC] Could not create backup: {e}")
            
            # Validate server data structure
            if not isinstance(database, dict):
                logger.error("[SYNC] Invalid database format from server")
                return False
            
            # Ensure required keys exist
            if 'passwords' not in database:
                database['passwords'] = {}
            if 'rfid_cards' not in database:
                database['rfid_cards'] = {}
            if 'devices' not in database:
                database['devices'] = {}
            
            # Update local database
            self.db_manager.devices_data = database
            self.db_manager.save_devices()
            
            # Update version
            self.current_version = server_data['version']
            self.last_update_time = datetime.now()
            
            stats = server_data.get('stats', {})
            logger.info(f"[SYNC]  Database updated successfully")
            logger.info(f"[SYNC]   Version: {self.current_version}")
            logger.info(f"[SYNC]   Passwords: {stats.get('passwords_count', 0)}")
            logger.info(f"[SYNC]   RFID Cards: {stats.get('rfid_cards_count', 0)}")
            logger.info(f"[SYNC]   Devices: {stats.get('devices_count', 0)}")
            
            return True
            
        except Exception as e:
            logger.error(f"[SYNC] Error applying database update: {e}")
            
            # Restore backup
            try:
                backup_file = f"{self.db_manager.devices_file}.backup"
                if os.path.exists(backup_file):
                    with open(backup_file, 'r') as f:
                        backup_data = json.load(f)
                    self.db_manager.devices_data = backup_data
                    self.db_manager.save_devices()
                    logger.warning("[SYNC] Restored from backup")
            except Exception as restore_error:
                logger.error(f"[SYNC] Failed to restore backup: {restore_error}")
            
            return False
    
    def perform_sync(self):
        """Perform one sync cycle"""
        try:
            logger.debug(f"[SYNC] Starting sync cycle #{self.sync_count + 1}")
            
            # Fetch from server
            server_data = self.fetch_database_from_server()
            
            if server_data is None:
                self.sync_errors += 1
                return False
            
            # Check if update needed
            needs_update = server_data.get('needs_update', False)
            
            if needs_update:
                logger.info(f"[SYNC] Database update available")
                success = self.apply_database_update(server_data)
                if success:
                    self.sync_count += 1
                    self.last_sync_time = datetime.now()
                    return True
                else:
                    self.sync_errors += 1
                    return False
            else:
                # No update needed, just update version
                self.current_version = server_data.get('version')
                self.last_sync_time = datetime.now()
                self.sync_count += 1
                logger.debug("[SYNC] Database is up-to-date")
                return True
                
        except Exception as e:
            logger.error(f"[SYNC] Error during sync: {e}")
            self.sync_errors += 1
            return False
    
    def sync_loop(self):
        """Main sync loop running in separate thread"""
        logger.info(f"[SYNC] Sync loop started")
        
        # Initial sync
        logger.info("[SYNC] Performing initial sync...")
        self.perform_sync()
        
        while not self.stop_event.is_set():
            try:
                # Wait for interval or stop event
                if self.stop_event.wait(timeout=self.sync_interval):
                    break
                
                # Perform sync
                if self.sync_enabled:
                    self.perform_sync()
                
            except Exception as e:
                logger.error(f"[SYNC] Error in sync loop: {e}")
                time.sleep(self.sync_interval)
        
        logger.info("[SYNC] Sync loop stopped")
    
    def start(self):
        """Start sync service"""
        if self.sync_thread and self.sync_thread.is_alive():
            logger.warning("[SYNC] Sync service already running")
            return
        
        self.stop_event.clear()
        self.sync_thread = Thread(target=self.sync_loop, daemon=True)
        self.sync_thread.start()
        
        logger.info("[SYNC]  Sync service started")
    
    def stop(self):
        """Stop sync service"""
        logger.info("[SYNC] Stopping sync service...")
        self.stop_event.set()
        
        if self.sync_thread:
            self.sync_thread.join(timeout=10)
        
        logger.info("[SYNC] Sync service stopped")
    
    def trigger_immediate_sync(self):
        """Trigger immediate sync (called when receiving MQTT sync trigger)"""
        logger.info("[SYNC] Immediate sync triggered")
        return self.perform_sync()
    
    def get_stats(self):
        """Get sync statistics"""
        return {
            'enabled': self.sync_enabled,
            'current_version': self.current_version,
            'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'last_update_time': self.last_update_time.isoformat() if self.last_update_time else None,
            'sync_count': self.sync_count,
            'sync_errors': self.sync_errors,
            'sync_interval': self.sync_interval
        }
    
    def enable_sync(self):
        """Enable automatic sync"""
        self.sync_enabled = True
        logger.info("[SYNC] Auto-sync enabled")
    
    def disable_sync(self):
        """Disable automatic sync"""
        self.sync_enabled = False
        logger.warning("[SYNC] Auto-sync disabled")