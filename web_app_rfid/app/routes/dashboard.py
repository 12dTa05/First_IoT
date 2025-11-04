from flask import Blueprint, jsonify, request
from app.db_connect import get_db
from ..utils.helpers import now_iso

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.get("/overview")
def overview_dashboard():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM devices;")
    devices = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM passwords;")
    users = cur.fetchone()["count"]

    cur.execute("SELECT COUNT(*) FROM access_logs;")
    logs = cur.fetchone()["count"]

    conn.close()
    return jsonify({
        "devices": devices,
        "users": users,
        "access_logs": logs
    })


@dashboard_bp.get("/temperature")
def temperature_chart():
    """
    Lấy dữ liệu nhiệt độ / độ ẩm cho user hiện tại
    + Trả về 30 bản ghi mới nhất để vẽ biểu đồ
    + Có kèm dữ liệu 'hôm nay' (nhiệt độ, độ ẩm, icon)
    """
    user_id = str(request.args.get("user_id", "")).strip()
    if not user_id:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400

    conn = get_db()
    cur = conn.cursor()

    # 1️⃣ Lấy device cảm biến của user
    cur.execute("""
    SELECT device_id
    FROM user_devices_view
    WHERE user_id = %s AND device_type ILIKE 'temperature%%'
    LIMIT 1;
""", (user_id,))

    dev_row = cur.fetchone()
    if not dev_row:
        conn.close()
        return jsonify({"ok": False, "error": "no_device", "msg": "User không có cảm biến nhiệt độ"}), 404

    device_id = dev_row["device_id"]

    # 2️⃣ Lấy 30 bản ghi mới nhất từ telemetry
    cur.execute("""
        SELECT time, temperature, humidity
        FROM telemetry
        WHERE device_id = %s
        ORDER BY time DESC
        LIMIT 30;
    """, (device_id,))
    rows = cur.fetchall()

    if not rows:
        conn.close()
        return jsonify({"ok": False, "error": "no_data"}), 404

    latest = rows[0]
    latest_temp = latest["temperature"]
    latest_hum = latest["humidity"]
    latest_time = latest["time"]

    # 3️⃣ Xác định icon phù hợp
    if latest_temp >= 33:
        icon = "🔥"
    elif latest_temp >= 28:
        icon = "☀️"
    elif latest_temp >= 24:
        icon = "🌤️"
    elif latest_temp >= 20:
        icon = "🌥️"
    else:
        icon = "❄️"

    conn.close()

    # 4️⃣ Trả về dữ liệu đầy đủ
    return jsonify({
        "ok": True,
        "device_id": device_id,
        "latest": {
            "temperature": latest_temp,
            "humidity": latest_hum,
            "time": latest_time.isoformat(),
            "icon": icon
        },
        "chart": [
            {"time": r["time"].isoformat(), "temp": r["temperature"], "hum": r["humidity"]}
            for r in rows[::-1]  # đảo lại để thời gian tăng dần
        ]
    })
