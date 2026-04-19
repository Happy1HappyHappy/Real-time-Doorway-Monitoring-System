"""
Send synthetic detection events to Kafka to measure Java backend latency.
Simulates 3 different people with slight embedding variations for Re-ID testing.
"""
import json
import time
import random
import numpy as np
from confluent_kafka import Producer

KAFKA_SERVERS = "localhost:9092"
TOPIC = "doorbell-detections"

producer = Producer({"bootstrap.servers": KAFKA_SERVERS, "acks": "1"})

# Generate 3 "base" person embeddings (512-dim, L2-normalized)
rng = np.random.RandomState(42)
base_embeddings = []
for _ in range(3):
    v = rng.randn(512).astype(np.float64)
    v /= np.linalg.norm(v)
    base_embeddings.append(v)

def noisy_embedding(base, noise_level=0.01):
    """Add small noise to simulate same person from different angle.
    noise_level=0.01 keeps cosine similarity ~0.97 (well above 0.75 threshold).
    """
    v = base + rng.randn(512) * noise_level
    v /= np.linalg.norm(v)
    return v.tolist()

print("=== Sending synthetic detections to Kafka ===")
print(f"  3 persons x 5 appearances each = 15 events")
print()

for round_num in range(5):
    for person_idx in range(3):
        event = {
            "cameraId": f"cam-0{(person_idx % 2) + 1}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{random.randint(0,999999):06d}",
            "kafkaProducedAt": int(time.time() * 1000),
            "detections": [
                {
                    "trackId": person_idx * 100 + round_num,
                    "bbox": [round(random.uniform(0.1, 0.9), 3) for _ in range(4)],
                    "embedding": noisy_embedding(base_embeddings[person_idx]),
                    "confidence": round(random.uniform(0.7, 0.98), 3),
                }
            ],
        }
        producer.produce(TOPIC, key=event["cameraId"], value=json.dumps(event))
        producer.poll(0)
        print(f"  Sent: round={round_num} person={person_idx} camera={event['cameraId']}")
        time.sleep(0.3)  # small gap so logs are readable

producer.flush(timeout=5)
print()
print("=== Done! Check java-backend logs for [LATENCY] and [RE-ID] tags ===")
