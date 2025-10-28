from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from services.database import db
from middleware.auth import get_current_user, check_device_ownership

router = APIRouter(prefix='/api/telemetry', tags=['telemetry'])

@router.get('/')
async def get_telemetry(
    device_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(100, le=1000),
    current_user: dict = Depends(get_current_user)
):
    try:
        user_id = current_user['user_id']
        
        query = 'SELECT t.* FROM telemetry t WHERE t.user_id = %s'
        params = [user_id]
        
        if device_id:
            query += ' AND t.device_id = %s'
            params.append(device_id)
        
        if start:
            query += ' AND t.time >= %s'
            params.append(start)
        
        if end:
            query += ' AND t.time <= %s'
            params.append(end)
        
        query += ' ORDER BY t.time DESC LIMIT %s'
        params.append(limit)
        
        result = db.query(query, tuple(params))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/latest/{device_id}')
async def get_latest_telemetry(
    device_id: str,
    current_user: dict = Depends(get_current_user),
    ownership: bool = Depends(check_device_ownership)
):
    try:
        result = db.query(
            """SELECT * FROM telemetry 
               WHERE device_id = %s 
               ORDER BY time DESC 
               LIMIT 1""",
            (device_id,)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='No telemetry data found')
        
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/aggregate/{device_id}')
async def get_aggregate_telemetry(
    device_id: str,
    interval: str = Query('1 hour'),
    start: Optional[str] = None,
    end: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    ownership: bool = Depends(check_device_ownership)
):
    try:
        query = """
            SELECT 
                time_bucket(%s, time) AS bucket,
                AVG((data->>'temperature')::float) AS avg_temperature,
                AVG((data->>'humidity')::float) AS avg_humidity,
                COUNT(*) AS sample_count
            FROM telemetry
            WHERE device_id = %s
        """
        params = [interval, device_id]
        
        if start:
            query += ' AND time >= %s'
            params.append(start)
        
        if end:
            query += ' AND time <= %s'
            params.append(end)
        
        query += ' GROUP BY bucket ORDER BY bucket DESC'
        
        result = db.query(query, tuple(params))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))