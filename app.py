"""
Mabati — self-hosted ironsheets catalog.

Flask + SQLite backend that serves a public catalog (with search, category
filters, flash sales and best sellers) and a password-protected admin
dashboard for managing inventory and image uploads.

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000  (admin at /admin)
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:  # pragma: no cover - Pillow is optional
    HAVE_PIL = False

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:  # pragma: no cover - dotenv is optional
    pass

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
DB_PATH = os.path.join(BASE_DIR, "mabati.db")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB uploads
IMAGE_MAX_DIMENSION = 1400

CATEGORIES = [
    "Box Profile",
    "Tile Profile",
    "Corrugated",
    "Plain Sheets",
    "Accessories",
]

# Business contact details (override with env vars on the VPS).
BUSINESS = {
    "name": os.environ.get("BUSINESS_NAME", "Mabati Ironsheets"),
    "phone": os.environ.get("BUSINESS_PHONE", "+254700000000"),
    "whatsapp": os.environ.get("BUSINESS_WHATSAPP", "254700000000"),
    "location": os.environ.get("BUSINESS_LOCATION", "Nairobi, Kenya"),
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "mabati-admin")

os.makedirs(UPLOAD_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables (idempotent) and seed a couple of demo rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            category      TEXT    NOT NULL,
            gauge         TEXT,
            finish_color  TEXT,
            price         REAL    NOT NULL DEFAULT 0,
            price_unit    TEXT    NOT NULL DEFAULT 'per meter',
            sale_price    REAL,
            description   TEXT,
            image_filename TEXT,
            is_best_seller INTEGER NOT NULL DEFAULT 0,
            in_stock      INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS leads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            phone        TEXT    NOT NULL,
            product_id   INTEGER,
            product_name TEXT,
            roof_size    TEXT,
            message      TEXT,
            handled      INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT    NOT NULL
        );
        """
    )
    _ensure_columns(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"] == 0:
        now = datetime.now(timezone.utc).isoformat()
        seed = [
            ("Resincot Charcoal Matte", "Box Profile", "G30",
             "Charcoal Grey Matte", 850, "per meter", None,
             "Anti-fade coating, 10-year warranty.", None, 1),
            ("Galvanised Corrugated Sheet", "Corrugated", "G28",
             "Silver", 720, "per meter", 640,
             "Heavy duty galvanised roofing sheet.", None, 1),
            ("Roman Tile Profile", "Tile Profile", "G32",
             "Brick Red Glossy", 980, "per meter", None,
             "Premium tiled look for modern homes.", None, 0),
        ]
        conn.executemany(
            """INSERT INTO products
               (name, category, gauge, finish_color, price, price_unit,
                sale_price, description, image_filename, is_best_seller,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [row + (now,) for row in seed],
        )
    conn.commit()
    conn.close()


def _ensure_columns(conn):
    """Add columns introduced after the first release (idempotent)."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    additions = {
        "finish_color": "TEXT",
        "price_unit": "TEXT NOT NULL DEFAULT 'per meter'",
    }
    for column, ddl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE products ADD COLUMN {column} {ddl}")


def row_to_dict(row):
    d = dict(row)
    if d.get("sale_price") is not None:
        d["discount_percent"] = round(
            (d["price"] - d["sale_price"]) / d["price"] * 100
        ) if d["price"] else 0
    d["image_url"] = (
        url_for("uploaded_file", filename=d["image_filename"])
        if d.get("image_filename")
        else None
    )
    return d


# --------------------------------------------------------------------------- #
# Auth helpers
# --------------------------------------------------------------------------- #
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"error": "authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def save_image(file_storage):
    """Validate, downscale and persist an uploaded image. Returns filename."""
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = os.path.join(UPLOAD_DIR, filename)

    if HAVE_PIL and ext != "gif":
        img = Image.open(file_storage.stream)
        img = img.convert("RGB")
        img.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION))
        img.save(dest, optimize=True, quality=85)
        return filename

    file_storage.save(dest)
    return filename


def delete_image(filename):
    if not filename:
        return
    path = os.path.join(UPLOAD_DIR, secure_filename(filename))
    if os.path.isfile(path):
        os.remove(path)


# --------------------------------------------------------------------------- #
# Public routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", business=BUSINESS, categories=CATEGORIES)


@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/products")
def api_products():
    search = (request.args.get("search") or "").strip()
    category = (request.args.get("category") or "").strip()
    flag = (request.args.get("filter") or "").strip()

    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if search:
        sql += " AND (name LIKE ? OR description LIKE ? OR category LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    if category and category.lower() != "all":
        sql += " AND category = ?"
        params.append(category)
    if flag == "best_seller":
        sql += " AND is_best_seller = 1"
    elif flag == "on_sale":
        sql += " AND sale_price IS NOT NULL AND sale_price > 0"
    elif flag == "in_stock":
        sql += " AND in_stock = 1"
    sql += " ORDER BY is_best_seller DESC, created_at DESC"

    rows = get_db().execute(sql, params).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/flash-sales")
