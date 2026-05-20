# Authors: Claire Liu, Yu-Jing Wei
# Description: Dockerfile for the VLM worker service that packages the Python app which
# calls an Ollama VLM to analyse person crops from Kafka.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
