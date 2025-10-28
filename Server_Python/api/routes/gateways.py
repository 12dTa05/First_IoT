from fastapi import APIRouter, HTTPException
from services.database import db

router = APIRouter(prefix='/api/gateways', tags=['gateways'])

@router.get('/')
async def get_gateways():
    try:
        result = db.query('SELECT * FROM gateways ORDER BY created_at DESC')
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{gateway_id}')
async def get_gateway(gateway_id: str):
    try:
        result = db.query(
            'SELECT * FROM gateways WHERE gateway_id = %s',
            (gateway_id,)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/{gateway_id}/devices')
async def get_gateway_devices(gateway_id: str):
    try:
        result = db.query(
            'SELECT * FROM devices WHERE gateway_id = %s',
            (gateway_id,)
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))