def api_flash_sales():
    rows = get_db().execute(
        """SELECT * FROM products
           WHERE sale_price IS NOT NULL AND sale_price > 0
           ORDER BY (price - sale_price) / price DESC"""
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/best-sellers")
def api_best_sellers():
    rows = get_db().execute(
        "SELECT * FROM products WHERE is_best_seller = 1 ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/categories")
def api_categories():
    rows = get_db().execute(
        "SELECT DISTINCT category FROM products ORDER BY category"
    ).fetchall()
    return jsonify([r["category"] for r in rows])


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #
@app.route("/admin")
def admin():
    return render_template("admin.html", business=BUSINESS, categories=CATEGORIES)


@app.route("/api/admin/session")
def admin_session():
    return jsonify({"authenticated": bool(session.get("admin"))})


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    if data.get("password") == ADMIN_PASSWORD:
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "invalid password"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


def _payload_from_request():
    """Read product fields from JSON or multipart form."""
    if request.form:
        src = request.form
        as_json = False
    else:
        src = request.get_json(silent=True) or {}
        as_json = True

    def get(key):
        return src.get(key)

    def as_flag(key):
        val = get(key)
        if isinstance(val, bool):
            return 1 if val else 0
        return 1 if str(val).lower() in {"1", "true", "on", "yes"} else 0

    def as_float(key):
        val = get(key)
        if val in (None, ""):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    return {
        "name": (get("name") or "").strip(),
        "category": (get("category") or "").strip(),
        "gauge": (get("gauge") or "").strip(),
        "finish_color": (get("finish_color") or "").strip(),
        "price": as_float("price") or 0,
        "price_unit": (get("price_unit") or "per meter").strip() or "per meter",
        "sale_price": as_float("sale_price"),
        "description": (get("description") or "").strip(),
        "is_best_seller": as_flag("is_best_seller"),
        "in_stock": as_flag("in_stock") if get("in_stock") is not None else 1,
        "_is_json": as_json,
    }


@app.route("/api/admin/products", methods=["POST"])
@login_required
def create_product():
    p = _payload_from_request()
    if not p["name"] or not p["category"]:
        return jsonify({"error": "name and category are required"}), 400

    image_filename = None
    file = request.files.get("image")
    if file and file.filename:
        if not allowed_file(file.filename):
            return jsonify({"error": "unsupported image type"}), 400
        image_filename = save_image(file)

    db = get_db()
    cur = db.execute(
        """INSERT INTO products
           (name, category, gauge, finish_color, price, price_unit,
            sale_price, description, image_filename, is_best_seller, in_stock,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["name"], p["category"], p["gauge"], p["finish_color"], p["price"],
            p["price_unit"], p["sale_price"], p["description"], image_filename,
            p["is_best_seller"], p["in_stock"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/admin/products/<int:pid>", methods=["PUT", "POST"])
@login_required
def update_product(pid):
    db = get_db()
    existing = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if existing is None:
        return jsonify({"error": "product not found"}), 404

    p = _payload_from_request()
    if not p["name"] or not p["category"]:
        return jsonify({"error": "name and category are required"}), 400

    image_filename = existing["image_filename"]
    file = request.files.get("image")
    if file and file.filename:
        if not allowed_file(file.filename):
            return jsonify({"error": "unsupported image type"}), 400
        delete_image(image_filename)
        image_filename = save_image(file)

    db.execute(
        """UPDATE products SET
           name=?, category=?, gauge=?, finish_color=?, price=?, price_unit=?,
           sale_price=?, description=?, image_filename=?, is_best_seller=?,
           in_stock=?
           WHERE id=?""",
        (
            p["name"], p["category"], p["gauge"], p["finish_color"], p["price"],
            p["price_unit"], p["sale_price"], p["description"], image_filename,
            p["is_best_seller"], p["in_stock"], pid,
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    return jsonify(row_to_dict(row))


@app.route("/api/admin/products/<int:pid>", methods=["DELETE"])
@login_required
def delete_product(pid):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if row is None:
        return jsonify({"error": "product not found"}), 404
    delete_image(row["image_filename"])
    db.execute("DELETE FROM products WHERE id = ?", (pid,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Public inquiries
# --------------------------------------------------------------------------- #
@app.route("/api/inquiries", methods=["POST"])
def create_inquiry():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not name or not phone:
        return jsonify({"error": "name and phone are required"}), 400

    db = get_db()
    cur = db.execute(
        """INSERT INTO leads
           (name, phone, product_id, product_name, roof_size, message,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            name, phone,
            data.get("product_id") or None,
            (data.get("product_name") or "").strip(),
            (data.get("roof_size") or "").strip(),
            (data.get("message") or "").strip(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid}), 201


@app.route("/api/admin/inquiries")
@login_required
def list_inquiries():
    rows = get_db().execute(
        "SELECT * FROM leads ORDER BY handled ASC, created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/inquiries/<int:lid>", methods=["POST"])
@login_required
def toggle_inquiry(lid):
    data = request.get_json(silent=True) or {}
    handled = 1 if (data.get("handled") in (True, "1", "true", "on")) else 0
    db = get_db()
    db.execute("UPDATE leads SET handled = ? WHERE id = ?", (handled, lid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/inquiries/<int:lid>", methods=["DELETE"])
@login_required
def delete_inquiry(lid):
    db = get_db()
    db.execute("DELETE FROM leads WHERE id = ?", (lid,))
    db.commit()
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
