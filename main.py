"""
Sagrada Família Tower Ticket Availability Server
Uses the FREE monthly calendar endpoint — no recaptcha token needed.
Makes just 2 API calls per refresh (one per month) instead of 93.
Returns date-level availability (available vs sold out).
"""

import requests
import json
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

# ─── CONFIG ──────────────────────────────────────────────────────────────────
VENUE_ID     = 3
REFRESH_MINS = 15
MONTHS_AHEAD = 2  # just 2 months

# Free endpoint — no recaptcha needed
CALENDAR_URL = "https://services.clorian.com/catalog/calendar/availability"

# ─── STATE ───────────────────────────────────────────────────────────────────
availability_cache = {}
last_updated = None
last_error = None

# ─── FETCH LOGIC ─────────────────────────────────────────────────────────────
def fetch_month(year, month):
    """Fetch the monthly calendar overview — no token required."""
    params = {
        "month":    month,
        "year":     year,
        "venueId":  VENUE_ID,
        "minTickets": 1,
    }
    headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin":     "https://tickets.sagradafamilia.org",
        "Referer":    "https://tickets.sagradafamilia.org/",
    }
    try:
        res = requests.get(CALENDAR_URL, params=params, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        print(f"  ✓ Fetched {year}-{month:02d}: {len(data) if isinstance(data, dict) else 'list'} entries")
        return data
    except Exception as e:
        print(f"  ✗ Error fetching {year}-{month:02d}: {e}")
        return None


def refresh_all():
    global availability_cache, last_updated, last_error

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting refresh...")
    new_cache = {}

    today = datetime.today()
    for offset in range(MONTHS_AHEAD + 1):
        year  = today.year  + (today.month + offset - 1) // 12
        month = (today.month + offset - 1) % 12 + 1

        data = fetch_month(year, month)
        if data is None:
            continue

        # Clorian calendar returns either:
        # { "2026-06-01": "availability", "2026-06-02": "no-availability", ... }
        # or a list of { date, status } objects
        if isinstance(data, dict):
            for date_str, status in data.items():
                available = status == "availability" or status == "available" or status is True
                new_cache[date_str] = {
                    "available": available,
                    "status":    status,
                    "live":      True,
                }
        elif isinstance(data, list):
            for item in data:
                date_str = item.get("date") or item.get("startDate") or ""
                if not date_str:
                    continue
                status   = item.get("status") or item.get("availability") or ""
                available = status in ("availability", "available") or item.get("available") is True
                new_cache[date_str] = {
                    "available": available,
                    "status":    status,
                    "live":      True,
                }

        time.sleep(0.5)  # be polite

    # Keep old data for anything we didn't get
    for date_str, val in availability_cache.items():
        if date_str not in new_cache:
            new_cache[date_str] = val

    availability_cache = new_cache
    last_updated = datetime.now().isoformat()
    last_error = None

    with open("cache.json", "w") as f:
        json.dump({"updated": last_updated, "data": availability_cache}, f)

    available_count = sum(1 for v in new_cache.values() if v.get("available"))
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Done — {len(new_cache)} dates, {available_count} available")


def background_loop():
    while True:
        refresh_all()
        print(f"  Next refresh in {REFRESH_MINS} minutes...")
        time.sleep(REFRESH_MINS * 60)


# ─── WEB SERVER ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


@app.route("/")
def home():
    available = sum(1 for v in availability_cache.values() if v.get("available"))
    soldout   = sum(1 for v in availability_cache.values() if not v.get("available"))
    return jsonify({
        "status":       "running",
        "last_updated": last_updated,
        "dates_cached": len(availability_cache),
        "available":    available,
        "sold_out":     soldout,
        "last_error":   last_error,
        "message":      "Sagrada Familia Availability Server is live!"
    })


@app.route("/availability")
def get_all():
    return jsonify({
        "updated": last_updated,
        "data":    availability_cache
    })


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
        print("No disk cache — fetching fresh data now")

    thread = threading.Thread(target=background_loop, daemon=True)
    thread.start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
