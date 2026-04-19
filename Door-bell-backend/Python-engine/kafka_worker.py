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
  RTSP_CONNECT_RETRIES     - RTSP connect retries, 0 means infinite (default: 0)
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
CONFIDENCE = float(os.environ.get("CONFIDENCE", "0.50"))
OUTPUT_FPS = int(os.environ.get("OUTPUT_FPS", "15"))
RTSP_CONNECT_RETRIES = int(os.environ.get("RTSP_CONNECT_RETRIES", "0"))
DEVICE = os.environ.get("DEVICE", "auto")
EMBEDDING_FRAMES = int(os.environ.get("EMBEDDING_FRAMES", "5"))


def _average_embeddings(embeddings: list) -> list:
    """Average multiple embeddings and L2-normalize the result."""
    arr = np.array(embeddings)
    avg = arr.mean(axis=0)
    norm = np.linalg.norm(avg)
    if norm > 0:
        avg = avg / norm
    return avg.tolist()


def _resolve_device() -> str:
    if DEVICE != "auto":
        return DEVICE
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class InferenceWorker:
    def __init__(self):
        self.device = _resolve_device()
        print(f"Loading YOLO model: {MODEL_PATH} on device={self.device}")
        self.model = YOLO(MODEL_PATH)

        self.producer = Producer({
            "bootstrap.servers": KAFKA_SERVERS,
            "acks": "1",
            "compression.type": "lz4",
        })

        self._active_ids = set()
        self._pending: dict = {}  # tid -> {embeddings, bbox, confidence}
        self._ffmpeg_out = None
        self._frame_size = None

        print(f"Worker ready | input={RTSP_INPUT} output={RTSP_OUTPUT}")

    def _open_rtsp_input(self):
        """Connect to the RTSP input, waiting forever by default."""
        attempt = 0

        while True:
            attempt += 1
            cap = cv2.VideoCapture(RTSP_INPUT, cv2.CAP_FFMPEG)
            if cap.isOpened():
                return cap

            try:
                cap.release()
            except Exception:
                pass

            if RTSP_CONNECT_RETRIES > 0:
                print(f"  Waiting for RTSP stream... (attempt {attempt}/{RTSP_CONNECT_RETRIES})")
                if attempt >= RTSP_CONNECT_RETRIES:
                    return None
            else:
                print(f"  Waiting for RTSP stream... (attempt {attempt})")

            time.sleep(2)

    def _start_ffmpeg_output(self, width, height):
        cmd = [
            "/usr/bin/ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(OUTPUT_FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
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
        t0 = time.time()
        results = self.model.track(
            frame,
            persist=True,
            tracker="botsort.yaml",
            classes=[0],
            conf=CONFIDENCE,
            iou=0.5,
            verbose=False,
            device=self.device,
        )
        t_yolo = (time.time() - t0) * 1000

        annotated = results[0].plot()
        boxes = results[0].boxes
        detections = []

        t_reid_total = 0.0
        left_ids = set()
        if boxes is not None and boxes.id is not None:
            current_ids = set(boxes.id.int().tolist())
            newly_seen = current_ids - self._active_ids
            left_ids = self._active_ids - current_ids
            self._active_ids = current_ids

            confs = boxes.conf.tolist()
            xywh = boxes.xywhn.tolist()
            id_list = boxes.id.int().tolist()

            # Initialize pending buffer for newly appeared tracks
            for i, tid in enumerate(id_list):
                if tid in newly_seen:
                    self._pending[tid] = {
                        "embeddings": [],
                        "bbox": [round(v, 3) for v in xywh[i]],
                        "confidence": round(confs[i], 3),
                    }

            # Collect one embedding per frame for all pending tracks
            for i, tid in enumerate(id_list):
                if tid not in self._pending:
                    continue
                pending = self._pending[tid]
                if len(pending["embeddings"]) >= EMBEDDING_FRAMES:
                    continue

                t_re = time.time()
                embedding = extract_embedding(frame, xywh[i])
                t_reid_total += (time.time() - t_re) * 1000

                if embedding:
                    pending["embeddings"].append(embedding)

                # Enough frames collected — finalize this track
                if len(pending["embeddings"]) >= EMBEDDING_FRAMES:
                    avg_emb = _average_embeddings(pending["embeddings"])
                    detections.append({
                        "trackId": tid,
                        "bbox": pending["bbox"],
                        "embedding": avg_emb,
                        "confidence": pending["confidence"],
                    })
                    print(f"  [{CAMERA_ID}] REID finalized trackId={tid} frames={EMBEDDING_FRAMES}")
                    del self._pending[tid]

            # Flush tracks that left before collecting enough frames
            for tid in left_ids:
                if tid in self._pending:
                    pending = self._pending.pop(tid)
                    if pending["embeddings"]:
                        avg_emb = _average_embeddings(pending["embeddings"])
                        detections.append({
                            "trackId": tid,
                            "bbox": pending["bbox"],
                            "embedding": avg_emb,
                            "confidence": pending["confidence"],
                        })
                        print(f"  [{CAMERA_ID}] REID flushed trackId={tid} frames={len(pending['embeddings'])}/{EMBEDDING_FRAMES}")
        else:
            left_ids = set(self._active_ids)
            self._active_ids = set()
            # Flush all pending tracks when no boxes detected
            for tid, pending in list(self._pending.items()):
                if pending["embeddings"]:
                    avg_emb = _average_embeddings(pending["embeddings"])
                    detections.append({
                        "trackId": tid,
                        "bbox": pending["bbox"],
                        "embedding": avg_emb,
                        "confidence": pending["confidence"],
                    })
            self._pending.clear()

        if detections or t_reid_total > 0:
            print(f"  [{CAMERA_ID}] TIMING | yolo={t_yolo:.0f}ms | reid={t_reid_total:.0f}ms | total_python={t_yolo + t_reid_total:.0f}ms")

        return annotated, detections, left_ids

    def run(self):
        """Main loop: pull RTSP -> infer -> push annotated RTSP + Kafka."""
        print(f"Connecting to {RTSP_INPUT}...")

        cap = self._open_rtsp_input()
        if cap is None or not cap.isOpened():
            print(f"ERROR: Cannot open RTSP stream {RTSP_INPUT}")
            sys.exit(1)

        # Log stream info
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"Connected to {RTSP_INPUT}")
        print(f"  [{CAMERA_ID}] Stream info: {w}x{h} @ {fps:.1f}fps")

        # Read first frame to confirm we can actually decode
        ret, first_frame = cap.read()
        if ret:
            print(f"  [{CAMERA_ID}] First frame received: shape={first_frame.shape} dtype={first_frame.dtype}")
        else:
            print(f"  [{CAMERA_ID}] WARNING: Connected but cannot read first frame!")

        frame_count = 0
        last_log_time = time.time()
        dropped_frames = 0

        try:
            while True:
                # Drain buffer — always grab the latest frame to avoid lag
                ret = cap.grab()
                if not ret:
                    dropped_frames += 1
                    if dropped_frames % 50 == 1:
                        print(f"  [{CAMERA_ID}] RTSP read failed (dropped={dropped_frames}), reconnecting...")
                    cap.release()
                    cap = self._open_rtsp_input()
                    if cap is None or not cap.isOpened():
                        print(f"ERROR: Cannot reopen RTSP stream {RTSP_INPUT}")
                        break
                    continue
                # Skip stale buffered frames, only decode the newest one
                for _ in range(4):
                    cap.grab()
                ret, frame = cap.retrieve()
                if not ret:
                    dropped_frames += 1
                    continue

                # Start FFmpeg output on first frame (to get actual resolution)
                if self._ffmpeg_out is None:
                    h, w = frame.shape[:2]
                    self._start_ffmpeg_output(w, h)

                t_infer_start = time.time()

                # Run inference
                annotated, detections, left_ids = self._process_frame(frame)
                frame_count += 1

                t_infer_ms = (time.time() - t_infer_start) * 1000

                # Push annotated frame to MediaMTX (best-effort, never kills the worker)
                mediamtx_ok = False
                if self._ffmpeg_out is not None:
                    try:
                        self._ffmpeg_out.stdin.write(annotated.tobytes())
                        mediamtx_ok = True
                    except BrokenPipeError:
                        print(f"  [{CAMERA_ID}] FFmpeg pipe broken, restarting output stream...")
                        try:
                            self._ffmpeg_out.stdin.close()
                            self._ffmpeg_out.wait(timeout=3)
                        except Exception:
                            pass
                        h, w = frame.shape[:2]
                        self._start_ffmpeg_output(w, h)

                # Send new detections to Kafka first (must arrive before left events)
                kafka_sent = 0
                if detections:
                    event = {
                        "type": "detection",
                        "cameraId": CAMERA_ID,
                        "timestamp": datetime.now().isoformat(),
                        "kafkaProducedAt": int(time.time() * 1000),
                        "detections": detections,
                    }
                    self.producer.produce(
                        TOPIC_DETECTIONS,
                        key=CAMERA_ID,
                        value=json.dumps(event),
                    )
                    self.producer.poll(0)
                    kafka_sent = len(detections)

                    for d in detections:
                        print(f"  [{CAMERA_ID}] KAFKA >>> NEW person trackId={d['trackId']} conf={d['confidence']} embedding_dim={len(d['embedding'])}")

                # Send left events to Kafka (after detections to preserve ordering)
                if left_ids:
                    left_event = {
                        "type": "left",
                        "cameraId": CAMERA_ID,
                        "timestamp": datetime.now().isoformat(),
                        "leftTrackIds": list(left_ids),
                    }
                    self.producer.produce(
                        TOPIC_DETECTIONS,
                        key=CAMERA_ID,
                        value=json.dumps(left_event),
                    )
                    self.producer.poll(0)
                    print(f"  [{CAMERA_ID}] KAFKA >>> LEFT trackIds={list(left_ids)}")

                # Periodic status log (every 5 seconds)
                now = time.time()
                elapsed = now - last_log_time
                if elapsed >= 5.0:
                    actual_fps = frame_count / (now - last_log_time) if last_log_time else 0
                    print(
                        f"  [{CAMERA_ID}] STATUS | frame={frame_count} "
                        f"| fps={actual_fps:.1f} "
                        f"| infer={t_infer_ms:.0f}ms "
                        f"| dropped={dropped_frames} "
                        f"| MediaMTX={'OK' if mediamtx_ok else 'FAIL'} "
                        f"| Kafka_sent={kafka_sent} "
                        f"| tracking={len(self._active_ids)} persons"
                    )
                    last_log_time = now

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
