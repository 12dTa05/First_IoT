from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from services.mqtt_service import mqtt_service
from services.database import db
from middleware.auth import get_current_user, check_device_ownership
import json
import uuid

router = APIRouter(prefix='/api/commands', tags=['commands'])

class CommandRequest(BaseModel):
    command: str
    params: Optional[dict] = None

class UnlockRequest(BaseModel):
    duration: int = 5

@router.post('/{gateway_id}/{device_id}')
async def send_command(gateway_id: str, device_id: str, req: CommandRequest, current_user: dict = Depends(get_current_user)):
    """Send command to device and log to command_logs"""
    try:
        # Verify ownership
        await check_device_ownership(device_id, current_user)
        
        # Generate command ID
        command_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # Prepare MQTT message
        topic = f'gateway/{gateway_id}/command/{device_id}'
        message = {
            'command_id': command_id,
            'device_id': device_id,
            'command': req.command,
            'params': req.params or {},
            'timestamp': timestamp,
            'user_id': current_user.get('user_id')
        }
        
        # Log command to database BEFORE sending
        log_query = """
            INSERT INTO command_logs (time, command_id, source, device_id, gateway_id, user_id, command_type, status, params, metadata)
            VALUES (%s::timestamptz, %s, 'client', %s, %s, %s, %s, 'sent', %s, %s)
        """
        
        db.query(log_query, (
            timestamp, command_id, device_id, gateway_id, current_user.get('user_id'), req.command, json.dumps(req.params or {}), json.dumps({'source_ip': 'api'})
        ))
        
        # Publish to MQTT
        success = mqtt_service.publish(topic, message)
        
        if not success:
            raise HTTPException(status_code=503, detail='Failed to send command to device')
        
        return {
            'success': True,
            'command_id': command_id,
            'message': 'Command sent successfully'
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/{gateway_id}/{device_id}/unlock')
async def unlock_door(gateway_id: str, device_id: str, req: UnlockRequest, current_user: dict = Depends(get_current_user)):
    """Unlock door for specified duration"""
    return await send_command(gateway_id, device_id, CommandRequest(command='unlock', params={'duration': req.duration}), current_user)

@router.post('/{gateway_id}/{device_id}/lock')
async def lock_door(gateway_id: str, device_id: str, current_user: dict = Depends(get_current_user)):
    """Lock door immediately"""
    return await send_command(gateway_id, device_id, CommandRequest(command='lock'), current_user)

@router.post('/{gateway_id}/{device_id}/fan_on')
async def fan_on(gateway_id: str, device_id: str, current_user: dict = Depends(get_current_user)):
    """Turn fan on"""
    return await send_command(gateway_id, device_id, CommandRequest(command='fan_on'), current_user)

@router.post('/{gateway_id}/{device_id}/fan_off')
async def fan_off(gateway_id: str, device_id: str, current_user: dict = Depends(get_current_user)):
    """Turn fan off"""
    return await send_command(gateway_id, device_id, CommandRequest(command='fan_off'), current_user)

@router.get('/{command_id}/status')
async def get_command_status(command_id: str, current_user: dict = Depends(get_current_user)
):
    """Get status of a command"""
    try:
        query = """
            SELECT command_id, command_type, status,  time, completed_at, result, params
            FROM command_logs
            WHERE command_id = %s AND user_id = %s
            ORDER BY time DESC
            LIMIT 1
        """
        
        result = db.query(query, (command_id, current_user.get('user_id')))
        
        if not result or len(result) == 0:
            raise HTTPException(status_code=404, detail='Command not found')
        
        return {
            'success': True,
            'data': result[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))