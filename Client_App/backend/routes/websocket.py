from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Cookie
import websockets
import asyncio
import json
import logging
from typing import Optional
from config.settings import settings
from services.auth_service import auth_service

router = APIRouter()
logger = logging.getLogger(__name__)

class WebSocketProxy:
    def __init__(self):
        self.active_connections = {}
    
    async def connect_to_vps(self, token: str):
        ws_url = f"{settings.VPS_WS_URL}/ws?token={token}"
        return await websockets.connect(ws_url)
    
    async def proxy_websocket(
        self,
        client_ws: WebSocket,
        vps_ws: websockets.WebSocketClientProtocol
    ):
        async def forward_client_to_vps():
            try:
                while True:
                    data = await client_ws.receive_text()
                    await vps_ws.send(data)
            except WebSocketDisconnect:
                logger.info("Client disconnected")
            except Exception as e:
                logger.error(f"Error forwarding client to VPS: {e}")
        
        async def forward_vps_to_client():
            try:
                async for message in vps_ws:
                    await client_ws.send_text(message)
            except Exception as e:
                logger.error(f"Error forwarding VPS to client: {e}")
        
        await asyncio.gather(
            forward_client_to_vps(),
            forward_vps_to_client(),
            return_exceptions=True
        )

ws_proxy = WebSocketProxy()

@router.websocket('/ws')
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: Optional[str] = Cookie(None, alias=settings.SESSION_COOKIE_NAME)
):
    if not session_id:
        await websocket.close(code=1008, reason='Authentication required')
        return
    
    session = auth_service.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason='Invalid session')
        return
    
    token = session.get('token')
    if not token:
        await websocket.close(code=1008, reason='No token found')
        return
    
    await websocket.accept()
    
    try:
        vps_ws = await ws_proxy.connect_to_vps(token)
        
        logger.info(f"WebSocket proxy established for user {session.get('username')}")
        
        await websocket.send_json({
            'type': 'connection',
            'status': 'connected',
            'message': 'Connected to real-time updates'
        })
        
        await ws_proxy.proxy_websocket(websocket, vps_ws)
        
    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        try:
            await websocket.close(code=1011, reason='Proxy connection failed')
        except:
            pass
    finally:
        try:
            await vps_ws.close()
        except:
            pass