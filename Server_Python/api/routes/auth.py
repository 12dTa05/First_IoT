from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import bcrypt
import jwt
from datetime import datetime, timedelta
from config.settings import settings
from services.database import db
from middleware.auth import verify_token

router = APIRouter(prefix='/api/auth', tags=['auth'])

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = None

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post('/register')
async def register(req: RegisterRequest):
    try:
        result = db.query(
            'SELECT 1 FROM users WHERE username = %s OR email = %s',
            (req.username, req.email)
        )
        
        if result:
            raise HTTPException(status_code=409, detail='Username or email already exists')
        
        password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
        user_id = f'user_{int(datetime.now().timestamp() * 1000)}'
        
        result = db.query(
            """INSERT INTO users (user_id, username, email, password_hash, full_name)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING user_id, username, email, full_name, role, created_at""",
            (user_id, req.username, req.email, password_hash, req.full_name)
        )
        
        user = result[0]
        
        token = jwt.encode(
            {
                'user_id': user['user_id'],
                'username': user['username'],
                'role': user['role'],
                'exp': datetime.utcnow() + timedelta(days=settings.JWT_EXPIRATION_DAYS)
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM
        )
        
        return {
            'token': token,
            'user': {
                'user_id': user['user_id'],
                'username': user['username'],
                'email': user['email'],
                'full_name': user['full_name'],
                'role': user['role']
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/login')
async def login(req: LoginRequest):
    try:
        result = db.query(
            """SELECT user_id, username, email, password_hash, full_name, role, active
               FROM users WHERE username = %s""",
            (req.username,)
        )
        
        if not result:
            raise HTTPException(status_code=401, detail='Invalid username or password')
        
        user = result[0]
        
        if not user['active']:
            raise HTTPException(status_code=403, detail='Account is deactivated')
        
        if not bcrypt.checkpw(req.password.encode(), user['password_hash'].encode()):
            raise HTTPException(status_code=401, detail='Invalid username or password')
        
        token = jwt.encode(
            {
                'user_id': user['user_id'],
                'username': user['username'],
                'role': user['role'],
                'exp': datetime.utcnow() + timedelta(days=settings.JWT_EXPIRATION_DAYS)
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM
        )
        
        return {
            'token': token,
            'user': {
                'user_id': user['user_id'],
                'username': user['username'],
                'email': user['email'],
                'full_name': user['full_name'],
                'role': user['role']
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/me')
async def get_me(token_data: dict = Depends(verify_token)):
    try:
        result = db.query(
            """SELECT user_id, username, email, full_name, role, created_at
               FROM users WHERE user_id = %s AND active = TRUE""",
            (token_data['user_id'],)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='User not found')
        
        return result[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))