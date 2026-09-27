import os
import time

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command


def test_migration_upgrade_downgrade_and_model_parity(engine):
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.check(config)
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "0002_ticket_indexes"
        )
        command.downgrade(config, "0001_initial")
        assert "ix_tickets_tenant_active_created" not in {
            index["name"] for index in inspect(connection).get_indexes("tickets")
        }
        command.downgrade(config, "base")
        assert "tickets" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        command.check(config)


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="Requires real Redis")
def test_real_redis_lua_counter_expires(redis_client):
    from app.config import Settings
    from app.security import LoginLimiter

    limiter = LoginLimiter(
        redis_client,
        Settings(
            jwt_secret="test-only-secret-longer-than-32-characters",
            login_rate_limit=1,
            login_window_seconds=1,
        ),
    )
    assert limiter.check("expiry@example.com", "127.0.0.1") == 0
    assert limiter.check("expiry@example.com", "127.0.0.1") > 0
    time.sleep(1.1)
    assert limiter.check("expiry@example.com", "127.0.0.1") == 0
