# TrainMan AI

> **Smart Mumbai Railway Assistant**  AI-powered chatbot and real-time information platform for Western, Central & Harbour railway lines.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python)](https://python.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas-47A248?style=flat-square&logo=mongodb)](https://www.mongodb.com)

---

## 📖 Overview

TrainMan AI is a full-stack intelligent railway assistant that helps Mumbai commuters get real-time train information, crowd forecasts, journey recommendations, and natural-language answers all in one place.

It combines a **FastAPI backend** with a **single-page web frontend** (served from the same process), an **AI chat engine** backed by a local LLM or rule-based fallback, and a **MongoDB** data layer for trains, chat history, and analytics.

---

##  Features

| Feature | Description |
|---|---|
|  **AI Chat** | Natural-language Q&A about trains, fares, timings, and routes |
|  **Smart Recommendations** | Optimal train suggestions based on source, destination, time & preference |
|  **Live Train Tracker** | Simulated real-time positions for up to 30 concurrent trains |
|  **Crowd Forecast** | 24-hour crowd prediction charts for Western, Central & Harbour lines |
|  **Train Catalogue** | Full timetable lookup across all three lines |
|  **Auth System** | JWT-based user authentication (register / login) |
|  **Chat History** | Persistent per-session conversation memory with MongoDB |
|  **Rate Limiting** | 30 requests/minute per IP on the chat endpoint |
|  **Hot-reload KB** | Invalidate knowledge base cache without restarting the server |

---

##  Architecture

```
RailMan-main/
├── app/
│   ├── api/               # FastAPI route handlers
│   │   ├── chat.py        # Chat, recommend, feedback, history endpoints
│   │   ├── trains.py      # Live trains, crowd forecast, train catalogue
│   │   ├── stations.py    # Station lookup
│   │   ├── analytics.py   # Usage analytics
│   │   └── auth.py        # Register / login / JWT
│   ├── services/          # Core business logic
│   │   ├── ai_engine.py           # Main query handler & LLM orchestration
│   │   ├── recommendation_engine.py # Route & train recommendation logic
│   │   ├── knowledge_base.py      # Static KB loader (JSON-backed)
│   │   ├── llm_runtime.py         # Local LLM (llama.cpp / GGUF) runtime
│   │   ├── context_resolver.py    # NLP entity extraction & context
│   │   ├── crowd_engine.py        # Crowd prediction model
│   │   ├── rail_network.py        # Railway graph / network utilities
│   │   ├── simulator.py           # Live train position simulator
│   │   └── time_utils.py          # IST time helpers
│   ├── db/
│   │   ├── mongo.py       # MongoDB connection lifecycle
│   │   ├── trains_db.py   # Trains collection queries
│   │   └── chat_db.py     # Chat history, feedback, rate-limiting
│   ├── models/
│   │   └── schemas.py     # Pydantic request/response models
│   ├── data/              # Static JSON knowledge files
│   │   ├── trains.json            # Full Mumbai train timetable
│   │   ├── stations.json          # Station metadata
│   │   ├── station_aliases.json   # Alternate station name mappings
│   │   ├── chatbot_knowledge.json # FAQ & factual KB
│   │   └── chatbot_dialogues.json # Scripted dialogue flows
│   └── main.py            # FastAPI app entry-point
├── models/
│   └── railman-chat.gguf  # Local GGUF model (place your model here)
├── ui/
│   └── index.html         # Single-page frontend (served by FastAPI)
├── tests/
│   ├── test_chat_followups.py
│   └── test_recommendation_engine.py
├── .env.example           # Environment variable template
├── requirements.txt
└── Procfile               # Heroku / Railway.app deployment
```

---

##  Getting Started

### Prerequisites

- Python 3.10+
- MongoDB Atlas account (or a local MongoDB instance)
- *(Optional)* A GGUF-format LLM model for local inference

### 1. Clone the repository

```bash
git clone https://github.com/harshitkhandelwal208/RailMan.git
cd RailMan
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM Settings (set LOCAL_LLM_ENABLED=0 to use rule-based fallback only)
LOCAL_LLM_ENABLED=1
LOCAL_LLM_MODEL_PATH=models/railman-chat.gguf
LOCAL_LLM_N_CTX=4096
LOCAL_LLM_THREADS=6
LOCAL_LLM_GPU_LAYERS=0
LOCAL_LLM_MAX_TOKENS=320
LOCAL_LLM_TEMPERATURE=0.2
LOCAL_LLM_TOP_P=0.9

# Provider order: try local first, then fall back to rule-based
RAILMAN_LLM_PROVIDER_ORDER=local,rule_based

# MongoDB — two separate databases for trains and chat
MONGODB_TRAINS_URI=mongodb+srv://<user>:<pass>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_TRAINS_DB=railman_trains
MONGODB_CHAT_URI=mongodb+srv://<user>:<pass>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_CHAT_DB=railman_chat

# Auth
JWT_SECRET=change-this-to-a-long-random-string-in-production

# Server
PORT=8000
```

### 5. (Optional) Add a local LLM model

Place a GGUF-format model file at the path specified by `LOCAL_LLM_MODEL_PATH`:

```
models/railman-chat.gguf
```

If no model is present, RailMan automatically falls back to the rule-based engine.

### 6. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

Open your browser at **http://localhost:8000** to access the UI, or **http://localhost:8000/docs** for the interactive API docs.

---

## 📡 API Reference

All endpoints are prefixed with `/api`.

### Chat & AI

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat` | Send a message and get an AI response |
| `POST` | `/api/recommend` | Get a structured train recommendation |
| `POST` | `/api/feedback` | Submit a rating/comment for a session |
| `POST` | `/api/clear_memory` | Clear conversation history for a session |
| `GET` | `/api/chat/history/{session_id}` | Retrieve stored messages |
| `GET` | `/api/chat/status` | LLM runtime + knowledge base diagnostics |

#### Example — `/api/chat`

```json
POST /api/chat
{
  "message": "Which fast train goes from Dadar to Churchgate before 9 AM?",
  "session_id": "abc-123",
  "user_id": "optional-user-id"
}
```

#### Example — `/api/recommend`

```json
POST /api/recommend
{
  "source": "Dadar",
  "destination": "Churchgate",
  "time": "08:45",
  "preference": "fast"
}
```

---

### Trains & Stations

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/live_trains` | Live positions of active trains (simulated) |
| `GET` | `/api/crowd_forecast` | 24-hour crowd data (`?zone=central&train_type=slow`) |
| `GET` | `/api/train_catalogue` | Full timetable (`?line=western\|central\|harbour`) |
| `GET` | `/api/stations` | Station list and metadata |

---

### Auth

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create a new user account |
| `POST` | `/api/auth/login` | Log in and receive a JWT token |

---

### System

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Full health check (DB, LLM, KB status) |
| `GET` | `/ping` | Lightweight liveness check (plain text `ok`) |
| `POST` | `/api/admin/invalidate_cache` | Hot-reload the knowledge base from disk |

---

##  Running Tests

```bash
pytest tests/
```

The test suite covers:
- **`test_chat_followups.py`** — multi-turn conversation and context tracking
- **`test_recommendation_engine.py`** — route recommendation correctness

---

##  Deployment

### Heroku / Railway.app

The included `Procfile` is ready for PaaS deployment:

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set all environment variables from `.env.example` in your hosting dashboard.

> **Note:** If using a local LLM on a PaaS platform, ensure the GGUF model is bundled in your repo or fetched at build time. For cloud deployments without GPU, set `LOCAL_LLM_ENABLED=0` and use `RAILMAN_LLM_PROVIDER_ORDER=rule_based`.

---

##  Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `LOCAL_LLM_ENABLED` | `1` | Enable (`1`) or disable (`0`) the local LLM |
| `LOCAL_LLM_MODEL_PATH` | `models/railman-chat.gguf` | Path to the GGUF model file |
| `LOCAL_LLM_N_CTX` | `4096` | Context window size (tokens) |
| `LOCAL_LLM_THREADS` | `6` | CPU threads for inference |
| `LOCAL_LLM_GPU_LAYERS` | `0` | GPU layers to offload (0 = CPU only) |
| `LOCAL_LLM_MAX_TOKENS` | `320` | Max tokens per response |
| `LOCAL_LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `LOCAL_LLM_TOP_P` | `0.9` | Nucleus sampling threshold |
| `RAILMAN_LLM_PROVIDER_ORDER` | `local,rule_based` | Comma-separated provider fallback chain |
| `MONGODB_TRAINS_URI` | — | MongoDB URI for train data |
| `MONGODB_TRAINS_DB` | `railman_trains` | Database name for train data |
| `MONGODB_CHAT_URI` | — | MongoDB URI for chat data |
| `MONGODB_CHAT_DB` | `railman_chat` | Database name for chat history |
| `JWT_SECRET` | — | Secret key for signing JWT tokens |
| `PORT` | `8000` | Port the server listens on |

---

##  Tech Stack

- **Backend**: [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org)
- **Database**: [MongoDB Atlas](https://www.mongodb.com/atlas) via [Motor](https://motor.readthedocs.io) (async)
- **AI / LLM**: Local GGUF inference via `llama-cpp-python`, with rule-based fallback
- **Auth**: [python-jose](https://github.com/mpdavis/python-jose) (JWT) + [bcrypt](https://github.com/pyca/bcrypt) password hashing
- **Data Validation**: [Pydantic v2](https://docs.pydantic.dev)
- **Frontend**: Vanilla HTML/CSS/JS served as a static SPA from `ui/index.html`

---

##  Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit your changes: `git commit -m "feat: add your feature"`
4. Push to the branch: `git push origin feat/your-feature`
5. Open a Pull Request

---

##  License

This project is open source. See [LICENSE](LICENSE) for details.

---

<div align="center">
  Made with ❤️ for Mumbai commuters
</div>
