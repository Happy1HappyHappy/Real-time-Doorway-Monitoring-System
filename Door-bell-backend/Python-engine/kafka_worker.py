"""
Kafka Inference Worker - consumes compressed frames, runs YOLO + BoTSORT,
sends detection results to another Kafka topic.

Each worker handles frames from specific camera(s) via Kafka partition assignment.
BoTSORT tracking state is maintained per camera.

Env vars:
  KAFKA_BOOTSTRAP_SERVERS  - Kafka broker (default: "localhost:9092")
  KAFKA_TOPIC_FRAMES       - input topic (default: "doorbell-frames")
  KAFKA_TOPIC_DETECTIONS   - output topic (default: "doorbell-detections")
  KAFKA_GROUP_ID           - consumer group (default: "yolo-workers")
  MODEL_PATH               - YOLO model path (default: "yolov8n.pt")
  CONFIDENCE               - detection confidence threshold (default: 0.35)
"""

import os
import json
import base64
import numpy as np
import cv2
from datetime import datetime
from confluent_kafka import Consumer, Producer, KafkaError
from ultralytics import YOLO

# ── Config ───────────────────────────────────────────────────────────────────
KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_FRAMES = os.environ.get("KAFKA_TOPIC_FRAMES", "doorbell-frames")
TOPIC_DETECTIONS = os.environ.get("KAFKA_TOPIC_DETECTIONS", "doorbell-detections")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "yolo-workers")
MODEL_PATH = os.environ.get("MODEL_PATH", "yolov8n.pt")
CONFIDENCE = float(os.environ.get("CONFIDENCE", "0.35"))


class InferenceWorker:
    def __init__(self):
        print(f"Loading YOLO model: {MODEL_PATH}")
        self.model = YOLO(MODEL_PATH)

        self.consumer = Consumer({
            "bootstrap.servers": KAFKA_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",     # skip old frames, only process new ones
            "enable.auto.commit": True,
            "max.poll.interval.ms": "300000",
        })
        self.consumer.subscribe([TOPIC_FRAMES])

        self.producer = Producer({
            "bootstrap.servers": KAFKA_SERVERS,
            "acks": "1",
            "compression.type": "lz4",
        })

        # Track active IDs per camera (for BoTSORT state)
        self._active_ids = {}  # cameraId -> set of track IDs

        print(f"Worker ready | consuming={TOPIC_FRAMES} producing={TOPIC_DETECTIONS}")

    def _decode_frame(self, image_b64: str) -> np.ndarray:
        """Decode base64 JPEG to OpenCV frame."""
        jpeg_bytes = base64.b64decode(image_b64)
        np_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    def _process_frame(self, camera_id: str, frame: np.ndarray) -> list:
        """Run YOLO + BoTSORT, return list of new detections."""
        results = self.model.track(
            frame,
            persist=True,
            tracker="botsort.yaml",
            classes=[0],            # person only
            conf=CONFIDENCE,
            iou=0.5,
            verbose=False,
        )

        boxes = results[0].boxes
        detections = []

        if boxes is not None and boxes.id is not None:
            current_ids = set(boxes.id.int().tolist())
            prev_ids = self._active_ids.get(camera_id, set())
            newly_seen = current_ids - prev_ids
            self._active_ids[camera_id] = current_ids

            confs = boxes.conf.tolist()
            xywh = boxes.xywhn.tolist()

            for i, tid in enumerate(boxes.id.int().tolist()):
                if tid in newly_seen:
                    detections.append({
                        "trackId": tid,
                        "bbox": [round(v, 3) for v in xywh[i]],
                        "embedding": [],    # TODO: add ReID embedding
                        "confidence": round(confs[i], 3),
                    })
        else:
            self._active_ids[camera_id] = set()

        return detections

    def run(self):
        """Main consume-process-produce loop."""
        print("Worker started, waiting for frames...")
        frame_count = 0

        try:
            while True:
                msg = self.consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    print(f"Kafka error: {msg.error()}")
                    continue

                # Parse message
                data = json.loads(msg.value().decode("utf-8"))
                camera_id = data["cameraId"]
                frame = self._decode_frame(data["image"])

                # Run inference
                detections = self._process_frame(camera_id, frame)
                frame_count += 1

                # Only send if new persons detected
                if detections:
                    event = {
                        "cameraId": camera_id,
                        "timestamp": datetime.now().isoformat(),
                        "detections": detections,
                    }
                    self.producer.produce(
                        TOPIC_DETECTIONS,
                        key=camera_id,
                        value=json.dumps(event),
                    )
                    self.producer.poll(0)

                    for d in detections:
                        print(f"  [{camera_id}] NEW person trackId={d['trackId']} conf={d['confidence']}")

                if frame_count % 100 == 0:
                    print(f"  Processed {frame_count} frames")

        except KeyboardInterrupt:
            print("Shutting down worker...")
        finally:
            self.consumer.close()
            self.producer.flush(timeout=5)
            print(f"Worker stopped. Total frames processed: {frame_count}")


if __name__ == "__main__":
    worker = InferenceWorker()
    worker.run()
