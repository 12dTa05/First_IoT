from fastapi import APIRouter, HTTPException, Response, Cookie, Depends
from pydantic import BaseModel
from typing import Optional
import logging
from services.api_client import api_client
from services.auth_service import auth_service
from config.settings import settings

router = APIRouter(prefix='/auth', tags=['auth'])
logger = logging.getLogger(__name__)

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None

async def get_current_session(session_id: Optional[str] = Cookie(None, alias=settings.SESSION_COOKIE_NAME)):
    if not session_id:
        raise HTTPException(status_code=401, detail='Not authenticated')
    
    session = auth_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail='Invalid or expired session')
    
    return session

@router.post('/login')
async def login(credentials: LoginRequest, response: Response):
    try:
        result = await api_client.post(
            '/api/auth/login',
            json_data={
                'username': credentials.username,
                'password': credentials.password
            }
        )
        
        if not result.get('token'):
            raise HTTPException(status_code=401, detail='Invalid credentials')
        
        session_id = auth_service.create_session(
            user_data=result.get('user', {}),
            token=result['token']
        )
        
        is_production = settings.ENVIRONMENT == 'production'

        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session_id,
            max_age=settings.SESSION_MAX_AGE,
            httponly=True,
            secure=is_production,  # Only secure in production
            samesite='none' if not is_production else 'lax',  # 'none' for cross-origin in dev
            domain=None  # Allow cookie on localhost
        )
        
        return {
            'success': True,
            'user': result.get('user')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail='Login failed')

@router.post('/register')
async def register(data: RegisterRequest, response: Response):
    try:
        result = await api_client.post(
            '/api/auth/register',
            json_data={
                'username': data.username,
                'email': data.email,
                'password': data.password,
                'full_name': data.full_name
            }
        )
        
        if not result.get('token'):
            raise HTTPException(status_code=400, detail='Registration failed')
        
        session_id = auth_service.create_session(
            user_data=result.get('user', {}),
            token=result['token']
        )
        
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session_id,
            max_age=settings.SESSION_MAX_AGE,
            httponly=True,
            secure=settings.ENVIRONMENT == 'production',
            samesite='lax'
        )
        
        return {
            'success': True,
            'user': result.get('user')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail='Registration failed')

@router.post('/logout')
async def logout(response: Response, session: dict = Depends(get_current_session)):
    session_id = None
    for sid, sess in auth_service.active_sessions.items():
        if sess.get('user_id') == session.get('user_id'):
            session_id = sid
            break
    
    if session_id:
        auth_service.destroy_session(session_id)
    
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME)
    
    return {'success': True, 'message': 'Logged out successfully'}

@router.get('/me')
async def get_current_user(session: dict = Depends(get_current_session)):
    token = session.get('token')
    
    result = await api_client.get('/api/auth/me', token=token)
    
    if not result.get('user_id'):
        raise HTTPException(status_code=401, detail='Invalid session')
    
    return result

@router.get('/session')
async def check_session(session: dict = Depends(get_current_session)):
    return {
        'authenticated': True,
        'user_id': session.get('user_id'),
        'username': session.get('username'),
        'role': session.get('role')
    }