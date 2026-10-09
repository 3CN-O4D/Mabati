"""Seed the site with 40 demo products and matching generated images.

The images are synthesized locally (roofing-sheet textures in a range of
finishes/colours) so the site can be tested without downloading media.

Usage:
    python seed.py
"""

import math
import os
import random
import sqlite3
from datetime import datetime, timezone

from PIL import Image

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
DB_PATH = os.path.join(BASE_DIR, "mabati.db")

PATTERN_W, PATTERN_H = 160, 110          # small canvas, upscaled for soft ribs
OUT_W, OUT_H = 1200, 800
N_PRODUCTS = 40

# finish name -> (rgb, glossy)
FINISHES = [
    ("Charcoal Grey Matte", (62, 62, 66), False),
    ("Charcoal Matte", (46, 46, 51), False),
    ("Charcoal Grey Glossy", (56, 56, 62), True),
    ("Brick Red Glossy", (146, 46, 40), True),
    ("Terra Cotta Matte", (154, 72, 42), False),
    ("Iron Grey", (78, 81, 85), False),
    ("Forest Green", (36, 88, 56), False),
    ("Stone Blue", (68, 98, 122), False),
    ("Silver Galvanised", (172, 176, 182), True),
    ("Copper", (154, 88, 50), True),
    ("Sapphire Blue", (32, 60, 112), False),
    ("Mahogany", (94, 46, 30), False),
    ("Wine Red Glossy", (100, 28, 38), True),
    ("Desert Sand", (198, 166, 124), False),
    ("Midnight Grey", (38, 41, 47), False),
    ("Pine Green", (30, 82, 60), False),
]

CATEGORIES = {
    "Box Profile": ("box", 12),
    "Tile Profile": ("tile", 10),
    "Corrugated": ("corrugated", 10),
    "Plain Sheets": ("plain", 5),
    "Accessories": ("plain", 3),
}

BRANDS = ["Resincot", "Imperial", "Comet", "Royal", "Sahara", "Coastline",
          "Magna", "Aristocrat", "DumaSmart", "Majestic"]

SUFFIX = {
    "Box Profile": "Box Profile Sheet",
    "Tile Profile": "Tile Profile Sheet",
    "Corrugated": "Corrugated Iron Sheet",
    "Plain Sheets": "Plain Iron Sheet",
    "Accessories": ["Half-Round Gutter", "Valley Capping", "Ridge Capper"],
}

DESCRIPTIONS = [
    "Anti-fade coating, 10-year warranty.",
    "High gloss polyester finish, weather resistant.",
    "Heavy gauge, suitable for residential and commercial roofs.",
    "UV-stabilised colour coating, easy to install.",
    "Corrosion resistant zinc-alum coating.",
    "Premium quality with a clean professional finish.",
    "Lightweight yet strong, ideal for modern homes.",
    "Noise dampening profile, excellent for sound insulation.",
]


def profile_shade(x, y, profile):
    """Return a shading multiplier for a pixel on the pattern canvas."""
    u = x / PATTERN_W * 32  # ~2 rib cycles across a panel cell
    if profile == "corrugated":
        return 0.78 + 0.22 * math.sin(u * 5.9 + 1.0)
    if profile == "box":
        phase = (x * 7.0) % 24.0
        if phase < 3.0:
            return 0.55 + 0.30 * (phase / 3.0)
        if phase > 21.0:
            return 1.05 - 0.25 * ((phase - 21.0) / 3.0)
        return 1.0 - 0.07 * math.sin(math.pi * phase / 24.0)
    if profile == "tile":
        return 0.84 + 0.16 * math.sin(x * 0.52)
    return 1.0


def build_pattern(profile, rgb, glossy):
    """Render a small textured canvas and return a scaled RGB image."""
    r, g, b = rgb
    rows = []
    for y in range(PATTERN_H):
        lighting = 1.0 - 0.18 * (y / PATTERN_H)  # top-lit sheet
        row = []
        for x in range(PATTERN_W):
            shade = profile_shade(x, y, profile) * lighting
            if glossy:
                shade = 0.5 + (shade - 0.5) * 1.45  # punchier contrast
            shade += random.uniform(-0.015, 0.015)   # fine grain
            row.append((
                max(0, min(255, int(r * shade))),
                max(0, min(255, int(g * shade))),
                max(0, min(255, int(b * shade))),
            ))
        rows.extend(row)
    img = Image.new("RGB", (PATTERN_W, PATTERN_H))
    img.putdata(rows)
    return img.resize((OUT_W, OUT_H), Image.LANCZOS)


def ensure_schema(conn):
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
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    for column, ddl in {
        "finish_color": "TEXT",
        "price_unit": "TEXT NOT NULL DEFAULT 'per meter'",
    }.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE products ADD COLUMN {column} {ddl}")


def seed():
    random.seed(42)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.execute("DELETE FROM products WHERE image_filename LIKE 'sample_%'")

    now = datetime.now(timezone.utc).isoformat()
    products = []
    idx = 0

    for category, (profile, count) in CATEGORIES.items():
        for i in range(count):
            idx += 1
            finish, rgb, glossy = FINISHES[(idx - 1) % len(FINISHES)]
            brand = BRANDS[(idx - 1) % len(BRANDS)]
            if category == "Accessories":
                suffix = SUFFIX[category][i % len(SUFFIX[category])]
            else:
                suffix = SUFFIX[category]
            gauge = ["G26", "G28", "G30", "G32"][(idx - 1) % 4]

            name = f"{brand} {finish} {suffix}"
            price = random.randrange(520, 1450, 10)
            sale_price = round(price * 0.85) if idx % 4 == 0 else None
            description = random.choice(DESCRIPTIONS)
            best_seller = 1 if idx % 6 == 0 else 0
            in_stock = 0 if idx in (17, 33) else 1

            filename = f"sample_{idx:02d}.jpg"
            build_pattern(profile, rgb, glossy).save(
                os.path.join(UPLOAD_DIR, filename), "JPEG", quality=82, optimize=True
            )

            products.append((
                name, category, gauge, finish, price, "per meter",
                sale_price, description, filename, best_seller, in_stock, now,
            ))
            print(f"[{idx:02d}/{N_PRODUCTS}] {name}  ->  {filename}")

    conn.executemany(
        """INSERT INTO products
           (name, category, gauge, finish_color, price, price_unit,
            sale_price, description, image_filename, is_best_seller, in_stock,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        products,
    )
    conn.commit()
    conn.close()
    print(f"\nDone: {len(products)} products seeded, images in {UPLOAD_DIR}")


if __name__ == "__main__":
    seed()