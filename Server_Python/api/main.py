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

from config.settings import settings
from services.database import db
from services.mqtt_service import mqtt_service
from services.alert_service import alert_service

from routes import auth, devices, telemetry, access, gateways, commands, sync, dashboard

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    try:
        db.connect()
        mqtt_service.connect()
        await offline_detector.start()
        await alert_service.start()
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
        db.close()
        mqtt_service.disconnect()
        logger.info('API Server shut down successfully')
    except Exception as e:
        logger.error(f'Error during shutdown: {e}')

# Initialize FastAPI app with lifespan
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
    """
    Health check endpoint with database and MQTT status
    """
    try:
        # Check database by attempting a simple query
        db_healthy = False
        try:
            db.query('SELECT 1')
            db_healthy = True
        except:
            pass
        
        # Check MQTT status
        mqtt_healthy = mqtt_service.connected
        
        return {
            'status': 'healthy' if (db_healthy and mqtt_healthy) else 'degraded',
            'timestamp': datetime.now().isoformat(),
            'services': {
                'database': 'connected' if db_healthy else 'disconnected',
                'mqtt': 'connected' if mqtt_healthy else 'disconnected'
            }
        }
    except Exception as e:
        logger.error(f'Health check error: {e}')
        return {
            'status': 'unhealthy',
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }

# Include routers
app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(telemetry.router)
app.include_router(access.router)
app.include_router(gateways.router)
app.include_router(sync.router)
app.include_router(commands.router)
app.include_router(dashboard.router)

logger.info('Sync routes loaded')

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f'Unhandled error: {exc}')
    return {
        'error': str(exc),
        'timestamp': datetime.now().isoformat()
    }

if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host='0.0.0.0',
        port=settings.API_PORT,
        reload=False
    )