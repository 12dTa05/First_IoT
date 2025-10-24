from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert
from datetime import datetime
from typing import List, Optional
import asyncpg

class DeviceService:
    @staticmethod
    async def get_devices(db: AsyncSession, gateway_id: Optional[str] = None):
        query = "SELECT * FROM devices"
        if gateway_id:
            query += f" WHERE gateway_id = '{gateway_id}'"
        result = await db.execute(query)
        return result.fetchall()

    @staticmethod
    async def get_device(db: AsyncSession, device_id: str):
        query = f"SELECT * FROM devices WHERE device_id = '{device_id}'"
        result = await db.execute(query)
        return result.fetchone()

    @staticmethod
    async def update_device_status(db: AsyncSession, device_id: str, status: str):
        query = f"""
            UPDATE devices 
            SET status = '{status}', last_seen = NOW() 
            WHERE device_id = '{device_id}'
        """
        await db.execute(query)
        await db.commit()

    @staticmethod
    async def insert_telemetry(db: AsyncSession, device_id: str, gateway_id: str, data: dict):
        query = """
            INSERT INTO telemetry (time, device_id, gateway_id, temperature, humidity, data)
            VALUES (NOW(), :device_id, :gateway_id, :temperature, :humidity, :data)
        """
        await db.execute(
            query,
            {
                "device_id": device_id,
                "gateway_id": gateway_id,
                "temperature": data.get("temperature"),
                "humidity": data.get("humidity"),
                "data": json.dumps(data),
            }
        )
        await db.commit()

    @staticmethod
    async def insert_access_log(db: AsyncSession, device_id: str, gateway_id: str, 
                                method: str, result: str, **kwargs):
        query = """
            INSERT INTO access_logs (time, device_id, gateway_id, method, result, 
                                    password_id, rfid_uid, deny_reason, metadata)
            VALUES (NOW(), :device_id, :gateway_id, :method, :result, 
                   :password_id, :rfid_uid, :deny_reason, :metadata)
        """
        await db.execute(
            query,
            {
                "device_id": device_id,
                "gateway_id": gateway_id,
                "method": method,
                "result": result,
                "password_id": kwargs.get("password_id"),
                "rfid_uid": kwargs.get("rfid_uid"),
                "deny_reason": kwargs.get("deny_reason"),
                "metadata": json.dumps(kwargs.get("metadata", {})),
            }
        )
        await db.commit()