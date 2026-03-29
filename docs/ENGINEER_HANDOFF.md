# Engineer Handoff

This document is the operational handoff for publishing the Tom Wood Workshop
Service Order System.

## 1. Current State

- App type: single Python process serving API and frontend
- Database: SQLite
- Frontend: single HTML file served by the same process
- Default port: `8484`
- Current package mode: demo mode, no enforced sign-in in the live UI

## 2. Recommended Deployment Shape

Use a single instance with a persistent disk.

Recommended baseline:

- reverse proxy with HTTPS
- persistent database path outside the repo checkout
- daily SQLite backups
- internal access control at network or proxy level

Good fits:

- internal Linux VM
- internal Docker host
- Railway with a volume

Not recommended:

- multiple writable replicas against one SQLite file
- direct open-internet exposure of this demo-mode branch

## 3. Local Smoke Test

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

Verify:

```bash
curl http://localhost:8484/api/health
curl http://localhost:8484/api/stats
open http://localhost:8484
```

Reset demo data if needed:

```bash
python3 start.py --reset
```

## 4. Environment Variables

Minimum production variables:

```bash
PORT=8484
DB_PATH=/srv/tomwood-workshop/data/tomwood.db
COOKIE_SECURE=1
SESSION_TTL_HOURS=12
```

Optional:

```bash
SESSION_COOKIE_NAME=tw_session
GOOGLE_CLIENT_ID=...
GOOGLE_HOSTED_DOMAIN=tomwood.no
```

## 5. Docker Deployment

Build:

```bash
docker build -t tomwood-workshop .
```

Run:

```bash
docker run -d \
  --name tomwood-workshop \
  -p 8484:8484 \
  -e PORT=8484 \
  -e DB_PATH=/app/data/tomwood.db \
  -e COOKIE_SECURE=1 \
  -v tomwood_data:/app/data \
  --restart unless-stopped \
  tomwood-workshop
```

An example compose file is included at
[ops/docker-compose.example.yml](../ops/docker-compose.example.yml).

## 6. Linux VM Deployment

Suggested filesystem layout:

- code: `/srv/tomwood-workshop`
- venv: `/srv/tomwood-workshop/.venv`
- data: `/srv/tomwood-workshop/data`
- backups: `/srv/tomwood-workshop/backups`

Install:

```bash
sudo mkdir -p /srv/tomwood-workshop/data /srv/tomwood-workshop/backups
sudo chown -R $USER /srv/tomwood-workshop
cd /srv/tomwood-workshop
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set at least:

```bash
PORT=8484
DB_PATH=/srv/tomwood-workshop/data/tomwood.db
COOKIE_SECURE=1
```

Then install:

- systemd unit from
  [ops/systemd/tomwood-workshop.service.example](../ops/systemd/tomwood-workshop.service.example)
- nginx config from
  [ops/nginx/tomwood-workshop.conf.example](../ops/nginx/tomwood-workshop.conf.example)

## 7. Railway Deployment

1. Create a service from this repository.
2. Add a persistent volume.
3. Mount it and set `DB_PATH` to the mounted file path.
4. Set `PORT` to Railway's provided port if needed.
5. Keep the service single-instance.
6. Add a custom domain or use Railway's generated URL.

## 8. Backup and Restore

Backup script included:

- [ops/scripts/backup_sqlite.sh](../ops/scripts/backup_sqlite.sh)

Example:

```bash
./ops/scripts/backup_sqlite.sh \
  /srv/tomwood-workshop/data/tomwood.db \
  /srv/tomwood-workshop/backups
```

Restore:

1. Stop the app service.
2. Decompress the chosen backup if needed.
3. Replace the live `tomwood.db`.
4. Start the app service again.

## 9. Security Notes

This package should currently be treated as unauthenticated unless protected by
external access control.

Important detail:

- The live UI is in demo mode.
- App admin restrictions are based on the selected current operator inside app
  settings, not on a signed-in identity.
- Do not expose this branch directly to the public internet without adding a
  real access layer.

Safer deployment choices:

- internal VPN only
- reverse proxy SSO
- Cloudflare Access
- Google IAP
- company identity-aware proxy

## 10. Suggested Go-Live Checklist

1. Run local smoke test.
2. Choose Docker or Linux service deployment.
3. Put SQLite on persistent storage.
4. Configure HTTPS and internal access control.
5. Turn on daily backups.
6. Verify `/api/health`, `/api/stats`, and the homepage.
7. Share only the protected internal URL.
