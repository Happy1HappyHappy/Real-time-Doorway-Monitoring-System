from ultralytics import YOLO
import cv2
import threading
import time
from datetime import datetime
from collections import deque
from typing import Optional

class DoorbellDetector:
    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        conf: float = 0.35,
        iou: float = 0.5,
        max_alerts: int = 100,
    ):
        print(f"Loading model: {model_path}")
        self.model = YOLO(model_path)
        self.conf = conf
        self.iou = iou

        self._lock = threading.Lock()
        self._latest_frame: Optional[bytes] = None   # annotated frame
        self._alerts: deque = deque(maxlen=max_alerts)
        self._active_ids: set = set()
        self._total_alerts: int = 0

        print("✅ YOLOv8 detector ready")

    def reset(self):
        """Clear state when camera restarts."""
        with self._lock:
            self._latest_frame = None
            self._active_ids = set()
        # keep alert history across restarts

    def process_frame(self, frame):
        """Run YOLOv8 + BoTSORT on a single frame."""
        results = self.model.track(
            frame,
            persist  = True,
            tracker  = "botsort.yaml",
            classes  = [0],          # person only
            conf     = self.conf,
            iou      = self.iou,
            verbose  = False,
        )

        annotated = results[0].plot()
        boxes = results[0].boxes

        new_alerts = []
        if boxes is not None and boxes.id is not None:
            current_ids = set(boxes.id.int().tolist())
            confs = boxes.conf.tolist()
            xywh = boxes.xywhn.tolist()  # normalised cx,cy,w,h

            with self._lock:
                newly_seen = current_ids - self._active_ids
                self._active_ids = current_ids

                for i, tid in enumerate(boxes.id.int().tolist()):
                    if tid in newly_seen:
                        alert = {
                            "track_id":   tid,
                            "confidence": round(confs[i], 3),
                            "bbox_norm":  [round(v, 3) for v in xywh[i]],
                            "timestamp":  datetime.now().isoformat(),
                        }
                        self._alerts.append(alert)
                        self._total_alerts += 1
                        new_alerts.append(alert)
        else:
            with self._lock:
                self._active_ids = set()

        # Print alerts outside lock
        for a in new_alerts:
            print(f"🔔 ALERT  track_id={a['track_id']}  conf={a['confidence']}  time={a['timestamp']}")

        with self._lock:
            self._latest_frame = annotated

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_latest_frame(self):
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def get_alerts(self, limit: int = 10):
        with self._lock:
            alerts = list(self._alerts)
        return list(reversed(alerts))[:limit]  # newest first

    def get_total_alerts(self) -> int:
        with self._lock:
            return self._total_alerts

    def get_active_ids(self):
        with self._lock:
            return list(self._active_ids)

    def get_person_count(self) -> int:
        with self._lock:
            return len(self._active_ids)

    def clear_alerts(self):
        with self._lock:
            self._alerts.clear()
            self._total_alerts = 0
