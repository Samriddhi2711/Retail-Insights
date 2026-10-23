import json
import httpx
import sys

API_URL = "http://localhost:8000/events/ingest"
BATCH_SIZE = 500

def load_events():
    events = []
    with open("output/events.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    if not events:
        print("No events found in output/events.jsonl")
        return

    print(f"Loaded {len(events)} events from file. Sending to API...")

    inserted = 0
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(events), BATCH_SIZE):
            batch = events[i : i + BATCH_SIZE]
            try:
                response = client.post(API_URL, json=batch)
                response.raise_for_status()
                data = response.json()
                inserted += data.get("accepted", 0)
                print(f"Batch {i // BATCH_SIZE + 1}: {data['accepted']} accepted, {data['duplicates']} duplicates, {data['rejected']} rejected")
            except Exception as e:
                print(f"ERROR on batch {i // BATCH_SIZE + 1}: {e}")

    print(f"\nTotal inserted successfully: {inserted}")

if __name__ == "__main__":
    load_events()