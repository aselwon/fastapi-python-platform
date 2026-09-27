# OpsTicket API

OpsTicket API is a multi-tenant REST API for internal operations ticketing. Registration creates a new tenant and its first administrator. The project demonstrates JWT authentication, role-based access control, PostgreSQL, Redis, Alembic migrations, soft deletion, audit fields, structured logging, tests, and Docker Compose.

## Public demo

No public demo is currently available. This repository is API-only; run it locally and use the Swagger UI at `/docs`.


## Stack

Python 3.12+, FastAPI, Pydantic v2, Uvicorn, SQLAlchemy 2.x, PostgreSQL 16, Alembic, Redis 7, Pytest, HTTPX, and Docker Compose.

## Architecture

```mermaid
flowchart LR
 C[Client / Swagger UI] --> A[FastAPI API]
 A --> JWT[JWT auth + RBAC]
 A --> DB[(PostgreSQL 16)]
 A --> R[(Redis 7<br/>login rate limiter)]
 M[Alembic migrations] --> DB
 A --> H["/health and /ready"]
```

The tenant is derived from the authenticated user and enforced in every data query. Public registration does not accept a tenant ID, so it always creates a new tenant. Email addresses are globally unique.

## Docker quick start

The simplest development start uses the default host ports 8000, 5432, and 6379:

```bash
docker compose up --build
```

If those ports are occupied, leave other containers running and choose free host ports. Redis can remain on its default host port:

```bash
export API_PORT=8085
export POSTGRES_PORT=55432
export JWT_SECRET="$(openssl rand -hex 32)"
docker compose up --build
```

`JWT_SECRET` is required by the application. Compose provides a development fallback, but deployments must use a strong random secret from a secret manager. Never commit secrets. Services are bound to `127.0.0.1`.

With the alternate ports, open [Swagger UI](http://localhost:8085/docs), [health](http://localhost:8085/health), or [readiness](http://localhost:8085/ready). The API container runs `alembic upgrade head` before Uvicorn. `docker compose down -v` also removes PostgreSQL and Redis volumes.

## Swagger walkthrough

1. Call `POST /auth/register` with, for example, `admin@example.com`, `Demo-password-123!`, `Demo Admin`, and `Acme Operations`. These are demo values entered through registration; there are no seed accounts.
2. Click **Authorize** and enter the email as `username` and the password as `password`. Swagger calls `POST /auth/token`. Tokens expire after 30 minutes.
3. Call `POST /tickets` with a title and optional description, then inspect it with `GET /tickets`.
4. Add discussion with `POST /tickets/{ticket_id}/comments`.

JSON clients can use `POST /auth/login` and send the returned token as `Authorization: Bearer <token>`.

## Configuration

Important variables are `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `ACCESS_TOKEN_MINUTES`, `LOGIN_RATE_LIMIT`, `LOGIN_WINDOW_SECONDS`, `API_PORT`, `POSTGRES_PORT`, and `REDIS_PORT`. Compose connects to the internal service names `postgres` and `redis`; published ports are host settings. Compose defaults are intended for development.

## Running without Docker

Install the locked development dependencies and point the local process at the intended services. The example below uses PostgreSQL on 55432 and Redis on 6379; port 5432 may belong to another project:

```bash
uv sync --frozen --extra dev
export DATABASE_URL='postgresql+psycopg://opsticket:opsticket@localhost:55432/opsticket'
export REDIS_URL='redis://localhost:6379/0'
export JWT_SECRET="$(openssl rand -hex 32)"
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --no-proxy-headers
```

Migration commands:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic downgrade base
```

The migration history contains `0001_initial` and `0002_ticket_indexes`. Readiness requires PostgreSQL at migration `0002_ticket_indexes` and a responsive Redis server.

## API overview

OpenAPI at `/docs` contains request and response examples.

- Auth: `POST /auth/register`, `POST /auth/login`, `POST /auth/token`, `GET /auth/me`.
- Users: `POST /users`, `GET /users`.
- Tickets: `POST /tickets`, `GET /tickets`, `GET/PATCH/DELETE /tickets/{id}`, `POST /tickets/{id}/assign`, `POST /tickets/{id}/close`.
- Comments: `POST/GET /tickets/{id}/comments`, `GET/PATCH/DELETE /tickets/{id}/comments/{comment_id}`.
- Operations: `GET /health`, `GET /ready`.

Ticket listing supports pagination (`limit`, `offset`), `status` and `assignee_id` filters, and sorting by `created_at`, `updated_at`, or `title` in ascending or descending order. Statuses are `open`, `in_progress`, and `closed`.

## Roles and security

- `admin`: manages tenant members, has full ticket and comment permissions, and can soft-delete resources.
- `agent`: creates, updates, assigns, and closes tickets; can comment and edit or delete their own comments.
- `viewer`: read-only access.

JWT claims identify the user and tenant, with a 30-minute default lifetime. Redis implements a fixed-window Lua rate limiter for both account and client IP. Login fails closed when Redis is unavailable. The application does not trust forwarded headers when determining the client IP. Tickets and comments are soft-deleted. Audit fields include `created_at`, `updated_at`, and `created_by`; bootstrap-created records may have a null creator. Request logs are structured JSON with a request ID and exclude passwords, tokens, bodies, and driver exception values.

## Tests and validation

Offline tests use SQLite in memory, Alembic migrations, and a fake Redis implementation:

```bash
uv run pytest
```

For real integration services, use an isolated PostgreSQL database and Redis namespace:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://opsticket:opsticket@localhost:55432/opsticket'
export TEST_REDIS_URL='redis://localhost:6379/15'
uv run pytest
```

Each integration run creates and removes its own PostgreSQL schema and Redis key namespace. Do not point these variables at production or shared data. The smoke test covers readiness, OpenAPI, registration, login, ticket CRUD, assignment, comments, closing, and soft deletion:

```bash
API_URL=http://localhost:8085 .venv/bin/python scripts/smoke.py
```

Local CI-equivalent checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The repository also contains a GitHub Actions lint-and-test workflow. Hosted Actions were not run because this checkout has no remote. A local Swagger acceptance run registered a test account, authorized successfully, and created a ticket with HTTP 201.

## Troubleshooting

- For `address already in use`, do not stop unrelated containers; set free `API_PORT` and `POSTGRES_PORT` values.
- If `/health` is 200 but `/ready` is 503, inspect `docker compose logs api postgres redis`, migration state, and Redis availability.
- If settings reject `JWT_SECRET`, generate a secret of at least 32 characters.
- After schema changes, rebuild Compose or run `uv run alembic upgrade head` locally.
- `POSTGRES_PORT` changes only the published host port; the API still reaches Compose PostgreSQL as `postgres:5432`.

## MVP trade-offs

This repository intentionally has no React UI, Kubernetes deployment, microservice split, or real email delivery. Redis is used for distributed login limiting rather than ticket-list caching, so the limit is shared across API instances. Multi-instance deployments should provide shared Redis, managed secrets, and environment isolation.
