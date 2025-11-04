from fastapi import APIRouter, HTTPException, Header
from typing import Optional
from services.database import db
import json
import hashlib
from datetime import datetime

router = APIRouter(prefix='/api/sync', tags=['sync'])

def calculate_db_version(data):
    """Calculate version hash from database content"""
    json_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(json_str.encode()).hexdigest()[:16]

@router.get('/database/{gateway_id}')
async def get_database_for_gateway(
    gateway_id: str,
    current_version: Optional[str] = Header(None, alias='X-DB-Version')
):
    """
    Endpoint for gateway to sync database
    Returns full database if version mismatch or first sync
    """
    try:
        # Verify gateway exists
        gateway_result = db.query(
            'SELECT user_id FROM gateways WHERE gateway_id = %s',
            (gateway_id,)
        )
        
        if not gateway_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        user_id = gateway_result[0]['user_id']
        
        # Get passwords for this user (only active or all)
        passwords_result = db.query(
            '''SELECT password_id, hash, active, description, 
                      created_at, last_used, expires_at, updated_at
               FROM passwords 
               WHERE user_id = %s
               ORDER BY created_at DESC''',
            (user_id,)
        )
        
        # Get RFID cards for this user
        rfid_result = db.query(
            '''SELECT uid, active, card_type, description,
                      registered_at, last_used, expires_at, 
                      deactivated_at, deactivation_reason, updated_at
               FROM rfid_cards 
               WHERE user_id = %s
               ORDER BY registered_at DESC''',
            (user_id,)
        )
        
        # Get devices for this gateway
        devices_result = db.query(
            '''SELECT device_id, device_type, location, communication,
                      status, last_seen, created_at, updated_at
               FROM devices 
               WHERE gateway_id = %s
               ORDER BY created_at DESC''',
            (gateway_id,)
        )
        
        # Format data - convert to dict format that gateway expects
        database_content = {
            'passwords': {
                row['password_id']: {
                    'hash': row['hash'],
                    'active': row['active'],
                    'description': row['description'],
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                    'last_used': row['last_used'].isoformat() if row['last_used'] else None,
                    'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
                }
                for row in passwords_result
            },
            'rfid_cards': {
                row['uid']: {
                    'active': row['active'],
                    'card_type': row['card_type'],
                    'description': row['description'],
                    'registered_at': row['registered_at'].isoformat() if row['registered_at'] else None,
                    'last_used': row['last_used'].isoformat() if row['last_used'] else None,
                    'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                    'deactivated_at': row['deactivated_at'].isoformat() if row['deactivated_at'] else None,
                    'deactivation_reason': row['deactivation_reason'],
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
                }
                for row in rfid_result
            },
            'devices': {
                row['device_id']: {
                    'device_type': row['device_type'],
                    'location': row['location'],
                    'communication': row['communication'],
                    'status': row['status'],
                    'registered_at': row['created_at'].isoformat() if row['created_at'] else None,  # Map created_at to registered_at
                    'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
                    'metadata': None,  # Gateway doesn't use this field
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
                }
                for row in devices_result
            }
        }
        
        # Calculate version
        new_version = calculate_db_version(database_content)
        
        # Check if update needed
        needs_update = current_version != new_version
        
        response = {
            'gateway_id': gateway_id,
            'version': new_version,
            'timestamp': datetime.now().isoformat(),
            'needs_update': needs_update
        }
        
        if needs_update:
            response['database'] = database_content
            response['stats'] = {
                'passwords_count': len(database_content['passwords']),
                'rfid_cards_count': len(database_content['rfid_cards']),
                'devices_count': len(database_content['devices'])
            }
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Sync error: {str(e)}')

