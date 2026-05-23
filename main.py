"""
Sagrada Família Tower Ticket Availability Server
Uses Playwright (headless browser) to fetch real availability data.
This bypasses the recaptcha token problem entirely — the browser
generates and uses its own token automatically, just like a real user.
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

TICKET_URL = "https://tickets.sagradafamilia.org/en/1-individual/4443-sagrada-familia-with-towers"
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
    """Turn a list of Clorian event objects into our slot format."""
    tower = {t: 0 for t in TOWER_SLOTS}
    entry = {t: 0 for t in ENTRY_SLOTS}
    if not isinstance(events, list):
        return {"tower": tower, "entry": entry, "live": True}
    for event in events:
        start = (event.get("startDatetime") or event.get("startDate") or
                 event.get("start") or "")
        if not start.startswith(date_str):
            continue
        time_str = start[11:16]  # "14:00"
        avail = (event.get("totalAvailability") or
                 event.get("availableTickets") or
                 event.get("available") or 0)
        if time_str in TOWER_SLOTS:
            tower[time_str] = avail
        if time_str in ENTRY_SLOTS:
            entry[time_str] = avail
    return {"tower": tower, "entry": entry, "live": True}


# ─── PLAYWRIGHT FETCH ─────────────────────────────────────────────────────────
def fetch_all_with_playwright():
    """
    Opens a real headless browser, visits the ticket page, then intercepts
    the API calls to collect availability data for all dates.
    No recaptcha token needed — the browser handles it automatically.
    """
    global availability_cache, last_updated, last_error

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting Playwright fetch...")
    dates = get_dates_to_check()
    new_cache = {}

    try:
        with sync_playwright() as p:
            # Launch headless Chromium
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # Storage for intercepted API responses
            api_responses = {}

            # Intercept all API calls to the Clorian availability endpoint
            def handle_response(response):
                if CLORIAN_BASE in response.url and "available" in response.url:
                    try:
                        data = response.json()
                        events = data if isinstance(data, list) else (
                            data.get("events") or data.get("data") or data.get("items") or []
                        )
                        # Find which date this response is for
                        for date_str in dates:
                            if date_str in response.url:
                                api_responses[date_str] = events
                                print(f"  ✓ Captured {date_str} — {len(events)} events")
                                break
                    except Exception as e:
                        print(f"  ✗ Failed to parse response: {e}")

            page.on("response", handle_response)

            # Visit the ticket page — this loads the calendar and triggers API calls
            print("  Opening ticket page...")
            page.goto(TICKET_URL, wait_until="networkidle", timeout=30000)
            print("  Page loaded — calendar should be visible")

            # Wait for initial API calls to complete
            page.wait_for_timeout(3000)

            # Now click through each month to trigger API calls for all dates
            for month_offset in range(MONTHS_AHEAD + 1):
                try:
                    # Click the "next month" arrow on the calendar
                    if month_offset > 0:
                        next_btn = page.query_selector("[aria-label='Next month'], .next-month, .fc-next-button, button[class*='next']")
                        if next_btn:
                            next_btn.click()
                            page.wait_for_timeout(2000)  # wait for API call

                    # Also try clicking each date to get slot-level data
                    # The calendar usually loads slot data when you click a date
                    date_cells = page.query_selector_all("[data-date], .fc-day, td[class*='day']")
                    for cell in date_cells[:5]:  # click a few dates per month
                        try:
                            cell.click()
                            page.wait_for_timeout(1000)
                        except:
                            pass
                except Exception as e:
                    print(f"  Navigation error (month {month_offset}): {e}")

            # Final wait for any remaining API calls
            page.wait_for_timeout(3000)

            browser.close()
            print(f"  Browser closed — captured {len(api_responses)} dates from API")

            # Parse all captured responses
            for date_str, events in api_responses.items():
                new_cache[date_str] = parse_events(events, date_str)

            # For dates not captured via interception, keep old data
            for date_str in dates:
                if date_str not in new_cache and date_str in availability_cache:
                    new_cache[date_str] = availability_cache[date_str]

        last_error = None

    except Exception as e:
        last_error = str(e)
        print(f"  ✗ Playwright error: {e}")
        # Keep existing cache on error
        new_cache = availability_cache

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
