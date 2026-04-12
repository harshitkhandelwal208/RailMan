# 🚀 RailMan AI — Deployment Guide

Complete setup in ~15 minutes. The backend serves **both the API and the web UI** from a single Python process — no separate frontend server needed.

---

## How It Works

```
Browser / Phone
     │
     ▼
FastAPI (uvicorn)
  ├── GET /          → serves web/index.html  (the full app)
  ├── POST /api/chat → AI chat
  ├── POST /api/recommend → recommendations
  ├── GET /api/live_trains → live train positions
  ├── GET /api/stations → station list
  └── GET /health    → status check
```

Open `http://localhost:8000` in any browser — desktop or mobile on the same network.

---

## Local Development

### 1. Install dependencies

```bash
cd railman/backend
python -m venv venv

# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...     # get from console.anthropic.com
MONGODB_URI=mongodb+srv://...    # from MongoDB Atlas (see below)
MONGODB_DB=railman
```

> **No API key?** The app still works — it uses the built-in rule-based engine which gives great recommendations without any LLM.

### 3. Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** in your browser.

**On your phone** (same WiFi): open `http://YOUR_PC_IP:8000`
Find your IP with `ipconfig` (Windows) or `ifconfig` (Mac/Linux).

---

## MongoDB Atlas Setup

### 1. Create free cluster

1. Go to **[cloud.mongodb.com](https://cloud.mongodb.com)** → sign up
2. **"Build a Database"** → **M0 Free** → AWS → **Mumbai (ap-south-1)**
3. Cluster name: `railman` → **"Create"**

### 2. Create database user

1. Left sidebar → **"Database Access"** → **"Add New Database User"**
2. Auth: **Password**
3. Username: `railman_user` · Password: click **"Autogenerate"** → copy it
4. Role: **"Atlas Admin"** → **"Add User"**

### 3. Allow all IPs

1. Left sidebar → **"Network Access"** → **"Add IP Address"**
2. Click **"Allow Access from Anywhere"** (`0.0.0.0/0`) → **"Confirm"**

### 4. Get connection string

1. **"Database"** → **"Connect"** → **"Drivers"** → Python
2. Copy the URI:
   ```
   mongodb+srv://railman_user:<password>@railman.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
3. Replace `<password>` with your actual password
4. Paste into your `.env` as `MONGODB_URI`

### 5. First startup

On first boot the app automatically:
- Pings the cluster
- Creates all indexes
- Seeds 28 stations + 388 trains

You'll see in the logs:
```
MongoDB: ping OK ✓
MongoDB: seeded 28 stations
MongoDB: seeded 388 trains
MongoDB: init complete ✓
```

---

## Deploy to Render (free hosting)

### 1. Push to GitHub

```bash
cd railman/backend
git init
git add .
git commit -m "RailMan AI — initial deploy"
# Create repo on github.com then:
git remote add origin https://github.com/YOUR_USERNAME/railman-backend.git
git branch -M main
git push -u origin main
```

### 2. Create Render Web Service

1. Go to **[render.com](https://render.com)** → sign up → **"New +"** → **"Web Service"**
2. Connect GitHub → select `railman-backend`
3. Settings:

   | Field | Value |
   |-------|-------|
   | Name | `railman-api` |
   | Region | Singapore |
   | Branch | `main` |
   | Runtime | Python 3 |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Plan | Free |

### 3. Environment variables

In the **"Environment"** tab:

| Key | Value |
|-----|-------|
| `MONGODB_URI` | your Atlas connection string |
| `MONGODB_DB` | `railman` |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` |
| `PYTHON_VERSION` | `3.11.0` |

Click **"Create Web Service"** → wait ~2 min for build.

### 4. Access the app

Your app will be live at:
```
https://railman-api.onrender.com
```

Open it in any browser — it loads the full web UI automatically.

---

## Keep Free Tier Warm (prevents 30s cold starts)

1. Go to **[uptimerobot.com](https://uptimerobot.com)** → free account
2. **"Add Monitor"** → HTTP(S)
3. URL: `https://railman-api.onrender.com/health`
4. Interval: **5 minutes**

Or upgrade to Render **Starter ($7/mo)** for always-on.

---

## Troubleshooting

**SSL handshake error in logs**
→ Already fixed in this version (`tlsAllowInvalidCertificates=True` + latest certifi).
→ Also run: `pip install --upgrade certifi pymongo motor`

**`{"db": "unavailable"}` on /health**
→ Check `MONGODB_URI` env var · confirm IP whitelist is `0.0.0.0/0` in Atlas

**App loads but trains not moving**
→ The `/api/live_trains` endpoint is being called — check browser console for errors
→ Confirm backend is running and reachable

**Can't open on phone**
→ Make sure phone and PC are on the same WiFi
→ Use your PC's local IP (from `ipconfig`), not `localhost`
→ Try: `http://192.168.x.x:8000`

**Build fails on Render**
→ Check `requirements.txt` is in the repo root (same folder as the `app/` directory)

**Atlas auth error**
→ Special characters in password must be URL-encoded (`@`→`%40`, `#`→`%23`)
