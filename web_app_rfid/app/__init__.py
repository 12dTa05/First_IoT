# app/__init__.py
from flask import Flask
from .routes.ui import ui_bp
from .routes.notify import notify_bp
from .routes.access import access_bp
from .routes.fan import fan_bp
# from .routes.overview import overview_bp
from .routes.rfid import rfid_bp
# from .routes.logs import logs_bp
from .routes.dashboard import dashboard_bp   # ✅ file mới bạn sẽ thêm
from .db_connect import get_db               # ✅ kết nối PostgreSQL
from .routes.devices import devices_bp     # ✅ thêm devices_bp

def create_app():
    app = Flask(__name__)
    app.secret_key = "e9e50b0a6d20a2a6b9f8a711ed2c21e1f37b6f118e6f2a598a012e3e2f24c7ab"

    # Không dùng file JSON nữa
    app.config["DB_CONN_FUNC"] = get_db

    # Đăng ký blueprint
    app.register_blueprint(rfid_bp)
    app.register_blueprint(access_bp)
    app.register_blueprint(fan_bp)
    # app.register_blueprint(overview_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(notify_bp)
    # app.register_blueprint(logs_bp)
    app.register_blueprint(dashboard_bp)  # ✅ thêm dashboard
    
    app.register_blueprint(devices_bp)

    return app
