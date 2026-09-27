import os
import uuid

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from alembic import command
from app.config import Settings
from app.main import create_app

PASSWORD = "Test-password-123!"


class MemoryRedis:
    """Deterministic offline counter; real Lua and expiry are tested with TEST_REDIS_URL."""

    def __init__(self):
        self.counters = {}

    def eval(self, script, numkeys, key, window):
        self.counters[key] = self.counters.get(key, 0) + 1
        return [self.counters[key], int(window)]

    def ping(self):
        return True

    def close(self):
        pass


class NamespacedRedis:
    """Never flush an external Redis database; delete only this test's keys."""

    def __init__(self, url):
        self.client = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self.prefix = f"opsticket-test:{uuid.uuid4()}:"
        self.keys = set()

    def eval(self, script, numkeys, key, window):
        key = self.prefix + key
        self.keys.add(key)
        return self.client.eval(script, numkeys, key, window)

    def ping(self):
        return self.client.ping()

    def close(self):
        if self.keys:
            self.client.delete(*self.keys)
        self.client.close()


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-longer-than-32-characters")
    url = os.environ.get("TEST_DATABASE_URL")
    control = None
    if url:
        # Each test owns a fresh schema, never the user's public schema or tables.
        schema = "test_" + uuid.uuid4().hex
        control = create_engine(url)
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        db_engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        db_engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(db_engine, "connect")
        def foreign_keys(dbapi, record):
            dbapi.execute("PRAGMA foreign_keys=ON")

    with db_engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield db_engine
    db_engine.dispose()
    if control:
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


@pytest.fixture
def redis_client():
    url = os.environ.get("TEST_REDIS_URL")
    client = NamespacedRedis(url) if url else MemoryRedis()
    yield client
    client.close()


@pytest.fixture
def client(engine, redis_client):
    settings = Settings(
        jwt_secret="test-only-secret-longer-than-32-characters", login_rate_limit=100
    )
    app = create_app(settings, engine, redis_client)
    # Lifespan is deliberately not entered: fixture owns engine/Redis teardown.
    http = TestClient(app)
    yield http
    http.close()


def register(client, email="admin@example.com"):
    return client.post(
        "/auth/register",
        json={
            "name": "Admin",
            "tenant_name": "Operations",
            "email": email,
            "password": PASSWORD,
        },
    )


def login(client, email="admin@example.com", password=PASSWORD):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def admin(client):
    response = register(client)
    assert response.status_code == 201, response.text
    return response.json(), login(client)


@pytest.fixture
def members(client, admin):
    users = {"admin": admin}
    for role in ("agent", "viewer"):
        email = f"{role}@example.com"
        response = client.post(
            "/users",
            headers=admin[1],
            json={
                "name": role.title(),
                "email": email,
                "password": PASSWORD,
                "role": role,
            },
        )
        assert response.status_code == 201, response.text
        users[role] = response.json(), login(client, email)
    return users


@pytest.fixture
def ticket(client, admin):
    response = client.post("/tickets", headers=admin[1], json={"title": "VPN outage"})
    assert response.status_code == 201, response.text
    return response.json()
