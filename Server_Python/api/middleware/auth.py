import jwt
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config.settings import settings
from services.database import db

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token')

def get_current_user(token_data: dict = Depends(verify_token)):
    return token_data

async def check_device_ownership(device_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get('user_id')
    
    result = db.query(
        'SELECT 1 FROM devices WHERE device_id = %s AND user_id = %s',
        (device_id, user_id)
    )
    
    if not result:
        raise HTTPException(status_code=403, detail='Access denied: You do not own this device')
    
    return True

async def check_gateway_ownership(gateway_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get('user_id')
    
    result = db.query(
        'SELECT 1 FROM gateways WHERE gateway_id = %s AND user_id = %s',
        (gateway_id, user_id)
    )
    
    if not result:
        raise HTTPException(status_code=403, detail='Access denied: You do not own this gateway')
    
    return True

def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return True