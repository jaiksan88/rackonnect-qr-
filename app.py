from flask import (Flask, request, redirect, session, url_for,
                   render_template_string, send_file, jsonify)
import sqlite3
import qrcode
import io
import csv
import os
import json
import urllib.request
from datetime import datetime, timezone
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-launch")

# ---------------- SETTINGS ----------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "12345")   # launch se pehle badlo
WEBSITE_URL = "https://rackonnect.com/"
DATABASE = os.environ.get("DATABASE", "scans.db")
# QR ke andar jo address jayega. Hosting ke baad apna public link daalo.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")

VENUES = {
    "mehrauli": "Mehrauli",
    "reppp": "REPPP Mehrauli",
    "sec21": "RKT Arena Sector 21",
    "sec72": "RKT Arena Sector 72",
    "unwind": "RKT Unwind Badminton Arena",
}


# ---------------- DATABASE ----------------
def connect_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db


def setup_database():
    db = connect_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            device TEXT,
            browser TEXT
        )
    """)
    # naye columns (purana data safe rahega)
    existing = [r["name"] for r in db.execute("PRAGMA table_info(scans)")]
    for col, kind in [("ip", "TEXT"), ("city", "TEXT"), ("region", "TEXT"),
                      ("country", "TEXT"), ("lat", "REAL"), ("lon", "REAL"),
                      ("accuracy", "REAL")]:
        if col not in existing:
            db.execute(f"ALTER TABLE scans ADD COLUMN {col} {kind}")
    db.commit()
    db.close()


# ---------------- HELPERS ----------------
def get_device(ua):
    ua = ua.lower()
    if "iphone" in ua or "ipad" in ua:
        return "iPhone / iPad"
    if "android" in ua:
        return "Android"
    if "windows" in ua:
        return "Windows"
    if "macintosh" in ua:
        return "Mac"
    return "Other"


def get_browser(ua):
    ua = ua.lower()
    if "edg/" in ua:
        return "Edge"
    if "chrome/" in ua:
        return "Chrome"
    if "firefox/" in ua:
        return "Firefox"
    if "safari/" in ua:
        return "Safari"
    return "Other"


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def lookup_city(ip):
    """IP se city/country (free service, key nahi chahiye)."""
    if not ip or ip.startswith(("127.", "192.168.", "10.", "172.")) or ip == "::1":
        return None, None, None
    try:
        with urllib.request.urlopen(f"https://ipwho.is/{ip}", timeout=2) as r:
            data = json.loads(r.read().decode())
        if data.get("success"):
            return data.get("city"), data.get("region"), data.get("country")
    except Exception:
        pass
    return None, None, None


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


# ---------------- SCAN (public) ----------------
SCAN_PAGE = """
<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rackonnect</title>
<style>
body{font-family:Arial;background:#102b2b;color:#fff;display:flex;
justify-content:center;align-items:center;min-height:100vh;margin:0;text-align:center}
.box{padding:24px;max-width:320px}
a{color:#7be0b5}
</style></head>
<body><div class="box">
<h2>Rackonnect</h2>
<p>Opening website...<br>Please tap <b>Allow</b> for location (optional).</p>
<p><a href="{{site}}">Skip / Continue</a></p>
</div>
<script>
var SITE = {{site|tojson}};
var ID = {{scan_id}};
var gone = false;
function go(){ if(gone) return; gone = true; window.location.href = SITE; }
function send(p){
  fetch("/geo/" + ID, {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({lat:p.coords.latitude, lon:p.coords.longitude,
                          acc:p.coords.accuracy})
  }).then(go).catch(go);
}
setTimeout(go, 9000);
if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition(send, go,
    {timeout: 7000, enableHighAccuracy: true});
} else { go(); }
</script></body></html>
"""


@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/scan/<slug>")
def scan(slug):
    if slug not in VENUES:
        return "Venue not found", 404

    ua = request.headers.get("User-Agent", "")
    ip = client_ip()
    city, region, country = lookup_city(ip)

    db = connect_db()
    cur = db.execute("""
        INSERT INTO scans (venue, scanned_at, device, browser, ip, city, region, country)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (VENUES[slug], datetime.now(timezone.utc).isoformat(),
          get_device(ua), get_browser(ua), ip, city, region, country))
    scan_id = cur.lastrowid
    db.commit()
    db.close()

    return render_template_string(SCAN_PAGE, site=WEBSITE_URL, scan_id=scan_id)


