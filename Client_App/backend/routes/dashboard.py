from fastapi import APIRouter, HTTPException, Depends
import logging
from services.api_client import api_client
from routes.auth import get_current_session

router = APIRouter(prefix='/dashboard', tags=['dashboard'])
logger = logging.getLogger(__name__)

@router.get('/overview')
async def get_dashboard_overview(session: dict = Depends(get_current_session)):
    token = session.get('token')
    result = await api_client.get('/api/dashboard/overview', token=token)
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch dashboard'))
    
    return result

@router.get('/activitiy')
async def get_recent_activities(
    hours: int = 24,
    session: dict = Depends(get_current_session)
):
    token = session.get('token')
    result = await api_client.get(
        '/api/dashboard/activity',
        token=token,
        params={'hours': hours}
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch activities'))
    
    return result

@router.get('/stats')
async def get_stats(session: dict = Depends(get_current_session)):
    token = session.get('token')
    result = await api_client.get('/api/dashboard/stats', token=token)
    
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', 'Failed to fetch stats'))
    
    return result