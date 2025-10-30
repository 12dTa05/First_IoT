from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from services.websocket_manager import ws_manager
from middleware.auth import verify_token
import logging
import jwt
from config.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

@router.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    try:
        # Verify JWT token
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
            user_id = payload.get('user_id')
            
            if not user_id:
                await websocket.close(code=1008, reason='Invalid token: missing user_id')
                return
                
        except jwt.ExpiredSignatureError:
            await websocket.close(code=1008, reason='Token expired')
            return
        except jwt.InvalidTokenError:
            await websocket.close(code=1008, reason='Invalid token')
            return
        
        # Connect WebSocket
        await ws_manager.connect(websocket, user_id)
        
        # Send welcome message
        await ws_manager.send_personal_message({
            'type': 'connection',
            'status': 'connected',
            'user_id': user_id,
            'message': 'Real-time connection established'
        }, websocket)
        
        # Keep connection alive and handle incoming messages
        try:
            while True:
                # Receive messages from client (e.g., ping/pong)
                data = await websocket.receive_text()
                
                # Handle client messages if needed
                if data == 'ping':
                    await ws_manager.send_personal_message({
                        'type': 'pong'
                    }, websocket)
                    
        except WebSocketDisconnect:
            logger.info(f'WebSocket disconnected normally: user={user_id}')
        finally:
            await ws_manager.disconnect(websocket, user_id)
            
    except Exception as e:
        logger.error(f'WebSocket error: {e}')
        try:
            await websocket.close(code=1011, reason='Internal server error')
        except:
            pass