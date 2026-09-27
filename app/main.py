import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.routes import router
from app.schemas import HealthOut, ReadyOut
from app.security import LoginLimiter

logger = logging.getLogger("opsticket.requests")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.propagate = False


def create_app(settings: Settings | None = None, engine=None, redis_client=None) -> FastAPI:
    settings = settings or Settings()
    engine = (
        engine
        if engine is not None
        else create_engine(
            settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3}
        )
    )
    redis_client = (
        redis_client
        if redis_client is not None
        else Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    )

    @asynccontextmanager
    async def lifespan(app):
        yield
        redis_client.close()
        engine.dispose()

    app = FastAPI(
        title="OpsTicket API",
        version="0.1.0",
        description=(
            "Tenant-isolated operations tickets. Register a workspace admin, then "
            "Authorize with email/password. Admins provision agents and viewers. "
            "Viewers are read-only; agents manage tickets/comments; admins also delete."
        ),
        openapi_tags=[
            {"name": "Auth", "description": "Registration, JWT access tokens, current account"},
            {"name": "Users", "description": "Tenant membership and admin provisioning"},
            {"name": "Tickets", "description": "Tenant-scoped CRUD, assignment and closure"},
            {"name": "Comments", "description": "Ticket discussion; admin and agent writes"},
            {"name": "Operations", "description": "Liveness and dependency readiness"},
        ],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.state.redis = redis_client
    app.state.login_limiter = LoginLimiter(redis_client, settings)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", incoming) else str(uuid.uuid4())
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Do not log bodies, tokens, credentials, query strings or driver exception values.
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route.path if route else "unmatched",
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        )
        return response

    @app.get("/health", response_model=HealthOut, tags=["Operations"])
    def health():
        return {"status": "ok"}

    @app.get(
        "/ready", response_model=ReadyOut, tags=["Operations"], responses={503: {"model": ReadyOut}}
    )
    def ready():
        database_ok = redis_ok = False
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                # An uninitialized or outdated DB is not ready to serve the API.
                database_ok = (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    == "0002_ticket_indexes"
                )
        except SQLAlchemyError:
            pass
        try:
            redis_ok = bool(redis_client.ping())
        except RedisError:
            pass
        ok = database_ok and redis_ok
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "unavailable",
                "database": database_ok,
                "redis": redis_ok,
            },
        )

    app.include_router(router)
    return app
