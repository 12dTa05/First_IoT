from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from services.mqtt_service import mqtt_service

router = APIRouter(prefix='/api/commands', tags=['commands'])

class CommandRequest(BaseModel):
    command: str
    params: Optional[dict] = None

class UnlockRequest(BaseModel):
    duration: int = 5

@router.post('/{gateway_id}/{device_id}')
async def send_command(gateway_id: str, device_id: str, req: CommandRequest):
    try:
        topic = f'iot/{gateway_id}/command'
        message = {
            'device_id': device_id,
            'command': req.command,
            'params': req.params,
            'timestamp': datetime.now().isoformat()
        }
        
        mqtt_service.publish(topic, message)
        
        return {
            'success': True,
            'message': 'Command sent',
            'data': message
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/{gateway_id}/{device_id}/unlock')
async def unlock_door(gateway_id: str, device_id: str, req: UnlockRequest):
    try:
        topic = f'iot/{gateway_id}/command'
        message = {
            'device_id': device_id,
            'command': 'unlock',
            'params': {'duration': req.duration},
            'timestamp': datetime.now().isoformat()
        }
        
        mqtt_service.publish(topic, message)
        
        return {
            'success': True,
            'message': 'Unlock command sent'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/{gateway_id}/{device_id}/lock')
async def lock_door(gateway_id: str, device_id: str):
    try:
        topic = f'iot/{gateway_id}/command'
        message = {
            'device_id': device_id,
            'command': 'lock',
            'timestamp': datetime.now().isoformat()
        }
        
        mqtt_service.publish(topic, message)
        
        return {
            'success': True,
            'message': 'Lock command sent'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))