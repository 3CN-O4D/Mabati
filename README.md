# Mabati

Self-hosted ironsheets catalog & inventory site. Flask + SQLite backend,
Tailwind frontend, served by Nginx/Gunicorn on a VPS.

## Features

- Public catalog with live search and category filters (mobile + desktop)
- **Dark mode** toggle, persisted and matched to system preference
- **Flash sales** (sale price + discount badge + countdown)
- **Best sellers** highlight section
- Product cards show profile type, gauge, **finish/color**, and **price unit** (per meter/sheet)
- **Click-to-zoom image lightbox**
- Fixed **Call Now / WhatsApp** bar on mobile, floating WhatsApp on desktop
- **Quick quote form** (name, phone, roof size) — leads stored in SQLite and viewable in admin
- Password-protected admin dashboard (`/admin`) for products + inquiries
- Images stored on disk in `static/uploads/` and served directly by Nginx

## Demo data

Generate 40 sample products with locally-rendered sheet images in a range of
finishes/colours (no downloads needed):

```bash
python seed.py
```

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY and ADMIN_PASSWORD
python app.py                 # http://127.0.0.1:5000  (admin at /admin)
```

## Project layout

```text
app.py                     Flask app: API, admin auth, uploads
requirements.txt           Python dependencies
mabati.db                  SQLite database (auto-created)
static/uploads/            Uploaded product images
templates/index.html       Public catalog page
templates/admin.html       Admin dashboard
deploy/nginx.conf          Nginx reverse-proxy config
deploy/mabati.service      systemd unit for gunicorn
```

## API

| Method | Route | Auth | Description |
| ------ | ----- | ---- | ----------- |
| GET | `/api/products` | – | List/search products (`search`, `category`, `filter`) |
| GET | `/api/flash-sales` | – | Products with an active sale price |
| GET | `/api/best-sellers` | – | Products flagged as best sellers |
| POST | `/api/admin/login` | – | Start an admin session |
| POST | `/api/inquiries` | – | Public quote request (creates a lead) |
| GET | `/api/admin/inquiries` | yes | List inquiries |
| POST | `/api/admin/inquiries/<id>` | yes | Mark inquiry handled/unhandled |
| DELETE | `/api/admin/inquiries/<id>` | yes | Delete inquiry |
| POST | `/api/admin/products` | yes | Create product (multipart, optional `image`) |
| POST/PUT | `/api/admin/products/<id>` | yes | Update product |
| DELETE | `/api/admin/products/<id>` | yes | Delete product |

## VPS deployment (Ubuntu/Debian)

```bash
sudo apt install -y python3-venv nginx
sudo mkdir -p /var/www/mabati-site && cd /var/www/mabati-site
# copy project files here, then:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # edit it
sudo cp deploy/mabati.service /etc/systemd/system/
sudo cp deploy/nginx.conf /etc/nginx/sites-available/mabati
sudo ln -s /etc/nginx/sites-available/mabati /etc/nginx/sites-enabled/
sudo systemctl daemon-reload
sudo systemctl enable --now mabati
sudo nginx -t && sudo systemctl reload nginx
```

Then point your domain's DNS to the VPS (via Cloudflare for free HTTPS + caching).
