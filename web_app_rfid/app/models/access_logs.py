import json

def log_access_event(conn, device_id, gateway_id, user_id, method,
                     result, password_id=None, rfid_uid=None,
                     deny_reason=None, metadata=None):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO access_logs (
            time, device_id, gateway_id, user_id,
            method, result, password_id, rfid_uid,
            deny_reason, metadata
        )
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        device_id, gateway_id, user_id,
        method, result, password_id, rfid_uid,
        deny_reason, json.dumps(metadata or {})
    ))
    cur.close()
