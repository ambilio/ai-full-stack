# Session 2 Lab — Container Packaging & Local Deployment

**Goal (60 min):** package the Session 1 model server into a Docker image and run it locally with the same production disciplines a managed cloud runtime enforces: injected secrets, health checks, resource limits, and (manually) replica scaling. Cloud deployment itself is covered as theory in the session deck; every concept practiced here maps one-to-one to a cloud runtime.

**Files:** `app.py` (server with /health and API-key auth) · `Dockerfile` (annotated with packaging discipline) · `requirements.txt` (pinned) · `docker-compose.yml` (secrets, health check, limits, scaling) · `.env.example` · `.dockerignore`

---

## Step 0 — Prerequisites (10 min, before class if possible)

**Ubuntu / Debian VM:**
```bash
docker --version
docker compose version

sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER     # run docker without sudo
newgrp docker                     # apply group in current shell (or log out/in)
docker run hello-world            # verify
```

**macOS / Windows laptop:** install Docker Desktop from docker.com, start it, then `docker run hello-world` in a terminal.

**Disk check:** the image build needs ~8 GB free (`df -h`).

## Step 1 — Build the image (10 min)

```bash
cd session2_lab
docker build -t llm-server:v1 .
docker images llm-server
```

While it builds, review the Dockerfile comments — each numbered comment is an exam-relevant discipline: pinned slim base image, non-root user, dependency layer before code layer (build-cache efficiency), CPU-only torch wheel (image size), model pre-baked into the image (cold-start vs size trade-off), HEALTHCHECK instruction.

**Record:** final image size from `docker images`. Then rebuild after touching only `app.py` (`touch app.py && docker build -t llm-server:v1 .`) and note how fast it is — every layer except the last is served from cache.

## Step 2 — Run and test the container (10 min)

```bash
docker run -d -p 8080:8080 -e API_KEY=test123 --name llm llm-server:v1

# watch health status change: starting -> healthy (this is the model loading)
watch -n 2 'docker ps --format "{{.Names}} {{.Status}}"'
```

When it shows `(healthy)`:

```bash
curl localhost:8080/health

curl -X POST localhost:8080/generate \
  -H "Content-Type: application/json" -H "X-API-Key: test123" \
  -d '{"text": "What is a container?", "max_tokens": 50}'

# wrong key -> 401: authentication works, key came from the environment
curl -X POST localhost:8080/generate \
  -H "Content-Type: application/json" -H "X-API-Key: wrong" -d '{"text":"hi"}'
```

**Record:** time from `docker run` to `(healthy)` — this is your container cold start.

```bash
docker stop llm && docker rm llm
```

## Step 3 — Secrets discipline (5 min)

```bash
cp .env.example .env
# edit .env and set a random value, e.g.:
sed -i "s/change-me.*/$(openssl rand -hex 12)/" .env
cat .env
```

Rules being practiced: the secret lives in `.env`; `.env` is listed in `.dockerignore` and must be in `.gitignore`; the image contains no secret (`docker history llm-server:v1` shows no key); the running container receives it as an environment variable. In a cloud runtime, a secret manager plays the role of `.env` and IAM controls who can read it.

## Step 4 — Run as a service with compose (10 min)

```bash
docker compose up -d --build
docker compose ps                  # note the random host port, e.g. 0.0.0.0:32768->8080
```

Test through the assigned port (replace 32768 with yours, key from your .env):

```bash
KEY=$(grep API_KEY .env | cut -d= -f2)
curl -X POST localhost:32768/generate \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"text": "hello", "max_tokens": 30}'
```

The compose file adds three production behaviors: `env_file` (secret injection), `healthcheck` (traffic-readiness signal), `mem_limit`/`cpus` (resource governance).

## Step 5 — Resource limits in action (5 min)

```bash
docker stats --no-stream            # observe live memory/CPU of the replica
```

Theory to observe, not necessarily run: if `mem_limit` were set below the model's needs (e.g. `1g`), the kernel OOM-kills the container mid-load and it restart-loops — the same failure appears in any cloud runtime when memory is under-provisioned. Right-sizing = measured usage + safety margin.

## Step 6 — Scale replicas manually (10 min)

```bash
docker compose up -d --scale llm=2
docker compose ps                   # two replicas, two different host ports
```

Send a request to each port and confirm both serve. Watch `docker stats` — memory doubles: every replica carries a full copy of the model.

This is exactly what a cloud autoscaler automates: it watches concurrency or CPU, runs the equivalent of `--scale llm=N`, and a load balancer spreads requests across replicas. Locally we are the autoscaler and the client is the load balancer — the mechanics are identical.

```bash
docker compose down
```

## Step 7 — Map local practice to cloud theory (5 min, discussion)

| Practiced locally | Managed cloud runtime equivalent |
|---|---|
| `docker build` + image | Build service + container registry |
| `-e API_KEY` / `.env` | Secret manager + IAM-controlled injection |
| `HEALTHCHECK` / compose healthcheck | Startup, readiness and liveness probes |
| `mem_limit`, `cpus` | Instance size / resource requests & limits |
| `--scale llm=2` (manual) | Autoscaler (concurrency/CPU-based, min/max instances) |
| Host-port per replica | Load balancer with a single stable URL |
| Cold start you timed | Scale-from-zero latency users experience |

## Deliverables

1. Image size, and rebuild time after an `app.py`-only change (layer caching evidence)
2. Screenshot: container transitioning `starting → healthy`, with your measured cold-start time
3. Screenshot: 401 with wrong key, 200 with correct key
4. Two-replica `docker compose ps` output, plus 3–4 sentences: which cloud runtime concept each lab step corresponds to, and one trade-off of scale-to-zero
