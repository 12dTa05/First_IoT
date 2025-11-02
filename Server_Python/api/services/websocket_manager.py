import logging
import json
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect
import asyncio

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        # user_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """Register new WebSocket connection"""
        await websocket.accept()
        
        async with self.lock:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
        
        logger.info(f'WebSocket connected: user={user_id}, total={len(self.active_connections[user_id])}')
    
    async def disconnect(self, websocket: WebSocket, user_id: str):
        """Remove WebSocket connection"""
        async with self.lock:
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
        
        logger.info(f'WebSocket disconnected: user={user_id}')
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send message to specific WebSocket"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f'Error sending message: {e}')
    
    async def broadcast_to_user(self, user_id: str, message: dict):
        """Broadcast message to all connections of a user"""
        if user_id not in self.active_connections:
            return
        
        disconnected = set()
        
        for websocket in self.active_connections[user_id]:
            try:
                await websocket.send_json(message)
            except WebSocketDisconnect:
                disconnected.add(websocket)
            except Exception as e:
                logger.error(f'Error broadcasting to user {user_id}: {e}')
                disconnected.add(websocket)
        
        # Clean up disconnected websockets
        if disconnected:
            async with self.lock:
                self.active_connections[user_id] -= disconnected
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
    
    async def broadcast_device_status(self, device_id: str, user_id: str, status: dict):
        """Broadcast device status update"""
        message = {
            'type': 'device_status',
            'device_id': device_id,
            'data': status
        }
        await self.broadcast_to_user(user_id, message)
    
    async def broadcast_alert(self, user_id: str, alert: dict):
        """Broadcast alert to user"""
        message = {
            'type': 'alert',
            'data': alert
        }
        await self.broadcast_to_user(user_id, message)
    
    async def broadcast_access_event(self, user_id: str, access: dict):
        """Broadcast access event"""
        message = {
            'type': 'access_event',
            'data': access
        }
        await self.broadcast_to_user(user_id, message)
    
    async def broadcast_telemetry(self, user_id: str, telemetry: dict):
        """Broadcast telemetry update"""
        message = {
            'type': 'telemetry',
            'data': telemetry
        }
        await self.broadcast_to_user(user_id, message)
    
    def get_connection_count(self, user_id: str = None) -> int:
        """Get number of active connections"""
        if user_id:
            return len(self.active_connections.get(user_id, set()))
        return sum(len(conns) for conns in self.active_connections.values())

# Singleton instance
ws_manager = WebSocketManager()