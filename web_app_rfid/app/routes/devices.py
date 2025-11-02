from flask import Blueprint, jsonify
from app.db_connect import get_db

devices_bp = Blueprint("devices", __name__, url_prefix="/devices")

@devices_bp.get("/for_user/<user_id>")
def get_devices_for_user(user_id):
    """Trả về danh sách thiết bị mà user có quyền dùng"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT device_id, gateway_id, device_type, location, status, last_seen, gateway_name
            FROM user_devices_view
            WHERE user_id = %s
        """, (user_id,))
        devices = cur.fetchall()
        cur.close()
        conn.close()

        if not devices:
            return jsonify({
                "ok": False,
                "message": "Người dùng này chưa được gán thiết bị nào."
            }), 200

        return jsonify({
            "ok": True,
            "devices": devices
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
