FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/eggs.db \
    MEDIA_DIR=/data/media

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.lock
COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/media \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8890
VOLUME ["/data"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8890"]
