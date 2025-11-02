from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from services.database import db
from middleware.auth import get_current_user

router = APIRouter(prefix='/api/access', tags=['access'])

@router.get('/logs')
async def get_access_logs(
    device_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = Query(100, le=1000),
    current_user: dict = Depends(get_current_user)
):
    try:
        user_id = current_user['user_id']
        
        query = 'SELECT * FROM access_logs WHERE user_id = %s'
        params = [user_id]
        
        if device_id:
            query += ' AND device_id = %s'
            params.append(device_id)
        
        if start:
            query += ' AND time >= %s'
            params.append(start)
        
        if end:
            query += ' AND time <= %s'
            params.append(end)
        
        if result:
            query += ' AND result = %s'
            params.append(result)
        
        query += ' ORDER BY time DESC LIMIT %s'
        params.append(limit)
        
        results = db.query(query, tuple(params))
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/rfid')
async def get_rfid_cards(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['user_id']
        result = db.query(
            """SELECT * FROM rfid_cards 
               WHERE user_id = %s 
               ORDER BY registered_at DESC""",
            (user_id,)
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))