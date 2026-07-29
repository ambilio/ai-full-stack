# Dockerfile — ClaimAssist API v3 (Session 8)
# Session 2 discipline: pinned slim base, non-root user, cached deps layer,
# HEALTHCHECK, secrets only via environment (.env — never baked in).
# v3: requirements now include chromadb; the chroma store (rag/chroma/) and
# the data directory are runtime volume mounts, never baked into the image.
FROM python:3.11-slim

RUN useradd -m appuser

WORKDIR /app

# deps first: cached layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code last: changes most often
COPY app.py .
RUN chown -R appuser /app

USER appuser

ENV PORT=8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
