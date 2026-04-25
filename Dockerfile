FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# HF Spaces: writable dirs are /tmp (ephemeral) and /data (persistent if enabled).
# Default to /data when present, fall back to /tmp.
ENV OUTPUT_DIR=/data/output \
    DB_PATH=/data/auto_clipper.db \
    HF_HOME=/data/.cache/huggingface \
    XDG_CACHE_HOME=/data/.cache \
    LLM_PROVIDER=groq \
    WHISPER_MODEL=small \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE=int8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Fall back to /tmp if /data is not writable (free HF Space without persistence)
RUN mkdir -p /data /tmp/output && chmod -R 777 /data /tmp/output || true

EXPOSE 7860

CMD ["sh", "-c", "if [ ! -w /data ]; then export OUTPUT_DIR=/tmp/output DB_PATH=/tmp/auto_clipper.db HF_HOME=/tmp/.cache/huggingface XDG_CACHE_HOME=/tmp/.cache; fi; mkdir -p $OUTPUT_DIR $HF_HOME && exec uvicorn dashboard.app:app --host 0.0.0.0 --port 7860"]
