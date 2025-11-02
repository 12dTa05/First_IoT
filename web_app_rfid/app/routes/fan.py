from flask import Blueprint, request, jsonify
from app.db_connect import get_db
import json, uuid

fan_bp = Blueprint("fan", __name__, url_prefix="/fan")

# 🔹 Lấy trạng thái quạt
@fan_bp.get("/<gateway_id>/<device_id>/state")
def fan_state(gateway_id, device_id):
    """Lấy trạng thái quạt theo command_logs mới nhất"""
    conn = get_db()
    cur = conn.cursor()
    try:
        # 1️⃣ Lấy bản ghi cuối cùng của set_fan đã hoàn thành
        cur.execute("""
            SELECT params->>'state' AS state, time
            FROM command_logs
            WHERE device_id=%s AND gateway_id=%s
              AND command_type='set_fan'
              AND status='completed'
            ORDER BY time DESC
            LIMIT 1;
        """, (device_id, gateway_id))
        cmd = cur.fetchone()

        # 2️⃣ Nếu chưa từng có log thì fallback về devices table
        if cmd and cmd["state"]:
            last_state = cmd["state"]
            last_time = cmd["time"]
        else:
            cur.execute("""
                SELECT COALESCE(status, 'off') AS status
                FROM devices
                WHERE device_id=%s AND gateway_id=%s;
            """, (device_id, gateway_id))
            dev = cur.fetchone()
            last_state = dev["status"] if dev else "off"
            last_time = None

        # 3️⃣ Lấy thêm metadata để trả về giao diện
        cur.execute("""
            SELECT location, device_type
            FROM devices
            WHERE device_id=%s AND gateway_id=%s;
        """, (device_id, gateway_id))
        info = cur.fetchone() or {}

        return jsonify({
            "ok": True,
            "device_id": device_id,
            "gateway_id": gateway_id,
            "status": last_state,
            "last_update": last_time,
            "location": info.get("location"),
            "device_type": info.get("device_type"),
            "source": "command_logs" if cmd else "devices",
        })
    except Exception as e:
        print("🔥 fan_state error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# 🔹 Bật / tắt quạt (RESTful)
@fan_bp.post("/<gateway_id>/<device_id>/toggle")
def toggle_fan(gateway_id, device_id):
    """Bật / tắt quạt + log command"""
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        if not device_id or not user_id:
            return jsonify({"ok": False, "error": "missing_fields"}), 400

        conn = get_db()
        cur = conn.cursor()

        # Lấy trạng thái hiện tại
        cur.execute("SELECT COALESCE(status, 'off') AS status FROM devices WHERE device_id=%s;", (device_id,))
        dev = cur.fetchone()
        if not dev:
            conn.close()
            return jsonify({"ok": False, "error": "device_not_found"}), 404

        new_state = "off" if dev["status"] == "on" else "on"
        cur.execute("UPDATE devices SET status=%s WHERE device_id=%s;", (new_state, device_id))

        # ✅ Ghi log command
        command_id = "cmd_" + uuid.uuid4().hex[:8]
        cur.execute("""
            INSERT INTO command_logs (
                time, command_id, source, device_id, gateway_id, user_id,
                command_type, status, params, result, completed_at, metadata
            ) VALUES (
                NOW(), %s, 'client', %s, %s, %s,
                'set_fan', 'completed', %s, %s, NOW(), %s
            );
        """, (
            command_id,
            device_id,
            gateway_id,
            user_id,
            json.dumps({"state": new_state}),
            json.dumps({"success": True}),
            json.dumps({"source_ip": request.remote_addr}),
        ))

        conn.commit()
        return jsonify({"ok": True, "state": new_state, "command_id": command_id})
    except Exception as e:
        print("🔥 fan_toggle error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()
