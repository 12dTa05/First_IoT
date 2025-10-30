from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta
from services.database import db
from middleware.auth import get_current_user
from typing import Optional

router = APIRouter(prefix='/api/system', tags=['system'])

@router.get('/logs')
async def get_system_logs(
    current_user: dict = Depends(get_current_user),
    log_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=500)
):
    """Get system logs with filters"""
    try:
        user_id = current_user['user_id']
        
        # Build query dynamically
        conditions = ['user_id = %s', "time > NOW() - INTERVAL '%s hours'"]
        params = [user_id, hours]
        
        if log_type:
            conditions.append('log_type = %s')
            params.append(log_type)
        
        if severity:
            conditions.append('severity = %s')
            params.append(severity)
        
        if device_id:
            conditions.append('device_id = %s')
            params.append(device_id)
        
        where_clause = ' AND '.join(conditions)
        
        query = f"""
            SELECT time, gateway_id, device_id, log_type, event, severity, message, value, threshold, metadata
            FROM system_logs
            WHERE {where_clause}
            ORDER BY time DESC
            LIMIT %s
        """
        
        params.append(limit)
        result = db.query(query, tuple(params))
        
        return {
            'success': True,
            'data': result,
            'count': len(result)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/stats')
async def get_system_stats(current_user: dict = Depends(get_current_user)):
    """Get overall system statistics"""
    try:
        user_id = current_user['user_id']
        
        # Device stats
        devices_query = """
            SELECT device_type, COUNT(*) as count, COUNT(*) FILTER (WHERE is_online = TRUE) as online_count
            FROM devices
            WHERE user_id = %s
            GROUP BY device_type
        """
        devices_stats = db.query(devices_query, (user_id,))
        
        # Access stats (last 7 days)
        access_query = """
            SELECT 
                DATE(time) as date,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE result = 'granted') as granted,
                COUNT(*) FILTER (WHERE result = 'denied') as denied
            FROM access_logs
            WHERE user_id = %s AND time > NOW() - INTERVAL '7 days'
            GROUP BY DATE(time)
            ORDER BY date DESC
        """
        access_stats = db.query(access_query, (user_id,))
        
        # Alert stats (last 30 days)
        alerts_query = """
            SELECT event as alert_type, severity, COUNT(*) as count
            FROM system_logs
            WHERE user_id = %s AND log_type = 'alert' AND time > NOW() - INTERVAL '30 days'
            GROUP BY event, severity
        """
        alerts_stats = db.query(alerts_query, (user_id,))
        
        return {
            'success': True,
            'data': {'devices_by_type': devices_stats, 'access_by_day': access_stats, 'alerts_by_type': alerts_stats}
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/health')
async def system_health(current_user: dict = Depends(get_current_user)):
    """Check system health for user's devices"""
    try:
        user_id = current_user['user_id']
        
        # Check offline devices
        offline_query = """
            SELECT device_id, device_type, location, last_seen, EXTRACT(EPOCH FROM (NOW() - last_seen))/60 as minutes_offline
            FROM devices
            WHERE user_id = %s AND is_online = FALSE
        """
        offline_devices = db.query(offline_query, (user_id,))
        
        # Check recent errors
        errors_query = """
            SELECT time, device_id, event, message
            FROM system_logs
            WHERE user_id = %s AND severity IN ('error', 'critical') AND time > NOW() - INTERVAL '24 hours'
            ORDER BY time DESC
            LIMIT 10
        """
        recent_errors = db.query(errors_query, (user_id,))
        
        # Overall health score
        total_devices = db.query_one(
            'SELECT COUNT(*) as total FROM devices WHERE user_id = %s',
            (user_id,)
        )['total']
        
        online_devices = db.query_one(
            'SELECT COUNT(*) as online FROM devices WHERE user_id = %s AND is_online = TRUE',
            (user_id,)
        )['online']
        
        health_score = (online_devices / total_devices * 100) if total_devices > 0 else 100
        
        return {
            'success': True,
            'data': {
                'health_score': round(health_score, 1),
                'total_devices': total_devices,
                'online_devices': online_devices,
                'offline_devices': offline_devices,
                'recent_errors': recent_errors
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))