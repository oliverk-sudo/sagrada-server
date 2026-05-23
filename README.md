[README.md](https://github.com/user-attachments/files/28175917/README.md)
# Sagrada Família Availability Server

A lightweight Python server that fetches live ticket availability from the
Sagrada Família (Clorian) API every 15 minutes and serves it to the dashboard.

## Files

| File | What it does |
|------|-------------|
| `main.py` | The server — fetches data and serves it |
| `requirements.txt` | Python packages needed |
| `Procfile` | Tells Railway how to start the server |

## API Endpoints

Once deployed, your server will have a URL like `https://your-app.railway.app`

| Endpoint | What it returns |
|----------|----------------|
| `/` | Server status and last update time |
| `/availability` | All dates and slot availability |
| `/availability/2026-07-15` | A single specific date |
| `/health` | Simple health check |

## Deploy to Railway (Free)

1. Go to **github.com** and create a free account
2. Create a new repository called `sagrada-server`
3. Upload these 3 files: `main.py`, `requirements.txt`, `Procfile`
4. Go to **railway.app** and sign in with GitHub
5. Click **New Project → Deploy from GitHub repo**
6. Select your `sagrada-server` repository
7. Railway will deploy automatically — takes about 2 minutes
8. Copy your server URL from the Railway dashboard

## Connect to the Dashboard

Once you have your Railway URL, tell Claude and the dashboard will be
updated to fetch live data from your server instead of demo data.

## Checking it works

Visit your Railway URL in a browser — you should see:
```json
{
  "status": "running",
  "dates_cached": 90,
  "message": "Sagrada Familia Availability Server is live!"
}
```
