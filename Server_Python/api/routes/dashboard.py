from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta
from services.database import db
from middleware.auth import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])

@router.get('/overview')
async def get_overview(current_user: dict = Depends(get_current_user)):
    """Get dashboard overview statistics"""
    try:
        user_id = current_user['user_id']
        
        # Count devices by status
        devices_query = """
            SELECT 
                COUNT(*) as total_devices,
                COUNT(*) FILTER (WHERE status = 'online') as online_devices,
                COUNT(*) FILTER (WHERE status= 'offline') as offline_devices
            FROM devices
            WHERE user_id = %s
        """
        devices_stats = db.query_one(devices_query, (user_id,))
        
        # Count gateways
        gateways_query = """
            SELECT 
                COUNT(*) as total_gateways,
                COUNT(*) FILTER (WHERE status = 'online') as online_gateways
            FROM gateways
            WHERE user_id = %s
        """
        gateways_stats = db.query_one(gateways_query, (user_id,))
        
        # Recent access logs (last 24h)
        access_query = """
            SELECT 
                COUNT(*) as total_access,
                COUNT(*) FILTER (WHERE result = 'granted') as granted,
                COUNT(*) FILTER (WHERE result = 'denied') as denied
            FROM access_logs
            WHERE user_id = %s
              AND time > NOW() - INTERVAL '24 hours'
        """
        access_stats = db.query_one(access_query, (user_id,))
        
        # Recent alerts (last 24h)
        alerts_query = """
            SELECT COUNT(*) as alert_count
            FROM system_logs
            WHERE user_id = %s
              AND log_type = 'alert'
              AND time > NOW() - INTERVAL '24 hours'
        """
        alerts_stats = db.query_one(alerts_query, (user_id,))
        
        # Latest temperature readings
        temp_query = """
            SELECT DISTINCT ON (device_id)
                device_id, temperature, humidity, time
            FROM telemetry
            WHERE user_id = %s
              AND time > NOW() - INTERVAL '1 hour'
            ORDER BY device_id, time DESC
        """
        latest_temps = db.query(temp_query, (user_id,))
        
        return {
            'success': True,
            'data': {
                'devices': devices_stats,
                'gateways': gateways_stats,
                'access': access_stats,
                'alerts': alerts_stats,
                'latest_readings': latest_temps
            }
        }
        
    except Exception as e:
        logger.error(f'Dashboard overview error: {e}')
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/recent-activities')
async def get_recent_activities(
    current_user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168)
):
    """Get recent activities (access logs) for dashboard"""
    try:
        user_id = current_user['user_id']
        
        # Get recent access logs
        query = """
            SELECT 
                time,
                device_id,
                gateway_id,
                method,
                result,
                password_id,
                rfid_uid,
                deny_reason
            FROM access_logs
            WHERE user_id = %s
              AND time > NOW() - INTERVAL '1 hour' * %s
            ORDER BY time DESC
            LIMIT 100
        """
        
        activities = db.query(query, (user_id, hours))
        
        return {
            'success': True,
            'data': activities if activities else []
        }
        
    except Exception as e:
        logger.error(f'Error fetching recent activities: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/activity')
async def get_activity(
    current_user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168)
):
    """Get activity timeline for last N hours"""
    try:
        user_id = current_user['user_id']
        
        # Recent access events
        access_query = """
            SELECT 
                time, device_id, method, result,
                'access' as event_type
            FROM access_logs
            WHERE user_id = %s
              AND time > NOW() - INTERVAL '%s hours' * %s
            ORDER BY time DESC
            LIMIT 50
        """
        
        # Recent alerts
        alerts_query = """
            SELECT 
                time, device_id, event, severity,
                'alert' as event_type, message
            FROM system_logs
            WHERE user_id = %s
            AND log_type = 'alert'
            AND time > NOW() - INTERVAL '1 hour' * %s
            ORDER BY time DESC
            LIMIT 50
        """
        
        # Combine results
        access_events = db.query(access_query, (user_id, hours))
        alert_events = db.query(alerts_query, (user_id, hours))
        
        # Merge and sort by time
        all_events = list(access_events) + list(alert_events)
        all_events.sort(key=lambda x: x['time'], reverse=True)
        
        return {
            'success': True,
            'data': all_events[:100]  # Limit to 100 most recent
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/temperature-history')
async def get_temperature_history(
    current_user: dict = Depends(get_current_user),
    device_id: str = Query(...),
    hours: int = Query(24, ge=1, le=168)
):
    """Get temperature history for device"""
    try:
        query = """
            SELECT time, temperature, humidity
            FROM telemetry
            WHERE user_id = %s
            AND device_id = %s
            AND time > NOW() - INTERVAL '1 hour' * %s
            ORDER BY time ASC
        """
        
        result = db.query(query, (current_user['user_id'], device_id, hours))
        
        return {
            'success': True,
            'data': result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/alerts')
async def get_alerts(
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200)
):
    """Get recent alerts"""
    try:
        query = """
            SELECT time, gateway_id, device_id, event, severity,
                   message, value, threshold
            FROM system_logs
            WHERE user_id = %s
              AND log_type = 'alert'
            ORDER BY time DESC
            LIMIT %s
        """
        
        result = db.query(query, (current_user['user_id'], limit))
        
        return {
            'success': True,
            'data': result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/stats')
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Get dashboard statistics for charts and analytics"""
    try:
        user_id = current_user['user_id']
        
        # Device stats by type
        devices_query = """
            SELECT 
                device_type,
                COUNT(*) as count,
                COUNT(*) FILTER (WHERE status = 'online') as online_count,
                COUNT(*) FILTER (WHERE status = 'offline') as offline_count
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
            WHERE user_id = %s 
              AND time > NOW() - INTERVAL '7 days'
            GROUP BY DATE(time)
            ORDER BY date DESC
        """
        access_stats = db.query(access_query, (user_id,))
        
        # Alert stats (last 30 days)
        alerts_query = """
            SELECT 
                event as alert_type,
                severity,
                COUNT(*) as count
            FROM system_logs
            WHERE user_id = %s 
              AND log_type = 'alert' 
              AND time > NOW() - INTERVAL '30 days'
            GROUP BY event, severity
        """
        alerts_stats = db.query(alerts_query, (user_id,))
        
        return {
            'success': True,
            'data': {
                'devices_by_type': devices_stats if devices_stats else [],
                'access_by_day': access_stats if access_stats else [],
                'alerts_by_type': alerts_stats if alerts_stats else []
            }
        }
        
    except Exception as e:
        logger.error(f'Error fetching dashboard stats: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))