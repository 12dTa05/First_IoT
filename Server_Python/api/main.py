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
        
        # Initialize MQTT service
        mqtt_config = {
            'host': settings.MQTT_HOST,
            'port': settings.MQTT_PORT,
            'username': settings.MQTT_USERNAME,
            'password': settings.MQTT_PASSWORD,
            'use_tls': False
        }
        init_mqtt_service(mqtt_config)
        
        # Start background tasks
        await offline_detector.start()
        await alert_service.start()
        
        # QUAN TRỌNG: Start WebSocket broadcast processor
        asyncio.create_task(process_websocket_broadcasts())
        
        logger.info('API Server started successfully')
        logger.info(f'Listening on port {settings.API_PORT}')
    except Exception as e:
        logger.error(f'Failed to start server: {e}')
        raise e
    
    yield
    
    # Shutdown
    try:
        await alert_service.stop()
        await offline_detector.stop()
        
        from services.mqtt_service import mqtt_service
        if mqtt_service:
            mqtt_service.disconnect()
            
        db.close()
        logger.info('API Server shut down successfully')
    except Exception as e:
        logger.error(f'Error during shutdown: {e}')

# Initialize FastAPI app
app = FastAPI(
    title='IoT API Server',
    version='2.0.0',
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

# Health check endpoint
@app.get('/health')
@limiter.limit('100/15minutes')
async def health_check(request: Request):
    """Health check endpoint"""
    try:
        from services.mqtt_service import mqtt_service
        
        db_healthy = False
        try:
            db.query('SELECT 1')
            db_healthy = True
        except:
            pass
        
        mqtt_healthy = mqtt_service.connected if mqtt_service else False
        
        return {
            'status': 'healthy' if (db_healthy and mqtt_healthy) else 'degraded',
            'timestamp': datetime.utcnow().isoformat(),
            'services': {
                'database': 'up' if db_healthy else 'down',
                'mqtt': 'up' if mqtt_healthy else 'down'
            }
        }
    except Exception as e:
        logger.error(f'Health check failed: {e}')
        return {
            'status': 'unhealthy',
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
        reload=False
    )