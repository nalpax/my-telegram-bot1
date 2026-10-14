# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    GIFT_RADAR_LOG_DIR=/app/data/logs

COPY requirements.txt .
# Optional session CA for managed builds; ordinary hosts use default CA roots.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt

COPY main.py ./main.py
COPY bot ./bot
COPY gift_radar ./gift_radar

CMD ["python", "main.py"]
