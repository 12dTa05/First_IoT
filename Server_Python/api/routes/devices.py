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
        return {
            'success': True,
            'data': result if result else [],
            'count': len(result) if result else 0
        }
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
        
        return {
            'success': True,
            'data': result[0]
        }
    except HTT
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

@router.post('/{device_id}/force-check')
async def force_check_device(device_id: str, current_user: dict = Depends(get_current_user)):
    """Force immediate status check for a specific device"""
    try:
        user_id = current_user['user_id']
        
        # Verify device belongs to user
        verify_query = "SELECT device_id, gateway_id FROM devices WHERE device_id = %s AND user_id = %s"
        verify_result = db.query_one(verify_query, (device_id, user_id))
        
        if not verify_result:
            raise HTTPException(status_code=404, detail='Device not found')
        
        # Force offline detector to check this device immediately
        from services.offline_detector import offline_detector
        was_marked_offline = await offline_detector.force_check_device(device_id)
        
        # Get updated device status
        status_query = """
            SELECT device_id, status, last_seen, 
                   EXTRACT(EPOCH FROM (NOW() - last_seen)) as seconds_since_last_seen
            FROM devices 
            WHERE device_id = %s
        """
        updated_status = db.query_one(status_query, (device_id,))
        
        return {
            'success': True,
            'message': f'Device status checked',
            'was_marked_offline': was_marked_offline,
            'current_status': updated_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in force check device: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{device_id}/status-history')
async def get_device_status_history(
    device_id: str,
    current_user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168)
):
    """Get status change history for a device"""
    try:
        user_id = current_user['user_id']
        
        # Verify device belongs to user
        verify_query = "SELECT device_id FROM devices WHERE device_id = %s AND user_id = %s"
        verify_result = db.query_one(verify_query, (device_id, user_id))
        
        if not verify_result:
            raise HTTPException(status_code=404, detail='Device not found')
        
        # Get status change events
        history_query = """
            SELECT 
                time,
                event,
                severity,
                message,
                metadata
            FROM system_logs
            WHERE device_id = %s
              AND event IN ('device_offline', 'device_online', 'device_status_change')
              AND time > NOW() - INTERVAL '1 hour' * %s
            ORDER BY time DESC
        """
        
        history = db.query(history_query, (device_id, hours))
        
        # Calculate statistics
        stats_query = """
            SELECT 
                COUNT(*) FILTER (WHERE event = 'device_offline') as offline_count,
                COUNT(*) FILTER (WHERE event = 'device_online') as online_count,
                COUNT(*) FILTER (WHERE event = 'device_status_change') as status_change_count
            FROM system_logs
            WHERE device_id = %s
              AND time > NOW() - INTERVAL '1 hour' * %s
        """
        
        stats = db.query_one(stats_query, (device_id, hours))
        
        return {
            'success': True,
            'device_id': device_id,
            'time_range_hours': hours,
            'history': history,
            'statistics': stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error fetching status history: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))