# app/utils/storage.py
from __future__ import annotations
import json
from pathlib import Path

# Thư mục data tương đối theo repo
BASE_DIR = Path(__file__).resolve().parents[1] / "data"
BASE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = BASE_DIR / "config.json"
LOGS_PATH   = BASE_DIR / "logs.json"

def _to_path(path) -> Path:
    return path if isinstance(path, Path) else Path(path)

def load_json(path, default=None):
    """
    Đọc JSON an toàn:
    - Chấp nhận str/Path.
    - Nếu file không tồn tại / rỗng / JSON hỏng -> trả default (hoặc {}).
    """
    p = _to_path(path)
    if not p.exists() or p.stat().st_size == 0:
        return default if default is not None else {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # JSON lỗi -> trả default để không crash
        return default if default is not None else {}

def save_json(path, data):
    """
    Ghi JSON an toàn:
    - Tạo thư mục cha nếu chưa có.
    - Ghi ra file .tmp rồi replace (atomic) để tránh hỏng file nếu mất điện.
    """
    p = _to_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
