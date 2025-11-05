# app/routes/access.py
from flask import Blueprint, request, jsonify
from app.db_connect import get_db
from app.utils.sync_trigger import trigger_sync_safe
from ..utils.helpers import sha256_hex, now_iso
import bcrypt
access_bp = Blueprint("access", __name__, url_prefix="/access")
from app.models.access_logs import log_access_event

# ✅ Check passcode qua DB
from app.models.command_logs import log_command_event

@access_bp.post("/<gateway_id>/<device_id>/passcode")
def access_by_passcode(gateway_id, device_id):
    """
    Xác thực passkey và ghi log truy cập (đúng logic, không granted sai).
    Ghi cả access_logs và command_logs, luôn lưu đúng user_id đang đăng nhập.
    """
    from app.models.command_logs import log_command_event
    from app.models.access_logs import log_access_event

    data = request.get_json(silent=True) or {}
    passcode = (data.get("passcode") or "").strip()
    frontend_uid = data.get("user_id")  # 🧩 user đang đăng nhập gửi lên

    SALT = "passkey_01_salt_2025"

    if not passcode:
        return jsonify({"ok": False, "error": "missing_passcode"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()

        # 1️⃣ Lấy tất cả passkey đang hoạt động
        cur.execute("SELECT password_id, hash, user_id FROM passwords WHERE active=TRUE;")
        passwords = cur.fetchall()

        # 2️⃣ Lấy danh sách user có quyền điều khiển thiết bị này
        cur.execute("SELECT user_id FROM user_devices_view WHERE device_id=%s;", (device_id,))
        allowed_users = [r["user_id"] for r in cur.fetchall()]

        matched_pid = None
        matched_uid = None

        # 3️⃣ Kiểm tra passcode từng user được phép
        for row in passwords:
            uid = row["user_id"]
            if uid not in allowed_users:
                continue

            db_hash = row["hash"]
            if not db_hash:
                continue

            # 🔒 chuẩn hóa hash -> bytes
            if isinstance(db_hash, memoryview):
                db_hash = db_hash.tobytes()
            elif isinstance(db_hash, str):
                db_hash = db_hash.encode("utf-8")
            elif isinstance(db_hash, (bytearray, bytes)):
                db_hash = bytes(db_hash)
            else:
                print(f"[WARN] Unknown hash type: {type(db_hash)}")
                continue

            # ✅ So sánh passcode bằng bcrypt
            try:
                if sha256_hex(SALT + passcode) == db_hash.decode() if isinstance(db_hash, (bytes, bytearray, memoryview)) else db_hash:
                    matched_pid = row["password_id"]
                    matched_uid = uid
                    break
            except Exception as e:
                print("[bcrypt error]", e)
                continue

        # 4️⃣ Kết quả xác thực
        result = "granted" if matched_pid else "denied"
        deny_reason = None if matched_pid else "Wrong password"

        # 🔹 Luôn lấy user_id thực tế (frontend_uid ưu tiên hơn)
        user_id_for_log = matched_uid or frontend_uid or "unknown"

        # 5️⃣ Ghi vào access_logs
        log_access_event(
            conn,
            device_id=device_id,
            gateway_id=gateway_id,
            user_id=user_id_for_log,
            method="passkey",
            result=result,
            password_id=matched_pid,
            deny_reason=deny_reason,
            metadata={"location": "Front Door"},
        )

        # 6️⃣ Ghi vào command_logs
        log_command_event(
            conn=conn,
            command_type="remote_unlock",
            source="client",
            device_id=device_id,
            gateway_id=gateway_id,
            user_id=user_id_for_log,
            params={"attempts": 1},
            result={"success": result == "granted", "deny_reason": deny_reason},
            metadata={
                "source_ip": request.remote_addr,
                "method": "passkey",
                "location": "Front Door"
            }
        )

        conn.commit()
        conn.close()
        print(f"[PASSKEY CHECK] user={user_id_for_log} result={result}")

        # 7️⃣ Nếu xác thực thành công, gửi lệnh unlock về gateway qua FastAPI server
        unlock_success = False
        if result == "granted":
            try:
                import requests
                import jwt
                from datetime import datetime, timedelta
                from app.utils.sync_trigger import FASTAPI_SERVER_URL

                # Tạo JWT token để xác thực với FastAPI server
                JWT_SECRET = "ThaiVuongMinhThaoLinhTu@2003"
                JWT_ALGORITHM = "HS256"

                token_payload = {
                    'user_id': user_id_for_log,
                    'username': user_id_for_log,
                    'role': 'user',
                    'exp': datetime.utcnow() + timedelta(minutes=5)  # Token tạm thời 5 phút
                }

                access_token = jwt.encode(token_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

                # Gọi API unlock của FastAPI server với token
                unlock_url = f"{FASTAPI_SERVER_URL}/api/commands/{gateway_id}/{device_id}/unlock"
                unlock_response = requests.post(
                    unlock_url,
                    json={"duration": 5},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=3
                )

                if unlock_response.status_code == 200:
                    unlock_data = unlock_response.json()
                    unlock_success = unlock_data.get("success", False)
                    print(f"[UNLOCK] Sent unlock command: success={unlock_success}")
                else:
                    print(f"[UNLOCK] Failed to unlock: HTTP {unlock_response.status_code}")

            except Exception as unlock_err:
                print(f"[UNLOCK] Error sending unlock command: {unlock_err}")
                # Không raise exception - vẫn return granted vì passkey đã đúng

        return jsonify({
            "ok": True,
            "result": result,
            "deny_reason": deny_reason,
            "gateway_id": gateway_id,
            "device_id": device_id,
            "unlock_sent": unlock_success  # Thêm flag để frontend biết đã gửi lệnh unlock
        })

    except Exception as e:
        print("🔥 access_by_passcode error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ✅ List passkeys (tạm tắt add/edit/delete)
@access_bp.post("/manage_passkey")
def manage_passkey():
    data = request.get_json(silent=True) or {}
    action = data.get("action")

    conn = get_db()
    cur = conn.cursor()

    # 🟢 Danh sách passkey
    if action == "list":
        cur.execute("""
            SELECT password_id AS id, user_id AS owner, description, active, created_at, expires_at
            FROM passwords
            ORDER BY created_at DESC;
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify(ok=True, passwords=rows)

    # 🟢 Thêm passkey
    elif action == "add":
        owner = data.get("owner", "").strip()
        passcode = data.get("passcode", "").strip()
        desc = data.get("description", "").strip()
        active = bool(data.get("active", True))
        expires_at = data.get("expires_at") or None

        if not owner or not passcode:
            return jsonify(ok=False, error="Thiếu owner hoặc passkey"), 400

        hashed = sha256_hex(passcode)

        new_id = f"passwd_{owner}_{int(__import__('time').time())}"
        cur.execute("""
            INSERT INTO passwords (password_id, user_id, hash, active, description, created_at, updated_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s)
        """, (new_id, owner, hashed, active, desc, expires_at))
        conn.commit()

        cur.close(); conn.close()

        # 🔄 Trigger immediate sync cho gateway của user
        trigger_sync_safe(owner)

        return jsonify(ok=True, message="Đã thêm passkey mới")

    # 🟢 Sửa passkey
    elif action == "edit":
        pid = data.get("id")
        desc = data.get("description", "").strip()
        active = bool(data.get("active", True))
        expires_at = data.get("expires_at") or None

        if not pid:
            return jsonify(ok=False, error="Thiếu ID passkey"), 400

        # Lấy user_id để trigger sync
        cur.execute("SELECT user_id FROM passwords WHERE password_id=%s;", (pid,))
        row = cur.fetchone()
        user_id = row["user_id"] if row else None

        cur.execute("""
            UPDATE passwords
            SET description=%s, active=%s, expires_at=%s, updated_at=NOW()
            WHERE password_id=%s
        """, (desc, active, expires_at, pid))
        conn.commit()
        cur.close(); conn.close()

        # 🔄 Trigger immediate sync cho gateway của user
        if user_id:
            trigger_sync_safe(user_id)

        return jsonify(ok=True, message="Đã cập nhật passkey")

    # 🟢 Xoá passkey
    elif action == "delete":
        pid = data.get("id")
        if not pid:
            return jsonify(ok=False, error="Thiếu ID để xoá"), 400

        # Lấy user_id trước khi xóa để trigger sync
        cur.execute("SELECT user_id FROM passwords WHERE password_id=%s;", (pid,))
        row = cur.fetchone()
        user_id = row["user_id"] if row else None

        cur.execute("DELETE FROM passwords WHERE password_id = %s;", (pid,))
        conn.commit()
        cur.close(); conn.close()

        # 🔄 Trigger immediate sync cho gateway của user
        if user_id:
            trigger_sync_safe(user_id)

        return jsonify(ok=True, message=f"Đã xoá passkey {pid}")

    # ❌ Action không hợp lệ
    cur.close(); conn.close()
    return jsonify(ok=False, error="Hành động không hợp lệ"), 400



@access_bp.post("/login")
def login_user():
    """Đăng nhập với username/password từ PostgreSQL"""
    import re, bcrypt

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"ok": False, "error": "missing_credentials"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, username, password_hash, full_name, role, active
            FROM users WHERE username = %s
        """, (username,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"ok": False, "error": "invalid_user"}), 403

        # ⚙️ TRUY CẬP THEO TÊN CỘT, KHÔNG DÙNG tuple unpack
        user_id = row["user_id"]
        uname = row["username"]
        hash_pw = row["password_hash"]
        full_name = row["full_name"]
        role = row["role"]
        active = row["active"]

        if not active:
            return jsonify({"ok": False, "error": "inactive_user"}), 403

        if not hash_pw:
            return jsonify({"ok": False, "error": "no_password_hash"}), 500

        # Làm sạch toàn bộ ký tự ẩn / BOM / xuống dòng
        hash_pw = str(hash_pw)
        hash_pw = re.sub(r"[^\x20-\x7E]", "", hash_pw).strip()

        print("[DEBUG FINAL HASH]", repr(hash_pw), "LEN:", len(hash_pw))

        try:
            if bcrypt.checkpw(password.encode(), hash_pw.encode()):
                return jsonify({
                    "ok": True,
                    "user_id": user_id,
                    "username": uname,
                    "full_name": full_name,
                    "role": role,
                    "message": "Đăng nhập thành công!"
                }), 200
            else:
                return jsonify({"ok": False, "error": "invalid_password"}), 403

        except ValueError as e:
            print("[BCRYPT ERROR]", e)
            return jsonify({"ok": False, "error": "invalid_hash_format"}), 400

    except Exception as e:
        print("[ERROR LOGIN]", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@access_bp.get("/check_permission")
def check_permission():
    """Kiểm tra user có quyền điều khiển thiết bị hay không"""
    user_id = request.args.get("user_id")
    device_id = request.args.get("device_id")

    if not user_id or not device_id:
        return jsonify({
            "ok": False,
            "granted": False,
            "reason": "missing_fields",
            "user_id": user_id,
            "device_id": device_id
        }), 200

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT 1 FROM user_devices_view
            WHERE user_id = %s AND device_id = %s
        """, (user_id, device_id))
        granted = cur.fetchone() is not None
        cur.close(); conn.close()

        return jsonify({
            "ok": True,
            "granted": bool(granted),
            "user_id": user_id,
            "device_id": device_id
        }), 200

    except Exception as e:
        print("[check_permission error]", e)
        return jsonify({
            "ok": False,
            "granted": False,
            "reason": str(e)
        }), 500



@access_bp.get("/get_device")
def get_device_for_user():
    user_id = request.args.get("user_id")
    dtype = (request.args.get("device_type") or "").lower().strip()

    if not user_id or not dtype:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT device_id, gateway_id
        FROM user_devices_view
        WHERE user_id = %s AND LOWER(device_type) = %s
        LIMIT 1;
    """, (user_id, dtype))
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row:
        return jsonify({"ok": True, "found": False})

    return jsonify({
        "ok": True,
        "found": True,
        "device_id": row["device_id"],
        "gateway_id": row["gateway_id"]
    })
