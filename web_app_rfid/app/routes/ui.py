#ui.py
from flask import Blueprint, render_template, redirect,Response
import os
from dotenv import load_dotenv

ui_bp = Blueprint("ui", __name__, url_prefix="")

@ui_bp.get("/ui")
def ui():
    return render_template("ui.html")

@ui_bp.get("/")
def index():
    return redirect("/ui", code=302)

load_dotenv()
print("📡 Loaded API_URL =", os.getenv("API_URL"))
@ui_bp.get("/config.js")
def config_js():
    api_url = os.getenv("API_URL", "http://127.0.0.1:8090")
    js_content = f"window.API_URL = '{api_url}';"
    return Response(js_content, mimetype="application/javascript")