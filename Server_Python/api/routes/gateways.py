from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta
from services.database import db
from services.offline_detector import offline_detector
from middleware.auth import get_current_user
import logging

router = APIRouter(prefix='/api/gateways', tags=['gateways'])
logger = logging.getLogger(__name__)

@router.get('/')
async def get_gateways(current_user: dict = Depends(get_current_user)):
    """Get all gateways for current user with enhanced status info"""
    try:
        user_id = current_user['user_id']
        
        query = """
            SELECT 
                g.gateway_id,
                g.name,
                g.location,
                g.status,
                g.last_seen,
                g.database_version,
                g.created_at,
                g.updated_at,
                EXTRACT(EPOCH FROM (NOW() - g.last_seen)) as seconds_since_last_seen,
                CASE 
                    WHEN g.status = 'offline' THEN 'offline'
                    WHEN g.last_seen IS NULL THEN 'unknown'
                    WHEN g.last_seen > NOW() - INTERVAL '1 minute' THEN 'excellent'
                    WHEN g.last_seen > NOW() - INTERVAL '2 minutes' THEN 'good'
                    WHEN g.last_seen > NOW() - INTERVAL '5 minutes' THEN 'fair'
                    ELSE 'poor'
                END as connection_quality,
                (
                    SELECT COUNT(*) 
                    FROM devices d 
                    WHERE d.gateway_id = g.gateway_id
                ) as total_devices,
                (
                    SELECT COUNT(*) 
                    FROM devices d 
                    WHERE d.gateway_id = g.gateway_id AND d.status = 'online'
                ) as online_devices
            FROM gateways g
            WHERE g.user_id = %s
            ORDER BY g.created_at DESC
        """
        
        gateways = db.query(query, (user_id,))
        
        return {
            'success': True,
            'data': gateways,
            'count': len(gateways) if gateways else 0
        }
        
    except Exception as e:
        logger.error(f'Error fetching gateways: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{gateway_id}')
async def get_gateway(gateway_id: str, current_user: dict = Depends(get_current_user)):
    """Get detailed information about a specific gateway"""
    try:
        user_id = current_user['user_id']
        
        query = """
            SELECT 
                g.*,
                EXTRACT(EPOCH FROM (NOW() - g.last_seen)) as seconds_since_last_seen,
                (
                    SELECT json_agg(json_build_object(
                        'device_id', d.device_id,
                        'device_type', d.device_type,
                        'status', d.status,
                        'last_seen', d.last_seen
                    ))
                    FROM devices d
                    WHERE d.gateway_id = g.gateway_id
                ) as devices
            FROM gateways g
            WHERE g.gateway_id = %s AND g.user_id = %s
        """
        
        result = db.query_one(query, (gateway_id, user_id))
        
        if not result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        return {
            'success': True,
            'data': result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error fetching gateway: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/{gateway_id}/force-check')
async def force_check_gateway(gateway_id: str, current_user: dict = Depends(get_current_user)):
    """Force immediate status check for a specific gateway"""
    try:
        user_id = current_user['user_id']
        
        # Verify gateway belongs to user
        verify_query = "SELECT gateway_id FROM gateways WHERE gateway_id = %s AND user_id = %s"
        verify_result = db.query_one(verify_query, (gateway_id, user_id))
        
        if not verify_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        # Force offline detector to check this gateway immediately
        was_marked_offline = await offline_detector.force_check_gateway(gateway_id)
        
        # Get updated gateway status
        status_query = """
            SELECT gateway_id, status, last_seen, 
                   EXTRACT(EPOCH FROM (NOW() - last_seen)) as seconds_since_last_seen
            FROM gateways 
            WHERE gateway_id = %s
        """
        updated_status = db.query_one(status_query, (gateway_id,))
        
        return {
            'success': True,
            'message': f'Gateway status checked',
            'was_marked_offline': was_marked_offline,
            'current_status': updated_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error in force check gateway: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{gateway_id}/connection-history')
async def get_connection_history(
    gateway_id: str,
    current_user: dict = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168)
):
    """Get connection history for a gateway (online/offline events)"""
    try:
        user_id = current_user['user_id']
        
        # Verify gateway belongs to user
        verify_query = "SELECT gateway_id FROM gateways WHERE gateway_id = %s AND user_id = %s"
        verify_result = db.query_one(verify_query, (gateway_id, user_id))
        
        if not verify_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        # Get connection events from system logs
        history_query = """
            SELECT 
                time,
                event,
                severity,
                message,
                metadata
            FROM system_logs
            WHERE gateway_id = %s
              AND event IN ('gateway_offline', 'gateway_online', 'gateway_offline_cascade')
              AND time > NOW() - INTERVAL '%s hours'
            ORDER BY time DESC
        """
        
        history = db.query(history_query, (gateway_id, hours))
        
        # Calculate uptime statistics
        stats_query = """
            SELECT 
                COUNT(*) FILTER (WHERE event = 'gateway_offline') as offline_count,
                COUNT(*) FILTER (WHERE event = 'gateway_online') as online_count,
                MIN(time) as oldest_event,
                MAX(time) as newest_event
            FROM system_logs
            WHERE gateway_id = %s
              AND event IN ('gateway_offline', 'gateway_online')
              AND time > NOW() - INTERVAL '%s hours'
        """
        
        stats = db.query_one(stats_query, (gateway_id, hours))
        
        return {
            'success': True,
            'gateway_id': gateway_id,
            'time_range_hours': hours,
            'history': history,
            'statistics': stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error fetching connection history: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/{gateway_id}/trigger-sync')
async def trigger_gateway_sync(gateway_id: str, current_user: dict = Depends(get_current_user)):
    """Trigger immediate database sync for a gateway"""
    try:
        user_id = current_user['user_id']
        
        # Verify gateway belongs to user
        verify_query = "SELECT gateway_id FROM gateways WHERE gateway_id = %s AND user_id = %s"
        verify_result = db.query_one(verify_query, (gateway_id, user_id))
        
        if not verify_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        # Publish sync trigger via MQTT
        from services.mqtt_service import mqtt_service
        
        if mqtt_service and mqtt_service.connected:
            sync_topic = f'gateway/{gateway_id}/sync/trigger'
            sync_payload = {
                'reason': 'manual_trigger',
                'triggered_by': user_id,
                'timestamp': datetime.now().isoformat()
            }
            
            success = mqtt_service.publish(sync_topic, sync_payload)
            
            if success:
                return {
                    'success': True,
                    'message': f'Sync trigger sent to gateway {gateway_id}'
                }
            else:
                raise HTTPException(status_code=500, detail='Failed to publish sync trigger')
        else:
            raise HTTPException(status_code=503, detail='MQTT service not available')
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error triggering sync: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))