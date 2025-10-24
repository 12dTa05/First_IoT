import redis.asyncio as redis
from .config import get_settings

settings = get_settings()
redis_client: redis.Redis = None

async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = await redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return redis_client

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()