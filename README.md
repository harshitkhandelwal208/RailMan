# 🚆 RailMan AI — Smart Mumbai Rail Assistant

> A production-grade, AI-powered web app for Mumbai's Western Railway line.  
> Natural language chat · Live train simulation · Crowd prediction · Journey planning

Open it in any browser — desktop or mobile. No app install needed.

---

## 🏗 Architecture

```
railman/
├── backend/                        # Everything — API + web frontend
│   ├── app/
│   │   ├── api/
│   │   │   ├── chat.py             # POST /api/chat, /api/recommend, /api/feedback
│   │   │   ├── trains.py           # GET  /api/live_trains, /api/crowd_forecast
│   │   │   ├── stations.py         # GET  /api/stations
│   │   │   └── analytics.py        # GET  /api/analytics, /api/popular_routes
│   │   ├── services/
│   │   │   ├── ai_engine.py        # Anthropic → OpenAI → rule-based fallback
│   │   │   ├── recommendation_engine.py  # Train scoring + NLP extraction
│   │   │   ├── crowd_engine.py     # Gaussian crowd prediction model
│   │   │   └── simulator.py        # Real-time train position physics
│   │   ├── db/
│   │   │   └── mongo.py            # MongoDB Atlas async connector (SSL-safe)
│   │   ├── models/
│   │   │   └── schemas.py          # Pydantic request/response models
│   │   ├── web/
│   │   │   └── index.html          # Full web app — served at GET /
│   │   └── data/
│   │       ├── stations.json       # 28 Western Line stations with real GPS coords
│   │       └── trains.json         # 388 trains (fast, semi, slow — both directions)
│   ├── requirements.txt
│   ├── .env.example
│   └── render.yaml                 # One-click Render.com deploy config
│
└── db/
    ├── seed.py                     # Manual MongoDB seeder (optional)
    └── schema.md                   # Collection schemas
```

---

## 🚀 Quick Start

```bash
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — add your MONGODB_URI and ANTHROPIC_API_KEY

# Run
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** in any browser.

**On your phone** (same WiFi): open `http://YOUR_PC_IP:8000`
Find your IP with `ipconfig` (Windows) or `ifconfig` (Mac/Linux).

---

## 🌐 Web App Screens

| Tab | Description |
|-----|-------------|
| 💬 **Chat** | Natural language AI assistant — ask anything about trains, crowd, routes |
| 🎯 **Plan** | Structured journey planner with dropdown station picker and crowd chart |
| 🗺 **Map** | Live Western Line map with animated train markers updating every 3 seconds |

No install required — works in Chrome, Firefox, Safari, and mobile browsers.

---

## 🔑 Environment Variables

All in `backend/.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Recommended | Enables Claude AI responses (`claude-sonnet-4`) |
| `OPENAI_API_KEY` | Optional | Fallback LLM if Anthropic not set |
| `MONGODB_URI` | Optional | Atlas connection string for query logging |
| `MONGODB_DB` | Optional | Database name (default: `railman`) |

> **No API keys?** The app works without them using the built-in rule-based engine.

---

## 📡 API Reference

### `POST /api/chat`
```json
{ "message": "Best train from Borivali to Churchgate at 9am", "session_id": "optional" }
```

### `POST /api/recommend`
```json
{ "source": "Borivali", "destination": "Churchgate", "time": "09:00", "preference": "balanced" }
```
`preference`: `fastest` | `least_crowded` | `balanced`

### `GET /api/live_trains`
Current position + crowd level for all active trains (up to 30 concurrent).

### `GET /api/stations`
All 28 Western Line stations with GPS coordinates.

### `GET /api/crowd_forecast?zone=central&train_type=slow`
24-hour crowd forecast array.

### `GET /api/analytics`
Usage stats, popular routes, feedback summary.

### `GET /health`
`{"status": "ok", "db": "connected"}` — use for uptime monitoring.

---

## 🌐 Deployment

See **DEPLOY.md** for the full step-by-step guide. Short version:

1. Push `backend/` to GitHub
2. Create a Web Service on [render.com](https://render.com)
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add `MONGODB_URI` and `ANTHROPIC_API_KEY` as env vars
4. Visit `https://your-service.onrender.com`

---

## 🧠 How It Works

### Crowd Prediction
Gaussian model centred on peak hours (9 AM, 7 PM) with zone multipliers, train-type multipliers, and a weekend discount (~35% less crowded).

### Recommendation Engine
Scores all 388 trains on crowd (0–100) and estimated travel time, weighted by user preference (fastest / least crowded / balanced).

### Train Simulator
5–30 trains active concurrently depending on time of day. Progress advances at ~28s per station segment; positions are interpolated between consecutive GPS coordinates and written to MongoDB's TTL-indexed `live_positions` collection.

### AI Engine Priority
1. **Anthropic Claude** (`claude-sonnet-4`) — primary
2. **OpenAI GPT-4o-mini** — fallback
3. **Rule-based engine** — works offline with no API keys

---

## 🗺 Western Line — 28 Stations

Churchgate · Marine Lines · Charni Road · Grant Road · Mumbai Central ·
Mahalaxmi · Lower Parel · Elphinstone Road · Dadar · Matunga Road ·
Mahim · Bandra · Khar Road · Santacruz · Vile Parle · **Andheri** ·
Jogeshwari · Goregaon · Malad · Kandivali · **Borivali** · Dahisar ·
Mira Road · Bhayandar · Naigaon · Vasai Road · Nalasopara · **Virar**

---

Built with ❤️ for Mumbai commuters
