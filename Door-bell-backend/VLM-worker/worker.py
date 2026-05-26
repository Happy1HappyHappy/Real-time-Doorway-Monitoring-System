"""
Authors: Claire Liu, Yu-Jing Wei
Description: VLM Worker that consumes analysis-requests (person crops from yolo-worker),
calls Ollama for description and threat-level assessment, and publishes analysis-results.

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
from confluent_kafka import Consumer, Producer, KafkaException

KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
TOPIC_REQUESTS = os.environ.get("KAFKA_TOPIC_ANALYSIS_REQUESTS", "doorbell-analysis-requests")
TOPIC_RESULTS = os.environ.get("KAFKA_TOPIC_ANALYSIS_RESULTS", "doorbell-analysis-results")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b")
VLM_TIMEOUT_SEC = int(os.environ.get("VLM_TIMEOUT_SEC", "120"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")

PROMPT = (
    "You are a residential doorbell security analyst. Describe the person in ONE short "
    "sentence covering what they are doing and what they are holding (if anything). "
    "Only mention an item if you can CLEARLY SEE it in their hand — do not guess.\n\n"
    "Threat level rules:\n"
    "  - \"safe\": ordinary visitor, empty hands, or holding ordinary items (package, "
    "bag, phone, flowers, coffee cup, keys, pen, pencil).\n"
    "  - \"watch\": wearing a hat, cap, hood, mask, scarf, or sunglasses (any face or "
    "head covering); loitering; peering in; pacing; turned away.\n"
    "  - \"alert\": clearly carrying a real weapon (knife, gun, bat, club, crowbar, "
    "metal pipe) OR carrying an umbrella (treated as a potential weapon here); "
    "running; hiding; forcing entry.\n\n"
    "IMPORTANT: A pen, pencil, or stylus is an ordinary office item and is ALWAYS "
    '"safe", NEVER "alert". Wearing a hat or a mask is ALWAYS at least "watch".\n\n'
    "Do NOT invent objects. If you cannot clearly see something in the hand, say "
    '"empty hands".\n\n'
    "Respond with STRICT JSON only, no markdown, no extra text:\n"
    '{"description": "...", "threat_level": "safe|watch|alert", "reason": "..."}'
)


# Threat-level override rules. Kept at module scope so unit tests can import them
# and verify the rules independently of the VLM HTTP call.
SAFE_ITEMS = ("pen", "pencil", "stylus", "marker")
ALERT_KEYWORDS = ("knife", "gun", "pistol", "firearm", "bat", "crowbar", "weapon", "blade", "umbrella")
WATCH_KEYWORDS = ("hat", "cap", "hood", "mask", "scarf", "sunglasses", "beanie", "helmet")


def _contains_word(text: str, word: str) -> bool:
    """Whole-word-ish match: avoids matching 'pencil' inside 'pencilcase' etc.
    Uses space-padding instead of regex for speed and to mirror original logic."""
    padded = f" {text} "
    return f" {word} " in padded or f" {word}." in padded or f" {word}," in padded


def apply_threat_overrides(level: str, description: str, reason: str) -> tuple:
    """Pure function: apply rule-based threat-level overrides on top of VLM output.

    Returns (level, reason). Extracted so we can unit-test the security rules
    without mocking the VLM HTTP call.
    """
    if level not in ("safe", "watch", "alert"):
        level = "safe"
    desc_lower = description.lower()

    # Hard DOWNGRADE: pen / pencil / stylus are ordinary items, force to safe.
    if level == "alert":
        for kw in SAFE_ITEMS:
            if _contains_word(desc_lower, kw):
                return "safe", f"auto-downgraded: '{kw}' is an ordinary item"

    # Hard ESCALATE: genuine weapons + umbrella.
    for kw in ALERT_KEYWORDS:
        if _contains_word(desc_lower, kw):
            if level != "alert":
                reason = (reason + f" | auto-escalated: detected '{kw}' in description").strip(" |")
            return "alert", reason

    # Hard ESCALATE to at least watch: hat or mask.
    if level == "safe":
        for kw in WATCH_KEYWORDS:
            if _contains_word(desc_lower, kw):
                reason = (reason + f" | auto-escalated: detected '{kw}' in description").strip(" |")
                return "watch", reason

    return level, reason


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
        description = str(parsed.get("description", ""))[:300]
        reason = str(parsed.get("reason", ""))[:300]

        level, reason = apply_threat_overrides(level, description, reason)

        return {
            "description": description,
            "threatLevel": level,
            "suspicious": level == "alert",
            "reason": reason,
            "latencyMs": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {"error": f"{type(e).__name__}: {e}", "latencyMs": elapsed_ms}


def _delivery_report(err, msg):
    """Log Kafka delivery failures so analysis results are never silently dropped."""
    if err is not None:
        print(
            f"KAFKA DELIVERY FAILED topic={msg.topic()} key={msg.key()} "
            f"err={err.code()} {err.str()}"
        )


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
            motion_hint = req.get("motionHint")
            if not image_b64:
                print(f"Skip {camera_id}:{track_id} — no image")
                continue

            print(f"[{camera_id}] VLM analysing trackId={track_id}... motion={motion_hint}")
            result = call_ollama(image_b64)

            # Force alert if motion indicates running.
            if motion_hint == "running" and "error" not in result:
                if result.get("threatLevel") != "alert":
                    prev_reason = result.get("reason", "")
                    result["reason"] = (prev_reason + " | motion: running detected").strip(" |")
                result["threatLevel"] = "alert"
                result["suspicious"] = True

            out = {
                "type": "analysis-result",
                "cameraId": camera_id,
                "trackId": track_id,
                "requestTimestamp": req.get("timestamp"),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **result,
            }
            try:
                producer.produce(
                    TOPIC_RESULTS,
                    key=f"{camera_id}:{track_id}",
                    value=json.dumps(out),
                    callback=_delivery_report,
                )
                producer.poll(0)
            except BufferError:
                # Local queue full — drain and drop this result rather than block
                # the consumer; we'd rather lose one analysis than stop processing.
                print(f"KAFKA BUFFER FULL on {TOPIC_RESULTS}, dropping result for {camera_id}:{track_id}")
                producer.poll(0.1)
            except KafkaException as e:
                print(f"KAFKA PRODUCE EXCEPTION on {TOPIC_RESULTS}: {e}")
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
