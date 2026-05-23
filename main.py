"""
Sagrada Família Tower Ticket Availability Server
Token is stored as a Railway environment variable RECAPTCHA_TOKEN
so you can update it without touching GitHub.
"""

import requests
import json
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PRODUCT_ID   = 4443
SALES_GROUP  = 1
MONTHS_AHEAD = 3
REFRESH_MINS = 15

TOWER_SLOTS = ["14:00","14:15","14:30","14:45","15:00","15:15",
               "15:30","15:45","16:00","16:15","16:30"]
ENTRY_SLOTS = ["13:30","13:45","14:00","14:15","14:30","14:45","15:00"]

CLORIAN_URL = "https://services.clorian.com/catalog/events/available"

# Token is read from environment variable — update it in Railway dashboard
# without needing to change any code or redeploy
def get_token():
    return os.environ.get("RECAPTCHA_TOKEN", "")

# We try both venue IDs since the site uses them inconsistently
VENUE_IDS = [3, 1]

# ─── STATE ───────────────────────────────────────────────────────────────────
availability_cache = {}
last_updated = None
token_expired = False
working_venue_id = 3  # will be updated when we find the working one

# ─── FETCH LOGIC ─────────────────────────────────────────────────────────────
def get_dates_to_check():
    dates = []
    today = datetime.today()
    for offset in range(MONTHS_AHEAD * 31):
        d = today + timedelta(days=offset)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def fetch_date(date_str, venue_id):
    global token_expired
    params = {
        "productId":     PRODUCT_ID,
        "salesGroupId":  SALES_GROUP,
        "venueId":       venue_id,
        "recaptcha":     get_token(),
        "startDateFrom": date_str,
        "startDateTo":   date_str,
    }
    headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin":     "https://tickets.sagradafamilia.org",
        "Referer":    "https://tickets.sagradafamilia.org/",
    }
    try:
        res = requests.get(CLORIAN_URL, params=params, headers=headers, timeout=10)
        if res.status_code in (401, 403):
            token_expired = True
            print(f"  ⚠ Token expired (HTTP {res.status_code}) for {date_str}")
            return None
        res.raise_for_status()
        token_expired = False
        events = res.json()
        if not isinstance(events, list):
            events = events.get("events") or events.get("data") or events.get("items") or []

        tower = {t: 0 for t in TOWER_SLOTS}
        entry = {t: 0 for t in ENTRY_SLOTS}
        for event in events:
            start = (event.get("startDatetime") or event.get("startDate") or event.get("start") or "")
            time_str = start[11:16] if len(start) >= 16 else None
            if not time_str:
                continue
            avail = (event.get("totalAvailability") or event.get("availableTickets") or event.get("available") or 0)
            if time_str in TOWER_SLOTS:
                tower[time_str] = avail
            if time_str in ENTRY_SLOTS:
                entry[time_str] = avail

        return {"tower": tower, "entry": entry, "live": True}
    except Exception as e:
        print(f"  ✗ Error fetching {date_str} (venue {venue_id}): {e}")
        return None


def fetch_date_any_venue(date_str):
    global working_venue_id
    # Try the last working venue first, then the other
    venues = [working_venue_id] + [v for v in VENUE_IDS if v != working_venue_id]
    for venue_id in venues:
        result = fetch_date(date_str, venue_id)
        if result:
            working_venue_id = venue_id
            return result
        if token_expired:
            return None  # no point trying other venues if token is dead
    return None


def refresh_all():
    global availability_cache, last_updated
    if not get_token():
        print("⚠ No RECAPTCHA_TOKEN set — please add it in Railway Variables")
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting refresh (venue {working_venue_id})...")
    dates = get_dates_to_check()
    new_cache = {}
    success = 0

    for date_str in dates:
        result = fetch_date_any_venue(date_str)
        if result:
            new_cache[date_str] = result
            success += 1
        elif date_str in availability_cache:
            new_cache[date_str] = availability_cache[date_str]
        if token_expired:
            print("  Token expired — stopping refresh early")
            break
        time.sleep(0.4)

    availability_cache = new_cache
    last_updated = datetime.now().isoformat()

    with open("cache.json", "w") as f:
        json.dump({"updated": last_updated, "data": availability_cache}, f)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Done — {success}/{len(dates)} dates fetched")


def background_refresh_loop():
    while True:
        refresh_all()
        print(f"  Next refresh in {REFRESH_MINS} minutes...")
        time.sleep(REFRESH_MINS * 60)


# ─── WEB SERVER ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


@app.route("/")
def home():
    return jsonify({
        "status":        "running",
        "last_updated":  last_updated,
        "dates_cached":  len(availability_cache),
        "token_set":     bool(get_token()),
        "token_expired": token_expired,
        "message":       "Sagrada Familia Availability Server is live!"
    })


@app.route("/availability")
def get_all():
    return jsonify({
        "updated":       last_updated,
        "token_expired": token_expired,
        "data":          availability_cache
    })


@app.route("/availability/<date_str>")
def get_date(date_str):
    if date_str in availability_cache:
        return jsonify(availability_cache[date_str])
    return jsonify({"error": "Date not found"}), 404


@app.route("/health")
def health():
    return jsonify({"status": "ok", "token_expired": token_expired})


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

    thread = threading.Thread(target=background_refresh_loop, daemon=True)
    thread.start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
