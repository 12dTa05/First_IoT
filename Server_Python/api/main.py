from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
from datetime import datetime
import logging
import uvicorn
import os
import asyncio

from config.settings import settings
from services.database import db
from services.mqtt_service import init_mqtt_service, process_websocket_broadcasts 
from services.alert_service import alert_service
from services.offline_detector import offline_detector

from routes import auth, devices, telemetry, access, gateways, commands, sync, dashboard, websocket, system

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    try:
        db.connect()
        logger.info('Database connected successfully')
        
        # Initialize MQTT service
        mqtt_config = {
            'host': settings.MQTT_HOST,
            'port': settings.MQTT_PORT,
            'username': settings.MQTT_USERNAME,
            'password': settings.MQTT_PASSWORD,
            'use_tls': settings.MQTT_USE_TLS if hasattr(settings, 'MQTT_USE_TLS') else False
        }
        
        mqtt_connected = init_mqtt_service(mqtt_config)
        if mqtt_connected:
            logger.info('MQTT service connected successfully')
        else:
            logger.warning('MQTT service failed to connect, will retry automatically')
        
        # Start offline detector with optimized settings
        # check_interval: 10 seconds for faster detection
        # device_timeout: 90 seconds (3x heartbeat interval of 30s)
        # gateway_timeout: 90 seconds (3x heartbeat interval of 30s)
        await offline_detector.start()
        logger.info('Offline detector started (check: 10s, timeout: 90s)')
        
        # Start alert service
        await alert_service.start()
        logger.info('Alert service started')
        
        # Start WebSocket broadcast processor
        asyncio.create_task(process_websocket_broadcasts())
        logger.info('WebSocket broadcast processor started')
        
        logger.info('=' * 70)
        logger.info('API Server started successfully')
        logger.info(f'Listening on port {settings.API_PORT}')
        logger.info('Status tracking: ENABLED (10s check, 90s timeout)')
        logger.info('=' * 70)
    except Exception as e:
        logger.error(f'Failed to start server: {e}', exc_info=True)
        raise e
    
    yield
    
    # Shutdown
    try:
        logger.info('Shutting down services...')
        
        await alert_service.stop()
        logger.info('Alert service stopped')
        
        await offline_detector.stop()
        logger.info('Offline detector stopped')
        
        from services.mqtt_service import mqtt_service
        if mqtt_service:
            mqtt_service.disconnect()
            logger.info('MQTT service disconnected')
            
        db.close()
        logger.info('Database connection closed')
        
        logger.info('API Server shut down successfully')
    except Exception as e:
        logger.error(f'Error during shutdown: {e}', exc_info=True)

# Initialize FastAPI app
app = FastAPI(
    title='IoT API Server',
    version='2.0.0',
    description='Enhanced IoT API with improved status tracking',
    lifespan=lifespan
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)

# Health check endpoint with detailed status
@app.get('/health')
@limiter.limit('100/15minutes')
async def health_check(request: Request):
    """Health check endpoint with service status details"""
    from services.mqtt_service import mqtt_service
    
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'database': db.is_connected() if hasattr(db, 'is_connected') else True,
            'mqtt': mqtt_service.connected if mqtt_service else False,
            'offline_detector': offline_detector.running,
            'alert_service': alert_service.running if hasattr(alert_service, 'running') else True
        },
        'configuration': {
            'offline_check_interval': offline_detector.check_interval,
            'device_timeout': offline_detector.device_timeout,
            'gateway_timeout': offline_detector.gateway_timeout
        }
    }
    
    all_services_healthy = all(health_status['services'].values())
    health_status['status'] = 'healthy' if all_services_healthy else 'degraded'
    
    return health_status

# Status monitoring endpoint (admin only)
@app.get('/api/admin/status-monitor')
async def status_monitor():
    """Get detailed status monitoring information"""
    try:
        # Get gateway status summary
        gateway_query = """
            SELECT 
                status,
                COUNT(*) as count,
                AVG(EXTRACT(EPOCH FROM (NOW() - last_seen))) as avg_seconds_since_seen
            FROM gateways
            GROUP BY status
        """
        gateway_stats = db.query(gateway_query)
        
        # Get device status summary
        device_query = """
            SELECT 
                status,
                COUNT(*) as count,
                AVG(EXTRACT(EPOCH FROM (NOW() - last_seen))) as avg_seconds_since_seen
            FROM devices
            GROUP BY status
        """
        device_stats = db.query(device_query)
        
        # Get recent offline events
        recent_offline_query = """
            SELECT 
                time,
                gateway_id,
                device_id,
                event,
                message
            FROM system_logs
            WHERE event IN ('gateway_offline', 'device_offline')
              AND time > NOW() - INTERVAL '1 hour'
            ORDER BY time DESC
            LIMIT 20
        """
        recent_offline = db.query(recent_offline_query)
        
        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'gateway_stats': gateway_stats,
            'device_stats': device_stats,
            'recent_offline_events': recent_offline,
            'offline_detector': {
                'running': offline_detector.running,
                'check_interval': offline_detector.check_interval,
                'device_timeout': offline_detector.device_timeout,
                'gateway_timeout': offline_detector.gateway_timeout
            }
        }
    except Exception as e:
        logger.error(f'Error in status monitor: {e}', exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

# Include routers
app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(telemetry.router)
app.include_router(access.router)
app.include_router(gateways.router)
app.include_router(commands.router)
app.include_router(sync.router)
app.include_router(dashboard.router)
app.include_router(websocket.router)
app.include_router(system.router)

if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host='0.0.0.0',
        port=settings.API_PORT,
        reload=False,
        log_level='info'
    )