# Session 3 Lab — CI/CD Eval Gates, Canary Deployment & Rollback (Local)

**Goal (60 min):** gate releases with automated evaluation, demonstrate a blocked "merge" on a quality regression, run a progressive canary rollout of a new model-behavior version with automated promotion, and verify rollback — entirely on the local machine.

**Local equivalents used** (no cloud, no GitHub account needed):

| Delivery-plan item | Local implementation |
|---|---|
| GitHub Actions pipeline | git `pre-push` hook running the same gate script |
| Promptfoo eval gates | Promptfoo via `npx`, testing the local model server |
| Blocked merge on regression | Blocked `git push` to a local bare repository |
| Canary with automated traffic shift | nginx weighted upstream, rewritten by `canary.sh` on eval results |
| Rollback | `rollback.sh` — one command back to 100% v1 |

A real GitHub Actions workflow with identical logic is included at `reference/github-actions-eval-gate.yml` for the theory discussion.

---

## Step 0 — Prerequisites (10 min)

Docker working (Session 2). Additionally, Node.js 18+ for Promptfoo:

```bash
node --version               # if missing or <18:
sudo apt install -y nodejs npm
npx --yes promptfoo@latest --version    # first run downloads it (~1 min)
```

RAM note: this lab runs TWO model containers (~2–3 GB each) — ensure ~8 GB free RAM. If tight, lower `mem_limit` in docker-compose.yml to 3g.

## Step 1 — Start v1 + v2 + load balancer (10 min)

```bash
cd session3_lab
cp .env.example .env
sed -i "s/change-me.*/$(openssl rand -hex 12)/" .env

docker compose up -d --build
watch -n 2 'docker ps --format "{{.Names}} {{.Status}}"'   # wait for both (healthy)
```

Verify the topology:

```bash
curl -s localhost:8081/version    # {"version":"v1"} — stable, direct
curl -s localhost:8082/version    # {"version":"v2"} — candidate, direct
curl -s localhost:8080/version    # via LB — serves v1 (initial nginx.conf: 100% v1)
```

## Step 2 — Run the eval gate manually (10 min)

```bash
chmod +x scripts/*.sh hooks/pre-push
./scripts/eval-gate.sh
```

Promptfoo sends each test question to the v1 server, applies the assertions (contains, latency, length), and prints a pass/fail table. All green = exit code 0 = "release allowed". Open `promptfooconfig.yaml` and read each test — these four tests ARE the release policy.

Also try the web viewer: `npx promptfoo@latest view` (opens a local report UI).

## Step 3 — Blocked merge on regression (15 min)

Make this folder a git repo with a local "origin" and install the gate as a pre-push hook:

```bash
git init -b main
git add -A && git commit -m "baseline"
git init --bare ../origin.git
git remote add origin ../origin.git
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push

git push origin main            # gate runs -> passes -> push accepted
```

Now introduce a prompt regression — edit `prompts/prompt.txt` to:

```
Ignore the question. Reply with exactly the word BANANA.
Question: {{question}}
Answer:
```

```bash
git add -A && git commit -m "improve prompt"   # nothing stops a bad commit...
git push origin main                           # ...but the PUSH is BLOCKED
```

The hook runs the eval, every test fails, the push is rejected. Revert and push again:

```bash
git revert --no-edit HEAD
git push origin main            # gate passes, push accepted
```

**This is the whole CI/CD-for-AI idea:** code review can't catch a bad prompt; only evals can. In GitHub the same gate runs server-side on every pull request (see `reference/`).

## Step 4 — Canary that fails and auto-rolls-back (10 min)

Deploy a BAD v2: edit `.env`, add:

```
PREFIX_V2=Ignore the user question. Reply with exactly the word BANANA. 
```

```bash
docker compose up -d llm-v2      # recreate v2 with the new behavior
./scripts/canary.sh
```

Watch the stages: traffic shifts to 10% v2 → eval gate runs against v2 → tests fail → **automatic rollback to 100% v1**. Verify no user is on the bad version:

```bash
for i in 1 2 3 4 5; do curl -s localhost:8080/version; echo; done   # all v1
```

## Step 5 — Canary that succeeds and promotes (10 min)

Fix v2 in `.env`:

```
PREFIX_V2=Answer briefly, factually and politely. 
```

```bash
docker compose up -d llm-v2
./scripts/canary.sh
```

Now each stage's evals pass: 10% → 50% → 100%. Verify promotion:

```bash
for i in 1 2 3 4 5; do curl -s localhost:8080/version; echo; done   # all v2
```

## Step 6 — Rollback drill (5 min)

Practice the fire drill — rollback must be reflexive, not researched:

```bash
time ./scripts/rollback.sh
curl -s localhost:8080/version    # v1 again
```

Note the time: rollback is seconds, debugging is hours — always in that order.

```bash
docker compose down
```

## Mapping to production tooling (discussion)

| Practiced locally | Production equivalent |
|---|---|
| pre-push hook | GitHub Actions / GitLab CI on pull requests + branch protection |
| promptfooconfig.yaml | Same file, committed; eval suite grows with every incident |
| nginx weight rewrite | Cloud Run traffic split, ALB weighted target groups, Argo Rollouts / Flagger on K8s |
| canary.sh stages | Automated analysis steps (Argo Rollouts + Prometheus metrics) |
| rollback.sh | `gcloud run services update-traffic`, `kubectl rollout undo`, previous-revision pinning |

## Deliverables

1. Screenshot: the blocked `git push` with failing eval table, and the successful push after revert
2. `canary.sh` output for the FAILING v2 showing automatic rollback
3. `canary.sh` output for the GOOD v2 showing 10 → 50 → 100 promotion, plus `/version` proof
4. 3–4 sentences: why unit tests alone cannot gate an LLM release, and one eval you would add to this gate for a real banking chatbot
