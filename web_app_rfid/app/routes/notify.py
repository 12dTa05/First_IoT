from flask import Blueprint, jsonify, request
from ..utils.helpers import parse_iso, is_today
from ..utils.storage import load_json, LOGS_PATH
from app.db_connect import get_db

notify_bp = Blueprint("notify", __name__, url_prefix="/notify")

# =============================
# ⚙️ 1️⃣ Lấy log điều khiển (Fan, Relay...) - giữ nguyên
# =============================
@notify_bp.get("/logs")
def all_logs():
    user_id = request.args.get("user_id")
    conn = get_db()
    cur = conn.cursor()

    if user_id:
        # 🔒 Lọc log theo quyền của user (dựa trên user_devices_view)
       cur.execute("""
            SELECT c.time, c.command_id, c.device_id, c.gateway_id, c.user_id,
                    c.command_type, c.status, c.params, c.result, c.metadata
                FROM command_logs AS c
                WHERE (c.device_id, c.gateway_id) IN (
                    SELECT device_id, gateway_id
                    FROM user_devices_view
                    WHERE user_id = %s
                )
                ORDER BY c.time DESC
                LIMIT 100;
            """, (user_id,))

    else:
        # 🟢 Nếu chưa truyền user_id (giữ tương thích với bản cũ)
        cur.execute("""
            SELECT time, command_id, device_id, gateway_id, user_id,
                   command_type, status, params, result, metadata
            FROM command_logs
            ORDER BY time DESC LIMIT 100;
        """)

    rows = cur.fetchall()
    conn.close()

    return jsonify({
        "ok": True,
        "logs": [dict(r) for r in rows]
    })


# =============================
# 🧩 2️⃣ Lịch sử vào/ra (RFID + Passkey)
# =============================
@notify_bp.get("/history")
def access_history():
    """
    Trả về lịch sử vào/ra cho đúng user đăng nhập
    (chỉ lấy các sự kiện RFID hoặc Passkey)
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT time, device_id, gateway_id, user_id, method, result,
               password_id, rfid_uid, deny_reason
        FROM access_logs
        WHERE device_id IN (
            SELECT device_id FROM user_devices_view WHERE user_id = %s
        )
        ORDER BY time DESC
        LIMIT 100;
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()

    return jsonify({
        "ok": True,
        "logs": [dict(r) for r in rows]
    })
