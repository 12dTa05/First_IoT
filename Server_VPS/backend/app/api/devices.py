from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from ..core.database import get_db
from ..core.security import decode_token
from ..models.schemas import DeviceResponse, CommandRequest
from ..services.mqtt import mqtt_service
import json

router = APIRouter(prefix="/devices", tags=["devices"])

async def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload

@router.get("/", response_model=List[DeviceResponse])
async def get_devices(
    gateway_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = "SELECT * FROM devices"
    if gateway_id:
        query += f" WHERE gateway_id = $1"
        result = await db.execute(query, gateway_id)
    else:
        result = await db.execute(query)
    
    devices = []
    for row in result:
        devices.append({
            "device_id": row[0],
            "gateway_id": row[1],
            "device_type": row[2],
            "location": row[3],
            "status": row[7],
            "last_seen": row[9]
        })
    return devices

@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = "SELECT * FROM devices WHERE device_id = $1"
    result = await db.execute(query, device_id)
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    
    return {
        "device_id": row[0],
        "gateway_id": row[1],
        "device_type": row[2],
        "location": row[3],
        "status": row[7],
        "last_seen": row[9]
    }

@router.post("/command")
async def send_command(
    cmd: CommandRequest,
    user: dict = Depends(verify_token)
):
    topic = f"device/{cmd.device_id}/command"
    payload = {
        "command": cmd.command,
        "params": cmd.params
    }
    mqtt_service.publish(topic, payload)
    return {"status": "sent", "device_id": cmd.device_id}

@router.get("/{device_id}/telemetry")
async def get_telemetry(
    device_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = """
        SELECT time, temperature, humidity, data 
        FROM telemetry 
        WHERE device_id = $1 
        ORDER BY time DESC 
        LIMIT $2
    """
    result = await db.execute(query, device_id, limit)
    
    data = []
    for row in result:
        data.append({
            "time": row[0].isoformat(),
            "temperature": row[1],
            "humidity": row[2],
            "data": row[3]
        })
    return data