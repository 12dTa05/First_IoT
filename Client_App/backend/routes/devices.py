from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import logging
from services.api_client import api_client
from routes.auth import get_current_session

router = APIRouter(prefix='/devices', tags=['devices'])
logger = logging.getLogger(__name__)

class CommandRequest(BaseModel):
    command_type: str
    parameters: Optional[dict] = None

@router.get('')
async def get_devices(session: dict = Depends(get_current_session)):
    token = session.get('token')
    result = await api_client.get('/api/devices', token=token)
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch devices'))
    
    return result

@router.get('/{device_id}')
async def get_device(device_id: str, session: dict = Depends(get_current_session)):
    token = session.get('token')
    result = await api_client.get(f'/api/devices/{device_id}', token=token)
    
    if not result.get('success'):
        status_code = result.get('status_code', 500)
        raise HTTPException(status_code=status_code, detail=result.get('error', 'Device not found'))
    
    return result

@router.post('/{device_id}/command')
async def send_command(
    device_id: str,
    command: CommandRequest,
    session: dict = Depends(get_current_session)
):
    token = session.get('token')
    result = await api_client.post(
        f'/api/commands/{device_id}',
        token=token,
        json_data={
            'command_type': command.command_type,
            'parameters': command.parameters or {}
        }
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Command failed'))
    
    return result

@router.get('/{device_id}/telemetry')
async def get_telemetry(
    device_id: str,
    hours: int = 24,
    session: dict = Depends(get_current_session)
):
    token = session.get('token')
    result = await api_client.get(
        f'/api/telemetry/{device_id}',
        token=token,
        params={'hours': hours}
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch telemetry'))
    
    return result

@router.get('/{device_id}/access-logs')
async def get_access_logs(
    device_id: str,
    hours: int = 24,
    session: dict = Depends(get_current_session)
):
    token = session.get('token')
    result = await api_client.get(
        f'/api/access/{device_id}/logs',
        token=token,
        params={'hours': hours}
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch logs'))
    
    return result

@router.get('/{device_id}/telemetry')
async def get_device_telemetry(
    device_id: str,
    hours: int = 24,
    session: dict = Depends(get_current_session)
):
    """Get telemetry data for a device"""
    token = session.get('token')
    result = await api_client.get(
        f'/api/telemetry/{device_id}',
        token=token,
        params={'hours': hours}
    )
    
    if not result.get('success'):
        raise HTTPException(
            status_code=result.get('status_code', 500),
            detail=result.get('error', 'Failed to fetch telemetry')
        )
    
    return result

@router.get('/{device_id}/access-logs')
async def get_device_access_logs(
    device_id: str,
    hours: int = 24,
    session: dict = Depends(get_current_session)
):
    """Get access logs for a device"""
    token = session.get('token')
    result = await api_client.get(
        f'/api/access/{device_id}/logs',
        token=token,
        params={'hours': hours}
    )
    
    if not result.get('success'):
        raise HTTPException(
            status_code=result.get('status_code', 500),
            detail=result.get('error', 'Failed to fetch access logs')
        )
    
    return result