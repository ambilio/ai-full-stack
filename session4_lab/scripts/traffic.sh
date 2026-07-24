#!/usr/bin/env bash
# traffic.sh — generates realistic mixed traffic so the dashboards have a story:
# mostly valid requests, every 5th request with a WRONG key (-> 401s feed the
# error-rate panel). Run it in one terminal while watching Grafana in another.
set -e
cd "$(dirname "$0")/.."
KEY=$(grep API_KEY .env | cut -d= -f2)

PROMPTS=(
  "What is a load balancer?"
  "Explain container health checks in one line."
  "What does p95 latency mean?"
  "Name one benefit of canary deployments."
  "What is an error budget?"
)

N=${1:-60}
echo "Sending $N requests (every 5th with a wrong API key)..."
for i in $(seq 1 "$N"); do
  P=${PROMPTS[$((RANDOM % ${#PROMPTS[@]}))]}
  if [ $((i % 5)) -eq 0 ]; then
    USE_KEY="wrong-key"          # deliberate 401 for the error panel
  else
    USE_KEY="$KEY"
  fi
  curl -s -o /dev/null -X POST localhost:8080/generate \
    -H "Content-Type: application/json" -H "X-API-Key: $USE_KEY" \
    -d "{\"text\": \"$P\", \"max_tokens\": 40}"
  echo "  request $i done"
done
echo "Traffic complete — check the Grafana panels."
