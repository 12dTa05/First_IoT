from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .api import devices, gateways, access, auth
from .services.mqtt import mqtt_service
from .core.database import get_db
from .core.redis import get_redis, close_redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await mqtt_service.connect()
    await get_redis()
    
    # Setup MQTT callbacks
    async def handle_telemetry(topic, payload):
        device_id = topic.split("/")[1]
        async for db in get_db():
            query = """
                INSERT INTO telemetry (time, device_id, gateway_id, temperature, humidity, data)
                VALUES (NOW(), $1, $2, $3, $4, $5)
            """
            await db.execute(
                query,
                device_id,
                payload.get("gateway_id"),
                payload.get("temperature"),
                payload.get("humidity"),
                payload
            )
            await db.commit()
    
    async def handle_status(topic, payload):
        device_id = topic.split("/")[1]
        async for db in get_db():
            query = """
                UPDATE devices SET status = $1, last_seen = NOW() 
                WHERE device_id = $2
            """
            await db.execute(query, payload.get("status"), device_id)
            await db.commit()
            
            query = """
                INSERT INTO device_status (time, device_id, gateway_id, status, sequence, metadata)
                VALUES (NOW(), $1, $2, $3, $4, $5)
            """
            await db.execute(
                query,
                device_id,
                payload.get("gateway_id"),
                payload.get("status"),
                payload.get("sequence"),
                payload
            )
            await db.commit()
    
    async def handle_access(topic, payload):
        device_id = topic.split("/")[1]
        async for db in get_db():
            query = """
                INSERT INTO access_logs (time, device_id, gateway_id, method, result, 
                                        password_id, rfid_uid, deny_reason, metadata)
                VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
            """
            await db.execute(
                query,
                device_id,
                payload.get("gateway_id"),
                payload.get("method"),
                payload.get("result"),
                payload.get("password_id"),
                payload.get("rfid_uid"),
                payload.get("deny_reason"),
                payload
            )
            await db.commit()
    
    mqtt_service.subscribe_callback("telemetry", handle_telemetry)
    mqtt_service.subscribe_callback("status", handle_status)
    mqtt_service.subscribe_callback("access", handle_access)
    
    yield
    
    # Shutdown
    await mqtt_service.disconnect()
    await close_redis()

app = FastAPI(
    title="IoT Gateway API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(gateways.router)
app.include_router(access.router)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}