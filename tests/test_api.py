import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from conftest import PASSWORD, login, register
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Comment, Tenant, Ticket, User


def test_registration_creates_tenant_admin_and_hashes_password(client):
    response = register(client)
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "admin" and data["tenant_id"]
    assert "password_hash" not in data and "password" not in data
    with client.app.state.session_factory() as db:
        user = db.get(User, data["id"])
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
        assert db.get(Tenant, data["tenant_id"]).name == "Operations"


def test_duplicate_email_rolls_back_tenant(client, admin):
    assert register(client, "ADMIN@example.com").status_code == 409
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(Tenant)).all()) == 1


def test_login_me_and_oauth_form(client, admin):
    assert client.get("/auth/me", headers=admin[1]).json()["id"] == admin[0]["id"]
    response = client.post(
        "/auth/token", data={"username": "admin@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 1800
    assert response.json()["token_type"] == "bearer"


@pytest.mark.parametrize("email", ["missing@example.com", "admin@example.com"])
def test_invalid_credentials_generic_error(client, admin, email):
    response = client.post("/auth/login", json={"email": email, "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_password_whitespace_is_significant(client):
    payload = {
        "name": "Admin",
        "tenant_name": "Ops",
        "email": "space@example.com",
        "password": "  password-with-spaces  ",
    }
    assert client.post("/auth/register", json=payload).status_code == 201
    login(client, payload["email"], payload["password"])
    assert (
        client.post(
            "/auth/login", json={"email": payload["email"], "password": payload["password"].strip()}
        ).status_code
        == 401
    )


@pytest.mark.parametrize("mutation", ["expired", "signature", "tenant", "missing_claim"])
def test_invalid_jwt_rejected(client, admin, mutation):
    claims = {
        "sub": admin[0]["id"],
        "tenant_id": admin[0]["tenant_id"],
        "iss": "opsticket",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    secret = client.app.state.settings.jwt_secret
    if mutation == "expired":
        claims["exp"] = datetime.now(UTC) - timedelta(minutes=5)
    elif mutation == "signature":
        secret = "a-different-long-secret-at-least-32-chars"
    elif mutation == "tenant":
        claims["tenant_id"] = str(uuid4())
    else:
        del claims["exp"]
    token = jwt.encode(claims, secret, algorithm="HS256")
    assert client.get("/tickets", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_auth_required(client):
    assert client.get("/tickets").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-email"},
        {"password": "short"},
        {"name": " "},
        {"role": "admin"},
        {"tenant_id": "arbitrary"},
    ],
)
def test_register_validation_and_no_tenant_injection(client, payload):
    data = {"email": "new@example.com", "password": PASSWORD, "name": "New", "tenant_name": "Ops"}
    data.update(payload)
    assert client.post("/auth/register", json=data).status_code == 422


def test_admin_provisions_members_and_audit(client, members):
    users = client.get("/users", headers=members["viewer"][1]).json()
    assert len(users) == 3
    assert members["agent"][0]["created_by"] == members["admin"][0]["id"]
    assert all("password_hash" not in user for user in users)


@pytest.mark.parametrize("role", ["viewer", "agent"])
def test_only_admin_can_create_users(client, members, role):
    response = client.post(
        "/users",
        headers=members[role][1],
        json={
            "name": "New",
            "email": "new@example.com",
            "password": PASSWORD,
            "role": "admin",
        },
    )
    assert response.status_code == 403


def test_viewer_cannot_write_any_resource(client, members, ticket):
    headers = members["viewer"][1]
    path = f"/tickets/{ticket['id']}"
    for method, url, body in [
        ("post", "/tickets", {"title": "No"}),
        ("patch", path, {"title": "No"}),
        ("post", path + "/assign", {"assignee_id": None}),
        ("post", path + "/close", None),
        ("post", path + "/comments", {"body": "No"}),
        ("delete", path, None),
    ]:
        assert client.request(method, url, headers=headers, json=body).status_code == 403
    assert client.get(path, headers=headers).status_code == 200


def test_agent_ticket_lifecycle_and_admin_soft_delete(client, members):
    headers = members["agent"][1]
    response = client.post(
        "/tickets", headers=headers, json={"title": "New", "description": "Issue"}
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["created_by"] == members["agent"][0]["id"]
    assert ticket["created_at"] and ticket["updated_at"]
    path = f"/tickets/{ticket['id']}"
    assert client.get(path, headers=headers).json()["description"] == "Issue"
    updated = client.patch(
        path, headers=headers, json={"title": "Changed", "status": "in_progress"}
    )
    assert updated.json()["title"] == "Changed"
    assigned = client.post(
        path + "/assign", headers=headers, json={"assignee_id": members["agent"][0]["id"]}
    )
    assert assigned.json()["assignee_id"] == members["agent"][0]["id"]
    assert (
        client.post(path + "/assign", headers=headers, json={"assignee_id": None}).json()[
            "assignee_id"
        ]
        is None
    )
    assert client.post(path + "/close", headers=headers).json()["status"] == "closed"
    assert client.delete(path, headers=headers).status_code == 403
    assert client.delete(path, headers=members["admin"][1]).status_code == 204
    assert client.get(path, headers=headers).status_code == 404
    assert client.get("/tickets", headers=headers).json()["total"] == 0
    with client.app.state.session_factory() as db:
        assert db.get(Ticket, ticket["id"]).deleted_at is not None


def test_ticket_filter_pagination_sorting(client, admin):
    for title in ("Charlie", "Alpha", "Bravo"):
        client.post("/tickets", headers=admin[1], json={"title": title})
    page = client.get("/tickets?sort=title&order=asc&limit=1&offset=1", headers=admin[1]).json()
    assert page["total"] == 3 and page["limit"] == 1 and page["offset"] == 1
    assert page["items"][0]["title"] == "Bravo"
    path = "/tickets/" + page["items"][0]["id"]
    client.post(path + "/close", headers=admin[1])
    client.post(path + "/assign", headers=admin[1], json={"assignee_id": admin[0]["id"]})
    filtered = client.get(
        "/tickets",
        headers=admin[1],
        params={
            "status": "closed",
            "assignee_id": admin[0]["id"],
        },
    ).json()
    assert filtered["total"] == 1 and filtered["items"][0]["title"] == "Bravo"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "status=unknown",
        "sort=password_hash",
        "order=random",
        "assignee_id=bad",
    ],
)
def test_list_validation(client, admin, query):
    assert client.get("/tickets?" + query, headers=admin[1]).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{"title": ""}, {"title": " "}, {"title": "x" * 201}, {"title": "OK", "tenant_id": "x"}],
)
def test_ticket_create_validation(client, admin, payload):
    assert client.post("/tickets", headers=admin[1], json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"title": None},
        {"description": None},
        {"status": None},
        {"status": "invalid"},
        {"created_by": "x"},
    ],
)
def test_ticket_patch_validation(client, admin, ticket, payload):
    assert (
        client.patch(f"/tickets/{ticket['id']}", headers=admin[1], json=payload).status_code == 422
    )


