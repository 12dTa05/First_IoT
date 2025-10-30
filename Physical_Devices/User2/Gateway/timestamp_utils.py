from datetime import datetime, timezone

def get_current_timestamp():
    return datetime.now(timezone.utc).isoformat()

def get_current_timestamp_compact():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def parse_timestamp(timestamp_str):
    try:
        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    except:
        return None

def timestamp_to_local(timestamp_utc, local_tz_offset=7):
    from datetime import timedelta
    return timestamp_utc + timedelta(hours=local_tz_offset)

# Quick access functions
now = get_current_timestamp
now_compact = get_current_timestamp_compact