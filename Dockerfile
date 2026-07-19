FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/ ./dashboard/
COPY config/ ./config/
COPY VERSION ./VERSION

RUN mkdir -p /app/data /app/logs && chmod 755 /app/data /app/logs

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8080"]
