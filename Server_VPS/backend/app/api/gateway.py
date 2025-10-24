from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from ..core.database import get_db
from ..api.devices import verify_token
from ..models.schemas import GatewayResponse

router = APIRouter(prefix="/gateways", tags=["gateways"])

@router.get("/", response_model=List[GatewayResponse])
async def get_gateways(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = """
        SELECT g.gateway_id, g.name, g.status, COUNT(d.device_id) as device_count
        FROM gateways g
        LEFT JOIN devices d ON g.gateway_id = d.gateway_id
        GROUP BY g.gateway_id, g.name, g.status
    """
    result = await db.execute(query)
    
    gateways = []
    for row in result:
        gateways.append({
            "gateway_id": row[0],
            "name": row[1],
            "status": row[2],
            "device_count": row[3]
        })
    return gateways

@router.get("/{gateway_id}", response_model=GatewayResponse)
async def get_gateway(
    gateway_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    query = """
        SELECT g.gateway_id, g.name, g.status, COUNT(d.device_id) as device_count
        FROM gateways g
        LEFT JOIN devices d ON g.gateway_id = d.gateway_id
        WHERE g.gateway_id = $1
        GROUP BY g.gateway_id, g.name, g.status
    """
    result = await db.execute(query, gateway_id)
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Gateway not found")
    
    return {
        "gateway_id": row[0],
        "name": row[1],
        "status": row[2],
        "device_count": row[3]
    }