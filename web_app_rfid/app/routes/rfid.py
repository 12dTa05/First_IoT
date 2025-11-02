from flask import Blueprint, request, jsonify
from app.db_connect import get_db
import json

rfid_bp = Blueprint("rfid", __name__, url_prefix="/rfid")


# ===========================================================
# ✅ Quẹt thẻ RFID (kiểm tra quyền truy cập)
# ===========================================================
@rfid_bp.post("/cards")
def add_rfid_card():
    try:
        data = request.get_json(silent=True) or {}
        uid = (data.get("uid") or "").strip().upper()
        user_id = (data.get("user_id") or "").strip()
        card_type = (data.get("card_type") or "MIFARE Classic").strip()
        description = (data.get("description") or "").strip() or None
        expires_at = data.get("expires_at") or None
        active = bool(data.get("active", True))

        if not uid or not user_id:
            return jsonify({"ok": False, "error": "missing_fields"}), 400

        conn = get_db()
        cur = conn.cursor()

        # ✅ 1. Kiểm tra UID đã tồn tại chưa
        cur.execute("SELECT 1 FROM rfid_cards WHERE uid = %s;", (uid,))
        if cur.fetchone():
            conn.close()
            return jsonify({
                "ok": False,
                "error": f"UID {uid} đã tồn tại — không thể thêm thẻ trùng"
            }), 400

        # ✅ 2. Kiểm tra user_id có hợp lệ không
        cur.execute("SELECT 1 FROM users WHERE user_id = %s;", (user_id,))
        if not cur.fetchone():
            conn.close()
            return jsonify({
                "ok": False,
                "error": f"user_id {user_id} không tồn tại trong hệ thống"
            }), 400

        # ✅ 3. Thực hiện thêm
        cur.execute("""
            INSERT INTO rfid_cards
            (uid, user_id, active, card_type, description, registered_at, updated_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s)
        """, (uid, user_id, active, card_type, description, expires_at))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"ok": True, "msg": "Thêm thẻ mới thành công"})

    except Exception as e:
        print("❌ RFID ADD ERROR:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ===========================================================
# ✅ API: Danh sách thẻ RFID
# ===========================================================
@rfid_bp.get("/cards")
def get_cards():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rfid_cards ORDER BY registered_at DESC;")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "cards": rows})




@rfid_bp.put("/cards/<uid>")
def update_rfid_card(uid):
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "").strip()
    card_type = data.get("card_type", "").strip()
    description = data.get("description", "").strip()
    expires_at = data.get("expires_at")
    active = data.get("active", True)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE rfid_cards
        SET user_id=%s, card_type=%s, description=%s,
            expires_at=%s, active=%s, updated_at=NOW()
        WHERE uid=%s;
    """, (user_id, card_type, description, expires_at, active, uid))

    conn.commit()
    conn.close()

    return jsonify({"ok": True, "msg": "RFID card updated successfully"})



@rfid_bp.delete("/cards/<uid>")
def delete_rfid_card(uid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rfid_cards WHERE uid=%s;", (uid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": "RFID card deleted"})


latest_uid = None

@rfid_bp.post("/scan")
def receive_scan():
    """Nhận UID từ gateway RFID"""
    global latest_uid
    data = request.get_json(silent=True) or {}
    latest_uid = data.get("uid", "").strip().upper()
    return jsonify({"ok": True})

@rfid_bp.get("/latest")
def get_latest_uid():
    global latest_uid
    if latest_uid:
        uid = latest_uid
        latest_uid = None  # reset sau khi đọc
        return jsonify({"ok": True, "uid": uid})
    return jsonify({"ok": True, "uid": None})

