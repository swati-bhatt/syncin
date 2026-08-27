# Syncin — dev workflow shortcuts.
# Host ports default to 5433/6380 so they don't clash with a local Postgres/Redis
# on 5432/6379. Override per call, e.g.:  make up POSTGRES_HOST_PORT=5544
POSTGRES_HOST_PORT ?= 5433
REDIS_HOST_PORT    ?= 6380
APP_PORT           ?= 3000

DC = POSTGRES_HOST_PORT=$(POSTGRES_HOST_PORT) REDIS_HOST_PORT=$(REDIS_HOST_PORT) APP_PORT=$(APP_PORT) docker compose -p syncin

.PHONY: up rebuild down clean prune logs ps psql

# up: build + (re)start, then delete the old dangling image left behind
up:
	$(DC) up -d --build
	docker image prune -f

# rebuild: force a from-scratch app image build, then delete the old one
rebuild:
	$(DC) build --no-cache app
	$(DC) up -d
	docker image prune -f

# down: stop + remove containers (KEEPS the data volumes)
down:
	$(DC) down

# clean: stop + remove containers AND volumes (wipes the DB), then prune images
clean:
	$(DC) down -v
	docker image prune -f

# prune: delete old/dangling images right now
prune:
	docker image prune -f

# logs: follow the app logs
logs:
	$(DC) logs -f app

# ps: container status
ps:
	$(DC) ps

# psql: open a psql shell on the Syncin DB
psql:
	$(DC) exec postgres psql -U syncin -d syncin

# typecheck: run tsc --noEmit inside the app container
typecheck:
	$(DC) exec app npx tsc --noEmit
