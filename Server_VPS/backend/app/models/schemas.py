from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class DeviceResponse(BaseModel):
    device_id: str
    gateway_id: str
    device_type: str
    location: Optional[str]
    status: str
    last_seen: Optional[datetime]

class TelemetryData(BaseModel):
    temperature: Optional[float]
    humidity: Optional[float]
    data: Optional[Dict[str, Any]]

class CommandRequest(BaseModel):
    device_id: str
    command: str
    params: Optional[Dict[str, Any]] = {}

class AccessLogResponse(BaseModel):
    time: datetime
    device_id: str
    method: str
    result: str
    owner: Optional[str]

class GatewayResponse(BaseModel):
    gateway_id: str
    name: str
    status: str
    device_count: int

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"