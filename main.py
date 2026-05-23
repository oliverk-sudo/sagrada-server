"""
Sagrada Família Tower Ticket Availability Server
Uses the FREE monthly calendar endpoint — no recaptcha token needed.
endpoint: /catalog/salesGroups/1/product/4443/availability?month=6&venueId=3&year=2026
Makes just 3 API calls per refresh (one per month). Completely free, forever.
"""

import requests
import json
import time
import threading
import os
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

# ─── CONFIG ──────────────────────────────────────────────────────────────────
VENUE_ID      = 3
SALES_GROUP   = 1
PRODUCT_ID    = 4443
REFRESH_MINS  = 15
MONTHS_AHEAD  = 2

BASE_URL = "https://services.clorian.com/catalog/salesGroups/1/product/4443/availability"

# ─── STATE ───────────────────────────────────────────────────────────────────
availability_cache = {}
last_updated = None
last_error   = None

# ─── FETCH ───────────────────────────────────────────────────────────────────
def fetch_month(year, month):
    params  = {"month": month, "year": year, "venueId": VENUE_ID}
    headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin":     "https://tickets.sagradafamilia.org",
        "Referer":    "https://tickets.sagradafamilia.org/",
    }
    try:
        res = requests.get(BASE_URL, params=params, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        print(f"  ✓ {year}-{month:02d}: {type(data).__name__} with {len(data)} entries")
        print(f"    Sample: {str(data)[:200]}")
        return data
    except Exception as e:
        print(f"  ✗ Error {year}-{month:02d}: {e}")
        return None


def refresh_all():
    global availability_cache, last_updated, last_error
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Refreshing...")

    new_cache = {}
    today = datetime.today()

    for offset in range(MONTHS_AHEAD + 1):
        year  = today.year + (today.month + offset - 1) // 12
        month = (today.month + offset - 1) % 12 + 1
        data  = fetch_month(year, month)
        if data is None:
            continue

        # Handle dict format: {"2026-06-01": "availability", ...}
        if isinstance(data, dict):
            for date_str, status in data.items():
                available = str(status).lower() in ("availability", "available", "true", "1")
                new_cache[date_str] = {"available": available, "status": str(status), "live": True}

        # Handle list format: [{"date": "2026-06-01", "status": "availability"}, ...]
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    date_str  = item.get("date") or item.get("startDate") or item.get("day") or ""
                    status    = str(item.get("status") or item.get("availability") or "")
                    available = status.lower() in ("availability", "available", "true", "1")
                    if date_str:
                        new_cache[date_str] = {"available": available, "status": status, "live": True}

        time.sleep(0.5)

    # Keep old data for anything not returned
    for k, v in availability_cache.items():
        if k not in new_cache:
            new_cache[k] = v

    availability_cache = new_cache
    last_updated = datetime.now().isoformat()
    last_error   = None

    with open("cache.json", "w") as f:
        json.dump({"updated": last_updated, "data": availability_cache}, f)

    available = sum(1 for v in new_cache.values() if v.get("available"))
    print(f"  Done — {len(new_cache)} dates, {available} available, {len(new_cache)-available} sold out")


def background_loop():
    while True:
        refresh_all()
        time.sleep(REFRESH_MINS * 60)


# ─── WEB SERVER ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route("/")
def home():
    available = sum(1 for v in availability_cache.values() if v.get("available"))
    return jsonify({
        "status":       "running",
        "last_updated": last_updated,
        "dates_cached": len(availability_cache),
        "available":    available,
        "sold_out":     len(availability_cache) - available,
        "last_error":   last_error,
        "message":      "Sagrada Familia Availability Server is live!"
    })

@app.route("/availability")
def get_all():
    return jsonify({"updated": last_updated, "data": availability_cache})

@app.route("/availability/<date_str>")
def get_date(date_str):
    if date_str in availability_cache:
        return jsonify(availability_cache[date_str])
    return jsonify({"error": "Date not found"}), 404

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ─── STARTUP ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        with open("cache.json") as f:
            saved = json.load(f)
            availability_cache.update(saved.get("data", {}))
            last_updated = saved.get("updated")
            print(f"Loaded {len(availability_cache)} dates from disk cache")
    except FileNotFoundError:
        print("No disk cache — fetching now")

    threading.Thread(target=background_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
