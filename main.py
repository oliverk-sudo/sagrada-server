"""
Sagrada Família Tower Ticket Availability Server
------------------------------------------------
- Fetches live availability from the Clorian ticketing API every 15 minutes
- Stores results in memory (and a local JSON file as backup)
- Serves results via a simple HTTP API that the dashboard can call
- Runs forever on a free Railway server
"""

import requests
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PRODUCT_ID    = 4443          # Sagrada Familia with Towers
VENUE_ID      = 3             # The basilica
SALES_GROUP   = 1
MONTHS_AHEAD  = 3             # How many months to check
REFRESH_MINS  = 15            # How often to fetch fresh data

# The time slots you care about
TOWER_SLOTS = ["14:00","14:15","14:30","14:45","15:00","15:15",
               "15:30","15:45","16:00","16:15","16:30"]
ENTRY_SLOTS = ["13:30","13:45","14:00","14:15","14:30","14:45","15:00"]

CLORIAN_URL = "https://services.clorian.com/catalog/events/available"

# ─── STATE ───────────────────────────────────────────────────────────────────
# This dictionary holds all the fetched data in memory
availability_cache = {}
last_updated = None

# ─── FETCH LOGIC ─────────────────────────────────────────────────────────────
def get_dates_to_check():
    """Returns a list of date strings for the next MONTHS_AHEAD months."""
    dates = []
    today = datetime.today()
    for offset in range(MONTHS_AHEAD * 31):
        d = today + timedelta(days=offset)
        if (d - today).days > MONTHS_AHEAD * 31:
            break
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def fetch_date(date_str):
    """Fetches availability for a single date from Clorian."""
    params = {
        "productId":     PRODUCT_ID,
        "salesGroupId":  SALES_GROUP,
        "venueId":       VENUE_ID,
        "startDateFrom": date_str,
        "startDateTo":   date_str,
    }
    headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; AvailabilityChecker/1.0)",
        "Origin":     "https://tickets.sagradafamilia.org",
        "Referer":    "https://tickets.sagradafamilia.org/",
    }
    try:
        response = requests.get(CLORIAN_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Clorian may return a list or wrap it in a key
        events = data if isinstance(data, list) else (
            data.get("events") or data.get("data") or data.get("items") or []
        )

        tower = {t: 0 for t in TOWER_SLOTS}
        entry = {t: 0 for t in ENTRY_SLOTS}

        for event in events:
            start = event.get("startDate") or event.get("start") or ""
            if not start.startswith(date_str):
                continue
            time_str = start[11:16]  # e.g. "14:00"
            avail = (
                event.get("availableTickets") or
                event.get("available") or
                event.get("availability") or 0
            )
            if time_str in TOWER_SLOTS:
                tower[time_str] = avail
            if time_str in ENTRY_SLOTS:
                entry[time_str] = avail

        return {"tower": tower, "entry": entry, "live": True}

    except Exception as e:
        print(f"  ✗ Error fetching {date_str}: {e}")
        return None


def refresh_all():
    """Fetches availability for all upcoming dates and updates the cache."""
    global availability_cache, last_updated

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting availability refresh...")
    dates = get_dates_to_check()
    new_cache = {}
    success = 0

    for date_str in dates:
        result = fetch_date(date_str)
        if result:
            new_cache[date_str] = result
            success += 1
        else:
            # Keep old data if we have it
            if date_str in availability_cache:
                new_cache[date_str] = availability_cache[date_str]
        # Be polite — small delay between requests so we don't hammer the server
        time.sleep(0.5)

    availability_cache = new_cache
    last_updated = datetime.now().isoformat()

    # Save to disk as backup
    with open("cache.json", "w") as f:
        json.dump({"updated": last_updated, "data": availability_cache}, f)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Done — {success}/{len(dates)} dates fetched successfully")


def background_refresh_loop():
    """Runs in a background thread, refreshing data every REFRESH_MINS minutes."""
    while True:
        refresh_all()
        print(f"  Next refresh in {REFRESH_MINS} minutes...")
        time.sleep(REFRESH_MINS * 60)


# ─── WEB SERVER ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # This allows the dashboard to call this server from any browser


@app.route("/")
def home():
    """Simple status page so you can check the server is running."""
    return jsonify({
        "status":       "running",
        "last_updated": last_updated,
        "dates_cached": len(availability_cache),
        "message":      "Sagrada Familia Availability Server is live!"
    })


@app.route("/availability")
def get_all_availability():
    """Returns all cached availability data."""
    return jsonify({
        "updated": last_updated,
        "data":    availability_cache
    })


@app.route("/availability/<date_str>")
def get_date_availability(date_str):
    """Returns availability for a specific date, e.g. /availability/2026-07-15"""
    if date_str in availability_cache:
        return jsonify(availability_cache[date_str])
    return jsonify({"error": "Date not found"}), 404


@app.route("/health")
def health():
    """Railway uses this to check the server is alive."""
    return jsonify({"status": "ok"})


# ─── STARTUP ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Try to load cached data from disk on startup (so we have something immediately)
    try:
        with open("cache.json") as f:
            saved = json.load(f)
            availability_cache.update(saved.get("data", {}))
            last_updated = saved.get("updated")
            print(f"Loaded {len(availability_cache)} dates from disk cache")
    except FileNotFoundError:
        print("No disk cache found — will fetch fresh data now")

    # Start the background refresh thread
    thread = threading.Thread(target=background_refresh_loop, daemon=True)
    thread.start()

    # Start the web server (Railway sets PORT automatically)
    import os
    port = int(os.environ.get("PORT", 8080))
    print(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
