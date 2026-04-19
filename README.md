# Door-bell: Smart Doorbell Surveillance System

A real-time multi-camera surveillance system that detects and re-identifies people using YOLOv8, BoTSORT tracking, and OSNet person re-identification. Detection events are streamed through Kafka to a Spring Boot backend, which matches identities via vector similarity search (Qdrant) and pushes live alerts over WebSocket.


## Local Development

### Start

```bash
docker compose up --build -d
```

Multi-camera mode (cam-01, cam-02, cam-03):

```bash
docker compose --profile multi-cam up --build -d
```

### Stop

```bash
docker compose down
```

### Restart a single service

```bash
docker compose up --build -d yolo-worker-1
docker compose up --build -d java-backend
```

### View logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs yolo-worker-1 -f
docker compose logs java-backend -f
docker compose logs mediamtx -f

# Last N lines
docker compose logs yolo-worker-1 --tail=30
```

### Start edge camera (local)

```bash
MEDIAMTX_URL=rtsp://localhost:8554/cam-01 python3 camera_producer.py
```

### Reset Qdrant (clear all person recognition data)

```bash
curl -X DELETE http://localhost:6333/collections/person-embeddings
```

### Open frontend

```
http://localhost
```

---

## AWS Deployment (g4dn.xlarge + GPU)

### EC2 Initial Setup (one-time)

SSH into the instance:

```bash
ssh -i ~/.ssh/door-bell-key.pem ubuntu@<EC2_PUBLIC_IP>
```

Install Docker (skip if already installed):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
newgrp docker
```

Install NVIDIA Container Toolkit:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify GPU:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### Configure .env

```bash
cp .env.gpu .env
nano .env  # Set AWS_PUBLIC_HOST and WEBRTC_ICE_HOST to the EC2 public IP
```

### Start (GPU mode)

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

### Stop

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down
```

### Restart a single service

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d yolo-worker-1
```

### View logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs yolo-worker-1 -f
docker compose logs java-backend -f
```

### Start edge camera (push from local to EC2)

```bash
MEDIAMTX_URL=rtsp://<EC2_PUBLIC_IP>:8554/cam-01 python3 camera_producer.py
```

### Reset Qdrant

```bash
curl -X DELETE http://<EC2_PUBLIC_IP>:6333/collections/person-embeddings
```

### Open frontend

```
http://<EC2_PUBLIC_IP>
```

### Pull latest code and redeploy

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d
```

---

## EC2 Security Group — Required Inbound Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH |
| 80 | TCP | Frontend |
| 8080 | TCP | Java Backend API |
| 8554 | TCP | MediaMTX RTSP (edge camera stream) |
| 8889 | TCP | MediaMTX WebRTC |
| 8189 | UDP | MediaMTX WebRTC UDP |
| 6333 | TCP | Qdrant |
| 9092 | TCP | Kafka |

---

## Components

| Component | Path | Description |
|-----------|------|-------------|
| **Python Edge** | `Python-edge/` | Captures webcam frames and pushes RTSP stream to MediaMTX via FFmpeg |
| **MediaMTX** | (Docker image) | RTSP/WebRTC media server, acts as the video hub |
| **YOLO Worker** | `Door-bell-backend/Python-engine/` | Pulls RTSP stream, runs YOLOv8 + BoTSORT tracking + OSNet ReID, publishes annotated stream and detection events to Kafka |
| **Java Backend** | `Door-bell-backend/Java-backend/` | Spring Boot service — consumes Kafka detections, matches/stores person embeddings in Qdrant, persists records in PostgreSQL, pushes live alerts via WebSocket |
| **PostgreSQL** | (Docker image) | Stores person and detection records |
| **Qdrant** | (Docker image) | Vector database for 512-dim person ReID embeddings (cosine similarity) |
| **Kafka** | (Docker image) | Message broker between YOLO workers and Java backend |

## Tech Stack

- **Computer Vision**: YOLOv8 (detection), BoTSORT (multi-object tracking), OSNet x1_0 (person re-identification)
- **Streaming**: RTSP via MediaMTX, FFmpeg for encoding/decoding
- **Backend**: Java 17 / Spring Boot 3, Spring Kafka, Spring WebSocket, JPA/Hibernate
- **Messaging**: Apache Kafka (KRaft mode, no Zookeeper)
- **Databases**: PostgreSQL 16, Qdrant (vector search)
- **Infrastructure**: Docker Compose, AWS EC2 g4dn.xlarge (NVIDIA T4 GPU)
