"""
MongoDB Atlas Manual Seeder
----------------------------
Run this if you want to pre-populate the DB before deploying,
or to reset collections to the canonical dataset.

The FastAPI app also seeds automatically on startup if collections are empty.

Usage:
  cd railman/backend
  pip install -r requirements.txt
  MONGODB_URI="mongodb+srv://..." python ../db/seed.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import motor.motor_asyncio
    from pymongo import ASCENDING, DESCENDING
except ImportError:
    print("ERROR: pip install motor pymongo dnspython")
    sys.exit(1)

DATA = Path(__file__).parent.parent / "backend" / "app" / "data"


async def seed():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        print("ERROR: Set MONGODB_URI environment variable")
        sys.exit(1)

    print(f"Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    db = client[os.getenv("MONGODB_DB", "railman")]

    try:
        await db.command("ping")
        print("✅ MongoDB connection OK")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # ── Stations ─────────────────────────────────────────────────────────
    print("\n── Seeding stations...")
    with open(DATA / "stations.json") as f:
        stations = json.load(f)
    await db.stations.drop()
    await db.stations.insert_many(stations)
    await db.stations.create_index("id", unique=True)
    await db.stations.create_index("index")
    print(f"  ✅ {len(stations)} stations inserted")

    # ── Trains ───────────────────────────────────────────────────────────
    print("── Seeding trains...")
    with open(DATA / "trains.json") as f:
        trains = json.load(f)
    await db.trains.drop()
    await db.trains.insert_many(trains)
    await db.trains.create_index("id", unique=True)
    await db.trains.create_index([("type", ASCENDING), ("direction", ASCENDING)])
    await db.trains.create_index([("departs_hour", ASCENDING), ("departs_minute", ASCENDING)])
    fast  = sum(1 for t in trains if t["type"] == "fast")
    semi  = sum(1 for t in trains if t["type"] == "semi")
    slow  = sum(1 for t in trains if t["type"] == "slow")
    up    = sum(1 for t in trains if t["direction"] == 1)
    down  = sum(1 for t in trains if t["direction"] == -1)
    print(f"  ✅ {len(trains)} trains inserted")
    print(f"     Fast: {fast}  Semi: {semi}  Slow: {slow}")
    print(f"     UP (→Virar): {up}  DOWN (→Churchgate): {down}")

    # ── Live positions (TTL) ──────────────────────────────────────────────
    print("── Setting up live_positions collection...")
    await db.live_positions.drop()
    await db.live_positions.create_index("train_id", unique=True)
    await db.live_positions.create_index("updated_at", expireAfterSeconds=60)
    print("  ✅ TTL index created (60s expiry)")

    # ── Other collections ─────────────────────────────────────────────────
    print("── Creating indexes on logs, recommendations, feedback...")
    await db.logs.create_index("session_id")
    await db.logs.create_index([("timestamp", DESCENDING)])
    await db.recommendations.create_index("session_id")
    await db.recommendations.create_index([("timestamp", DESCENDING)])
    await db.recommendations.create_index(
        [("request.source", ASCENDING), ("request.destination", ASCENDING)]
    )
    await db.feedback.create_index("session_id")
    await db.feedback.create_index([("rating", ASCENDING)])
    print("  ✅ Indexes created")

    # ── Summary ────────────────────────────────────────────────────────────
    collections = await db.list_collection_names()
    print(f"\n🎉 Done! Collections in '{db.name}': {sorted(collections)}")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
