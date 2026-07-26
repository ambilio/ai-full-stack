# Dockerfile — the traffic mirror. Slim: no model, no torch — it only proxies.
FROM python:3.11-slim

RUN useradd -m appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mirror.py .
USER appuser

ENV PORT=8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD exec uvicorn mirror:app --host 0.0.0.0 --port ${PORT}
