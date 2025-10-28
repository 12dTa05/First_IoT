from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.database import db
from middleware.auth import get_current_user, check_device_ownership

router = APIRouter(prefix='/api/devices', tags=['devices'])

class UpdateDeviceRequest(BaseModel):
    location: Optional[str] = None
    metadata: Optional[dict] = None

@router.get('/')
async def get_devices(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['user_id']
        result = db.query(
            """SELECT d.*, g.name AS gateway_name, g.status AS gateway_status
               FROM devices d
               JOIN gateways g ON d.gateway_id = g.gateway_id
               WHERE d.user_id = %s
               ORDER BY d.created_at DESC""",
            (user_id,)
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{device_id}')
async def get_device(
    device_id: str,
    current_user: dict = Depends(get_current_user),
    ownership: bool = Depends(check_device_ownership)
):
    try:
        result = db.query(
            """SELECT d.*, g.name AS gateway_name, g.status AS gateway_status
               FROM devices d
               JOIN gateways g ON d.gateway_id = g.gateway_id
               WHERE d.device_id = %s""",
            (device_id,)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='Device not found')
        
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put('/{device_id}')
async def update_device(
    device_id: str,
    req: UpdateDeviceRequest,
    current_user: dict = Depends(get_current_user),
    ownership: bool = Depends(check_device_ownership)
):
    try:
        import json
        result = db.query(
            """UPDATE devices 
               SET location = COALESCE(%s, location),
                   metadata = COALESCE(%s, metadata),
                   updated_at = NOW()
               WHERE device_id = %s
               RETURNING *""",
            (req.location, json.dumps(req.metadata) if req.metadata else None, device_id)
        )
        
        return result[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{device_id}/health')
async def get_device_health(
    device_id: str,
    current_user: dict = Depends(get_current_user),
    ownership: bool = Depends(check_device_ownership)
):
    try:
        result = db.query(
            'SELECT * FROM device_health_view WHERE device_id = %s',
            (device_id,)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='Device not found')
        
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))