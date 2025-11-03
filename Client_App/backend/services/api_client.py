import httpx
import logging
from typing import Dict, Any, Optional
from config.settings import settings

logger = logging.getLogger(__name__)

class APIClient:
    def __init__(self):
        self.base_url = settings.VPS_API_URL
        self.timeout = settings.API_TIMEOUT
        
    async def request(
        self,
        method: str,
        endpoint: str,
        token: Optional[str] = None,
        json_data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {}
        
        if token:
            headers['Authorization'] = f'Bearer {token}'
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                    headers=headers
                )
                
                if response.status_code >= 400:
                    logger.error(f"API error {response.status_code}: {response.text}")
                    return {
                        'success': False,
                        'error': response.text,
                        'status_code': response.status_code
                    }
                
                return response.json()
                
        except httpx.TimeoutException:
            logger.error(f"Request timeout for {endpoint}")
            return {'success': False, 'error': 'Request timeout'}
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {'success': False, 'error': str(e)}
    
    async def get(self, endpoint: str, token: Optional[str] = None, params: Optional[Dict] = None):
        return await self.request('GET', endpoint, token=token, params=params)
    
    async def post(self, endpoint: str, token: Optional[str] = None, json_data: Optional[Dict] = None):
        return await self.request('POST', endpoint, token=token, json_data=json_data)
    
    async def put(self, endpoint: str, token: Optional[str] = None, json_data: Optional[Dict] = None):
        return await self.request('PUT', endpoint, token=token, json_data=json_data)
    
    async def delete(self, endpoint: str, token: Optional[str] = None):
        return await self.request('DELETE', endpoint, token=token)

api_client = APIClient()