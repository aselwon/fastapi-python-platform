"""Exercise the documented contributor flow against a running Compose stack."""

import json
import os
import urllib.request
import uuid

base_url = os.environ.get("API_URL", "http://localhost:8000")


def request(method, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base_url + path,
        method=method,
        headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        assert response.headers.get("X-Request-ID")
        body = response.read()
        return json.loads(body) if body else None


def main():
    assert request("GET", "/ready")["status"] == "ready"
    spec = request("GET", "/openapi.json")
    assert spec["paths"]["/tickets"]["post"]["security"]
    email = f"smoke-{uuid.uuid4().hex}@example.com"
    password = "Smoke-test-password-123!"
    user = request(
        "POST",
        "/auth/register",
        {
            "name": "Smoke Admin",
            "tenant_name": "Smoke workspace",
            "email": email,
            "password": password,
        },
    )
    token = request("POST", "/auth/login", {"email": email, "password": password})["access_token"]
    ticket = request("POST", "/tickets", {"title": "Smoke test ticket"}, token)
    path = "/tickets/" + ticket["id"]
    assert ticket["tenant_id"] == user["tenant_id"]
    assert request("GET", "/tickets", token=token)["total"] == 1
    request("PATCH", path, {"description": "Created through documented API flow"}, token)
    request("POST", path + "/assign", {"assignee_id": user["id"]}, token)
    comment = request("POST", path + "/comments", {"body": "Verified"}, token)
    assert request("GET", path + "/comments", token=token)[0]["id"] == comment["id"]
    assert request("POST", path + "/close", token=token)["status"] == "closed"
    request("DELETE", path, token=token)
    assert request("GET", "/tickets", token=token)["total"] == 0
    print(
        "PASS: readiness, OpenAPI, register, login, ticket CRUD, assign, comments, "
        "close, soft delete"
    )


if __name__ == "__main__":
    main()
