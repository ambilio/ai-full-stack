#!/usr/bin/env bash
# mirror_traffic.sh — send 39 varied requests through the traffic mirror (:8080).
# Each request: v1 answers the caller; a mirrored copy hits v2 in the background.
# Both versions emit Langfuse traces tagged model_version:v1 / model_version:v2.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env may contain values with spaces (PREFIX_V2), so grep the key instead of sourcing.
API_KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)
MIRROR_URL=${MIRROR_URL:-http://localhost:8080/generate}

QUESTIONS=(
  "What is UPI?"
  "What is the capital of India?"
  "Explain what an insurance premium is in one sentence."
  "Answer yes or no: is 7 a prime number?"
  "What does GDP stand for?"
  "Name two renewable energy sources."
  "What is a REST API?"
  "How many days are there in a leap year?"
  "What is the boiling point of water in Celsius?"
  "Define machine learning in one sentence."
  "What currency is used in Japan?"
  "What is the difference between RAM and disk storage?"
  "Which planet is known as the red planet?"
)

ROUNDS=3   # 13 questions x 3 rounds = 39 requests (target: 30-50)
total=0
for round in $(seq 1 "$ROUNDS"); do
  for q in "${QUESTIONS[@]}"; do
    total=$((total + 1))
    printf '[%02d] %s\n' "$total" "$q"
    curl -s -X POST "$MIRROR_URL" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${API_KEY}" \
      -d "{\"text\": \"${q}\", \"max_tokens\": 48}" | head -c 160
    printf '\n\n'
    sleep 1
  done
done
echo "Done: sent ${total} requests through the mirror."
echo "Compare v1 vs v2 in the Langfuse UI: filter traces by tag model_version:v1 / model_version:v2."