@router.post('/notify-change/{user_id}')
async def notify_database_change(user_id: str):
    """
    Internal endpoint to notify all gateways of a user when database changes
    This triggers immediate sync via MQTT
    """
    try:
        # Get all online gateways for this user
        gateways = db.query(
            'SELECT gateway_id FROM gateways WHERE user_id = %s AND status = %s',
            (user_id, 'online')
        )
        
        if not gateways:
            return {'message': 'No online gateways found', 'notified': 0}
        
        # Send MQTT notification to each gateway
        notified_count = 0
        for gateway in gateways:
            gateway_id = gateway['gateway_id']
            topic = f'gateway/{gateway_id}/sync/trigger'
            
            message = {
                'action': 'sync_database',
                'reason': 'database_updated',
                'timestamp': datetime.now().isoformat()
            }
            
            if mqtt_service and mqtt_service.publish(topic, message):
                notified_count += 1
        
        return {
            'message': 'Sync notifications sent',
            'notified': notified_count,
            'total_gateways': len(gateways)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/version/{gateway_id}')
async def get_database_version(gateway_id: str):
    """Quick endpoint to check current database version without downloading full data"""
    try:
        gateway_result = db.query(
            'SELECT user_id FROM gateways WHERE gateway_id = %s',
            (gateway_id,)
        )
        
        if not gateway_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        user_id = gateway_result[0]['user_id']
        
        # Get lightweight data for version calculation
        passwords = db.query(
            'SELECT password_id, updated_at FROM passwords WHERE user_id = %s',
            (user_id,)
        )
        rfid_cards = db.query(
            'SELECT uid, updated_at FROM rfid_cards WHERE user_id = %s',
            (user_id,)
        )
        devices = db.query(
            'SELECT device_id, updated_at FROM devices WHERE gateway_id = %s',
            (gateway_id,)
        )
        
        # Simple version based on counts and last update times
        version_data = {
            'passwords': [{'id': p['password_id'], 't': p['updated_at'].isoformat()} for p in passwords],
            'rfid_cards': [{'id': r['uid'], 't': r['updated_at'].isoformat()} for r in rfid_cards],
            'devices': [{'id': d['device_id'], 't': d['updated_at'].isoformat()} for d in devices]
        }
        
        version = calculate_db_version(version_data)
        
        return {
            'gateway_id': gateway_id,
            'version': version,
            'timestamp': datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/heartbeat/{gateway_id}')
async def gateway_heartbeat(gateway_id: str):
    """
    Gateway heartbeat endpoint
    Updates last_seen and status
    """
    try:
        # Update gateway heartbeat
        result = db.query(
            """UPDATE gateways 
               SET last_seen = NOW(),
                   status = 'online',
                   updated_at = NOW()
               WHERE gateway_id = %s
               RETURNING gateway_id, user_id, status""",
            (gateway_id,)
        )
        
        if not result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        gateway = result[0]
        
        return {
            'success': True,
            'gateway_id': gateway['gateway_id'],
            'status': gateway['status'],
            'timestamp': datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/status/{gateway_id}')
async def get_sync_status(gateway_id: str):
    """
    Get sync status for gateway
    Returns gateway info and database version
    """
    try:
        # Get gateway info
        gateway_result = db.query(
            """SELECT gateway_id, user_id, name, location, status, 
                      last_seen, database_version, updated_at
               FROM gateways 
               WHERE gateway_id = %s""",
            (gateway_id,)
        )
        
        if not gateway_result:
            raise HTTPException(status_code=404, detail='Gateway not found')
        
        gateway = gateway_result[0]
        user_id = gateway['user_id']
        
        # Count resources
        password_count = db.query_one(
            'SELECT COUNT(*) as count FROM passwords WHERE user_id = %s',
            (user_id,)
        )['count']
        
        rfid_count = db.query_one(
            'SELECT COUNT(*) as count FROM rfid_cards WHERE user_id = %s',
            (user_id,)
        )['count']
        
        device_count = db.query_one(
            'SELECT COUNT(*) as count FROM devices WHERE gateway_id = %s',
            (gateway_id,)
        )['count']
        
        return {
            'gateway_id': gateway['gateway_id'],
            'name': gateway['name'],
            'location': gateway['location'],
            'status': gateway['status'],
            'last_seen': gateway['last_seen'].isoformat() if gateway['last_seen'] else None,
            'database_version': gateway['database_version'],
            'resources': {
                'passwords': password_count,
                'rfid_cards': rfid_count,
                'devices': device_count
            },
            'last_updated': gateway['updated_at'].isoformat() if gateway['updated_at'] else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))