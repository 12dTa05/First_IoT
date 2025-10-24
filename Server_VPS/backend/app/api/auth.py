from fastapi import APIRouter, Depends, HTTPException
from datetime import timedelta
from ..core.security import verify_password, create_access_token, get_password_hash
from ..models.schemas import LoginRequest, TokenResponse
from ..core.config import get_settings
import os

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

USERS = {
    os.getenv("ADMIN_USERNAME", "admin"): {
        "password": os.getenv("ADMIN_PASSWORD", "admin"),
        "role": "admin"
    }
}

@router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest):
    user = USERS.get(credentials.username)
    if not user or user["password"] != credentials.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    access_token = create_access_token(
        data={"sub": credentials.username, "role": user["role"]},
        expires_delta=timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    
    return {"access_token": access_token, "token_type": "bearer"}