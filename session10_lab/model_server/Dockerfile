# llm-server:openai — the local model container with an OpenAI-compatible API.
# Dockerfile discipline from Session 2: pinned slim base, cached dependency
# layer, non-root user, HEALTHCHECK. Secrets only ever arrive via .env.
FROM python:3.11.9-slim

WORKDIR /app

# Dependency layer first so code edits do not re-download torch/transformers.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Non-root user; the HF cache lives in the user's home (mounted as a volume
# in docker-compose.yml so the model download survives container recreation).
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
ENV HF_HOME=/home/appuser/.cache/huggingface

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health', timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
