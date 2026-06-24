# Syncin — command reference

Run everything from this folder (`~/Desktop/x/syncin`). Docker Desktop must be running.

## Everyday (Makefile shortcuts)

| Command | What it does |
|---|---|
| `make up` | Build + start the stack (Postgres + Redis + app), then auto-delete the old build image |
| `make down` | Stop & remove containers — **keeps** your data |
| `make clean` | Stop & remove containers **and** volumes — **wipes** the DB |
| `make rebuild` | Force a from-scratch app rebuild, then prune the old image |
| `make logs` | Follow the app logs |
| `make ps` | Show container status |
| `make psql` | Open a `psql` shell on the Syncin database |
| `make prune` | Delete dangling (old) build images right now |

Host ports default to **5433** (Postgres) / **6380** (Redis) / **3000** (app) so they
don't clash with a local Homebrew Postgres/Redis on 5432/6379.
Override per call, e.g.: `make up APP_PORT=8080`.

## Test the API (stack must be up)

Seeded demo tenant API key: `sk_demo_tenant_key_123`

```bash
# health / readiness
curl localhost:3000/health
curl localhost:3000/ready

# create a booking  -> 201 + JSON
curl -X POST localhost:3000/v1/events \
  -H "Authorization: Bearer sk_demo_tenant_key_123" \
  -H "Idempotency-Key: demo-001" -H "Content-Type: application/json" \
  -d '{"recipient":{"name":"Jane Doe","phone":"+15551234567"},"startsAt":"2026-09-01T14:30:00-04:00"}'

# repeat the SAME Idempotency-Key -> identical response, no duplicate
```

## Raw Docker (if you ever skip `make`)

```bash
# start
POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6380 docker compose -p syncin up -d --build
# stop (keep data)
docker compose -p syncin down
# logs
docker compose -p syncin logs -f app
```

## Occasional Docker disk cleanup

```bash
docker image prune -f      # remove dangling (old) build images
docker builder prune -f    # remove old build CACHE (the big disk hog)
docker system df           # see what's using disk
```
