"""
Sagrada Família Tower Ticket Availability Server
Uses Playwright to load the ticket page (getting valid session cookies),
then makes direct API calls for each date using those cookies.
"""

import json
import time
import threading
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PRODUCT_ID   = 4443
VENUE_ID     = 3
SALES_GROUP  = 1
MONTHS_AHEAD = 3
REFRESH_MINS = 15

TOWER_SLOTS = ["14:00","14:15","14:30","14:45","15:00","15:15",
               "15:30","15:45","16:00","16:15","16:30"]
ENTRY_SLOTS = ["13:30","13:45","14:00","14:15","14:30","14:45","15:00"]

TICKET_URL   = "https://tickets.sagradafamilia.org/en/1-individual/4443-sagrada-familia-with-towers"
CLORIAN_BASE = "https://services.clorian.com/catalog/events/available"

# ─── STATE ───────────────────────────────────────────────────────────────────
availability_cache = {}
last_updated = None
last_error = None

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def get_dates_to_check():
    dates = []
    today = datetime.today()
    for offset in range(MONTHS_AHEAD * 31):
        d = today + timedelta(days=offset)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def parse_events(events, date_str):
    tower = {t: 0 for t in TOWER_SLOTS}
    entry = {t: 0 for t in ENTRY_SLOTS}
    if not isinstance(events, list):
        return {"tower": tower, "entry": entry, "live": True}
    for event in events:
        start = (event.get("startDatetime") or event.get("startDate") or
                 event.get("start") or "")
        time_str = start[11:16] if len(start) >= 16 else None
        if not time_str:
            continue
        avail = (event.get("totalAvailability") or
                 event.get("availableTickets") or
                 event.get("available") or 0)
        if time_str in TOWER_SLOTS:
            tower[time_str] = avail
        if time_str in ENTRY_SLOTS:
            entry[time_str] = avail
    return {"tower": tower, "entry": entry, "live": True}


# ─── MAIN FETCH ──────────────────────────────────────────────────────────────
def fetch_all_with_playwright():
    global availability_cache, last_updated, last_error

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting Playwright fetch...")
    dates = get_dates_to_check()
    new_cache = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # Step 1: visit the ticket page to get session cookies + recaptcha token
            print("  Loading ticket page to get session...")
            captured_token = None
            captured_venue = VENUE_ID

            def on_request(request):
                nonlocal captured_token, captured_venue
                if CLORIAN_BASE in request.url and "recaptcha=" in request.url:
                    # Extract token from the URL
                    for part in request.url.split("&"):
                        if part.startswith("recaptcha="):
                            captured_token = part[len("recaptcha="):]
                        if part.startswith("venueId="):
                            captured_venue = part[len("venueId="):]

            page.on("request", on_request)

            try:
                page.goto(TICKET_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)  # wait for API calls to fire
            except Exception as e:
                print(f"  Page load warning: {e}")

            if captured_token:
                print(f"  ✓ Captured token from page (venue {captured_venue})")
            else:
                print("  ⚠ No token captured — will try with cookies only")

            # Step 2: use the page's fetch() to make API calls for each date
            # This runs JavaScript inside the browser, using its existing session
            print(f"  Fetching {len(dates)} dates via browser API calls...")
            success = 0

            for date_str in dates:
                try:
                    # Build URL with or without token
                    if captured_token:
                        url = (f"{CLORIAN_BASE}?productId={PRODUCT_ID}"
                               f"&salesGroupId={SALES_GROUP}&venueId={captured_venue}"
                               f"&recaptcha={captured_token}"
                               f"&startDateFrom={date_str}&startDateTo={date_str}")
                    else:
                        url = (f"{CLORIAN_BASE}?productId={PRODUCT_ID}"
                               f"&salesGroupId={SALES_GROUP}&venueId={VENUE_ID}"
                               f"&startDateFrom={date_str}&startDateTo={date_str}")

                    # Execute fetch inside the browser (uses browser's cookies/session)
                    result = page.evaluate(f"""
                        async () => {{
                            try {{
                                const res = await fetch("{url}", {{
                                    headers: {{
                                        "Accept": "application/json",
                                        "Origin": "https://tickets.sagradafamilia.org",
                                        "Referer": "https://tickets.sagradafamilia.org/"
                                    }}
                                }});
                                if (!res.ok) return {{ error: res.status }};
                                return await res.json();
                            }} catch(e) {{
                                return {{ error: e.message }};
                            }}
                        }}
                    """)

                    if isinstance(result, dict) and "error" in result:
                        print(f"  ✗ {date_str}: {result['error']}")
                        continue

                    events = result if isinstance(result, list) else (
                        result.get("events") or result.get("data") or result.get("items") or []
                    )
                    parsed = parse_events(events, date_str)
                    new_cache[date_str] = parsed
                    success += 1
                    if success % 10 == 0:
                        print(f"  ... {success} dates fetched")

                    time.sleep(0.3)

                except Exception as e:
                    print(f"  ✗ {date_str}: {e}")

            browser.close()
            print(f"  Browser closed — {success}/{len(dates)} dates fetched")
            last_error = None

    except Exception as e:
        last_error = str(e)
        print(f"  ✗ Playwright error: {e}")
        new_cache = availability_cache  # keep existing on error

    # Keep old data for any dates we didn't get
    for date_str in dates:
        if date_str not in new_cache and date_str in availability_cache:
            new_cache[date_str] = availability_cache[date_str]

    availability_cache = new_cache
    last_updated = datetime.now().isoformat()

    with open("cache.json", "w") as f:
        json.dump({"updated": last_updated, "data": availability_cache}, f)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Done — {len(new_cache)} dates cached")


def background_loop():
    while True:
        fetch_all_with_playwright()
        print(f"  Next refresh in {REFRESH_MINS} minutes...")
        time.sleep(REFRESH_MINS * 60)


# ─── WEB SERVER ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


@app.route("/")
def home():
    return jsonify({
        "status":       "running",
        "last_updated": last_updated,
        "dates_cached": len(availability_cache),
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
        print("No disk cache — will fetch fresh data now")

    thread = threading.Thread(target=background_loop, daemon=True)
    thread.start()

    port = int(os.environ.get("PORT", 8080))
    print(f"Server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