@app.route("/geo/<int:scan_id>", methods=["POST"])
def save_geo(scan_id):
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
        acc = float(data.get("acc") or 0)
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify(ok=False), 400

    db = connect_db()
    db.execute("UPDATE scans SET lat=?, lon=?, accuracy=? WHERE id=?",
               (lat, lon, acc, scan_id))
    db.commit()
    db.close()
    return jsonify(ok=True)


# ---------------- ADMIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        error = "Incorrect password. Please try again."

    return render_template_string("""
    <html><head><title>Rackonnect Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
    body{font-family:Arial;background:#eef2f4;display:flex;
    justify-content:center;align-items:center;min-height:90vh}
    .login{background:white;padding:35px;border-radius:14px;width:320px;text-align:center}
    input,button{padding:12px;width:90%;margin:10px 0}
    button{background:#087b54;color:white;border:0;border-radius:6px;cursor:pointer}
    .error{color:red}
    </style></head>
    <body><div class="login">
    <h2>Rackonnect QR Analytics</h2><p>Admin Login</p>
    <p class="error">{{error}}</p>
    <form method="POST">
    <input type="password" name="password" placeholder="Enter admin password" required>
    <button type="submit">Login</button>
    </form></div></body></html>
    """, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@login_required
def dashboard():
    db = connect_db()
    total = db.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    gps_count = db.execute(
        "SELECT COUNT(*) FROM scans WHERE lat IS NOT NULL").fetchone()[0]
    venues = db.execute("""SELECT venue, COUNT(*) AS total FROM scans
                           GROUP BY venue ORDER BY total DESC""").fetchall()
    devices = db.execute("""SELECT device, COUNT(*) AS total FROM scans
                            GROUP BY device ORDER BY total DESC""").fetchall()
    cities = db.execute("""SELECT COALESCE(city,'Unknown') AS city,
                           COALESCE(country,'') AS country, COUNT(*) AS total
                           FROM scans GROUP BY city, country
                           ORDER BY total DESC LIMIT 20""").fetchall()
    recent = db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 100").fetchall()
    points = [
        {"lat": r["lat"], "lon": r["lon"], "venue": r["venue"],
         "time": r["scanned_at"][:16].replace("T", " ")}
        for r in db.execute(
            "SELECT * FROM scans WHERE lat IS NOT NULL ORDER BY id DESC LIMIT 500")
    ]
    db.close()

    return render_template_string("""
    <!DOCTYPE html><html><head>
    <title>Rackonnect QR Analytics</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <style>
    body{margin:0;background:#f1f4f5;font-family:Arial;color:#18252d}
    header{background:#102b2b;color:white;padding:22px}
    main{padding:22px;max-width:1100px;margin:auto}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:15px}
    .card{background:white;padding:20px;border-radius:12px;margin-bottom:18px}
    .number{font-size:32px;font-weight:bold;color:#087b54}
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left;font-size:14px}
    th{background:#edf3f0}
    a{color:#087b54}
    .btn{display:inline-block;background:#087b54;color:white;padding:10px 14px;
    border-radius:6px;text-decoration:none;margin:4px}
    .table-wrap{overflow-x:auto}
    #map{height:380px;border-radius:10px}
    </style></head>
    <body>
    <header><h1>Rackonnect QR Analytics</h1><p>Venue Scan Management Dashboard</p></header>
    <main>
    <p><a class="btn" href="/logout">Logout</a>
    <a class="btn" href="/export">Download CSV Report</a></p>

    <div class="cards">
      <div class="card"><p>Total QR Scans</p><div class="number">{{total}}</div></div>
      <div class="card"><p>Venues With Scans</p><div class="number">{{venues|length}}</div></div>
      <div class="card"><p>Exact Location Shared</p><div class="number">{{gps_count}}</div></div>
    </div>

    <div class="card"><h2>Scan Map (exact location)</h2>
      <div id="map"></div>
      {% if not points %}<p>Abhi koi exact location nahi aayi. Jab koi "Allow" dabayega, yahan dikhegi.</p>{% endif %}
    </div>

    <div class="card"><h2>Venue-wise Reports</h2><div class="table-wrap"><table>
    <tr><th>Venue</th><th>Total Scans</th></tr>
    {% for v in venues %}<tr><td>{{v.venue}}</td><td>{{v.total}}</td></tr>{% endfor %}
    </table></div></div>

    <div class="card"><h2>City-wise Reports</h2><div class="table-wrap"><table>
    <tr><th>City</th><th>Country</th><th>Scans</th></tr>
    {% for c in cities %}<tr><td>{{c.city}}</td><td>{{c.country}}</td><td>{{c.total}}</td></tr>{% endfor %}
    </table></div></div>

    <div class="card"><h2>Device Reports</h2><div class="table-wrap"><table>
    <tr><th>Device</th><th>Scans</th></tr>
    {% for d in devices %}<tr><td>{{d.device}}</td><td>{{d.total}}</td></tr>{% endfor %}
    </table></div></div>

    <div class="card"><h2>Generate Venue QR Codes</h2>
    {% for slug,name in all_venues.items() %}
    <p><b>{{name}}</b> <a class="btn" href="/qr/{{slug}}">Download QR</a></p>
    {% endfor %}
    <p style="font-size:13px;color:#555">QR link base: {{base_url}}</p>
    </div>

    <div class="card"><h2>Latest Scan Records</h2><div class="table-wrap"><table>
    <tr><th>Venue</th><th>Date &amp; Time (UTC)</th><th>City</th><th>Device</th>
    <th>Browser</th><th>Exact Location</th></tr>
    {% for r in recent %}
    <tr><td>{{r.venue}}</td><td>{{r.scanned_at}}</td>
    <td>{{r.city or '-'}}{% if r.country %}, {{r.country}}{% endif %}</td>
    <td>{{r.device}}</td><td>{{r.browser}}</td>
    <td>{% if r.lat is not none %}<a target="_blank"
    href="https://www.google.com/maps?q={{r.lat}},{{r.lon}}">Open map</a>{% else %}-{% endif %}</td>
    </tr>{% endfor %}
    </table></div></div>
    </main>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
    var pts = {{points|tojson}};
    var map = L.map('map').setView([28.6139, 77.2090], 10);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(map);
    var bounds = [];
    pts.forEach(function(p){
      L.marker([p.lat, p.lon]).addTo(map).bindPopup(p.venue + "<br>" + p.time);
      bounds.push([p.lat, p.lon]);
    });
    if (bounds.length) map.fitBounds(bounds, {padding: [30, 30], maxZoom: 15});
    </script>
    </body></html>
    """, total=total, gps_count=gps_count, venues=venues, devices=devices,
        cities=cities, recent=recent, points=points,
        all_venues=VENUES, base_url=PUBLIC_BASE_URL)


@app.route("/qr/<slug>")
@login_required
def make_qr(slug):
    if slug not in VENUES:
        return "Venue not found", 404

    tracking_link = f"{PUBLIC_BASE_URL.rstrip('/')}/scan/{slug}"
    img = qrcode.make(tracking_link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True,
                     download_name=f"Rackonnect_{slug}_QR.png")


@app.route("/export")
@login_required
def export():
    db = connect_db()
    rows = db.execute("SELECT * FROM scans ORDER BY id DESC").fetchall()
    db.close()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "Venue", "Scan Date (UTC)", "City", "Region", "Country",
                "Latitude", "Longitude", "Accuracy (m)", "Device", "Browser"])
    for r in rows:
        w.writerow([r["id"], r["venue"], r["scanned_at"], r["city"], r["region"],
                    r["country"], r["lat"], r["lon"], r["accuracy"],
                    r["device"], r["browser"]])

    data = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True,
                     download_name="Rackonnect_Scan_Report.csv")


setup_database()

if __name__ == "__main__":
    print("Rackonnect QR Analytics is starting...")
    app.run(host="0.0.0.0", port=5000, debug=False)