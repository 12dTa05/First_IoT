# app/models/command_logs.py
from datetime import datetime
from psycopg2.extras import Json
import json
def log_command_event(conn, command_type, source, device_id, gateway_id,
                      user_id, params, result, metadata):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO command_logs (
            time, command_id, source, device_id, gateway_id, user_id,
            command_type, status, params, result, completed_at, metadata
        )
        VALUES (
            NOW(), concat('cmd_', substr(md5(random()::text), 1, 8)),
            %s, %s, %s, %s,
            %s, 'completed', %s, %s, NOW(), %s
        )
    """, (
        source, device_id, gateway_id, user_id,
        command_type, json.dumps(params or {}),
        json.dumps(result or {}), json.dumps(metadata or {})
    ))
    cur.close()
