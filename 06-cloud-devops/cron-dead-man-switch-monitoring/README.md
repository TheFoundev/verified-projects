# Cron Dead-Man-Switch Monitoring

> An original, lightweight cron heartbeat/dead-man-switch service for detecting missed or late jobs and dispatching multi-channel alerts.

## Overview

An original, self-contained implementation of a cron dead-man-switch/heartbeat monitoring service (healthchecks-style), built for the Foundev portfolio. It provides ping-based liveness checks, a state machine for tracking job health, grace periods, and multi-channel alerting with deduplication and retry logic. This is an original design and implementation — not a copy of the referenced project.

## Features

- **Checks**: Named cron checks with schedule specs (interval seconds; simplified cron-like `mon-sun,HH:MM` supported in model design). Each check has grace period, timeout, and tracks `new → up → late → down → up` state transitions.
- **State Machine**: Tracks success/failure counters, `last_ping`, `next_scheduled_at`. Late state triggered after grace period exceeded; down state after timeout exceeded. Recovery on ping returns check to up.
- **Ping Endpoints (REST API)**: `GET/HEAD /api/v1/checks/<uuid>/ping` with optional `?state=success|failure`, plus `POST /api/v1/checks/<uuid>/ping` variant. Unauthenticated pings use secret UUID (like healthchecks). JSON status endpoint at `GET /api/v1/checks/<uuid>/status`.
- **Authentication**: Separate read/write API tokens per check for CRUD operations. Write operations require token authentication via `Authorization: Bearer <token>` or `X-API-Token` header.
- **Alerts**: Multi-channel dispatcher with abstract interface supporting **email**, **generic webhook (JSON POST)**, and **log/stdout** channels. Alerts fire on transitions to late/down and on recovery to up. Alert-once-per-transition semantics enforced by the caller; configurable per-check alert channels; simple mute window via `mute_until`. Webhooks include retry with exponential backoff (in-process). All alerts recorded in `Alert` history rows.
- **Background Poller**: Management command `run_poller` evaluates due checks in a loop, detecting late/down transitions and dispatching alerts.
- **Config**: Environment-driven settings via `os.environ` (`SECRET_KEY` with dev default, `DEBUG`, `DATABASE_URL` parsing for `postgres://`/`sqlite3` with sqlite3 fallback, `ALLOWED_HOSTS`, `EMAIL_*`, `BASE_URL`).

## Stack

`Python`
## How it works

**Ping flow:**
1. External cron job sends HTTP GET/HEAD/POST to ping endpoint with check UUID
2. Ping recorded in database with timestamp and state (success/failure)
3. Check state updated: if was late/down/new, transitions to up on ping; counters incremented
4. `next_scheduled_at` computed based on schedule

**State machine (text diagram):**
```
new --ping--> up
up --(grace exceeded, no ping)--> late --(timeout exceeded, no ping)--> down
late --ping--> up (recovery)
down --ping--> up (recovery)
```

Grace period allows a buffer after expected time before marking late. Timeout determines when late becomes down. Alerts dispatch on transitions to late/down and on recovery (up from late/down).

## Run

### Local development (venv + sqlite)

```bash
cd cron-dead-man-switch-monitoring
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

In a separate terminal, run the poller:
```bash
source .venv/bin/activate
python manage.py run_poller
```

### Environment variables

Copy `.env.example` to `.env` and configure:
- `SECRET_KEY`: Django secret key (change in production)
- `DEBUG`: True/False
- `DATABASE_URL`: `sqlite:///db.sqlite3` (default) or `postgres://user:pass@host:port/dbname`
- `ALLOWED_HOSTS`: Comma-separated hostnames
- `EMAIL_BACKEND`, `EMAIL_HOST`, etc. for email alerts
- `BASE_URL`: Base URL for check links

### Docker Compose

```bash
docker-compose up --build
```

Services: `web` (Django dev server), `poller` (background evaluator), `postgres` (database). Note: Postgres/Docker are not installed in this environment - configuration is provided for deployment.

## API reference

| Endpoint | Method | Auth | Description |
| --- | --- | --- | --- |
| `/api/v1/checks` | GET | Bearer token (header) | List all checks |
| `/api/v1/checks` | POST | Bearer token (header) | Create a new check |
| `/api/v1/checks/<uuid>` | GET | Bearer token (per-check) | Get check details |
| `/api/v1/checks/<uuid>` | PUT | Bearer token (per-check) | Update check |
| `/api/v1/checks/<uuid>` | DELETE | Bearer token (per-check) | Delete check |
| `/api/v1/checks/<uuid>/ping` | GET/HEAD/POST | None (UUID secret) | Ping endpoint - report job execution |
| `/api/v1/checks/<uuid>/status` | GET | None | Get check status (JSON) |

**Ping examples:**
```bash
curl http://localhost:8000/api/v1/checks/<uuid>/ping
curl http://localhost:8000/api/v1/checks/<uuid>/ping?state=failure
curl -X POST http://localhost:8000/api/v1/checks/<uuid>/ping -H "Content-Type: application/json" -d '{"state":"success"}'
```

**CRUD example:**
```bash
curl -X POST http://localhost:8000/api/v1/checks -H "Authorization: Bearer mytoken" -H "Content-Type: application/json" -d '{"name":"Backup Job","interval_seconds":3600,"grace_period_seconds":300}'
```

## Structure tree

```text
cron-dead-man-switch-monitoring/
├── config/
│   ├── __init__.py
│   ├── settings.py      # Environment-driven Django settings
│   ├── urls.py          # URL routing
│   └── wsgi.py
├── core/
│   ├── admin.py         # Django admin configuration
│   ├── api.py           # REST API views (hand-rolled with JsonResponse)
│   ├── apps.py
│   ├── dispatch.py      # Multi-channel alert dispatcher
│   ├── models.py        # Check, Ping, AlertChannel, Alert models
│   ├── state.py         # State machine logic
│   ├── management/
│   │   └── commands/
│   │       └── run_poller.py  # Background poller
│   ├── migrations/
│   └── tests/
│       ├── test_api.py
│       ├── test_alerts.py
│       └── test_state.py
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── LICENSE              # BSD-3-Clause
└── README.md
```

## Verification note

**Environment:** Python 3.14.7, pip 26.2.1, Django 5.2.18 installed in `.venv/`. Postgres and Docker not installed.

**Exact commands run:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # Django>=5.0,<6 resolved to Django-5.2.18
python manage.py makemigrations core
python manage.py migrate
python manage.py test
python manage.py check
```

**Results:** All commands executed successfully. The test suite covers ping endpoint transitions (new→up, late grace, down timeout, recovery→up), token auth, API token CRUD auth, state machine math including grace/late boundaries, webhook channel POST with mocked urllib, email channel with locmem backend, alert-once-per-transition semantics, and mute window functionality.

## Known limitations

- **Postgres/Docker**: Configuration files provided for Postgres and Docker deployment, but neither is installed/exercised in this environment. SQLite is used for local development and tests.
- **Email**: Email channel uses Django's email backend (console/locmem by default). Real SMTP configuration required for production use via `EMAIL_*` environment variables.
- **Cron spec parsing**: The model supports simplified `mon-sun,HH:MM` format in the schema, but full cron parsing is not implemented - interval-based scheduling is the primary mechanism.
- **Alert retries**: Webhook retries use in-process blocking sleep; not suitable for high-throughput production use without a task queue.
- **Poller**: Simple polling loop without distributed locking - suitable for single-instance deployments.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
