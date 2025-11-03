from typing import Optional, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}
    
    def create_session(self, user_data: Dict, token: str) -> str:
        session_id = f"session_{int(datetime.now().timestamp() * 1000)}"
        
        self.active_sessions[session_id] = {
            'user_id': user_data.get('user_id'),
            'username': user_data.get('username'),
            'role': user_data.get('role'),
            'token': token,
            'created_at': datetime.now(),
            'last_activity': datetime.now()
        }
        
        logger.info(f"Session created: {session_id} for user {user_data.get('username')}")
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        session = self.active_sessions.get(session_id)
        
        if not session:
            return None
        
        session['last_activity'] = datetime.now()
        return session
    
    def get_token(self, session_id: str) -> Optional[str]:
        session = self.get_session(session_id)
        return session.get('token') if session else None
    
    def destroy_session(self, session_id: str) -> bool:
        if session_id in self.active_sessions:
            user_id = self.active_sessions[session_id].get('user_id')
            del self.active_sessions[session_id]
            logger.info(f"Session destroyed: {session_id} for user {user_id}")
            return True
        return False
    
    def cleanup_expired_sessions(self, max_age_seconds: int = 86400):
        now = datetime.now()
        expired = []
        
        for session_id, session in self.active_sessions.items():
            age = (now - session['last_activity']).total_seconds()
            if age > max_age_seconds:
                expired.append(session_id)
        
        for session_id in expired:
            self.destroy_session(session_id)
        
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")

auth_service = AuthService()