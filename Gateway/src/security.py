import hmac
import hashlib
import time
import threading
from typing import Set, Dict
from collections import deque


class SecurityManager:    
    def __init__(self, hmac_key: bytes, logger):
        """Initialize security manager"""
        self.hmac_key = hmac_key
        self.logger = logger
        
        # Replay protection
        self.nonce_lock = threading.Lock()
        self.recent_nonces: Set[int] = set()
        self.nonce_timestamps: deque = deque(maxlen=10000)
        
        # Rate limiting
        self.rate_limit_lock = threading.Lock()
        self.request_history: Dict[str, deque] = {}
        
        # Configuration
        self.MAX_TIME_DRIFT = 300  # 5 minutes
        self.NONCE_CLEANUP_INTERVAL = 600  # 10 minutes
        self.RATE_LIMIT_WINDOW = 60  # 1 minute
        self.RATE_LIMIT_MAX_REQUESTS = 10  # Max 10 requests per minute per device
        
        # Start cleanup thread
        self._start_cleanup_thread()
        
        self.logger.info("Security manager initialized")
    
    def _start_cleanup_thread(self):
        """Start background thread to clean old nonces"""
        def cleanup_worker():
            while True:
                time.sleep(self.NONCE_CLEANUP_INTERVAL)
                self._cleanup_old_nonces()
        
        thread = threading.Thread(target=cleanup_worker, daemon=True, name="NonceCleanup")
        thread.start()
    
    def _cleanup_old_nonces(self):
        """Remove nonces older than MAX_TIME_DRIFT"""
        with self.nonce_lock:
            current_time = time.time()
            cutoff_time = current_time - self.MAX_TIME_DRIFT
            
            # Remove old entries
            while self.nonce_timestamps and self.nonce_timestamps[0][0] < cutoff_time:
                _, old_nonce = self.nonce_timestamps.popleft()
                self.recent_nonces.discard(old_nonce)
            
            self.logger.debug(f"Nonce cleanup: {len(self.recent_nonces)} nonces in cache")
    
    # ========== HMAC Functions ==========
    
    def calculate_hmac(self, data: str) -> str:
        """Calculate HMAC-SHA256 for data"""
        try:
            mac = hmac.new(
                self.hmac_key,
                data.encode('utf-8'),
                hashlib.sha256
            )
            return mac.hexdigest()
        except Exception as e:
            self.logger.error(f"Error calculating HMAC: {e}")
            return ""
    
    def verify_hmac(self, data: str, received_hmac: str) -> bool:
        """Verify HMAC-SHA256 signature"""
        try:
            calculated_hmac = self.calculate_hmac(data)
            
            # Constant-time comparison to prevent timing attacks
            is_valid = hmac.compare_digest(calculated_hmac, received_hmac)
            
            if not is_valid:
                self.logger.warning("HMAC verification failed")
            
            return is_valid
            
        except Exception as e:
            self.logger.error(f"Error verifying HMAC: {e}")
            return False
    
    # ========== Replay Attack Protection ==========
    
    def verify_freshness(self, timestamp: int, nonce: int) -> bool:
        """
        Verify request freshness to prevent replay attacks
        
        Args:
            timestamp: Unix timestamp from request
            nonce: Random nonce from request
            
        Returns:
            True if request is fresh, False otherwise
        """
        try:
            current_time = time.time()
            
            # Check timestamp (allow ±5 minutes drift)
            time_diff = abs(current_time - timestamp)
            
            if time_diff > self.MAX_TIME_DRIFT:
                self.logger.warning(
                    f"Request timestamp too old/future: "
                    f"diff={time_diff}s, max={self.MAX_TIME_DRIFT}s"
                )
                return False
            
            # Check nonce
            with self.nonce_lock:
                if nonce in self.recent_nonces:
                    self.logger.warning(f"Duplicate nonce detected: {nonce}")
                    return False
                
                # Add to cache
                self.recent_nonces.add(nonce)
                self.nonce_timestamps.append((current_time, nonce))
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error verifying freshness: {e}")
            return False
    
    # ========== Rate Limiting ==========
    
    def check_rate_limit(self, device_id: str) -> bool:
        """
        Check if device has exceeded rate limit
        
        Args:
            device_id: Device identifier
            
        Returns:
            True if within limit, False if exceeded
        """
        try:
            with self.rate_limit_lock:
                current_time = time.time()
                
                # Initialize history for new device
                if device_id not in self.request_history:
                    self.request_history[device_id] = deque(maxlen=self.RATE_LIMIT_MAX_REQUESTS)
                
                history = self.request_history[device_id]
                
                # Remove requests outside window
                while history and history[0] < current_time - self.RATE_LIMIT_WINDOW:
                    history.popleft()
                
                # Check limit
                if len(history) >= self.RATE_LIMIT_MAX_REQUESTS:
                    self.logger.warning(
                        f"Rate limit exceeded for {device_id}: "
                        f"{len(history)} requests in {self.RATE_LIMIT_WINDOW}s"
                    )
                    return False
                
                # Add current request
                history.append(current_time)
                
                return True
                
        except Exception as e:
            self.logger.error(f"Error checking rate limit: {e}")
            return True  # Fail open to avoid blocking legitimate requests
    
    # ========== Password Hashing ==========
    
    @staticmethod
    def hash_password(password: str, salt: str) -> str:
        """
        Hash password with salt using SHA-256
        
        Args:
            password: Plain text password
            salt: Salt string
            
        Returns:
            Hex digest of hash (first 12 characters)
        """
        salted = salt + password
        hash_obj = hashlib.sha256(salted.encode('utf-8'))
        return hash_obj.hexdigest()[:12]
    
    # ========== Statistics ==========
    
    def get_statistics(self) -> Dict[str, any]:
        """Get security statistics"""
        with self.nonce_lock, self.rate_limit_lock:
            return {
                'nonces_cached': len(self.recent_nonces),
                'devices_tracked': len(self.request_history),
                'max_time_drift': self.MAX_TIME_DRIFT,
                'rate_limit_window': self.RATE_LIMIT_WINDOW,
                'rate_limit_max': self.RATE_LIMIT_MAX_REQUESTS
            }