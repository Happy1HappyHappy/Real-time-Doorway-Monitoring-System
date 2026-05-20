# Authors: Claire Liu, Yu-Jing Wei
# Description: Dockerfile for the YOLO inference worker, installing ffmpeg/OpenCV deps
# and the Python stack (ultralytics, torchreid, confluent-kafka) needed for detection,
# tracking, and re-identification.
ARG CUDA_BASE=python:3.11-slim
FROM ${CUDA_BASE}

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "kafka_worker.py"]
