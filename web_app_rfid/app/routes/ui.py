from flask import Blueprint, render_template, redirect

ui_bp = Blueprint("ui", __name__, url_prefix="")

@ui_bp.get("/ui")
def ui():
    return render_template("ui.html")

@ui_bp.get("/")
def index():
    return redirect("/ui", code=302)
