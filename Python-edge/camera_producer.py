"""
Camera Producer - captures frames from webcam and pushes RTSP stream to MediaMTX.
One instance per camera. Configured via environment variables.

Env vars:
  CAMERA_SOURCE  - camera index or video file path (default: "0")
  CAMERA_ID      - unique camera identifier (default: "cam-01")
  MEDIAMTX_URL   - MediaMTX RTSP publish URL (default: "rtsp://localhost:8554/cam-01")
  FRAME_WIDTH    - output width (default: 1080)
  FRAME_HEIGHT   - output height (default: 720)
  FPS            - target frames per second (default: 15)
"""

import os
import sys
import time
import subprocess
import cv2

# ── Config from env ──────────────────────────────────────────────────────────
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "0")
CAMERA_ID = os.environ.get("CAMERA_ID", "cam-01")
MEDIAMTX_URL = os.environ.get("MEDIAMTX_URL", "rtsp://localhost:8554/cam-01")
FRAME_W = int(os.environ.get("FRAME_WIDTH", "1080"))
FRAME_H = int(os.environ.get("FRAME_HEIGHT", "720"))
TARGET_FPS = int(os.environ.get("FPS", "15"))
SHOW_PREVIEW = os.environ.get("SHOW_PREVIEW", "true").lower() == "true"


def main():
    # Parse camera source: int for USB, string for file/RTSP
    try:
        source = int(CAMERA_SOURCE)
    except ValueError:
        source = CAMERA_SOURCE

    print(f"Starting camera producer | camera={CAMERA_ID} source={source}")
    print(f"  MediaMTX={MEDIAMTX_URL}")
    print(f"  Resolution={FRAME_W}x{FRAME_H} FPS={TARGET_FPS}")

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"ERROR: Cannot open camera {source}")
        sys.exit(1)

    # FFmpeg process to push RTSP to MediaMTX
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{FRAME_W}x{FRAME_H}",
        "-r", str(TARGET_FPS),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", str(TARGET_FPS * 2),  # keyframe every 2 seconds
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        MEDIAMTX_URL,
    ]

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    frame_interval = 1.0 / TARGET_FPS
    frame_count = 0

    try:
        while True:
            t0 = time.time()

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            resized = cv2.resize(frame, (FRAME_W, FRAME_H))

            # Write raw frame to FFmpeg stdin
            try:
                ffmpeg_proc.stdin.write(resized.tobytes())
            except BrokenPipeError:
                print("ERROR: FFmpeg pipe broken")
                break

            # Show local preview window
            if SHOW_PREVIEW:
                cv2.imshow(f"Camera {CAMERA_ID}", resized)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            frame_count += 1
            if frame_count % 100 == 0:
                print(f"  [{CAMERA_ID}] sent {frame_count} frames")

            # Maintain target FPS
            elapsed = time.time() - t0
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"Stopping camera producer {CAMERA_ID}")
    finally:
        cap.release()
        if SHOW_PREVIEW:
            cv2.destroyAllWindows()
        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait(timeout=5)
        print(f"Camera producer {CAMERA_ID} stopped. Total frames: {frame_count}")


if __name__ == "__main__":
    main()
