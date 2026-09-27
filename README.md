# Agent Relay (SQLite starter)

Agent Relay is a small FastAPI service for registering agents, delivering one
task at a time, and recording results. The local starter is self-contained:
SQLite persists the queue and attempts, while workers execute tasks on their own
machines. The included worker deterministically returns `input.upper()`.

## Run it

```bash
uv sync
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> for the token-based local dashboard. The default
database is `./agent-relay.db`; set `RELAY_DATABASE_URL` to use another SQLite
file. `GET /health` is a liveness check and `GET /ready` verifies database
connectivity and schema (it queries the real tables, so a wiped volume
reports not-ready instead of passing with zero tables).

Register two identities and send a task:

```bash
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"uppercase"}')
```

The response contains each agent's secret `token` once. Keep it outside source
control. Use `Authorization: Bearer <token>` for all subsequent API calls;
registration is the only unauthenticated endpoint. For a shared installation,
set `RELAY_ENROLLMENT_SECRET` and send it as `X-Enrollment-Secret` when
registering.

## Run the deterministic worker

The worker can register itself and save credentials in a mode-0600 JSON file:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1
```

For failure/redelivery demonstrations, make local execution intentionally slow
and stop the process after one completion:

```bash
uv run python main.py worker --credentials ./uppercase-credentials.json \
  --slow-seconds 75 --worker-id slow-laptop
```

The worker heartbeats during long work. Killing it leaves the claim leased;
after the 60-second lease expires, another worker can claim the task with a new
token and incremented attempt number. `RELAY_LEASE_SECONDS` and
`RELAY_MAX_ATTEMPTS` are configurable server settings.

An existing credential can also be supplied explicitly (the token is not
written to disk):

```bash
uv run python main.py worker --agent-id agent_123 --token agt_… --worker-id laptop-2
```

## Storage and delivery behavior

`database.py` contains SQLAlchemy models, SQLite WAL setup, and the isolated
`BEGIN IMMEDIATE` transaction helper. `storage.py` contains task/claim/recovery
operations; routes and request models are kept in `main.py` and `schemas.py`.
SQLite does not provide PostgreSQL's `FOR UPDATE SKIP LOCKED`, so the starter
serializes writer transactions to make concurrent claims safe across processes.
Students can port this storage seam to PostgreSQL later without changing the
HTTP protocol or lifecycle in `SPEC.md`.

Claims are at-least-once and leased for 60 seconds by default. Heartbeats extend
an active lease. A completion or failure must include the recipient's bearer
token and claim token. Repeating the exact terminal request with that claim
token is idempotent; a stale token or different result receives `409`.

## Verify

The test suite covers the main protocol, sender/recipient access boundaries,
hashed claim-token behavior, idempotent terminal retries, concurrent claims,
lease expiry before and after recovery, pagination/error shape, and dashboard
asset serving:

```bash
uv run pytest -q
```

Tests default to a scratch database at `/tmp/agent-relay-test.db` so they
don't reset your dev server's `./agent-relay.db`. The fixture drops and
recreates all tables on whatever `RELAY_DATABASE_URL` points at, so stop
the dev server first or set `RELAY_DATABASE_URL` to a scratch file before
running tests against another database.

This starter intentionally does not include Docker, Kubernetes, CI, external
brokers, an LLM, or a PostgreSQL implementation. Those are deployment and
student-port concerns rather than part of the local relay protocol.

## Homework 3: containerise and deploy (step-by-step)

This section documents the exact steps performed for HW3 in this repo.

### Q1: Understand the project

Answer: **Agents claim tasks from a DB through an HTTP API.**

Why: `SPEC.md` defines a Relay API backed by a database (SQLite starter,
PostgreSQL port); `main.py` exposes `POST /api/v1/tasks/claim` and terminal
endpoints; `storage.py:claim_one()` moves one `queued` task to `processing`
with a leased delivery attempt. There is no broker and no direct
agent-to-agent channel; the dashboard only reads through the API.

Run it:

```bash
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

### Q2: Register agents and test the task flow

Answer: the sender sees **`completed`** after the recipient submits its result.

Flow performed against the live API:

1. `POST /api/v1/agents` as `alice-sender`, keep `token`.
2. `POST /api/v1/agents` as `bob-worker`, keep `token`.
3. Sender: `POST /api/v1/tasks` with `{"to": "<bob id>", "input": "..."}` → `queued`.
4. Recipient: `POST /api/v1/tasks/claim` → `200` with `claim_token`.
5. Recipient: `POST /api/v1/tasks/{id}/complete` with that `claim_token`.
6. Sender: `GET /api/v1/tasks/{id}` → `status: completed`.
7. `GET /` returns the dashboard (200).

This is codified in `test_integration_task_flow.py`
(`test_q2_two_agents_exchange_task_and_result`), which runs against the real
API + DB and asserts the sender-visible status is `completed`:

```bash
uv run pytest test_integration_task_flow.py test_agent_relay.py -q
# 5 passed
```

### Q3: Containerization

Answer: **`-p`** publishes a container port to the host.

`Dockerfile` builds a slim runtime image and binds uvicorn to all interfaces
(otherwise `-p` looks broken because uvicorn defaults to `127.0.0.1`):

```dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t agent-relay:local .
docker run -d --name agent-relay-test -p 8000:8000 agent-relay:local
```

The Q2 flow was repeated against `http://127.0.0.1:8000` on the containerized
API (register → send → claim → complete → sender sees `completed`, dashboard
200). Cleanup: `docker rm -f agent-relay-test`.

