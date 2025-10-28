from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from datetime import datetime
import logging
import uvicorn

from config.settings import settings
from services.database import db
from services.mqtt_service import mqtt_service

from routes import auth, devices, telemetry, access, gateways, commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title='IoT API Server', version='2.0.0')

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)

@app.on_event('startup')
async def startup_event():
    try:
        db.connect()
        mqtt_service.connect()
        logger.info('API Server started successfully')
    except Exception as e:
        logger.error(f'Failed to start server: {e}')
        raise e

@app.on_event('shutdown')
async def shutdown_event():
    try:
        db.close()
        mqtt_service.disconnect()
        logger.info('API Server shut down successfully')
    except Exception as e:
        logger.error(f'Error during shutdown: {e}')

@app.get('/health')
@limiter.limit('100/15minutes')
async def health_check(request: Request):
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    }

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(telemetry.router)
app.include_router(access.router)
app.include_router(gateways.router)
app.include_router(commands.router)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f'Error: {exc}')
    return {
        'error': str(exc)
    }

if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host='0.0.0.0',
        port=settings.API_PORT,
        reload=False
    )