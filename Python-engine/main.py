from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from contextlib import asynccontextmanager
import uvicorn
import cv2
import threading
import time
from detector import DoorbellDetector

# ── Global state ─────────────────────────────────────────────────────────────
detector: DoorbellDetector = None
camera_thread: threading.Thread = None
is_running: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    global detector
    detector = DoorbellDetector()
    print("✅ Doorbell system ready")
    yield
    stop_camera()
    print("🛑 Doorbell system stopped")

app = FastAPI(
    title="🚪 Doorbell Detection System",
    description="YOLOv8 + BoTSORT person detection via USB webcam",
    version="1.0.0",
    lifespan=lifespan
)

# ── Camera loop (runs in background thread) ───────────────────────────────────
def camera_loop(source: int):
    global is_running
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"❌ Cannot open camera {source}")
        is_running = False
        return

    print(f"📷 Camera {source} opened")
    while is_running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        detector.process_frame(frame)

    cap.release()
    print("📷 Camera released")

def stop_camera():
    global is_running, camera_thread
    is_running = False
    if camera_thread and camera_thread.is_alive():
        camera_thread.join(timeout=3)

# ── MJPEG stream generator ────────────────────────────────────────────────────
def generate_stream():
    while True:
        frame = detector.get_latest_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' +
            jpeg.tobytes() +
            b'\r\n'
        )
        time.sleep(1 / 30)  # ~30fps

# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/camera/start")
def start_camera(source: int = 0):
    """Start the webcam and begin person detection."""
    global camera_thread, is_running
    if is_running:
        return JSONResponse({"status": "already_running"}, status_code=200)

    is_running = True
    detector.reset()
    camera_thread = threading.Thread(target=camera_loop, args=(source,), daemon=True)
    camera_thread.start()
    return {"status": "started", "camera_source": source}

@app.post("/camera/stop")
def stop_camera_route():
    """Stop the webcam."""
    stop_camera()
    return {"status": "stopped"}

@app.get("/status")
def get_status():
    """Get current detection status and alerts."""
    return {
        "is_running": is_running,
        "persons_detected": detector.get_person_count(),
        "active_track_ids": detector.get_active_ids(),
        "total_alerts": detector.get_total_alerts(),
        "alerts": detector.get_alerts(),
    }

@app.get("/alerts")
def get_alerts(limit: int = 10):
    """Get the latest N person detection alerts."""
    return {
        "alerts": detector.get_alerts(limit=limit),
        "total": detector.get_total_alerts(),
    }

@app.post("/alerts/clear")
def clear_alerts():
    """Clear alert history."""
    detector.clear_alerts()
    return {"status": "cleared"}

@app.get("/stream")
def video_stream():
    """MJPEG live stream with bounding boxes drawn."""
    if not is_running:
        return JSONResponse({"error": "Camera not started. POST /camera/start first."}, status_code=400)
    return StreamingResponse(
        generate_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/snapshot")
def snapshot():
    """Return the latest annotated frame as JPEG."""
    frame = detector.get_latest_frame()
    if frame is None:
        return JSONResponse({"error": "No frame available"}, status_code=404)
    _, jpeg = cv2.imencode('.jpg', frame)
    return StreamingResponse(iter([jpeg.tobytes()]), media_type="image/jpeg")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
