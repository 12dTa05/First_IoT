# app/utils/helpers.py
from datetime import datetime, time
from zoneinfo import ZoneInfo
from .storage import save_json, LOGS_PATH
import threading, json, hashlib
import subprocess
print("[DEBUG HELPERS FILE] Đang chạy helpers từ:", __file__)

TZ = ZoneInfo("Asia/Bangkok")
file_lock = threading.Lock()

def now_iso(): 
    return datetime.now(TZ).isoformat(timespec="seconds")



def sha256_hex(s) -> str:
    """SHA256 chuẩn – nhận cả str hoặc bytes."""
    import hashlib
    if isinstance(s, bytes):
        b = s
    else:
        b = str(s).encode("utf-8")
    h = hashlib.sha256(b).hexdigest()
    return h

def parse_iso(s: str) -> datetime:
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
        return datetime.fromisoformat(s).astimezone(TZ)
    except Exception:
        return datetime.now(TZ)

def is_today(dt: datetime) -> bool:
    now = datetime.now(TZ)
    return dt.date() == now.date()

def append_log(entry):
    entry.setdefault("timestamp", now_iso())
    with file_lock:
        try:
            with open(LOGS_PATH, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
        logs.append(entry)
        save_json(LOGS_PATH, logs)

def within_time_range(local_dt, start_str, end_str):
    s_h, s_m = map(int, start_str.split(":"))
    e_h, e_m = map(int, end_str.split(":"))
    t = local_dt.time()
    start_t = time(s_h, s_m)
    end_t = time(e_h, e_m)
    return (start_t <= t < end_t) if start_t <= end_t else (t >= start_t or t < end_t)
