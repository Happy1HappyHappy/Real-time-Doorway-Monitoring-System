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
import base64
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
TOPIC_ANALYSIS_REQUESTS = os.environ.get("KAFKA_TOPIC_ANALYSIS_REQUESTS", "doorbell-analysis-requests")
ANALYSIS_CROP_MAX_SIZE = int(os.environ.get("ANALYSIS_CROP_MAX_SIZE", "512"))
ANALYSIS_JPEG_QUALITY = int(os.environ.get("ANALYSIS_JPEG_QUALITY", "80"))
MODEL_PATH = os.environ.get("MODEL_PATH", "yolov8n.pt")
CONFIDENCE = float(os.environ.get("CONFIDENCE", "0.50"))
OUTPUT_FPS = int(os.environ.get("OUTPUT_FPS", "15"))
RTSP_CONNECT_RETRIES = int(os.environ.get("RTSP_CONNECT_RETRIES", "0"))
DEVICE = os.environ.get("DEVICE", "auto")
EMBEDDING_FRAMES = int(os.environ.get("EMBEDDING_FRAMES", "5"))
VLM_REANALYSIS_INTERVAL_SEC = float(os.environ.get("VLM_REANALYSIS_INTERVAL_SEC", "5.0"))
# Motion detection: pixel-normalized speed threshold (fraction of frame width per second).
MOTION_RUNNING_THRESHOLD = float(os.environ.get("MOTION_RUNNING_THRESHOLD", "0.35"))
MOTION_WINDOW_SEC = float(os.environ.get("MOTION_WINDOW_SEC", "0.8"))


def _crop_bbox(frame: np.ndarray, bbox_xywhn: list) -> np.ndarray:
    """Crop a person from the frame given normalized xywh bbox."""
    h, w = frame.shape[:2]
    cx, cy, bw, bh = bbox_xywhn
    x1 = max(0, int((cx - bw / 2) * w))
    y1 = max(0, int((cy - bh / 2) * h))
    x2 = min(w, int((cx + bw / 2) * w))
    y2 = min(h, int((cy + bh / 2) * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def _encode_crop_b64(crop: np.ndarray) -> str:
    """Resize crop (max side = ANALYSIS_CROP_MAX_SIZE) and return base64 JPEG."""
    if crop is None or crop.size == 0:
        return None
    h, w = crop.shape[:2]
    m = max(h, w)
    if m > ANALYSIS_CROP_MAX_SIZE:
        scale = ANALYSIS_CROP_MAX_SIZE / m
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, ANALYSIS_JPEG_QUALITY])
    if not ok:
        return None
    return base64.b64encode(buf.tobytes()).decode("ascii")


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
        self._last_vlm_ts: dict = {}  # tid -> last VLM request timestamp (seconds)
        self._motion_history: dict = {}  # tid -> list of (ts, center_x, center_y)
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
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-use_wallclock_as_timestamps", "1",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(OUTPUT_FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-x264-params", "nal-hrd=cbr:force-cfr=1:sliced-threads=1:sync-lookahead=0:rc-lookahead=0",
            "-g", str(OUTPUT_FPS),
            "-bf", "0",
            "-max_delay", "0",
            "-muxdelay", "0",
            "-muxpreload", "0",
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

        annotated = results[0].plot(conf=False, labels=False)
        boxes = results[0].boxes
        detections = []
        analysis_requests = []

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
            now_ts = time.time()
            for i, tid in enumerate(id_list):
                if tid in newly_seen:
                    self._pending[tid] = {
                        "embeddings": [],
                        "bbox": [round(v, 3) for v in xywh[i]],
                        "confidence": round(confs[i], 3),
                    }

                # Update motion history (center in normalized coords)
                cx, cy = xywh[i][0], xywh[i][1]
                hist = self._motion_history.setdefault(tid, [])
                hist.append((now_ts, cx, cy))
                # Keep only samples within the motion window
                cutoff = now_ts - MOTION_WINDOW_SEC
                self._motion_history[tid] = [s for s in hist if s[0] >= cutoff]

                # Compute speed (normalized distance per second) over the window
                motion_hint = None
                samples = self._motion_history[tid]
                if len(samples) >= 2:
                    t0, x0, y0 = samples[0]
                    t1, x1, y1 = samples[-1]
                    dt = max(t1 - t0, 1e-3)
                    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                    speed = dist / dt  # fraction of frame per second
                    if speed >= MOTION_RUNNING_THRESHOLD:
                        motion_hint = "running"

                # Queue a VLM analysis request for new tracks, or re-analyse
                # tracks still in frame every VLM_REANALYSIS_INTERVAL_SEC.
                last_ts = self._last_vlm_ts.get(tid, 0.0)
                if tid in newly_seen or (now_ts - last_ts) >= VLM_REANALYSIS_INTERVAL_SEC:
                    crop = _crop_bbox(frame, xywh[i])
                    img_b64 = _encode_crop_b64(crop)
                    if img_b64:
                        analysis_requests.append({
                            "trackId": tid,
                            "bbox": [round(v, 3) for v in xywh[i]],
                            "imageJpegB64": img_b64,
                            "motionHint": motion_hint,
                        })
                        self._last_vlm_ts[tid] = now_ts
                        if motion_hint:
                            print(f"  [{CAMERA_ID}] MOTION trackId={tid} speed>{MOTION_RUNNING_THRESHOLD}")

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
                self._last_vlm_ts.pop(tid, None)
                self._motion_history.pop(tid, None)
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
            self._last_vlm_ts.clear()
            self._motion_history.clear()

        if detections or t_reid_total > 0:
            print(f"  [{CAMERA_ID}] TIMING | yolo={t_yolo:.0f}ms | reid={t_reid_total:.0f}ms | total_python={t_yolo + t_reid_total:.0f}ms")

        # Collect current positions of all active tracks
        positions = []
        if boxes is not None and boxes.id is not None:
            for i, tid in enumerate(id_list):
                positions.append({"trackId": tid, "bbox": [round(v, 3) for v in xywh[i]]})

        return annotated, detections, left_ids, positions, analysis_requests

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
                annotated, detections, left_ids, positions, analysis_requests = self._process_frame(frame)
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

                # Send analysis requests (one per newly-seen track)
                for req in analysis_requests:
                    analysis_event = {
                        "type": "analysis-request",
                        "cameraId": CAMERA_ID,
                        "timestamp": datetime.now().isoformat(),
                        "trackId": req["trackId"],
                        "bbox": req["bbox"],
                        "imageJpegB64": req["imageJpegB64"],
                    }
                    self.producer.produce(
                        TOPIC_ANALYSIS_REQUESTS,
                        key=f"{CAMERA_ID}:{req['trackId']}",
                        value=json.dumps(analysis_event),
                    )
                    self.producer.poll(0)
                    print(f"  [{CAMERA_ID}] ANALYSIS REQUEST trackId={req['trackId']} bytes={len(req['imageJpegB64'])}")

                # Send position update to Kafka every frame
                if positions:
                    pos_event = {
                        "type": "position",
                        "cameraId": CAMERA_ID,
                        "timestamp": datetime.now().isoformat(),
                        "tracks": positions,
                    }
                    self.producer.produce(
                        TOPIC_DETECTIONS,
                        key=CAMERA_ID,
                        value=json.dumps(pos_event),
                    )
                    self.producer.poll(0)

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
