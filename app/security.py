import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import Settings
from app.models import User

password_hasher = PasswordHash.recommended()
# Use the same expensive verification path for unknown accounts.
dummy_hash = password_hasher.hash("not-a-real-account-password")


def create_token(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user.id,
            "tenant_id": user.tenant_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "iss": "opsticket",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


class LoginLimiter:
    """Atomic fixed-window Redis counters; no plaintext email or IP in keys."""

    script = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return {count, redis.call('TTL', KEYS[1])}
    """

    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def check(self, email: str, ip: str) -> int:
        retry = 0
        for scope, value in (("account", email), ("ip", ip)):
            digest = hashlib.sha256(value.encode()).hexdigest()
            count, ttl = self.client.eval(
                self.script, 1, f"login:{scope}:{digest}", self.settings.login_window_seconds
            )
            # Allow shared office networks some headroom, while limiting account guessing.
            limit = self.settings.login_rate_limit * (5 if scope == "ip" else 1)
            if count > limit:
                retry = max(retry, max(int(ttl), 1))
        return retry
