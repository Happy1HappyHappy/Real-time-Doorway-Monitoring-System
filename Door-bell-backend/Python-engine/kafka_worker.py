"""
YOLO Inference Worker - pulls RTSP stream, runs YOLO + BoTSORT,
pushes annotated stream back to MediaMTX, sends detection JSON to Kafka.

Env vars:
  RTSP_INPUT               - input RTSP URL from MediaMTX (e.g. rtsp://mediamtx:8554/cam-01)
  RTSP_OUTPUT              - output RTSP URL for annotated stream (e.g. rtsp://mediamtx:8554/cam-01/annotated)
  CAMERA_ID                - camera identifier (e.g. cam-01)
  KAFKA_BOOTSTRAP_SERVERS  - Kafka broker (default: "kafka:29092")
  KAFKA_TOPIC_DETECTIONS   - output topic (default: "doorbell-detections")
  MODEL_PATH               - YOLO model path (default: "yolov8n.pt")
  CONFIDENCE               - detection confidence threshold (default: 0.35)
  OUTPUT_FPS               - annotated stream FPS (default: 15)
"""

import os
import sys
import json
import time
import subprocess
import numpy as np
import cv2
from datetime import datetime
from confluent_kafka import Producer, KafkaError
from ultralytics import YOLO
from reid_extractor import extract_embedding

# ── Config ───────────────────────────────────────────────────────────────────
RTSP_INPUT = os.environ.get("RTSP_INPUT", "rtsp://localhost:8554/cam-01")
RTSP_OUTPUT = os.environ.get("RTSP_OUTPUT", "rtsp://localhost:8554/cam-01/annotated")
CAMERA_ID = os.environ.get("CAMERA_ID", "cam-01")
KAFKA_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
TOPIC_DETECTIONS = os.environ.get("KAFKA_TOPIC_DETECTIONS", "doorbell-detections")
MODEL_PATH = os.environ.get("MODEL_PATH", "yolov8n.pt")
CONFIDENCE = float(os.environ.get("CONFIDENCE", "0.35"))
OUTPUT_FPS = int(os.environ.get("OUTPUT_FPS", "15"))


class InferenceWorker:
    def __init__(self):
        print(f"Loading YOLO model: {MODEL_PATH}")
        self.model = YOLO(MODEL_PATH)

        self.producer = Producer({
            "bootstrap.servers": KAFKA_SERVERS,
            "acks": "1",
            "compression.type": "lz4",
        })

        self._active_ids = set()
        self._ffmpeg_out = None
        self._frame_size = None

        print(f"Worker ready | input={RTSP_INPUT} output={RTSP_OUTPUT}")

    def _start_ffmpeg_output(self, width, height):
        """Start FFmpeg process to push annotated frames to MediaMTX."""
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(OUTPUT_FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", str(OUTPUT_FPS * 2),
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            RTSP_OUTPUT,
        ]
        self._ffmpeg_out = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._frame_size = (width, height)
        print(f"FFmpeg output started: {width}x{height} @ {OUTPUT_FPS}fps -> {RTSP_OUTPUT}")

    def _process_frame(self, frame: np.ndarray) -> list:
        """Run YOLO + BoTSORT, return annotated frame and new detections."""
        results = self.model.track(
            frame,
            persist=True,
            tracker="botsort.yaml",
            classes=[0],
            conf=CONFIDENCE,
            iou=0.5,
            verbose=False,
        )

        annotated = results[0].plot()
        boxes = results[0].boxes
        detections = []

        if boxes is not None and boxes.id is not None:
            current_ids = set(boxes.id.int().tolist())
            newly_seen = current_ids - self._active_ids
            self._active_ids = current_ids

            confs = boxes.conf.tolist()
            xywh = boxes.xywhn.tolist()

            for i, tid in enumerate(boxes.id.int().tolist()):
                if tid in newly_seen:
                    bbox = [round(v, 3) for v in xywh[i]]
                    embedding = extract_embedding(frame, xywh[i])
                    detections.append({
                        "trackId": tid,
                        "bbox": bbox,
                        "embedding": embedding,
                        "confidence": round(confs[i], 3),
                    })
        else:
            self._active_ids = set()

        return annotated, detections

    def run(self):
        """Main loop: pull RTSP -> infer -> push annotated RTSP + Kafka."""
        print(f"Connecting to {RTSP_INPUT}...")

        # Retry loop for RTSP connection (MediaMTX or Edge might not be ready)
        cap = None
        for attempt in range(30):
            cap = cv2.VideoCapture(RTSP_INPUT, cv2.CAP_FFMPEG)
            if cap.isOpened():
                break
            print(f"  Waiting for RTSP stream... (attempt {attempt + 1}/30)")
            time.sleep(2)

        if cap is None or not cap.isOpened():
            print(f"ERROR: Cannot open RTSP stream {RTSP_INPUT}")
            sys.exit(1)

        print(f"Connected to {RTSP_INPUT}")
        frame_count = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                # Start FFmpeg output on first frame (to get actual resolution)
                if self._ffmpeg_out is None:
                    h, w = frame.shape[:2]
                    self._start_ffmpeg_output(w, h)

                # Run inference
                annotated, detections = self._process_frame(frame)
                frame_count += 1

                # Push annotated frame to MediaMTX
                try:
                    self._ffmpeg_out.stdin.write(annotated.tobytes())
                except BrokenPipeError:
                    print("ERROR: FFmpeg output pipe broken")
                    break

                # Send new detections to Kafka
                if detections:
                    event = {
                        "cameraId": CAMERA_ID,
                        "timestamp": datetime.now().isoformat(),
                        "detections": detections,
                    }
                    self.producer.produce(
                        TOPIC_DETECTIONS,
                        key=CAMERA_ID,
                        value=json.dumps(event),
                    )
                    self.producer.poll(0)

                    for d in detections:
                        print(f"  [{CAMERA_ID}] NEW person trackId={d['trackId']} conf={d['confidence']}")

                if frame_count % 100 == 0:
                    print(f"  [{CAMERA_ID}] Processed {frame_count} frames")

        except KeyboardInterrupt:
            print("Shutting down worker...")
        finally:
            cap.release()
            self.producer.flush(timeout=5)
            if self._ffmpeg_out:
                self._ffmpeg_out.stdin.close()
                self._ffmpeg_out.wait(timeout=5)
            print(f"Worker stopped. Total frames: {frame_count}")


if __name__ == "__main__":
    worker = InferenceWorker()
    worker.run()
