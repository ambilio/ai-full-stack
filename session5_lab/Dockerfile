# Dockerfile — Session 5: versioned model server (same discipline as Session 2/3)
# One image serves BOTH v1 and v2 — the versions differ only in configuration.

# 1. BASE IMAGE: slim, pinned version. Not 'latest', not full python, not CUDA.
FROM python:3.11-slim

# 2. Never run as root in production images (supply-chain hygiene)
RUN useradd -m appuser

WORKDIR /app

# 3. Copy requirements FIRST — Docker caches this layer, so editing app.py
#    later doesn't re-install 2 GB of packages (layer caching discipline)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu
#   ^ CPU-only torch wheel: ~200 MB instead of ~2.5 GB with CUDA.

# 4. PRE-BAKE the model into the image: cold start doesn't download 1 GB
#    from Hugging Face. For serious models, use a model volume instead.
ENV HF_HOME=/app/hf-cache
RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('Qwen/Qwen2.5-0.5B-Instruct')" \
    && chown -R appuser /app

# 5. App code goes LAST — it changes most often, so it invalidates
#    the fewest cached layers.
COPY app.py .

USER appuser

# 6. Managed cloud runtimes inject $PORT — listening on it keeps this image
#    cloud-ready even though we deploy locally in this lab.
ENV PORT=8080

# 7. Container-native health check: Docker marks the container "healthy"
#    only after the model has loaded and /health responds.
HEALTHCHECK --interval=15s --timeout=3s --start-period=120s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
