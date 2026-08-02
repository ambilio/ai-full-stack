# ClaimAssist API v5 — Session 2 Dockerfile discipline:
# pinned slim base, cached dependency layer, non-root user, HEALTHCHECK.
FROM python:3.11.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# v5: guardrails + redaction ship with the app. The prompt registry (prompts/)
# and data/ are mounted read-only by docker-compose so a prompt release does
# not require an image rebuild — only a restart with a new PROMPT_VERSION.
COPY app.py guardrails.py redact.py ./

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
