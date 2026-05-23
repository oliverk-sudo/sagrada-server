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
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PRODUCT_ID    = 4443          # Sagrada Familia with Towers
VENUE_ID      = 1             # Correct venue ID from live URL
SALES_GROUP   = 1
MONTHS_AHEAD  = 3
REFRESH_MINS  = 15

TOWER_SLOTS = ["14:00","14:15","14:30","14:45","15:00","15:15",
               "15:30","15:45","16:00","16:15","16:30"]
ENTRY_SLOTS = ["13:30","13:45","14:00","14:15","14:30","14:45","15:00"]

CLORIAN_URL = "https://services.clorian.com/catalog/events/available"

# Recaptcha token — paste a fresh one here when it expires.
# Get it by: going to tickets.sagradafamilia.org, picking a date,
# opening DevTools Network tab, copying the recaptcha= value from
# the available?productId=4443 request URL.
RECAPTCHA_TOKEN = "0cAFcWeA7y7ZVzYlEZaYazMRTgeTwxj6T6PTuEiHLDFZCrd58FNnhPztc-e7OhLY8PbYVeZENTfy9WAuCq3YzN21pRfHjiqZLZT0bJ5QCA2JEhTsxyNM0-0_sTsd3zoyB-uX05659i_H0ZZyxwOdobv8Ppfgcdguf4OnOonNAWNqMC1JmtiMOGqyEMJYcuGYfQisu_7ai5uUpuX4OshHmf2X63bD5Y3DX4T4OcaN25qkWulkW5uawEFCIQoNxY3bMY1dGgi9uy-B-08kGbtublidGs3e-BvFqCpSEVmOjEvoeRbYetfDim9nfYmJrXtdBBeU9qqm-ZT9S2EHEDG2ENfSyU1WgcWpEqa9tVCbnskZaUYVO4zx1c4Ooes5HZujLPsb3Elk3jTOYu2ZUdUApvvgHIIS9XHy5e1MZBVpyngeVX5nRf5rHl77QL6in0ZodMP1TPxY_mMGOhYeuXAyv4rMdUPaqI6rx0I4PQGAQ58PsdmIAhl68bfeI-Ee-7TaFvQz266h9dgIkUTJemYbCX5c2sSkI7NWkFSzY9yEZp9by6hpGljz-FCK2rXzK_3jyhb-Px3MCIfle5FrWWUgKuTJqPu1ciQp8UCO4wIjrbwmq5TpygcMFoRdCKRuRbtmQDpdnzUB0FCvbVQ6roPLz1T2Seic0GdPRd4FLFqcjiC5zHJ5BjQmA_Wy-f9XkBPerDBdwy81KG6WQJWsNtPI429GH5R9uteonD8P7ExYmhMaJYYu8u2gbWsaVRgoNyl9ESUhWmq_853rdw6P6YLyMGe_Yu43qsS-lSRbOdLwe9ESd0fDVIrecTCiqcJHTif-lwXFPS7l4fbmtbSvPH7N9Ynnt5kzTJdF9XYNGwtt2t7S6vIZWUgTO_hHDeQgf9Hq-hN0p-_vY4EOYOc3jL77O6tGi7BQzfWZGHhthWsXEdMvh88hHSMGG7lgEUxhBcKQYEloQCQL_T3RzsBacA3Wwu-URYFA6bk9AvopEFm_FTV6825Aj7l5DeppqYc3e6i0P6tRJZbaLvOh7u3zQgPTW_xMmAKeGh2_rf2sN--1l8b2RYuM-ljdTaH6b60-y-12FKXBI2aNPpwoXHfyxbdj2R0lgImjrsQHYIeZOJM0wEaSpOAUfPh9wFGuQ"

# ─── STATE ───────────────────────────────────────────────────────────────────
availability_cache = {}
last_updated = None
token_expired = False

# ─── FETCH LOGIC ─────────────────────────────────────────────────────────────
def get_dates_to_check():
    dates = []
    today = datetime.today()
    for offset in range(MONTHS_AHEAD * 31):
        d = today + timedelta(days=offset)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def fetch_date(date_str):
    global token_expired
    params = {
        "productId":     PRODUCT_ID,
        "salesGroupId":  SALES_GROUP,
        "venueId":       VENUE_ID,
        "recaptcha":     RECAPTCHA_TOKEN,
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
        response = requests.get(CLORIAN_URL, params=params, headers=headers, timeout=10)

        # 401/403 usually means the token expired
        if response.status_code in (401, 403):
            token_expired = True
            print(f"  ⚠ Token may have expired (HTTP {response.status_code})")
            return None

        response.raise_for_status()
        token_expired = False
        data = response.json()

        events = data if isinstance(data, list) else (
            data.get("events") or data.get("data") or data.get("items") or []
        )

        tower = {t: 0 for t in TOWER_SLOTS}
        entry = {t: 0 for t in ENTRY_SLOTS}

        for event in events:
            start = event.get("startDate") or event.get("start") or ""
            if not start.startswith(date_str):
                continue
            time_str = start[11:16]
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
    global availability_cache, last_updated
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting refresh...")
    dates = get_dates_to_check()
    new_cache = {}
    success = 0

    for date_str in dates:
        result = fetch_date(date_str)
        if result:
            new_cache[date_str] = result
            success += 1
        elif date_str in availability_cache:
            new_cache[date_str] = availability_cache[date_str]
        time.sleep(0.4)  # be polite to the server

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
        "token_expired": token_expired,
        "message":       "Sagrada Familia Availability Server is live!"
    })


@app.route("/availability")
def get_all_availability():
    return jsonify({
        "updated":       last_updated,
        "token_expired": token_expired,
        "data":          availability_cache
    })


@app.route("/availability/<date_str>")
def get_date_availability(date_str):
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
