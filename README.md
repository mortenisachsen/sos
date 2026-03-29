# Tom Wood Workshop Service Order System

Internal workshop application for service orders, clients, team assignment, and
workspace settings. The current package runs as a single Python process with a
SQLite database and a single-file frontend.

## What Is In This Package

- Python backend in [server.py](server.py)
- SQLite schema and seed data in [db_schema.py](db_schema.py)
- Frontend in [static/index.html](static/index.html)
- Local start helper in [start.py](start.py)
- Engineer handoff notes in [docs/ENGINEER_HANDOFF.md](docs/ENGINEER_HANDOFF.md)
- Deployment templates in [ops/](ops)

## Important Runtime Note

This branch is currently packaged in demo mode.

- The live UI is not enforcing Google sign-in right now.
- Admin-only controls inside the app are based on the selected current operator.
- For any shared or published environment, put the app behind a trusted access
  layer such as VPN, internal reverse-proxy SSO, Cloudflare Access, or re-enable
  app-level login before exposing it broadly.

The backend still contains Google auth/session endpoints, but the current UI is
not using them.

## Local Setup

1. Create and activate a virtual environment.
2. Install Python dependencies.
3. Start the app.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

Then open [http://localhost:8484](http://localhost:8484).

If you prefer the helper that opens a browser:

```bash
python3 start.py
```

To reset to demo seed data:

```bash
python3 start.py --reset
```

## Configuration

The app reads environment variables from the shell and, if present, from a local
`.env` file via [env_config.py](env_config.py).

Start from [.env.example](.env.example):

```bash
cp .env.example .env
```

### Supported Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8484` | Port the HTTP server binds to |
| `DB_PATH` | `db/tomwood.db` | Absolute path recommended for production SQLite file |
| `COOKIE_SECURE` | `0` | Set to `1` when served only over HTTPS |
| `SESSION_TTL_HOURS` | `12` | Session lifetime for backend auth sessions |
| `SESSION_COOKIE_NAME` | `tw_session` | Cookie name for backend auth sessions |
| `GOOGLE_CLIENT_ID` | empty | Optional backend Google token verification setting |
| `GOOGLE_HOSTED_DOMAIN` | empty | Optional domain restriction such as `tomwood.no` |

## Recommended Publish Model

This application is best deployed as:

- one app instance
- one persistent SQLite database file
- one reverse proxy or platform HTTPS endpoint
- scheduled backups of the SQLite file

SQLite is a good fit for a single workshop deployment. It is not a good fit for
multiple app replicas writing at the same time.

## Deployment Options

### Option A: Docker

Files included:

- [Dockerfile](Dockerfile)
- [ops/docker-compose.example.yml](ops/docker-compose.example.yml)

Quick start:

```bash
docker build -t tomwood-workshop .
docker run \
  -p 8484:8484 \
  -e PORT=8484 \
  -e DB_PATH=/app/data/tomwood.db \
  -v tomwood_data:/app/data \
  tomwood-workshop
```

### Option B: Linux VM or Internal Server

Files included:

- [ops/systemd/tomwood-workshop.service.example](ops/systemd/tomwood-workshop.service.example)
- [ops/nginx/tomwood-workshop.conf.example](ops/nginx/tomwood-workshop.conf.example)
- [ops/scripts/backup_sqlite.sh](ops/scripts/backup_sqlite.sh)

Recommended path layout:

- app code: `/srv/tomwood-workshop`
- venv: `/srv/tomwood-workshop/.venv`
- database: `/srv/tomwood-workshop/data/tomwood.db`
- backups: `/srv/tomwood-workshop/backups`

### Option C: Railway or Similar Single-Instance Platform

Use a single service and mount persistent storage for SQLite. Point `DB_PATH` to
the mounted volume path. Keep the deployment single-instance.

## Health Check

Use:

- `GET /api/health`

Example:

```bash
curl http://localhost:8484/api/health
```

## Main API Endpoints

Base URL: `http://localhost:8484`

| Method | Endpoint | Notes |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/stats` | Dashboard data |
| `GET` | `/api/orders` | List orders |
| `POST` | `/api/orders` | Create order |
| `PUT` | `/api/orders/:id` | Update order |
| `PATCH` | `/api/orders/:id/status` | Change status |
| `DELETE` | `/api/orders/:id` | Delete order |
| `GET` | `/api/clients` | List clients |
| `POST` | `/api/clients` | Create client |
| `GET` | `/api/goldsmiths` | List active team members |
| `GET` | `/api/settings` | Read workspace settings |
| `PUT` | `/api/settings` | Save settings, admin-only in app logic |

## Engineer Handoff

Start with [docs/ENGINEER_HANDOFF.md](docs/ENGINEER_HANDOFF.md). It includes:

- local smoke test
- production setup checklist
- Docker deployment steps
- Linux service deployment steps
- Railway deployment steps
- backup and restore notes
- security caveats for this demo-mode branch