### Q4: Docker Compose and PostgreSQL

Answer: the API must use hostname **`postgres`** (the Compose service name).

`database.py:immediate_transaction()` branches: SQLite keeps `BEGIN
IMMEDIATE`; PostgreSQL falls back to a normal session transaction.
`storage.py:claim_one()` adds `FOR UPDATE SKIP LOCKED` on PostgreSQL so
concurrent workers skip locked rows.

`compose.yaml` runs both services:

- `postgres` (`postgres:16-alpine`, `pgdata` volume, `pg_isready` healthcheck)
- `api` (`build: .`, `RELAY_DATABASE_URL=postgresql+psycopg://relay:relay@postgres:5432/relay`,
  `depends_on: postgres healthy`)

Run and verify:

```bash
docker compose up --build -d
# repeat the Q2 flow against http://127.0.0.1:8000
docker compose exec postgres psql -U relay -d relay -c "SELECT id, status FROM tasks;"
docker compose down
```

The task row is stored in PostgreSQL (`completed`), confirming the app no
longer uses SQLite in this stack.

### Q5: Deploy to Kubernetes (kind)

Answer: a **`Deployment`** keeps the requested replica count running and
manages updates.

Manifests in `k8s/`:

- `postgres-pvc.yaml` — 1Gi `ReadWriteOnce` persistent storage
- `postgres-deployment.yaml` — `postgres:16-alpine`, PVC mount,
  `pg_isready` readiness/liveness probes
- `postgres-service.yaml` — `ClusterIP` on 5432
- `api-deployment.yaml` — `replicas: 2`, `imagePullPolicy: Never`,
  `RELAY_DATABASE_URL=...@postgres:5432/relay`, readiness `/ready`,
  liveness `/health`, RollingUpdate strategy
- `api-service.yaml` — `ClusterIP` on 8000

Deploy:

```bash
kind load docker-image agent-relay:local --name kind
kubectl apply -f k8s/
kubectl get pvc,pods,svc,deploy   # postgres Bound/Running, agent-relay 2/2 Available
kubectl port-forward svc/agent-relay 8000:8000
# repeat the Q2 flow against http://127.0.0.1:8000 -> sender sees completed
```

Note: API pods may `CrashLoopBackOff` once if they start before PostgreSQL is
ready (`init_db()` runs at import); they become `1/1 Running` after restart
once postgres is healthy.

### Q6: CI/CD (act + v2)

Answer: if a test fails, **keep the existing version running and stop the
deployment** (deploy only runs when tests pass).

`.github/workflows/ci.yml`:

- `test` — postgres service, install deps, `pytest test_agent_relay.py
  test_integration_task_flow.py` with
  `RELAY_DATABASE_URL=postgresql+psycopg://relay:relay@localhost:5432/relay`
- `build-and-deploy` — `needs: test`; builds `agent-relay:$GITHUB_SHA`
  (unique tag per version), `kind load docker-image`, `kubectl set image`,
  `kubectl rollout status --timeout=180s`

Run locally with act (stop any host process on 5432 first, e.g. an unrelated
local postgres, because the postgres service maps 5432):

```bash
act -j test -P ubuntu-latest=catthehacker/ubuntu:act-latest
# 5 passed against PostgreSQL, Job succeeded
```

v2 release:

1. Change `dashboard.html:18` to `<h1>Agent Relay v2</h1>`.
2. Re-run local `pytest` and `act -j test` (both pass).
3. Build/load/roll out the new tag and wait:

```bash
docker build -t agent-relay:v2 .
kind load docker-image agent-relay:v2 --name kind
kubectl set image deployment/agent-relay api=agent-relay:v2
kubectl rollout status deployment/agent-relay --timeout=180s
kubectl port-forward svc/agent-relay 8000:8000
# GET / contains <h1>Agent Relay v2</h1>; task flow still returns completed
```

### Homework answers (summary)

1. Agents claim tasks from a DB through an HTTP API.
2. `completed`
3. `-p`
4. `postgres`
5. `Deployment`
6. Keep the existing version running and stop the deployment.
