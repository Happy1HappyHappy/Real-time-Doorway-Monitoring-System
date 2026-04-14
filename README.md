# Door-bell: Smart Doorbell Surveillance System

A real-time multi-camera surveillance system that detects and re-identifies people using YOLOv8, BoTSORT tracking, and OSNet person re-identification. Detection events are streamed through Kafka to a Spring Boot backend, which matches identities via vector similarity search (Qdrant) and pushes live alerts over WebSocket.

## Architecture

```
┌─────────────┐    RTSP     ┌───────────┐    RTSP     ┌───────────────┐
│ Python Edge │ ──────────> │ MediaMTX  │ ──────────> │ YOLO Worker   │
│ (camera)    │             │ (media    │ <────────── │ (inference +  │
└─────────────┘             │  server)  │  annotated  │  ReID + track)│
                            └───────────┘             └──────┬────────┘
                                                             │ Kafka
                                                             v
┌──────────┐  WebSocket   ┌──────────────┐  SQL/Vector  ┌─────────┐
│ Frontend │ <─────────── │ Java Backend │ ───────────> │Postgres │
│ (client) │              │ (Spring Boot)│              │ Qdrant  │
└──────────┘              └──────────────┘              └─────────┘
```

### Components

| Component | Path | Description |
|-----------|------|-------------|
| **Python Edge** | `Python-edge/` | Captures webcam frames, pushes RTSP stream to MediaMTX via FFmpeg |
| **MediaMTX** | (Docker image) | RTSP/WebRTC media server, acts as the video hub |
| **YOLO Worker** | `Door-bell-backend/Python-engine/` | Pulls RTSP stream, runs YOLOv8 + BoTSORT tracking + OSNet ReID, publishes annotated stream and detection events to Kafka |
| **Java Backend** | `Door-bell-backend/Java-backend/` | Spring Boot service — consumes Kafka detections, matches/stores person embeddings in Qdrant, persists records in PostgreSQL, pushes live alerts via WebSocket |
| **PostgreSQL** | (Docker image) | Stores person and detection records |
| **Qdrant** | (Docker image) | Vector database for 512-dim person ReID embeddings (cosine similarity) |
| **Kafka** | (Docker image) | Message broker between YOLO workers and Java backend |

## Tech Stack

- **Computer Vision**: YOLOv8 (detection), BoTSORT (multi-object tracking), OSNet (person re-identification)
- **Streaming**: RTSP via MediaMTX, FFmpeg for encoding/decoding
- **Backend**: Java 17 / Spring Boot 3, Spring Kafka, Spring WebSocket, JPA/Hibernate
- **Messaging**: Apache Kafka (KRaft mode, no Zookeeper)
- **Databases**: PostgreSQL 16, Qdrant (vector search)
- **Infrastructure**: Docker Compose

## Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for the edge camera producer)
- A webcam or video source (for the edge device)
- FFmpeg (installed on the edge device)

## Quick Start

### 1. Configure environment

Copy and edit the `.env` file in the project root:

Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_PUBLIC_HOST` | `localhost` | Public hostname for Kafka external listener |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` | Kafka broker address (internal) |
| `MODEL_PATH` | `yolov8n.pt` | YOLO model file |
| `CONFIDENCE` | `0.35` | Detection confidence threshold |
| `POSTGRES_DB` | `doorbell` | PostgreSQL database name |
| `POSTGRES_USER` | `doorbell` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `doorbell123` | PostgreSQL password |
| `JAVA_SERVER_PORT` | `8080` | Java backend exposed port |

### 2. Start backend services

```bash
# Start with a single YOLO worker (recommended for testing)
docker compose up --build

# Start with all 3 camera workers
docker compose --profile multi-cam up --build
```

This starts: Kafka, MediaMTX, YOLO worker(s), Java backend, PostgreSQL, and Qdrant.

### 3. Start the edge camera producer

On the edge device (e.g., Raspberry Pi or your laptop):

```bash
cd Python-edge
pip install opencv-python
python camera_producer.py
```

Environment variables for the camera producer:

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMERA_SOURCE` | `0` | Camera index or video file path |
| `CAMERA_ID` | `cam-01` | Unique camera identifier |
| `MEDIAMTX_URL` | `rtsp://localhost:8554/cam-01` | MediaMTX RTSP publish URL |
| `FRAME_WIDTH` | `1080` | Output frame width |
| `FRAME_HEIGHT` | `720` | Output frame height |
| `FPS` | `15` | Target frames per second |

### 4. View annotated stream

The YOLO worker publishes annotated RTSP streams back to MediaMTX. You can view them with any RTSP player (e.g., VLC):

```
rtsp://localhost:8554/cam-01/annotated
```

Or via WebRTC in a browser:

```
http://localhost:8889/cam-01/annotated
```

## Service Ports

| Service | Port | Protocol |
|---------|------|----------|
| Kafka | 9092 | TCP |
| MediaMTX (RTSP) | 8554 | TCP |
| MediaMTX (WebRTC HTTP) | 8889 | TCP |
| MediaMTX (WebRTC UDP) | 8890 | UDP |
| Java Backend | 8080 | HTTP |
| PostgreSQL | 5432 | TCP |
| Qdrant | 6333 | HTTP |

## How It Works

1. **Edge camera** captures webcam frames and pushes an RTSP stream to MediaMTX
2. **YOLO Worker** pulls the RTSP stream, runs YOLOv8 person detection with BoTSORT multi-object tracking
3. For each newly detected person, **OSNet** extracts a 512-dimensional ReID embedding
4. Detection events (camera ID, track ID, bounding box, embedding, confidence) are published to Kafka
5. **Java Backend** consumes events, searches **Qdrant** for matching person embeddings (cosine similarity > 0.75)
   - Match found: updates the person's `lastSeenAt` timestamp
   - No match: creates a new person record and stores the embedding in Qdrant
6. Detection records are persisted in **PostgreSQL** and live alerts are pushed to clients via **WebSocket** (`/topic/detections`)

## Project Structure

```
Door-bell/
├── docker-compose.yml              # All backend services
├── .env                            # Environment configuration
├── Python-edge/                    # Edge device camera producer
│   ├── camera_producer.py
│   └── requirements.txt
└── Door-bell-backend/
    ├── Python-engine/              # YOLO inference worker
    │   ├── Dockerfile
    │   ├── kafka_worker.py         # Main worker loop
    │   ├── detector.py             # YOLOv8 + BoTSORT detector
    │   ├── reid_extractor.py       # OSNet ReID embedding extractor
    │   └── requirements.txt
    └── Java-backend/               # Spring Boot backend
        ├── Dockerfile
        ├── pom.xml
        └── src/main/java/.../
            ├── kafka/DetectionConsumer.java
            ├── service/DetectionService.java
            ├── service/QdrantService.java
            ├── dto/DetectionEvent.java
            ├── entity/Person.java
            ├── entity/DetectionRecord.java
            └── config/
```
