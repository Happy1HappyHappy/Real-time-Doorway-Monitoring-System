"""
VLM Worker - consumes analysis-requests (person crops from yolo-worker),
calls Ollama for description + anomaly assessment, publishes analysis-results.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS       - default: "kafka:29092"
  KAFKA_TOPIC_ANALYSIS_REQUESTS - default: "doorbell-analysis-requests"
  KAFKA_TOPIC_ANALYSIS_RESULTS  - default: "doorbell-analysis-results"
  OLLAMA_URL                    - default: "http://host.docker.internal:11434"
  OLLAMA_MODEL                  - default: "qwen2.5vl:3b"
  VLM_TIMEOUT_SEC               - default: 30
"""

import os
import json
import time
import requests
from confluent_kafka import Consumer, Producer

KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
TOPIC_REQUESTS = os.environ.get("KAFKA_TOPIC_ANALYSIS_REQUESTS", "doorbell-analysis-requests")
TOPIC_RESULTS = os.environ.get("KAFKA_TOPIC_ANALYSIS_RESULTS", "doorbell-analysis-results")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b")
VLM_TIMEOUT_SEC = int(os.environ.get("VLM_TIMEOUT_SEC", "120"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")

PROMPT = (
    "You are a residential doorbell security analyst. Describe the person in ONE short sentence "
    "covering what they appear to be DOING (e.g. knocking, waiting, looking around, carrying "
    "package, walking past, running). Then assign a threat level using THESE EXACT rules:\n"
    "  - \"safe\": ordinary visitor, clearly visible face, normal posture, empty hands or "
    "ordinary items (package, bag, phone, flowers).\n"
    "  - \"watch\": wearing a hat, cap, hood, mask, scarf, or sunglasses that partially "
    "conceal the face; loitering; peering in; pacing; turned away; unclear intent.\n"
    "  - \"alert\": holding ANY elongated hand-held object such as a pencil, pen, stick, "
    "rod, bat, club, bar, pipe, knife, gun, crowbar, screwdriver, or tool; running; "
    "attempting to hide; forcing entry.\n\n"
    "Any thin stick-like object in the hand (even a pencil or pen) counts as \"alert\". "
    "Do NOT output \"alert\" just because a hat or mask is worn — that is \"watch\".\n\n"
    "Respond with STRICT JSON only, no markdown, no extra text:\n"
    '{"description": "...", "threat_level": "safe|watch|alert", "reason": "..."}'
)


def call_ollama(image_b64: str) -> dict:
    """Call Ollama VLM; return parsed dict or error payload."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": PROMPT,
        "images": [image_b64],
        "stream": False,
        "format": "json",
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {"temperature": 0.2},
    }
    t0 = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=VLM_TIMEOUT_SEC)
        r.raise_for_status()
        data = r.json()
        elapsed_ms = int((time.time() - t0) * 1000)
        raw = data.get("response", "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": f"invalid JSON from VLM: {raw[:200]}", "latencyMs": elapsed_ms}
        level = str(parsed.get("threat_level", "safe")).lower().strip()
        if level not in ("safe", "watch", "alert"):
            level = "safe"
        return {
            "description": str(parsed.get("description", ""))[:300],
            "threatLevel": level,
            "suspicious": level == "alert",
            "reason": str(parsed.get("reason", ""))[:300],
            "latencyMs": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {"error": f"{type(e).__name__}: {e}", "latencyMs": elapsed_ms}


def warmup_ollama():
    """Preload the model into memory so the first real request isn't slow."""
    try:
        print(f"Warming up {OLLAMA_MODEL} at {OLLAMA_URL} (this may take 30-60s on first run)...")
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": "", "keep_alive": OLLAMA_KEEP_ALIVE, "stream": False},
            timeout=300,
        )
        r.raise_for_status()
        print("Warmup complete.")
    except Exception as e:
        print(f"Warmup failed (will retry on first real request): {e}")


def main():
    warmup_ollama()

    consumer = Consumer({
        "bootstrap.servers": KAFKA_SERVERS,
        "group.id": "vlm-worker",
        "auto.offset.reset": "latest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([TOPIC_REQUESTS])

    producer = Producer({
        "bootstrap.servers": KAFKA_SERVERS,
        "acks": "1",
        "compression.type": "lz4",
    })

    print(f"VLM worker ready | ollama={OLLAMA_URL} model={OLLAMA_MODEL}")
    print(f"  consuming: {TOPIC_REQUESTS}")
    print(f"  producing: {TOPIC_RESULTS}")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"Kafka error: {msg.error()}")
                continue

            try:
                req = json.loads(msg.value())
            except Exception as e:
                print(f"Bad message: {e}")
                continue

            camera_id = req.get("cameraId")
            track_id = req.get("trackId")
            image_b64 = req.get("imageJpegB64")
            if not image_b64:
                print(f"Skip {camera_id}:{track_id} — no image")
                continue

            print(f"[{camera_id}] VLM analysing trackId={track_id}...")
            result = call_ollama(image_b64)

            out = {
                "type": "analysis-result",
                "cameraId": camera_id,
                "trackId": track_id,
                "requestTimestamp": req.get("timestamp"),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **result,
            }
            producer.produce(
                TOPIC_RESULTS,
                key=f"{camera_id}:{track_id}",
                value=json.dumps(out),
            )
            producer.poll(0)
            if "error" in result:
                print(f"[{camera_id}] VLM FAIL track={track_id} | {result['error']} | {result['latencyMs']}ms")
            else:
                level = result.get("threatLevel", "safe")
                badge = {"alert": "🔴 ALERT", "watch": "🟡 WATCH", "safe": "🟢 SAFE"}.get(level, level)
                print(f"[{camera_id}] VLM DONE track={track_id} | {badge} | {result['latencyMs']}ms | {result.get('description','')[:80]}")
    except KeyboardInterrupt:
        print("Shutting down VLM worker...")
    finally:
        consumer.close()
        producer.flush(timeout=5)


if __name__ == "__main__":
    main()
