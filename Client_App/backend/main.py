from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import logging
from config.settings import settings
from routes import auth, devices, dashboard, websocket
from services.auth_service import auth_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting BFF server...")
    logger.info(f"VPS API URL: {settings.VPS_API_URL}")
    logger.info(f"Frontend URL: {settings.FRONTEND_URL}")
    yield
    logger.info("Shutting down BFF server...")
    auth_service.cleanup_expired_sessions()

app = FastAPI(
    title="IoT Client BFF",
    description="Backend For Frontend - Proxy layer for IoT Web Client",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://orange-doodle-5wx965q4p56cpp6j-3001.app.github.dev'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(dashboard.router)
app.include_router(websocket.router)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "BFF",
        "vps_api": settings.VPS_API_URL
    }

@app.get("/")
async def root():
    return {
        "message": "IoT Client BFF API",
        "docs": "/docs"
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.BFF_HOST,
        port=settings.BFF_PORT,
        reload=settings.DEBUG,
        log_level="info"
    )