def test_missing_or_invalid_ticket(client, admin):
    assert client.get(f"/tickets/{uuid4()}", headers=admin[1]).status_code == 404
    assert client.get("/tickets/not-uuid", headers=admin[1]).status_code == 422


def test_cross_tenant_isolation_all_ticket_operations(client, admin, ticket):
    other = register(client, "other@example.com").json()
    headers = login(client, other["email"])
    path = f"/tickets/{ticket['id']}"
    assert client.get("/tickets", headers=headers).json()["total"] == 0
    assert [u["id"] for u in client.get("/users", headers=headers).json()] == [other["id"]]
    for method, suffix, body in [
        ("get", "", None),
        ("patch", "", {"title": "stolen"}),
        ("delete", "", None),
        ("post", "/close", None),
        ("post", "/assign", {"assignee_id": other["id"]}),
        ("get", "/comments", None),
        ("post", "/comments", {"body": "intruder"}),
    ]:
        assert client.request(method, path + suffix, headers=headers, json=body).status_code == 404


def test_assignee_must_be_eligible_member(client, members, ticket):
    other = register(client, "external@example.com").json()
    for user_id in (other["id"], members["viewer"][0]["id"], str(uuid4())):
        assert (
            client.post(
                f"/tickets/{ticket['id']}/assign",
                headers=members["admin"][1],
                json={"assignee_id": user_id},
            ).status_code
            == 404
        )


def test_database_rejects_cross_tenant_assignment(client, admin, ticket):
    other = register(client, "outside@example.com").json()
    with client.app.state.session_factory() as db:
        row = db.get(Ticket, ticket["id"])
        row.assignee_id = other["id"]
        with pytest.raises(IntegrityError):
            db.commit()


