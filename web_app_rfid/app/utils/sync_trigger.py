"""
Sync Trigger Utility
Helper để trigger database sync ngay lập tức cho gateway khi có thay đổi
"""
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# FastAPI Server URL - có thể config qua environment variable
FASTAPI_SERVER_URL = "http://159.223.63.61:3000"


def trigger_gateway_sync(user_id: str, timeout: int = 2) -> bool:
    """
    Trigger immediate database sync for all gateways của user

    Args:
        user_id: User ID cần trigger sync
        timeout: Request timeout (seconds)

    Returns:
        bool: True nếu trigger thành công, False nếu thất bại
    """
    if not user_id:
        logger.warning("trigger_gateway_sync called with empty user_id")
        return False

    try:
        url = f"{FASTAPI_SERVER_URL}/api/sync/notify-change/{user_id}"

        # Call API endpoint để trigger sync
        response = requests.post(url, timeout=timeout)

        if response.status_code == 200:
            data = response.json()
            notified = data.get('notified', 0)
            logger.info(f"✅ Triggered sync for user {user_id}: {notified} gateways notified")
            return True
        else:
            logger.warning(f"⚠️ Sync trigger failed: HTTP {response.status_code}")
            return False

    except requests.exceptions.Timeout:
        logger.warning(f"⚠️ Sync trigger timeout for user {user_id}")
        return False
    except requests.exceptions.ConnectionError:
        logger.warning(f"⚠️ Cannot connect to FastAPI server at {FASTAPI_SERVER_URL}")
        return False
    except Exception as e:
        logger.error(f"❌ Sync trigger error for user {user_id}: {e}")
        return False


def trigger_sync_safe(user_id: str) -> None:
    """
    Safe wrapper - không raise exception, chỉ log warning nếu fail
    Dùng cho các trường hợp không quan trọng (gateway sẽ poll sau 5-10s)

    Args:
        user_id: User ID cần trigger sync
    """
    try:
        trigger_gateway_sync(user_id, timeout=1)
    except Exception as e:
        logger.warning(f"Sync trigger failed (non-critical): {e}")
        pass
