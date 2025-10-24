from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from datetime import datetime, timedelta
from ..core.database import get_db
from ..api.devices import verify_token
from ..models.schemas import AccessLogResponse

router = APIRouter(prefix="/access", tags=["access"])

@router.get("/logs", response_model=List[AccessLogResponse])
async def get_access_logs(
    device_id: Optional[str] = None,
    method: Optional[str] = None,
    result: Optional[str] = None,
    hours: int = 24,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = """
        SELECT al.time, al.device_id, al.method, al.result,
               COALESCE(p.owner, r.owner) as owner
        FROM access_logs al
        LEFT JOIN passwords p ON al.password_id = p.password_id
        LEFT JOIN rfid_cards r ON al.rfid_uid = r.uid
        WHERE al.time > $1
    """
    params = [datetime.utcnow() - timedelta(hours=hours)]
    param_count = 2
    
    if device_id:
        query += f" AND al.device_id = ${param_count}"
        params.append(device_id)
        param_count += 1
    
    if method:
        query += f" AND al.method = ${param_count}"
        params.append(method)
        param_count += 1
    
    if result:
        query += f" AND al.result = ${param_count}"
        params.append(result)
        param_count += 1
    
    query += f" ORDER BY al.time DESC LIMIT ${param_count}"
    params.append(limit)
    
    result = await db.execute(query, *params)
    
    logs = []
    for row in result:
        logs.append({
            "time": row[0],
            "device_id": row[1],
            "method": row[2],
            "result": row[3],
            "owner": row[4]
        })
    return logs

@router.get("/stats")
async def get_access_stats(
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = """
        SELECT 
            method,
            result,
            COUNT(*) as count
        FROM access_logs
        WHERE time > $1
        GROUP BY method, result
    """
    result = await db.execute(query, datetime.utcnow() - timedelta(hours=hours))
    
    stats = {}
    for row in result:
        method = row[0]
        if method not in stats:
            stats[method] = {}
        stats[method][row[1]] = row[2]
    
    return stats