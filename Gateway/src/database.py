"""
Thread-safe Database Manager for Gateway
Handles devices, passwords, RFID cards, and settings
"""

import json
import os
import threading
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path


class Database:   
    def __init__(self, db_path: str, logger):
        self.db_path = Path(db_path)
        self.logger = logger
        
        # Thread locks for each data structure
        self.devices_lock = threading.RLock()
        self.settings_lock = threading.RLock()
        
        # Data structures
        self.devices = {}
        self.settings = {}
        
        # Ensure directory exists
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        # Load data
        self.load_database()
        
        self.logger.info(f"Database initialized at {self.db_path}")
    
    def load_database(self):
        with self.devices_lock, self.settings_lock:
            self.devices = self._load_json('devices.json', {
                'devices': {},
                'rfid_cards': {},
                'passwords': {}
            })
            
            self.settings = self._load_json('settings.json', {
                'automation': {
                    'auto_fan_enabled': True,
                    'auto_fan_temp_threshold': 28.0
                },
                'home_occupied': False,
                'last_access': {},
                'sync': {
                    'last_sync_server': '1970-01-01T00:00:00Z'
                }
            })
            
            self.logger.info(
                f"Loaded database: "
                f"{len(self.devices.get('devices', {}))} devices, "
                f"{len(self.devices.get('rfid_cards', {}))} RFID cards, "
                f"{len(self.devices.get('passwords', {}))} passwords"
            )
    
    def _load_json(self, filename: str, default: Any = None) -> Any:
        """Load JSON file with default value if not exists"""
        file_path = self.db_path / filename
        
        try:
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.logger.debug(f"Loaded {filename}")
                return data
            else:
                self.logger.info(f"{filename} not found, using default")
                return default if default is not None else {}
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error in {filename}: {e}")
            return default if default is not None else {}
        except Exception as e:
            self.logger.error(f"Error loading {filename}: {e}")
            return default if default is not None else {}
    
    def _save_json(self, filename: str, data: Any):
        """Save data to JSON file atomically"""
        file_path = self.db_path / filename
        temp_path = file_path.with_suffix('.tmp')
        
        try:
            # Write to temporary file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Atomic rename
            temp_path.replace(file_path)
            
            self.logger.debug(f"Saved {filename}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving {filename}: {e}")
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()
            return False
    
    def save_all(self):
        """Save all database files"""
        with self.devices_lock, self.settings_lock:
            success = True
            success &= self._save_json('devices.json', self.devices)
            success &= self._save_json('settings.json', self.settings)
            
            if success:
                self.logger.info("All database files saved")
            else:
                self.logger.error("Some database files failed to save")
            
            return success
    
    # ========== RFID Authentication ==========
    
    def authenticate_rfid(self, uid: str) -> bool:
        """Authenticate RFID card by UID"""
        with self.devices_lock:
            try:
                cards = self.devices.get('rfid_cards', {})
                card = cards.get(uid)
                
                if not card:
                    self.logger.debug(f"RFID {uid} not found in database")
                    return False
                
                is_active = card.get('active', False)
                
                if is_active:
                    self.logger.info(f"RFID {uid} authenticated successfully")
                    
                    # Update last used
                    card['last_used'] = datetime.now().isoformat()
                    self._save_json('devices.json', self.devices)
                else:
                    self.logger.warning(f"RFID {uid} is inactive")
                
                return is_active
                
            except Exception as e:
                self.logger.error(f"Error authenticating RFID: {e}")
                return False
    
    # ========== Password Authentication ==========
    
    def authenticate_passkey(self, password_hash: str) -> Tuple[bool, Optional[str]]:
        """
        Authenticate password by hash
        Returns: (is_valid, password_id)
        """
        with self.devices_lock:
            try:
                passwords = self.devices.get('passwords', {})
                
                if not passwords:
                    self.logger.error("No passwords in database")
                    return False, None
                
                # Search for matching password
                for pwd_id, pwd_data in passwords.items():
                    stored_hash = pwd_data.get('hash')
                    is_active = pwd_data.get('active', False)
                    
                    if stored_hash == password_hash and is_active:
                        self.logger.info(f"Password authenticated: {pwd_id}")
                        
                        # Update last used
                        pwd_data['last_used'] = datetime.now().isoformat()
                        self._save_json('devices.json', self.devices)
                        
                        return True, pwd_id
                
                self.logger.warning("No matching password found")
                return False, None
                
            except Exception as e:
                self.logger.error(f"Error authenticating password: {e}")
                return False, None
    
    # ========== Device Management ==========
    
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get device information by ID"""
        with self.devices_lock:
            return self.devices.get('devices', {}).get(device_id)
    
    def update_device(self, device_id: str, data: Dict[str, Any]):
        """Update device information"""
        with self.devices_lock:
            if 'devices' not in self.devices:
                self.devices['devices'] = {}
            
            if device_id not in self.devices['devices']:
                self.devices['devices'][device_id] = {}
            
            self.devices['devices'][device_id].update(data)
            self.devices['devices'][device_id]['last_update'] = datetime.now().isoformat()
            
            self._save_json('devices.json', self.devices)
            self.logger.debug(f"Updated device: {device_id}")
    
    def add_rfid_card(self, uid: str, name: str, active: bool = True):
        """Add new RFID card"""
        with self.devices_lock:
            if 'rfid_cards' not in self.devices:
                self.devices['rfid_cards'] = {}
            
            self.devices['rfid_cards'][uid] = {
                'name': name,
                'active': active,
                'added': datetime.now().isoformat(),
                'last_used': None
            }
            
            self._save_json('devices.json', self.devices)
            self.logger.info(f"Added RFID card: {uid} ({name})")
    
    def add_password(self, pwd_id: str, password_hash: str, name: str, active: bool = True):
        """Add new password"""
        with self.devices_lock:
            if 'passwords' not in self.devices:
                self.devices['passwords'] = {}
            
            self.devices['passwords'][pwd_id] = {
                'hash': password_hash,
                'name': name,
                'active': active,
                'added': datetime.now().isoformat(),
                'last_used': None
            }
            
            self._save_json('devices.json', self.devices)
            self.logger.info(f"Added password: {pwd_id} ({name})")
    
    def deactivate_rfid(self, uid: str):
        """Deactivate RFID card"""
        with self.devices_lock:
            if uid in self.devices.get('rfid_cards', {}):
                self.devices['rfid_cards'][uid]['active'] = False
                self._save_json('devices.json', self.devices)
                self.logger.info(f"Deactivated RFID: {uid}")
    
    def deactivate_password(self, pwd_id: str):
        """Deactivate password"""
        with self.devices_lock:
            if pwd_id in self.devices.get('passwords', {}):
                self.devices['passwords'][pwd_id]['active'] = False
                self._save_json('devices.json', self.devices)
                self.logger.info(f"Deactivated password: {pwd_id}")
    
    # ========== Settings Management ==========
    
    def get_automation_settings(self) -> Dict[str, Any]:
        """Get automation settings"""
        with self.settings_lock:
            return self.settings.get('automation', {})
    
    def update_automation_settings(self, settings: Dict[str, Any]):
        """Update automation settings"""
        with self.settings_lock:
            if 'automation' not in self.settings:
                self.settings['automation'] = {}
            
            self.settings['automation'].update(settings)
            self._save_json('settings.json', self.settings)
            self.logger.info(f"Updated automation settings: {settings}")
    
    def update_home_state(self, occupied: bool, method: str, **kwargs):
        """Update home occupied state"""
        with self.settings_lock:
            self.settings['home_occupied'] = occupied
            self.settings['last_access'] = {
                'method': method,
                'timestamp': datetime.now().isoformat(),
                **kwargs
            }
            
            self._save_json('settings.json', self.settings)
            self.logger.info(f"Home state updated: occupied={occupied}, method={method}")
    
    def get_sync_info(self) -> Dict[str, Any]:
        """Get sync information"""
        with self.settings_lock:
            return self.settings.get('sync', {})
    
    def update_sync_info(self, last_sync: str):
        """Update last sync timestamp"""
        with self.settings_lock:
            if 'sync' not in self.settings:
                self.settings['sync'] = {}
            
            self.settings['sync']['last_sync_server'] = last_sync
            self._save_json('settings.json', self.settings)
    
    # ========== Sync from Server ==========
    
    def apply_sync_changes(self, changes: Dict[str, Any]) -> bool:
        """Apply changes from server sync"""
        try:
            with self.devices_lock, self.settings_lock:
                # Update devices
                if 'devices' in changes:
                    for device_id, device_data in changes['devices'].items():
                        if 'devices' not in self.devices:
                            self.devices['devices'] = {}
                        self.devices['devices'][device_id] = device_data
                    
                    self.logger.info(f"Synced {len(changes['devices'])} devices")
                
                # Update passwords
                if 'passwords' in changes:
                    for pwd_id, pwd_data in changes['passwords'].items():
                        if 'passwords' not in self.devices:
                            self.devices['passwords'] = {}
                        self.devices['passwords'][pwd_id] = pwd_data
                    
                    self.logger.info(f"Synced {len(changes['passwords'])} passwords")
                
                # Update RFID cards
                if 'rfid_cards' in changes:
                    for uid, card_data in changes['rfid_cards'].items():
                        if 'rfid_cards' not in self.devices:
                            self.devices['rfid_cards'] = {}
                        self.devices['rfid_cards'][uid] = card_data
                    
                    self.logger.info(f"Synced {len(changes['rfid_cards'])} RFID cards")
                
                # Update settings
                if 'settings' in changes:
                    automation = changes['settings'].get('automation', {})
                    if automation:
                        if 'automation' not in self.settings:
                            self.settings['automation'] = {}
                        self.settings['automation'].update(automation)
                    
                    self.logger.info("Synced automation settings")
                
                # Save all changes
                self.save_all()
                
                return True
                
        except Exception as e:
            self.logger.error(f"Error applying sync changes: {e}")
            return False
    
    # ========== Statistics ==========
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics"""
        with self.devices_lock, self.settings_lock:
            return {
                'devices_count': len(self.devices.get('devices', {})),
                'rfid_cards_count': len(self.devices.get('rfid_cards', {})),
                'passwords_count': len(self.devices.get('passwords', {})),
                'home_occupied': self.settings.get('home_occupied', False),
                'last_sync': self.settings.get('sync', {}).get('last_sync_server')
            }