def test_comment_crud_audit_pagination_and_ownership(client, members, ticket):
    path = f"/tickets/{ticket['id']}/comments"
    author = members["agent"][1]
    comment = client.post(path, headers=author, json={"body": "Investigating"})
    assert comment.status_code == 201
    comment = comment.json()
    assert comment["created_by"] == members["agent"][0]["id"]
    detail = path + "/" + comment["id"]
    assert client.get(detail, headers=members["viewer"][1]).json()["body"] == "Investigating"
    client.post(path, headers=author, json={"body": "Next"})
    assert len(client.get(path + "?limit=1&offset=1", headers=author).json()) == 1
    assert client.patch(detail, headers=author, json={"body": "Solved"}).json()["body"] == "Solved"
    for method in ("patch", "delete"):
        assert (
            client.request(
                method,
                detail,
                headers=members["viewer"][1],
                json={"body": "No"} if method == "patch" else None,
            ).status_code
            == 403
        )
    assert client.delete(detail, headers=author).status_code == 204
    assert client.get(detail, headers=author).status_code == 404
    with client.app.state.session_factory() as db:
        assert db.get(Comment, comment["id"]).deleted_at is not None


def test_agent_cannot_edit_others_comment_admin_can(client, members, ticket):
    path = f"/tickets/{ticket['id']}/comments"
    comment = client.post(path, headers=members["admin"][1], json={"body": "Admin note"}).json()
    detail = path + "/" + comment["id"]
    assert client.patch(detail, headers=members["agent"][1], json={"body": "No"}).status_code == 403
    assert client.delete(detail, headers=members["agent"][1]).status_code == 403
    assert (
        client.patch(detail, headers=members["admin"][1], json={"body": "Edit"}).status_code == 200
    )


def test_comments_hidden_across_tenants_and_deleted_tickets(client, admin, ticket):
    path = f"/tickets/{ticket['id']}"
    comment = client.post(path + "/comments", headers=admin[1], json={"body": "Private"}).json()
    register(client, "another@example.com")
    external = login(client, "another@example.com")
    detail = path + "/comments/" + comment["id"]
    for method in ("get", "patch", "delete"):
        assert (
            client.request(
                method, detail, headers=external, json={"body": "No"} if method == "patch" else None
            ).status_code
            == 404
        )
    client.delete(path, headers=admin[1])
    assert client.get(path + "/comments", headers=admin[1]).status_code == 404
    assert client.get(detail, headers=admin[1]).status_code == 404
    assert client.post(path + "/comments", headers=admin[1], json={"body": "No"}).status_code == 404


def test_empty_comment_rejected(client, admin, ticket):
    assert (
        client.post(
            f"/tickets/{ticket['id']}/comments", headers=admin[1], json={"body": " "}
        ).status_code
        == 422
    )


def test_login_rate_limit_shared_across_login_endpoints(client):
    client.app.state.settings.login_rate_limit = 2
    payload = {"email": "missing@example.com", "password": PASSWORD}
    assert client.post("/auth/login", json=payload).status_code == 401
    assert (
        client.post(
            "/auth/token", data={"username": payload["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    response = client.post("/auth/login", json=payload)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_redis_outage_fails_login_closed_and_readiness(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RedisConnectionError("unavailable")

    monkeypatch.setattr(client.app.state.redis, "eval", unavailable)
    monkeypatch.setattr(client.app.state.redis, "ping", unavailable)
    assert (
        client.post(
            "/auth/login", json={"email": "a@example.com", "password": PASSWORD}
        ).status_code
        == 503
    )
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["redis"] is False
    assert client.get("/health").status_code == 200


def test_readiness_requires_database(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def unavailable(*args, **kwargs):
        raise OperationalError("select", {}, Exception("unavailable"))

    monkeypatch.setattr(client.app.state.engine, "connect", unavailable)
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["database"] is False
    assert response.json()["redis"] is True


def test_health_ready_openapi_examples_and_security(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready", "database": True, "redis": True}
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["components"]["schemas"]["Register"]["examples"]
    assert spec["components"]["schemas"]["TicketCreate"]["examples"]
    assert spec["paths"]["/tickets"]["post"]["security"]
    assert all(operation["tags"] for path in spec["paths"].values() for operation in path.values())


def test_request_id_and_structured_logging(client, caplog):
    logger = logging.getLogger("opsticket.requests")
    logger.addHandler(caplog.handler)
    try:
        response = client.get("/health?secret=do-not-log", headers={"X-Request-ID": "trace-123"})
        assert response.headers["X-Request-ID"] == "trace-123"
        record = json.loads(caplog.records[-1].message)
        assert record["request_id"] == "trace-123" and record["status"] == 200
        assert "do-not-log" not in caplog.text
        response = client.get("/health", headers={"X-Request-ID": "bad id"})
        assert response.headers["X-Request-ID"] != "bad id"
    finally:
        logger.removeHandler(caplog.handler)
